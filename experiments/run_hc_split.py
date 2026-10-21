"""只跑大容量业务 HC:对比 S0 BFS / 单节点最优 / S6 注水法 / S7 分段LP(星上拆分)。

  isolated  每个任务在空网络上单独求解:时延分量(取最晚到达的子包)、拆分数、拆分收益、求解耗时
  sim       完整硬约束仿真:接受率、时延、拆分比例、迁移、算力占用
  --omega   可选:另跑一组更高的每 bit 计算量(isolated),看算力更重时拆分收益如何变化
用法: python experiments/run_hc_split.py --scenario 1 --t-end 3600 --out results/hc_split
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
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))
from run_hc_bfs_delay import hc_only                                 # noqa: E402
from network import build_snapshot, attach_terminals, terminals_of   # noqa: E402
from simulator import Simulator                                     # noqa: E402
from solvers import make_solver, base                               # noqa: E402
from energy import compute_latency_ms                               # noqa: E402

VARIANTS = [("S0_BFS", "S0", 0), ("单节点最优", "S6", 1), ("S6注水", "S6", 0), ("S7分段LP", "S7", 0)]
COMPONENTS = ["uplink_prop_ms", "uplink_tx_ms", "split_overhead_ms", "isl_prop_ms", "isl_tx_ms",
              "compute_ms", "down_prop_ms", "down_tx_ms"]


def variant_cfg(base_cfg, solver, max_splits):
    cfg = copy.deepcopy(base_cfg)
    cfg["simulation"].update(solver=solver, strategy="latency")
    cfg["algorithm"].setdefault("split", {})["max_splits"] = max_splits
    return cfg


def components(snap, task, alloc, coef):
    """取最晚到达的子包,拆出各时延分量。不拆分时 uplink=终端到算力节点整段前段,isl 为 0。"""
    if not alloc.splits:
        parts = [(snap.nodes[alloc.compute_node], 1.0, alloc.path_pre, alloc.path_post)]
    else:
        parts = [(snap.nodes[s.node], s.share, s.path_pre, s.path_post) for s in alloc.splits]
    load = base.split_edge_bits(task, [(x, pre, posts) for _, x, pre, posts in parts])
    split = len(parts) > 1
    best, best_t = None, -1.0
    for node, x, pre, posts in parts:
        # 统一口径:上行 = 终端→接入卫星第一跳;星间 = 接入卫星→算力节点
        up, isl = pre[:2], pre[1:]
        if split:
            up_tx, isl_tx = base.segment_tx_ms(snap, up, load), base.segment_tx_ms(snap, isl, load)
        else:
            # 不拆分为直通转发:整段前段只有一个瓶颈传输时延,记在瓶颈所在的一段
            tx = base.segment_tx_ms(snap, pre, load)
            up_tx = tx if base.segment_tx_ms(snap, up, load) >= tx - 1e-12 else 0.0
            isl_tx = tx - up_tx
        c = {"uplink_prop_ms": base.path_delay(snap, up), "uplink_tx_ms": up_tx,
             "split_overhead_ms": base.split_overhead_ms(task, coef) if split else 0.0,
             "isl_prop_ms": base.path_delay(snap, isl), "isl_tx_ms": isl_tx,
             "compute_ms": compute_latency_ms(node, task, x)}
        j = max(range(len(posts)), key=lambda i: base.path_delay(snap, posts[i])
                + base.segment_tx_ms(snap, posts[i], load))
        c["down_prop_ms"] = base.path_delay(snap, posts[j])
        c["down_tx_ms"] = base.segment_tx_ms(snap, posts[j], load)
        t = sum(c.values())
        if t > best_t:
            best, best_t = c, t
    best["total_ms"] = best_t
    return best


def isolated(base_cfg, scenario, t_end):
    coef = float(base_cfg["algorithm"].get("split", {}).get("overhead_s_per_Tbit", 0.01))
    sim = Simulator(variant_cfg(base_cfg, "S0", 0), scenario=scenario)
    solvers = {lab: make_solver(name, variant_cfg(base_cfg, name, ms)) for lab, name, ms in VARIANTS}
    rows, slot = [], float(base_cfg["simulation"]["slot_sec"])
    for i in range(int(t_end // slot)):
        t = i * slot
        tasks = sim.gen.gen_for_slot(t, i)
        if not tasks:
            continue
        snap = build_snapshot(t, sim.all_nodes, sim.cfg, scenario)
        attach_terminals(snap, terminals_of(tasks), sim.cfg)
        for tk in tasks:
            row = {"t": t, "task_id": tk.id, "rate_Gbps": tk.rate_mbps / 1e3,
                   "packet_Mbit": tk.packet_bits / 1e6, "compute_TFLOPS": tk.compute_rate / 1e12}
            for lab, solver in solvers.items():
                for n in snap.nodes.values():
                    n.reset_resources()
                for lk in snap.links.values():
                    lk.reset_bw()
                t0 = time.perf_counter()
                a = solver.solve(snap, tk, "latency")
                row[f"{lab}|solve_ms"] = (time.perf_counter() - t0) * 1000
                if a is None:
                    row[f"{lab}|total_ms"] = ""
                    continue
                row[f"{lab}|splits"] = max(1, len(a.splits))
                row[f"{lab}|shares"] = json.dumps([round(s.share, 4) for s in a.splits])
                for k, v in components(snap, tk, a, coef).items():
                    row[f"{lab}|{k}"] = v
            rows.append(row)
    return rows


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]


def summarize_isolated(rows):
    out = []
    for lab, _, _ in VARIANTS:
        ok = [r for r in rows if r.get(f"{lab}|total_ms", "") != ""]
        if not ok:
            out.append({"variant": lab, "tasks": len(rows), "feasible": 0})
            continue
        tot = [r[f"{lab}|total_ms"] for r in ok]
        sp = [r[f"{lab}|splits"] for r in ok]
        both = [r for r in ok if r.get("单节点最优|total_ms", "") != ""]
        gain = [1 - r[f"{lab}|total_ms"] / r["单节点最优|total_ms"] for r in both]
        split_rows = [r for r in both if r[f"{lab}|splits"] > 1]
        rescued = [r for r in ok if r.get("单节点最优|total_ms", "") == ""]   # 单节点不可行、本算法可行
        rec = {"variant": lab, "tasks": len(rows), "feasible": len(ok),
               "rescued_vs_single": len(rescued),
               "total_mean_ms": statistics.mean(tot), "total_p50_ms": q(tot, .5),
               "total_p95_ms": q(tot, .95), "total_max_ms": max(tot),
               "vs_single_mean_%": 100 * statistics.mean(gain) if gain else float("nan"),
               "split_tasks": len(split_rows), "split_tasks_%": 100 * len(split_rows) / len(ok),
               "split_gain_over_0.1ms": sum(
                   r["单节点最优|total_ms"] - r[f"{lab}|total_ms"] > 0.1 for r in split_rows),
               "split_gain_over_1ms": sum(
                   r["单节点最优|total_ms"] - r[f"{lab}|total_ms"] > 1.0 for r in split_rows),
               "gain_when_split_mean_ms": statistics.mean(
                   r["单节点最优|total_ms"] - r[f"{lab}|total_ms"] for r in split_rows) if split_rows else 0.0,
               "splits_mean": statistics.mean(sp), "splits_max": max(sp),
               "splits_dist": json.dumps(dict(sorted(Counter(sp).items()))),
               "solve_ms_mean": statistics.mean(r[f"{lab}|solve_ms"] for r in rows)}
        for k in COMPONENTS:
            rec[f"{k}_mean"] = statistics.mean(r[f"{lab}|{k}"] for r in ok)
        out.append(rec)
    return out


def compute_util(sim, m):
    """每片各卫星算力占用(拆分时按子包份额分摊到各节点)。"""
    cap = {n.id: n.capacity_total for n in sim.all_nodes if n.capacity_total > 0}
    by_t = defaultdict(lambda: defaultdict(float))
    for r in m._allocation_history:
        splits = json.loads(r["splits_json"]) if r.get("splits_json") else []
        if splits:
            for s in splits:
                by_t[r["t_alloc"]][s["node"]] += r["compute_rate_used"] * s["share"]
        else:
            by_t[r["t_alloc"]][r["compute_node"]] += r["compute_rate_used"]
    peak_sat = max(max(v / cap[n] for n, v in load.items()) for load in by_t.values())
    peak_busy = max(len(load) for load in by_t.values())
    return 100 * peak_sat, peak_busy


def full_sim(base_cfg, scenario, t_end):
    out = []
    for lab, name, ms in VARIANTS:
        cfg = variant_cfg(base_cfg, name, ms)
        cfg["simulation"]["t_end_sec"] = t_end
        sim = Simulator(cfg, scenario=scenario)
        t0 = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            m = sim.run()
        hist = m._allocation_history
        init = [r for r in hist if r["event_type"] == "INITIAL"]
        worst = defaultdict(float)
        for r in hist:
            worst[r["task_id"]] = max(worst[r["task_id"]], r["max_delay_ms"])
        d_init = [r["max_delay_ms"] for r in init]
        d_all = [r["max_delay_ms"] for r in hist]
        sp = [r["n_splits"] for r in hist]
        peak_sat, peak_busy = compute_util(sim, m)
        out.append({
            "variant": lab, "tasks": len(m.attempts), "accepted": len(init),
            "rejected": len(m.rejects), "dropped": len(m.dropped_ids),
            "init_delay_mean_ms": statistics.mean(d_init), "init_delay_p95_ms": q(d_init, .95),
            "all_delay_mean_ms": statistics.mean(d_all), "all_delay_p95_ms": q(d_all, .95),
            "all_delay_max_ms": max(d_all),
            "task_worst_delay_mean_ms": statistics.mean(worst.values()),
            "split_alloc_%": 100 * sum(s > 1 for s in sp) / len(sp),
            "splits_mean": statistics.mean(sp), "splits_max": max(sp),
            "migrations": m.migrations, "peak_concurrency": m.max_concurrency,
            "peak_sat_util_%": peak_sat, "peak_busy_sats": peak_busy,
            "solve_ms_mean": statistics.mean(m.solve_times_ms),
            "solve_ms_p95": q(m.solve_times_ms, .95), "wall_s": time.perf_counter() - t0,
        })
        m.export(str(Path(OUT) / "sim" / lab))
    return out


def write_csv(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))     # 不可行的任务缺少部分列
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)


OUT = None


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--scenario", type=int, default=1)
    ap.add_argument("--t-end", type=float, default=3600)
    ap.add_argument("--out", default=str(ROOT / "results" / "hc_split"))
    ap.add_argument("--omega", type=float, default=None, help="额外跑一组 isolated,HC 每 bit 计算量取该值")
    ap.add_argument("--skip-sim", action="store_true")
    args = ap.parse_args()
    OUT = args.out
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    base_cfg = hc_only(yaml.safe_load(open(args.config, encoding="utf-8")))

    rows = isolated(base_cfg, args.scenario, args.t_end)
    write_csv(out / "isolated_tasks.csv", rows)
    summ = summarize_isolated(rows)
    write_csv(out / "isolated_summary.csv", summ)
    if args.omega:
        cfg2 = copy.deepcopy(base_cfg)
        cfg2["traffic"]["qos"]["HC"]["cycles_per_bit_base"] = args.omega
        rows2 = isolated(cfg2, args.scenario, args.t_end)
        write_csv(out / f"isolated_tasks_omega{int(args.omega)}.csv", rows2)
        write_csv(out / f"isolated_summary_omega{int(args.omega)}.csv", summarize_isolated(rows2))
    if not args.skip_sim:
        write_csv(out / "sim_summary.csv", full_sim(base_cfg, args.scenario, args.t_end))
    json.dump({"scenario": args.scenario, "t_end": args.t_end, "omega_extra": args.omega,
               "split": base_cfg["algorithm"].get("split", {})},
              open(out / "run_meta.json", "w"), indent=2, ensure_ascii=False)
    print(f"结果写入 {out}/")


if __name__ == "__main__":
    main()
