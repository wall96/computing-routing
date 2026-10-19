"""S0 最近节点 + 最短跳(朴素基线,《设计文档》§9.3 S0)。

不感知压缩/能耗/策略,只按跳数就近选可行算力节点。用来做对照下限。
"""
from __future__ import annotations
from collections import deque
from typing import Optional, Dict, List
from models import Snapshot, Task, Allocation
from solvers import base
from solvers.base import Solver


def bfs(snapshot: Snapshot, src: str, task: Task, rate_req: float):
    """按跳数的单源;边仅要求 alive 且带宽够 rate_req(粗过滤)。返回 dist(hops), prev。"""
    dist: Dict[str, int] = {src: 0}
    prev: Dict[str, str] = {}
    adj = snapshot.adj()
    q = deque([src])
    while q:
        u = q.popleft()
        if not snapshot.can_relay(u, src):
            continue
        for v in adj.get(u, []):
            if v in dist:
                continue
            lk = snapshot.get_link(u, v)
            if lk is None or not lk.alive or lk.bw_remain_mbps < rate_req:
                continue
            dist[v] = dist[u] + 1
            prev[v] = u
            q.append(v)
    return dist, prev


class S0Nearest(Solver):
    name = "S0"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        cands = base.candidate_compute_nodes(snapshot, task, self.enforce_energy)
        if not cands or task.src not in snapshot.nodes:
            return None
        dist_pre, prev_pre = bfs(snapshot, task.src, task, task.rate_mbps)
        post_cache = {dst: bfs(snapshot, dst, task, task.rate_mbps * task.compression_rho)
                      for dst in set(task.dst_set)}

        reachable = [(dist_pre[n.id], n) for n in cands if n.id in dist_pre]
        reachable.sort(key=lambda x: x[0])       # 跳数最近优先
        for _, nd in reachable:
            v = nd.id
            path_pre = base.reconstruct(prev_pre, task.src, v)
            if path_pre is None:
                continue
            post_paths: List[List[str]] = []
            ok = True
            for dst in task.dst_set:
                _, prev_d = post_cache[dst]
                p_dv = base.reconstruct(prev_d, dst, v)
                if p_dv is None:
                    ok = False; break
                post_paths.append(list(reversed(p_dv)))
            if not ok:
                continue
            alloc = base.validate_and_build(snapshot, task, nd, path_pre, post_paths,
                                            strategy, self.name, self.enforce_energy)
            if alloc is not None:
                return alloc
        return None
