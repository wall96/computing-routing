"""M2 网络快照：可见性判断 + 链路属性填充。

每 30 s 调一次 build_snapshot()。
"""
from __future__ import annotations
import math
from typing import Dict, List, Tuple
import numpy as np

from models import Node, Link, LinkType, NodeType, Snapshot
from topology import position_ecef, R_EARTH_M

LIGHT_SPEED = 299_792_458.0    # m/s


# ============= 可见性 =============
def los_clear(p1: np.ndarray, p2: np.ndarray, earth_radius: float = R_EARTH_M,
              margin: float = 50_000.0) -> bool:
    """两点连线是否被地球遮挡（含 50 km 大气余量）。"""
    d = p2 - p1
    # 投影 t* 使得 p1 + t*d 离原点最近
    a = np.dot(d, d)
    if a == 0:
        return True
    t = -np.dot(p1, d) / a
    if t <= 0 or t >= 1:
        # 最近点不在线段中间，则两端点都在地球外即视为不被遮挡
        return True
    closest = p1 + t * d
    return float(np.linalg.norm(closest)) >= (earth_radius - margin) + margin
    # 即闭合距离 >= R_e（带一点裕度）


def elevation_deg(ground_pos: np.ndarray, sat_pos: np.ndarray) -> float:
    """从地面站看卫星的仰角（度）。地面位置矢量正向 = 当地天顶。"""
    los = sat_pos - ground_pos
    los_norm = float(np.linalg.norm(los))
    if los_norm < 1.0:
        return 90.0
    zenith = ground_pos / float(np.linalg.norm(ground_pos))
    cos_zen = float(np.dot(los, zenith) / los_norm)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen_angle = math.acos(cos_zen)
    return 90.0 - math.degrees(zen_angle)


# ============= 链路构建 =============
def _delay_ms(p1: np.ndarray, p2: np.ndarray) -> float:
    return float(np.linalg.norm(p2 - p1)) / LIGHT_SPEED * 1000.0


def _build_isl_intra_plane(leos: List[Node], positions: Dict[str, np.ndarray],
                           link_cfg: dict, beta_map: dict) -> List[Link]:
    """同轨道平面内：每颗连前后各一颗。"""
    by_plane: Dict[int, List[Node]] = {}
    for n in leos:
        by_plane.setdefault(n.plane_idx, []).append(n)
    links: List[Link] = []
    bw = link_cfg["bw_mbps"]
    loss = link_cfg["loss_rate"]
    jitter = link_cfg["jitter_ms"]
    beta = beta_map["ISL_LEO_LEO"]
    for plane, sats in by_plane.items():
        sats.sort(key=lambda x: x.sat_in_plane_idx)
        S = len(sats)
        for i in range(S):
            j = (i + 1) % S
            u, v = sats[i].id, sats[j].id
            if not los_clear(positions[u], positions[v]):
                continue
            d_ms = _delay_ms(positions[u], positions[v])
            links.append(Link(u=u, v=v, type=LinkType.ISL_LEO_LEO,
                              bw_total_mbps=bw, bw_remain_mbps=bw,
                              delay_ms=d_ms, loss=loss, jitter_ms=jitter,
                              beta=beta))
    return links


