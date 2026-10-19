"""M3 任务生成器:4类业务到达、高峰包络和星上/地面端点模式。

默认 satellite 模式下，所有业务都由卫星产生并投递到其他卫星；LL/HR从
当前时间片拓扑中选择较近的卫星，HC/MC可选择全局卫星。地面站仅作为算力节点。
"""
from __future__ import annotations
from heapq import heappop, heappush
import math
import random
from typing import List, Dict, Tuple
from models import Task, BizType, NodeType, Snapshot


def _rate_mbps(rng, cfg) -> float:
    if "rate_mbps" in cfg:
        lo, hi = cfg["rate_mbps"]; return rng.uniform(lo, hi)
    if "rate_kbps" in cfg:
        lo, hi = cfg["rate_kbps"]; return rng.uniform(lo, hi) / 1000.0
    raise ValueError("缺少 rate_mbps / rate_kbps")


def _pick_distinct(rng, pool, k, excludes):
    cand = [x for x in pool if x not in excludes]
    if k >= len(cand):
        return list(cand)
    return rng.sample(cand, k)


def _haversine_km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    r = 6371.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp = math.radians(la2 - la1); dl = math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


class TaskGenerator:
    def __init__(self, traffic_cfg: dict, access_nodes, slot_sec: float, seed: int = 42,
                 end_time_sec: float = None):
        """access_nodes仅供兼容ground端点模式，格式为List[(id,lat,lon)]。"""
        self.cfg = traffic_cfg
        self.endpoint_mode = str(traffic_cfg.get("endpoint_mode", "satellite")).lower()
        self.access = [a[0] for a in access_nodes]
        self.coord = {a[0]: (a[1], a[2]) for a in access_nodes}
        self.slot = slot_sec
        self.rng = random.Random(seed)
        self.end_time_sec = end_time_sec
        self.duration_adjustments = []
        self._counter = 0
        self.peak = traffic_cfg.get("peak_profile", [])
        # 预计算每个 access 的“由近到远”邻居列表(不含自己)
        self.nearest: Dict[str, List[str]] = {}
        for a in self.access:
            others = [x for x in self.access if x != a]
            others.sort(key=lambda x: _haversine_km(self.coord[a], self.coord[x]))
            self.nearest[a] = others
        self._snapshot_t = None
        self._sat_nearest_cache: Dict[str, List[str]] = {}

    def _endpoint_pool(self, snapshot: Snapshot = None, biz: str = None) -> List[str]:
        if self.endpoint_mode == "satellite":
            if snapshot is None:
                return []
            allowed = set(self.cfg.get("endpoint_types_by_biz", {}).get(
                biz, ["LEO", "GEO"]))
            return [nid for nid, node in snapshot.nodes.items()
                    if node.type.value in allowed]
        return list(self.access)

    def _nearest_satellites(self, snapshot: Snapshot, src: str,
                            endpoints: List[str]) -> List[str]:
        """按当前静态快照的最短传播时延排列可达卫星。"""
        if self._snapshot_t != snapshot.t:
            self._snapshot_t = snapshot.t
            self._sat_nearest_cache.clear()
        if src in self._sat_nearest_cache:
            return self._sat_nearest_cache[src]
        dist = {src: 0.0}
        heap = [(0.0, src)]
        adj = snapshot.adj()
        while heap:
            d, u = heappop(heap)
            if d != dist.get(u):
                continue
            for v in adj.get(u, []):
                link = snapshot.get_link(u, v)
                if link is None or not link.alive:
                    continue
                nd = d + link.delay_ms
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    heappush(heap, (nd, v))
        ordered = sorted((x for x in endpoints if x != src and x in dist),
                         key=lambda x: (dist[x], x))
        self._sat_nearest_cache[src] = ordered
        return ordered

    def _new_id(self, biz):
        self._counter += 1
        return f"T-{biz}-{self._counter:06d}"

    def _peak_mult(self, slot_idx: int) -> float:
        for seg in self.peak:
            if seg["from"] <= slot_idx < seg["to"]:
                return float(seg["mult"])
        return 1.0

    def _poisson(self, lam):
        if lam <= 0:
            return 0
        L = pow(2.718281828, -lam); k = 0; p = 1.0
        while p > L:
            k += 1; p *= self.rng.random()
        return k - 1

    def gen_for_slot(self, t_now: float, slot_idx: int,
                     snapshot: Snapshot = None) -> List[Task]:
        out: List[Task] = []
        rates = self.cfg.get("arrival_rate_per_slot", {})
        fixed = self.cfg.get("fixed_arrivals_per_slot", {})
        overrides = self.cfg.get("fixed_arrival_overrides", {})
        slot_override = overrides.get(slot_idx, overrides.get(str(slot_idx), {}))
        qos: Dict[str, dict] = self.cfg["qos"]
        mult = self._peak_mult(slot_idx)
        for biz_str, lam in rates.items():
            if biz_str in fixed:
                n = int(slot_override.get(biz_str, fixed[biz_str]))
            else:
                n = self._poisson(lam * mult)
            bc = qos[biz_str]
            endpoints = self._endpoint_pool(snapshot, biz_str)
            for _ in range(n):
                if len(endpoints) < 2:
                    continue
                src = self.rng.choice(endpoints)
                dst_knn = bc.get("dst_knn")
                if self.endpoint_mode == "satellite" and dst_knn:
                    pool = self._nearest_satellites(snapshot, src, endpoints)[:int(dst_knn)]
                elif self.endpoint_mode == "ground" and dst_knn:
                    pool = self.nearest[src][:int(dst_knn)]
                else:
                    pool = [x for x in endpoints if x != src]
                if biz_str == "MC":
                    dst_set = _pick_distinct(self.rng, pool, bc.get("k_dst", 3), [])
                    if not dst_set:
                        continue
                else:
                    if not pool:
                        continue
                    dst_set = [self.rng.choice(pool)]
                rate = _rate_mbps(self.rng, bc)
                dur = self.rng.uniform(*bc["duration_s"])
                # 时间片模型只在边界做一次联合路由。任务统一在片首进入，避免
                # “记录为片内到达、实际却在片首提前占资源”的时间口径错误。
                at_slot_start = self.cfg.get("arrival_at_slot_start", True)
                random_all = self.cfg.get("random_arrivals_all_businesses", False)
                independent_time = self.cfg.get("independent_time_rng", False)
                if independent_time:
                    # Keep endpoints/rates paired when duration/arrival settings change.
                    if not hasattr(self, "_time_rng"):
                        self._time_rng = random.Random(self.cfg.get("time_seed", 1042))
                    dur = self._time_rng.uniform(*bc["duration_s"])
                    intra_slot_offset = self._time_rng.uniform(0, self.slot)
                else:
                    intra_slot_offset = self.rng.uniform(0, self.slot) if random_all or bc["mode"] == "burst" else 0.0
                # 即使启用片首到达也保留一次随机采样，使其他任务属性和旧实验
                # 使用完全相同的随机数序列，便于严格对照。
                arrival = t_now if at_slot_start else t_now + intra_slot_offset
                sampled_duration = dur
                if self.end_time_sec is not None:
                    dur = min(dur, max(0.0, self.end_time_sec - arrival))
                    if dur <= 0:
                        continue
                task_id = self._new_id(biz_str)
                omega_base = float(bc.get("cycles_per_bit_base", bc.get("cycles_per_bit", 0.0)))
                omega_per_dst = float(bc.get("cycles_per_bit_per_dst", 0.0))
                omega_eff = omega_base + len(dst_set) * omega_per_dst
                data_bits = rate * 1e6 * dur
                rho = max(1e-3, min(1.0, float(bc.get("compression_rho", 1.0))))
                out.append(Task(
                    id=task_id, src=src, dst_set=dst_set,
                    biz=BizType(biz_str), priority=int(bc["priority"]), mode=bc["mode"],
                    rate_mbps=rate, delay_budget_ms=float(bc["delay_ms"]),
                    loss_budget=float(bc["loss"]), cycles_per_bit=omega_base,
                    Lref_bits=float(bc["Lref_bits"]), arrival_t=arrival, duration_s=dur,
                    compression_rho=rho,
                    processing_logic=str(bc.get("processing_logic", "")),
                    cycles_per_bit_per_dst=omega_per_dst,
                    fixed_processing_ms=float(bc.get("fixed_processing_ms", 0.0)),
                    fixed_processing_ms_per_dst=float(bc.get("fixed_processing_ms_per_dst", 0.0)),
                    cycles_total=data_bits * omega_eff, data_volume_bits=data_bits,
                    compute_rate=rate * 1e6 * omega_eff,   # equivalent op/s
                ))
                self.duration_adjustments.append(dict(task_id=task_id,biz=biz_str,
                    arrival_t=arrival,sampled_duration_s=sampled_duration,
                    sampled_t_end=arrival+sampled_duration,duration_s=dur,t_end=arrival+dur,
                    duration_clipped=int(dur<sampled_duration),hard_end_sec=self.end_time_sec))
        out.sort(key=lambda x: (x.priority, x.arrival_t))
        return out
