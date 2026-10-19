"""M2 网络快照:场景剪裁 + 可见性 + 时变信道 + 30s 快照(《设计文档》§2, §5)。

场景:
  1 单层LEO:      LEO(L1) + 地面ACCESS;链路 SGL_LEO + ISL_LEO_LEO
  2 双层LEO+GEO:  LEO(L1,L2) + GEO + ACCESS;+ SGL_GEO + ISL_LEO_GEO
  3 多层LEO+地面: LEO(L1,L2,L3) + 地面(EDGE+ACCESS,无DC);+ TER
"""
from __future__ import annotations
import math
from typing import Dict, List, Tuple
import numpy as np

from models import Node, Link, LinkType, NodeType, Snapshot
from topology import position_ecef, R_EARTH_M, _geodetic_to_ecef_m

LIGHT_SPEED = 299_792_458.0
ISL_REF_DIST_M = 5_000_000.0     # ISL 时变参考距离


# ============= 可见性 =============
def los_clear(p1: np.ndarray, p2: np.ndarray, margin: float = 50_000.0) -> bool:
    d = p2 - p1
    a = float(np.dot(d, d))
    if a == 0:
        return True
    t = -float(np.dot(p1, d)) / a
    if t <= 0 or t >= 1:
        return True
    closest = p1 + t * d
    return float(np.linalg.norm(closest)) >= (R_EARTH_M - margin)


def elevation_deg(ground_pos: np.ndarray, sat_pos: np.ndarray) -> float:
    los = sat_pos - ground_pos
    ln = float(np.linalg.norm(los))
    if ln < 1.0:
        return 90.0
    zenith = ground_pos / float(np.linalg.norm(ground_pos))
    cos_zen = max(-1.0, min(1.0, float(np.dot(los, zenith) / ln)))
    return 90.0 - math.degrees(math.acos(cos_zen))


def _delay_ms(p1, p2) -> float:
    return float(np.linalg.norm(p2 - p1)) / LIGHT_SPEED * 1000.0


# ============= 时变因子 =============
def _sgl_factor(elev: float, min_elev: float, floor: float, tv: bool) -> float:
    if not tv:
        return 1.0
    g = (elev - min_elev) / max(1e-6, (90.0 - min_elev))
    return max(floor, min(1.0, g))


def _isl_factor(dist_m: float, floor: float, tv: bool) -> float:
    if not tv:
        return 1.0
    g = 1.0 - dist_m / ISL_REF_DIST_M
    return max(floor, min(1.0, g))


def _mk_link(u, v, ltype, bw_nom, loss, jitter, beta_nom, delay_ms, g) -> Link:
    """按时变因子 g 调整有效带宽与 β(R=R_nom·g → β=β_nom/g)。"""
    g = max(1e-3, g)
    bw_eff = bw_nom * g
    beta_eff = beta_nom / g
    return Link(u=u, v=v, type=ltype, bw_total_mbps=bw_eff, bw_remain_mbps=bw_eff,
                delay_ms=delay_ms, loss=loss, jitter_ms=jitter, beta=beta_eff)


# ============= ISL =============
def _isl_intra_plane(leos: List[Node], pos, cfg, beta, isl_floor, tv) -> List[Link]:
    by_plane: Dict[Tuple[str, int], List[Node]] = {}
    for n in leos:
        by_plane.setdefault((n.shell, n.plane_idx), []).append(n)
    links = []
    for _, sats in by_plane.items():
        sats.sort(key=lambda x: x.sat_in_plane_idx)
        S = len(sats)
        if S < 2:
            continue
        for i in range(S):
            j = (i + 1) % S
            u, v = sats[i].id, sats[j].id
            if not los_clear(pos[u], pos[v]):
                continue
            d = float(np.linalg.norm(pos[v] - pos[u]))
            links.append(_mk_link(u, v, LinkType.ISL_LEO_LEO, cfg["bw_mbps"],
                                  cfg["loss_rate"], cfg["jitter_ms"], beta,
                                  _delay_ms(pos[u], pos[v]), _isl_factor(d, isl_floor, tv)))
    return links