def _build_isl_inter_plane(leos: List[Node], positions: Dict[str, np.ndarray],
                           link_cfg: dict, beta_map: dict) -> List[Link]:
    """跨平面：每颗对相邻两个平面的最近 LEO 各建一条。"""
    by_plane: Dict[int, List[Node]] = {}
    for n in leos:
        by_plane.setdefault(n.plane_idx, []).append(n)
    plane_ids = sorted(by_plane.keys())
    P = len(plane_ids)
    links: List[Link] = []
    bw = link_cfg["bw_mbps"]
    loss = link_cfg["loss_rate"]
    jitter = link_cfg["jitter_ms"]
    beta = beta_map["ISL_LEO_LEO"]
    seen = set()
    for pi in plane_ids:
        # 仅向"右"邻居建（避免重复）；环形
        pj = plane_ids[(plane_ids.index(pi) + 1) % P]
        sats_i = by_plane[pi]
        sats_j = by_plane[pj]
        pos_j = np.stack([positions[s.id] for s in sats_j])
        for s_i in sats_i:
            p_i = positions[s_i.id]
            d = np.linalg.norm(pos_j - p_i, axis=1)
            order = np.argsort(d)
            # 取最近的 1 颗（如果有 LOS）；可扩展到 2
            for k in order[:2]:
                s_j = sats_j[int(k)]
                if not los_clear(p_i, positions[s_j.id]):
                    continue
                key = tuple(sorted([s_i.id, s_j.id]))
                if key in seen:
                    continue
                seen.add(key)
                d_ms = _delay_ms(p_i, positions[s_j.id])
                links.append(Link(u=s_i.id, v=s_j.id, type=LinkType.ISL_LEO_LEO,
                                  bw_total_mbps=bw, bw_remain_mbps=bw,
                                  delay_ms=d_ms, loss=loss, jitter_ms=jitter,
                                  beta=beta))
                break
    return links


def _build_isl_leo_geo(leos: List[Node], geos: List[Node],
                       positions: Dict[str, np.ndarray],
                       link_cfg: dict, beta_map: dict) -> List[Link]:
    """LEO ↔ GEO 的 Ka 馈电链路：能 LOS 即建。"""
    if not geos:
        return []
    bw = link_cfg["bw_mbps"]
    loss = link_cfg["loss_rate"]
    jitter = link_cfg["jitter_ms"]
    beta = beta_map["ISL_LEO_GEO"]
    geo_pos = {g.id: positions[g.id] for g in geos}
    links: List[Link] = []
    for leo in leos:
        p_leo = positions[leo.id]
        for gid, p_geo in geo_pos.items():
            if not los_clear(p_leo, p_geo):
                continue
            d_ms = _delay_ms(p_leo, p_geo)
            links.append(Link(u=leo.id, v=gid, type=LinkType.ISL_LEO_GEO,
                              bw_total_mbps=bw, bw_remain_mbps=bw,
                              delay_ms=d_ms, loss=loss, jitter_ms=jitter,
                              beta=beta))
    return links


def _build_sgl(sats: List[Node], grounds: List[Node],
               positions: Dict[str, np.ndarray],
               link_cfg: dict, link_type: LinkType, beta: float) -> List[Link]:
    bw = link_cfg["bw_mbps"]
    loss = link_cfg["loss_rate"]
    jitter = link_cfg["jitter_ms"]
    min_elev = link_cfg["min_elevation_deg"]
    links: List[Link] = []
    for sat in sats:
        p_sat = positions[sat.id]
        for g in grounds:
            p_g = positions[g.id]
            elev = elevation_deg(p_g, p_sat)
            if elev < min_elev:
                continue
            d_ms = _delay_ms(p_sat, p_g)
            links.append(Link(u=sat.id, v=g.id, type=link_type,
                              bw_total_mbps=bw, bw_remain_mbps=bw,
                              delay_ms=d_ms, loss=loss, jitter_ms=jitter,
                              beta=beta))
    return links


