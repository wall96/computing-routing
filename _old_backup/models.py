"""数据结构：Node / Link / Task / Allocation。

字段命名与 设计方案 v3 §九 一致；类型字符串与 config.yaml 中的 key 对齐。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict, Set


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
    capacity_total: float        # TFLOPS（应用层）
    capacity_remain: float       # TFLOPS
    cpu_freq: float              # f_v (Hz)
    cap_coef: float              # κ_v (J/(cycle·Hz²))
    eta: float                   # η_v = κ·f²  (J/cycle)
    # 几何（轨道节点会按时间动态算 ECI/ECEF；地面节点直接给 ECEF）
    lat: Optional[float] = None
    lon: Optional[float] = None
    # 轨道参数（仅卫星）
    orbit_a_km: Optional[float] = None       # 半长轴
    orbit_inc_rad: Optional[float] = None    # 倾角
    orbit_raan_rad: Optional[float] = None   # 升交点赤经
    orbit_M0_rad: Optional[float] = None     # t=0 时的平近点角
    orbit_n_rad_s: Optional[float] = None    # 平均运动 (rad/s)
    plane_idx: Optional[int] = None          # 同轨道平面 idx
    sat_in_plane_idx: Optional[int] = None   # 平面内 idx

    def reset_capacity(self):
        self.capacity_remain = self.capacity_total


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
    beta: float                  # J/bit，能耗系数
    alive: bool = True

    def reset_bw(self):
        self.bw_remain_mbps = self.bw_total_mbps


@dataclass
class Task:
    id: str
    src: str                     # ACCESS 节点 id
    dst_set: List[str]           # 目的 ACCESS 节点 id（多连接时 K 个；其他业务长度 1，==src 表示结果回到自己）
    biz: BizType
    priority: int
    mode: str                    # 'burst' | 'persist'
    rate_mbps: float             # 数据速率
    delay_budget_ms: float       # 时延要求
    loss_budget: float           # 丢包要求
    cycles_per_bit: int          # 单 bit 所需 CPU 周期
    arrival_t: float             # 到达时刻 (s)
    duration_s: float            # 持续时长
    # 衍生
    cycles_total: int = 0        # c_τ：总 CPU 周期
    data_volume_bits: float = 0  # D_0：原始总数据量（上送 + 回传）
    compute_rate_tflops: float = 0  # 持续算力需求
    # v4：在网压缩 (§4.3)
    compression_rho: float = 1.0    # 端到端压缩比 ρ ∈ (0,1]；HR=1.0 强制不压缩

    @property
    def t_end(self) -> float:
        return self.arrival_t + self.duration_s


@dataclass
class Allocation:
    task: Task
    compute_chain: List[Tuple[str, int]]   # [(node_id, cycles_i)]，Σcycles_i = task.cycles_total
    paths: List[List[str]]       # 节点序列（多连接时多条路径；起点 src，终点 dst）
    energy_compute_J: float
    energy_tx_J: float
    bw_per_link: Dict[Tuple[str, str], float]  # 每条边占用带宽（各层之和）
    # v4 新增
    delivered_data_bits: float = 0.0     # = D_0 × ρ
    energy_compute_per_node_J: Dict[str, float] = field(default_factory=dict)
    # 调度结果元数据
    accepted: bool = True
    strategy: str = ""
    reject_reason: Optional[str] = None
    t_alloc: float = 0.0

    @property
    def energy_total_J(self) -> float:
        return self.energy_compute_J + self.energy_tx_J

    @property
    def compute_node(self) -> str:
        """兼容旧接口：取 chain 末端节点（fan-out 起点）。"""
        return self.compute_chain[-1][0] if self.compute_chain else ""


@dataclass
class Snapshot:
    """30 s 时间片内的网络视图。"""
    t: float                     # 切片起点
    nodes: Dict[str, Node]
    links: Dict[Tuple[str, str], Link]   # 无向但内部用有序键

    def link_key(self, u: str, v: str) -> Tuple[str, str]:
        return (u, v) if u < v else (v, u)

    def get_link(self, u: str, v: str) -> Optional[Link]:
        return self.links.get(self.link_key(u, v))
