"""Satellite-side split offloading (S6 water-filling, S7 τ-segmented LP), packet delay model.

终端无算力不能拆:整包上行到接入卫星 g,在 g 上拆分后经星间链路送往多个算力节点。
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import Node, NodeType, Link, LinkType, Snapshot, Task, BizType
from resources import ResourceLedger
from solvers import make_solver
from solvers.base import evaluate_splits, build_split_allocation, split_overhead_ms

D = 40e6          # 40 Mbit 包
RHO = 0.2


def cfg(**split):
    return {'algorithm': {'split': {'overhead_s_per_Tbit': 0.01, **split},
                          'enforce_energy_budget': True}}


def sat(nid, cap=1e15):
    return Node(nid, NodeType.LEO, cap, cap, 1e-13, 1e6, 1e6)


def term(nid):
    return Node(nid, NodeType.TERMINAL, 0, 0, 0, 0, 0)


def link(u, v, delay, bw, kind):
    return Link(u, v, kind, bw, bw, delay, 1e-7, 0, 1e-9)


def snap(nodes, links):
    ns = {n.id: n for n in nodes}
    ls = {(min(l.u, l.v), max(l.u, l.v)): l for l in links}
    return Snapshot(0, ns, ls, {'LEO'}, endpoint_only={n.id for n in nodes if n.type == NodeType.TERMINAL})


def task(packet=D, budget=500.0, omega=3000):
    rate = 10e6
    return Task('T', 'A', ['B'], BizType.HC, 3, 'persist', rate / 1e6, budget, .01, omega, 12000, 0, 60,
                RHO, cycles_total=rate * omega * 60, data_volume_bits=rate * 60,
                compute_rate=rate * omega, packet_bits=packet)


def fanout(isl_bw=10000.0, down_bw=2000.0, cap=20e12):
    """A→g 上行;g 经三条星间链路到 c1/c2/c3(算力较小);各自下行到 B。"""
    SGL, ISL = LinkType.SGL_LEO, LinkType.ISL_LEO_LEO
    nodes = [term('A'), term('B'), sat('g', 1.0), sat('c1', cap), sat('c2', cap), sat('c3', cap)]
    links = [link('A', 'g', 3, 20000, SGL),
             link('g', 'c1', 2, isl_bw, ISL), link('g', 'c2', 3, isl_bw, ISL), link('g', 'c3', 4, isl_bw, ISL),
             link('c1', 'B', 3, down_bw, SGL), link('c2', 'B', 3, down_bw, SGL), link('c3', 'B', 3, down_bw, SGL)]
    return snap(nodes, links)


def two_uplinks():
    """A 能看到 g1、g2 两颗卫星,各连一个算力节点。"""
    SGL, ISL = LinkType.SGL_LEO, LinkType.ISL_LEO_LEO
    nodes = [term('A'), term('B'), sat('g1', 1.0), sat('g2', 1.0), sat('c1', 20e12), sat('c2', 20e12)]
    links = [link('A', 'g1', 3, 2000, SGL), link('A', 'g2', 3, 2000, SGL),
             link('g1', 'c1', 2, 10000, ISL), link('g2', 'c2', 2, 10000, ISL),
             link('c1', 'B', 3, 2000, SGL), link('c2', 'B', 3, 2000, SGL)]
    return snap(nodes, links)


def solve(name, s, t, **split):
    return make_solver(name, cfg(**split)).solve(s, t, 'latency')


class SatelliteSplitTest(unittest.TestCase):
    def test_split_happens_on_access_satellite(self):
        s, t = fanout(), task()
        for name in ('S6', 'S7'):
            a = solve(name, s, t)
            self.assertGreater(len(a.splits), 1, name)
            # 所有子包共用同一条上行 A→g,在 g 上拆分
            self.assertEqual({x.path_pre[:2] == ['A', 'g'] for x in a.splits}, {True}, name)
            self.assertAlmostEqual(sum(x.share for x in a.splits), 1.0)
            self.assertLess(a.max_delay_ms, solve(name, s, t, max_splits=1).max_delay_ms - 5, name)

    def test_waterfill_equal_finish(self):
        s, t = fanout(), task()
        a = solve('S6', s, t)
        f = [x.finish_ms for x in a.splits]
        self.assertAlmostEqual(max(f), min(f), places=6)
        parts = [(s.nodes[x.node], x.share, x.path_pre, x.path_post) for x in a.splits]
        self.assertAlmostEqual(evaluate_splits(s, t, parts, 0.01)[0], a.max_delay_ms)

    def test_split_delay_includes_full_uplink_and_overhead(self):
        s, t = fanout(), task()
        a = solve('S6', s, t)
        up = 3 + D / 20000e6 * 1000                     # 上行传播 + 整包上行传输(存储转发)
        self.assertGreater(min(x.finish_ms for x in a.splits), up + split_overhead_ms(t, 0.01))

    def test_terminal_side_split_rejected(self):
        s, t = two_uplinks(), task()
        parts = [(s.nodes['c1'], .5, ['A', 'g1', 'c1'], [['c1', 'B']]),
                 (s.nodes['c2'], .5, ['A', 'g2', 'c2'], [['c2', 'B']])]
        self.assertIsNone(build_split_allocation(s, t, parts, 'latency', 'X', True, 0.01))
        for name in ('S6', 'S7'):
            a = solve(name, s, t)
            self.assertEqual(a.splits, [], name)      # 只能经单条上行,唯一算力节点 → 不拆

    def test_lp_matches_waterfill_when_independent(self):
        s, t = fanout(), task()
        self.assertAlmostEqual(solve('S7', s, t).max_delay_ms, solve('S6', s, t).max_delay_ms, places=3)

    def test_lp_handles_shared_downlink(self):
        SGL, ISL = LinkType.SGL_LEO, LinkType.ISL_LEO_LEO
        nodes = [term('A'), term('B'), sat('g', 1.0), sat('c1', 20e12), sat('c2', 20e12), sat('d', 1.0)]
        links = [link('A', 'g', 3, 20000, SGL), link('g', 'c1', 2, 10000, ISL), link('g', 'c2', 2, 10000, ISL),
                 link('c1', 'd', 2, 10000, ISL), link('c2', 'd', 2, 10000, ISL), link('d', 'B', 3, 500, SGL)]
        s, t = snap(nodes, links), task()
        one = solve('S6', s, t, max_splits=1)
        for name in ('S6', 'S7'):
            self.assertLessEqual(solve(name, s, t).max_delay_ms, one.max_delay_ms + 1e-6, name)

    def test_no_split_when_no_benefit(self):
        # 包很小、计算极快:传输/计算时延远小于各路径传播时延差,只用最近的节点
        s = fanout(cap=1e18)
        for name in ('S6', 'S7'):
            a = solve(name, s, task(packet=1e3, omega=1))
            self.assertEqual(a.splits, [], name)
            self.assertEqual(a.compute_node, 'c1', name)

    def test_large_overhead_prevents_split(self):
        s, t = fanout(), task()
        for name in ('S6', 'S7'):
            self.assertEqual(solve(name, s, t, overhead_s_per_Tbit=1e4).splits, [], name)

    def test_ledger_reserve_release_roundtrip(self):
        s, t = fanout(), task()
        a = solve('S7', s, t)
        snapshot_state = lambda: ({k: l.bw_remain_mbps for k, l in s.links.items()},
                                  {k: (n.capacity_remain, n.power_remain_W) for k, n in s.nodes.items()})
        before = snapshot_state()
        led = ResourceLedger(cfg()['algorithm'])
        led.reserve(s, a)
        self.assertTrue(all(s.nodes[x.node].capacity_remain < 20e12 for x in a.splits))
        led.release(s, a)
        self.assertEqual(before, snapshot_state())

    def test_only_latency_strategy(self):
        with self.assertRaises(ValueError):
            make_solver('S6', cfg()).solve(fanout(), task(), 'energy')


if __name__ == '__main__':
    unittest.main()
