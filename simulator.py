"""主仿真循环:时间片驱动 + 任务调度 + 资源簿记 + KPI(《设计文档》§10)。"""
from __future__ import annotations
import time
from typing import List, Optional

from models import Node, Snapshot, Task, BizType, NodeType
from topology import build_shell, build_geo, build_ground
from network import build_snapshot, attach_terminals, terminals_of
from tasks import TaskGenerator
from resources import ResourceLedger
from metrics import Metrics
from solvers import make_solver


class Simulator:
    def __init__(self, cfg: dict, scenario=None, strategy=None, solver=None):
        self.cfg = cfg
        self.sim = cfg["simulation"]
        self.scenario = int(scenario if scenario is not None else self.sim["scenario"])
        self.strategy = strategy or self.sim["strategy"]
        self.solver_name = solver or self.sim["solver"]
        self.slot = float(self.sim["slot_sec"])
        self.t_start = float(self.sim["t_start_sec"])
        self.t_end = float(self.sim["t_end_sec"])
        self.max_concurrency = int(cfg["constellation"].get(
            "scenario_max_concurrency", {}).get(self.scenario, 0))

        self.all_nodes = self._build_nodes()
        access = [(n.id, n.lat, n.lon) for n in self.all_nodes if n.type == NodeType.ACCESS]
        self.gen = TaskGenerator(cfg["traffic"], access, self.slot,
                                 seed=int(self.sim.get("random_seed", 42)),
                                 end_time_sec=self.t_end if cfg["traffic"].get(
                                     "truncate_at_simulation_end", False) else None)
        self.metrics = Metrics(meta={"scenario": self.scenario, "strategy": self.strategy,
                                     "solver": self.solver_name,
                                     "endpoint_mode": cfg["traffic"].get("endpoint_mode", "satellite")})
        self.ledger = ResourceLedger(cfg["algorithm"], event_sink=self.metrics.record_resource_event)
        self.solver = make_solver(self.solver_name, cfg)
        self._allocation_counter = 0

    def _build_nodes(self) -> List[Node]:
        cap = self.cfg["compute_capacity_tflops"]
        eta = self.cfg["energy_compute_eta_J_per_cycle"]
        pw = self.cfg["compute_power_budget_W"]
        nodes: List[Node] = []
        for shell in self.cfg["constellation"]["shells"]:
            nodes += build_shell(shell, cap["LEO"], eta["LEO"], pw["LEO"])
        geo = self.cfg["constellation"]["geo"]
        nodes += build_geo(geo["longitudes_deg"], cap["GEO"], eta["GEO"], pw["GEO"])
        nodes += build_ground(self.cfg["ground"]["stations"], cap, eta, pw)
        return nodes

    @staticmethod
    def reject_reason(snapshot, task) -> str:
        """无可行解时细分原因:终端不在任何卫星覆盖内 / 其它约束不可行。"""
        if task.src in snapshot.coverage and not snapshot.coverage[task.src]:
            return "src_no_coverage"
        if any(d in snapshot.coverage and not snapshot.coverage[d] for d in task.dst_set):
            return "dst_no_coverage"
        return "no_feasible"

    def attach_terminals(self, snapshot, tasks):
        if self.cfg["traffic"].get("endpoint_mode") == "terminal":
            attach_terminals(snapshot, terminals_of(tasks), self.cfg)

    def _solve(self, snapshot, task) -> Optional[object]:
        t0 = time.perf_counter()
        alloc = self.solver.solve(snapshot, task, self.strategy)
        self.metrics.solve_times_ms.append((time.perf_counter() - t0) * 1000.0)
        return alloc

    def _identify_allocation(self, alloc, t, event_type):
        self._allocation_counter += 1
        alloc.allocation_id = f"A-{self._allocation_counter:08d}"
        alloc.t_alloc = t
        self.metrics.record_alloc(alloc, event_type=event_type)

    def _schedule_task(self, snapshot, tk, t) -> bool:
        alloc = self._solve(snapshot, tk)
        if alloc is None and tk.biz in (BizType.LL, BizType.HR) \
                and self.cfg["algorithm"].get("enable_preemption"):
            for _ in range(20):
                p = self.ledger.preempt_one(snapshot, tk.priority, t_now=t)
                if p is None:
                    break
                self.metrics.preemptions += 1
                alloc = self._solve(snapshot, tk)
                if alloc is not None:
                    break
        if alloc is None:
            return False
        self._identify_allocation(alloc, t, "INITIAL")
        self.ledger.reserve(snapshot, alloc, t_now=t, event="ALLOCATE")
        return True

    def run(self):
        if self.sim.get("event_driven_within_slot", False):
            from event_simulation import run_event_simulation
            return run_event_simulation(self)
        t = self.t_start
        slot_idx = 0
        wall = time.time()
        while t < self.t_end:
            snapshot = build_snapshot(t, self.all_nodes, self.cfg, self.scenario)
            new_tasks = self.gen.gen_for_slot(t, slot_idx, snapshot=snapshot)
            for tk in new_tasks:
                self.metrics.record_attempt(tk, generated_slot=slot_idx, scheduled_t=t)
            # 地面终端:按本片卫星位置重算覆盖并接入(存续任务也要重新接入)
            self.attach_terminals(snapshot, [a.task for a in self.ledger.active] + new_tasks)

            rerouted = reroute_failed = 0
            acc = rej = 0
            if self.cfg["algorithm"].get("reroute_all_each_slot", True):
                # 每个片首从满资源状态开始，把跨片存续任务与本片新任务放入同一个
                # 优先级队列，各求解一次。片内拓扑、路径和资源分配均保持不变。
                previous = list(self.ledger.active)
                self.ledger.active.clear()
                for n in snapshot.nodes.values():
                    n.reset_resources()
                for lk in snapshot.links.values():
                    lk.reset_bw()
                finished = [a for a in previous if a.task.t_end <= t]
                ongoing = [a for a in previous if a.task.t_end > t]
                jobs = [(a.task, a) for a in ongoing] + [(tk, None) for tk in new_tasks]
                jobs.sort(key=lambda pair: (pair[0].priority, pair[0].arrival_t, pair[0].id))
                for task, old in jobs:
                    if self.max_concurrency and len(self.ledger.active) >= self.max_concurrency:
                        if old is None:
                            self.metrics.record_reject(task, "scenario_concurrency_cap")
                            rej += 1
                        else:
                            self.metrics.record_drop(old)
                            reroute_failed += 1
                        continue
                    new = self._solve(snapshot, task)
                    if new is None:
                        if old is None:
                            self.metrics.record_reject(task, self.reject_reason(snapshot, task))
                            rej += 1
                        else:
                            self.metrics.record_drop(old)
                            reroute_failed += 1
                        continue
                    if old is None:
                        self._identify_allocation(new, t, "INITIAL")
                        self.ledger.reserve(snapshot, new, t_now=t, event="ALLOCATE")
                        acc += 1
                    else:
                        moved = new.compute_node != old.compute_node
                        self._identify_allocation(new, t, "MIGRATION" if moved else "REROUTE")
                        self.ledger.reserve(snapshot, new, t_now=t, event="REROUTE_ALLOCATE")
                        self.metrics.reroutes += 1
                        rerouted += 1
                        if moved:
                            self.metrics.migrations += 1
            else:
                # 兼容旧模式：旧路径仍可用则直接重落账，仅对断链任务重算。
                orphaned = self.ledger.reset_snapshot_state(snapshot, t_now=t)
                for a in orphaned:
                    if a.task.t_end <= t:
                        continue
                    new = self._solve(snapshot, a.task)
                    if new is not None:
                        self._identify_allocation(new, t, "MIGRATION")
                        self.ledger.reserve(snapshot, new, t_now=t, event="MIGRATE_ALLOCATE")
                        self.metrics.migrations += 1
                    else:
                        self.metrics.record_drop(a)
                        reroute_failed += 1
                finished = self.ledger.evict_finished(snapshot, t)
                rerouted = len(orphaned) - reroute_failed
                for tk in new_tasks:
                    if self.max_concurrency and len(self.ledger.active) >= self.max_concurrency:
                        self.metrics.record_reject(tk, "scenario_concurrency_cap")
                        rej += 1
                        continue
                    if self._schedule_task(snapshot, tk, t):
                        acc += 1
                    else:
                        self.metrics.record_reject(tk, self.reject_reason(snapshot, tk))
                        rej += 1

            self.metrics.record_slot(t, snapshot, acc, rej, len(self.ledger.active),
                                     finished=len(finished), rerouted=rerouted,
                                     reroute_failed=reroute_failed)
            print(f"[slot {slot_idx:>3} t={t:>6.0f}] new={len(new_tasks):>3} "
                  f"acc={acc:>3} rej={rej:>3} fin={len(finished):>3} "
                  f"active={len(self.ledger.active):>4} reroute={rerouted:>3} "
                  f"fail={reroute_failed:>3} "
                  f"|V|={len(snapshot.nodes)} |E|={len(snapshot.links)} "
                  f"wall={time.time()-wall:.1f}s")
            slot_idx += 1
            t += self.slot

        self.metrics.print_summary()
        return self.metrics
