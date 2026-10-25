"""能耗 / 计算时延 / 计算功率(《项目总体设计文档.md》§6, §7)。

- 计算能耗:  E_comp = η_v · c_τ
- 计算功率:  P_comp = η_v · compute_rate      (J/cycle × cycle/s = W)   —— 能量硬约束用
- 计算时延:  t_compute = 固定开销 + x · D · ω / C_v   (包模型,x 为子包比例)
             t_compute = 固定开销 + L_ref · ω / C_v   (流式模型,packet_bits=0 时)
- 传输能耗:  E_tx = Σ β_e · D                  (前段 D0 / 后段 D0ρ)
"""
from __future__ import annotations
from typing import List
from models import Node, Task, Snapshot


def compute_energy_J(node: Node, cycles: float) -> float:
    return node.eta * cycles


def compute_power_W(node: Node, task: Task) -> float:
    """任务在 node 计算的瞬时功率(W)。"""
    return node.eta * task.compute_rate


def compute_ms_per_share(node: Node, task: Task) -> float:
    """计算时延中与数据量成正比的部分(share=1 时的值,ms)。

    包模型(packet_bits>0):整包 D·ω / C_v;流式模型:单块 Lref·ω / C_v。均乘负载放大。
    """
    if node.capacity_total <= 0:
        return float("inf")
    bits = task.packet_bits if task.packet_bits > 0 else task.Lref_bits
    utilization = max(0.0, min(0.95, 1.0 - node.capacity_remain / node.capacity_total))
    queue_factor = 1.0 / max(0.05, 1.0 - utilization)
    return bits * task.effective_cycles_per_bit / node.capacity_total * 1000.0 * queue_factor


def compute_latency_ms(node: Node, task: Task, share: float = 1.0) -> float:
    """计算时延 = 固定开销 + 比例部分 × share。

    share 为子包占整包比例;拆分后各节点并行计算,固定开销各自独立、不累加。
    """
    if node.capacity_total <= 0:
        return float("inf")
    return task.fixed_processing_total_ms + compute_ms_per_share(node, task) * share


def path_tx_energy_J(snapshot: Snapshot, path: List[str], bits: float) -> float:
    total = 0.0
    for u, v in zip(path[:-1], path[1:]):
        link = snapshot.get_link(u, v)
        if link is None:
            raise ValueError(f"path 上找不到边 ({u},{v})")
        total += link.beta * bits
    return total
