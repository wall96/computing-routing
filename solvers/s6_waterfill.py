"""S6 等完成时间注水法(星上拆分卸载,latency 策略)。

终端无算力不能拆:整包上行到接入卫星 A,A 上拆分(开销 ∝ 包大小),子包经星间链路送到
多个算力节点并行计算,再各自下行到目的终端汇合;整包时延 = 最晚子包到达时间。
对每个候选拆分点 A:t_k = d_k + a_k·x_k(见 split_common),min max_k t_k 的最优解为
所有被用子包同时到达 τ,x_k = clip((τ − d_k)/a_k, 0, cap_k),Σx_k = 1。

为使子包相互独立(注水法前提),A 的每条出星间链路只保留 d 最小的一个候选;
解出后精确核算(含共用下行),不可行则去掉最远的子包重解。
最后与不拆分的最优单节点(直通转发)比较,拆分更快才拆。
"""
from __future__ import annotations
from typing import List, Optional

from models import Snapshot, Task, Allocation
from solvers.base import Solver, INF
from solvers.split_common import (Cand, build_groups, try_build, best_single, better,
                                  split_settings, require_latency)


def waterfill(cands: List[Cand]):
    """返回 (τ, shares);总容量不足 1 时返回 (INF, None)。"""
    if sum(c.cap for c in cands) < 1.0 - 1e-9:
        return INF, None

    def total(tau):
        return sum(min(c.cap, max(0.0, (tau - c.d) / c.a)) for c in cands)

    bps = sorted({c.d for c in cands} | {c.d + c.a * c.cap for c in cands})
    lo = bps[0]
    for hi in bps[1:]:
        s_hi = total(hi)
        if s_hi >= 1.0 - 1e-12:
            s_lo = total(lo)
            tau = lo + (1.0 - s_lo) * (hi - lo) / (s_hi - s_lo) if s_hi > s_lo else hi
            break
        lo = hi
    else:
        return INF, None
    return tau, [min(c.cap, max(0.0, (tau - c.d) / c.a)) for c in cands]


class S6WaterFill(Solver):
    name = "S6"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        require_latency(strategy, self.name)
        max_splits, coef, min_gain, max_access = split_settings(self.acfg)
        groups = build_groups(snapshot, task, self.enforce_energy, coef, max_access)
        if not groups:
            return None
        best = best_single(snapshot, task, groups, strategy, self.name, self.enforce_energy)
        if max_splits == 1:
            return best
        for cands in groups.values():
            if best is not None and cands[0].d >= best.max_delay_ms:
                continue                      # 拆分点的固定时延已不比不拆分更快
            seen, nodes, pool = set(), set(), []
            for c in cands:
                if c.branch not in seen and c.node.id not in nodes:
                    seen.add(c.branch); nodes.add(c.node.id); pool.append(c)
            if max_splits:
                pool = pool[:max_splits]
            active = list(pool)
            while len(active) > 1:
                tau, shares = waterfill(active)
                if shares is None:
                    break
                used = [(c, x) for c, x in zip(active, shares) if x > 1e-6]
                if len(used) < 2:
                    break                     # 注水只用 1 个 = 拆分无收益
                alloc = try_build(snapshot, task, [c for c, _ in used], [x for _, x in used],
                                  strategy, self.name, self.enforce_energy, coef)
                if alloc is not None:
                    if better(alloc, best, min_gain):
                        best = alloc
                    break
                far = max(used, key=lambda cx: cx[0].d)[0]
                active = [c for c in active if c is not far]
        return best
