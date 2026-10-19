"""能耗 / 计算时延 / 计算功率(《项目总体设计文档.md》§6, §7)。

- 计算能耗:  E_comp = η_v · c_τ
- 计算功率:  P_comp = η_v · compute_rate      (J/cycle × cycle/s = W)   —— 能量硬约束用
- 计算时延:  t_compute = L_ref · ω / C_v      (流式单块/单帧,用聚合吞吐 C_v)
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


def compute_latency_ms(node: Node, task: Task) -> float:
    """流式单块/帧时延：固定开销 + 纯计算时延 × 负载放大。"""
    if node.capacity_total <= 0:
        return float("inf")
    block_cycles = task.Lref_bits * task.effective_cycles_per_bit
    utilization = max(0.0, min(0.95, 1.0 - node.capacity_remain / node.capacity_total))
    queue_factor = 1.0 / max(0.05, 1.0 - utilization)
    return task.fixed_processing_total_ms + block_cycles / node.capacity_total * 1000.0 * queue_factor


def path_tx_energy_J(snapshot: Snapshot, path: List[str], bits: float) -> float:
    total = 0.0
    for u, v in zip(path[:-1], path[1:]):
        link = snapshot.get_link(u, v)
        if link is None:
            raise ValueError(f"path 上找不到边 ({u},{v})")
        total += link.beta * bits
    return total