def _isl_inter_plane(leos: List[Node], pos, cfg, beta, isl_floor, tv,
                     cross_shell: bool) -> List[Link]:
    """跨平面(同壳层邻面)+ 可选跨壳层最近匹配。"""
    by_shell: Dict[str, List[Node]] = {}
    for n in leos:
        by_shell.setdefault(n.shell, []).append(n)
    links = []
    seen = set()

    def connect(group_a: List[Node], group_b: List[Node], kmax=1):
        if not group_a or not group_b:
            return
        posb = np.stack([pos[s.id] for s in group_b])
        for sa in group_a:
            d = np.linalg.norm(posb - pos[sa.id], axis=1)
            for k in np.argsort(d)[:kmax + 1]:
                sb = group_b[int(k)]
                if sb.id == sa.id:
                    continue
                if not los_clear(pos[sa.id], pos[sb.id]):
                    continue
                key = tuple(sorted([sa.id, sb.id]))
                if key in seen:
                    continue
                seen.add(key)
                dd = float(np.linalg.norm(pos[sb.id] - pos[sa.id]))
                links.append(_mk_link(sa.id, sb.id, LinkType.ISL_LEO_LEO, cfg["bw_mbps"],
                                      cfg["loss_rate"], cfg["jitter_ms"], beta,
                                      _delay_ms(pos[sa.id], pos[sb.id]),
                                      _isl_factor(dd, isl_floor, tv)))
                break

    # 同壳层邻面(环形)
    for shell, sats in by_shell.items():
        planes = sorted(set(s.plane_idx for s in sats))
        P = len(planes)
        by_plane = {p: [s for s in sats if s.plane_idx == p] for p in planes}
        for i, p in enumerate(planes):
            pj = planes[(i + 1) % P]
            if pj == p:
                continue
            connect(by_plane[p], by_plane[pj], kmax=1)

    # 跨壳层:每颗对"其它壳层"最近的一颗
    if cross_shell:
        shells = sorted(by_shell.keys())
        for i in range(len(shells)):
            for j in range(i + 1, len(shells)):
                connect(by_shell[shells[i]], by_shell[shells[j]], kmax=1)
    return links


def _isl_leo_geo(leos, geos, pos, cfg, beta, isl_floor, tv) -> List[Link]:
    links = []
    for leo in leos:
        for g in geos:
            if not los_clear(pos[leo.id], pos[g.id]):
                continue
            d = float(np.linalg.norm(pos[g.id] - pos[leo.id]))
            links.append(_mk_link(leo.id, g.id, LinkType.ISL_LEO_GEO, cfg["bw_mbps"],
                                  cfg["loss_rate"], cfg["jitter_ms"], beta,
                                  _delay_ms(pos[leo.id], pos[g.id]),
                                  _isl_factor(d, isl_floor, tv)))
    return links


# ============= SGL =============
def _sgl(sats, grounds, pos, cfg, ltype, beta, sgl_floor, tv) -> List[Link]:
    links = []
    min_elev = cfg["min_elevation_deg"]
    for sat in sats:
        for g in grounds:
            elev = elevation_deg(pos[g.id], pos[sat.id])
            if elev < min_elev:
                continue
            links.append(_mk_link(sat.id, g.id, ltype, cfg["bw_mbps"],
                                  cfg["loss_rate"], cfg["jitter_ms"], beta,
                                  _delay_ms(pos[sat.id], pos[g.id]),
                                  _sgl_factor(elev, min_elev, sgl_floor, tv)))
    return links


# ============= TER =============
def _ter(grounds, pos, cfg, beta) -> List[Link]:
    dcs = [g for g in grounds if g.type == NodeType.DC]
    edges = [g for g in grounds if g.type == NodeType.EDGE]
    access = [g for g in grounds if g.type == NodeType.ACCESS]
    links = []

    def add(u, v):
        d = _delay_ms(pos[u], pos[v]) * 1.5
        links.append(Link(u=u, v=v, type=LinkType.TER, bw_total_mbps=cfg["bw_mbps"],
                          bw_remain_mbps=cfg["bw_mbps"], delay_ms=d,
                          loss=cfg["loss_rate"], jitter_ms=cfg["jitter_ms"], beta=beta))

    hubs = edges + dcs
    for i, a in enumerate(dcs):
        for b in dcs[i + 1:]:
            add(a.id, b.id)
    for e in edges:
        for d in sorted(dcs, key=lambda x: float(np.linalg.norm(pos[e.id] - pos[x.id])))[:2]:
            add(e.id, d.id)
    for ac in access:
        for d in sorted(hubs, key=lambda x: float(np.linalg.norm(pos[ac.id] - pos[x.id])))[:2]:
            add(ac.id, d.id)
    # EDGE 之间也连最近一个,提升地面连通
    for i, e in enumerate(edges):
        others = [x for x in edges if x.id != e.id]
        if others:
            nb = min(others, key=lambda x: float(np.linalg.norm(pos[e.id] - pos[x.id])))
            add(e.id, nb.id)
    return links


