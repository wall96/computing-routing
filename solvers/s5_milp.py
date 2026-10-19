"""S5 MILP 精确解(小规模最优参照,《设计文档》§9.3 S5)。

对单任务在当前残余资源下求最优放置 + 两段路由(与其它 solver 同为逐任务贪心口径,
但段内是精确最优)。需要 PuLP;图过大则跳过(返回 None)。
支持 latency(min 最大链时延)与 energy(min 总能耗);balance 近似为 min 最大链路负载。
"""
from __future__ import annotations
import math
from typing import Optional, List
from models import Snapshot, Task, Allocation
from solvers import base
from solvers.base import Solver
from energy import compute_latency_ms, compute_power_W, compute_energy_J


class S5Milp(Solver):
    name = "S5"

    def solve(self, snapshot: Snapshot, task: Task, strategy: str) -> Optional[Allocation]:
        try:
            import pulp
        except ImportError:
            raise RuntimeError("S5 需要 PuLP:pip install pulp")

        mcfg = self.acfg.get("milp", {})
        if len(snapshot.nodes) > mcfg.get("max_nodes", 80):
            return None    # 太大,跳过(仅小规模参照)

        cands = base.candidate_compute_nodes(snapshot, task, self.enforce_energy)
        if not cands or task.src not in snapshot.nodes:
            return None
        cand_ids = [c.id for c in cands]
        cand_map = {c.id: c for c in cands}

        # 有向弧
        arcs = []
        for (a, b), lk in snapshot.links.items():
            if not lk.alive:
                continue
            arcs.append((a, b)); arcs.append((b, a))
        # 终端不能中转:非本任务端点的终端弧直接删除
        own = {task.src, *task.dst_set}
        arcs = [(a, b) for (a, b) in arcs
                if not ({a, b} & snapshot.endpoint_only) or {a, b} & snapshot.endpoint_only <= own]
        r0, rho, D0 = task.rate_mbps, task.compression_rho, task.data_volume_bits
        dsts = task.dst_set

        prob = pulp.LpProblem("route", pulp.LpMinimize)
        y = pulp.LpVariable.dicts("y", cand_ids, cat="Binary")
        fpre = pulp.LpVariable.dicts("fpre", arcs, cat="Binary")
        fpost = {j: pulp.LpVariable.dicts(f"fpost{j}", arcs, cat="Binary") for j in range(len(dsts))}
        prob += pulp.lpSum(y[v] for v in cand_ids) == 1

        def out_in(fvar, node):
            o = pulp.lpSum(fvar[(a, b)] for (a, b) in arcs if a == node)
            i = pulp.lpSum(fvar[(a, b)] for (a, b) in arcs if b == node)
            return o, i

        # 前段:src 发 1,选中的 v 收 y_v
        for n in snapshot.nodes:
            o, i = out_in(fpre, n)
            if n == task.src:
                prob += (o - i == 1)
            elif n in cand_map:
                prob += (o - i == -y[n])
            else:
                prob += (o - i == 0)
        # 后段:每个 dst,选中的 v 发 y_v,dst 收 1
        for j, dst in enumerate(dsts):
            for n in snapshot.nodes:
                o, i = out_in(fpost[j], n)
                src_amt = y[n] if n in cand_map else 0
                if n == dst:
                    prob += (o - i == src_amt - 1)
                elif n in cand_map:
                    prob += (o - i == src_amt)
                else:
                    prob += (o - i == 0)

        # 本任务自己的终端也只能作本段首/尾:其它终端在本段内不得有流量
        for seg_vars, ends in [(fpre, {task.src})] + [
                (fpost[j], {dst}) for j, dst in enumerate(dsts)]:
            for n in own & snapshot.endpoint_only - ends:
                prob += pulp.lpSum(seg_vars[(a, b)] for (a, b) in arcs if n in (a, b)) == 0

        # 带宽(无向物理链路)
        for (a, b), lk in snapshot.links.items():
            use = r0 * (fpre[(a, b)] + fpre[(b, a)])
            use += rho * r0 * pulp.lpSum(fpost[j][(a, b)] + fpost[j][(b, a)] for j in range(len(dsts)))
            prob += use <= lk.bw_remain_mbps + 1e-6

        def arc_delay(a, b):
            return snapshot.get_link(a, b).delay_ms

        def arc_lloss(a, b):
            return -math.log(max(1e-12, 1 - snapshot.get_link(a, b).loss))

        tcomp = pulp.lpSum(y[v] * compute_latency_ms(cand_map[v], task) for v in cand_ids)
        pre_delay = pulp.lpSum(fpre[(a, b)] * arc_delay(a, b) for (a, b) in arcs)
        pre_lloss = pulp.lpSum(fpre[(a, b)] * arc_lloss(a, b) for (a, b) in arcs)
        Lbud = -math.log(max(1e-12, 1 - task.loss_budget))
        Tmax = pulp.LpVariable("Tmax", lowBound=0)
        for j in range(len(dsts)):
            pdj = pulp.lpSum(fpost[j][(a, b)] * arc_delay(a, b) for (a, b) in arcs)
            plj = pulp.lpSum(fpost[j][(a, b)] * arc_lloss(a, b) for (a, b) in arcs)
            prob += (pre_delay + tcomp + pdj <= task.delay_budget_ms)
            prob += (pre_lloss + plj <= Lbud)
            prob += (Tmax >= pre_delay + tcomp + pdj)

        # 目标
        if strategy == "energy":
            e_pre = pulp.lpSum(fpre[(a, b)] * snapshot.get_link(a, b).beta * D0 for (a, b) in arcs)
            e_post = pulp.lpSum(fpost[j][(a, b)] * snapshot.get_link(a, b).beta * D0 * rho
                                for j in range(len(dsts)) for (a, b) in arcs)
            e_comp = pulp.lpSum(y[v] * compute_energy_J(cand_map[v], task.cycles_total) for v in cand_ids)
            prob += e_pre + e_post + e_comp
        elif strategy == "balance":
            Lmax = pulp.LpVariable("Lmax", lowBound=0)
            for (a, b), lk in snapshot.links.items():
                use = r0 * (fpre[(a, b)] + fpre[(b, a)])
                use += rho * r0 * pulp.lpSum(fpost[j][(a, b)] + fpost[j][(b, a)]
                                             for j in range(len(dsts)))
                prob += Lmax >= use / max(1e-9, lk.bw_total_mbps)
            prob += Lmax
        else:  # latency
            prob += Tmax

        status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
        if pulp.LpStatus[status] != "Optimal":
            return None

        # 还原路径
        v_star = next((v for v in cand_ids if y[v].value() and y[v].value() > 0.5), None)
        if v_star is None:
            return None
        path_pre = self._trace(task.src, v_star, arcs, fpre)
        if path_pre is None:
            return None
        posts = []
        for j, dst in enumerate(dsts):
            p = self._trace(v_star, dst, arcs, fpost[j])
            if p is None:
                return None
            posts.append(p)
        return base.validate_and_build(snapshot, task, cand_map[v_star], path_pre, posts,
                                       strategy, self.name, self.enforce_energy)

    def _trace(self, src, dst, arcs, fvar) -> Optional[List[str]]:
        if src == dst:
            return [src]
        nxt = {}
        for (a, b) in arcs:
            val = fvar[(a, b)].value()
            if val and val > 0.5:
                nxt[a] = b
        path = [src]; cur = src; seen = {src}
        while cur != dst:
            if cur not in nxt:
                return None
            cur = nxt[cur]
            if cur in seen:
                return None
            seen.add(cur); path.append(cur)
            if len(path) > 64:
                return None
        return path
