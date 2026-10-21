"""拆分卸载(S6/S7)共用:按接入卫星分组的候选算力节点 + 线性完成时间系数。

拆分只能在星上:终端无算力,整包先上行到接入卫星 A,A 收齐后拆分,各子包再经星间链路
送往不同算力节点计算,最后各自下行到目的终端汇合。对一颗接入卫星 A,经候选 k 的子包
(比例 x)到达时间近似为
    t_k(x) = d_k + a_k · x
  d_k = 上行传播 + 整包上行传输 + 拆分开销 + 星间传播 + 固定处理 + max_j 后段传播   (与 x 无关)
  a_k = 整包星间传输 + 整包计算 + max_j 整包后段传输                               (与 x 成正比)
传输按直通转发取路径瓶颈速率:比特 / min_e 速率_e。
"""
from __future__ import annotations
from dataclasses import dataclass
from heapq import heappush, heappop
from typing import Dict, List, Optional, Tuple

from models import Node, NodeType, Snapshot, Task
from energy import compute_ms_per_share, compute_power_W
from solvers import base
from solvers.base import INF


@dataclass
class Cand:
    node: Node
    pre: List[str]            # 完整前段路径:源 → (接入卫星 →) 算力节点
    posts: List[List[str]]
    access: str               # 接入卫星(拆分点)
    head: float               # 拆分点之前的时间 ms:上行传播 + 整包上行传输 + 拆分开销
    prop_isl: float           # 接入卫星 → 算力节点 传播 ms
    prop_posts: List[float]
    fixed: float              # 固定处理时延 ms
    comp: float               # 整包计算时延(比例部分) ms
    tx_isl: float             # 整包星间传输 ms
    tx_posts: List[float]
    node_cap: float           # 算力/功率允许的最大比例
    bw_cap: float             # 带宽允许的最大比例(单独使用该路径时)
    branch: str               # 拆分后的第一跳(接入卫星本身计算时为 access)

    @property
    def isl(self) -> List[str]:
        return self.pre[self.pre.index(self.access):]

    @property
    def d(self) -> float:
        return self.head + self.prop_isl + self.fixed + max(self.prop_posts, default=0.0)

    @property
    def a(self) -> float:
        return self.tx_isl + self.comp + max(self.tx_posts, default=0.0)

    @property
    def cap(self) -> float:
        return min(1.0, self.node_cap, self.bw_cap)


def _edges(path):
    return [base._ekey(u, v) for u, v in zip(path[:-1], path[1:])]


def tx_dijkstra(snapshot: Snapshot, source: str, task: Task, bits: float, budget: float = None):
    """边权 = 传播时延 + bits/物理速率 的单源最短路(终端不中转,超预算剪枝)。"""
    budget = task.delay_budget_ms if budget is None else budget
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


def _eligible(snapshot: Snapshot, n: Node) -> bool:
    return (n.type.value in snapshot.compute_eligible
            and n.type not in (NodeType.ACCESS, NodeType.TERMINAL) and n.capacity_remain > 0)


def build_groups(snapshot: Snapshot, task: Task, enforce_energy: bool = True,
                 split_coef: float = 0.0, max_access: int = 3,
                 per_access: int = 40) -> Dict[str, List[Cand]]:
    """{接入卫星: 候选列表(按 d 升序)}。

    源为终端:取上行最快的 max_access 颗覆盖卫星作为拆分点;源为卫星:源自身即拆分点。
    每个拆分点保留综合距离最小的 per_access 个算力节点。
    """
    if task.src not in snapshot.nodes:
        return {}
    D, rho = task.packet_bits, task.compression_rho
    post = {dst: tx_dijkstra(snapshot, dst, task, D * rho) for dst in set(task.dst_set)}
    ok_dst = lambda nid: all(nid in post[d][0] for d in task.dst_set)
    over = base.split_overhead_ms(task, split_coef)

    if task.src in snapshot.endpoint_only:
        ups = []
        for a in snapshot.adj().get(task.src, []):
            lk = snapshot.get_link(task.src, a)
            if lk is None or not lk.alive or lk.bw_remain_mbps <= 1e-6:
                continue
            ups.append((lk.delay_ms + _bottleneck_tx_ms(snapshot, [task.src, a], D), a))
        ups.sort()
        accesses = [(a, t) for t, a in ups[:max_access]]
    else:
        accesses = [(task.src, 0.0)]

    groups: Dict[str, List[Cand]] = {}
    for a, up_ms in accesses:
        head = up_ms + over
        dist, prev = tx_dijkstra(snapshot, a, task, D, budget=task.delay_budget_ms - head)
        prefix = [task.src] if a != task.src else []
        reach = [n for n in snapshot.nodes.values()
                 if _eligible(snapshot, n) and n.id in dist and ok_dst(n.id)]
        reach.sort(key=lambda n: (dist[n.id] + max(post[d][0][n.id] for d in task.dst_set), n.id))
        out = []
        for n in reach[:per_access]:
            isl = base.reconstruct(prev, a, n.id)
            c = _make_cand(snapshot, task, n, prefix + isl, a, head, post, enforce_energy)
            if c is not None:
                out.append(c)
        out.sort(key=lambda c: (c.d, c.a, c.node.id))
        if out:
            groups[a] = out
    return groups


