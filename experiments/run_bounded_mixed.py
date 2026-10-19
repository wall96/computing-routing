"""3600-second closed-window mixed workloads: nine actual simulation runs."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from simulator import Simulator
from experiments.run_mixed_3600 import extra_outputs,write_csv
from experiments.run_mixed_strategies import read,number,summarize,STRATEGIES

SOURCE=ROOT/'results'/'mixed_3600_latency_sparse_peak_20261016'/'variable_random'
OUT=ROOT/'results'/'mixed_3600_bounded_all_strategies_20261016'


def source_for(sc,out):
    archived=out/'input_reference'/f'sc{sc}'
    return archived if archived.exists() else SOURCE/f'sc{sc}'


def archive_reference(out):
    """Retain the original sampled durations even if earlier run folders change."""
    manifest={}
    for sc in (1,2,3):
        target=out/'input_reference'/f'sc{sc}';target.mkdir(parents=True,exist_ok=True)
        manifest[f'sc{sc}']={}
        for name in ('effective_config.json','generated_tasks.csv','arrival_schedule.csv'):
            path=target/name
            if not path.exists():shutil.copy2(SOURCE/f'sc{sc}'/name,path)
            manifest[f'sc{sc}'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (out/'input_reference_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


def materialize_run_metrics(out):
    rows=read(out/'mixed_all_metrics.csv')
    for sc in (1,2,3):
        for strategy in STRATEGIES:
            selected=[r for r in rows if int(float(r['scenario']))==sc and r['strategy']==strategy]
            assert len(selected)==5
            write_csv(out/strategy/f'sc{sc}'/'mixed_metrics.csv',selected)


def closed_window_audit(path,source):
    rows=read(path/'generated_tasks.csv');old={r['task_id']:r for r in read(source/'generated_tasks.csv')}
    changes={r['task_id']:number(r) for r in read(path/'duration_adjustments.csv')}
    lives={r['task_id']:number(r) for r in read(path/'lifecycle_tasks.csv')}
    assert len(rows)==len(old)==len(changes)==len(lives)
    changed=[]
    ignored={'duration_s','t_end','cycles_total','data_volume_bits','final_status','reject_reason'}
    for raw in rows:
        tid=raw['task_id'];r=number(raw);prior=old[tid];c=changes[tid]
        assert {k:v for k,v in raw.items() if k not in ignored}=={k:v for k,v in prior.items() if k not in ignored}
        expected=min(float(prior['duration_s']),3600-r['arrival_t'])
        assert abs(r['duration_s']-expected)<1e-8
        assert 0<=r['arrival_t']<r['t_end']<=3600
        assert abs(r['data_volume_bits']-r['rate_mbps']*1e6*r['duration_s'])<max(1e-5,r['data_volume_bits']*1e-12)
        assert abs(r['cycles_total']-r['data_volume_bits']*r['cycles_per_bit_effective'])<max(.01,r['cycles_total']*1e-12)
        assert abs(c['sampled_duration_s']-float(prior['duration_s']))<1e-8
        assert lives[tid]['status']!='ACTIVE_AT_END'
        if lives[tid]['initially_accepted'] and lives[tid]['status']!='DROPPED':
            assert lives[tid]['status']=='COMPLETED'
            assert abs(lives[tid]['service_seconds']-r['duration_s'])<1e-6
        if c['duration_clipped']:changed.append(tid)
    kpi=json.loads((path/'kpi.json').read_text(encoding='utf-8'))
    assert kpi['max_concurrency']=={1:200,2:250,3:500}[int(kpi['meta']['scenario'])]
    slots=read(path/'slot_stats.csv')
    assert float(slots[-1]['active'])==0
    result=dict(all_task_intervals_within_window='PASS',no_ACTIVE_AT_END='PASS',
        input_preserved_except_bounded_duration='PASS',data_and_operations_recomputed='PASS',
        all_surviving_admissions_completed='PASS',generated=len(rows),duration_clipped_tasks=len(changed),
        max_requested_end_s=max(float(r['t_end']) for r in rows),final_active=0,
        source_dir=str(source.resolve()),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (path/'closed_window_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def run(strategy,sc,out):
    source=source_for(sc,out);path=out/strategy/f'sc{sc}';path.mkdir(parents=True,exist_ok=True)
    cfg=json.loads((source/'effective_config.json').read_text(encoding='utf-8'))
    for key in ('scenario_shells','scenario_geo','scenario_ground','scenario_max_concurrency'):
        cfg['constellation'][key]={int(k):v for k,v in cfg['constellation'][key].items()}
    cfg['simulation'].update(strategy=strategy,event_output_dir=str(path.resolve()))
    cfg['traffic']['truncate_at_simulation_end']=True
    (path/'effective_config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv(path/'arrival_schedule.csv',read(source/'arrival_schedule.csv'))
    sim=Simulator(cfg,scenario=sc,strategy=strategy,solver='S1')
    sim.metrics.meta.update(mixed_variant='bounded_random',mixed_ratio={'LL':3,'HR':2,'HC':3,'MC':1},
        task_end_policy='duration=min(sampled_duration,simulation_end-arrival)',source_input=str(source.resolve()))
    m=sim.run();m.export(str(path));summary=extra_outputs(sim,path)
    write_csv(path/'duration_adjustments.csv',sim.gen.duration_adjustments)
    audit=closed_window_audit(path,source)
    assert summary['active_at_end']==0
    print(json.dumps(dict(strategy=strategy,summary=summary,boundary_audit=audit),ensure_ascii=True),flush=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--strategy',choices=STRATEGIES)
    ap.add_argument('--scenario',type=int,choices=(1,2,3))
    ap.add_argument('--summarize',action='store_true')
    ap.add_argument('--out',type=Path,default=OUT)
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    if args.summarize:
        archive_reference(args.out)
        audits=[]
        for sc in (1,2,3):
            for strategy in STRATEGIES:
                path=args.out/strategy/f'sc{sc}'
                audits.append(dict(scenario=sc,strategy=strategy,**closed_window_audit(path,source_for(sc,args.out))))
                # The general comparator requires a completed marker.
                (path/'comparison_audit.json').write_text(json.dumps(audits[-1],ensure_ascii=False,indent=2),encoding='utf-8')
        summarize(args.out,args.out/'latency')
        materialize_run_metrics(args.out)
        meta_path=args.out/'comparison_manifest.json'
        meta=json.loads(meta_path.read_text(encoding='utf-8'))
        meta.update(new_run_groups=9,reused_run_groups=0,all_strategies_actually_rerun=True,
            task_end_policy='generation_time_duration_cap',input_reference=str(args.out/'input_reference'))
        meta['source_sha256']['experiments/run_bounded_mixed.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        meta_path.write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        preview=args.out/'数据预览.md'
        text=preview.read_text(encoding='utf-8')
        text=text.replace('本次新增负载均衡与能耗优化各3组，复用原时长、低背景＋一次短峰的逐任务输入。未修改Word/PDF报告。',
            '本次三场景×三策略9组全部重新仿真，采用相同的低背景＋一次短峰输入，生成时将实际业务结束时刻限制在3600 s内。Word报告由build_bounded_mixed_report.py替换旧补充章。')
        text=text.replace('ACTIVE_AT_END：3600 s停止时仍在正常执行、预定结束时间晚于3600 s；不是失败，也不是完整完成。',
            '本次不允许ACTIVE_AT_END：全部生成任务满足arrival_t+duration_s≤3600，未掉线的已接纳业务均须完整完成。')
        text=text.replace('期末任务只计本观测窗口内已发生的资源占用和能耗。',
            '实际数据量及总计算量在生成时随配置时长重算；原抽样时长保存在duration_adjustments.csv中，仅用于审计。')
        preview.write_text(text,encoding='utf-8')
        (args.out/'closed_window_manifest.json').write_text(json.dumps(dict(runs=audits,
            boundary_policy='generation_time_duration_cap',all_nine_runs_inside_3600='PASS'),ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        if args.strategy is None or args.scenario is None:ap.error('--strategy and --scenario are required')
        run(args.strategy,args.scenario,args.out)


if __name__=='__main__':main()
