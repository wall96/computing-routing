"""Mixed LL:HR:HC:MC=3:2:3:1, exact events and segment accounting."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
import random
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import yaml
from simulator import Simulator

WEIGHTS={'LL':3,'HR':2,'HC':3,'MC':1}
DEFAULT_OUT=ROOT/'results'/'mixed_3600_latency_20261016'
SPARSE_OUT=ROOT/'results'/'mixed_3600_latency_sparse_peak_20261016'


def planned_timing(traffic,slots):
    """Forecast offered load, not routing outcomes, with the generator's time stream."""
    rng=random.Random(traffic['time_seed'])
    events=[]
    for i in range(slots):
        counts=traffic['fixed_arrival_overrides'].get(i,{})
        for biz in WEIGHTS:
            for _ in range(counts.get(biz,0)):
                duration=rng.uniform(*traffic['qos'][biz]['duration_s'])
                arrival=i*30+rng.uniform(0,30)
                events.extend(((arrival,1),(min(slots*30,arrival+duration),-1)))
    active=peak=0;integral=0.0;previous=0.0
    for when,delta in sorted(events):
        integral+=active*(when-previous)
        active+=delta;peak=max(peak,active);previous=when
    return peak,integral/(slots*30)


def sparse_peak_profile(traffic,cap,slots,schedule_seed=2042):
    """Random finite-count background plus one calibrated 30-second arrival pulse.

    Background count is derived from Little's law at 20% nominal occupancy.
    Only the 3600-second total has exact business proportions; slot counts vary.
    Pulse sizing uses offered arrival/end times only, without consulting routing.
    """
    mean_duration=sum(w*sum(traffic['qos'][b]['duration_s'])/2
                      for b,w in WEIGHTS.items())/sum(WEIGHTS.values())
    background_units=max(1,round(.20*cap*(slots*30)/mean_duration/9))
    rng=random.Random(schedule_seed)
    background={i:{b:0 for b in WEIGHTS} for i in range(slots)}
    for biz,weight in WEIGHTS.items():
        for _ in range(weight*background_units):
            background[rng.randrange(slots)][biz]+=1
    traffic['fixed_arrivals_per_slot']={b:0 for b in WEIGHTS}
    peak_slot=slots//2
    for pulse_units in range(math.ceil(cap/9)+2):
        schedule={i:dict(counts) for i,counts in background.items()}
        for biz,w in WEIGHTS.items():schedule[peak_slot][biz]+=w*pulse_units
        traffic['fixed_arrival_overrides']=schedule
        offered_peak,offered_mean=planned_timing(traffic,slots)
        if offered_peak>=math.ceil(1.05*cap):break
    else:
        raise RuntimeError('Cannot construct the requested offered concurrency peak')
    traffic['arrival_profile']={
        'name':'sparse_peak','schedule_seed':schedule_seed,'nominal_background_fraction':.20,
        'weighted_mean_duration_s':mean_duration,'background_tasks':background_units*9,
        'pulse_tasks':pulse_units*9,'pulse_start_s':peak_slot*30,
        'pulse_end_s':(peak_slot+1)*30,'offered_peak':offered_peak,
        'offered_time_avg_active':offered_mean,'ratio_scope':'whole_horizon',
        'pulse_sizing':'first multiple of 9 reaching 105% cap in offered-time forecast',
        'background_counts_by_slot':background}


def write_csv(path,rows):
    with Path(path).open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def experiment_config(base,scenario,variant,out,slots=120,arrival_profile='legacy_dense'):
    cfg=json.loads(json.dumps(base))
    # JSON round-trip changes scenario integer keys to strings; restore them.
    for key in ('scenario_shells','scenario_geo','scenario_ground','scenario_max_concurrency'):
        cfg['constellation'][key]={int(k):v for k,v in cfg['constellation'][key].items()}
    cfg['simulation'].update(scenario=scenario,strategy='latency',solver='S1',
        t_start_sec=0,t_end_sec=slots*30,slot_sec=30,random_seed=42,
        event_driven_within_slot=True,event_output_dir=str(Path(out).resolve()))
    cfg['scale']['enabled']=False
    cfg['algorithm'].update(latency_lazy_candidates=True,enable_preemption=False)
    cap=cfg['constellation']['scenario_max_concurrency'][scenario]
    steady_units=max(1,round(cap*.6/9))
    peak_units=max(math.ceil(cap/9),round(cap*(1.05 if scenario==3 else 1.0)/9))
    traffic=cfg['traffic']
    traffic.update(arrival_rate_per_slot=WEIGHTS,
        fixed_arrivals_per_slot={b:w*steady_units for b,w in WEIGHTS.items()},
        fixed_arrival_overrides={i:{b:w*peak_units for b,w in WEIGHTS.items()}
                                for i in range(slots) if i%10==5},
        independent_time_rng=True,time_seed=1042,random_arrivals_all_businesses=True,
        arrival_at_slot_start=variant=='fixed30')
    if variant=='fixed30':
        for qos in traffic['qos'].values():
            qos['duration_s']=[30,30];qos['mode']='persist'
    elif arrival_profile=='sparse_peak':
        sparse_peak_profile(traffic,cap,slots)
    return cfg


