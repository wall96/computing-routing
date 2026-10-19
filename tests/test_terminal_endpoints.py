"""Ground-terminal endpoints: random coordinates, coverage-based access, no terminal transit."""
from contextlib import redirect_stdout
import copy
import io
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from models import Node, NodeType, Link, LinkType, Snapshot, Task, BizType
from network import build_snapshot, attach_terminals, satellite_coverage, terminals_of
from simulator import Simulator
from solvers import make_solver
from tasks import TaskGenerator, _haversine_km

ROOT = Path(__file__).resolve().parents[1]


def load_cfg():
    return yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8'))


def demand(src, dsts, budget=200.0):
    return Task('X', src, dsts, BizType.HC, 3, 'persist', 1, budget, .01, 50, 12000, 0, 60, .2,
                cycles_total=1e6 * 50 * 60, data_volume_bits=1e6 * 60, compute_rate=1e6 * 50)


def two_sat_snapshot():
    """终端 A 同时连 s1、s2,s1 算力为 0:只有经 A 中转才能 s1→A→s2,应被禁止。"""
    sat = lambda nid, cap: Node(nid, NodeType.LEO, cap, cap, 1e-13, 1e3, 1e3)
    term = lambda nid: Node(nid, NodeType.TERMINAL, 0, 0, 0, 0, 0)
    nodes = {'s1': sat('s1', 0), 's2': sat('s2', 1e12), 'A': term('A'), 'B': term('B')}
    mk = lambda u, v: Link(u, v, LinkType.SGL_LEO, 100, 100, 2, 0, 0, 1e-8)
    links = {('A', 's1'): mk('A', 's1'), ('A', 's2'): mk('A', 's2'), ('B', 's1'): mk('B', 's1')}
    snap = Snapshot(0, nodes, links, {'LEO'}, endpoint_only={'A', 'B'},
                    coverage={'A': ['s1', 's2'], 'B': ['s1']})
    return snap


class TerminalGeneratorTest(unittest.TestCase):
    def test_coordinates_respect_band_and_distance(self):
        tcfg = load_cfg()['traffic']
        gen = TaskGenerator(tcfg, [], 30, seed=7)
        tasks = [t for i in range(20) for t in gen.gen_for_slot(i * 30, i)]
        self.assertTrue(tasks)
        lo, hi = tcfg['terminal']['lat_range_deg']
        for t in tasks:
            self.assertTrue(t.src.startswith('UT-') and all(d.startswith('UT-') for d in t.dst_set))
            self.assertEqual(len(t.dst_set), len(t.dst_coords))
            self.assertNotIn(t.src, t.dst_set)
            for lat, lon in [t.src_coord, *t.dst_coords]:
                self.assertTrue(lo <= lat <= hi and -180 <= lon <= 180)
            limit = tcfg['terminal']['dst_max_km'].get(t.biz.value)
            if limit:
                for c in t.dst_coords:
                    self.assertLessEqual(_haversine_km(t.src_coord, c), limit + 1e-6)
        mc = [t for t in tasks if t.biz == BizType.MC]
        self.assertTrue(all(len(t.dst_set) == tcfg['qos']['MC']['k_dst'] for t in mc))

    def test_same_seed_reproducible(self):
        tcfg = load_cfg()['traffic']
        a = TaskGenerator(copy.deepcopy(tcfg), [], 30, seed=3).gen_for_slot(0, 0)
        b = TaskGenerator(copy.deepcopy(tcfg), [], 30, seed=3).gen_for_slot(0, 0)
        self.assertEqual([(t.src_coord, t.dst_coords) for t in a],
                         [(t.src_coord, t.dst_coords) for t in b])


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_cfg()
        sim = Simulator(self.cfg, scenario=1)
        self.snap = build_snapshot(0, sim.all_nodes, self.cfg, 1)

    def test_terminal_links_only_to_covering_satellites(self):
        cov = satellite_coverage(self.snap, 30.0, 110.0, self.cfg)
        attach_terminals(self.snap, [('UT-a', 30.0, 110.0)], self.cfg)
        self.assertEqual(self.snap.coverage['UT-a'], [s for s, _ in cov])
        self.assertEqual(set(self.snap.adj()['UT-a']), set(self.snap.coverage['UT-a']))
        min_elev = self.cfg['link_params']['sgl_leo']['min_elevation_deg']
        self.assertTrue(all(e >= min_elev for _, e in cov))
        self.assertEqual(self.snap.nodes['UT-a'].type, NodeType.TERMINAL)

    def test_polar_terminal_has_no_coverage(self):
        # L1 倾角 53°、550 km:北极点不可能有仰角 ≥25° 的卫星
        attach_terminals(self.snap, [('UT-p', 89.9, 0.0)], self.cfg)
        self.assertEqual(self.snap.coverage['UT-p'], [])
        task = demand('UT-p', ['UT-p'])
        self.assertEqual(Simulator.reject_reason(self.snap, task), 'src_no_coverage')