def _build_ter(grounds: List[Node], positions: Dict[str, np.ndarray],
               link_cfg: dict, beta: float) -> List[Link]:
    """地面光纤：DC 全互联；EDGE 接最近 DC；ACCESS 接最近 EDGE/DC。"""
    bw = link_cfg["bw_mbps"]
    loss = link_cfg["loss_rate"]
    jitter = link_cfg["jitter_ms"]
    dcs = [g for g in grounds if g.type == NodeType.DC]
    edges = [g for g in grounds if g.type == NodeType.EDGE]
    access = [g for g in grounds if g.type == NodeType.ACCESS]
    links: List[Link] = []

    def add(u: str, v: str, p1, p2):
        d_ms = _delay_ms(p1, p2) * 1.5  # 光纤迂回放大系数
        links.append(Link(u=u, v=v, type=LinkType.TER,
                          bw_total_mbps=bw, bw_remain_mbps=bw,
                          delay_ms=d_ms, loss=loss, jitter_ms=jitter,
                          beta=beta))

    # DC 全互联
    for i, a in enumerate(dcs):
        for b in dcs[i+1:]:
            add(a.id, b.id, positions[a.id], positions[b.id])
    # EDGE -> 最近 DC（最多接 2 个，做冗余）
    for e in edges:
        if not dcs:
            continue
        ds = sorted(dcs, key=lambda d: float(np.linalg.norm(positions[e.id] - positions[d.id])))
        for d in ds[:2]:
            add(e.id, d.id, positions[e.id], positions[d.id])
    # ACCESS -> 最近 EDGE 或 DC（最多接 2 个）
    targets = edges + dcs
    for ac in access:
        if not targets:
            continue
        ds = sorted(targets, key=lambda x: float(np.linalg.norm(positions[ac.id] - positions[x.id])))
        for d in ds[:2]:
            add(ac.id, d.id, positions[ac.id], positions[d.id])
    return links


# ============= 入口 =============
def build_snapshot(t_sec: float, all_nodes: List[Node],
                   cfg_link: dict, beta_map: dict, scenario: str) -> Snapshot:
    """按场景剪裁拓扑并构建当前时刻的链路集合。

    scenario:
      A: LEO + 地面（DC/EDGE/ACCESS）；GEO 整体剔除
      B: LEO + GEO + ACCESS；地面 DC/EDGE 不参与路由
    """
    # 节点剪裁
    if scenario == "A":
        nodes = [n for n in all_nodes if n.type != NodeType.GEO]
    elif scenario == "B":
        nodes = [n for n in all_nodes if n.type not in (NodeType.DC, NodeType.EDGE)]
    else:
        raise ValueError(f"未知 scenario={scenario}")

    # 算位置
    positions: Dict[str, np.ndarray] = {n.id: position_ecef(n, t_sec) for n in nodes}
    leos = [n for n in nodes if n.type == NodeType.LEO]
    geos = [n for n in nodes if n.type == NodeType.GEO]
    grounds = [n for n in nodes if n.type in (NodeType.DC, NodeType.EDGE, NodeType.ACCESS)]

    links: List[Link] = []
    # ISL
    links += _build_isl_intra_plane(leos, positions, cfg_link["isl_leo_leo"], beta_map)
    links += _build_isl_inter_plane(leos, positions, cfg_link["isl_leo_leo"], beta_map)
    if scenario == "B":
        links += _build_isl_leo_geo(leos, geos, positions, cfg_link["isl_leo_geo"], beta_map)
    # SGL
    if scenario == "A":
        links += _build_sgl(leos, grounds, positions,
                            cfg_link["sgl_leo"], LinkType.SGL_LEO,
                            beta_map["SGL_LEO"])
        links += _build_ter(grounds, positions, cfg_link["ter"], beta_map["TER"])
    elif scenario == "B":
        links += _build_sgl(leos, grounds, positions,
                            cfg_link["sgl_leo"], LinkType.SGL_LEO,
                            beta_map["SGL_LEO"])
        links += _build_sgl(geos, grounds, positions,
                            cfg_link["sgl_geo"], LinkType.SGL_GEO,
                            beta_map["SGL_GEO"])

    # 装入 Snapshot
    nodes_dict = {n.id: n for n in nodes}
    links_dict: Dict[Tuple[str, str], Link] = {}
    for lk in links:
        key = (lk.u, lk.v) if lk.u < lk.v else (lk.v, lk.u)
        if key in links_dict:
            continue   # 同节点对仅保留一条（前面的）
        links_dict[key] = lk
    return Snapshot(t=t_sec, nodes=nodes_dict, links=links_dict)
