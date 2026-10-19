"""Run balance/energy against the exact saved sparse-peak latency inputs.

Only CSV/JSON/Markdown data are written. No Word/PDF report is modified.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simulator import Simulator
from experiments.run_mixed_3600 import extra_outputs,write_csv

BASE=ROOT/'results'/'mixed_3600_latency_sparse_peak_20261016'/'variable_random'
OUT=ROOT/'results'/'mixed_3600_other_strategies_20261016'
BIZ=('ALL','LL','HR','HC','MC')
STRATEGIES=('latency','balance','energy')


def read(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def number(row):
    result={}
    for k,v in row.items():
        try:result[k]=float(v) if v!='' else ''
        except (TypeError,ValueError):result[k]=v
    return result


def input_signature(rows):
    clean=[{k:v for k,v in r.items() if k not in ('final_status','reject_reason')}
           for r in sorted(rows,key=lambda x:x['task_id'])]
    return hashlib.sha256(json.dumps(clean,sort_keys=True).encode()).hexdigest()


def result_metrics(path,strategy,sc,baseline):
    """Independent result audit and event-time aggregate metrics, common schema."""
    generated=read(path/'generated_tasks.csv');expected=read(baseline/'generated_tasks.csv')
    signature=input_signature(generated)
    assert signature==input_signature(expected),('Unpaired generated inputs',sc,strategy)
    cfg=json.loads((path/'effective_config.json').read_text(encoding='utf-8'))
    reference=json.loads((baseline/'effective_config.json').read_text(encoding='utf-8'))
    for item in (cfg,reference):
        item['simulation'].pop('strategy',None);item['simulation'].pop('event_output_dir',None)
    assert cfg==reference,('Configuration differs beyond strategy/output directory',sc,strategy)
    tasks={r['task_id']:number(r) for r in generated}
    lives={r['task_id']:number(r) for r in read(path/'lifecycle_tasks.csv')}
    slots=[number(r) for r in read(path/'slot_stats.csv')]
    stats={r['biz']:number(r) for r in read(path/'mixed_metrics.csv')}
    kpi=json.loads((path/'kpi.json').read_text(encoding='utf-8'))
    assert kpi['meta']['strategy']==strategy
    assert len(slots)==120 and len(lives)==len(tasks)
    cap={1:200,2:250,3:500}[sc]
    peaks=defaultdict(float);jain=defaultdict(float);cap_seconds=near_seconds=0.0
    first_cap='';previous=None;power_integral=0.0;last_t=0.0;timeline_peak=0
    with (path/'event_timeline.csv').open(encoding='utf-8-sig',newline='') as f:
        for raw in csv.DictReader(f):
            event=number(raw);now=event['t'];last_t=now
            assert 0<=event['active']<=cap
            timeline_peak=max(timeline_peak,event['active'])
            if previous:
                dt=now-previous['t'];assert dt>=-1e-7
                if previous['active']==cap:cap_seconds+=dt
                if previous['active']>=cap*.9:near_seconds+=dt
                power_integral+=previous['compute_power_W']*dt
                for kind in ('link','node'):
                    if previous[kind+'_util_mean']>1e-15:
                        jain[kind]+=dt/(1+previous[kind+'_util_cv']**2)
            for b in BIZ[1:]:peaks[b]=max(peaks[b],event['active_'+b])
            if event['active']==cap and first_cap=='':first_cap=now
            previous=event
    assert last_t==3600
    assert timeline_peak==stats['ALL']['peak_concurrency']==kpi['max_concurrency']
    assert timeline_peak==max(s['active_max'] for s in slots)
    routes=read(path/'routing_allocations.csv')
    totals=defaultdict(lambda:defaultdict(float));last_end={};route_max=defaultdict(float)
    power_events=defaultdict(lambda:defaultdict(lambda:[0.0,0.0]))
    for r in routes:
        tid=r['task_id'];task=tasks[tid];start=float(r['t_alloc']);end=float(r['service_t_end'])
        dt=float(r['service_duration_s'])
        assert abs(end-start-dt)<1e-7 and start>=task['arrival_t']-1e-7
        assert end<=min(3600,task['t_end'])+1e-7
        assert end<=30*(int((start+1e-8)//30)+1)+1e-7
        assert abs(start-last_end.get(tid,task['arrival_t']))<1e-7
        last_end[tid]=end
        assert float(r['max_delay_ms'])<=task['delay_budget_ms']+1e-6
        assert float(r['max_loss'])<=task['loss_budget']+1e-12
        assert abs(float(r['energy_compute_J'])-float(r['power_used_W'])*dt)<1e-5
        for b in ('ALL',r['biz']):
            t=totals[b]
            t['service']+=dt;t['compute_delay']+=float(r['compute_latency_ms'])*dt
            t['compute_energy']+=float(r['energy_compute_J']);t['tx_energy']+=float(r['energy_tx_J'])
            if dt>1e-12:
                for when,sign in ((start,1),(end,-1)):
                    power_events[b][when][0]+=sign*float(r['power_used_W'])
                    power_events[b][when][1]+=sign*float(r['energy_tx_J'])/dt
        route_max[tid]=max(route_max[tid],float(r['max_delay_ms']))
    assert abs(totals['ALL']['service']-sum(s['active_mean']*30 for s in slots))<1e-4
    assert abs(power_integral-totals['ALL']['compute_energy'])<1e-4
    power_peaks={}
    for biz in BIZ:
        comp=tx=peak_comp=peak_tx=peak_total=0.0
        # Combine simultaneous starts/ends before observing the new interval.
        for when,(dc,dt) in sorted(power_events[biz].items()):
            comp+=dc;tx+=dt
            peak_comp=max(peak_comp,comp);peak_tx=max(peak_tx,tx);peak_total=max(peak_total,comp+tx)
        power_peaks[biz]=(peak_comp,peak_tx,peak_total)
    assert abs(power_peaks['ALL'][0]-stats['ALL']['peak_compute_power_W'])<1e-5
    resource_events=0
    with (path/'resource_allocations.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            remain,total=float(r['remain_after']),float(r['total_capacity'])
            eps=max(1e-6,total*1e-12)
            assert -eps<=remain<=total+eps
            assert 0<=float(r['t'])<=3600
            resource_events+=1
    for tid,t in lives.items():
        assert t['service_seconds']<=t['duration_s']+1e-6
        if t['status']=='COMPLETED':assert abs(t['service_seconds']-t['duration_s'])<1e-6
        if t['status']=='ACTIVE_AT_END':assert t['requested_t_end']>3600
        if t['initially_accepted']:assert abs(t['lifetime_max_delay_ms']-route_max[tid])<1e-6
    output=[]
    for b in BIZ:
        s=stats[b];t=totals[b]
        assert abs(s['energy_total_J']-t['compute_energy']-t['tx_energy'])<.001
        assert abs(s['observed_service_seconds']-t['service'])<1e-5
        selected=[x for x in lives.values() if b=='ALL' or x['biz']==b]
        assert s['generated']==sum(s[k] for k in ('completed','active_at_end','dropped','rejected_cap','rejected_no_feasible'))
        common=dict(s)
        common.update(strategy=strategy,solver='S1')
        common.update(peak_concurrency=s['peak_concurrency'] if b=='ALL' else peaks[b],
            lifetime_loss_max=max((x['lifetime_max_loss'] for x in selected if x['initially_accepted']),default=0.0),
            compute_delay_service_weighted_ms=t['compute_delay']/max(1e-12,t['service']),
            time_at_cap_s=cap_seconds if b=='ALL' else '',
            time_at_cap_pct=cap_seconds/36 if b=='ALL' else '',
            time_above_90pct_cap_s=near_seconds if b=='ALL' else '',first_cap_t=first_cap if b=='ALL' else '',
            mean_node_cv=statistics.fmean(x['node_util_cv_mean'] for x in slots) if b=='ALL' else '',
            mean_link_jain=jain['link']/3600 if b=='ALL' else '',mean_node_jain=jain['node']/3600 if b=='ALL' else '',
            mean_compute_power_W=t['compute_energy']/3600,
            mean_tx_power_W=t['tx_energy']/3600,
            mean_total_power_W=(t['compute_energy']+t['tx_energy'])/3600,
            peak_compute_power_W=power_peaks[b][0],peak_tx_power_W=power_peaks[b][1],peak_total_power_W=power_peaks[b][2],
            fallback_attempts=kpi['meta']['algorithm_diagnostics']['fallback_attempts'] if b=='ALL' else '',
            fallback_accepted=kpi['meta']['algorithm_diagnostics']['fallback_accepted'] if b=='ALL' else '',
            initially_accepted_survival_rate=s['final_survived']/max(1,s['initially_accepted']),
            wall_seconds=kpi['meta'].get('wall_seconds','') if b=='ALL' else '',input_sha256=signature)
        output.append(common)
    audit=dict(scenario=sc,strategy=strategy,source_dir=str(path.resolve()),baseline_dir=str(baseline.resolve()),
        identical_inputs='PASS',identical_physical_config='PASS',input_sha256=signature,
        QoS_and_resource_bounds='PASS',energy_integral='PASS',time_integral='PASS',
        power_integral_and_peak='PASS',
        service_continuity='PASS',status_partition='PASS',resource_events=resource_events,
        actual_peak_concurrency=stats['ALL']['peak_concurrency'],time_at_cap_s=cap_seconds)
    return output,slots,audit


def run(strategy,sc,out,baseline):
    path=out/strategy/f'sc{sc}';source=baseline/f'sc{sc}'
    path.mkdir(parents=True,exist_ok=True)
    cfg=json.loads((source/'effective_config.json').read_text(encoding='utf-8'))
    for key in ('scenario_shells','scenario_geo','scenario_ground','scenario_max_concurrency'):
        cfg['constellation'][key]={int(k):v for k,v in cfg['constellation'][key].items()}
    cfg['simulation'].update(strategy=strategy,event_output_dir=str(path.resolve()))
    (path/'effective_config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    schedule_source=source/'arrival_schedule.csv'
    # Regenerate the CSV from the saved arrival schedule, without sampling again.
    write_csv(path/'arrival_schedule.csv',read(schedule_source))
    sim=Simulator(cfg,scenario=sc,strategy=strategy,solver='S1',no_scale=True)
    sim.metrics.meta.update(mixed_variant='variable_random',mixed_ratio={'LL':3,'HR':2,'HC':3,'MC':1},
        paired_baseline=str(source.resolve()))
    metrics=sim.run();metrics.export(str(path));extra_outputs(sim,path)
    rows,slots,audit=result_metrics(path,strategy,sc,source)
    write_csv(path/'mixed_metrics.csv',rows)
    (path/'comparison_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows[0],ensure_ascii=True),flush=True)


def common_comparison(base_path,path,sc,strategy):
    """Compare equal task IDs over equal observed service intervals, not full-life totals."""
    groups=[]
    for p in (base_path,path):
        group=defaultdict(list)
        for r in read(p/'routing_allocations.csv'):group[r['task_id']].append(number(r))
        for values in group.values():values.sort(key=lambda r:r['t_alloc'])
        groups.append(group)
    demand={r['task_id']:r for r in read(base_path/'generated_tasks.csv')}
    accum=defaultdict(lambda:defaultdict(float));common_tasks=defaultdict(set)
    for tid in groups[0].keys() & groups[1].keys():
        a,b=groups[0][tid],groups[1][tid];i=j=0
        while i<len(a) and j<len(b):
            x,y=a[i],b[j];begin=max(x['t_alloc'],y['t_alloc']);end=min(x['service_t_end'],y['service_t_end'])
            dt=max(0,end-begin)
            if dt>1e-10:
                for biz in ('ALL',demand[tid]['biz']):
                    common_tasks[biz].add(tid);s=accum[biz];s['seconds']+=dt
                    s['bits']+=float(demand[tid]['rate_mbps'])*1e6*dt
                    for prefix,r in (('latency',x),('other',y)):
                        s[prefix+'_energy']+=r['energy_total_J']/r['service_duration_s']*dt
                        s[prefix+'_delay']+=r['max_delay_ms']*dt
            if x['service_t_end']<=y['service_t_end']:i+=1
            else:j+=1
    result=[]
    for biz in BIZ:
        s=accum[biz];secs=max(1e-12,s['seconds']);bits=max(1e-12,s['bits'])
        result.append(dict(scenario=sc,strategy=strategy,biz=biz,common_tasks=len(common_tasks[biz]),
            common_service_seconds=s['seconds'],common_input_Gbit=s['bits']/1e9,
            latency_common_energy_J=s['latency_energy'],strategy_common_energy_J=s['other_energy'],
            common_energy_reduction_pct=100*(1-s['other_energy']/s['latency_energy']) if s['latency_energy']>1e-12 else '',
            latency_common_energy_J_per_Gbit=s['latency_energy']/bits*1e9,
            strategy_common_energy_J_per_Gbit=s['other_energy']/bits*1e9,
            latency_common_delay_ms=s['latency_delay']/secs,strategy_common_delay_ms=s['other_delay']/secs))
    return result


def summarize(out,baseline,allow_partial=False):
    all_rows=[];all_slots=[];audits=[];paired=[];pending=[]
    for sc in (1,2,3):
        for strategy in STRATEGIES:
            path=baseline/f'sc{sc}' if strategy=='latency' else out/strategy/f'sc{sc}'
            if strategy!='latency' and not (path/'comparison_audit.json').exists():
                if not allow_partial:raise FileNotFoundError(f'Run not complete: {path}; use --allow-partial for a labelled interim preview')
                pending.append(dict(scenario=sc,strategy=strategy));continue
            rows,slots,audit=result_metrics(path,strategy,sc,baseline/f'sc{sc}')
            if strategy!='latency' and not allow_partial:write_csv(path/'mixed_metrics.csv',rows)
            all_rows.extend(rows);all_slots.extend(dict(scenario=sc,strategy=strategy,**r) for r in slots);audits.append(audit)
            if strategy!='latency':paired.extend(common_comparison(baseline/f'sc{sc}',path,sc,strategy))
    summary=[r for r in all_rows if r['biz']=='ALL']
    suffix='_partial' if pending else ''
    write_csv(out/f'mixed_all_metrics{suffix}.csv',all_rows)
    write_csv(out/f'strategy_summary{suffix}.csv',summary)
    write_csv(out/f'new_strategies_metrics{suffix}.csv',[r for r in all_rows if r['strategy']!='latency'])
    write_csv(out/f'mixed_all_slot_metrics{suffix}.csv',all_slots)
    write_csv(out/f'common_service_comparison{suffix}.csv',paired)
    manifest=dict(run_groups=len(audits),new_run_groups=sum(a['strategy']!='latency' for a in audits),
        expected_run_groups=9,pending=pending,complete=not pending,metric_rows=len(all_rows),slot_rows=len(all_slots),
        word_report_modified=False,baseline=str(baseline.resolve()),runs=audits,
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'experiments/run_mixed_strategies.py','experiments/run_mixed_3600.py','simulator.py',
            'event_simulation.py','solvers/s1_dijkstra.py','solvers/base.py','tasks.py','metrics.py')})
    (out/f'comparison_manifest{suffix}.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 3600 s混合业务：三策略数据预览','',
        '本次新增负载均衡与能耗优化各3组，复用原时长、低背景＋一次短峰的逐任务输入。未修改Word/PDF报告。',
        'ACTIVE_AT_END：3600 s停止时仍在正常执行、预定结束时间晚于3600 s；不是失败，也不是完整完成。',
        '期末任务只计本观测窗口内已发生的资源占用和能耗。',
        '', '|场景|策略|接纳率|完成/期末在服/掉线|平均/峰值并发|生命周期时延均值ms|J/Gbit|链路CV|链路峰值%|',
        '|---|---|---:|---|---|---:|---:|---:|---:|']
    if pending:lines.insert(2,'进行中预览，仅包含已完成并通过审计的组；待完成：'+json.dumps(pending,ensure_ascii=False)+'。')
    for r in summary:
        lines.append(f"|{int(r['scenario'])}|{r['strategy']}|{r['initial_accept_rate']:.2%}|"
            f"{int(r['completed'])}/{int(r['active_at_end'])}/{int(r['dropped'])}|{r['time_avg_active']:.2f}/{int(r['peak_concurrency'])}|"
            f"{r['lifetime_delay_mean_ms']:.3f}|{r['energy_J_per_input_Gbit']:.3f}|{r['mean_link_cv']:.3f}|{r['peak_link_util_pct']:.3f}|")
    lines+=['','## 模型动态功率（计算＋传输，不含平台静态功耗）','',
        '|场景|策略|全网平均W|全网峰值W|计算功率峰值W|传输功率峰值W|',
        '|---|---|---:|---:|---:|---:|']
    for r in summary:
        lines.append(f"|{int(r['scenario'])}|{r['strategy']}|{r['mean_total_power_W']:.3f}|{r['peak_total_power_W']:.3f}|"
                     f"{r['peak_compute_power_W']:.3f}|{r['peak_tx_power_W']:.3f}|")
    lines+=['','## 同任务、同服务区间比较','',
        '为排除策略拒绝/掉线不同造成的能耗偏差，下面仅统计两策略都实际服务的同任务、重叠时间段。',
        '|场景|策略|共同服务任务数|相对latency节能%|latency/本策略 服务加权时延ms|',
        '|---|---|---:|---:|---|']
    for r in paired:
        if r['biz']=='ALL':lines.append(f"|{r['scenario']}|{r['strategy']}|{r['common_tasks']}|{r['common_energy_reduction_pct']:.2f}|"
            f"{r['latency_common_delay_ms']:.3f}/{r['strategy_common_delay_ms']:.3f}|")
    lines+=['',f'数据入口：strategy_summary{suffix}.csv为{len(summary)}行总体对照；mixed_all_metrics{suffix}.csv为{len(all_rows)}行总体/分业务指标；',
        f'new_strategies_metrics{suffix}.csv只含本次新增两策略；mixed_all_slot_metrics{suffix}.csv含{len(all_slots)}个时间片；',
        f'common_service_comparison{suffix}.csv提供同任务同服务区间能耗/时延对照，避免只比较总J造成误读。',
        '每个balance/scN、energy/scN目录含任务、路径、计算节点、带宽/算力/功率事件、生命周期和审计明细。',
        '峰值资源利用率按实际事件记录；J/Gbit以实际服务的原始输入量归一化；平均功率仅为业务增量项。',
        '三策略是启发式求解：balance最小化预测平方利用率增量，energy最小化计算＋传输能量；',
        '若首选路径不满足QoS，则沿用已有最小时延候选补充搜索，不放宽时延或丢包预算。',
        'latency的即时候选代价与本表生命周期最坏时延均值不是同一指标；MC候选按目的路径代价求和，',
        '而时延指标先取最坏目的、再取跨片最坏值，所以不能预设该指标必然由latency取得全局最小。',
        'balance优化链路与算力利用率平方和的增量，不保证链路、节点每一个CV/峰值指标都同时最优。',
        '平均传输功率为链路增量能耗/3600 s，不是射频发射机铭牌功率；不含平台静态功耗。',
        'energy最小化总能耗，不是单独最小化全网瞬时功率峰值；同一3600 s窗口的平均功率与总能耗同比例。',
        '本次不调算法、不改物理资源，仅切换策略；单种子结果不能当作统计显著性结论。']
    (out/('数据预览_进行中.md' if pending else '数据预览.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(groups=len(audits),metrics=len(all_rows),paired_rows=len(paired),complete=not pending,word_modified=False),ensure_ascii=True))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--strategy',choices=('balance','energy'))
    ap.add_argument('--scenario',type=int,choices=(1,2,3))
    ap.add_argument('--summarize',action='store_true')
    ap.add_argument('--allow-partial',action='store_true',help='Labelled interim summaries include completed, audited runs only')
    ap.add_argument('--baseline',type=Path,default=BASE)
    ap.add_argument('--out',type=Path,default=OUT)
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    if args.summarize:summarize(args.out,args.baseline,args.allow_partial)
    else:
        if args.strategy is None or args.scenario is None:ap.error('--strategy and --scenario are required for a run')
        run(args.strategy,args.scenario,args.out,args.baseline)


if __name__=='__main__':main()
