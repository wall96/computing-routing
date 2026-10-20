"""S6 等完成时间注水法(拆分卸载,latency 策略)。

把一个业务包按比例 x_k 拆到多个算力节点并行计算,整包时延 = 最晚子包到达时间。
子包到达时间 t_k = d_k + a_k·x_k(见 split_common),min max_k t_k 的最优解满足:
所有被使用的子包同时到达,时刻 τ;x_k = clip((τ − d_k)/a_k, 0, cap_k),Σx_k = 1。
d_k ≥ τ 的候选不分配——拆不拆、拆几份由收益自动决定。

为让各子包相互独立(注水法前提),每条上行星地链路只保留 d 最小的一个候选;
解出后用 evaluate_splits 精确核算(含共用链路),不可行则去掉最远的子包重解。
"""
from __future__ import annotations
from typing import List, Optional

from models import Snapshot, Task, Allocation
from solvers.base import Solver, INF
from solvers.split_common import Cand, build_candidates, try_build, best_single, require_latency


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
    shares = [min(c.cap, max(0.0, (tau - c.d) / c.a)) for c in cands]
    return tau, shares


class S6WaterFill(Solver):
    name = "S6"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        require_latency(strategy, self.name)
        cands = build_candidates(snapshot, task, self.enforce_energy)
        if not cands:
            return None
        max_splits = int(self.acfg.get("split", {}).get("max_splits", 0)) or len(cands)
        if max_splits == 1:
            return best_single(snapshot, task, cands, strategy, self.name, self.enforce_energy)

        # 每条上行链路只保留 d 最小的候选、每个节点只用一次,保证子包独立
        seen, used_nodes, pool = set(), set(), []
        for c in cands:
            if c.uplink not in seen and c.node.id not in used_nodes:
                seen.add(c.uplink); used_nodes.add(c.node.id); pool.append(c)
        pool = pool[:max_splits]

        active = list(pool)
        while active:
            tau, shares = waterfill(active)
            if shares is None:
                break
            used = [(c, x) for c, x in zip(active, shares) if x > 1e-6]
            alloc = try_build(snapshot, task, [c for c, _ in used], [x for _, x in used],
                              strategy, self.name, self.enforce_energy)
            if alloc is not None:
                return alloc
            if len(used) <= 1:
                break
            # 去掉最远(d 最大)的子包再解
            far = max(used, key=lambda cx: cx[0].d)[0]
            active = [c for c in active if c is not far]
        return best_single(snapshot, task, cands, strategy, self.name, self.enforce_energy)
