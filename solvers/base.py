"""Solver 基类 + 全体共享工具(《设计文档》§9.1)。

所有 solver 只负责"选 v* + 找两段路";硬约束校验 / 能耗 / 组装 统一走本模块,
保证不同算法在同一口径下公平对比。
"""
from __future__ import annotations
from heapq import heappush, heappop
from typing import Dict, List, Tuple, Optional

from models import Node, Task, Snapshot, NodeType, Allocation, SubAlloc
from energy import (compute_energy_J, compute_power_W, compute_latency_ms, compute_ms_per_share,
                    path_tx_energy_J)

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


# ============= 包模型:传输时延 =============
def _ekey(u: str, v: str) -> Tuple[str, str]:
    return (u, v) if u < v else (v, u)


def split_edge_bits(task: Task, parts) -> Dict[Tuple[str, str], float]:
    """各链路上本任务所有子包的总比特数。parts: [(share, path_pre, path_post_list)]。"""
    D, rho = task.packet_bits, task.compression_rho
    load: Dict[Tuple[str, str], float] = {}
    for share, pre, posts in parts:
        for u, v in zip(pre[:-1], pre[1:]):
            load[_ekey(u, v)] = load.get(_ekey(u, v), 0.0) + share * D
        for p in posts:
            for u, v in zip(p[:-1], p[1:]):
                load[_ekey(u, v)] = load.get(_ekey(u, v), 0.0) + share * D * rho
    return load


def segment_tx_ms(snapshot: Snapshot, path: List[str], load: Dict[Tuple[str, str], float]) -> float:
    """一段路径的传输时延(ms)。

    直通(流水线)转发:一段的传输时延取路径上最慢的一跳,即 max_e 比特_e / 速率_e;
    同一任务的多个子包共用某链路时,该链路上的比特数累加(串行发送,保守上界)。
    速率取链路当前片的物理速率 bw_total。
    """
    t = 0.0
    for u, v in zip(path[:-1], path[1:]):
        lk = snapshot.get_link(u, v)
        bits = load.get(_ekey(u, v), 0.0)
        if bits > 0:
            t = max(t, bits / (lk.bw_total_mbps * 1e6) * 1000.0)
    return t


def evaluate_splits(snapshot: Snapshot, task: Task, parts):
    """包模型下逐子包到达时间。parts: [(node, share, path_pre, path_post_list)]。

    子包 k 到达目的 j 的时刻 = 前段传播 + 前段传输 + 计算(share) + 后段传播 + 后段传输;
    整包时延取所有子包、所有目的中最晚者;整包丢失 = 任一子包丢失。
    返回 (max_delay_ms, max_loss, [各子包最晚到达 ms], [各子包计算时延 ms])。
    """
    load = split_edge_bits(task, [(s, pre, posts) for _, s, pre, posts in parts])
    finishes, comps = [], []
    surv_by_dst = [1.0] * len(task.dst_set)
    for node, share, pre, posts in parts:
        head = path_delay(snapshot, pre) + segment_tx_ms(snapshot, pre, load)
        t_comp = compute_latency_ms(node, task, share)
        surv_pre = path_survive(snapshot, pre)
        f = 0.0
        for j, p in enumerate(posts):
            f = max(f, head + t_comp + path_delay(snapshot, p) + segment_tx_ms(snapshot, p, load))
            surv_by_dst[j] *= surv_pre * path_survive(snapshot, p)
        finishes.append(f); comps.append(t_comp)
    max_loss = max(1 - s for s in surv_by_dst) if surv_by_dst else 0.0
    return max(finishes), max_loss, finishes, comps


def _paths_ok(snapshot: Snapshot, task: Task, node_id: str, pre, posts) -> bool:
    if not pre or pre[0] != task.src or pre[-1] != node_id:
        return False
    if len(posts) != len(task.dst_set):
        return False
    for dst, p in zip(task.dst_set, posts):
        if not p or p[0] != node_id or p[-1] != dst:
            return False
    # 终端只能作首/尾节点,不能出现在任何路径中间
    if snapshot.endpoint_only and any(
            n in snapshot.endpoint_only for p in [pre, *posts] for n in p[1:-1]):
        return False
    return all(path_delay(snapshot, p) != INF for p in [pre, *posts])


