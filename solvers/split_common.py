"""拆分卸载(S6/S7)共用:候选算力节点 + 两段路径 + 线性完成时间系数。

包模型下,若子包比例为 x,经候选 k 的子包到达时间近似为
    t_k(x) = d_k + a_k · x
  d_k = 前段传播 + 固定处理 + max_j 后段传播            (与 x 无关)
  a_k = 整包前段传输 + 整包计算 + max_j 整包后段传输     (与 x 成正比)
其中传输按直通转发取路径瓶颈速率:比特 / min_e 速率_e。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from heapq import heappush, heappop
from typing import Dict, List, Optional, Tuple

from models import Node, NodeType, Snapshot, Task
from energy import compute_ms_per_share, compute_power_W
from solvers import base
from solvers.base import INF


@dataclass
class Cand:
    node: Node
    pre: List[str]
    posts: List[List[str]]
    prop_pre: float
    prop_posts: List[float]
    fixed: float              # 固定处理时延 ms
    comp: float               # 整包计算时延(比例部分) ms
    tx_pre: float             # 整包前段传输 ms(单独使用该路径时)
    tx_posts: List[float]
    node_cap: float           # 算力/功率允许的最大比例
    bw_cap: float             # 带宽允许的最大比例(单独使用该路径时)
    uplink: Tuple[str, str] = field(default=("", ""))

    @property
    def d(self) -> float:
        return self.prop_pre + self.fixed + max(self.prop_posts, default=0.0)

    @property
    def a(self) -> float:
        return self.tx_pre + self.comp + max(self.tx_posts, default=0.0)

    @property
    def cap(self) -> float:
        return min(1.0, self.node_cap, self.bw_cap)


def _edges(path):
    return [base._ekey(u, v) for u, v in zip(path[:-1], path[1:])]


def tx_dijkstra(snapshot: Snapshot, source: str, task: Task, bits: float,
                first_hop: Optional[str] = None):
    """边权 = 传播时延 + bits/物理速率 的单源最短路(终端不中转,超预算剪枝)。

    first_hop 给定时强制第一跳(源终端必须经指定的上行卫星)。
    """
    budget = task.delay_budget_ms
    dist: Dict[str, float] = {source: 0.0}
    prev: Dict[str, str] = {}
    adj = snapshot.adj()
    pq = [(0.0, source)]
    while pq:
        d, u = heappop(pq)
        if d > dist.get(u, INF):
            continue
        if not snapshot.can_relay(u, source):
            continue
        for v in adj.get(u, []):
            if first_hop is not None and u == source and v != first_hop:
                continue
            lk = snapshot.get_link(u, v)
            if lk is None or not lk.alive or lk.bw_remain_mbps <= 1e-6:
                continue
            nd = d + lk.delay_ms + (bits / (lk.bw_total_mbps * 1e6) * 1000.0 if bits > 0 else 0.0)
            if nd > budget or nd >= dist.get(v, INF):
                continue
            dist[v] = nd; prev[v] = u
            heappush(pq, (nd, v))
    return dist, prev


def _bottleneck_tx_ms(snapshot: Snapshot, path: List[str], bits: float) -> float:
    if bits <= 0 or len(path) < 2:
        return 0.0
    rate = min(snapshot.get_link(u, v).bw_total_mbps for u, v in zip(path[:-1], path[1:]))
    return bits / (rate * 1e6) * 1000.0


def build_candidates(snapshot: Snapshot, task: Task, enforce_energy: bool = True,
                     per_uplink: int = 15) -> List[Cand]:
    """候选 (算力节点, 前段路径),按 d 升序。

    源为地面终端时按上行链路分别生成:对每颗覆盖源终端的卫星,强制第一跳经它,
    各保留综合距离最小的 per_uplink 个算力节点。否则单棵最短路树会让所有候选
    挤在同一条最快的上行链路上,拆分无法利用其它上行链路分担传输。
    同一节点可能经不同上行链路出现多次,由调用方保证一个节点只用一次。
    """
    if task.src not in snapshot.nodes:
        return []
    D, rho = task.packet_bits, task.compression_rho
    post = {dst: tx_dijkstra(snapshot, dst, task, D * rho) for dst in set(task.dst_set)}
    eligible = [n for n in snapshot.nodes.values()
                if n.type.value in snapshot.compute_eligible
                and n.type not in (NodeType.ACCESS, NodeType.TERMINAL)
                and n.capacity_remain > 0
                and all(n.id in post[d][0] for d in task.dst_set)]
    first_hops = (snapshot.adj().get(task.src, []) if task.src in snapshot.endpoint_only
                  else [None])
    out: List[Cand] = []
    for fh in first_hops:
        dist_pre, prev_pre = tx_dijkstra(snapshot, task.src, task, D, first_hop=fh)
        reach = [n for n in eligible if n.id in dist_pre]
        reach.sort(key=lambda n: dist_pre[n.id] + max(post[d][0][n.id] for d in task.dst_set))
        for n in reach[:per_uplink]:
            c = _make_cand(snapshot, task, n, base.reconstruct(prev_pre, task.src, n.id),
                           post, enforce_energy)
            if c is not None:
                out.append(c)
    out.sort(key=lambda c: (c.d, c.a, c.node.id))
    return out


def _make_cand(snapshot, task, n, pre, post, enforce_energy) -> Optional[Cand]:
    D, rho = task.packet_bits, task.compression_rho
    if pre is None:
        return None
    posts = [base.reconstruct(post[d][1], d, n.id) for d in task.dst_set]
    if any(p is None for p in posts):
        return None
    posts = [list(reversed(p)) for p in posts]
    node_cap = n.capacity_remain / task.compute_rate if task.compute_rate > 0 else 1.0
    power = compute_power_W(n, task)
    if enforce_energy and power > 0:
        node_cap = min(node_cap, n.power_remain_W / power)
    bw_cap = INF
    for (u, v), need in base.compute_bw_per_link(task, pre, posts).items():
        if need > 0:
            bw_cap = min(bw_cap, snapshot.get_link(u, v).bw_remain_mbps / need)
    c = Cand(node=n, pre=pre, posts=posts,
             prop_pre=base.path_delay(snapshot, pre),
             prop_posts=[base.path_delay(snapshot, p) for p in posts],
             fixed=task.fixed_processing_total_ms, comp=compute_ms_per_share(n, task),
             tx_pre=_bottleneck_tx_ms(snapshot, pre, D),
             tx_posts=[_bottleneck_tx_ms(snapshot, p, D * rho) for p in posts],
             node_cap=node_cap, bw_cap=bw_cap,
             uplink=_edges(pre)[0] if len(pre) > 1 else (task.src, task.src))
    if c.d < task.delay_budget_ms and c.cap > 1e-9:
        return c
    return None


def try_build(snapshot, task, cands: List[Cand], shares, strategy, solver_name, enforce_energy):
    parts = [(c.node, x, c.pre, c.posts) for c, x in zip(cands, shares) if x > 1e-6]
    total = sum(p[1] for p in parts)
    if total <= 0:
        return None
    parts = [(n, x / total, pre, posts) for n, x, pre, posts in parts]
    return base.build_split_allocation(snapshot, task, parts, strategy, solver_name, enforce_energy)


def best_single(snapshot, task, cands: List[Cand], strategy, solver_name, enforce_energy,
                tries: int = 30):
    """不拆分时的最优单节点:按单路径完成时间 d+a 升序逐个精确校验。"""
    for c in sorted((c for c in cands if c.cap >= 1.0 - 1e-9), key=lambda c: (c.d + c.a, c.node.id))[:tries]:
        alloc = try_build(snapshot, task, [c], [1.0], strategy, solver_name, enforce_energy)
        if alloc is not None:
            return alloc
    return None


def require_latency(strategy: str, name: str):
    if strategy != "latency":
        raise ValueError(f"{name} 目前只实现 latency 策略(最小化最晚子包到达时间),收到 {strategy}")
