"""S4 蚁群 ACO(元启发式,《设计文档》§9.3 S4)。

对决策空间(选哪个候选算力节点 × 该节点的哪条 pre/post 路)做信息素引导搜索。
以 S1 解为初始最优(保证不劣于 S1),再用蚁群在 top-M 候选 + KSP 路池中尝试改进。
"""
from __future__ import annotations
import random
from typing import Optional, List, Dict
from models import Snapshot, Task, Allocation
from solvers import base
from solvers.base import Solver, INF
from solvers.s1_dijkstra import rank_candidates
from solvers.s2_ksp import yen_ksp


def _obj(alloc: Allocation, strategy: str) -> float:
    if strategy == "energy":
        return alloc.energy_total_J
    if strategy == "balance":
        return max(alloc.bw_per_link.values()) if alloc.bw_per_link else 0.0
    return alloc.max_delay_ms


class S4Aco(Solver):
    name = "S4"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        ranked = rank_candidates(snapshot, task, strategy, self.k, self.enforce_energy)
        if not ranked:
            return None
        top_m = min(self.acfg.get("top_m_candidates", 20), len(ranked))
        cands = ranked[:top_m]
        aco = self.acfg.get("aco", {})
        n_ants = aco.get("ants", 15); n_iter = aco.get("iters", 20)
        alpha = aco.get("alpha", 1.0); beta = aco.get("beta", 2.0)
        evap = aco.get("evap", 0.3); q = aco.get("q", 1.0)
        K = self.acfg.get("ksp_K", 4)
        rng = random.Random(hash(task.id) & 0xFFFFFFFF)

        # 初始最优 = S1 第一个可行解
        best_alloc = None; best_cost = INF
        for _, nd, pre0, post0 in cands:
            a = base.validate_and_build(snapshot, task, nd, pre0, post0, strategy,
                                        self.name, self.enforce_energy)
            if a is not None:
                best_alloc, best_cost = a, _obj(a, strategy)
                break
        # 缓存每候选的路池(惰性)
        pools: Dict[int, tuple] = {}

        def build_pool(i):
            if i in pools:
                return pools[i]
            nd = cands[i][1]; v = nd.id
            r0, rho, D0 = task.rate_mbps, task.compression_rho, task.data_volume_bits
            pre_pool = yen_ksp(snapshot, task.src, v, task, strategy, r0, D0, self.k, K)
            post_pools = {}
            ok = bool(pre_pool)
            for dst in set(task.dst_set):
                pool = yen_ksp(snapshot, v, dst, task, strategy, r0 * rho, D0 * rho, self.k, K)
                if not pool:
                    ok = False; break
                post_pools[dst] = pool
            pools[i] = (ok, nd, pre_pool, post_pools)
            return pools[i]

        # 信息素:候选级
        tau = [1.0] * len(cands)
        heur = [1.0 / (c[0] + 1e-9) for c in cands]

        def eval_candidate(i) -> Optional[Allocation]:
            ok, nd, pre_pool, post_pools = build_pool(i)
            if not ok:
                return None
            pre = pre_pool[rng.randrange(len(pre_pool))]
            posts = [post_pools[dst][rng.randrange(len(post_pools[dst]))] for dst in task.dst_set]
            return base.validate_and_build(snapshot, task, nd, pre, list(posts), strategy,
                                           self.name, self.enforce_energy)

        for _ in range(n_iter):
            iter_best = None; iter_best_cost = INF; deposits = []
            for _ant in range(n_ants):
                # 轮盘赌选候选
                weights = [(tau[i] ** alpha) * (heur[i] ** beta) for i in range(len(cands))]
                s = sum(weights)
                if s <= 0:
                    i = rng.randrange(len(cands))
                else:
                    r = rng.random() * s; acc = 0.0; i = len(cands) - 1
                    for j, w in enumerate(weights):
                        acc += w
                        if acc >= r:
                            i = j; break
                a = eval_candidate(i)
                if a is None:
                    continue
                c = _obj(a, strategy)
                deposits.append((i, c))
                if c < iter_best_cost:
                    iter_best, iter_best_cost = a, c
            # 更新信息素
            for i in range(len(tau)):
                tau[i] *= (1 - evap)
            for i, c in deposits:
                tau[i] += q / (c + 1e-9)
            if iter_best is not None and iter_best_cost < best_cost:
                best_alloc, best_cost = iter_best, iter_best_cost
        return best_alloc