class WalkerConstellationTest(unittest.TestCase):
    def test_full_scale_delta_and_star(self):
        cfg = load_cfg()
        sim = Simulator(cfg, scenario=3)
        for shell in cfg['constellation']['shells']:
            sats = [n for n in sim.all_nodes if n.shell == shell['name']]
            self.assertEqual(len(sats), shell['planes'] * shell['per_plane'])   # 不缩比
            raans = sorted({round(math.degrees(n.orbit_raan_rad), 6) for n in sats})
            span = 360.0 if shell['pattern'] == 'delta' else 180.0
            self.assertEqual(len(raans), shell['planes'])
            self.assertAlmostEqual(raans[1] - raans[0], span / shell['planes'])

    def test_terminal_band_coverage_in_scenario1(self):
        # 20×25 Walker Delta 在赤道附近有零星瞬时空洞,纬度带内按面积应 ≥99% 有覆盖
        cfg = load_cfg()
        sim = Simulator(cfg, scenario=1)
        gen = TaskGenerator(cfg['traffic'], [], 30, seed=11)
        hit = n = 0
        for t in (0, 900, 1800):
            snap = build_snapshot(t, sim.all_nodes, cfg, 1)
            for _ in range(300):
                lat, lon = gen._random_ground_point()
                hit += bool(satellite_coverage(snap, lat, lon, cfg)); n += 1
        self.assertGreaterEqual(hit / n, 0.99)


class NoTerminalTransitTest(unittest.TestCase):
    def test_solvers_never_relay_through_terminal(self):
        cfg = load_cfg()
        for name in ('S0', 'S1', 'S2', 'S3', 'S4'):
            snap = two_sat_snapshot()
            # B 只被 s1 覆盖,s1 无算力;唯一能算的 s2 只能经终端 A 到达 → 必须拒绝
            alloc = make_solver(name, cfg).solve(snap, demand('B', ['A']), 'latency')
            self.assertIsNone(alloc, name)
            # A 直连 s2:上行 A→s2 计算,下行 s2→A
            alloc = make_solver(name, cfg).solve(snap, demand('A', ['A']), 'latency')
            self.assertIsNotNone(alloc, name)
            self.assertEqual((alloc.path_pre, alloc.path_post), (['A', 's2'], [['s2', 'A']]))


class TerminalSimulationTest(unittest.TestCase):
    def test_short_run_routes_ground_to_ground_via_satellite_compute(self):
        cfg = load_cfg()
        cfg['simulation'].update(t_end_sec=90)
        sim = Simulator(cfg, scenario=1)
        with redirect_stdout(io.StringIO()):
            m = sim.run()
        self.assertTrue(m.attempts)
        allocs = list(m.alloc_by_task.values())
        self.assertTrue(allocs)
        for a in allocs:
            self.assertTrue(a.path_pre[0].startswith('UT-'))
            self.assertEqual(a.compute_node_type, 'LEO')
            for dst, p in zip(a.task.dst_set, a.path_post):
                self.assertEqual(p[-1], dst)
            for p in a.paths:
                self.assertFalse(any(n.startswith('UT-') for n in p[1:-1]))
        reasons = {r for _, r in m.rejects}
        self.assertTrue(reasons <= {'no_feasible', 'src_no_coverage', 'dst_no_coverage',
                                    'scenario_concurrency_cap'})


if __name__ == '__main__':
    unittest.main()
