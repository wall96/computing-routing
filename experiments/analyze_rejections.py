"""在同一时间片的满资源快照上反事实复算被拒绝任务，定位拒绝原因。"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml

from models import BizType, Task
from network import build_snapshot
from simulator import Simulator


def task_from_row(row: dict) -> Task:
    return Task(
        id=row["task_id"], src=row["src"], dst_set=json.loads(row["dst_json"]),
        biz=BizType(row["biz"]), priority=int(row["priority"]), mode=row["mode"],
        rate_mbps=float(row["rate_mbps"]), delay_budget_ms=float(row["delay_budget_ms"]),
        loss_budget=float(row["loss_budget"]), cycles_per_bit=float(row["cycles_per_bit"]),
        Lref_bits=float(row["Lref_bits"]), arrival_t=float(row["arrival_t"]),
        duration_s=float(row["duration_s"]), compression_rho=float(row["compression_rho"]),
        processing_logic=row.get("processing_logic", ""),
        cycles_per_bit_per_dst=float(row.get("cycles_per_bit_per_dst") or 0),
        fixed_processing_ms=float(row.get("fixed_processing_ms") or 0),
        fixed_processing_ms_per_dst=float(row.get("fixed_processing_ms_per_dst") or 0),
        cycles_total=float(row["cycles_total"]), data_volume_bits=float(row["data_volume_bits"]),
        compute_rate=float(row["compute_rate_cycles_s"]),
    )


def classify(snapshot, solver, task: Task, strategy: str) -> str:
    adj = snapshot.adj()
    src_isolated = not adj.get(task.src)
    isolated_dst = [dst for dst in task.dst_set if not adj.get(dst)]
    if src_isolated and isolated_dst:
        return "SOURCE_AND_DEST_NO_VISIBLE_SATELLITE"
    if src_isolated:
        return "SOURCE_NO_VISIBLE_SATELLITE"
    if isolated_dst:
        return "DEST_NO_VISIBLE_SATELLITE"

    # 若满资源时可行，则正式运行中的失败来自同片其他任务占用。
    if solver.solve(snapshot, task, strategy) is not None:
        return "RESOURCE_CONTENTION"

    delay_relaxed = replace(task, delay_budget_ms=1.0e12)
    if solver.solve(snapshot, delay_relaxed, strategy) is not None:
        return "DELAY_BUDGET"
    loss_relaxed = replace(task, loss_budget=1.0)
    if solver.solve(snapshot, loss_relaxed, strategy) is not None:
        return "LOSS_BUDGET"
    both_relaxed = replace(task, delay_budget_ms=1.0e12, loss_budget=1.0)
    if solver.solve(snapshot, both_relaxed, strategy) is not None:
        return "DELAY_AND_LOSS"
    return "TOPOLOGY_OR_ALGORITHM_NO_PATH"


def main():
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("result_dir")
    ap.add_argument("--config", default=str(root / "config.yaml"))
    ap.add_argument("--biz", default="LL")
    ap.add_argument("--scenario", type=int, default=1)
    ap.add_argument("--strategy", default="latency")
    ap.add_argument("--solver", default="S1")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    sim = Simulator(cfg, scenario=args.scenario, strategy=args.strategy,
                    solver=args.solver)
    result_dir = Path(args.result_dir)
    with open(result_dir / "generated_tasks.csv", encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["biz"] == args.biz and r["final_status"] == "REJECTED"]

    snapshots = {}
    output = []
    for row in rows:
        slot = int(row["generated_slot"])
        if slot not in snapshots:
            t = slot * float(cfg["simulation"]["slot_sec"])
            snapshots[slot] = build_snapshot(t, sim.all_nodes, cfg, args.scenario)
        task = task_from_row(row)
        reason = classify(snapshots[slot], sim.solver, task, args.strategy)
        output.append({
            "task_id": task.id, "biz": task.biz.value, "generated_slot": slot,
            "t": snapshots[slot].t, "src": task.src,
            "dst_json": json.dumps(task.dst_set, ensure_ascii=False),
            "rate_mbps": task.rate_mbps, "delay_budget_ms": task.delay_budget_ms,
            "diagnosed_reason": reason,
        })

    fields = list(output[0]) if output else ["task_id", "diagnosed_reason"]
    with open(result_dir / "rejection_diagnostics.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    counts = Counter(row["diagnosed_reason"] for row in output)
    summary = {"biz": args.biz, "rejected": len(output), "reason_counts": dict(counts),
               "reason_rates": {k: v / len(output) for k, v in counts.items()} if output else {}}
    with open(result_dir / "rejection_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