def build_split_allocation(snapshot: Snapshot, task: Task, parts, strategy: str,
                           solver_name: str, enforce_energy: bool = True) -> Optional[Allocation]:
    """拆分方案的全部硬约束校验 + 组装。parts: [(node, share, path_pre, path_post_list)]。

    带宽/算力/功率按比例 share 占用;时延、丢包按 evaluate_splits 精确核算。
    """
    parts = [p for p in parts if p[1] > 1e-9]
    if not parts or abs(sum(p[1] for p in parts) - 1.0) > 1e-6:
        return None
    if len({p[0].id for p in parts}) != len(parts):
        return None                       # 同一节点只允许一个子包
    for node, _, pre, posts in parts:
        if not _paths_ok(snapshot, task, node.id, pre, posts):
            return None
    max_delay, max_loss, finishes, comps = evaluate_splits(snapshot, task, parts)
    if max_delay > task.delay_budget_ms or max_loss > task.loss_budget:
        return None

    bw: Dict[Tuple[str, str], float] = {}
    for _, share, pre, posts in parts:
        for key, need in compute_bw_per_link(task, pre, posts).items():
            bw[key] = bw.get(key, 0.0) + need * share
    for (u, v), need in bw.items():
        lk = snapshot.get_link(u, v)
        if lk is None or lk.bw_remain_mbps < need - 1e-6:
            return None

    subs: List[SubAlloc] = []
    e_comp = e_tx = 0.0
    D0, rho = task.data_volume_bits, task.compression_rho
    for (node, share, pre, posts), f, tc in zip(parts, finishes, comps):
        rate = task.compute_rate * share
        power = compute_power_W(node, task) * share
        if node.capacity_remain < rate - 1e-6:
            return None
        if enforce_energy and node.power_remain_W < power - 1e-9:
            return None
        subs.append(SubAlloc(node=node.id, share=share, path_pre=pre, path_post=list(posts),
                             compute_rate_used=rate, power_used_W=power,
                             compute_latency_ms=tc, finish_ms=f))
        e_comp += compute_energy_J(node, task.cycles_total * share)
        e_tx += path_tx_energy_J(snapshot, pre, D0 * share)
        e_tx += sum(path_tx_energy_J(snapshot, p, D0 * share * rho) for p in posts)

    main = max(subs, key=lambda s: s.share)
    main_node = snapshot.nodes[main.node]
    return Allocation(
        task=task, compute_node=main.node, path_pre=main.path_pre, path_post=main.path_post,
        bw_per_link=bw, compute_node_type=main_node.type.value,
        compute_latency_ms=max(comps), delivered_data_bits=D0 * rho,
        energy_compute_J=e_comp, energy_tx_J=e_tx,
        compute_rate_used=task.compute_rate, power_used_W=sum(s.power_used_W for s in subs),
        strategy=strategy, solver=solver_name, max_delay_ms=max_delay, max_loss=max_loss,
        splits=subs if len(subs) > 1 else [],
    )


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
    # 包模型:加上传输时延(单节点 = share 1 的单个子包);流式模型此项为 0
    load = split_edge_bits(task, [(1.0, path_pre, path_post_list)]) if task.packet_bits > 0 else {}
    tx_pre = segment_tx_ms(snapshot, path_pre, load) if load else 0.0

    max_delay = 0.0
    max_loss = 0.0
    for dst, p_post in zip(task.dst_set, path_post_list):
        if not p_post or p_post[0] != node.id or p_post[-1] != dst:
            return None
        d_post = path_delay(snapshot, p_post)
        if d_post == INF:
            return None
        chain_delay = d_pre + t_comp + d_post
        if load:
            chain_delay += tx_pre + segment_tx_ms(snapshot, p_post, load)
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
