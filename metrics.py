"""M6 评估:主 KPI = QoS 达标率(《设计文档》§11)。

按**唯一任务**计数(迁移会为同一任务生成新 Allocation,取最新;丢弃则标记失败),
避免重复计数导致达标率 > 100%。
"""
from __future__ import annotations
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from models import Allocation, Task, Snapshot

RESOURCE_FIELDS = [
    "event_seq", "t", "event", "allocation_id", "task_id", "biz",
    "resource_type", "resource_id", "node_id", "node_type", "link_u",
    "link_v", "link_type", "path_roles", "allocation_delta", "unit",
    "total_capacity", "remain_before", "remain_after",
]

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(p / 100.0 * len(s)))]


class Metrics:
    def __init__(self, meta: dict = None):
        self.meta = meta or {}
        self.attempts: List[Task] = []                 # 唯一到达
        self.alloc_by_task: Dict[str, Allocation] = {}  # 每任务最新分配
        self.dropped_ids: set = set()
        self.rejects: List[tuple] = []
        self.preemptions = 0
        self.migrations = 0
        self.reroutes = 0
        self.solve_times_ms: List[float] = []
        self._slot_stats: List[dict] = []
        self._task_context: Dict[str, dict] = {}
        self._allocation_history: List[dict] = []
        self._resource_events: List[dict] = []
        self._resource_event_seq = 0
        self.max_concurrency = 0
        self._resource_stream = None
        self._resource_stream_path = None
        self._history_index = {}
        self.task_terminal = {}
        self.task_service_seconds = defaultdict(float)
        self.task_energy = defaultdict(lambda: [0.0, 0.0])
        self.task_max_delay = defaultdict(float)
        self.task_max_loss = defaultdict(float)

    def stream_resources(self, out_dir):
        path = Path(out_dir).resolve() / "resource_allocations.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._resource_stream_path = path
        self._resource_stream = path.open("w", newline="", encoding="utf-8-sig")
        self._resource_writer = csv.DictWriter(self._resource_stream, fieldnames=RESOURCE_FIELDS)
        self._resource_writer.writeheader()

    def close_segment(self, a, t_end):
        duration = max(0.0, t_end-a.t_alloc)
        a.energy_compute_J = a._compute_J_per_s * duration
        a.energy_tx_J = a._tx_J_per_s * duration
        a.delivered_data_bits = a.task.rate_bps * a.task.compression_rho * duration
        row=self._allocation_history[self._history_index[a.allocation_id]]
        row.update(service_t_end=t_end, service_duration_s=duration,
                   energy_compute_J=a.energy_compute_J, energy_tx_J=a.energy_tx_J,
                   energy_total_J=a.energy_total_J, delivered_data_bits=a.delivered_data_bits)
        self.task_service_seconds[a.task.id]+=duration
        self.task_energy[a.task.id][0]+=a.energy_compute_J
        self.task_energy[a.task.id][1]+=a.energy_tx_J
        self.task_max_delay[a.task.id]=max(self.task_max_delay[a.task.id],a.max_delay_ms)
        self.task_max_loss[a.task.id]=max(self.task_max_loss[a.task.id],a.max_loss)

    def record_attempt(self, t, generated_slot=None, scheduled_t=None):
        self.attempts.append(t)
        self._task_context[t.id] = {
            "generated_slot": generated_slot,
            "scheduled_t": scheduled_t,
        }

    def record_alloc(self, a, event_type="INITIAL"):
        self.alloc_by_task[a.task.id] = a
        self.dropped_ids.discard(a.task.id)
        self._allocation_history.append({
            "allocation_id": a.allocation_id,
            "event_type": event_type,
            "task_id": a.task.id,
            "biz": a.task.biz.value,
            "t_alloc": a.t_alloc,
            "src": a.task.src,
            "dst_json": json.dumps(a.task.dst_set, ensure_ascii=False),
            "compute_node": a.compute_node,
            "compute_node_type": a.compute_node_type,
            "strategy": a.strategy,
            "solver": a.solver,
            "path_pre_json": json.dumps(a.path_pre, ensure_ascii=False),
            "path_post_json": json.dumps(a.path_post, ensure_ascii=False),
            "bw_per_link_json": json.dumps([
                {"u": u, "v": v, "bw_mbps": bw}
                for (u, v), bw in sorted(a.bw_per_link.items())
            ], ensure_ascii=False),
            "rate_mbps": a.task.rate_mbps,
            "compression_rho": a.task.compression_rho,
            "processing_logic": a.task.processing_logic,
            "cycles_per_bit_base": a.task.cycles_per_bit,
            "cycles_per_bit_per_dst": a.task.cycles_per_bit_per_dst,
            "cycles_per_bit_effective": a.task.effective_cycles_per_bit,
            "fixed_processing_ms_total": a.task.fixed_processing_total_ms,
            "compute_rate_used": a.compute_rate_used,
            "power_used_W": a.power_used_W,
            "compute_latency_ms": a.compute_latency_ms,
            "max_delay_ms": a.max_delay_ms,
            "max_loss": a.max_loss,
            "delivered_data_bits": a.delivered_data_bits,
            "energy_compute_J": a.energy_compute_J,
            "energy_tx_J": a.energy_tx_J,
            "energy_total_J": a.energy_total_J,
        })
        if self.meta.get("segment_accounting"):
            self._history_index[a.allocation_id]=len(self._allocation_history)-1
            self._allocation_history[-1].update(service_t_end="",service_duration_s=0.0)

    def record_resource_event(self, event: dict):
        self._resource_event_seq += 1
        row={"event_seq": self._resource_event_seq, **event}
        if self._resource_stream is not None:
            self._resource_writer.writerow(row)
        else:
            self._resource_events.append(row)

    def record_reject(self, t, r): self.rejects.append((t, r))

    def record_drop(self, a): self.dropped_ids.add(a.task.id)

    def record_slot(self, t, snapshot: Snapshot, accepted, rejected, active_n,
                    finished=0, rerouted=0, reroute_failed=0):
        lu = [1 - lk.bw_remain_mbps / lk.bw_total_mbps
              for lk in snapshot.links.values() if lk.bw_total_mbps > 0]
        nu = [1 - n.capacity_remain / n.capacity_total
              for n in snapshot.nodes.values() if n.capacity_total > 0]
        self.max_concurrency = max(self.max_concurrency, active_n)
        self._slot_stats.append({
            "t": t, "accepted": accepted, "rejected": rejected, "active": active_n,
            "finished": finished, "rerouted": rerouted, "reroute_failed": reroute_failed,
            "link_util_mean": _mean(lu), "link_util_max": max(lu) if lu else 0,
            "node_util_mean": _mean(nu), "node_util_max": max(nu) if nu else 0,
        })

    # ---------- 汇总(按唯一任务)----------
    def summary(self) -> dict:
        arrived = defaultdict(int)
        for t in self.attempts:
            arrived[t.biz.value] += 1

        accepted = defaultdict(int)     # 曾成功分配且未被最终丢弃
        qos_pass = defaultdict(int)
        energy = defaultdict(float)
        served: List[Allocation] = []
        for tid, a in self.alloc_by_task.items():
            b = a.task.biz.value
            if tid in self.dropped_ids:
                continue
            accepted[b] += 1
            energy[b] += a.energy_total_J
            served.append(a)
            if (a.max_delay_ms <= a.task.delay_budget_ms + 1e-6
                    and a.max_loss <= a.task.loss_budget + 1e-12):
                qos_pass[b] += 1

        qos_rate = {b: qos_pass[b] / arrived[b] if arrived[b] else 0.0 for b in arrived}
        accept_rate = {b: accepted[b] / arrived[b] if arrived[b] else 0.0 for b in arrived}
        tot_arr = sum(arrived.values()); tot_qos = sum(qos_pass.values())

        e_total = sum(a.energy_total_J for a in served)
        e_comp = sum(a.energy_compute_J for a in served)
        e_tx = sum(a.energy_tx_J for a in served)
        node_dist = defaultdict(int)
        rho_l, tc_l, tcr_l, preh, posth = [], [], [], [], []
        for a in served:
            nt = a.compute_node_type
            if not nt:
                nt = a.compute_node.split("-")[0]
                nt = "LEO" if nt in ("L1", "L2", "L3") else nt
            node_dist[nt] += 1
            rho_l.append(a.task.compression_rho)
            tc_l.append(a.compute_latency_ms)
            if a.max_delay_ms > 0:
                tcr_l.append(a.compute_latency_ms / a.max_delay_ms)
            preh.append(len(a.path_pre) - 1)
            posth.append(_mean([len(p) - 1 for p in a.path_post]))

        result = {
            "meta": self.meta,
            "total_arrived": tot_arr,
            "total_accepted": len(served),
            "total_dropped": len(self.dropped_ids),
            "total_rejected": len(self.rejects),
            "preemptions": self.preemptions, "migrations": self.migrations,
            "reroutes": self.reroutes,
            "max_concurrency": self.max_concurrency,
            "qos_rate_overall": tot_qos / tot_arr if tot_arr else 0.0,
            "qos_rate_by_biz": qos_rate, "accept_rate_by_biz": accept_rate,
            "total_energy_J": e_total, "compute_energy_J": e_comp, "tx_energy_J": e_tx,
            "avg_energy_per_task_J": e_total / max(1, len(served)),
            "energy_by_biz_J": dict(energy),
            "avg_compression_savings": 1 - _mean(rho_l) if rho_l else 0.0,
            "compute_node_type_dist": dict(node_dist),
            "avg_compute_latency_ms": _mean(tc_l),
            "compute_latency_ratio": _mean(tcr_l),
            "avg_pre_hops": _mean(preh), "avg_post_hops": _mean(posth),
            "solve_ms_p50": _pct(self.solve_times_ms, 50),
            "solve_ms_p99": _pct(self.solve_times_ms, 99),
        }
        if self.meta.get("segment_accounting"):
            comp=sum(x[0] for x in self.task_energy.values())
            tx=sum(x[1] for x in self.task_energy.values())
            result.update(total_initially_accepted=len(self.alloc_by_task),
                total_completed=sum(s=="COMPLETED" for s in self.task_terminal.values()),
                total_active_at_end=sum(s=="ACTIVE_AT_END" for s in self.task_terminal.values()),
                total_energy_J=comp+tx, compute_energy_J=comp, tx_energy_J=tx,
                avg_energy_per_task_J=(comp+tx)/max(1,len(self.alloc_by_task)),
                total_service_seconds=sum(self.task_service_seconds.values()),
                energy_by_biz_J={b:sum(sum(self.task_energy[t.id]) for t in self.attempts if t.biz.value==b) for b in arrived})
        return result

    # ---------- 导出 ----------
    def export(self, out_dir: str):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        s = self.summary()
        with open(f"{out_dir}/kpi.json", "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False, default=str)
        with open(f"{out_dir}/slot_stats.csv", "w", newline="", encoding="utf-8-sig") as f:
            slot_fields=list(self._slot_stats[0]) if self.meta.get("segment_accounting") and self._slot_stats else ["t", "accepted", "rejected", "active",
                                              "finished", "rerouted", "reroute_failed",
                                              "link_util_mean", "link_util_max",
                                              "node_util_mean", "node_util_max"]
            w = csv.DictWriter(f, fieldnames=slot_fields)
            w.writeheader()
            for st in self._slot_stats:
                w.writerow(st)
        with open(f"{out_dir}/tasks.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["task_id", "biz", "src", "dst", "rate_mbps", "rho", "compute_node",
                        "compute_node_type",
                        "strategy", "solver", "delay_ms", "loss", "t_compute_ms",
                        "e_compute_J", "e_tx_J", "e_total_J", "status"])
            for tid, a in self.alloc_by_task.items():
                st = self.task_terminal.get(tid,"DROPPED" if tid in self.dropped_ids else "OK")
                energies=self.task_energy.get(tid,[a.energy_compute_J,a.energy_tx_J])
                w.writerow([a.task.id, a.task.biz.value, a.task.src, ";".join(a.task.dst_set),
                            f"{a.task.rate_mbps:.3f}", f"{a.task.compression_rho:.2f}",
                            a.compute_node, a.compute_node_type, a.strategy, a.solver,
                            f"{a.max_delay_ms:.3f}",
                            f"{a.max_loss:.2e}", f"{a.compute_latency_ms:.4f}",
                            f"{energies[0]:.4e}", f"{energies[1]:.4e}",
                            f"{sum(energies):.4e}", st])
            for t, r in self.rejects:
                w.writerow([t.id, t.biz.value, t.src, ";".join(t.dst_set),
                            f"{t.rate_mbps:.3f}", f"{t.compression_rho:.2f}", "", "", "", "",
                            "", "", "", "", "", "", f"REJECT:{r}"])

        # 所有自动生成的任务（无论接受、拒绝或后续丢弃）。
        reject_by_id = {t.id: reason for t, reason in self.rejects}
        generated_fields = [
            "task_id", "biz", "priority", "mode", "src", "dst_json",
            "src_coord_json", "dst_coords_json",
            "rate_mbps", "delay_budget_ms", "loss_budget", "processing_logic",
            "cycles_per_bit", "cycles_per_bit_per_dst", "cycles_per_bit_effective",
            "fixed_processing_ms", "fixed_processing_ms_per_dst",
            "fixed_processing_ms_total",
            "Lref_bits", "arrival_t", "duration_s", "t_end", "compression_rho",
            "cycles_total", "data_volume_bits", "compute_rate_cycles_s",
            "generated_slot", "scheduled_t", "final_status", "reject_reason",
        ]
        with open(f"{out_dir}/generated_tasks.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=generated_fields)
            w.writeheader()
            for t in self.attempts:
                if t.id in self.dropped_ids:
                    status = "DROPPED"
                elif t.id in self.alloc_by_task:
                    status = "ALLOCATED"
                else:
                    status = "REJECTED"
                status=self.task_terminal.get(t.id,status)
                ctx = self._task_context.get(t.id, {})
                w.writerow({
                    "task_id": t.id, "biz": t.biz.value, "priority": t.priority,
                    "mode": t.mode, "src": t.src,
                    "dst_json": json.dumps(t.dst_set, ensure_ascii=False),
                    "src_coord_json": json.dumps(t.src_coord) if t.src_coord else "",
                    "dst_coords_json": json.dumps(t.dst_coords) if t.dst_coords else "",
                    "rate_mbps": t.rate_mbps, "delay_budget_ms": t.delay_budget_ms,
                    "loss_budget": t.loss_budget, "processing_logic": t.processing_logic,
                    "cycles_per_bit": t.cycles_per_bit,
                    "cycles_per_bit_per_dst": t.cycles_per_bit_per_dst,
                    "cycles_per_bit_effective": t.effective_cycles_per_bit,
                    "fixed_processing_ms": t.fixed_processing_ms,
                    "fixed_processing_ms_per_dst": t.fixed_processing_ms_per_dst,
                    "fixed_processing_ms_total": t.fixed_processing_total_ms,
                    "Lref_bits": t.Lref_bits, "arrival_t": t.arrival_t,
                    "duration_s": t.duration_s, "t_end": t.t_end,
                    "compression_rho": t.compression_rho,
                    "cycles_total": t.cycles_total,
                    "data_volume_bits": t.data_volume_bits,
                    "compute_rate_cycles_s": t.compute_rate,
                    "generated_slot": ctx.get("generated_slot"),
                    "scheduled_t": ctx.get("scheduled_t"),
                    "final_status": status,
                    "reject_reason": reject_by_id.get(t.id, ""),
                })

        # 每次初始分配和迁移分配均保留一行，路径字段为标准 JSON。
        allocation_fields = [
            "allocation_id", "event_type", "task_id", "biz", "t_alloc", "src",
            "dst_json", "compute_node", "compute_node_type", "strategy", "solver", "path_pre_json",
            "path_post_json", "bw_per_link_json", "rate_mbps", "compression_rho",
            "processing_logic", "cycles_per_bit_base", "cycles_per_bit_per_dst",
            "cycles_per_bit_effective", "fixed_processing_ms_total",
            "compute_rate_used", "power_used_W", "compute_latency_ms", "max_delay_ms",
            "max_loss", "delivered_data_bits", "energy_compute_J", "energy_tx_J",
            "energy_total_J",
        ]
        if self.meta.get("segment_accounting"):
            allocation_fields += ["service_t_end", "service_duration_s"]
        with open(f"{out_dir}/routing_allocations.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=allocation_fields)
            w.writeheader()
            w.writerows(self._allocation_history)

        # 资源事件是逐任务、逐链路/节点的长表，正值表示占用，负值表示释放。
        resource_fields = [
            "event_seq", "t", "event", "allocation_id", "task_id", "biz",
            "resource_type", "resource_id", "node_id", "node_type", "link_u",
            "link_v", "link_type", "path_roles", "allocation_delta", "unit",
            "total_capacity", "remain_before", "remain_after",
        ]
        if self._resource_stream_path is not None:
            if self._resource_stream_path != (Path(out_dir).resolve()/"resource_allocations.csv"):
                raise ValueError("export must use the streaming resource output directory")
            self._resource_stream.flush()
            self._resource_stream.close()
            self._resource_stream = None
        else:
            with open(f"{out_dir}/resource_allocations.csv", "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=resource_fields)
                w.writeheader()
                w.writerows(self._resource_events)

    def print_summary(self):
        s = self.summary()
        print("\n" + "=" * 64)
        print(f"  KPI 摘要  {s['meta']}")
        print("=" * 64)
        print(f"  到达 {s['total_arrived']}  服务中/完成 {s['total_accepted']}  "
              f"拒绝 {s['total_rejected']}  丢弃 {s['total_dropped']}  "
              f"抢占 {s['preemptions']}  重路由 {s['reroutes']}  迁移 {s['migrations']}")
        print(f"  峰值并发: {s['max_concurrency']}")
        print(f"  ★ 总 QoS 达标率: {s['qos_rate_overall']*100:6.2f}%")
        print("  分业务 QoS 达标率 / 接受率:")
        for b in ["LL", "HR", "HC", "MC"]:
            if b in s["qos_rate_by_biz"]:
                print(f"    {b}: QoS {s['qos_rate_by_biz'][b]*100:6.2f}%   "
                      f"Accept {s['accept_rate_by_biz'][b]*100:6.2f}%")
        print(f"  总能耗 {s['total_energy_J']:.3e} J "
              f"(计算 {s['compute_energy_J']:.2e} / 传输 {s['tx_energy_J']:.2e})")
        print(f"  平均每任务能耗 {s['avg_energy_per_task_J']:.3e} J   "
              f"压缩节省 {s['avg_compression_savings']*100:.1f}%")
        print(f"  计算节点分布 {s['compute_node_type_dist']}")
        print(f"  平均 t_compute {s['avg_compute_latency_ms']:.4f} ms "
              f"(占端到端 {s['compute_latency_ratio']*100:.2f}%)")
        print(f"  前/后段平均跳数 {s['avg_pre_hops']:.2f}/{s['avg_post_hops']:.2f}")
        print(f"  求解耗时 P50 {s['solve_ms_p50']:.2f}ms  P99 {s['solve_ms_p99']:.2f}ms")
        print("=" * 64)
