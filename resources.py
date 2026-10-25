"""M5 资源簿记:带宽 + 算力 + 功率 扣减/释放 + 抢占(《设计文档》§7, §10.2)。

每次资源变化都会通过 event_sink 输出结构化事件，供 metrics.py 导出
resource_allocations.csv。
"""
from __future__ import annotations
from typing import Callable, List, Optional
from models import Snapshot, Allocation, BizType


class ResourceLedger:
    def __init__(self, algorithm_cfg: dict, event_sink: Optional[Callable[[dict], None]] = None):
        self.active: List[Allocation] = []
        self.cfg = algorithm_cfg
        self.enforce_energy = algorithm_cfg.get("enforce_energy_budget", True)
        self.event_sink = event_sink

    def _link_roles(self, alloc: Allocation, u: str, v: str) -> str:
        key = (u, v) if u < v else (v, u)
        roles = []
        segs = ([(f"S{k}:", s.path_pre, s.path_post) for k, s in enumerate(alloc.splits)]
                if alloc.splits else [("", alloc.path_pre, alloc.path_post)])
        for tag, pre, posts in segs:
            for a, b in zip(pre[:-1], pre[1:]):
                if ((a, b) if a < b else (b, a)) == key:
                    roles.append(f"{tag}PRE")
                    break
            for i, path in enumerate(posts):
                for a, b in zip(path[:-1], path[1:]):
                    if ((a, b) if a < b else (b, a)) == key:
                        roles.append(f"{tag}POST:{i}")
                        break
        return ";".join(roles)

    def _emit(self, snapshot: Snapshot, alloc: Allocation, event: str,
              resource_type: str, resource_id: str, delta: float, unit: str,
              total: float, before: float, after: float, *, node=None, link=None,
              t_now: Optional[float] = None):
        if self.event_sink is None:
            return
        self.event_sink({
            "t": snapshot.t if t_now is None else t_now,
            "event": event,
            "allocation_id": alloc.allocation_id,
            "task_id": alloc.task.id,
            "biz": alloc.task.biz.value,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "node_id": node.id if node is not None else "",
            "node_type": node.type.value if node is not None else "",
            "link_u": link.u if link is not None else "",
            "link_v": link.v if link is not None else "",
            "link_type": link.type.value if link is not None else "",
            "path_roles": self._link_roles(alloc, link.u, link.v) if link is not None else "COMPUTE",
            "allocation_delta": delta,
            "unit": unit,
            "total_capacity": total,
            "remain_before": before,
            "remain_after": after,
        })

    # ---------- 占用 / 释放 ----------
    def reserve(self, snapshot: Snapshot, alloc: Allocation, track: bool = True,
                t_now: Optional[float] = None, event: str = "ALLOCATE"):
        for (u, v), bw in alloc.bw_per_link.items():
            lk = snapshot.get_link(u, v)
            if lk is not None:
                before = lk.bw_remain_mbps
                lk.bw_remain_mbps -= bw
                self._emit(snapshot, alloc, event, "BANDWIDTH", f"{u}<->{v}", bw,
                           "Mbps", lk.bw_total_mbps, before, lk.bw_remain_mbps,
                           link=lk, t_now=t_now)
        for nid, (rate, power) in alloc.node_loads().items():
            node = snapshot.nodes.get(nid)
            if node is None:
                continue
            before = node.capacity_remain
            node.capacity_remain -= rate
            self._emit(snapshot, alloc, event, "COMPUTE", node.id,
                       rate, "cycle/s", node.capacity_total,
                       before, node.capacity_remain, node=node, t_now=t_now)
            if self.enforce_energy:
                before_power = node.power_remain_W
                node.power_remain_W -= power
                self._emit(snapshot, alloc, event, "POWER", node.id,
                           power, "W", node.power_budget_W,
                           before_power, node.power_remain_W, node=node, t_now=t_now)
        if track:
            self.active.append(alloc)

    def release(self, snapshot: Snapshot, alloc: Allocation, t_now: Optional[float] = None,
                event: str = "RELEASE"):
        for (u, v), bw in alloc.bw_per_link.items():
            lk = snapshot.get_link(u, v)
            if lk is not None:
                before = lk.bw_remain_mbps
                lk.bw_remain_mbps = min(lk.bw_total_mbps, lk.bw_remain_mbps + bw)
                self._emit(snapshot, alloc, event, "BANDWIDTH", f"{u}<->{v}", -bw,
                           "Mbps", lk.bw_total_mbps, before, lk.bw_remain_mbps,
                           link=lk, t_now=t_now)
        for nid, (rate, power) in alloc.node_loads().items():
            node = snapshot.nodes.get(nid)
            if node is None:
                continue
            before = node.capacity_remain
            node.capacity_remain = min(node.capacity_total, node.capacity_remain + rate)
            self._emit(snapshot, alloc, event, "COMPUTE", node.id,
                       -rate, "cycle/s", node.capacity_total,
                       before, node.capacity_remain, node=node, t_now=t_now)
            if self.enforce_energy:
                before_power = node.power_remain_W
                node.power_remain_W = min(node.power_budget_W, node.power_remain_W + power)
                self._emit(snapshot, alloc, event, "POWER", node.id,
                           -power, "W", node.power_budget_W,
                           before_power, node.power_remain_W, node=node, t_now=t_now)
        if alloc in self.active:
            self.active.remove(alloc)

    def evict_finished(self, snapshot: Snapshot, t_now: float) -> List[Allocation]:
        finished = [a for a in self.active if a.task.t_end <= t_now]
        for a in finished:
            self.release(snapshot, a, t_now=t_now, event="RELEASE_FINISHED")
        return finished

    def reset_snapshot_state(self, snapshot: Snapshot, t_now: Optional[float] = None) -> List[Allocation]:
        """新片:重置满状态,再把仍可行的活跃 alloc 重新落上。
        返回无法在新快照落账的 alloc(需 simulator 迁移或丢弃),并已从 active 移除。"""
        for n in snapshot.nodes.values():
            n.reset_resources()
        for lk in snapshot.links.values():
            lk.reset_bw()
        orphaned = []
        for a in list(self.active):
            if self._can_reapply(snapshot, a):
                self.reserve(snapshot, a, track=False, t_now=t_now, event="REAPPLY")
            else:
                self.active.remove(a)
                orphaned.append(a)
        return orphaned

    def _can_reapply(self, snapshot: Snapshot, alloc: Allocation) -> bool:
        for (u, v), bw in alloc.bw_per_link.items():
            lk = snapshot.get_link(u, v)
            if lk is None or not lk.alive or lk.bw_remain_mbps < bw:
                return False
        for nid, (rate, power) in alloc.node_loads().items():
            node = snapshot.nodes.get(nid)
            if node is None or node.capacity_remain < rate:
                return False
            if self.enforce_energy and node.power_remain_W < power:
                return False
        return True

    # ---------- 抢占:高优先级抢低 ----------
    def preempt_one(self, snapshot: Snapshot, incoming_priority: int,
                    t_now: Optional[float] = None) -> Optional[Allocation]:
        if not self.cfg.get("enable_preemption", True):
            return None
        ratio = self.cfg.get("preempt_lower_bound_ratio", 0.5)
        # 只抢占优先级更低(数值更大)且尚未被抢的任务
        cand = [a for a in self.active
                if a.task.priority > incoming_priority and not a._preempted
                and a.task.biz in (BizType.HC, BizType.MC)]
        if not cand:
            return None
        cand.sort(key=lambda a: -sum(a.bw_per_link.values()))
        a = cand[0]
        freed = False
        new_bw = {}
        for (u, v), bw in a.bw_per_link.items():
            lk = snapshot.get_link(u, v)
            released = bw * (1 - ratio)
            if lk is not None and released > 0:
                before = lk.bw_remain_mbps
                lk.bw_remain_mbps = min(lk.bw_total_mbps, lk.bw_remain_mbps + released)
                self._emit(snapshot, a, "PREEMPT_RELEASE", "BANDWIDTH", f"{u}<->{v}",
                           -released, "Mbps", lk.bw_total_mbps, before,
                           lk.bw_remain_mbps, link=lk, t_now=t_now)
                freed = True
            new_bw[(u, v)] = bw * ratio
        if not freed:
            return None
        a.bw_per_link = new_bw
        a._preempted = True
        return a
