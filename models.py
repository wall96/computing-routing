"""数据结构:Node / Link / Task / Allocation / Snapshot。

对应《项目总体设计文档.md》§8 问题形式化 与 §12 参数。
单位约定:
  - 节点算力 capacity_*  以等效 op/s 存储(= 等效 TFLOPS × 1e12)
  - 任务 compute_rate    以等效 op/s 存储（字段名保留 cycle 仅为兼容旧CSV）
  - 能耗 eta             J/op;链路 beta J/bit
  - 功率 power_*         W
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict


class NodeType(str, Enum):
    LEO = "LEO"
    GEO = "GEO"
    EDGE = "EDGE"
    DC = "DC"
    ACCESS = "ACCESS"


class LinkType(str, Enum):
    ISL_LEO_LEO = "ISL_LEO_LEO"
    ISL_LEO_GEO = "ISL_LEO_GEO"
    SGL_LEO = "SGL_LEO"
    SGL_GEO = "SGL_GEO"
    TER = "TER"


class BizType(str, Enum):
    LL = "LL"   # 低时延
    HR = "HR"   # 高可靠
    HC = "HC"   # 大容量
    MC = "MC"   # 多连接


@dataclass
class Node:
    id: str
    type: NodeType
    capacity_total: float        # cycle/s
    capacity_remain: float       # equivalent op/s
    eta: float                   # η_v (J/op)
    power_budget_W: float        # P_v^max
    power_remain_W: float        # 剩余功率预算
    cpu_freq: float = 0.0        # f_v (Hz)，仅记录
    cap_coef: float = 0.0        # κ_v，仅记录
    # 几何
    lat: Optional[float] = None
    lon: Optional[float] = None
    # 轨道参数(卫星)
    orbit_a_km: Optional[float] = None
    orbit_inc_rad: Optional[float] = None
    orbit_raan_rad: Optional[float] = None
    orbit_M0_rad: Optional[float] = None
    orbit_n_rad_s: Optional[float] = None
    shell: Optional[str] = None
    plane_idx: Optional[int] = None
    sat_in_plane_idx: Optional[int] = None

    def reset_resources(self):
        self.capacity_remain = self.capacity_total
        self.power_remain_W = self.power_budget_W


@dataclass
class Link:
    u: str
    v: str
    type: LinkType
    bw_total_mbps: float
    bw_remain_mbps: float
    delay_ms: float
    loss: float
    jitter_ms: float
    beta: float                  # J/bit(时变时按当片速率计算)
    alive: bool = True

    def reset_bw(self):
        self.bw_remain_mbps = self.bw_total_mbps


@dataclass
class Task:
    id: str
    src: str
    dst_set: List[str]           # 目的 ACCESS(MC 多个;其它 == [src] 回源)
    biz: BizType
    priority: int
    mode: str                    # 'burst' | 'persist'
    rate_mbps: float             # r0 原始输入速率
    delay_budget_ms: float
    loss_budget: float
    cycles_per_bit: float        # 基础 ω (等效 operation/bit，旧字段名保持兼容)
    Lref_bits: float             # 处理块/帧大小(bit)
    arrival_t: float
    duration_s: float
    compression_rho: float = 1.0
    processing_logic: str = ""
    cycles_per_bit_per_dst: float = 0.0
    fixed_processing_ms: float = 0.0
    fixed_processing_ms_per_dst: float = 0.0
    # 衍生
    cycles_total: float = 0.0    # c_τ = D0·ω
    data_volume_bits: float = 0.0  # D0
    compute_rate: float = 0.0    # equivalent op/s = r0_bps·ω

    @property
    def rate_bps(self) -> float:
        return self.rate_mbps * 1e6

    @property
    def effective_cycles_per_bit(self) -> float:
        """实际每bit计算量；MC等业务可为每个目的地增加处理。"""
        return self.cycles_per_bit + len(self.dst_set) * self.cycles_per_bit_per_dst

    @property
    def fixed_processing_total_ms(self) -> float:
        return self.fixed_processing_ms + len(self.dst_set) * self.fixed_processing_ms_per_dst

    @property
    def t_end(self) -> float:
        return self.arrival_t + self.duration_s


@dataclass
class Allocation:
    task: Task
    compute_node: str                     # v*
    path_pre: List[str]                   # src → v*  (载 D0)
    path_post: List[List[str]]            # v* → 各 dst (载 D0ρ)
    bw_per_link: Dict[Tuple[str, str], float]
    compute_node_type: str = ""            # LEO/GEO/EDGE/DC
    compute_latency_ms: float = 0.0       # t_compute(v*)
    delivered_data_bits: float = 0.0      # D0·ρ
    energy_compute_J: float = 0.0
    energy_tx_J: float = 0.0
    compute_rate_used: float = 0.0        # cycle/s 占用
    power_used_W: float = 0.0             # W 占用
    # 元数据
    accepted: bool = True
    strategy: str = ""
    solver: str = ""
    reject_reason: Optional[str] = None
    t_alloc: float = 0.0
    max_delay_ms: float = 0.0             # 实际端到端时延(含 t_compute)
    max_loss: float = 0.0                 # 实际累乘丢包
    allocation_id: str = ""                # 本次分配唯一 ID（迁移后生成新 ID）
    _preempted: bool = False

    @property
    def paths(self) -> List[List[str]]:
        return [self.path_pre] + self.path_post

    @property
    def energy_total_J(self) -> float:
        return self.energy_compute_J + self.energy_tx_J


@dataclass
class Snapshot:
    """30 s 时间片内的网络视图。"""
    t: float
    nodes: Dict[str, Node]
    links: Dict[Tuple[str, str], Link]
    compute_eligible: set = field(default_factory=set)   # 本场景可作算力节点的 type 集合(字符串)
    _adj: Optional[Dict[str, List[str]]] = None

    def link_key(self, u: str, v: str) -> Tuple[str, str]:
        return (u, v) if u < v else (v, u)

    def get_link(self, u: str, v: str) -> Optional[Link]:
        return self.links.get(self.link_key(u, v))

    def adj(self) -> Dict[str, List[str]]:
        if self._adj is None:
            a: Dict[str, List[str]] = {nid: [] for nid in self.nodes}
            for (u, v) in self.links:
                a.setdefault(u, []).append(v)
                a.setdefault(v, []).append(u)
            self._adj = a
        return self._adj
