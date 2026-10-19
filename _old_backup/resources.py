"""M5 资源簿记：算力扣减/释放 + 带宽扣减/释放 + LL 抢占。"""
from __future__ import annotations
from typing import Dict, List, Tuple, Optional
from models import Snapshot, Allocation, Task, BizType


class ResourceLedger:
    """跨时间片维护活跃 Allocation。"""

    def __init__(self, algorithm_cfg: dict):
        self.active: List[Allocation] = []
        self.cfg = algorithm_cfg

    # ============= 占用 / 释放 =============
    def reserve(self, snapshot: Snapshot, alloc: Allocation, track: bool = True):
        """把分配落到当前 snapshot 上：扣带宽 / 扣算力。

        track=True：把该 alloc 加入活跃列表（新分配时用）；
        track=False：仅修改 snapshot 状态（跨片重新落账时用）。
        """
        for (u, v), bw in alloc.bw_per_link.items():
            link = snapshot.get_link(u, v)
            if link is None:
                continue
            link.bw_remain_mbps -= bw
        node = snapshot.nodes.get(alloc.compute_node)
        if node is not None:
            node.capacity_remain -= alloc.task.compute_rate_tflops
        if track:
            self.active.append(alloc)

    def release(self, snapshot: Snapshot, alloc: Allocation):
        """释放：把这条 alloc 的带宽/算力归还（仅对当前 snapshot）。"""
        for (u, v), bw in alloc.bw_per_link.items():
            link = snapshot.get_link(u, v)
            if link is None:
                continue
            link.bw_remain_mbps = min(link.bw_total_mbps, link.bw_remain_mbps + bw)
        node = snapshot.nodes.get(alloc.compute_node)
        if node is not None:
            node.capacity_remain = min(node.capacity_total,
                                       node.capacity_remain + alloc.task.compute_rate_tflops)
        if alloc in self.active:
            self.active.remove(alloc)

    def evict_finished(self, snapshot: Snapshot, t_now: float) -> List[Allocation]:
        """t_now 时刻已经结束的任务批量释放。"""
        finished = [a for a in self.active if a.task.t_end <= t_now]
        for a in finished:
            self.release(snapshot, a)
        return finished

    def reset_snapshot_state(self, snapshot: Snapshot):
        """新时间片：先把 snapshot 重置满状态，再把当前活跃 alloc 重新落上去。
        注意 Allocation.bw_per_link 的边可能在新 snapshot 上不存在（链路切换），
        这种情况由 simulator 决定是迁移还是终止。"""
        for n in snapshot.nodes.values():
            n.reset_capacity()
        for lk in snapshot.links.values():
            lk.reset_bw()
        # 重新落账：只改 snapshot 状态，不再追加到 active
        for a in list(self.active):
            ok = self._can_reapply(snapshot, a)
            if ok:
                self.reserve(snapshot, a, track=False)
            # 如果不行则保留在 active（由 simulator 决策迁移），暂不在新 snapshot 上预扣

    def _can_reapply(self, snapshot: Snapshot, alloc: Allocation) -> bool:
        """检查 alloc 在新 snapshot 上是否所有边/节点都还在且容量够。"""
        for (u, v), bw in alloc.bw_per_link.items():
            link = snapshot.get_link(u, v)
            if link is None or not link.alive or link.bw_remain_mbps < bw:
                return False
        node = snapshot.nodes.get(alloc.compute_node)
        if node is None or node.capacity_remain < alloc.task.compute_rate_tflops:
            return False
        return True

    # ============= 抢占 =============
    def preempt_one(self, snapshot: Snapshot) -> Optional[Allocation]:
        """挑一个占用最多带宽的 HC/MC 任务，把它带宽缩到 ratio。
        每次调用只处理一个，便于 simulator 增量重试 LL 任务的 Dijkstra。"""
        if not self.cfg.get("enable_preemption", True):
            return None
        ratio = self.cfg.get("preempt_lower_bound_ratio", 0.5)
        candidates = [a for a in self.active
                      if a.task.biz in (BizType.HC, BizType.MC)
                      and not getattr(a, "_preempted", False)]
        if not candidates:
            return None
        candidates.sort(key=lambda a: -sum(a.bw_per_link.values()))
        a = candidates[0]
        keep_old = dict(a.bw_per_link)
        new_bw = {}
        freed_any = False
        for (u, v), bw in keep_old.items():
            link = snapshot.get_link(u, v)
            if link is None:
                new_bw[(u, v)] = bw * ratio
                continue
            released = bw * (1 - ratio)
            link.bw_remain_mbps = min(link.bw_total_mbps, link.bw_remain_mbps + released)
            new_bw[(u, v)] = bw * ratio
            if released > 0:
                freed_any = True
        if not freed_any:
            return None
        a.bw_per_link = new_bw
        a.task.rate_mbps *= ratio
        a._preempted = True
        return a
