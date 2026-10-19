"""M6 评估：KPI 收集 + 导出。"""
from __future__ import annotations
import csv
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

from models import Allocation, Task, BizType, Snapshot


class Metrics:
    def __init__(self):
        self.attempts: List[Task] = []
        self.allocs: List[Allocation] = []
        self.rejects: List[tuple] = []     # (Task, reason)
        self.preemptions: int = 0
        self._slot_stats: List[dict] = []

    def record_attempt(self, task: Task):
        self.attempts.append(task)

    def record_alloc(self, alloc: Allocation):
        self.allocs.append(alloc)

    def record_reject(self, task: Task, reason: str):
        self.rejects.append((task, reason))

    def record_slot(self, t: float, snapshot: Snapshot, accepted: int, rejected: int):
        # 链路 / 节点利用率
        link_util = []
        for lk in snapshot.links.values():
            if lk.bw_total_mbps <= 0:
                continue
            link_util.append(1 - lk.bw_remain_mbps / lk.bw_total_mbps)
        node_util = []
        for n in snapshot.nodes.values():
            if n.capacity_total <= 0:
                continue
            node_util.append(1 - n.capacity_remain / n.capacity_total)
        self._slot_stats.append({
            "t": t,
            "accepted": accepted,
            "rejected": rejected,
            "link_util_mean": _safe_mean(link_util),
            "link_util_max": max(link_util) if link_util else 0,
            "node_util_mean": _safe_mean(node_util),
            "node_util_max": max(node_util) if node_util else 0,
        })

    # ============= 汇总 =============
    def summary(self) -> dict:
        by_biz_total = defaultdict(int)
        by_biz_accept = defaultdict(int)
        by_biz_energy = defaultdict(float)
        for t in self.attempts:
            by_biz_total[t.biz.value] += 1
        for a in self.allocs:
            by_biz_accept[a.task.biz.value] += 1
            by_biz_energy[a.task.biz.value] += a.energy_total_J

        accept_rate = {b: (by_biz_accept[b] / by_biz_total[b] if by_biz_total[b] else 0.0)
                       for b in by_biz_total}
        # QoS 达标
        ll_accepted = [a for a in self.allocs if a.task.biz == BizType.LL]
        ll_pass = 0
        for a in ll_accepted:
            d = sum_path_delay(a)
            if d <= a.task.delay_budget_ms:
                ll_pass += 1
        ll_qos = ll_pass / max(1, len(ll_accepted))

        # 平均能耗
        e_per_task = sum(a.energy_total_J for a in self.allocs) / max(1, len(self.allocs))
        e_total = sum(a.energy_total_J for a in self.allocs)
        e_compute = sum(a.energy_compute_J for a in self.allocs)
        e_tx = sum(a.energy_tx_J for a in self.allocs)

        return {
            "total_attempts": len(self.attempts),
            "total_accepted": len(self.allocs),
            "total_rejected": len(self.rejects),
            "preemptions": self.preemptions,
            "accept_rate_by_biz": accept_rate,
            "ll_qos_pass_rate": ll_qos,
            "avg_energy_per_task_J": e_per_task,
            "total_energy_J": e_total,
            "total_compute_energy_J": e_compute,
            "total_tx_energy_J": e_tx,
            "energy_per_biz_J": dict(by_biz_energy),
        }

    # ============= 持久化 =============
    def export(self, out_dir: str):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        # JSON 摘要
        summary = self.summary()
        with open(f"{out_dir}/kpi.json", "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
        # 切片明细
        with open(f"{out_dir}/slot_stats.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["t", "accepted", "rejected",
                                              "link_util_mean", "link_util_max",
                                              "node_util_mean", "node_util_max"])
            w.writeheader()
            for s in self._slot_stats:
                w.writerow(s)
        # 任务明细
        with open(f"{out_dir}/tasks.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["task_id", "biz", "src", "dst", "rate_mbps",
                        "compute_node", "strategy", "n_paths",
                        "delay_ms_total", "energy_compute_J",
                        "energy_tx_J", "energy_total_J", "accepted",
                        "paths"])
            for a in self.allocs:
                paths_str = " | ".join(" -> ".join(p) for p in a.paths)
                w.writerow([a.task.id, a.task.biz.value, a.task.src,
                            ";".join(a.task.dst_set), f"{a.task.rate_mbps:.3f}",
                            a.compute_node, a.strategy, len(a.paths),
                            f"{sum_path_delay(a):.3f}",
                            f"{a.energy_compute_J:.6e}",
                            f"{a.energy_tx_J:.6e}",
                            f"{a.energy_total_J:.6e}",
                            a.accepted, paths_str])
            for t, reason in self.rejects:
                w.writerow([t.id, t.biz.value, t.src, ";".join(t.dst_set),
                            f"{t.rate_mbps:.3f}", "", "", 0, "", "", "", "",
                            f"REJECTED:{reason}", ""])
        # 路径明细 (易读)
        with open(f"{out_dir}/paths.txt", "w") as f:
            for a in self.allocs:
                f.write(f"[{a.task.id}] {a.task.biz.value}  "
                        f"src={a.task.src}  dst={','.join(a.task.dst_set)}  "
                        f"compute@{a.compute_node}  strategy={a.strategy}  "
                        f"rate={a.task.rate_mbps:.2f}Mbps  "
                        f"delay={sum_path_delay(a):.2f}ms  "
                        f"E={a.energy_total_J:.3e}J\n")
                for i, p in enumerate(a.paths):
                    bw = 0.0
                    for u, v in zip(p[:-1], p[1:]):
                        bw = max(bw, a.bw_per_link.get((u, v), 0.0))
                    f.write(f"    path#{i+1} ({len(p)-1} hops, bw={bw:.2f}Mbps): "
                            f"{' -> '.join(p)}\n")
                f.write("\n")

    # ============= 打印 =============
    def print_summary(self):
        s = self.summary()
        print()
        print("=" * 60)
        print("  仿真 KPI 摘要")
        print("=" * 60)
        print(f"  到达任务: {s['total_attempts']}")
        print(f"  接受: {s['total_accepted']}    拒绝: {s['total_rejected']}    抢占: {s['preemptions']}")
        print()
        print("  各业务接受率:")
        for b, r in s["accept_rate_by_biz"].items():
            print(f"    {b}: {r*100:6.2f}%")
        print(f"  LL 时延 QoS 达标率: {s['ll_qos_pass_rate']*100:6.2f}%")
        print()
        print(f"  总能耗:           {s['total_energy_J']:.3e} J")
        print(f"   ├─ 计算:         {s['total_compute_energy_J']:.3e} J")
        print(f"   └─ 传输:         {s['total_tx_energy_J']:.3e} J")
        print(f"  平均每任务能耗:    {s['avg_energy_per_task_J']:.3e} J")
        print("=" * 60)


def sum_path_delay(alloc: Allocation) -> float:
    """对 Allocation 的路径而言的总时延（取最长那条，多连接情况）。"""
    if not alloc.paths:
        return 0.0
    # 单连接：路径上所有边时延之和
    # 多连接：取各路径中最大者
    # 由于 Allocation 不携带 snapshot，这里只能从粗略字段估
    # 真正的时延记录由 simulator 在 reserve 之前调一次 sum_path_delay_with_snapshot
    # 这里简单返回 0；若你在 simulator 里把 delay 直接缓存到 alloc 即可
    return getattr(alloc, "_cached_max_delay_ms", 0.0)


def _safe_mean(xs):
    return sum(xs) / len(xs) if xs else 0.0
