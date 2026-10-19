"""能耗计算（设计方案 v4 §四）

E_total = Σ_i η_{v_i} · w_i  +  Σ_e β_e · D_at(e)
其中 D_at(e) 取决于该段链路在 compute chain 中所处的"层级 k"，
即被前面 k 个 stage 压缩过：D(k) = D_0 · ρ^(k/K)。
"""
from __future__ import annotations
from typing import List, Dict, Tuple, Iterable
from models import Node, Link, Task, Snapshot


def compute_energy_J(node: Node, cycles: int) -> float:
    """节点 v 处理 cycles 个 CPU 周期所耗能量。"""
    return node.eta * cycles


def link_tx_energy_J(link: Link, bits: float) -> float:
    """单条链路传输 bits 比特数据所耗能量。"""
    return link.beta * bits


def path_tx_energy_J(snapshot: Snapshot, path: List[str], bits: float) -> float:
    """路径上每条边都按相同 bits 累计（适用于无压缩、单层情形）。"""
    total = 0.0
    for u, v in zip(path[:-1], path[1:]):
        link = snapshot.get_link(u, v)
        if link is None:
            raise ValueError(f"path 上找不到边 ({u},{v})")
        total += link_tx_energy_J(link, bits)
    return total


# ============= v4：分层数据量 =============
def data_volume_at_layer(D0: float, rho: float, k: int, K: int) -> float:
    """完成 k 个 stage 后的数据量。D(k) = D_0 · ρ^(k/K)。"""
    if K <= 0:
        return D0
    if k <= 0:
        return D0
    if k >= K:
        return D0 * rho
    return D0 * (rho ** (k / K))


def path_tx_energy_layered_J(snapshot: Snapshot,
                             path_with_layers: List[Tuple[str, str, int]],
                             D0: float, rho: float, K: int) -> float:
    """对路径上每段 (u, v, k) 按 D(k) 累计。

    path_with_layers: [(u, v, k), ...]，k 是该段所在层级（即"该段之前已完成 k 个 stage"）。
    """
    total = 0.0
    for u, v, k in path_with_layers:
        link = snapshot.get_link(u, v)
        if link is None:
            raise ValueError(f"path 上找不到边 ({u},{v})")
        total += link.beta * data_volume_at_layer(D0, rho, k, K)
    return total


def total_task_energy_layered_J(snapshot: Snapshot,
                                compute_chain: List[Tuple[str, int]],
                                paths_with_layers: List[List[Tuple[str, str, int]]],
                                D0: float, rho: float, K: int) -> Tuple[float, float, Dict[str, float]]:
    """v4：返回 (E_compute, E_tx, E_compute_per_node)。

    compute_chain: [(node_id, cycles_i)]
    paths_with_layers: 每条路径段的 (u, v, layer_k) 列表
    """
    e_per_node: Dict[str, float] = {}
    e_comp = 0.0
    for node_id, w_i in compute_chain:
        node = snapshot.nodes.get(node_id)
        if node is None or w_i <= 0:
            continue
        e = node.eta * w_i
        e_per_node[node_id] = e_per_node.get(node_id, 0.0) + e
        e_comp += e
    e_tx = sum(path_tx_energy_layered_J(snapshot, p, D0, rho, K) for p in paths_with_layers)
    return e_comp, e_tx, e_per_node


# 兼容旧接口（HR flat 路径用）
def total_task_energy_J(snapshot: Snapshot,
                        compute_node: Node,
                        paths: List[List[str]],
                        cycles: int,
                        bits: float) -> Tuple[float, float]:
    """旧版：单 compute 节点 + 单层数据量。仅 HR fallback 用。"""
    e_comp = compute_energy_J(compute_node, cycles)
    e_tx = sum(path_tx_energy_J(snapshot, p, bits) for p in paths)
    return e_comp, e_tx
