"""S3 LARAC / 约束最短路(《设计文档》§9.3 S3)。

面向"时延约束下最小代价"(代价 = energy 或 balance)。对候选 v* 用 Lagrange
松弛 w_λ(e)=cost(e)+λ·delay(e) 迭代 λ,找满足端到端时延预算的最小代价两段路。
latency 策略下退化为 S1(min-delay 即最优)。
"""
from __future__ import annotations
from heapq import heappush, heappop
from typing import Optional, List, Dict, Tuple
from models import Snapshot, Task, Allocation
from solvers import base
from solvers.base import Solver, INF
from solvers.s1_dijkstra import rank_candidates


def _base_cost(lk, task, strategy, bits, k):
    if strategy == "energy":
        return lk.beta * bits
    if strategy == "balance":
        u = max(0.0, 1 - lk.bw_remain_mbps / max(1e-9, lk.bw_total_mbps))
        return u ** k + 1e-6
    return lk.delay_ms


def _agg_dijkstra(snapshot, source, task, strategy, rate_req, bits, k, lam):
    """聚合权 cost + λ·delay 的单源最短路。"""
    dist = {nid: INF for nid in snapshot.nodes}
    prev: Dict[str, str] = {}
    if source not in dist:
        return dist, prev
    dist[source] = 0.0
    adj = snapshot.adj()
    pq = [(0.0, source)]
    while pq:
        d, u = heappop(pq)
        if d > dist[u]:
            continue
        for v in adj.get(u, []):
            lk = snapshot.get_link(u, v)
            if lk is None or not lk.alive or lk.bw_remain_mbps < rate_req:
                continue
            if lk.delay_ms > task.delay_budget_ms:
                continue
            w = _base_cost(lk, task, strategy, bits, k) + lam * lk.delay_ms
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd; prev[v] = u
                heappush(pq, (nd, v))
    return dist, prev


class S3Larac(Solver):
    name = "S3"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        ranked = rank_candidates(snapshot, task, strategy, self.k, self.enforce_energy)
        if not ranked:
            return None
        # 1) 快速直连:任一候选可行即返回(latency 下 LARAC 无增益,直接等价 S1)
        for _, nd, pre0, post0 in ranked:
            a = base.validate_and_build(snapshot, task, nd, pre0, post0, strategy,
                                        self.name, self.enforce_energy)
            if a is not None:
                return a
        if strategy == "latency":
            return None
        # 2) 直连全失败 → 对前 refine_cap 个候选做 LARAC(时延约束下最小代价)
        refine_cap = self.acfg.get("refine_cap", 3)
        for _, nd, _, _ in ranked[:refine_cap]:
            got = self._larac_candidate(snapshot, task, nd, strategy)
            if got is not None:
                return got
        return None

    def _eval(self, snapshot, task, v, strategy, lam):
        """给定 λ,返回 (pre, posts, cost_total, delay_total) 或 None。"""
        r0, rho, D0 = task.rate_mbps, task.compression_rho, task.data_volume_bits
        dist_pre, prev_pre = _agg_dijkstra(snapshot, task.src, task, strategy, r0, D0, self.k, lam)
        if dist_pre.get(v, INF) == INF:
            return None
        pre = base.reconstruct(prev_pre, task.src, v)
        if pre is None:
            return None
        posts = []
        for dst in task.dst_set:
            dist_d, prev_d = _agg_dijkstra(snapshot, dst, task, strategy, r0 * rho, D0 * rho,
                                           self.k, lam)
            if dist_d.get(v, INF) == INF:
                return None
            p = base.reconstruct(prev_d, dst, v)
            if p is None:
                return None
            posts.append(list(reversed(p)))
        cost = base.seg_cost(snapshot, pre, strategy, D0, self.k)
        cost += sum(base.seg_cost(snapshot, p, strategy, D0 * rho, self.k) for p in posts)
        d_pre = base.path_delay(snapshot, pre)
        d_post = max(base.path_delay(snapshot, p) for p in posts)
        return pre, posts, cost, d_pre + d_post

    def _larac_candidate(self, snapshot, task, nd, strategy):
        from energy import compute_latency_ms
        v = nd.id
        budget = task.delay_budget_ms - compute_latency_ms(nd, task)
        if budget <= 0:
            return None
        pc = self._eval(snapshot, task, v, strategy, 0.0)          # min cost
        if pc is None:
            return None
        if pc[3] <= budget:
            return base.validate_and_build(snapshot, task, nd, pc[0], pc[1],
                                           strategy, self.name, self.enforce_energy)
        pd = self._eval(snapshot, task, v, strategy, 1e15)         # ~min delay
        if pd is None or pd[3] > budget:
            return None
        for _ in range(self.acfg.get("larac_max_iter", 20)):
            denom = (pd[3] - pc[3])
            if abs(denom) < 1e-12:
                break
            lam = (pc[2] - pd[2]) / denom
            if lam <= 0:
                break
            r = self._eval(snapshot, task, v, strategy, lam)
            if r is None:
                break
            if abs(r[2] - pc[2]) < 1e-12 or abs(r[2] - pd[2]) < 1e-12:
                break
            if r[3] <= budget:
                pd = r
            else:
                pc = r
        return base.validate_and_build(snapshot, task, nd, pd[0], pd[1],
                                       strategy, self.name, self.enforce_energy)
