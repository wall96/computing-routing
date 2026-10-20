"""CLI 入口。"""
from __future__ import annotations
import argparse
import logging
from pathlib import Path
import yaml

from simulator import Simulator


def parse_args():
    p = argparse.ArgumentParser(description="星地算力路由仿真器 v5")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--scenario", type=int, choices=[1, 2, 3], default=None)
    p.add_argument("--strategy", choices=["latency", "balance", "energy"], default=None)
    p.add_argument("--solver", choices=["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"], default=None)
    p.add_argument("--t-end", type=float, default=None)
    p.add_argument("--slot", type=float, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--out", default="results")
    return p.parse_args()


def main():
    args = parse_args()
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = Path(__file__).parent / args.config
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if args.t_end is not None:
        cfg["simulation"]["t_end_sec"] = args.t_end
    if args.slot is not None:
        cfg["simulation"]["slot_sec"] = args.slot
    if args.seed is not None:
        cfg["simulation"]["random_seed"] = args.seed
    logging.basicConfig(level=cfg["simulation"].get("log_level", "INFO"))

    sim = Simulator(cfg, scenario=args.scenario, strategy=args.strategy,
                    solver=args.solver)
    metrics = sim.run()
    metrics.export(args.out)
    print(f"\n结果已保存至: {args.out}/")


if __name__ == "__main__":
    main()
