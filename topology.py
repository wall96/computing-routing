"""M1 拓扑生成:多壳层 Walker LEO + GEO + 50 地面城市 + 圆轨道传播。

只做几何,不算可见性(可见性在 network.py)。算力单位:等效op/s(=TFLOPS×1e12)。
"""
from __future__ import annotations
import math
import random
from typing import Dict, List
import numpy as np

from models import Node, NodeType

MU_EARTH = 3.986004418e14
R_EARTH_M = 6_378_137.0
EARTH_OMEGA = 7.2921159e-5
TFLOPS_TO_CPS = 1e12          # 旧常量名保留兼容；1等效TFLOPS = 1e12 op/s

# 记录用的默认 f/κ(仅用于可解释性;η 由配置直接给)
FREQ_MAP = {"LEO": 1e9, "GEO": 1.5e9, "EDGE": 2e9, "DC": 3e9, "ACCESS": 0.0}
KAPPA_MAP = {"LEO": 1e-27, "GEO": 1e-27, "EDGE": 5e-28, "DC": 1e-28, "ACCESS": 0.0}


def _deg2rad(x: float) -> float:
    return x * math.pi / 180.0


def _geodetic_to_ecef_m(lat_deg: float, lon_deg: float, h_m: float = 0.0) -> np.ndarray:
    lat = _deg2rad(lat_deg); lon = _deg2rad(lon_deg)
    r = R_EARTH_M + h_m
    return np.array([r * math.cos(lat) * math.cos(lon),
                     r * math.cos(lat) * math.sin(lon),
                     r * math.sin(lat)])


def _make_sat(sid, shell, plane, s_idx, a_m, inc, raan, M0, n,
              cap_cps, eta, ntype=NodeType.LEO,
              power_budget=0.0) -> Node:
    return Node(
        id=sid, type=ntype,
        capacity_total=cap_cps, capacity_remain=cap_cps,
        eta=eta, power_budget_W=power_budget, power_remain_W=power_budget,
        cpu_freq=FREQ_MAP[ntype.value], cap_coef=KAPPA_MAP[ntype.value],
        orbit_a_km=a_m / 1000.0, orbit_inc_rad=inc, orbit_raan_rad=raan,
        orbit_M0_rad=M0, orbit_n_rad_s=n,
        shell=shell, plane_idx=plane, sat_in_plane_idx=s_idx,
    )


def sample_capacity(cap_tflops, eta: float, power_budget: float, rng) -> tuple:
    """单颗星算力(op/s)与功率上限(W)。

    cap_tflops 为数值:所有星相同,功率取配置值;
    为 [lo, hi]:每颗星独立均匀抽取,功率上限按 Pmax = η × Cmax 随算力同比例给出。
    """
    if isinstance(cap_tflops, (list, tuple)):
        lo, hi = cap_tflops
        cap = rng.uniform(float(lo), float(hi)) * TFLOPS_TO_CPS
        return cap, eta * cap
    return float(cap_tflops) * TFLOPS_TO_CPS, power_budget


