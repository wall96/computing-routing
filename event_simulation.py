"""Exact arrivals/completions within each frozen topology slot.

This engine is opt-in. Legacy slot-batched experiments remain reproducible.
"""
from __future__ import annotations

import csv
import heapq
import math
import time
from collections import Counter, defaultdict
from pathlib import Path

from network import build_snapshot


class UtilizationMonitor:
    def __init__(self, snapshot, sink):
        self.sink=sink
        self.count={'BANDWIDTH':len(snapshot.links),
                    'COMPUTE':sum(n.capacity_total>0 for n in snapshot.nodes.values())}
        self.loads={k:{} for k in self.count}
        self.sums=defaultdict(float)
        self.squares=defaultdict(float)
        self.heaps={k:[] for k in self.count}
        self.power=0.0

    def event(self, row):
        self.sink(row)
        kind=row['resource_type']
        if kind=='POWER':
            self.power+=row['allocation_delta']
            return
        if kind not in self.count:return
        key=row['resource_id'];new=max(0.0,1-row['remain_after']/row['total_capacity'])
        old=self.loads[kind].get(key,0.0)
        self.loads[kind][key]=new
        self.sums[kind]+=new-old
        self.squares[kind]+=new*new-old*old
        heapq.heappush(self.heaps[kind],(-new,key))

    def view(self, active):
        result={'active':len(active),'compute_power_W':max(0.0,self.power)}
        counts=Counter(a.task.biz.value for a in active)
        result.update({f'active_{b}':counts[b] for b in ('LL','HR','HC','MC')})
        for kind,prefix in [('BANDWIDTH','link'),('COMPUTE','node')]:
            heap=self.heaps[kind];loads=self.loads[kind]
            while heap and -heap[0][0]!=loads.get(heap[0][1],0.0):heapq.heappop(heap)
            n=max(1,self.count[kind]);mean=max(0.0,self.sums[kind]/n)
            std=math.sqrt(max(0.0,self.squares[kind]/n-mean*mean))
            result.update({f'{prefix}_util_mean':mean,
                f'{prefix}_util_max':-heap[0][0] if heap else 0.0,
                f'{prefix}_util_cv':std/mean if mean>1e-15 else 0.0})
        return result


