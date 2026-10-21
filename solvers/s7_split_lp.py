"""S7 按 τ 分段的线性规划(星上拆分卸载,latency 策略)。

与 S6 目标相同(min τ = 最晚子包到达时间),但显式建模同一任务子包共用链路:
共用链路上各子包的比特累加后按该链路速率串行发送(与 evaluate_splits 同口径)。
对每个拆分点(接入卫星)A,候选按 d_k 升序;第 m 段只允许前 m 个候选参与,解一个 LP:
  min τ
  s.t. Σx = 1, 0 ≤ x_k ≤ 算力/功率上限
       u_k  ≥ Σ_i x_i·bits_i(e)/rate_e      ∀ e ∈ 星间段_k        (A → 算力节点)
       w_kj ≥ Σ_i x_i·bits_i(e)/rate_e      ∀ e ∈ 后段_kj
       head + 星间传播_k + 固定处理 + 后段传播_kj + comp_k·x_k + u_k + w_kj ≤ τ
       Σ_i x_i·带宽需求_i(e) ≤ 剩余带宽_e
逐段增大 m,当最优 τ ≤ d_{m+1} 时后续候选不可能再改善,停止。
LP 不建模丢包,由精确校验兜底。最后与不拆分的最优单节点比较,拆分更快才拆。
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import linprog

from models import Snapshot, Task, Allocation
from solvers.base import Solver, INF
from solvers.split_common import (Cand, build_groups, try_build, best_single, better,
                                  split_settings, require_latency, _edges)


def solve_prefix_lp(snapshot: Snapshot, task: Task, cands: List[Cand]):
    """对同一拆分点下的给定候选集解 LP,返回 (τ, shares);不可行返回 (INF, None)。"""
    m, J = len(cands), len(task.dst_set)
    D, rho, r0 = task.packet_bits, task.compression_rho, task.rate_mbps
    itau = m
    iu = lambda k: m + 1 + k
    iw = lambda k, j: 2 * m + 1 + k * J + j
    nvar = 2 * m + 1 + m * J

    isl_e = [set(_edges(c.isl)) for c in cands]
    post_e = [[set(_edges(p)) for p in c.posts] for c in cands]
    t_coef: Dict[Tuple[str, str], np.ndarray] = {}
    for k in range(m):
        for e in isl_e[k]:
            t_coef.setdefault(e, np.zeros(m))[k] += D
        for es in post_e[k]:
            for e in es:
                t_coef.setdefault(e, np.zeros(m))[k] += D * rho
    for e, coef in t_coef.items():
        coef *= 1000.0 / (snapshot.get_link(*e).bw_total_mbps * 1e6)
    # 带宽:含共用的上行链路
    bw_coef: Dict[Tuple[str, str], np.ndarray] = {}
    for k, c in enumerate(cands):
        for e in _edges(c.pre):
            bw_coef.setdefault(e, np.zeros(m))[k] += r0
        for p in c.posts:
            for e in _edges(p):
                bw_coef.setdefault(e, np.zeros(m))[k] += r0 * rho

    rows, rhs = [], []

    def add(entries, b):
        row = np.zeros(nvar)
        for i, v in entries:
            row[i] += v
        rows.append(row); rhs.append(b)

    for e, coef in t_coef.items():
        load = [(i, coef[i]) for i in range(m) if coef[i]]
        for k in range(m):
            if e in isl_e[k]:
                add(load + [(iu(k), -1.0)], 0.0)
            for j in range(J):
                if e in post_e[k][j]:
                    add(load + [(iw(k, j), -1.0)], 0.0)
    for k, c in enumerate(cands):
        for j in range(J):
            add([(k, c.comp), (iu(k), 1.0), (iw(k, j), 1.0), (itau, -1.0)],
                -(c.head + c.prop_isl + c.fixed + c.prop_posts[j]))
    for e, coef in bw_coef.items():
        add([(i, coef[i]) for i in range(m) if coef[i]], snapshot.get_link(*e).bw_remain_mbps)

    A_eq = np.zeros((1, nvar)); A_eq[0, :m] = 1.0
    cost = np.zeros(nvar); cost[itau] = 1.0
    bounds = [(0.0, min(1.0, c.node_cap)) for c in cands] + [(0.0, None)] * (nvar - m)
    res = linprog(cost, A_ub=np.array(rows), b_ub=np.array(rhs), A_eq=A_eq, b_eq=[1.0],
                  bounds=bounds, method="highs")
    if res.status != 0:
        return INF, None
    return float(res.x[itau]), [float(v) for v in res.x[:m]]


def compact(snapshot: Snapshot, task: Task, cands: List[Cand], shares, tau: float,
            rel_tol: float = 1e-4, abs_tol: float = 1e-3):
    """LP 最优面常有退化解:逐个去掉份额最小的子包重解,τ 不变差就接受。"""
    keep = [(c, x) for c, x in zip(cands, shares) if x > 1e-6]
    while len(keep) > 1:
        drop = min(range(len(keep)), key=lambda i: keep[i][1])
        trial = [c for i, (c, _) in enumerate(keep) if i != drop]
        if sum(min(1.0, c.node_cap) for c in trial) < 1.0 - 1e-9:
            break
        t2, s2 = solve_prefix_lp(snapshot, task, trial)
        if s2 is None or t2 > tau * (1 + rel_tol) + abs_tol:
            break
        keep = [(c, x) for c, x in zip(trial, s2) if x > 1e-6]
    return [c for c, _ in keep], [x for _, x in keep]


class S7SplitLP(Solver):
    name = "S7"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        require_latency(strategy, self.name)
        max_splits, coef, min_gain, max_access = split_settings(self.acfg)
        groups = build_groups(snapshot, task, self.enforce_energy, coef, max_access)
        if not groups:
            return None
        best = best_single(snapshot, task, groups, strategy, self.name, self.enforce_energy)
        if max_splits == 1:
            return best
        scfg = self.acfg.get("split", {})
        per_branch = int(scfg.get("lp_per_uplink", 2))
        limit = int(scfg.get("lp_max_candidates", 12))
        for cands in groups.values():
            if best is not None and cands[0].d >= best.max_delay_ms:
                continue
            count: Dict[str, int] = {}
            nodes, pool = set(), []
            for c in cands:
                if count.get(c.branch, 0) < per_branch and c.node.id not in nodes:
                    count[c.branch] = count.get(c.branch, 0) + 1
                    nodes.add(c.node.id); pool.append(c)
                if len(pool) >= limit:
                    break
            solved = []
            for m in range(2, len(pool) + 1):
                prefix = pool[:m]
                if sum(min(1.0, c.node_cap) for c in prefix) < 1.0 - 1e-9:
                    continue
                tau, shares = solve_prefix_lp(snapshot, task, prefix)
                if shares is not None:
                    solved.append((tau, m, shares))
                    if m < len(pool) and tau <= pool[m].d:
                        break
            for tau, m, shares in sorted(solved):
                if best is not None and tau >= best.max_delay_ms:
                    break
                sub, sh = compact(snapshot, task, pool[:m], shares, tau)
                if len(sub) < 2 or (max_splits and len(sub) > max_splits):
                    continue
                alloc = try_build(snapshot, task, sub, sh, strategy, self.name,
                                  self.enforce_energy, coef)
                if alloc is not None:
                    if better(alloc, best, min_gain):
                        best = alloc
                    break
        return best