def _make_cand(snapshot, task, n, pre, access, head, post, enforce_energy) -> Optional[Cand]:
    D, rho = task.packet_bits, task.compression_rho
    posts = [base.reconstruct(post[d][1], d, n.id) for d in task.dst_set]
    if any(p is None for p in posts):
        return None
    posts = [list(reversed(p)) for p in posts]
    isl = pre[pre.index(access):]
    node_cap = n.capacity_remain / task.compute_rate if task.compute_rate > 0 else 1.0
    power = compute_power_W(n, task)
    if enforce_energy and power > 0:
        node_cap = min(node_cap, n.power_remain_W / power)
    bw_cap = INF
    for (u, v), need in base.compute_bw_per_link(task, isl, posts).items():
        if need > 0:
            bw_cap = min(bw_cap, snapshot.get_link(u, v).bw_remain_mbps / need)
    c = Cand(node=n, pre=pre, posts=posts, access=access, head=head,
             prop_isl=base.path_delay(snapshot, isl),
             prop_posts=[base.path_delay(snapshot, p) for p in posts],
             fixed=task.fixed_processing_total_ms, comp=compute_ms_per_share(n, task),
             tx_isl=_bottleneck_tx_ms(snapshot, isl, D),
             tx_posts=[_bottleneck_tx_ms(snapshot, p, D * rho) for p in posts],
             node_cap=node_cap, bw_cap=bw_cap,
             branch=isl[1] if len(isl) > 1 else access)
    if c.d < task.delay_budget_ms and c.cap > 1e-9:
        return c
    return None


def try_build(snapshot, task, cands: List[Cand], shares, strategy, solver_name, enforce_energy,
              split_coef: float = 0.0):
    parts = [(c.node, x, c.pre, c.posts) for c, x in zip(cands, shares) if x > 1e-6]
    total = sum(p[1] for p in parts)
    if total <= 0:
        return None
    parts = [(n, x / total, pre, posts) for n, x, pre, posts in parts]
    return base.build_split_allocation(snapshot, task, parts, strategy, solver_name,
                                       enforce_energy, split_coef)


def best_single(snapshot, task, groups: Dict[str, List[Cand]], strategy, solver_name,
                enforce_energy, tries: int = 30):
    """不拆分的最优单节点:直通转发,按精确单路径时延升序逐个校验。"""
    seen, ranked = set(), []
    for cands in groups.values():
        for c in cands:
            if c.node.id in seen or c.cap < 1.0 - 1e-9:
                continue
            seen.add(c.node.id)
            delay = base.evaluate_splits(snapshot, task, [(c.node, 1.0, c.pre, c.posts)])[0]
            ranked.append((delay, c.node.id, c))
    ranked.sort(key=lambda r: (r[0], r[1]))
    for _, _, c in ranked[:tries]:
        alloc = try_build(snapshot, task, [c], [1.0], strategy, solver_name, enforce_energy)
        if alloc is not None:
            return alloc
    return None


def better(a, b, min_gain_ms: float = 0.0):
    """a(拆分)是否比 b(不拆分)好出 min_gain_ms 以上。"""
    if a is None:
        return False
    return b is None or a.max_delay_ms < b.max_delay_ms - min_gain_ms


def split_settings(acfg: dict):
    s = acfg.get("split", {})
    return (int(s.get("max_splits", 0)), float(s.get("overhead_s_per_Tbit", 0.01)),
            float(s.get("min_gain_ms", 0.0)), int(s.get("max_access", 3)))


def require_latency(strategy: str, name: str):
    if strategy != "latency":
        raise ValueError(f"{name} 目前只实现 latency 策略(最小化最晚子包到达时间),收到 {strategy}")