def run_event_simulation(sim):
    out=Path(sim.sim['event_output_dir'])
    out.mkdir(parents=True,exist_ok=True)
    m=sim.metrics
    m.meta.update(segment_accounting=True,arrival_semantics='exact_time_within_frozen_slot',
                  energy_semantics='integral_over_served_segments_in_observation_window',
                  preemption=False)
    m.stream_resources(out)
    carry=[]
    wall=time.perf_counter()
    timeline_file=(out/'event_timeline.csv').open('w',encoding='utf-8-sig',newline='')
    timeline=None
    try:
        for slot_index in range(int(round((sim.t_end-sim.t_start)/sim.slot))):
            start=sim.t_start+slot_index*sim.slot;end=min(sim.t_end,start+sim.slot)
            snapshot=build_snapshot(start,sim.all_nodes,sim.cfg,sim.scenario)
            for node in snapshot.nodes.values():node.reset_resources()
            sim.ledger.active.clear()
            monitor=UtilizationMonitor(snapshot,m.record_resource_event)
            sim.ledger.event_sink=monitor.event
            new_tasks=sim.gen.gen_for_slot(start,slot_index,snapshot=snapshot)
            for task in new_tasks:
                m.record_attempt(task,generated_slot=slot_index,scheduled_t=task.arrival_t)
            counters=dict(accepted=0,rejected=0,finished=0,rerouted=0,reroute_failed=0)
            current_time=start
            view=monitor.view(sim.ledger.active)
            integrals=defaultdict(float)
            peaks=defaultdict(float)
            solve_first=len(m.solve_times_ms)

            def observe(t,event):
                nonlocal current_time,view,timeline
                dt=t-current_time
                if dt < -1e-8:raise RuntimeError('events out of chronological order')
                for key,value in view.items():integrals[key]+=value*max(0.0,dt)
                current_time=t
                view=monitor.view(sim.ledger.active)
                for key,value in view.items():peaks[key]=max(peaks[key],value)
                row={'t':t,'slot':slot_index,'event':event,**view}
                if timeline is None:
                    timeline=csv.DictWriter(timeline_file,fieldnames=list(row))
                    timeline.writeheader()
                timeline.writerow(row)
                m.max_concurrency=max(m.max_concurrency,int(view['active']))

            def reserve(task,old,t):
                if sim.max_concurrency and len(sim.ledger.active)>=sim.max_concurrency:
                    if old is None:
                        m.record_reject(task,'scenario_concurrency_cap');counters['rejected']+=1
                    else:
                        m.record_drop(old);m.task_terminal[task.id]='DROPPED';counters['reroute_failed']+=1
                    return None
                alloc=sim._solve(snapshot,task)
                if alloc is None:
                    if old is None:
                        m.record_reject(task,'no_feasible');counters['rejected']+=1
                    else:
                        m.record_drop(old);m.task_terminal[task.id]='DROPPED';counters['reroute_failed']+=1
                    return None
                alloc._compute_J_per_s=alloc.power_used_W
                alloc._tx_J_per_s=alloc.energy_tx_J/task.duration_s
                alloc.energy_compute_J=alloc.energy_tx_J=0.0
                alloc.delivered_data_bits=0.0
                moved=old is not None and alloc.compute_node!=old.compute_node
                event='INITIAL' if old is None else ('MIGRATION' if moved else 'REROUTE')
                sim._identify_allocation(alloc,t,event)
                sim.ledger.reserve(snapshot,alloc,t_now=t,event='ALLOCATE' if old is None else 'REROUTE_ALLOCATE')
                if old is None:counters['accepted']+=1
                else:
                    counters['rerouted']+=1;m.reroutes+=1
                    if moved:m.migrations+=1
                return alloc

            # Only businesses that already exist at the boundary are rerouted.
            for old in sorted(carry,key=lambda a:(a.task.priority,a.task.arrival_t,a.task.id)):
                reserve(old.task,old,start)
            observe(start,'TOPOLOGY_REROUTED')
            queue=[]
            for alloc in sim.ledger.active:
                if alloc.task.t_end<=end:
                    heapq.heappush(queue,(alloc.task.t_end,0,alloc.task.priority,alloc.task.id,alloc))
            for task in new_tasks:
                heapq.heappush(queue,(task.arrival_t,1,task.priority,task.id,task))
            while queue:
                t,kind,_,_,item=heapq.heappop(queue)
                if t>end+1e-8:raise RuntimeError('generated event outside its topology slot')
                # Integrate the old state up to this instant before any mutation.
                observe(t,'BEFORE_FINISH' if kind==0 else 'BEFORE_ARRIVAL')
                if kind==0:
                    m.close_segment(item,t)
                    sim.ledger.release(snapshot,item,t_now=t,event='RELEASE_FINISHED')
                    m.task_terminal[item.task.id]='COMPLETED';counters['finished']+=1
                else:
                    alloc=reserve(item,None,t)
                    if alloc is not None and item.t_end<=end:
                        heapq.heappush(queue,(item.t_end,0,item.priority,item.id,alloc))
                observe(t,'FINISH' if kind==0 else 'ARRIVAL')
            observe(end,'SLOT_END')
            row=dict(t=start,**counters,active=len(sim.ledger.active),
                     generated=len(new_tasks),active_mean=integrals['active']/(end-start),
                     active_max=int(peaks['active']),
                     link_util_mean=integrals['link_util_mean']/(end-start),
                     link_util_max=peaks['link_util_max'],
                     node_util_mean=integrals['node_util_mean']/(end-start),
                     node_util_max=peaks['node_util_max'],
                     link_util_cv_mean=integrals['link_util_cv']/(end-start),
                     node_util_cv_mean=integrals['node_util_cv']/(end-start),
                     compute_power_mean_W=integrals['compute_power_W']/(end-start),
                     compute_power_max_W=peaks['compute_power_W'],
                     solve_total_ms=sum(m.solve_times_ms[solve_first:]))
            row.update({f'active_mean_{b}':integrals[f'active_{b}']/(end-start)
                        for b in ('LL','HR','HC','MC')})
            m._slot_stats.append(row)
            carry=list(sim.ledger.active)
            for alloc in carry:
                m.close_segment(alloc,end)
                if end>=sim.t_end:
                    m.task_terminal[alloc.task.id]='ACTIVE_AT_END'
                sim.ledger.release(snapshot,alloc,t_now=end,
                    event='RELEASE_HORIZON' if end>=sim.t_end else 'RELEASE_SLOT_END')
            if m._resource_stream is not None:m._resource_stream.flush()
            timeline_file.flush()
            if slot_index%10==0 or end>=sim.t_end:
                print(f'[event slot {slot_index:3d} t={start:.0f}] '
                      f'new={len(new_tasks)} accept={counters["accepted"]} reject={counters["rejected"]} '
                      f'mean_active={row["active_mean"]:.2f} peak={row["active_max"]} '
                      f'reroute={counters["rerouted"]} drop={counters["reroute_failed"]} '
                      f'wall={time.perf_counter()-wall:.1f}s',flush=True)
        m.meta['wall_seconds']=time.perf_counter()-wall
        m.meta['algorithm_diagnostics']=getattr(sim.solver,'diagnostics',{})
        m.meta['observation_end_sec']=sim.t_end
        return m
    finally:
        timeline_file.close()
