"""批量实验:场景×策略×算法 横向对比 + 消融(《设计文档》§11.3/§11.4)。

用法(在 code/ 下):
  python experiments/run_matrix.py --quick        # 短时冒烟对比
  python experiments/run_matrix.py                 # 主对比
"""
from __future__ import annotations
import argparse
import copy
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml
from simulator import Simulator


def load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_one(cfg, scenario, strategy, solver, t_end, tag="", detail_out=None):
    cfg = copy.deepcopy(cfg)
    cfg["simulation"]["t_end_sec"] = t_end
    cfg["simulation"]["log_level"] = "WARNING"
    sim = Simulator(cfg, scenario=scenario, strategy=strategy, solver=solver)
    # 静默逐片打印
    import builtins
    orig = builtins.print
    builtins.print = lambda *a, **k: None
    try:
        m = sim.run()
    finally:
        builtins.print = orig
    if detail_out is not None:
        m.export(str(detail_out))
    s = m.summary()
    row = {
        "tag": tag, "scenario": scenario, "strategy": strategy, "solver": solver,
        "arrived": s["total_arrived"], "accepted": s["total_accepted"],
        "dropped": s["total_dropped"], "max_conc": s["max_concurrency"],
        "reroutes": s["reroutes"], "migrations": s["migrations"],
        "qos_overall": round(s["qos_rate_overall"], 4),
        "qos_LL": round(s["qos_rate_by_biz"].get("LL", 0), 4),
        "qos_HR": round(s["qos_rate_by_biz"].get("HR", 0), 4),
        "qos_HC": round(s["qos_rate_by_biz"].get("HC", 0), 4),
        "qos_MC": round(s["qos_rate_by_biz"].get("MC", 0), 4),
        "energy_J": round(s["total_energy_J"], 3),
        "avg_E_per_task_J": round(s["avg_energy_per_task_J"], 4),
        "compress_save": round(s["avg_compression_savings"], 3),
        "node_dist": s["compute_node_type_dist"],
        "solve_p50_ms": round(s["solve_ms_p50"], 3),
        "solve_p99_ms": round(s["solve_ms_p99"], 3),
    }
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--t-end", type=float, default=None)
    ap.add_argument("--solvers", default="S0,S1,S2,S3")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "results"))
    args = ap.parse_args()

    base_cfg = load_cfg(args.config)
    t_end = args.t_end if args.t_end else (300 if args.quick else 600)
    solvers = args.solvers.split(",")
    rows = []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs_out = out / "runs"

    # 1) 主对比:三场景 × 三策略 × 算法
    for scenario in [1, 2, 3]:
        for strategy in ["latency", "balance", "energy"]:
            for solver in solvers:
                print(f"运行 scenario={scenario} strategy={strategy} solver={solver} ...",
                      flush=True)
                rows.append(run_one(base_cfg, scenario, strategy, solver, t_end,
                                    tag="main",
                                    detail_out=runs_out / f"main_sc{scenario}_{strategy}_{solver}"))

    # 2) 消融(场景3, latency, S1):压缩关 / 能量约束关 / 抢占关
    def ablate(name, mutate):
        cfg = copy.deepcopy(base_cfg)
        mutate(cfg)
        print(f"消融 {name} ...", flush=True)
        rows.append(run_one(cfg, 3, "latency", "S1", t_end, tag=f"ablation:{name}",
                            detail_out=runs_out / f"ablation_{name}"))

    def no_compress(cfg):
        for b in cfg["traffic"]["qos"].values():
            b["compression_rho"] = 1.0
    def no_energy(cfg):
        cfg["algorithm"]["enforce_energy_budget"] = False
    def no_preempt(cfg):
        cfg["algorithm"]["enable_preemption"] = False

    ablate("no_compression", no_compress)
    ablate("no_energy_constraint", no_energy)
    ablate("no_preemption", no_preempt)

    # 导出
    keys = list(rows[0].keys())
    with open(out / "compare.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    # Markdown 表
    with open(out / "compare.md", "w") as f:
        f.write("# 算法/场景/策略 横向对比\n\n")
        cols = ["tag", "scenario", "strategy", "solver", "qos_overall",
                "qos_LL", "qos_HR", "qos_HC", "qos_MC", "energy_J",
                "avg_E_per_task_J", "compress_save", "max_conc", "solve_p99_ms"]
        f.write("| " + " | ".join(cols) + " |\n")
        f.write("|" + "|".join(["---"] * len(cols)) + "|\n")
        for r in rows:
            f.write("| " + " | ".join(str(r[c]) for c in cols) + " |\n")
    print(f"\n对比结果已保存: {out}/compare.csv, {out}/compare.md")


if __name__ == "__main__":
    main()
