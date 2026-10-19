"""M4 路由决策核心：硬约束过滤 + 加权 Dijkstra + 联合打分。

设计方案 §8.1–§8.3。
"""
from __future__ import annotations
import math
from heapq import heappush, heappop
from typing import Dict, List, Tuple, Optional, Iterable

from models import Node, Link, Task, Snapshot, NodeType, BizType, Allocation
from energy import compute_energy_J, path_tx_energy_J


INF = float("inf")


# ============= 业务硬约束 =============
def edge_violates_hard(link: Link, task: Task) -> bool:
    """单条边层面就能拒绝的硬约束（如带宽不够）。"""
    if not link.alive:
        return True
    if link.bw_remain_mbps < task.rate_mbps:
        return True
    return False


def path_violates_hard(path: List[str], snapshot: Snapshot, task: Task) -> Optional[str]:
    """路径级硬约束检查。返回违反原因，None 表示通过。"""
    if len(path) < 2:
        return None
    total_delay = 0.0
    survive = 1.0
    bottleneck_bw = INF
    for u, v in zip(path[:-1], path[1:]):
        link = snapshot.get_link(u, v)
        if link is None:
            return f"链路缺失 ({u},{v})"
        total_delay += link.delay_ms
        survive *= (1 - link.loss)
        bottleneck_bw = min(bottleneck_bw, link.bw_remain_mbps)
    cum_loss = 1 - survive
    # 业务级阈值
    if total_delay > task.delay_budget_ms:
        return f"时延 {total_delay:.2f} > {task.delay_budget_ms}"
    if cum_loss > task.loss_budget:
        return f"丢包 {cum_loss:.2e} > {task.loss_budget:.2e}"
    if bottleneck_bw < task.rate_mbps:
        return f"带宽 {bottleneck_bw:.1f} < {task.rate_mbps:.1f}"
    return None


# ============= 边权 =============
def edge_weight(link: Link, task: Task, strategy: str, k: int = 4) -> float:
    """三种策略 + 业务硬约束剪枝。返回 INF 表示该边不可用。"""
    if edge_violates_hard(link, task):
        return INF
    # 时延阈值剪枝（避免无效搜索）
    if link.delay_ms > task.delay_budget_ms:
        return INF
    if strategy == "latency":
        return link.delay_ms
    elif strategy == "balance":
        u_e = max(0.0, 1.0 - link.bw_remain_mbps / max(1.0, link.bw_total_mbps))
        return u_e ** k + 1e-6        # +ε 避免全 0 等价
    elif strategy == "energy":
        return link.beta * task.data_volume_bits   # 单位 J
    else:
        raise ValueError(f"未知策略 {strategy}")


# ============= Dijkstra =============
def dijkstra(snapshot: Snapshot, src: str, task: Task,
             strategy: str, k: int = 4,
             allowed_nodes: Optional[Iterable[str]] = None
             ) -> Tuple[Dict[str, float], Dict[str, str]]:
    """src 单源最短路。返回 (距离 dist, 前驱 prev)。"""
    dist: Dict[str, float] = {nid: INF for nid in snapshot.nodes}
    prev: Dict[str, str] = {}
    if src not in dist:
        return dist, prev
    dist[src] = 0.0
    pq: List[Tuple[float, str]] = [(0.0, src)]
    allowed = set(allowed_nodes) if allowed_nodes is not None else None
    # 邻接表（按需）
    adj: Dict[str, List[str]] = {}
    for (u, v) in snapshot.links:
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)
    while pq:
        d, u = heappop(pq)
        if d > dist[u]:
            continue
        for v in adj.get(u, []):
            if allowed is not None and v not in allowed and v != src:
                continue
            link = snapshot.get_link(u, v)
            w = edge_weight(link, task, strategy, k)
            if w == INF:
                continue
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heappush(pq, (nd, v))
    return dist, prev


def reconstruct_path(prev: Dict[str, str], src: str, dst: str) -> Optional[List[str]]:
    if src == dst:
        return [src]
    if dst not in prev:
        return None
    path = [dst]
    cur = dst
    while cur != src:
        if cur not in prev:
            return None
        cur = prev[cur]
        path.append(cur)
    return list(reversed(path))


# ============= 候选算力节点筛选 =============
def candidate_compute_nodes(snapshot: Snapshot, task: Task, scenario: str) -> List[Node]:
    """按场景剪裁 + 算力够用的节点。"""
    if scenario == "A":
        eligible_types = (NodeType.LEO, NodeType.EDGE, NodeType.DC)
    elif scenario == "B":
        eligible_types = (NodeType.LEO, NodeType.GEO)
    else:
        raise ValueError(scenario)
    out = []
    for n in snapshot.nodes.values():
        if n.type not in eligible_types:
            continue
        if n.capacity_remain < task.compute_rate_tflops:
            continue
        out.append(n)
    return out


