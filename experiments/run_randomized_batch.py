"""Fresh random workloads for every batch/scene/policy (unpaired comparison).

Prepare once with a new automatic seed; run all nine cases from that manifest.
An explicit --seed reproduces inputs. No algorithm or hardware changes are made.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import secrets
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simulator import Simulator
from experiments.run_mixed_3600 import WEIGHTS,sparse_peak_profile,write_csv,extra_outputs
from experiments.run_mixed_strategies import STRATEGIES,read,number,input_signature,result_metrics
from experiments.run_bounded_mixed import materialize_run_metrics

PREVIOUS=ROOT/'results'/'mixed_3600_bounded_all_strategies_20261016'


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def derived_seed(master,scenario,stream):
    raw=f'mixed-fresh-v1:{master}:{scenario}:{stream}'.encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:4],'big')


def restore_keys(cfg):
    for key in ('scenario_shells','scenario_geo','scenario_ground','scenario_max_concurrency'):
        cfg['constellation'][key]={int(k):v for k,v in cfg['constellation'][key].items()}
    cfg['traffic']['fixed_arrival_overrides']={int(k):v for k,v in cfg['traffic']['fixed_arrival_overrides'].items()}
    return cfg


def fresh_config(base,sc,master,strategy='balance'):
    cfg=restore_keys(json.loads(json.dumps(base)))
    seeds={stream:derived_seed(master,sc,stream if strategy=='balance' else f'{strategy}:{stream}') for stream in ('task','time','schedule')}
    cfg['simulation'].update(random_seed=seeds['task'],strategy=strategy)
    cfg['simulation'].pop('event_output_dir',None)
    traffic=cfg['traffic']
    traffic.update(time_seed=seeds['time'],independent_time_rng=True,
        random_arrivals_all_businesses=True,arrival_at_slot_start=False,truncate_at_simulation_end=True)
    sparse_peak_profile(traffic,{1:200,2:250,3:500}[sc],120,seeds['schedule'])
    return cfg,seeds


def prepare(out=None,seed=None):
    master=secrets.randbits(48) if seed is None else seed
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    out=Path(out) if out else ROOT/'results'/f'mixed_3600_random_batch_{stamp}_{master:x}'
    if out.exists() and any(out.iterdir()):raise FileExistsError(f'Refusing to overwrite existing batch: {out}')
    out.mkdir(parents=True,exist_ok=True)
    cases=prepare_cases(out,master)
    manifest=dict(batch_id=out.name,master_seed=master,seed_source='secrets' if seed is None else 'explicit_replay',
        generated_at_china=stamp,previous_batch=str(PREVIOUS),cases=cases,
        policy_pairing='independent fresh workloads for every scene AND policy; unpaired comparison',
        expected_run_groups=9,algorithm_and_hardware_changes=False,
        end_policy='duration=min(sampled_duration,3600-arrival)',complete=False)
    dump(out/'batch_manifest.json',manifest)
    print(json.dumps(dict(batch=str(out.resolve()),**manifest),ensure_ascii=True),flush=True)
    return out


def prepare_cases(out,master):
    cases=[]
    for sc in (1,2,3):
        base=json.loads((PREVIOUS/'latency'/f'sc{sc}'/'effective_config.json').read_text(encoding='utf-8'))
        for st in STRATEGIES:
            cfg,seeds=fresh_config(base,sc,master,st)
            path=out/'cases'/st/f'sc{sc}';path.mkdir(parents=True,exist_ok=True)
            dump(path/'config.json',cfg)
            traffic=cfg['traffic'];profile=traffic['arrival_profile']
            write_csv(path/'arrival_schedule.csv',[
                dict(slot=i,t_start=i*30,is_pulse_slot=int(i==60),
                     **{f'background_{b}':profile['background_counts_by_slot'][i][b] for b in WEIGHTS},
                     **{f'arrivals_{b}':traffic['fixed_arrival_overrides'][i][b] for b in WEIGHTS}) for i in range(120)])
            cases.append(dict(scenario=sc,strategy=st,seeds=seeds,offered_peak=profile['offered_peak'],
                offered_time_avg_active=profile['offered_time_avg_active'],
                background_tasks=profile['background_tasks'],pulse_tasks=profile['pulse_tasks'],
                config_sha256=hashlib.sha256((path/'config.json').read_bytes()).hexdigest()))
    assert len({v for c in cases for v in c['seeds'].values()})==27
    return cases


def independent_reconfigure(out):
    """Honor a clarified unpaired design; archive old non-balance runs separately first."""
    path=out/'batch_manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    archive=out/'preclarification_paired_reference';archive.mkdir(exist_ok=True)
    if not (archive/'batch_manifest.json').exists():dump(archive/'batch_manifest.json',manifest)
    manifest['cases']=prepare_cases(out,manifest['master_seed'])
    manifest.update(policy_pairing='independent fresh workloads for every scene AND policy; unpaired comparison',
        user_clarification='Each policy must also use independently generated business data',
        preclarification_reference_excluded=str(archive),complete=False)
    dump(path,manifest);print(json.dumps(manifest,ensure_ascii=True),flush=True)


def boundary_audit(path):
    rows=[number(r) for r in read(path/'generated_tasks.csv')]
    changes={r['task_id']:number(r) for r in read(path/'duration_adjustments.csv')}
    life={r['task_id']:number(r) for r in read(path/'lifecycle_tasks.csv')}
    assert len(rows)==len(changes)==len(life)
    counts=Counter(r['biz'] for r in rows)
    assert all(counts[b]==w*counts['MC'] for b,w in WEIGHTS.items())
    for r in rows:
        c=changes[r['task_id']];l=life[r['task_id']]
        assert 0<=r['arrival_t']<r['t_end']<=3600
        assert abs(r['duration_s']-min(c['sampled_duration_s'],3600-r['arrival_t']))<1e-7
        assert abs(r['data_volume_bits']-r['rate_mbps']*1e6*r['duration_s'])<max(1e-5,r['data_volume_bits']*1e-12)
        assert abs(r['cycles_total']-r['data_volume_bits']*r['cycles_per_bit_effective'])<max(.01,r['cycles_total']*1e-12)
        assert l['status']!='ACTIVE_AT_END'
        if l['status']=='COMPLETED':assert abs(l['service_seconds']-r['duration_s'])<1e-6
    kpi=json.loads((path/'kpi.json').read_text(encoding='utf-8'))
    cap={1:200,2:250,3:500}[int(kpi['meta']['scenario'])]
    assert kpi['max_concurrency']==cap,('Actual peak did not reach cap; retain this run and diagnose, do not reroll',path)
    assert float(read(path/'slot_stats.csv')[-1]['active'])==0
    audit=dict(all_task_intervals_within_window='PASS',no_ACTIVE_AT_END='PASS',
        data_and_operations_recomputed='PASS',exact_3_2_3_1='PASS',generated=len(rows),
        duration_clipped_tasks=sum(c['duration_clipped'] for c in changes.values()),
        max_requested_end_s=max(r['t_end'] for r in rows),final_active=0,actual_peak=cap)
    dump(path/'closed_window_audit.json',audit)
    return audit


def run(out,sc,strategy):
    manifest=json.loads((out/'batch_manifest.json').read_text(encoding='utf-8'))
    case=out/'cases'/strategy/f'sc{sc}'
    cfg=restore_keys(json.loads((case/'config.json').read_text(encoding='utf-8')))
    path=out/strategy/f'sc{sc}'
    if (path/'kpi.json').exists():raise FileExistsError(f'Completed run exists: {path}')
    path.mkdir(parents=True,exist_ok=True)
    cfg['simulation'].update(strategy=strategy,event_output_dir=str(path.resolve()))
    dump(path/'effective_config.json',cfg)
    write_csv(path/'arrival_schedule.csv',read(case/'arrival_schedule.csv'))
    sim=Simulator(cfg,scenario=sc,strategy=strategy,solver='S1',no_scale=True)
    sim.metrics.meta.update(mixed_variant='fresh_bounded_random',mixed_ratio=WEIGHTS,
        random_batch_id=manifest['batch_id'],master_seed=manifest['master_seed'])
    m=sim.run();m.export(str(path));summary=extra_outputs(sim,path)
    write_csv(path/'duration_adjustments.csv',sim.gen.duration_adjustments)
    audit=boundary_audit(path)
    dump(path/'comparison_audit.json',audit)
    print(json.dumps(dict(strategy=strategy,summary=summary,boundary_audit=audit),ensure_ascii=True),flush=True)


def finalize_exported_run(out,sc,strategy):
    """Idempotently rebuild supplemental tables from completed raw simulation CSVs.

    Useful if a process ended after its raw export but before postprocessing.
    Routing is NOT rerun and no outcomes are invented or changed.
    """
    from network import build_snapshot
    path=out/strategy/f'sc{sc}'
    kpi=json.loads((path/'kpi.json').read_text(encoding='utf-8'))
    assert kpi['meta']['observation_end_sec']==3600
    generated=[number(r) for r in read(path/'generated_tasks.csv')]
    routes=[number(r) for r in read(path/'routing_allocations.csv')]
    slots=[number(r) for r in read(path/'slot_stats.csv')]
    assert len(slots)==120 and len(generated)==kpi['total_arrived']
    tasks=[SimpleNamespace(id=r['task_id'],biz=SimpleNamespace(value=r['biz']),src=r['src'],
        dst_set=json.loads(r['dst_json']),arrival_t=r['arrival_t'],duration_s=r['duration_s'],
        t_end=r['t_end'],rate_bps=r['rate_mbps']*1e6) for r in generated]
    by_id={t.id:t for t in tasks}
    energies=defaultdict(lambda:[0.,0.]);service=defaultdict(float)
    max_delay=defaultdict(float);max_loss=defaultdict(float)
    accepted={}
    for r in routes:
        tid=r['task_id'];accepted[tid]=r
        service[tid]+=r['service_duration_s']
        energies[tid][0]+=r['energy_compute_J'];energies[tid][1]+=r['energy_tx_J']
        max_delay[tid]=max(max_delay[tid],r['max_delay_ms']);max_loss[tid]=max(max_loss[tid],r['max_loss'])
    assert abs(sum(sum(v) for v in energies.values())-kpi['total_energy_J'])<.001
    metrics=SimpleNamespace(meta=kpi['meta'],_allocation_history=routes,attempts=tasks,
        rejects=[(by_id[r['task_id']],r['reject_reason']) for r in generated if r['reject_reason']],
        task_terminal={r['task_id']:r['final_status'] for r in generated},task_energy=energies,
        task_service_seconds=service,task_max_delay=max_delay,task_max_loss=max_loss,
        max_concurrency=kpi['max_concurrency'],_slot_stats=slots,alloc_by_task=accepted,summary=lambda:kpi)
    extra_outputs(SimpleNamespace(metrics=metrics,slot=30,t_end=3600,t_start=0,scenario=sc),path)
    # Replay only the generator, not any allocation decision, to recover sampled durations.
    cfg=restore_keys(json.loads((path/'effective_config.json').read_text(encoding='utf-8')))
    sim=Simulator(cfg,scenario=sc,strategy=strategy,solver='S1',no_scale=True)
    replay=[]
    for i in range(120):
        replay.extend(sim.gen.gen_for_slot(i*30,i,build_snapshot(i*30,sim.all_nodes,cfg,sc)))
    assert len(replay)==len(tasks)
    raw_by_id={r['task_id']:r for r in generated}
    for t in replay:
        raw=raw_by_id[t.id]
        assert t.src==raw['src'] and t.dst_set==json.loads(raw['dst_json'])
        assert abs(t.arrival_t-raw['arrival_t'])<1e-8 and abs(t.duration_s-raw['duration_s'])<1e-8
        assert abs(t.rate_bps-raw['rate_mbps']*1e6)<1e-5
    write_csv(path/'duration_adjustments.csv',sim.gen.duration_adjustments)
    audit=boundary_audit(path);audit['supplemental_tables_rebuilt_from_raw_csv']=True
    dump(path/'comparison_audit.json',audit)
    print(json.dumps(dict(scenario=sc,strategy=strategy,finalized=True,audit=audit),ensure_ascii=True),flush=True)


def cross_batch(out):
    old=read(PREVIOUS/'mixed_all_metrics.csv');new=read(out/'mixed_all_metrics.csv')
    indexed={(int(float(r['scenario'])),r['strategy'],r['biz']):number(r) for r in old}
    keys=('generated','completed','dropped','rejected_cap','rejected_no_feasible','initial_accept_rate',
        'final_survival_rate','time_avg_active','lifetime_delay_mean_ms','lifetime_delay_p95_ms',
        'energy_J_per_input_Gbit','energy_total_J','observed_input_Gbit','mean_link_cv','mean_node_cv',
        'peak_link_util_pct','peak_node_util_pct','mean_total_power_W','peak_total_power_W',
        'migrations','source_compute_segment_pct','solve_p99_ms')
    comparisons=[]
    for raw in new:
        b=number(raw);a=indexed[int(b['scenario']),b['strategy'],b['biz']]
        row=dict(scenario=int(b['scenario']),strategy=b['strategy'],biz=b['biz'],comparison='unpaired_different_workloads')
        for key in keys:
            row['batch_A_'+key]=a[key];row['batch_B_'+key]=b[key]
            row['change_pct_'+key]=100*(b[key]/a[key]-1) if isinstance(a[key],(int,float)) and a[key]!=0 and isinstance(b[key],(int,float)) else ''
        comparisons.append(row)
    write_csv(out/'cross_batch_comparison.csv',comparisons)
    write_csv(out/'two_batch_metrics.csv',[dict(batch=batch,**r) for batch,group in (('A',old),('B',new)) for r in group])


def summarize_batch(out):
    all_rows=[];all_slots=[];result_audits=[];workloads=[]
    manifest=json.loads((out/'batch_manifest.json').read_text(encoding='utf-8'))
    assert len({seed for c in manifest['cases'] for seed in c['seeds'].values()})==27
    for sc in (1,2,3):
        for st in STRATEGIES:
            path=out/st/f'sc{sc}';boundary_audit(path)
            actual=json.loads((path/'effective_config.json').read_text(encoding='utf-8'))
            planned=json.loads((out/'cases'/st/f'sc{sc}'/'config.json').read_text(encoding='utf-8'))
            actual['simulation'].pop('event_output_dir',None)
            assert actual==planned,('Actual run does not use the frozen independent input config',sc,st)
            rows,slots,audit=result_metrics(path,st,sc,path)
            audit.pop('identical_inputs');audit.pop('identical_physical_config')
            audit.update(comparison_design='independent_policy_inputs',self_consistency='PASS')
            all_rows.extend(rows);all_slots.extend(dict(scenario=sc,strategy=st,**r) for r in slots)
            result_audits.append(audit)
            dump(path/'comparison_audit.json',audit)
            generated=[number(r) for r in read(path/'generated_tasks.csv')]
            node_types={n.id:n.type.value for n in Simulator(restore_keys(json.loads(json.dumps(actual))),scenario=sc,no_scale=True).all_nodes}
            case=next(c for c in manifest['cases'] if c['scenario']==sc and c['strategy']==st)
            assert abs(sum(r['duration_s'] for r in generated)/3600-case['offered_time_avg_active'])<1e-7
            for biz in ('ALL','LL','HR','HC','MC'):
                tasks=[r for r in generated if biz=='ALL' or r['biz']==biz]
                assert all(node_types[r['src']] in ('LEO','GEO') and all(node_types[d] in ('LEO','GEO') for d in json.loads(r['dst_json'])) for r in tasks)
                assert all(node_types[r['src']]=='LEO' and all(node_types[d]=='LEO' for d in json.loads(r['dst_json'])) for r in tasks if r['biz']=='LL')
                workloads.append(dict(scenario=sc,strategy=st,biz=biz,generated=len(tasks),
                    offered_input_Gbit=sum(r['data_volume_bits'] for r in tasks)/1e9,
                    offered_service_seconds=sum(r['duration_s'] for r in tasks),
                    offered_time_avg_active=sum(r['duration_s'] for r in tasks)/3600,
                    mean_rate_mbps=sum(r['rate_mbps'] for r in tasks)/len(tasks),
                    mean_actual_duration_s=sum(r['duration_s'] for r in tasks)/len(tasks),
                    distinct_sources=len({r['src'] for r in tasks}),
                    distinct_destination_sets=len({r['dst_json'] for r in tasks})))
    write_csv(out/'mixed_all_metrics.csv',all_rows)
    write_csv(out/'strategy_summary.csv',[r for r in all_rows if r['biz']=='ALL'])
    write_csv(out/'mixed_all_slot_metrics.csv',all_slots)
    write_csv(out/'workload_summary.csv',workloads)
    materialize_run_metrics(out)
    audits=[]
    for sc in (1,2,3):
        hashes={st:input_signature(read(out/st/f'sc{sc}'/'generated_tasks.csv')) for st in STRATEGIES}
        prior=input_signature(read(PREVIOUS/'latency'/f'sc{sc}'/'generated_tasks.csv'))
        assert len(set(hashes.values()))==3 and prior not in hashes.values()
        # Physical configuration and algorithm settings must be unchanged from batch A.
        a=json.loads((PREVIOUS/'latency'/f'sc{sc}'/'effective_config.json').read_text(encoding='utf-8'))
        for st in STRATEGIES:
            b=json.loads((out/st/f'sc{sc}'/'effective_config.json').read_text(encoding='utf-8'))
            configs=json.loads(json.dumps([a,b]))
            for cfg in configs:
                for key in ('random_seed','event_output_dir','strategy'):cfg['simulation'].pop(key,None)
                for key in ('time_seed','fixed_arrival_overrides','arrival_profile'):cfg['traffic'].pop(key)
            assert configs[0]==configs[1],('Unexpected non-randomization change',sc,st)
            audits.append(dict(scenario=sc,strategy=st,batch_A_sha256=prior,batch_B_sha256=hashes[st],
                differs_from_previous='PASS',three_strategies_different='PASS',unchanged_physics_and_algorithms='PASS'))
    assert len({r['batch_B_sha256'] for r in audits})==9
    write_csv(out/'batch_input_audit.csv',audits)
    cross_batch(out)
    mp=out/'comparison_manifest.json';meta=dict(run_groups=9,expected_run_groups=9,complete=True,
        new_run_groups=9,reused_run_groups=0,all_strategies_actually_rerun=True,input_audits=audits,
        metric_rows=45,slot_rows=1080,word_report_modified=False,runs=result_audits,
        comparison_design='independent workloads per scenario and policy; NOT paired',
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
            'experiments/run_randomized_batch.py','experiments/run_mixed_3600.py','tasks.py',
            'simulator.py','event_simulation.py','solvers/s1_dijkstra.py','metrics.py')})
    dump(mp,meta)
    mp=out/'batch_manifest.json';manifest=json.loads(mp.read_text(encoding='utf-8'))
    manifest.update(complete=True,input_audits=audits)
    dump(mp,manifest)
    summaries={(int(r['scenario']),r['strategy']):r for r in all_rows if r['biz']=='ALL'}
    contrasts=[]
    for sc in (1,2,3):
        for st in ('balance','energy'):
            l=summaries[sc,'latency'];s=summaries[sc,st]
            contrasts.append(dict(scenario=sc,strategy=st,comparison='unpaired_independent_workloads_not_causal_savings',
                latency_generated=l['generated'],strategy_generated=s['generated'],
                latency_input_Gbit=l['observed_input_Gbit'],strategy_input_Gbit=s['observed_input_Gbit'],
                latency_energy_J_per_Gbit=l['energy_J_per_input_Gbit'],strategy_energy_J_per_Gbit=s['energy_J_per_input_Gbit'],
                normalized_energy_difference_pct=100*(s['energy_J_per_input_Gbit']/l['energy_J_per_input_Gbit']-1),
                latency_delay_ms=l['lifetime_delay_mean_ms'],strategy_delay_ms=s['lifetime_delay_mean_ms']))
    write_csv(out/'unpaired_strategy_comparison.csv',contrasts)
    lines=['# 新随机业务批次B：每场景、每策略独立输入','',
        '九组任务输入均不同；非配对结果，差值同时受算法和任务输入影响，不能解释成同任务节能率。',
        '所有任务结束≤3600 s，期末在服0；种子见batch_manifest.json。','',
        '|场景|策略|完成/生成|平均/峰值并发|时延均值ms|J/Gbit|链路CV|链路峰值%|',
        '|---|---|---|---|---:|---:|---:|---:|']
    for (sc,st),r in summaries.items():
        lines.append(f"|{sc}|{st}|{int(r['completed'])}/{int(r['generated'])}|{r['time_avg_active']:.2f}/{int(r['peak_concurrency'])}|"
            f"{r['lifetime_delay_mean_ms']:.3f}|{r['energy_J_per_input_Gbit']:.3f}|{r['mean_link_cv']:.3f}|{r['peak_link_util_pct']:.3f}|")
    lines+=['','完整45行指标：mixed_all_metrics.csv；跨批次45行：cross_batch_comparison.csv；两批90行：two_batch_metrics.csv。',
        'unpaired_strategy_comparison.csv仅给非配对的归一化能耗差值，不进行跨输入task_id匹配。',
        '澄清前的配对参考数据隔离在preclarification_paired_reference，未进入正式统计。']
    (out/'数据预览.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    group=ap.add_mutually_exclusive_group();group.add_argument('--prepare',action='store_true');group.add_argument('--summarize',action='store_true');group.add_argument('--finalize',action='store_true');group.add_argument('--independent-reconfigure',action='store_true')
    ap.add_argument('--batch',type=Path);ap.add_argument('--seed',type=int)
    ap.add_argument('--scenario',type=int,choices=(1,2,3));ap.add_argument('--strategy',choices=STRATEGIES)
    args=ap.parse_args()
    if args.prepare:prepare(args.batch,args.seed)
    elif not args.batch:ap.error('--batch is required after preparation')
    elif args.independent_reconfigure:independent_reconfigure(args.batch)
    elif args.summarize:summarize_batch(args.batch)
    elif args.finalize and args.scenario and args.strategy:finalize_exported_run(args.batch,args.scenario,args.strategy)
    elif args.scenario and args.strategy:run(args.batch,args.scenario,args.strategy)
    else:ap.error('Use --prepare, --summarize, or --scenario/--strategy')


if __name__=='__main__':main()
