"""Split offloading (S6 water-filling, S7 τ-segmented LP) under the packet delay model."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import Node, NodeType, Link, LinkType, Snapshot, Task, BizType
from resources import ResourceLedger
from solvers import make_solver
from solvers.base import evaluate_splits

CFG = {'algorithm': {'split': {}, 'enforce_energy_budget': True}}
D = 40e6          # 40 Mbit 包
RHO = 0.2


def sat(nid):
    return Node(nid, NodeType.LEO, 1e15, 1e15, 1e-13, 1e6, 1e6)


def term(nid):
    return Node(nid, NodeType.TERMINAL, 0, 0, 0, 0, 0)


def link(u, v, delay, bw=1000.0, kind=LinkType.SGL_LEO):
    return Link(u, v, kind, bw, bw, delay, 1e-7, 0, 1e-9)


def snap(nodes, links):
    ns = {n.id: n for n in nodes}
    ls = {(min(l.u, l.v), max(l.u, l.v)): l for l in links}
    return Snapshot(0, ns, ls, {'LEO'}, endpoint_only={n.id for n in nodes if n.type == NodeType.TERMINAL})


def task(packet=D, budget=200.0):
    return Task('T', 'A', ['B'], BizType.HC, 3, 'persist', 10, budget, .01, 100, 12000, 0, 60, RHO,
                cycles_total=10e6 * 100 * 60, data_volume_bits=10e6 * 60, compute_rate=10e6 * 100,
                packet_bits=packet)


def independent():
    """A 经三颗卫星到 B,上下行链路各不相同:子包完全独立。"""
    nodes = [term('A'), term('B'), sat('s1'), sat('s2'), sat('s3')]
    links = [link('A', 's1', 3), link('s1', 'B', 3),
             link('A', 's2', 5), link('s2', 'B', 5),
             link('A', 's3', 8), link('s3', 'B', 8)]
    return snap(nodes, links)


def shared_downlink():
    """A 有两条上行,但 B 只被 s3 覆盖:所有子包共用 s3→B 下行。"""
    nodes = [term('A'), term('B'), sat('s1'), sat('s2'), sat('s3')]
    links = [link('A', 's1', 3), link('A', 's2', 4), link('s3', 'B', 3, bw=200.0),
             link('s1', 's3', 2, 10000, LinkType.ISL_LEO_LEO),
             link('s2', 's3', 2, 10000, LinkType.ISL_LEO_LEO)]
    return snap(nodes, links)


def single(snapshot, t):
    cfg = {'algorithm': {'split': {'max_splits': 1}, 'enforce_energy_budget': True}}
    return make_solver('S6', cfg).solve(snapshot, t, 'latency')


class SplitTest(unittest.TestCase):
    def test_waterfill_equal_finish_and_beats_single(self):
        s, t = independent(), task()
        a = make_solver('S6', CFG).solve(s, t, 'latency')
        one = single(s, t)
        self.assertGreater(len(a.splits), 1)
        self.assertAlmostEqual(sum(x.share for x in a.splits), 1.0)
        finishes = [x.finish_ms for x in a.splits]
        self.assertAlmostEqual(max(finishes), min(finishes), places=6)   # 等完成时间
        self.assertLess(a.max_delay_ms, one.max_delay_ms - 5)
        # 报告的时延就是精确核算值
        parts = [(s.nodes[x.node], x.share, x.path_pre, x.path_post) for x in a.splits]
        self.assertAlmostEqual(evaluate_splits(s, t, parts)[0], a.max_delay_ms)

    def test_lp_matches_waterfill_when_independent(self):
        s, t = independent(), task()
        wf = make_solver('S6', CFG).solve(s, t, 'latency')
        lp = make_solver('S7', CFG).solve(s, t, 'latency')
        self.assertAlmostEqual(lp.max_delay_ms, wf.max_delay_ms, places=4)

    def test_lp_handles_shared_downlink(self):
        s, t = shared_downlink(), task()
        wf = make_solver('S6', CFG).solve(s, t, 'latency')
        lp = make_solver('S7', CFG).solve(s, t, 'latency')
        one = single(s, t)
        self.assertLessEqual(lp.max_delay_ms, wf.max_delay_ms + 1e-6)
        self.assertLessEqual(lp.max_delay_ms, one.max_delay_ms + 1e-6)

    def test_no_split_when_no_benefit(self):
        # 包很小:传输/计算时延远小于各路径传播时延差,只用最近的节点
        s, t = independent(), task(packet=1e3)
        for name in ('S6', 'S7'):
            a = make_solver(name, CFG).solve(s, t, 'latency')
            self.assertEqual(a.splits, [], name)
            self.assertEqual(a.compute_node, 's1', name)

    def test_far_candidate_unused(self):
        s, t = independent(), task()
        s.get_link('A', 's3').delay_ms = 150.0      # 太远:固定时延已超过统一完成时间
        s._adj = None
        for name in ('S6', 'S7'):
            a = make_solver(name, CFG).solve(s, t, 'latency')
            self.assertNotIn('s3', {x.node for x in a.splits}, name)

    def test_ledger_reserve_release_roundtrip(self):
        s, t = independent(), task()
        a = make_solver('S7', CFG).solve(s, t, 'latency')
        before = ({k: l.bw_remain_mbps for k, l in s.links.items()},
                  {k: (n.capacity_remain, n.power_remain_W) for k, n in s.nodes.items()})
        led = ResourceLedger(CFG['algorithm'])
        led.reserve(s, a)
        used = {x.node for x in a.splits}
        self.assertTrue(all(s.nodes[n].capacity_remain < 1e15 for n in used))
        led.release(s, a)
        after = ({k: l.bw_remain_mbps for k, l in s.links.items()},
                 {k: (n.capacity_remain, n.power_remain_W) for k, n in s.nodes.items()})
        self.assertEqual(before, after)

    def test_only_latency_strategy(self):
        with self.assertRaises(ValueError):
            make_solver('S6', CFG).solve(independent(), task(), 'energy')


if __name__ == '__main__':
    unittest.main()
