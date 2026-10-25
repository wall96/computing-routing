"""只跑大容量业务 HC + S0(BFS 最近节点,不拆分),输出各时延分量明细,检查参数是否合理。

两部分:
  breakdown  每个 HC 任务在空网络上按 BFS 选点,拆出 前段传播/前段传输/计算/后段传播/后段传输,
             同时检查路径带宽是否够 r0(不够也照样记录时延,便于看量级)
  sim        完整仿真(带宽/算力/功率硬约束),看接受率和拒绝原因
用法: python experiments/run_hc_bfs_delay.py --scenario 1 --out results/hc_bfs_delay
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
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from network import build_snapshot, attach_terminals, terminals_of   # noqa: E402
from simulator import Simulator                                     # noqa: E402
from solvers import base                                            # noqa: E402
from solvers.s0_nearest import bfs                                  # noqa: E402
from energy import compute_latency_ms, compute_ms_per_share         # noqa: E402
from tasks import _haversine_km                                     # noqa: E402


def hc_only(cfg):
    cfg = copy.deepcopy(cfg)
    t = cfg["traffic"]
    t["arrival_rate_per_slot"] = {"HC": t["arrival_rate_per_slot"]["HC"]}
    t.get("fixed_arrivals_per_slot", {}).pop("LL", None)
    cfg["simulation"].update(solver="S0", strategy="latency")
    return cfg


def bfs_pick(snap, task):
    """S0 同口径:按跳数最近的可用算力节点(不检查带宽),返回 (node, pre, post_list)。"""
    dist, prev = bfs(snap, task.src, task, 0.0)
    post = {d: bfs(snap, d, task, 0.0) for d in set(task.dst_set)}
    cands = [n for n in snap.nodes.values()
             if n.type.value in snap.compute_eligible and n.capacity_total > 0 and n.id in dist]
    cands.sort(key=lambda n: (dist[n.id], n.id))
    for n in cands:
        pre = base.reconstruct(prev, task.src, n.id)
        posts = []
        for d in task.dst_set:
            p = base.reconstruct(post[d][1], d, n.id)
            if p is None:
                break
            posts.append(list(reversed(p)))
        if pre and len(posts) == len(task.dst_set):
            return n, pre, posts
    return None


def breakdown_row(snap, task, n, pre, posts):
    load = base.split_edge_bits(task, [(1.0, pre, posts)])
    prop_pre, tx_pre = base.path_delay(snap, pre), base.segment_tx_ms(snap, pre, load)
    comp = compute_latency_ms(n, task)
    j = max(range(len(posts)), key=lambda i: base.path_delay(snap, posts[i])
            + base.segment_tx_ms(snap, posts[i], load))
    prop_post = base.path_delay(snap, posts[j])
    tx_post = base.segment_tx_ms(snap, posts[j], load)
    up = snap.get_link(pre[0], pre[1])
    down = snap.get_link(posts[j][-2], posts[j][-1])
    bw = base.compute_bw_per_link(task, pre, posts)
    short = [k for k, need in bw.items() if snap.get_link(*k).bw_total_mbps < need - 1e-6]
    total = prop_pre + tx_pre + comp + prop_post + tx_post
    return {
        "task_id": task.id, "src_dst_km": round(_haversine_km(task.src_coord, task.dst_coords[0]), 1),
        "rate_Gbps": task.rate_mbps / 1e3, "packet_Mbit": task.packet_bits / 1e6,
        "compute_TFLOPS": task.compute_rate / 1e12, "compute_node": n.id,
        "hops_pre": len(pre) - 1, "hops_post": len(posts[j]) - 1,
        "uplink_Mbps": round(up.bw_total_mbps, 1), "downlink_Mbps": round(down.bw_total_mbps, 1),
        "prop_pre_ms": prop_pre, "tx_pre_ms": tx_pre, "compute_ms": comp,
        "compute_var_ms": compute_ms_per_share(n, task), "fixed_ms": task.fixed_processing_total_ms,
        "prop_post_ms": prop_post, "tx_post_ms": tx_post, "total_ms": total,
        "budget_ms": task.delay_budget_ms, "within_budget": int(total <= task.delay_budget_ms),
        "bw_ok": int(not short), "bw_short_links": len(short),
        "need_up_Mbps": task.rate_mbps, "need_down_Mbps": task.rate_mbps * task.compression_rho,
    }


def stats(rows, key):
    xs = sorted(r[key] for r in rows)
    q = lambda p: xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]
    return {"field": key, "mean": statistics.mean(xs), "p50": q(.5), "p95": q(.95),
            "min": xs[0], "max": xs[-1]}


def compute_match(sim, m):
    """每个时间片:在服 HC 的算力需求 vs 卫星算力(每片全部重路由,按 t_alloc 分组即为该片在服集合)。"""
    cap = {n.id: n.capacity_total for n in sim.all_nodes if n.type.value in ("LEO", "GEO")}
    eligible_cap = sum(c for nid, c in cap.items()
                       if nid.startswith(tuple(sim.cfg["constellation"]["scenario_shells"][sim.scenario])))
    by_t = {}
    for r in m._allocation_history:
        by_t.setdefault(r["t_alloc"], []).append(r)
    rows = []
    for t in sorted(by_t):
        load, cnt = {}, Counter()
        for r in by_t[t]:
            load[r["compute_node"]] = load.get(r["compute_node"], 0.0) + r["compute_rate_used"]
            cnt[r["compute_node"]] += 1
        util = {nid: v / cap[nid] for nid, v in load.items()}
        demand = sum(load.values())
        rows.append({
            "t": t, "active_tasks": len(by_t[t]), "busy_sats": len(load),
            "demand_TFLOPS": demand / 1e12, "capacity_TFLOPS": eligible_cap / 1e12,
            "constellation_util_%": 100 * demand / eligible_cap,
            "mean_busy_sat_util_%": 100 * statistics.mean(util.values()),
            "max_sat_util_%": 100 * max(util.values()),
            "max_tasks_on_one_sat": max(cnt.values()),
            "sats_over_80%": sum(u > 0.8 for u in util.values()),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--scenario", type=int, default=1)
    ap.add_argument("--t-end", type=float, default=3600)
    ap.add_argument("--out", default=str(ROOT / "results" / "hc_bfs_delay"))
    args = ap.parse_args()
    cfg = hc_only(yaml.safe_load(open(args.config, encoding="utf-8")))
    cfg["simulation"]["t_end_sec"] = args.t_end
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    # ---- breakdown:每个时间片的新任务在空网络上按 BFS 选点 ----
    sim = Simulator(cfg, scenario=args.scenario)
    rows, no_path = [], 0
    slot = float(cfg["simulation"]["slot_sec"])
    for i in range(int(args.t_end // slot)):
        t = i * slot
        tasks = sim.gen.gen_for_slot(t, i)
        if not tasks:
            continue
        snap = build_snapshot(t, sim.all_nodes, cfg, args.scenario)
        attach_terminals(snap, terminals_of(tasks), cfg)
        for tk in tasks:
            pick = bfs_pick(snap, tk)
            if pick is None:
                no_path += 1
                continue
            rows.append({"t": t, **breakdown_row(snap, tk, *pick)})
    with open(out / "hc_delay_breakdown.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    keys = ["prop_pre_ms", "tx_pre_ms", "compute_ms", "compute_var_ms", "prop_post_ms",
            "tx_post_ms", "total_ms", "hops_pre", "hops_post", "uplink_Mbps", "downlink_Mbps",
            "rate_Gbps", "packet_Mbit", "compute_TFLOPS", "src_dst_km"]
    summary = [stats(rows, k) for k in keys]
    with open(out / "hc_delay_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0])); w.writeheader(); w.writerows(summary)

    # ---- sim:完整硬约束仿真 ----
    sim2 = Simulator(cfg, scenario=args.scenario)
    with contextlib.redirect_stdout(io.StringIO()):
        m = sim2.run()
    m.export(str(out / "sim"))
    accepted = {r["task_id"] for r in m._allocation_history if r["event_type"] == "INITIAL"}
    cm_rows = compute_match(sim2, m)
    with open(out / "compute_match_by_slot.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(cm_rows[0])); w.writeheader(); w.writerows(cm_rows)
    meta = {
        "scenario": args.scenario, "t_end": args.t_end, "solver": "S0",
        "breakdown_tasks": len(rows), "breakdown_no_path": no_path,
        "breakdown_within_budget_%": 100 * sum(r["within_budget"] for r in rows) / len(rows),
        "breakdown_bw_ok_%": 100 * sum(r["bw_ok"] for r in rows) / len(rows),
        "sim_tasks": len(m.attempts), "sim_accepted": len(accepted),
        "sim_reject_reasons": dict(Counter(r for _, r in m.rejects)),
        "sim_dropped": len(m.dropped_ids),
        "compute_match": {
            "peak_active_tasks": max(r["active_tasks"] for r in cm_rows),
            "peak_demand_TFLOPS": max(r["demand_TFLOPS"] for r in cm_rows),
            "constellation_capacity_TFLOPS": cm_rows[0]["capacity_TFLOPS"],
            "peak_constellation_util_%": max(r["constellation_util_%"] for r in cm_rows),
            "max_sat_util_%": max(r["max_sat_util_%"] for r in cm_rows),
            "max_tasks_on_one_sat": max(r["max_tasks_on_one_sat"] for r in cm_rows),
            "peak_busy_sats": max(r["busy_sats"] for r in cm_rows),
            "sat_slots_over_80%": sum(r["sats_over_80%"] for r in cm_rows),
        },
    }
    json.dump(meta, open(out / "run_meta.json", "w"), indent=2, ensure_ascii=False)
    print(json.dumps(meta, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
