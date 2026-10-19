"""CLI 入口。"""
from __future__ import annotations
import argparse
import logging
from pathlib import Path

import yaml

from simulator import Simulator


def parse_args():
    p = argparse.ArgumentParser(description="算力路由仿真器 (v3)")
    p.add_argument("--config", default="config.yaml", help="配置文件路径")
    p.add_argument("--scenario", choices=["A", "B"], default=None, help="场景覆盖")
    p.add_argument("--t-end", type=float, default=None, help="仿真结束时间 (s)")
    p.add_argument("--slot", type=float, default=None, help="时间片长度 (s)")
    p.add_argument("--no-scale", action="store_true", help="不应用缩放（完整 1200 颗 LEO）")
    p.add_argument("--force-strategy", choices=["latency", "balance", "energy"],
                   default=None, help="覆盖业务默认策略")
    p.add_argument("--out", default="results", help="输出目录")
    p.add_argument("--seed", type=int, default=None)
    return p.parse_args()


def main():
    args = parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        cfg_path = Path(__file__).parent / args.config
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    if args.t_end is not None:
        cfg["simulation"]["t_end_sec"] = args.t_end
    if args.slot is not None:
        cfg["simulation"]["slot_sec"] = args.slot
    if args.seed is not None:
        cfg["simulation"]["random_seed"] = args.seed

    logging.basicConfig(level=cfg["simulation"].get("log_level", "INFO"))

    sim = Simulator(cfg, scenario=args.scenario, force_strategy=args.force_strategy)
    if args.no_scale:
        sim.disable_scale()

    metrics = sim.run()
    metrics.export(args.out)
    print(f"\n结果已保存至: {args.out}/")


if __name__ == "__main__":
    main()