def build_shell(shell_cfg: dict, cap_tflops, eta: float, power_budget: float,
                rng=None) -> List[Node]:
    """一个壳层的 Walker 星座 i:T/P/F(全量,不缩比)。

    pattern=delta(默认):RAAN 在 [0,360) 均布,Ω_p = 2πp/P,适合倾斜轨道;
    pattern=star:RAAN 在 [0,180) 均布,Ω_p = πp/P,适合近极轨道。
    相位:M₀(p,s) = 2πs/S + 2πF·p/T。
    """
    name = shell_cfg["name"]
    planes = int(shell_cfg["planes"])
    per_plane = int(shell_cfg["per_plane"])
    phase_F = int(shell_cfg.get("phase", 1))
    pattern = str(shell_cfg.get("pattern", "delta")).lower()
    if pattern not in ("delta", "star"):
        raise ValueError(f"壳层 {name}: 未知 Walker pattern {pattern}")
    raan_span = 2 * math.pi if pattern == "delta" else math.pi
    inc = _deg2rad(shell_cfg["inc_deg"])
    a = R_EARTH_M + shell_cfg["alt_km"] * 1000.0
    n = math.sqrt(MU_EARTH / (a ** 3))
    if rng is None:
        rng = random.Random(0)

    nodes: List[Node] = []
    total = planes * per_plane
    for p in range(planes):
        raan = raan_span * p / planes
        for s in range(per_plane):
            M0 = (2 * math.pi * s / per_plane + 2 * math.pi * phase_F * p / total) % (2 * math.pi)
            sid = f"{name}-{p:02d}-{s:02d}"
            cap_cps, pb = sample_capacity(cap_tflops, eta, power_budget, rng)
            nodes.append(_make_sat(sid, name, p, s, a, inc, raan, M0, n,
                                   cap_cps, eta, NodeType.LEO, pb))
    return nodes


def build_geo(longitudes_deg: List[float], cap_tflops, eta: float,
              power_budget: float, rng=None) -> List[Node]:
    GEO_ALT_M = 35_786_000.0
    a = R_EARTH_M + GEO_ALT_M
    n = math.sqrt(MU_EARTH / (a ** 3))
    if rng is None:
        rng = random.Random(0)
    out = []
    for i, lon in enumerate(longitudes_deg):
        raan = _deg2rad(lon); M0 = 0.0
        cap_cps, pb = sample_capacity(cap_tflops, eta, power_budget, rng)
        out.append(_make_sat(f"GEO-{i:02d}", "GEO", -1, i, a, 0.0, raan, M0, n,
                             cap_cps, eta, NodeType.GEO, pb))
    return out


def build_ground(stations: List[dict], cap_tflops: Dict[str, float],
                 eta_map: Dict[str, float], power_map: Dict[str, float]) -> List[Node]:
    out = []
    for st in stations:
        t = st["type"]
        cap = cap_tflops.get(t, 0.0) * TFLOPS_TO_CPS
        pb = power_map.get(t, 0.0)
        out.append(Node(
            id=st["name"], type=NodeType(t),
            capacity_total=cap, capacity_remain=cap,
            eta=eta_map.get(t, 0.0), power_budget_W=pb, power_remain_W=pb,
            cpu_freq=FREQ_MAP.get(t, 0.0), cap_coef=KAPPA_MAP.get(t, 0.0),
            lat=st["lat"], lon=st["lon"],
        ))
    return out


# ======================== 轨道传播 ========================
def propagate_satellite(node: Node, t_sec: float) -> np.ndarray:
    a = node.orbit_a_km * 1000.0
    M = (node.orbit_M0_rad + node.orbit_n_rad_s * t_sec) % (2 * math.pi)
    x_orb = a * math.cos(M); y_orb = a * math.sin(M)
    inc = node.orbit_inc_rad
    x1 = x_orb; y1 = y_orb * math.cos(inc); z1 = y_orb * math.sin(inc)
    raan = node.orbit_raan_rad
    return np.array([x1 * math.cos(raan) - y1 * math.sin(raan),
                     x1 * math.sin(raan) + y1 * math.cos(raan),
                     z1])


def eci_to_ecef(eci: np.ndarray, t_sec: float) -> np.ndarray:
    theta = EARTH_OMEGA * t_sec
    c, s = math.cos(theta), math.sin(theta)
    return np.array([c * eci[0] + s * eci[1], -s * eci[0] + c * eci[1], eci[2]])


def position_ecef(node: Node, t_sec: float) -> np.ndarray:
    if node.type in (NodeType.LEO, NodeType.GEO):
        return eci_to_ecef(propagate_satellite(node, t_sec), t_sec)
    return _geodetic_to_ecef_m(node.lat, node.lon, 0.0)
