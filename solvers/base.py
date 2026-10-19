"""Solver 基类 + 全体共享工具(《设计文档》§9.1)。

所有 solver 只负责"选 v* + 找两段路";硬约束校验 / 能耗 / 组装 统一走本模块,
保证不同算法在同一口径下公平对比。
"""
from __future__ import annotations
from heapq import heappush, heappop
from typing import Dict, List, Tuple, Optional

from models import Node, Task, Snapshot, NodeType, Allocation
from energy import compute_energy_J, compute_power_W, compute_latency_ms, path_tx_energy_J

INF = float("inf")


# ============= 候选算力节点 =============
def candidate_compute_nodes(snapshot: Snapshot, task: Task,
                            enforce_energy: bool = True) -> List[Node]:
    eligible = snapshot.compute_eligible
    out = []
    for n in snapshot.nodes.values():
        if n.type.value not in eligible:
            continue
        if n.type == NodeType.ACCESS:
            continue
        if n.capacity_remain < task.compute_rate:
            continue
        if enforce_energy and n.power_remain_W < compute_power_W(n, task):
            continue
        out.append(n)
    return out


# ============= 边权 / Dijkstra =============
def edge_weight(link, task: Task, strategy: str, rate_req: float, bits: float, k: int) -> float:
    if not link.alive or link.bw_remain_mbps < rate_req:
        return INF
    if link.delay_ms > task.delay_budget_ms:
        return INF
    if strategy == "latency":
        return link.delay_ms
    if strategy == "balance":
        u_e = max(0.0, 1.0 - link.bw_remain_mbps / max(1e-9, link.bw_total_mbps))
        # Marginal convex congestion cost: include the request being placed.
        # No fixed hop epsilon: it would overwhelm the cost of low-rate flows.
        inc = rate_req / max(1e-9, link.bw_total_mbps)
        return inc * (2.0 * u_e + inc)
    if strategy == "energy":
        return link.beta * bits
    raise ValueError(strategy)


def single_source(snapshot: Snapshot, source: str, task: Task, strategy: str,
                  rate_req: float, bits: float, k: int,
                  banned_edges: set = None, banned_nodes: set = None
                  ) -> Tuple[Dict[str, float], Dict[str, str]]:
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
        if not snapshot.can_relay(u, source):
            continue
        for v in adj.get(u, []):
            if banned_nodes and v in banned_nodes and v != source:
                continue
            if banned_edges:
                ek = (u, v) if u < v else (v, u)
                if ek in banned_edges:
                    continue
            lk = snapshot.get_link(u, v)
            w = edge_weight(lk, task, strategy, rate_req, bits, k)
            if w == INF:
                continue
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd; prev[v] = u
                heappush(pq, (nd, v))
    return dist, prev


def reconstruct(prev: Dict[str, str], src: str, dst: str) -> Optional[List[str]]:
    if src == dst:
        return [src]
    if dst not in prev:
        return None
    path = [dst]; cur = dst
    while cur != src:
        if cur not in prev:
            return None
        cur = prev[cur]; path.append(cur)
    return list(reversed(path))


# ============= 段代价 / 节点惩罚 =============
def seg_cost(snapshot: Snapshot, path: List[str], strategy: str, bits: float, k: int) -> float:
    if len(path) < 2:
        return 0.0
    if strategy == "latency":
        return sum(snapshot.get_link(u, v).delay_ms for u, v in zip(path[:-1], path[1:]))
    if strategy == "balance":
        m = 0.0
        for u, v in zip(path[:-1], path[1:]):
            lk = snapshot.get_link(u, v)
            m = max(m, max(0.0, 1 - lk.bw_remain_mbps / max(1e-9, lk.bw_total_mbps)))
        return m
    if strategy == "energy":
        return path_tx_energy_J(snapshot, path, bits)
    raise ValueError(strategy)


def node_penalty(node: Node, task: Task, strategy: str) -> float:
    if strategy == "latency":
        return compute_latency_ms(node, task)          # 倾向快算节点
    if strategy == "balance":
        if node.capacity_total <= 0:
            return 0.0
        return 0.5 * (1 - node.capacity_remain / node.capacity_total)
    if strategy == "energy":
        return compute_energy_J(node, task.cycles_total)
    return 0.0


