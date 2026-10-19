"""四类单业务、整时间片持续时长的对照实验。

每轮只启用 LL/HR/HC/MC 中的一类，保留该类在 config.yaml 中的基础到达率和
高峰包络；任务在时间片起点到达，duration 固定等于 slot_sec。
"""
from __future__ import annotations

import argparse
import builtins
import csv
import json
from collections import Counter
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml

from simulator import Simulator


BIZ_TYPES = ("LL", "HR", "HC", "MC")


def load_cfg(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_silently(sim: Simulator):
    original_print = builtins.print
    builtins.print = lambda *args, **kwargs: None
    try:
        return sim.run()
    finally:
        builtins.print = original_print


def main():
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="单业务、整时间片时长四轮对照实验")
    ap.add_argument("--config", default=str(root / "config.yaml"))
    ap.add_argument("--out", default=str(root / "results" / "single_biz"))
    ap.add_argument("--scenario", type=int, choices=[1, 2, 3], default=None)
    ap.add_argument("--strategy", choices=["latency", "balance", "energy"], default=None)
    ap.add_argument("--solver", choices=["S0", "S1", "S2", "S3", "S4", "S5"], default=None)
    ap.add_argument("--t-end", type=float, default=None)
    ap.add_argument("--slot", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--businesses", default=",".join(BIZ_TYPES),
                    help="要运行的业务列表，默认 LL,HR,HC,MC")
    ap.add_argument("--ll-dst-knn", type=int, default=None,
                    help="覆盖 LL 的近邻目的地候选数，例如 1 表示只投递到最近站点")
    ap.add_argument("--stress-concurrency", action="store_true",
                    help="fixed concurrency stress mode with a configurable peak slot")
    ap.add_argument("--steady-ratio", type=float, default=0.60,
                    help="normal-slot arrivals divided by the scenario cap")
    ap.add_argument("--stress-slots", type=int, default=10,
                    help="number of slots in stress mode")
    ap.add_argument("--peak-slot", type=int, default=None,
                    help="slot that injects exactly the scenario cap")
    ap.add_argument("--peak-offered-ratio", type=float, default=1.0,
                    help="peak offered load divided by the scenario cap")
    args = ap.parse_args()

    base = load_cfg(args.config)
    if args.t_end is not None:
        base["simulation"]["t_end_sec"] = args.t_end
    if args.slot is not None:
        base["simulation"]["slot_sec"] = args.slot
    if args.seed is not None:
        base["simulation"]["random_seed"] = args.seed

    scenario = args.scenario or int(base["simulation"]["scenario"])
    strategy = args.strategy or base["simulation"]["strategy"]
    solver = args.solver or base["simulation"]["solver"]
    slot = float(base["simulation"]["slot_sec"])
    scenario_cap = int(base["constellation"].get(
        "scenario_max_concurrency", {}).get(scenario, 0))
    peak_slot = args.peak_slot if args.peak_slot is not None else args.stress_slots // 2
    if args.stress_concurrency:
        if scenario_cap <= 0:
            ap.error("stress mode requires scenario_max_concurrency")
        if not 0.5 <= args.steady_ratio <= 1.0:
            ap.error("--steady-ratio must be within [0.5, 1.0]")
        if args.stress_slots < 2:
            ap.error("--stress-slots must be at least 2")
        if not 0 <= peak_slot < args.stress_slots:
            ap.error("--peak-slot is outside the stress run")
        if args.peak_offered_ratio < 1.0:
            ap.error("--peak-offered-ratio must be at least 1.0")
        base["simulation"]["t_end_sec"] = (
            float(base["simulation"].get("t_start_sec", 0.0))
            + args.stress_slots * slot
        )
    original_rates = dict(base["traffic"]["arrival_rate_per_slot"])
    businesses = [b.strip().upper() for b in args.businesses.split(",") if b.strip()]
    unknown = [b for b in businesses if b not in BIZ_TYPES]
    if unknown:
        ap.error(f"未知业务 {unknown}; 可选 {BIZ_TYPES}")
    if not businesses:
        ap.error("--businesses 不能为空")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []

    for biz in businesses:
        # 独立加载，避免上一轮的修改泄漏到下一轮。
        cfg = load_cfg(args.config)
        cfg["simulation"].update(base["simulation"])
        cfg["traffic"]["arrival_rate_per_slot"] = {
            b: original_rates.get(b, 0) if b == biz else 0 for b in BIZ_TYPES
        }
        cfg["traffic"]["qos"][biz]["mode"] = "persist"
        cfg["traffic"]["qos"][biz]["duration_s"] = [slot, slot]
        if args.stress_concurrency:
            steady_count = int(math.ceil(scenario_cap * args.steady_ratio))
            peak_count = int(math.ceil(scenario_cap * args.peak_offered_ratio))
            cfg["traffic"]["fixed_arrivals_per_slot"] = {biz: steady_count}
            cfg["traffic"]["fixed_arrival_overrides"] = {
                peak_slot: {biz: peak_count}
            }
        if args.ll_dst_knn is not None:
            cfg["traffic"]["qos"]["LL"]["dst_knn"] = args.ll_dst_knn

        print(f"运行单业务 {biz}: duration={slot:g}s ...", flush=True)
        sim = Simulator(cfg, scenario=scenario, strategy=strategy, solver=solver)
        metrics = run_silently(sim)
        run_out = out / biz
        metrics.meta["algorithm_diagnostics"] = getattr(sim.solver, "diagnostics", {})
        metrics.export(str(run_out))
        effective_cfg = json.loads(json.dumps(cfg))
        effective_cfg["simulation"].update(scenario=scenario, strategy=strategy, solver=solver)
        with open(run_out / "effective_config.json", "w", encoding="utf-8") as f:
            json.dump(effective_cfg, f, ensure_ascii=False, indent=2)
        s = metrics.summary()
        arrived = s["total_arrived"]
        ever_allocated = len(metrics.alloc_by_task)
        slot_count = len(metrics._slot_stats)
        generated_by_slot = Counter(
            metrics._task_context[t.id].get("generated_slot") for t in metrics.attempts
        )
        generated_counts = [generated_by_slot.get(i, 0) for i in range(slot_count)]
        active_counts = [int(st["active"]) for st in metrics._slot_stats]
        avg_generated = sum(generated_counts) / max(1, slot_count)
        max_generated = max(generated_counts, default=0)
        avg_active = sum(active_counts) / max(1, slot_count)
        row = {
            "biz_only": biz,
            "scenario": scenario,
            "strategy": strategy,
            "solver": solver,
            "slot_sec": slot,
            "duration_sec": slot,
            "base_arrival_per_slot": original_rates.get(biz, 0),
            "stress_mode": args.stress_concurrency,
            "scenario_concurrency_cap": scenario_cap,
            "peak_offered_ratio": args.peak_offered_ratio,
            "avg_generated_concurrency": avg_generated,
            "max_generated_concurrency": max_generated,
            "avg_generated_cap_ratio": avg_generated / scenario_cap if scenario_cap else 0.0,
            "arrived": arrived,
            "ever_allocated": ever_allocated,
            "initial_accept_rate": ever_allocated / arrived if arrived else 0.0,
            "final_accepted": s["total_accepted"],
            "final_qos_rate": s["qos_rate_overall"],
            "rejected": s["total_rejected"],
            "dropped": s["total_dropped"],
            "preemptions": s["preemptions"],
            "reroutes": s["reroutes"],
            "migrations": s["migrations"],
            "max_concurrency": s["max_concurrency"],
            "avg_active_concurrency": avg_active,
            "avg_active_cap_ratio": avg_active / scenario_cap if scenario_cap else 0.0,
            "total_energy_J": s["total_energy_J"],
            "avg_energy_per_task_J": s["avg_energy_per_task_J"],
            "avg_pre_hops": s["avg_pre_hops"],
            "avg_post_hops": s["avg_post_hops"],
            "solve_p99_ms": s["solve_ms_p99"],
        }
        rows.append(row)
        print(f"  arrived={arrived} allocated={ever_allocated} "
              f"qos={s['qos_rate_overall']:.2%} dropped={s['total_dropped']}")

    fields = list(rows[0])
    with open(out / "compare.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(out / "compare.md", "w", encoding="utf-8") as f:
        f.write("# 单业务整时间片对照实验\n\n")
        f.write("| " + " | ".join(fields) + " |\n")
        f.write("|" + "|".join(["---"] * len(fields)) + "|\n")
        for row in rows:
            f.write("| " + " | ".join(str(row[k]) for k in fields) + " |\n")
    print(f"对比结果已保存至 {out}")


if __name__ == "__main__":
    main()
