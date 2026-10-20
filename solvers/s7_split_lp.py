"""S7 按 τ 分段的线性规划(拆分卸载,latency 策略)。

与 S6 相同的目标 min τ = 最晚子包到达时间,但显式建模同一任务子包共用链路:
共用链路上各子包的比特累加后按该链路速率串行发送(与 evaluate_splits 同口径)。

候选按 d_k 升序;第 m 段只允许前 m 个候选参与,解一个 LP:
  min τ
  s.t. Σx = 1, 0 ≤ x_k ≤ 算力/功率上限
       u_k  ≥ Σ_i x_i·bits_i(e)/rate_e      ∀ e ∈ 前段_k        (前段传输)
       w_kj ≥ Σ_i x_i·bits_i(e)/rate_e      ∀ e ∈ 后段_kj       (后段传输)
       d_kj + comp_k·x_k + u_k + w_kj ≤ τ                       (每个子包、每个目的)
       Σ_i x_i·带宽需求_i(e) ≤ 剩余带宽_e
逐段增大 m,当最优 τ ≤ d_{m+1} 时后续候选不可能再改善,停止。
LP 不建模丢包(整包丢包 = 任一子包丢失),由精确校验兜底,失败则取次优段。
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import linprog

from models import Snapshot, Task, Allocation
from solvers import base
from solvers.base import Solver, INF
from solvers.split_common import Cand, build_candidates, try_build, best_single, require_latency, _edges


def solve_prefix_lp(snapshot: Snapshot, task: Task, cands: List[Cand]):
    """对给定候选集解 LP,返回 (τ, shares);不可行返回 (INF, None)。"""
    m, J = len(cands), len(task.dst_set)
    D, rho, r0 = task.packet_bits, task.compression_rho, task.rate_mbps
    ix = lambda k: k
    itau = m
    iu = lambda k: m + 1 + k
    iw = lambda k, j: 2 * m + 1 + k * J + j
    nvar = 2 * m + 1 + m * J

    pre_e = [set(_edges(c.pre)) for c in cands]
    post_e = [[set(_edges(p)) for p in c.posts] for c in cands]
    # 每条边:各候选在其上的比特系数(ms/单位比例)与带宽系数(Mbps/单位比例)
    t_coef: Dict[Tuple[str, str], np.ndarray] = {}
    bw_coef: Dict[Tuple[str, str], np.ndarray] = {}
    for k in range(m):
        for e in pre_e[k]:
            t_coef.setdefault(e, np.zeros(m))[k] += D
            bw_coef.setdefault(e, np.zeros(m))[k] += r0
        for es in post_e[k]:
            for e in es:
                t_coef.setdefault(e, np.zeros(m))[k] += D * rho
                bw_coef.setdefault(e, np.zeros(m))[k] += r0 * rho
    for e, coef in t_coef.items():
        coef *= 1000.0 / (snapshot.get_link(*e).bw_total_mbps * 1e6)

    rows, rhs = [], []

    def add(entries, b):
        row = np.zeros(nvar)
        for i, v in entries:
            row[i] += v
        rows.append(row); rhs.append(b)

    for e, coef in t_coef.items():
        load = [(ix(i), coef[i]) for i in range(m) if coef[i]]
        for k in range(m):
            if e in pre_e[k]:
                add(load + [(iu(k), -1.0)], 0.0)
            for j in range(J):
                if e in post_e[k][j]:
                    add(load + [(iw(k, j), -1.0)], 0.0)
    for k, c in enumerate(cands):
        for j in range(J):
            add([(ix(k), c.comp), (iu(k), 1.0), (iw(k, j), 1.0), (itau, -1.0)],
                -(c.prop_pre + c.fixed + c.prop_posts[j]))
    for e, coef in bw_coef.items():
        add([(ix(i), coef[i]) for i in range(m) if coef[i]], snapshot.get_link(*e).bw_remain_mbps)

    A_eq = np.zeros((1, nvar)); A_eq[0, :m] = 1.0
    cost = np.zeros(nvar); cost[itau] = 1.0
    bounds = [(0.0, min(1.0, c.node_cap)) for c in cands] + [(0.0, None)] * (nvar - m)
    res = linprog(cost, A_ub=np.array(rows), b_ub=np.array(rhs), A_eq=A_eq, b_eq=[1.0],
                  bounds=bounds, method="highs")
    if res.status != 0:
        return INF, None
    return float(res.x[itau]), [float(v) for v in res.x[:m]]


class S7SplitLP(Solver):
    name = "S7"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        require_latency(strategy, self.name)
        cands = build_candidates(snapshot, task, self.enforce_energy)
        if not cands:
            return None
        scfg = self.acfg.get("split", {})
        max_splits = int(scfg.get("max_splits", 0))
        if max_splits == 1:
            return best_single(snapshot, task, cands, strategy, self.name, self.enforce_energy)
        # 候选池:按 d 升序,每条上行链路至多 lp_per_uplink 个,共至多 lp_max_candidates 个
        per_up, limit = int(scfg.get("lp_per_uplink", 2)), int(scfg.get("lp_max_candidates", 12))
        count: Dict[Tuple[str, str], int] = {}
        used_nodes, pool = set(), []
        for c in cands:
            if count.get(c.uplink, 0) < per_up and c.node.id not in used_nodes:
                count[c.uplink] = count.get(c.uplink, 0) + 1
                used_nodes.add(c.node.id)
                pool.append(c)
            if len(pool) >= limit:
                break

        solved = []
        for m in range(1, len(pool) + 1):
            prefix = pool[:m]
            if sum(min(1.0, c.node_cap) for c in prefix) < 1.0 - 1e-9:
                continue
            tau, shares = solve_prefix_lp(snapshot, task, prefix)
            if shares is not None:
                solved.append((tau, m, shares))
                if m < len(pool) and tau <= pool[m].d:
                    break                     # 下一个候选的固定时延已不小于 τ,不可能改善
        for tau, m, shares in sorted(solved):
            if tau > task.delay_budget_ms:
                break
            sub, shares = compact(snapshot, task, pool[:m], shares, tau)
            if max_splits and sum(x > 1e-6 for x in shares) > max_splits:
                continue
            alloc = try_build(snapshot, task, sub, shares, strategy, self.name,
                              self.enforce_energy)
            if alloc is not None:
                return alloc
        return best_single(snapshot, task, cands, strategy, self.name, self.enforce_energy)


def compact(snapshot: Snapshot, task: Task, cands: List[Cand], shares, tau: float,
            rel_tol: float = 1e-4, abs_tol: float = 1e-3):
    """LP 最优面常有退化解(共用上行的候选间可任意分配而 τ 不变)。

    逐个去掉份额最小的子包重解,τ 不变差(容差内)就接受,得到子包数尽量少的等价解。
    """
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