# ============= 路径几何量 =============
def path_delay(snapshot: Snapshot, path: List[str]) -> float:
    d = 0.0
    for u, v in zip(path[:-1], path[1:]):
        lk = snapshot.get_link(u, v)
        if lk is None:
            return INF
        d += lk.delay_ms
    return d


def path_survive(snapshot: Snapshot, path: List[str]) -> float:
    s = 1.0
    for u, v in zip(path[:-1], path[1:]):
        lk = snapshot.get_link(u, v)
        if lk is None:
            return 0.0
        s *= (1 - lk.loss)
    return s


# ============= 硬约束校验 + 组装 =============
def compute_bw_per_link(task: Task, path_pre: List[str],
                        path_post_list: List[List[str]]) -> Dict[Tuple[str, str], float]:
    bw: Dict[Tuple[str, str], float] = {}
    r0 = task.rate_mbps
    for u, v in zip(path_pre[:-1], path_pre[1:]):
        key = (u, v) if u < v else (v, u)
        bw[key] = bw.get(key, 0.0) + r0
    r_post = r0 * task.compression_rho
    for p in path_post_list:
        for u, v in zip(p[:-1], p[1:]):
            key = (u, v) if u < v else (v, u)
            bw[key] = bw.get(key, 0.0) + r_post
    return bw


def validate_and_build(snapshot: Snapshot, task: Task, node: Node,
                       path_pre: List[str], path_post_list: List[List[str]],
                       strategy: str, solver_name: str,
                       enforce_energy: bool = True) -> Optional[Allocation]:
    """全部硬约束校验 + 组装 Allocation。任一不满足返回 None。"""
    if not path_pre or path_pre[0] != task.src or path_pre[-1] != node.id:
        return None
    # 终端只能作首/尾节点,不能出现在任何路径中间
    if snapshot.endpoint_only and any(
            n in snapshot.endpoint_only for p in [path_pre, *path_post_list] for n in p[1:-1]):
        return None
    t_comp = compute_latency_ms(node, task)
    d_pre = path_delay(snapshot, path_pre)
    if d_pre == INF:
        return None
    surv_pre = path_survive(snapshot, path_pre)

    max_delay = 0.0
    max_loss = 0.0
    for dst, p_post in zip(task.dst_set, path_post_list):
        if not p_post or p_post[0] != node.id or p_post[-1] != dst:
            return None
        d_post = path_delay(snapshot, p_post)
        if d_post == INF:
            return None
        chain_delay = d_pre + t_comp + d_post
        if chain_delay > task.delay_budget_ms:
            return None
        chain_loss = 1 - surv_pre * path_survive(snapshot, p_post)
        if chain_loss > task.loss_budget:
            return None
        max_delay = max(max_delay, chain_delay)
        max_loss = max(max_loss, chain_loss)

    # 带宽聚合校验(前段 r0 + 各后段 ρr0 累加)
    bw = compute_bw_per_link(task, path_pre, path_post_list)
    for (u, v), need in bw.items():
        lk = snapshot.get_link(u, v)
        if lk is None or lk.bw_remain_mbps < need - 1e-6:
            return None

    # 算力 / 功率
    p_comp = compute_power_W(node, task)
    if node.capacity_remain < task.compute_rate:
        return None
    if enforce_energy and node.power_remain_W < p_comp:
        return None

    D0 = task.data_volume_bits
    e_tx = path_tx_energy_J(snapshot, path_pre, D0)
    e_tx += sum(path_tx_energy_J(snapshot, p, D0 * task.compression_rho) for p in path_post_list)
    return Allocation(
        task=task, compute_node=node.id, path_pre=path_pre, path_post=list(path_post_list),
        bw_per_link=bw, compute_node_type=node.type.value, compute_latency_ms=t_comp,
        delivered_data_bits=D0 * task.compression_rho,
        energy_compute_J=compute_energy_J(node, task.cycles_total), energy_tx_J=e_tx,
        compute_rate_used=task.compute_rate, power_used_W=p_comp,
        strategy=strategy, solver=solver_name, max_delay_ms=max_delay, max_loss=max_loss,
    )


class Solver:
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.acfg = cfg["algorithm"]
        self.k = self.acfg.get("load_balance_k", 4)
        self.enforce_energy = self.acfg.get("enforce_energy_budget", True)

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        raise NotImplementedError
