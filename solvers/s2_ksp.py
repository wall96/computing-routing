"""S2 K 最短路(Yen)+ 联合选择(《设计文档》§9.3 S2)。

策略:先用 S1 排名;候选的直连最优路能过则直接用(此时≈S1,快);
若某候选直连路违反硬约束(丢包/带宽/时延),对该候选用 Yen 找 K 条备选组合救回。
只精修 top-M 候选,控制开销。
"""
from __future__ import annotations
from typing import Optional, List, Dict
from models import Snapshot, Task, Allocation
from solvers import base
from solvers.base import Solver, INF
from solvers.s1_dijkstra import rank_candidates


def yen_ksp(snapshot: Snapshot, src: str, dst: str, task: Task, strategy: str,
            rate_req: float, bits: float, k: int, K: int) -> List[List[str]]:
    if src == dst:
        return [[src]]
    dist, prev = base.single_source(snapshot, src, task, strategy, rate_req, bits, k)
    if dist.get(dst, INF) == INF:
        return []
    p0 = base.reconstruct(prev, src, dst)
    if p0 is None:
        return []
    A = [p0]
    B = []
    seen = {tuple(p0)}
    for _ in range(1, K):
        prev_path = A[-1]
        for i in range(len(prev_path) - 1):
            spur = prev_path[i]
            root = prev_path[:i + 1]
            banned_edges = set()
            for p in A:
                if len(p) > i and p[:i + 1] == root:
                    a, b = p[i], p[i + 1]
                    banned_edges.add((a, b) if a < b else (b, a))
            banned_nodes = set(root[:-1])
            dist_s, prev_s = base.single_source(snapshot, spur, task, strategy,
                                                rate_req, bits, k, banned_edges, banned_nodes)
            if dist_s.get(dst, INF) == INF:
                continue
            spur_path = base.reconstruct(prev_s, spur, dst)
            if spur_path is None:
                continue
            total = root[:-1] + spur_path
            tt = tuple(total)
            if tt in seen:
                continue
            seen.add(tt)
            cost = base.seg_cost(snapshot, total, strategy, bits, k)
            B.append((cost, total))
        if not B:
            break
        B.sort(key=lambda x: x[0])
        A.append(B.pop(0)[1])
    return A


class S2KSP(Solver):
    name = "S2"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        ranked = rank_candidates(snapshot, task, strategy, self.k, self.enforce_energy)
        if not ranked:
            return None
        # 1) 快速 S1 直连:任一候选可行即返回(常态,与 S1 同速)
        for _, nd, pre0, post0 in ranked:
            a = base.validate_and_build(snapshot, task, nd, pre0, post0, strategy,
                                        self.name, self.enforce_energy)
            if a is not None:
                return a
        # 2) S1 全失败 → 对前 refine_cap 个候选做 KSP 精修(限 worst-case 开销)
        K = self.acfg.get("ksp_K", 3)
        refine_cap = self.acfg.get("refine_cap", 3)
        r0, rho, D0 = task.rate_mbps, task.compression_rho, task.data_volume_bits
        for _, nd, _, _ in ranked[:refine_cap]:
            v = nd.id
            pre_pool = yen_ksp(snapshot, task.src, v, task, strategy, r0, D0, self.k, K)
            if not pre_pool:
                continue
            post_pools = {}
            ok = True
            for dst in set(task.dst_set):
                pool = yen_ksp(snapshot, v, dst, task, strategy, r0 * rho, D0 * rho, self.k, K)
                if not pool:
                    ok = False; break
                post_pools[dst] = pool
            if not ok:
                continue
            best = self._best_combo(snapshot, task, nd, pre_pool, post_pools, strategy)
            if best is not None:
                return best
        return None

    def _best_combo(self, snapshot, task, nd, pre_pool, post_pools, strategy):
        best_alloc = None
        best_cost = INF
        for pre in pre_pool:
            # 为每个 dst 选一条 post(独立择优即可,带宽聚合在 validate 里统一校验)
            post_paths = [post_pools[dst][0] for dst in task.dst_set]
            # 尝试若干组合:先用各 dst 的第 1 条;失败再逐个换
            combos = [[post_pools[dst][j] if j < len(post_pools[dst]) else post_pools[dst][-1]
                       for dst in task.dst_set]
                      for j in range(max(len(p) for p in post_pools.values()))]
            for post_paths in combos:
                alloc = base.validate_and_build(snapshot, task, nd, pre, list(post_paths),
                                                strategy, self.name, self.enforce_energy)
                if alloc is not None:
                    c = alloc.energy_total_J if strategy == "energy" else alloc.max_delay_ms
                    if c < best_cost:
                        best_cost = c; best_alloc = alloc
                    break
        return best_alloc