def extra_outputs(sim,out):
    m=sim.metrics;history=m._allocation_history
    by_task=defaultdict(list)
    for r in history:by_task[r['task_id']].append(r)
    lifecycle=[]
    rejected={t.id:reason for t,reason in m.rejects}
    for task in m.attempts:
        rows=by_task[task.id];accepted=bool(rows)
        status=m.task_terminal.get(task.id,'REJECTED')
        ec,et=m.task_energy.get(task.id,[0.0,0.0])
        lifecycle.append(dict(task_id=task.id,biz=task.biz.value,src=task.src,
            dst_json=json.dumps(task.dst_set),arrival_t=task.arrival_t,duration_s=task.duration_s,
            requested_t_end=task.t_end,status=status,reject_reason=rejected.get(task.id,''),
            initially_accepted=int(accepted),allocation_segments=len(rows),
            reroutes=max(0,len(rows)-1),migrations=sum(r['event_type']=='MIGRATION' for r in rows),
            service_seconds=m.task_service_seconds.get(task.id,0.0),
            last_service_end=max((r['service_t_end'] for r in rows),default=''),
            lifetime_max_delay_ms=m.task_max_delay.get(task.id,0.0),
            lifetime_max_loss=m.task_max_loss.get(task.id,0.0),
            energy_compute_J=ec,energy_tx_J=et,energy_total_J=ec+et,
            input_bits_served=task.rate_bps*m.task_service_seconds.get(task.id,0.0)))
    write_csv(out/'lifecycle_tasks.csv',lifecycle)
    slot_seconds=sim.slot;horizon=sim.t_end-sim.t_start
    report=[]
    for biz in ('ALL','LL','HR','HC','MC'):
        tasks=[r for r in lifecycle if biz=='ALL' or r['biz']==biz]
        routes=[r for r in history if biz=='ALL' or r['biz']==biz]
        served=[r for r in tasks if r['initially_accepted']]
        survived=[r for r in tasks if r['status'] in ('COMPLETED','ACTIVE_AT_END')]
        delays=[r['lifetime_max_delay_ms'] for r in served]
        energy=sum(r['energy_total_J'] for r in tasks)
        bits=sum(r['input_bits_served'] for r in tasks)
        durations=sum(r['service_seconds'] for r in tasks)
        assert abs(durations-sum(r['service_duration_s'] for r in routes))<1e-5
        row=dict(scenario=sim.scenario,variant=sim.metrics.meta['mixed_variant'],biz=biz,
            generated=len(tasks),initially_accepted=len(served),
            initial_accept_rate=len(served)/len(tasks),
            completed=sum(r['status']=='COMPLETED' for r in tasks),
            active_at_end=sum(r['status']=='ACTIVE_AT_END' for r in tasks),
            dropped=sum(r['status']=='DROPPED' for r in tasks),
            final_survived=len(survived),final_survival_rate=len(survived)/len(tasks),
            rejected_cap=sum(r['reject_reason']=='scenario_concurrency_cap' for r in tasks),
            rejected_no_feasible=sum(r['reject_reason']=='no_feasible' for r in tasks),
            observed_service_seconds=durations,time_avg_active=durations/horizon,
            lifetime_delay_mean_ms=statistics.fmean(delays) if delays else 0.0,
            lifetime_delay_p95_ms=float(np.quantile(delays,.95)) if delays else 0.0,
            lifetime_delay_max_ms=max(delays,default=0.0),
            service_weighted_delay_ms=sum(r['max_delay_ms']*r['service_duration_s'] for r in routes)/max(1e-12,durations),
            energy_total_J=energy,energy_compute_J=sum(r['energy_compute_J'] for r in tasks),
            energy_tx_J=sum(r['energy_tx_J'] for r in tasks),
            energy_per_initially_accepted_J=energy/max(1,len(served)),
            energy_J_per_input_Gbit=energy/max(1e-12,bits)*1e9,
            observed_input_Gbit=bits/1e9,allocation_segments=len(routes),
            reroutes=sum(r['reroutes'] for r in tasks),migrations=sum(r['migrations'] for r in tasks),
            source_compute_segment_pct=100*sum(r['src']==r['compute_node'] for r in routes)/max(1,len(routes)),
            ground_compute_segment_pct=100*sum(r['compute_node_type'] in ('EDGE','DC') for r in routes)/max(1,len(routes)),
            peak_concurrency=m.max_concurrency if biz=='ALL' else '',
            peak_link_util_pct=100*max(r['link_util_max'] for r in m._slot_stats) if biz=='ALL' else '',
            mean_link_cv=sum(r['link_util_cv_mean']*slot_seconds for r in m._slot_stats)/horizon if biz=='ALL' else '',
            peak_node_util_pct=100*max(r['node_util_max'] for r in m._slot_stats) if biz=='ALL' else '',
            peak_compute_power_W=max(r['compute_power_max_W'] for r in m._slot_stats) if biz=='ALL' else '',
            solve_p99_ms=m.summary()['solve_ms_p99'] if biz=='ALL' else '')
        report.append(row)
    write_csv(out/'mixed_metrics.csv',report)
    initial_counts=Counter(r['biz'] for r in lifecycle)
    units=initial_counts['MC']
    assert all(initial_counts[b]==w*units for b,w in WEIGHTS.items())
    assert abs(sum(r['active_mean']*slot_seconds for r in m._slot_stats)-sum(r['service_seconds'] for r in lifecycle))<1e-4
    assert sum(r['status']=='REJECTED' for r in lifecycle)+len(m.alloc_by_task)==len(lifecycle)
    assert all(r['service_seconds']<=r['duration_s']+1e-6 for r in lifecycle)
    sources=['simulator.py','event_simulation.py','metrics.py','tasks.py','solvers/s1_dijkstra.py','config.yaml']
    audit=dict(generated_ratio=dict(initial_counts),exact_3_2_3_1='PASS',
        service_time_integral='PASS',status_partition='PASS',no_over_service='PASS',
        source_sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources})
    (out/'run_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    return report[0]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--scenario',type=int,choices=(1,2,3),required=True)
    ap.add_argument('--variant',choices=('fixed30','variable_random'),required=True)
    ap.add_argument('--slots',type=int,default=120)
    ap.add_argument('--arrival-profile',choices=('sparse_peak','legacy_dense'),default='sparse_peak')
    ap.add_argument('--out',type=Path)
    args=ap.parse_args()
    base=yaml.safe_load((ROOT/'config.yaml').read_text(encoding='utf-8'))
    output_root=args.out or (SPARSE_OUT if args.variant=='variable_random' and args.arrival_profile=='sparse_peak' else DEFAULT_OUT)
    out=output_root/args.variant/f'sc{args.scenario}';out.mkdir(parents=True,exist_ok=True)
    cfg=experiment_config(base,args.scenario,args.variant,out,args.slots,args.arrival_profile)
    (out/'effective_config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    if cfg['traffic'].get('arrival_profile',{}).get('name')=='sparse_peak':
        profile=cfg['traffic']['arrival_profile']
        write_csv(out/'arrival_schedule.csv',[
            dict(slot=i,t_start=i*30,is_pulse_slot=int(i*30==profile['pulse_start_s']),
                 **{f'background_{b}':profile['background_counts_by_slot'][i][b] for b in WEIGHTS},
                 **{f'arrivals_{b}':cfg['traffic']['fixed_arrival_overrides'][i][b] for b in WEIGHTS})
            for i in range(args.slots)])
    sim=Simulator(cfg,scenario=args.scenario,strategy='latency',solver='S1',no_scale=True)
    sim.metrics.meta.update(mixed_variant=args.variant,mixed_ratio=WEIGHTS)
    metrics=sim.run()
    metrics.export(str(out))
    summary=extra_outputs(sim,out)
    if args.variant=='variable_random' and args.arrival_profile=='sparse_peak':
        assert summary['peak_concurrency']==sim.max_concurrency, 'Actual carried peak did not reach scenario cap'
    print(json.dumps(summary,ensure_ascii=True),flush=True)


if __name__=='__main__':main()
