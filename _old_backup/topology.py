"""M1 拓扑生成：Walker Star LEO 星座 + GEO + 地面节点 + 圆轨道传播。

只做几何，不计算可见性（可见性在 network.py）。
"""
from __future__ import annotations
import math
from typing import Dict, List, Tuple
import numpy as np

from models import Node, NodeType

# 物理常量
MU_EARTH = 3.986004418e14         # m^3/s^2
R_EARTH_M = 6_378_137.0           # m（赤道半径）
EARTH_OMEGA = 7.2921159e-5        # rad/s（自转角速度）


def _deg2rad(x: float) -> float:
    return x * math.pi / 180.0


def _geodetic_to_ecef_m(lat_deg: float, lon_deg: float, h_m: float = 0.0) -> np.ndarray:
    """简化为球形地球，足够算路由。"""
    lat = _deg2rad(lat_deg)
    lon = _deg2rad(lon_deg)
    r = R_EARTH_M + h_m
    return np.array([r * math.cos(lat) * math.cos(lon),
                     r * math.cos(lat) * math.sin(lon),
                     r * math.sin(lat)])


def build_walker_star_leo(total: int, planes: int, phase_F: int,
                          inc_deg: float, alt_km: float,
                          capacity_tflops: float, eta_J_per_cycle: float,
                          cpu_freq: float, cap_coef: float) -> List[Node]:
    """Walker Star（RAAN 跨 180°）+ phase factor F。

    返回卫星列表，每颗卫星携带轨道根数，可用 propagate_satellite() 求当前 ECI 位置。
    """
    assert total % planes == 0, "total 必须能被 planes 整除"
    sats_per_plane = total // planes
    a = R_EARTH_M + alt_km * 1000.0           # 半长轴 (m)
    n = math.sqrt(MU_EARTH / (a ** 3))        # 平均运动 (rad/s)
    inc = _deg2rad(inc_deg)

    nodes: List[Node] = []
    for p in range(planes):
        # Walker Star: RAAN 在 [0, 180°)
        raan = math.pi * p / planes
        for s in range(sats_per_plane):
            # 平面内均布；不同平面之间用 phase 抖动
            M0 = 2 * math.pi * s / sats_per_plane \
                + 2 * math.pi * phase_F * p / total
            M0 = M0 % (2 * math.pi)
            sid = f"LEO-{p:02d}-{s:02d}"
            nodes.append(Node(
                id=sid,
                type=NodeType.LEO,
                capacity_total=capacity_tflops,
                capacity_remain=capacity_tflops,
                cpu_freq=cpu_freq,
                cap_coef=cap_coef,
                eta=eta_J_per_cycle,
                orbit_a_km=a / 1000.0,
                orbit_inc_rad=inc,
                orbit_raan_rad=raan,
                orbit_M0_rad=M0,
                orbit_n_rad_s=n,
                plane_idx=p,
                sat_in_plane_idx=s,
            ))
    return nodes


def build_geo(longitudes_deg: List[float], capacity_tflops: float,
              eta_J_per_cycle: float, cpu_freq: float, cap_coef: float) -> List[Node]:
    """GEO 卫星：高度 35786 km，赤道，固定经度。"""
    GEO_ALT_M = 35_786_000.0
    a = R_EARTH_M + GEO_ALT_M
    n = math.sqrt(MU_EARTH / (a ** 3))        # 接近地球自转
    nodes: List[Node] = []
    for i, lon in enumerate(longitudes_deg):
        # GEO 在 ECEF 中固定，但我们仍按轨道参数表达（在 t=0 时位于该经度）
        raan = _deg2rad(lon)                   # 赤道圈，RAAN 直接对应经度
        M0 = 0.0
        sid = f"GEO-{i:02d}"
        nodes.append(Node(
            id=sid,
            type=NodeType.GEO,
            capacity_total=capacity_tflops,
            capacity_remain=capacity_tflops,
            cpu_freq=cpu_freq,
            cap_coef=cap_coef,
            eta=eta_J_per_cycle,
            orbit_a_km=a / 1000.0,
            orbit_inc_rad=0.0,
            orbit_raan_rad=raan,
            orbit_M0_rad=M0,
            orbit_n_rad_s=n,
            plane_idx=-1,
            sat_in_plane_idx=i,
        ))
    return nodes


def build_ground(stations: List[dict],
                 capacity_map: Dict[str, float],
                 eta_map: Dict[str, float],
                 cpu_freq_map: Dict[str, float],
                 cap_coef_map: Dict[str, float]) -> List[Node]:
    """地面节点：DC / EDGE / ACCESS。"""
    out = []
    for st in stations:
        t = st["type"]
        out.append(Node(
            id=st["name"],
            type=NodeType(t),
            capacity_total=capacity_map.get(t, 0.0),
            capacity_remain=capacity_map.get(t, 0.0),
            cpu_freq=cpu_freq_map.get(t, 0.0),
            cap_coef=cap_coef_map.get(t, 0.0),
            eta=eta_map.get(t, 0.0),
            lat=st["lat"],
            lon=st["lon"],
        ))
    return out


# ======================== 轨道传播 ========================
def propagate_satellite(node: Node, t_sec: float) -> np.ndarray:
    """圆轨道近似：返回卫星在地心惯性系（ECI）中的位置 (m)。"""
    a = node.orbit_a_km * 1000.0
    M = (node.orbit_M0_rad + node.orbit_n_rad_s * t_sec) % (2 * math.pi)
    # 轨道平面内坐标
    x_orb = a * math.cos(M)
    y_orb = a * math.sin(M)
    # 倾角旋转（绕 x 轴）
    inc = node.orbit_inc_rad
    x1 = x_orb
    y1 = y_orb * math.cos(inc)
    z1 = y_orb * math.sin(inc)
    # RAAN 旋转（绕 z 轴）
    raan = node.orbit_raan_rad
    x_eci = x1 * math.cos(raan) - y1 * math.sin(raan)
    y_eci = x1 * math.sin(raan) + y1 * math.cos(raan)
    z_eci = z1
    return np.array([x_eci, y_eci, z_eci])


def eci_to_ecef(eci: np.ndarray, t_sec: float) -> np.ndarray:
    """ECI -> ECEF（仅地球自转角，未含岁差章动）。"""
    theta = EARTH_OMEGA * t_sec
    c, s = math.cos(theta), math.sin(theta)
    return np.array([c * eci[0] + s * eci[1],
                     -s * eci[0] + c * eci[1],
                     eci[2]])


def position_ecef(node: Node, t_sec: float) -> np.ndarray:
    """通用接口：节点在 t 时刻的 ECEF 位置 (m)。"""
    if node.type in (NodeType.LEO, NodeType.GEO):
        eci = propagate_satellite(node, t_sec)
        return eci_to_ecef(eci, t_sec)
    else:
        # 地面节点假设无海拔
        return _geodetic_to_ecef_m(node.lat, node.lon, h_m=0.0)
