"""拆分卸载对比:S1 / 单节点最优(S6, max_splits=1) / S6 注水法 / S7 分段LP。

两组实验(包模型,latency 策略):
  isolated  每个任务在空网络上单独求解,只看算法本身(时延、拆分数、求解耗时)
  sim       完整时间片仿真 t_end 秒,任务之间争用资源(接受率、时延)
用法: python experiments/run_split_compare.py --out results/split_compare [--t-end 600] [--slots 6]
"""
from __future__ import annotations
import argparse
import contextlib
import copy
import csv
import io
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from network import build_snapshot, attach_terminals, terminals_of   # noqa: E402
from simulator import Simulator                                     # noqa: E402
from solvers import make_solver                                     # noqa: E402

VARIANTS = [  # (标签, solver, max_splits)
    ("S1", "S1", 0),
    ("单节点最优", "S6", 1),
    ("S6注水", "S6", 0),
    ("S7分段LP", "S7", 0),
]
BIZ = ("LL", "HR", "HC", "MC")


def variant_cfg(base, max_splits):
    cfg = copy.deepcopy(base)
    cfg["simulation"]["strategy"] = "latency"
    cfg["algorithm"].setdefault("split", {})["max_splits"] = max_splits
    return cfg


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def isolated(base, scenario, slots):
    sim = Simulator(variant_cfg(base, 0), scenario=scenario)
    solvers = {lab: make_solver(name, variant_cfg(base, ms)) for lab, name, ms in VARIANTS}
    rows = []
    for i in range(slots):
        t = i * 300.0
        snap = build_snapshot(t, sim.all_nodes, sim.cfg, scenario)
        tasks = sim.gen.gen_for_slot(t, 20)         # 取高峰倍率的片,任务更多
        attach_terminals(snap, terminals_of(tasks), sim.cfg)
        for tk in tasks:
            row = {"scenario": scenario, "t": t, "task_id": tk.id, "biz": tk.biz.value,
                   "packet_Mbit": tk.packet_bits / 1e6, "n_dst": len(tk.dst_set),
                   "budget_ms": tk.delay_budget_ms}
            for lab, solver in solvers.items():
                for n in snap.nodes.values():
                    n.reset_resources()
                for lk in snap.links.values():
                    lk.reset_bw()
                t0 = time.perf_counter()
                a = solver.solve(snap, tk, "latency")
                row[f"{lab}|ms"] = (time.perf_counter() - t0) * 1000
                row[f"{lab}|delay"] = a.max_delay_ms if a else ""
                row[f"{lab}|splits"] = (max(1, len(a.splits)) if a else "")
            rows.append(row)
    return rows


def summarize_isolated(rows):
    out = []
    for biz in BIZ + ("ALL",):
        sub = [r for r in rows if biz == "ALL" or r["biz"] == biz]
        if not sub:
            continue
        for lab, _, _ in VARIANTS:
            ok = [r for r in sub if r[f"{lab}|delay"] != ""]
            d = [r[f"{lab}|delay"] for r in ok]
            sp = [r[f"{lab}|splits"] for r in ok]
            base_ok = [r for r in ok if r["单节点最优|delay"] != ""]
            gain = [1 - r[f"{lab}|delay"] / r["单节点最优|delay"] for r in base_ok]
            out.append({
                "biz": biz, "variant": lab, "tasks": len(sub),
                "feasible_%": 100 * len(ok) / len(sub),
                "delay_mean_ms": statistics.mean(d) if d else float("nan"),
                "delay_p95_ms": pct(d, 95),
                "vs_single_mean_%": 100 * statistics.mean(gain) if gain else float("nan"),
                "split_tasks_%": 100 * sum(s > 1 for s in sp) / len(sp) if sp else float("nan"),
                "splits_mean": statistics.mean(sp) if sp else float("nan"),
                "splits_max": max(sp) if sp else 0,
                "solve_ms_mean": statistics.mean(r[f"{lab}|ms"] for r in sub),
            })
    return out


def full_sim(base, scenario, t_end):
    out = []
    for lab, name, ms in VARIANTS:
        cfg = variant_cfg(base, ms)
        cfg["simulation"]["t_end_sec"] = t_end
        sim = Simulator(cfg, scenario=scenario, solver=name)
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            m = sim.run()
        wall = time.perf_counter() - t0
        init = [r for r in m._allocation_history if r["event_type"] == "INITIAL"]
        by_biz = defaultdict(list)
        for r in init:
            by_biz[r["biz"]].append(r)
        n_by_biz = defaultdict(int)
        for t in m.attempts:
            n_by_biz[t.biz.value] += 1
        for biz in BIZ + ("ALL",):
            rs = init if biz == "ALL" else by_biz[biz]
            n = len(m.attempts) if biz == "ALL" else n_by_biz[biz]
            d = [r["max_delay_ms"] for r in rs]
            sp = [r["n_splits"] for r in rs]
            out.append({
                "scenario": scenario, "variant": lab, "biz": biz, "tasks": n,
                "accept_%": 100 * len(rs) / n if n else float("nan"),
                "delay_mean_ms": statistics.mean(d) if d else float("nan"),
                "delay_p95_ms": pct(d, 95),
                "split_tasks_%": 100 * sum(s > 1 for s in sp) / len(sp) if sp else float("nan"),
                "splits_mean": statistics.mean(sp) if sp else float("nan"),
                "solve_ms_mean": statistics.mean(m.solve_times_ms) if m.solve_times_ms else 0,
                "wall_s": wall,
            })
    return out


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--out", default=str(ROOT / "results" / "split_compare"))
    ap.add_argument("--scenarios", default="1,2,3")
    ap.add_argument("--slots", type=int, default=6, help="isolated 实验取的快照数(每 300 s 一个)")
    ap.add_argument("--t-end", type=float, default=600)
    ap.add_argument("--skip-sim", action="store_true")
    args = ap.parse_args()
    base = yaml.safe_load(open(args.config, encoding="utf-8"))
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    scenarios = [int(s) for s in args.scenarios.split(",")]

    iso_rows, iso_sum, sim_rows = [], [], []
    for sc in scenarios:
        print(f"[isolated] scenario {sc} ...", flush=True)
        rows = isolated(base, sc, args.slots)
        iso_rows += rows
        iso_sum += [{"scenario": sc, **r} for r in summarize_isolated(rows)]
        if not args.skip_sim:
            print(f"[sim] scenario {sc} t_end={args.t_end:g} ...", flush=True)
            sim_rows += full_sim(base, sc, args.t_end)
    write_csv(out / "isolated_tasks.csv", iso_rows)
    write_csv(out / "isolated_summary.csv", iso_sum)
    write_csv(out / "sim_summary.csv", sim_rows)
    json.dump({"config": args.config, "scenarios": scenarios, "slots": args.slots,
               "t_end": args.t_end}, open(out / "run_meta.json", "w"), indent=2)
    print(f"结果写入 {out}/")


if __name__ == "__main__":
    main()
