"""主仿真循环：时间片驱动 + 任务调度 + 资源簿记 + KPI 收集。"""
from __future__ import annotations
import time
from typing import Dict, List, Optional

from models import Node, Snapshot, Task, BizType, Allocation, NodeType
from topology import build_walker_star_leo, build_geo, build_ground
from network import build_snapshot
from tasks import TaskGenerator
from router import solve_task
from resources import ResourceLedger
from metrics import Metrics


def _max_path_delay(snapshot: Snapshot, alloc: Allocation) -> float:
    best = 0.0
    for path in alloc.paths:
        d = 0.0
        for u, v in zip(path[:-1], path[1:]):
            link = snapshot.get_link(u, v)
            if link is None:
                d = float("inf")
                break
            d += link.delay_ms
        best = max(best, d)
    return best


class Simulator:
    def __init__(self, cfg: dict, scenario: Optional[str] = None,
                 force_strategy: Optional[str] = None):
        self.cfg = cfg
        self.sim_cfg = cfg["simulation"]
        if scenario is not None:
            self.sim_cfg["scenario"] = scenario
        self.scenario: str = self.sim_cfg["scenario"]
        self.slot = float(self.sim_cfg["slot_sec"])
        self.t_start = float(self.sim_cfg["t_start_sec"])
        self.t_end = float(self.sim_cfg["t_end_sec"])
        self.force_strategy = force_strategy

        # ------- 全量节点 -------
        self.all_nodes: List[Node] = self._build_all_nodes()

        # ------- 任务生成器 -------
        access_ids = [n.id for n in self.all_nodes if n.type == NodeType.ACCESS]
        self.gen = TaskGenerator(cfg["traffic"], access_ids,
                                 seed=int(self.sim_cfg.get("random_seed", 42)))

        self.ledger = ResourceLedger(cfg["algorithm"])
        self.metrics = Metrics()

    def _build_all_nodes(self) -> List[Node]:
        cap = self.cfg["compute_capacity_tflops"]
        eta = self.cfg["energy_compute_eta_J_per_cycle"]
        # 默认 CPU 频率 / κ（仅供记录，η 已直接给）
        freq_map = {"LEO": 1e9, "GEO": 1.5e9, "EDGE": 2e9, "DC": 3e9, "ACCESS": 0.0}
        kappa_map = {"LEO": 1e-27, "GEO": 1e-27, "EDGE": 5e-28, "DC": 1e-28, "ACCESS": 0.0}

        leo_cfg = self.cfg["constellation"]["leo"]
        scale = self.cfg.get("scale", {})
        if not getattr(self, "_no_scale", False):
            if "leo_total_override" in scale:
                leo_cfg = dict(leo_cfg)
                leo_cfg["total"] = scale["leo_total_override"]
            if "leo_planes_override" in scale:
                leo_cfg = dict(leo_cfg)
                leo_cfg["planes"] = scale["leo_planes_override"]
        leos = build_walker_star_leo(
            total=leo_cfg["total"], planes=leo_cfg["planes"],
            phase_F=leo_cfg["phase"], inc_deg=leo_cfg["inclination_deg"],
            alt_km=leo_cfg["altitude_km"],
            capacity_tflops=cap["LEO"],
            eta_J_per_cycle=eta["LEO"],
            cpu_freq=freq_map["LEO"], cap_coef=kappa_map["LEO"],
        )
        geo_cfg = self.cfg["constellation"]["geo"]
        geos = build_geo(longitudes_deg=geo_cfg["longitudes_deg"],
                         capacity_tflops=cap["GEO"], eta_J_per_cycle=eta["GEO"],
                         cpu_freq=freq_map["GEO"], cap_coef=kappa_map["GEO"])
        ground = build_ground(self.cfg["ground"]["stations"], cap, eta,
                              freq_map, kappa_map)
        return leos + geos + ground

    def disable_scale(self):
        self._no_scale = True
        self.all_nodes = self._build_all_nodes()
        access_ids = [n.id for n in self.all_nodes if n.type == NodeType.ACCESS]
        self.gen = TaskGenerator(self.cfg["traffic"], access_ids,
                                 seed=int(self.sim_cfg.get("random_seed", 42)))

    # ============= 主循环 =============
    def run(self):
        beta_map = self.cfg["energy_link_beta_J_per_bit"]
        cfg_link = self.cfg["link_params"]

        t = self.t_start
        slot_idx = 0
        wall_start = time.time()
        prev_snapshot: Optional[Snapshot] = None
        while t < self.t_end:
            slot_idx += 1
            # ---------- M2: 当前时间片快照 ----------
            snapshot = build_snapshot(t, self.all_nodes, cfg_link, beta_map, self.scenario)

            # 跨片资源迁移：把仍活跃的 alloc 落到新 snapshot 上（能落则保留）
            self.ledger.reset_snapshot_state(snapshot)

            # 释放已结束任务
            finished = self.ledger.evict_finished(snapshot, t)

            # ---------- M3: 任务到达 ----------
            new_tasks: List[Task] = self.gen.gen_for_slot(t, self.slot)

            # ---------- M4: 调度 ----------
            accepted_n = 0
            rejected_n = 0
            for tk in new_tasks:
                self.metrics.record_attempt(tk)
                alloc = solve_task(snapshot, tk, self.scenario,
                                   self.cfg["algorithm"], self.force_strategy)
                if alloc is None:
                    # LL 业务尝试增量抢占：每次抢一个最大占用者再重试
                    if tk.biz == BizType.LL and self.cfg["algorithm"].get("enable_preemption"):
                        max_attempts = 20
                        for _ in range(max_attempts):
                            p = self.ledger.preempt_one(snapshot)
                            if p is None:
                                break
                            self.metrics.preemptions += 1
                            alloc = solve_task(snapshot, tk, self.scenario,
                                               self.cfg["algorithm"], self.force_strategy)
                            if alloc is not None:
                                break
                if alloc is None:
                    self.metrics.record_reject(tk, "no_feasible_path")
                    rejected_n += 1
                    continue
                # 落账
                alloc._cached_max_delay_ms = _max_path_delay(snapshot, alloc)
                alloc.t_alloc = t
                self.ledger.reserve(snapshot, alloc)
                self.metrics.record_alloc(alloc)
                accepted_n += 1

            self.metrics.record_slot(t, snapshot, accepted_n, rejected_n)
            wall = time.time() - wall_start
            print(f"[slot {slot_idx:>3} t={t:>6.0f}s] "
                  f"new={len(new_tasks):>3} accept={accepted_n:>3} reject={rejected_n:>3} "
                  f"finished={len(finished):>3} active={len(self.ledger.active):>4} "
                  f"|V|={len(snapshot.nodes)} |E|={len(snapshot.links)} "
                  f"wall={wall:.1f}s")

            prev_snapshot = snapshot
            t += self.slot

        self.metrics.print_summary()
        return self.metrics