# ============= 场景剪裁 =============
def scenario_config(cfg: dict, scenario: int):
    con = cfg["constellation"]
    shells = con["scenario_shells"][scenario]
    use_geo = con["scenario_geo"][scenario]
    ground_form = con["scenario_ground"][scenario]
    if scenario == 1:
        eligible = {"LEO"}
    elif scenario == 2:
        eligible = {"LEO", "EDGE", "DC"}
    else:
        eligible = {"LEO", "GEO"}
    return set(shells), use_geo, ground_form, eligible


def select_nodes(all_nodes: List[Node], cfg: dict, scenario: int) -> Tuple[List[Node], set]:
    active_shells, use_geo, ground_form, eligible = scenario_config(cfg, scenario)
    use_dc = cfg["ground"].get("use_dc", False)
    out = []
    for n in all_nodes:
        if n.type == NodeType.LEO:
            if n.shell in active_shells:
                out.append(n)
        elif n.type == NodeType.GEO:
            if use_geo:
                out.append(n)
        elif n.type == NodeType.ACCESS:
            if ground_form == "edge_access":
                out.append(n)
        elif n.type == NodeType.EDGE:
            if ground_form in ("edge_access", "compute_only"):
                out.append(n)
        elif n.type == NodeType.DC:
            if ground_form in ("edge_access", "compute_only") and use_dc:
                out.append(n)
    return out, eligible


# ============= 入口 =============
def build_snapshot(t_sec: float, all_nodes: List[Node], cfg: dict, scenario: int) -> Snapshot:
    beta_map = cfg["energy_link_beta_J_per_bit"]
    cfg_link = cfg["link_params"]
    ch = cfg.get("channel", {})
    tv = ch.get("time_varying", True)
    sgl_floor = ch.get("sgl_rate_floor", 0.4)
    isl_floor = ch.get("isl_rate_floor", 0.7)
    cross_shell = cfg["constellation"].get("allow_cross_shell_isl", True)

    nodes, eligible = select_nodes(all_nodes, cfg, scenario)
    pos = {n.id: position_ecef(n, t_sec) for n in nodes}
    leos = [n for n in nodes if n.type == NodeType.LEO]
    geos = [n for n in nodes if n.type == NodeType.GEO]
    grounds = [n for n in nodes if n.type in (NodeType.DC, NodeType.EDGE, NodeType.ACCESS)]

    links: List[Link] = []
    links += _isl_intra_plane(leos, pos, cfg_link["isl_leo_leo"], beta_map["ISL_LEO_LEO"], isl_floor, tv)
    links += _isl_inter_plane(leos, pos, cfg_link["isl_leo_leo"], beta_map["ISL_LEO_LEO"],
                              isl_floor, tv, cross_shell)
    if geos:
        links += _isl_leo_geo(leos, geos, pos, cfg_link["isl_leo_geo"], beta_map["ISL_LEO_GEO"],
                              isl_floor, tv)
        links += _sgl(geos, grounds, pos, cfg_link["sgl_geo"], LinkType.SGL_GEO,
                      beta_map["SGL_GEO"], sgl_floor, tv)
    links += _sgl(leos, grounds, pos, cfg_link["sgl_leo"], LinkType.SGL_LEO,
                  beta_map["SGL_LEO"], sgl_floor, tv)
    if any(g.type in (NodeType.EDGE, NodeType.DC) for g in grounds):
        links += _ter(grounds, pos, cfg_link["ter"], beta_map["TER"])

    nodes_dict = {n.id: n for n in nodes}
    links_dict: Dict[Tuple[str, str], Link] = {}
    for lk in links:
        key = (lk.u, lk.v) if lk.u < lk.v else (lk.v, lk.u)
        if key in links_dict:
            continue
        links_dict[key] = lk
    return Snapshot(t=t_sec, nodes=nodes_dict, links=links_dict, compute_eligible=eligible)


