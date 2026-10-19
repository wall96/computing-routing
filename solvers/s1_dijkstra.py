"""S1 加权 Dijkstra 两段分解(主力启发式,《设计文档》§9.3 S1)。"""
from __future__ import annotations
from typing import Optional, Dict, List, Tuple
from models import Snapshot, Task, Allocation
from solvers import base
from solvers.base import Solver, INF


def projected_balance_cost(snapshot: Snapshot, task: Task, node,
                           path_pre, post_paths) -> float:
    """Increment of sum(link_util**2) + sum(compute_util**2).

    Aggregate overlapping PRE/POST links before scoring; all terms are
    dimensionless and use the same physical resource denominators.
    """
    cost = 0.0
    for (u, v), rate in base.compute_bw_per_link(task, path_pre, post_paths).items():
        link = snapshot.get_link(u, v)
        if link is None or rate > link.bw_remain_mbps + 1e-6:
            return INF
        used = max(0.0, 1.0 - link.bw_remain_mbps / link.bw_total_mbps)
        inc = rate / link.bw_total_mbps
        cost += inc * (2.0 * used + inc)
    used = max(0.0, 1.0 - node.capacity_remain / node.capacity_total)
    inc = task.compute_rate / node.capacity_total
    return cost + inc * (2.0 * used + inc)


def rank_candidates(snapshot: Snapshot, task: Task, strategy: str, k: int,
                    enforce_energy: bool):
    """返回 [(score, node, path_pre, path_post_list)] 升序。

    跨区域投递(src≠dst):前段 src→v* 载 D₀(速率 r0),后段 v*→各 dst 载 D₀ρ(速率 ρr0),
    两段是不同路径,计算节点 v* 的选址(靠近 src 早压缩)成为真正的优化空间。
    """
    cands = base.candidate_compute_nodes(snapshot, task, enforce_energy)
    if not cands or task.src not in snapshot.nodes:
        return []
    D0, r0, rho = task.data_volume_bits, task.rate_mbps, task.compression_rho

    dist_pre, prev_pre = base.single_source(snapshot, task.src, task, strategy, r0, D0, k)
    post_cache: Dict[str, Tuple[dict, dict]] = {}
    for dst in set(task.dst_set):
        post_cache[dst] = base.single_source(snapshot, dst, task, strategy, r0 * rho, D0 * rho, k)
    ranked = []
    for nd in cands:
        v = nd.id
        if dist_pre.get(v, INF) == INF:
            continue
        path_pre = base.reconstruct(prev_pre, task.src, v)
        if path_pre is None:
            continue
        cost_pre = base.seg_cost(snapshot, path_pre, strategy, D0, k)
        post_paths, post_costs, ok = [], [], True
        for dst in task.dst_set:
            dist_d, prev_d = post_cache[dst]
            if dist_d.get(v, INF) == INF:
                ok = False; break
            p_dv = base.reconstruct(prev_d, dst, v)
            if p_dv is None:
                ok = False; break
            p_post = list(reversed(p_dv))
            post_paths.append(p_post)
            post_costs.append(base.seg_cost(snapshot, p_post, strategy, D0 * rho, k))
        if not ok:
            continue
        if strategy == "balance":
            score = projected_balance_cost(snapshot, task, nd, path_pre, post_paths)
        else:
            path_total = cost_pre + sum(post_costs)
            score = path_total + base.node_penalty(nd, task, strategy)
        ranked.append((score, nd, path_pre, post_paths))
    ranked.sort(key=lambda x: x[0])
    return ranked


class S1Dijkstra(Solver):
    name = "S1"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.diagnostics = {"fallback_attempts": 0, "fallback_accepted": 0}

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        if strategy == "latency" and self.acfg.get("latency_lazy_candidates", False):
            return self._latency_lazy(snapshot, task)
        ranked = rank_candidates(snapshot, task, strategy, self.k, self.enforce_energy)
        for _, nd, path_pre, post_paths in ranked:
            alloc = base.validate_and_build(snapshot, task, nd, path_pre, post_paths,
                                            strategy, self.name, self.enforce_energy)
            if alloc is not None:
                return alloc
        # An energy/congestion-shortest path can violate a delay constraint
        # even when a propagation-shortest feasible path exists. Search that
        # additional candidate family using the original objective ranking.
        if strategy != "latency":
            self.diagnostics["fallback_attempts"] += 1
            alternatives = rank_candidates(snapshot, task, "latency", self.k, self.enforce_energy)
            if strategy == "balance":
                alternatives.sort(key=lambda c: projected_balance_cost(
                    snapshot, task, c[1], c[2], c[3]))
            else:
                alternatives.sort(key=lambda c: base.seg_cost(
                    snapshot, c[2], "energy", task.data_volume_bits, self.k)
                    + sum(base.seg_cost(snapshot, p, "energy",
                        task.data_volume_bits * task.compression_rho, self.k) for p in c[3])
                    + base.node_penalty(c[1], task, "energy"))
            for _, nd, path_pre, post_paths in alternatives:
                alloc = base.validate_and_build(snapshot, task, nd, path_pre, post_paths,
                                                strategy, self.name, self.enforce_energy)
                if alloc is not None:
                    self.diagnostics["fallback_accepted"] += 1
                    return alloc
        return None

    def _latency_lazy(self, snapshot, task):
        """Same latency objective/validation, reconstruct paths lazily by score band.

        Dijkstra distance bounds avoid constructing paths for every compute node.
        Near ties are rebuilt and ranked with the original floating-point sums,
        preserving the original candidate order and objective tie handling.
        """
        cands = base.candidate_compute_nodes(snapshot, task, self.enforce_energy)
        if not cands:
            return None
        pre_dist, pre_prev = base.single_source(snapshot, task.src, task, "latency",
            task.rate_mbps, task.data_volume_bits, self.k)
        post = {d: base.single_source(snapshot, d, task, "latency",
            task.rate_mbps * task.compression_rho,
            task.data_volume_bits * task.compression_rho, self.k) for d in set(task.dst_set)}
        ordered=[]
        for i,nd in enumerate(cands):
            score=pre_dist.get(nd.id,INF)+base.node_penalty(nd,task,"latency")
            score+=sum(post[d][0].get(nd.id,INF) for d in task.dst_set)
            if score < INF:
                ordered.append((score,i,nd))
        ordered.sort(key=lambda x:(x[0],x[1]))
        j=0
        while j < len(ordered):
            end=j+1
            while end<len(ordered) and ordered[end][0]-ordered[j][0] <= 1e-8:
                end+=1
            exact=[]
            for _,i,nd in ordered[j:end]:
                pre=base.reconstruct(pre_prev,task.src,nd.id)
                paths=[list(reversed(base.reconstruct(post[d][1],d,nd.id))) for d in task.dst_set]
                score=base.seg_cost(snapshot,pre,"latency",0,self.k)
                score+=sum(base.seg_cost(snapshot,p,"latency",0,self.k) for p in paths)
                score+=base.node_penalty(nd,task,"latency")
                exact.append((score,i,nd,pre,paths))
            exact.sort(key=lambda x:(x[0],x[1]))
            for _,_,nd,pre,paths in exact:
                alloc=base.validate_and_build(snapshot,task,nd,pre,paths,"latency",self.name,self.enforce_energy)
                if alloc is not None:
                    return alloc
            j=end
        return None
