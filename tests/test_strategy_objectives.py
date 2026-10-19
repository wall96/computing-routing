"""Focused checks for congestion look-ahead and QoS-preserving fallback."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models import BizType, Link, LinkType, Node, NodeType, Snapshot, Task
from solvers.s1_dijkstra import S1Dijkstra, projected_balance_cost


def node(nid, kind=NodeType.ACCESS):
    capacity = 1e12 if kind == NodeType.LEO else 0.0
    return Node(nid, kind, capacity, capacity, 1e-12, 1.0, 1.0)


def link(u, v, delay, beta, remain=100.0):
    return Link(u, v, LinkType.ISL_LEO_LEO, 100.0, remain, delay, 0.0, 0.0, beta)


def task(budget=20.0):
    return Task('T', 's', ['d'], BizType.LL, 1, 'persist', 2.0,
                budget, 0.01, 50.0, 12000.0, 0.0, 30.0, 0.1,
                cycles_total=3e9, data_volume_bits=60e6, compute_rate=1e8)


def snapshot(direct_delay=5.0, detour_delay=15.0, direct_remain=100.0):
    nodes = {'s': node('s', NodeType.LEO), 'a': node('a'), 'd': node('d')}
    links = [link('s', 'd', direct_delay, 1e-8, direct_remain),
             link('s', 'a', detour_delay, 1e-10),
             link('a', 'd', detour_delay, 1e-10)]
    return Snapshot(0, nodes, {tuple(sorted((x.u, x.v))): x for x in links}, {'LEO'})


class ObjectivesTest(unittest.TestCase):
    def test_energy_fallback_recovers_delay_feasibility(self):
        solver = S1Dijkstra({'algorithm': {}})
        alloc = solver.solve(snapshot(), task(), 'energy')
        self.assertIsNotNone(alloc)
        self.assertEqual(alloc.path_post, [['s', 'd']])
        self.assertLessEqual(alloc.max_delay_ms, 20.0)
        self.assertEqual(solver.diagnostics['fallback_accepted'], 1)

    def test_energy_prefers_lower_energy_when_both_feasible(self):
        solver = S1Dijkstra({'algorithm': {}})
        alloc = solver.solve(snapshot(detour_delay=4), task(), 'energy')
        self.assertEqual(alloc.path_post, [['s', 'a', 'd']])
        self.assertEqual(solver.diagnostics['fallback_accepted'], 0)

    def test_balance_avoids_hot_link_and_latency_stays_short(self):
        snap = snapshot(detour_delay=4, direct_remain=10)
        solver = S1Dijkstra({'algorithm': {}})
        balanced = solver.solve(snap, task(), 'balance')
        latency = solver.solve(snap, task(), 'latency')
        self.assertEqual(balanced.path_post, [['s', 'a', 'd']])
        self.assertEqual(latency.path_post, [['s', 'd']])

    def test_balance_scores_aggregate_shared_link_increment(self):
        snap = snapshot()
        tk = task()
        # PRE uses 2 Mbps, POST traverses the same undirected link at 0.2 Mbps.
        score = projected_balance_cost(snap, tk, snap.nodes['s'],
                                       ['s', 'd'], [['d', 's']])
        expected = (2.2 / 100)**2 + (tk.compute_rate / 1e12)**2
        self.assertAlmostEqual(score, expected)


if __name__ == '__main__':
    unittest.main()