# ============= 地面终端接入 =============
def _sat_table(snapshot: Snapshot, cfg: dict):
    """本快照卫星 ID / ECEF 位置矩阵 / 最小仰角,按快照缓存。"""
    cache = getattr(snapshot, "_sat_table", None)
    if cache is not None:
        return cache
    lp = cfg["link_params"]
    min_elev = {NodeType.LEO: lp["sgl_leo"]["min_elevation_deg"],
                NodeType.GEO: lp["sgl_geo"]["min_elevation_deg"]}
    sats = [n for n in snapshot.nodes.values() if n.type in min_elev]
    ids = [n.id for n in sats]
    pos = (np.stack([position_ecef(n, snapshot.t) for n in sats])
           if sats else np.zeros((0, 3)))
    thr = np.array([min_elev[n.type] for n in sats])
    snapshot._sat_table = (ids, pos, thr)
    return snapshot._sat_table


def satellite_coverage(snapshot: Snapshot, lat: float, lon: float, cfg: dict
                       ) -> List[Tuple[str, float]]:
    """返回当前快照中覆盖 (lat, lon) 的卫星 [(sat_id, 仰角deg)],按仰角降序。

    覆盖判据与 SGL 一致:LEO 仰角 ≥ sgl_leo.min_elevation_deg,GEO ≥ sgl_geo.min_elevation_deg。
    """
    ids, pos, thr = _sat_table(snapshot, cfg)
    if not ids:
        return []
    gpos = _geodetic_to_ecef_m(lat, lon, 0.0)
    los = pos - gpos
    zenith = gpos / np.linalg.norm(gpos)
    # 用逐元素乘加而非 @:macOS Accelerate 的 matmul 会误报浮点警告
    cos_zen = np.clip((los * zenith).sum(axis=1) / np.linalg.norm(los, axis=1), -1.0, 1.0)
    elev = 90.0 - np.degrees(np.arccos(cos_zen))
    out = [(ids[i], float(elev[i])) for i in np.nonzero(elev >= thr)[0]]
    out.sort(key=lambda x: -x[1])
    return out


def attach_terminals(snapshot: Snapshot, terminals: List[Tuple[str, float, float]],
                     cfg: dict) -> None:
    """把地面终端 [(id, lat, lon)] 挂到快照:先算卫星覆盖,再为每颗可见卫星建一条 SGL。

    终端算力为 0、不在 compute_eligible 中,且登记到 endpoint_only,
    求解器只能把它当作路径起点/终点,不能经它在卫星间中转。
    """
    beta_map = cfg["energy_link_beta_J_per_bit"]
    lp = cfg["link_params"]
    ch = cfg.get("channel", {})
    tv = ch.get("time_varying", True)
    sgl_floor = ch.get("sgl_rate_floor", 0.4)
    sgl = {NodeType.LEO: (lp["sgl_leo"], LinkType.SGL_LEO, beta_map["SGL_LEO"]),
           NodeType.GEO: (lp["sgl_geo"], LinkType.SGL_GEO, beta_map["SGL_GEO"])}
    ids, pos, _ = _sat_table(snapshot, cfg)
    row = {sid: i for i, sid in enumerate(ids)}
    for tid, lat, lon in terminals:
        if tid in snapshot.nodes:
            continue
        gpos = _geodetic_to_ecef_m(lat, lon, 0.0)
        covered = []
        for sid, elev in satellite_coverage(snapshot, lat, lon, cfg):
            sat = snapshot.nodes[sid]
            lcfg, ltype, beta = sgl[sat.type]
            spos = pos[row[sid]]
            lk = _mk_link(sid, tid, ltype, lcfg["bw_mbps"], lcfg["loss_rate"], lcfg["jitter_ms"],
                          beta, _delay_ms(spos, gpos),
                          _sgl_factor(elev, lcfg["min_elevation_deg"], sgl_floor, tv))
            snapshot.links[snapshot.link_key(sid, tid)] = lk
            covered.append(sid)
        # 覆盖计算用的是挂接前的卫星表,终端节点最后再加入
        snapshot.nodes[tid] = Node(id=tid, type=NodeType.TERMINAL, capacity_total=0.0,
                                   capacity_remain=0.0, eta=0.0, power_budget_W=0.0,
                                   power_remain_W=0.0, lat=lat, lon=lon)
        snapshot.endpoint_only.add(tid)
        snapshot.coverage[tid] = covered
    snapshot._adj = None


def terminals_of(tasks) -> List[Tuple[str, float, float]]:
    """从任务中收集需要挂接的终端 (id, lat, lon)。"""
    out = []
    for tk in tasks:
        if tk.src_coord is None:
            continue
        out.append((tk.src, *tk.src_coord))
        for dst, coord in zip(tk.dst_set, tk.dst_coords or []):
            out.append((dst, *coord))
    return out