# ============= 联合打分 =============
def path_cost_for_strategy(snapshot: Snapshot, path: List[str],
                           task: Task, strategy: str, k: int = 4) -> float:
    if len(path) < 2:
        return 0.0
    if strategy == "latency":
        return sum(snapshot.get_link(u, v).delay_ms for u, v in zip(path[:-1], path[1:]))
    elif strategy == "balance":
        u_max = 0.0
        for u, v in zip(path[:-1], path[1:]):
            link = snapshot.get_link(u, v)
            ue = max(0.0, 1 - link.bw_remain_mbps / max(1.0, link.bw_total_mbps))
            u_max = max(u_max, ue)
        return u_max
    elif strategy == "energy":
        return path_tx_energy_J(snapshot, path, task.data_volume_bits)
    raise ValueError(strategy)


def node_penalty(node: Node, task: Task, strategy: str) -> float:
    if strategy == "latency":
        return 0.0
    if strategy == "balance":
        # 算力越满，分数越高（越不愿意放）
        if node.capacity_total <= 0:
            return 0.0
        return 0.5 * (1 - node.capacity_remain / node.capacity_total)
    if strategy == "energy":
        return compute_energy_J(node, task.cycles_total)
    return 0.0


# ============= 单任务决策 =============
def solve_task(snapshot: Snapshot, task: Task, scenario: str,
               algorithm_cfg: dict,
               strategy_override: Optional[str] = None) -> Optional[Allocation]:
    """处理一个任务（含多连接）。返回 Allocation 或 None（拒绝）。"""
    # 选策略
    strategy = strategy_override or _default_strategy(task.biz)
    k = algorithm_cfg.get("load_balance_k", 4)

    candidates = candidate_compute_nodes(snapshot, task, scenario)
    if not candidates:
        return None

    src = task.src
    if src not in snapshot.nodes:
        return None

    # 跑一次 src→all
    dist_in, prev_in = dijkstra(snapshot, src, task, strategy, k)

    best_score = INF
    best_pick: Optional[Tuple[Node, List[List[str]], Dict, float]] = None
    # 缓存目的地最短路（多连接每个 dst 复用）
    path_to_dst_cache: Dict[Tuple[str, str], Optional[List[str]]] = {}

    for cand in candidates:
        if dist_in[cand.id] == INF:
            continue
        path_in = reconstruct_path(prev_in, src, cand.id)
        if path_in is None:
            continue
        why = path_violates_hard(path_in, snapshot, task)
        if why is not None:
            continue

        # 多连接：从 cand 到每个 dst 都要可达
        paths_total: List[List[str]] = [path_in]
        path_costs = [path_cost_for_strategy(snapshot, path_in, task, strategy, k)]
        feasible = True

        # cand→各 dst
        # 注：这里把 cand 当源；可能用户的 dst==src（结果回到自己），就回到 src
        if task.dst_set:
            # 对每个 dst 单独做一次 dij（cand 到 dst）；缓存按 (cand, dst)
            need_dij_from_cand = False
            dij_cache_dist = None
            dij_cache_prev = None
            for dst in task.dst_set:
                if dst == cand.id:
                    paths_total.append([cand.id])
                    path_costs.append(0.0)
                    continue
                cache_key = (cand.id, dst)
                if cache_key in path_to_dst_cache:
                    p_out = path_to_dst_cache[cache_key]
                else:
                    if not need_dij_from_cand:
                        dij_cache_dist, dij_cache_prev = dijkstra(snapshot, cand.id, task, strategy, k)
                        need_dij_from_cand = True
                    p_out = reconstruct_path(dij_cache_prev, cand.id, dst) if dij_cache_dist[dst] != INF else None
                    path_to_dst_cache[cache_key] = p_out
                if p_out is None:
                    feasible = False
                    break
                why2 = path_violates_hard(p_out, snapshot, task)
                if why2 is not None:
                    feasible = False
                    break
                paths_total.append(p_out)
                path_costs.append(path_cost_for_strategy(snapshot, p_out, task, strategy, k))

        if not feasible:
            continue

        # 评分：路径成本（合并方式取决于策略）+ 节点惩罚
        if strategy == "balance":
            path_total_cost = max(path_costs)
        else:
            path_total_cost = sum(path_costs)
        score = path_total_cost + node_penalty(cand, task, strategy)

        if score < best_score:
            best_score = score
            best_pick = (cand, paths_total, {}, path_total_cost)

    if best_pick is None:
        return None

    cand, paths_total, _, path_cost = best_pick

    # 真实能耗（独立于策略，为了 KPI）
    e_comp = compute_energy_J(cand, task.cycles_total)
    e_tx = sum(path_tx_energy_J(snapshot, p, task.data_volume_bits) for p in paths_total)

    # 资源占用簿记
    bw_per_link: Dict[Tuple[str, str], float] = {}
    for p in paths_total:
        for u, v in zip(p[:-1], p[1:]):
            key = (u, v) if u < v else (v, u)
            bw_per_link[key] = bw_per_link.get(key, 0.0) + task.rate_mbps

    return Allocation(
        task=task,
        compute_node=cand.id,
        paths=paths_total,
        energy_compute_J=e_comp,
        energy_tx_J=e_tx,
        bw_per_link=bw_per_link,
        accepted=True,
        strategy=strategy,
    )


def _default_strategy(biz: BizType) -> str:
    return {
        BizType.LL: "latency",
        BizType.HR: "latency",
        BizType.HC: "balance",
        BizType.MC: "balance",
    }[biz]
