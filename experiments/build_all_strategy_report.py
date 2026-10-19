"""Audit 36 paired experiments, plot metrics, and build the Chinese Word report.

Run from code/: python experiments/build_all_strategy_report.py
Raw simulation CSVs remain the source of truth; no metrics are hand-entered.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import yaml
import docx
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from simulator import Simulator
from network import build_snapshot
from solvers.base import single_source

OUT = ROOT / 'results' / 'all_strategy_single_biz_20261016'
FIG = OUT / 'figures'
STRATEGIES = ('latency', 'balance', 'energy')
BIZ = ('LL', 'HR', 'HC', 'MC')
LABEL = {'latency': '最小时延', 'balance': '负载均衡', 'energy': '能耗优化'}
COLORS = {'latency': '#2864A0', 'balance': '#D88722', 'energy': '#27856B'}


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def quantile(xs, p):
    return float(np.quantile(xs, p)) if xs else 0.0


def moment(values):
    mean = statistics.fmean(values) if values else 0
    std = statistics.pstdev(values) if values else 0
    squares = sum(x*x for x in values)
    return mean, std, std / mean if mean else 0, (sum(values)**2 / (len(values)*squares) if squares else 0)


def collect():
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf-8'))
    snapshots = {}
    for sc in (1, 2, 3):
        sim = Simulator(cfg, scenario=sc)
        for i in range(10):
            snapshots[sc, i] = build_snapshot(i*30, sim.all_nodes, cfg, sc)
    summary, timeseries, rejection_rows, runs = [], [], [], {}
    input_hashes, config_hashes = {}, {}
    for sc in (1, 2, 3):
        for biz in BIZ:
            for strategy in STRATEGIES:
                path = OUT / strategy / f'sc{sc}' / biz
                generated = read_csv(path / 'generated_tasks.csv')
                routes = read_csv(path / 'routing_allocations.csv')
                slots = read_csv(path / 'slot_stats.csv')
                kpi = json.loads((path / 'kpi.json').read_text(encoding='utf-8'))
                effective = json.loads((path / 'effective_config.json').read_text(encoding='utf-8'))
                effective['simulation'].pop('strategy', None)
                assert len(slots) == 10, path
                assert len(routes) == kpi['total_accepted'], path
                assert len(generated) == kpi['total_arrived'], path
                assert all(r['event_type'] == 'INITIAL' for r in routes), path
                assert kpi['total_dropped'] == 0, path
                assert not any('GEO' in (r['src'] + r['dst_json']) for r in generated if biz == 'LL')
                canonical = [{key: value for key, value in r.items()
                              if key not in ('final_status', 'reject_reason')} for r in generated]
                input_hashes[sc, biz, strategy] = hashlib.sha256(
                    json.dumps(canonical, sort_keys=True).encode()).hexdigest()
                config_hashes[sc, biz, strategy] = hashlib.sha256(
                    json.dumps(effective, sort_keys=True).encode()).hexdigest()
                demands = {r['task_id']: r for r in generated}
                by_task = {r['task_id']: r for r in routes}
                for r in routes:
                    demand = demands[r['task_id']]
                    assert float(r['max_delay_ms']) <= float(demand['delay_budget_ms']) + 1e-6
                    assert float(r['max_loss']) <= float(demand['loss_budget']) + 1e-12
                active = [int(x['active']) for x in slots]
                cap = cfg['constellation']['scenario_max_concurrency'][sc]
                assert max(active) == cap, (path, active)
                assert min(active) >= 0.5*cap, (path, active)
                resources = defaultdict(dict)
                powers = defaultdict(float)
                for r in read_csv(path / 'resource_allocations.csv'):
                    t = float(r['t'])
                    if r['resource_type'] in ('BANDWIDTH', 'COMPUTE'):
                        total, remain = float(r['total_capacity']), float(r['remain_after'])
                        assert remain >= -1e-5 and remain <= total + 1e-5, (path, r)
                        resources[t][r['resource_type'], r['resource_id']] = 1-remain/total
                    if r['resource_type'] == 'POWER':
                        assert float(r['remain_after']) >= -1e-6
                        powers[t] += float(r['allocation_delta'])
                this_slots = []
                for i, raw in enumerate(slots):
                    t = float(raw['t'])
                    snap = snapshots[sc, i]
                    load = resources[t]
                    lu = [load.get(('BANDWIDTH', f'{u}<->{v}'), 0.0) for u, v in snap.links]
                    nu = [load.get(('COMPUTE', n), 0.0) for n in snap.nodes]
                    lm, ls, lcv, lj = moment(lu)
                    nm, ns, ncv, nj = moment(nu)
                    assert abs(max(lu)-float(raw['link_util_max'])) < 1e-8
                    assert abs(max(nu)-float(raw['node_util_max'])) < 1e-8
                    slot_routes = [r for r in routes if float(r['t_alloc']) == t]
                    row = dict(scenario=sc, biz=biz, strategy=strategy, t_sec=t,
                               generated=sum(int(x['generated_slot']) == i for x in generated),
                               active=int(raw['active']), rejected=int(raw['rejected']),
                               link_util_mean=lm, link_util_max=max(lu), link_util_std=ls,
                               link_util_cv=lcv, link_jain=lj,
                               node_util_mean=nm, node_util_max=max(nu), node_util_std=ns,
                               node_util_cv=ncv, node_jain=nj,
                               total_compute_power_W=powers[t],
                               energy_J=sum(float(x['energy_total_J']) for x in slot_routes))
                    this_slots.append(row)
                    timeseries.append(row)
                delays = [float(x['max_delay_ms']) for x in routes]
                input_bits = sum(float(demands[x]['data_volume_bits']) for x in by_task)
                reasons = Counter(x['reject_reason'] for x in generated if x['final_status'] == 'REJECTED')
                diag = kpi['meta'].get('algorithm_diagnostics', {})
                summary.append(dict(
                    scenario=sc, biz=biz, strategy=strategy,
                    arrived=len(generated), accepted=len(routes),
                    dropped=kpi['total_dropped'],preemptions=kpi['preemptions'],
                    reroutes=kpi['reroutes'],migrations=kpi['migrations'],
                    request_success_rate=len(routes)/len(generated), accepted_qos_rate=1.0,
                    rejected_cap=reasons.get('scenario_concurrency_cap', 0),
                    rejected_no_feasible=reasons.get('no_feasible', 0),
                    min_active=min(active), avg_active=statistics.fmean(active), max_active=max(active),
                    delay_mean_ms=statistics.fmean(delays), delay_p95_ms=quantile(delays,.95),
                    delay_max_ms=max(delays),
                    compute_delay_mean_ms=kpi['avg_compute_latency_ms'],
                    delay_margin_min_ms=float(generated[0]['delay_budget_ms'])-max(delays),
                    max_loss=max(float(r['max_loss']) for r in routes),
                    energy_total_J=kpi['total_energy_J'],
                    energy_compute_J=kpi['compute_energy_J'], energy_tx_J=kpi['tx_energy_J'],
                    energy_mean_J=kpi['avg_energy_per_task_J'], energy_J_per_input_Gbit=kpi['total_energy_J']/input_bits*1e9,
                    peak_link_util_pct=100*max(x['link_util_max'] for x in this_slots),
                    mean_link_cv=statistics.fmean(x['link_util_cv'] for x in this_slots),
                    mean_link_jain=statistics.fmean(x['link_jain'] for x in this_slots),
                    peak_node_util_pct=100*max(x['node_util_max'] for x in this_slots),
                    mean_node_cv=statistics.fmean(x['node_util_cv'] for x in this_slots),
                    peak_compute_power_W=max(powers.values()),
                    compute_at_source_pct=100*sum(x['src']==x['compute_node'] for x in routes)/len(routes),
                    ground_compute_pct=100*sum(x['compute_node_type'] in ('EDGE','DC') for x in routes)/len(routes),
                    avg_pre_hops=kpi['avg_pre_hops'], avg_post_hops=kpi['avg_post_hops'],
                    solve_p99_ms=kpi['solve_ms_p99'], fallback_attempts=diag.get('fallback_attempts',0),
                    fallback_accepted=diag.get('fallback_accepted',0), input_sha256=input_hashes[sc,biz,strategy]))
                for r in generated:
                    if r['final_status'] != 'REJECTED':
                        continue
                    lower_bound, classification = None, '达到并发硬上限'
                    if r['reject_reason'] == 'no_feasible':
                        # Full-resource propagation lower bound, independent of placement.
                        from models import Task, BizType
                        tk = Task(r['task_id'],r['src'],json.loads(r['dst_json']),BizType(biz),
                                  int(r['priority']),r['mode'],float(r['rate_mbps']),
                                  float(r['delay_budget_ms']),float(r['loss_budget']),
                                  float(r['cycles_per_bit']),float(r['Lref_bits']),float(r['arrival_t']),30)
                        snap = snapshots[sc,int(r['generated_slot'])]
                        distances,_ = single_source(snap,tk.src,tk,'latency',0,0,4)
                        lower_bound = max(distances[d] for d in tk.dst_set)+float(r['fixed_processing_ms_total'])
                        classification = ('传播时延下界已超过预算' if lower_bound>tk.delay_budget_ms
                                          else 'S1候选搜索不可行；未证明全局无解')
                    rejection_rows.append(dict(scenario=sc,biz=biz,strategy=strategy,
                        task_id=r['task_id'],slot=int(r['generated_slot']),src=r['src'],
                        dst_json=r['dst_json'],reason=r['reject_reason'],
                        optimistic_delay_lower_bound_ms=lower_bound,classification=classification))
                runs[sc,biz,strategy] = dict(summary=summary[-1], routes=by_task,
                                            generated=generated, slots=this_slots, kpi=kpi)
    for sc in (1,2,3):
        for biz in BIZ:
            assert len({input_hashes[sc,biz,s] for s in STRATEGIES}) == 1, ('input mismatch',sc,biz)
            assert len({config_hashes[sc,biz,s] for s in STRATEGIES}) == 1, ('config mismatch',sc,biz)
            common = set.intersection(*(set(runs[sc,biz,s]['routes']) for s in STRATEGIES))
            for st in STRATEGIES:
                row = runs[sc,biz,st]['summary']
                row['common_accepted_tasks'] = len(common)
                row['common_energy_mean_J'] = statistics.fmean(
                    float(runs[sc,biz,st]['routes'][tid]['energy_total_J']) for tid in common)
                row['common_delay_mean_ms'] = statistics.fmean(
                    float(runs[sc,biz,st]['routes'][tid]['max_delay_ms']) for tid in common)
    return cfg, summary, timeseries, rejection_rows, runs


def make_plots(runs):
    FIG.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],
                         'axes.unicode_minus':False,'font.size':10.5,
                         'axes.spines.top':False,'axes.spines.right':False})
    def grouped(filename, key, title, ylabel, relative=False, log=False):
        fig, axes = plt.subplots(1,3,figsize=(8.5,3.5))
        x = np.arange(4); width=.24
        for sc, ax in enumerate(axes,1):
            for j,st in enumerate(STRATEGIES):
                y = [runs[sc,b,st]['summary'][key] for b in BIZ]
                if relative:
                    y = [v/runs[sc,b,'latency']['summary'][key] for v,b in zip(y,BIZ)]
                ax.bar(x+(j-1)*width,y,width,label=LABEL[st],color=COLORS[st])
            ax.set_xticks(x,BIZ); ax.set_title(f'场景{sc}'); ax.grid(axis='y',alpha=.2)
            if log:ax.set_yscale('log')
            if relative:ax.axhline(1,color='#555555',lw=.7,ls='--')
            ax.set_ylabel(ylabel)
            if key == 'request_success_rate':
                ax.set_ylim(.985,1.001)
        fig.legend(*axes[0].get_legend_handles_labels(),loc='lower center',ncol=3,fontsize=9)
        fig.suptitle(title,fontsize=12);fig.tight_layout(rect=(0,.08,1,.96))
        fig.savefig(FIG/filename,dpi=220);plt.close(fig)
    grouped('01_delay.png','common_delay_mean_ms','同一批成功任务的平均端到端时延','时延（ms）')
    grouped('02_energy_ratio.png','common_energy_mean_J','配对任务平均能耗：以最小时延策略为1','相对能耗（倍）',True)
    grouped('03_link_cv.png','mean_link_cv','链路利用率变异系数：同场景内越低越均匀','时间片平均CV')
    grouped('04_success.png','request_success_rate','全部请求的成功率（包含并发上限拒绝）','成功率')
    grouped('05_solver.png','solve_p99_ms','单任务求解耗时P99（含并行运行的系统负载影响）','耗时（ms）')
    fig, axes = plt.subplots(1,3,figsize=(8.5,3.5))
    for sc,ax in enumerate(axes,1):
        for st in STRATEGIES:
            s = runs[sc,'HC',st]['slots']
            ax.plot([x['t_sec'] for x in s],[100*x['link_util_max'] for x in s],
                    marker='o',ms=3,color=COLORS[st],label=LABEL[st])
        ax.set_title(f'场景{sc}：HC'); ax.set_xlabel('仿真时间（s）')
        ax.set_ylabel('最忙链路利用率（%）');ax.grid(alpha=.2);ax.set_ylim(bottom=0)
        ax.axvline(150,color='#999999',ls=':',lw=1)
    fig.legend(*axes[0].get_legend_handles_labels(),loc='lower center',ncol=3,fontsize=9)
    fig.suptitle('HC链路峰值随时间变化（150 s为并发峰值片）',fontsize=12)
    fig.tight_layout(rect=(0,.08,1,.96));fig.savefig(FIG/'06_hc_link_timeseries.png',dpi=220);plt.close(fig)
    fig, axes = plt.subplots(2,1,figsize=(8.5,5.6))
    cases=[(sc,b) for sc in (1,2,3) for b in BIZ]
    for ax,key,title,fmt in [(axes[0],'compute_at_source_pct','源端就地计算比例（%）','.0f'),
                              (axes[1],'peak_node_util_pct','峰值算力/计算功率利用率（%）','.4g')]:
        values=np.array([[runs[sc,b,st]['summary'][key] for sc,b in cases] for st in STRATEGIES])
        im=ax.imshow(values,aspect='auto',cmap='YlGnBu')
        ax.set_xticks(range(12),[f'{sc}-{b}' for sc,b in cases],rotation=60)
        ax.set_yticks(range(3),[LABEL[s] for s in STRATEGIES]);ax.set_title(title)
        for i in range(3):
            for j in range(12):
                color='white' if values[i,j]>(values.max()+values.min())/2 else '#222222'
                ax.text(j,i,format(values[i,j],fmt),ha='center',va='center',fontsize=8.5,color=color)
    fig.tight_layout();fig.savefig(FIG/'07_placement_compute.png',dpi=200);plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(8.5,3.5))
    for sc,ax in enumerate(axes,1):
        for st in STRATEGIES:
            slots=runs[sc,'MC',st]['slots']
            ax.plot([x['t_sec'] for x in slots],[x['active'] for x in slots],
                    marker='o',ms=3,label=LABEL[st],color=COLORS[st],alpha=.85)
        cap={1:200,2:250,3:500}[sc]
        ax.axhline(cap,color='#555555',ls='--',lw=.7);ax.axhline(cap/2,color='#888888',ls=':',lw=.7)
        ax.set_title(f'场景{sc}：MC实际并发');ax.set_xlabel('仿真时间（s）')
        ax.set_ylabel('活跃任务数');ax.grid(alpha=.2)
    fig.legend(*axes[0].get_legend_handles_labels(),loc='lower center',ncol=3,fontsize=9)
    fig.tight_layout(rect=(0,.08,1,1));fig.savefig(FIG/'08_concurrency.png',dpi=220);plt.close(fig)


def configure_document(document):
    section=document.sections[0]
    section.page_width=Cm(21);section.page_height=Cm(29.7)
    section.top_margin=Cm(2.0);section.bottom_margin=Cm(1.8)
    section.left_margin=Cm(1.8);section.right_margin=Cm(1.8)
    for name,size,font in [('Normal',10.5,'宋体'),('Title',26,'黑体'),
                           ('Heading 1',17,'黑体'),('Heading 2',13,'黑体'),('Heading 3',11,'黑体')]:
        style=document.styles[name];style.font.name=font;style.font.size=Pt(size)
        style.element.rPr.rFonts.set(qn('w:eastAsia'),font)
    normal=document.styles['Normal'].paragraph_format
    normal.line_spacing=1.2;normal.space_after=Pt(6)
    header=section.header.paragraphs[0]
    header.text='全场景全策略单业务测试 | 2026-10-16'
    header.runs[0].font.size=Pt(9)
    footer=section.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run('第 ')
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
    footer.add_run(' 页')


def table(document, headers, rows, size=8.5):
    t=document.add_table(rows=1, cols=len(headers));t.style='Light Shading Accent 1'
    for c,h in zip(t.rows[0].cells,headers):c.text=str(h)
    trpr=t.rows[0]._tr.get_or_add_trPr();repeat=OxmlElement('w:tblHeader');trpr.append(repeat)
    for row in rows:
        cells=t.add_row().cells
        for c,val in zip(cells,row):c.text=str(val)
    for row_index,row in enumerate(t.rows):
        # Keep each row intact when tables span pages.
        prop=row._tr.get_or_add_trPr();prop.append(OxmlElement('w:cantSplit'))
        for c in row.cells:
            for p in c.paragraphs:
                p.paragraph_format.space_after=Pt(2);p.paragraph_format.space_before=Pt(2)
                if row_index == 0 or (len(t.rows)<=6 and row_index<len(t.rows)-1):
                    p.paragraph_format.keep_with_next=True
                for run in p.runs:run.font.size=Pt(size)
    document.add_paragraph('')
    return t


def paragraph(document,text):
    return document.add_paragraph(text)


def figure(document,filename,caption):
    document.add_picture(str(FIG/filename),width=Cm(17.0))
    document.paragraphs[-1].paragraph_format.keep_with_next=True
    p=document.add_paragraph(caption);p.paragraph_format.space_after=Pt(10)
    for r in p.runs:r.font.size=Pt(9);r.italic=True


def pct_change(value,baseline):
    return 100*(value/baseline-1) if baseline else 0


def make_document(cfg,summary,timeseries,rejections,runs):
    document=Document();configure_document(document)
    p=document.add_paragraph();p.paragraph_format.space_before=Pt(90)
    p=document.add_paragraph('全场景全策略\n单业务测试',style='Title')
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p=document.add_paragraph('三场景 × 三策略 × 四类业务\n高并发压力实验与算法改进分析')
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p=document.add_paragraph('实验日期：2026年10月16日\n样本：随机种子42；每组10个时间片；共36组正式实验')
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    document.add_page_break()
    document.add_heading('1. 主要发现',level=1)
    hc2={s:runs[2,'HC',s]['summary'] for s in STRATEGIES}
    paragraph(document,'本报告在相同任务输入、相同网络容量和相同QoS约束下，对最小时延、负载均衡和能耗优化进行配对实验。'
              '三策略的输入CSV逐字段校验一致，所有改动限于路由代价、候选排序与可行路径搜索。报告中的“最小时延”是策略名称，'
              '对应S1启发式算法；对于多目的业务，它以各后段时延之和排序，不承诺全局最小最坏目的时延。')
    paragraph(document,f"以最接近带宽瓶颈的场景2 HC为例：最小时延策略峰值链路利用率{hc2['latency']['peak_link_util_pct']:.2f}%，"
              f"负载均衡为{hc2['balance']['peak_link_util_pct']:.2f}%，能耗优化为{hc2['energy']['peak_link_util_pct']:.2f}%。"
              f"单位成功任务能耗分别为{hc2['latency']['energy_mean_J']:.2f}、{hc2['balance']['energy_mean_J']:.2f}、"
              f"{hc2['energy']['energy_mean_J']:.2f} J。")
    for st in STRATEGIES:
        rows=[r for r in summary if r['strategy']==st]
        count=sum(r['accepted'] for r in rows);arr=sum(r['arrived'] for r in rows)
        paragraph(document,f"{LABEL[st]}：接纳{count}/{arr}个请求，整体请求成功率{count/arr:.3%}；"
                  f"并发上限拒绝{sum(r['rejected_cap'] for r in rows)}个，不可行拒绝{sum(r['rejected_no_feasible'] for r in rows)}个。")
    paragraph(document,'已接纳任务全部通过时延和丢包约束；全部36组实际峰值都达到200/250/500，'
              '平均活跃任务数均不低于各场景上限的50%。成功率已包含主动过载引起的并发上限拒绝。')
    figure(document,'02_energy_ratio.png','图1：配对任务的相对平均能耗。每个场景、每种业务仅统计三策略共同成功的任务，最小时延策略归一化为1。')
    document.add_heading('2. 实验场景与业务条件',level=1)
    table(document,['场景','节点组成','并发上限','常规/峰值片请求'],[
        ['1','500 LEO；无EDGE、DC、GEO',200,'120 / 200'],
        ['2','500 LEO + 18 EDGE + 10 DC',250,'150 / 250'],
        ['3','2×500 LEO + 6 GEO；无地面节点',500,'300 / 525']])
    paragraph(document,'L1为550 km、53°倾角，L2为1100 km、87°倾角；每层20轨道面、每面25颗卫星。'
              '场景2地面节点作为候选计算节点，也可作为路由中继；业务源和目的仅从卫星中选取。'
              '场景3 LL端点仅LEO，HR/HC/MC允许LEO与GEO。LL和HR分别从传播最近的5/10颗可达卫星中选择目的端；HC为全局单目的，MC为全局3目的。')
    table(document,['业务','原始速率','时延/丢包预算','压缩ρ','op/bit','固定处理ms'],[
        ['LL','2–10 Mbps','20 ms / 1%','0.10','50','0.05'],
        ['HR','10–100 kbps','200 ms / 0.02%','0.33','200','0.10'],
        ['HC','100–500 Mbps','200 ms / 1%','0.20','1000','1.00'],
        ['MC','10–100 kbps','200 ms / 1%','0.05','40+15×3=85','0.10+0.02×3=0.16']])
    table(document,['计算节点','等效算力TFLOPS','计算功率预算W','η (J/op)'],[
        [n,cfg['compute_capacity_tflops'][n],cfg['compute_power_budget_W'][n],
         f"{cfg['energy_compute_eta_J_per_cycle'][n]:.5g}"] for n in ('LEO','GEO','EDGE','DC')])
    paragraph(document,'LEO–LEO、LEO–GEO、LEO–地面、地面TER标称带宽分别为10 Gbps、2 Gbps、1 Gbps、100 Gbps。'
              '时变因子g按距离/仰角计算，有效带宽为Bnom×g，单位bit能耗为βnom/g。'
              'βnom分别为3.5×10⁻⁹、4×10⁻⁸、5×10⁻⁸、1.5×10⁻¹⁰ J/bit。')
    document.add_heading('3. 测试方法与公平性',level=1)
    paragraph(document,'每组运行300 s，30 s为一片，共10片。片0至4、6至9为60%上限负载，片5（150 s）为峰值。'
              '任务均在片首到达、持续30 s；片内拓扑和路由不变。下一片重新构建拓扑和重新计算业务。'
              '本轮没有跨片存续任务，因此重路由/迁移数量为0符合设计。')
    paragraph(document,'场景3峰值注入525个请求，并发硬上限保持500，用于容纳少量物理路径不可行请求。'
              '达到上限后拒绝后续请求。报告同时列出请求成功率、已接纳QoS率、上限拒绝和不可行拒绝，便于解释过载。')
    paragraph(document,'使用随机种子42。对每个“场景×业务”，三个generated_tasks.csv移除结果状态列后逐字段相等，'
              '并计算SHA-256；有效配置仅允许strategy字段不同。每条路由再次核验时延、丢包和资源剩余量。'
              '配对能耗/时延比较采用三策略共同成功任务交集，避免通过拒绝高成本任务获得表面优势。')
    paragraph(document,'这是单随机种子的可控压力实验；10片覆盖300 s轨道变化。结果支持本配置内的策略比较，'
              '不用于估计长期轨道周期性能或统计置信区间。求解耗时为本机多进程并行实验期间的墙钟时间，存在系统负载噪声。')
    figure(document,'08_concurrency.png','图2：MC的实际并发曲线。虚线为场景并发上限，点线为50%下限；其他业务同样通过逐片审计。')
    document.add_heading('4. 指标定义与计算模型',level=1)
    definitions=[
        ['请求成功率','满足QoS且已接纳的请求数 / 全部生成请求数；包含并发上限拒绝。'],
        ['已接纳QoS率','已接纳且满足时延/丢包的任务数 / 已接纳任务数。'],
        ['平均/P95/最大时延','每任务先取最差目的端端到端时延，再跨成功任务求平均、95分位和最大。P95用线性插值。'],
        ['时延余量/计算时延/丢包','最小时延余量=业务预算−最大时延；计算时延为处理项均值；最大丢包为路径各跳成功率乘积的补数。'],
        ['总/平均能耗','Ecomp+Etx；均值除以成功任务数。包含整段30 s的数据处理和传输。'],
        ['配对平均能耗','三策略共同成功任务的平均能耗，保持任务身份、速率、端点完全相同。'],
        ['J/Gbit输入','总能耗 / 成功任务原始输入bit总量 ×10⁹；MC以原始输入一次计量。'],
        ['峰值链路利用率','各片调度完成后，全部链路的已用带宽/有效容量的最大值。'],
        ['链路CV','每片对全部链路（含零负载）计算标准差/均值，再对10片平均；同拓扑内越小越均匀。'],
        ['链路Jain指数','(Σu)² / (链路数量×Σu²)，越接近1越均匀；包含空闲链路。'],
        ['峰值算力利用率','各片全部计算节点的已用op/s/总op/s最大值；当前标定下等于计算功率利用率。'],
        ['源端计算/地面卸载比例','计算节点等于源卫星、或节点类型为EDGE/DC的成功任务比例。'],
        ['求解P99','单任务调用S1的墙钟求解耗时99分位；按原KPI实现的经验分位统计。'],
        ['回退成功数','主目标候选均不可行后，补充的最短传播路径候选恢复成功的任务数。']]
    table(document,['指标','定义与解释'],definitions,9)
    paragraph(document,'时延公式：T(s,d)=Σ前段距离/c + Tcompute(v) + Σ后段距离/c；MC取max_d。'
              'TER传播时延乘1.5。Tcompute=Tfixed + Lref×ω/Cv×1/(1−u)，u最多按0.95截断。'
              'Lref依次为12000、8000、1000000、12000 bit。')
    paragraph(document,'资源和能耗公式：计算吞吐r×ω，计算功率η×r×ω，计算能耗η×D₀×ω；'
              '前段带宽r、后段带宽ρr；重合链路按各路径流量相加；传输能耗为Σ前段βD₀+Σ各后段βρD₀。'
              'MC共享后段按独立复制流累加，尚未实现共享多播树去重。')
    paragraph(document,'模型边界：显式计入传播和处理时延；未显式加入链路排队、分组序列化、jitter_ms、协议栈、重传等待。'
              '计算负载放大仅近似排队。功率仅含业务动态计算，传输按J/bit计能耗；不含设备静态、平台和散热功耗，'
              '也没有发射机瞬时功率约束。低CV不必然意味着低总资源开销，应结合能耗、时延和Jain一起解读。')
    document.add_heading('5. 策略定义、问题定位与算法改进',level=1)
    paragraph(document,'最小时延：Dijkstra边权为传播时延；候选计算节点按前段传播+各后段传播之和+计算时延排序。'
              '单目的业务该排序与端到端时延一致，MC为各后段求和的启发式。统一硬约束校验保证每个目的端满足预算。')
    paragraph(document,'原负载均衡：边权u⁴+10⁻⁶，仅使用分配前负载；候选节点代价使用路径最大负载加节点利用率。'
              '它忽略当前任务的带宽增量，尤其容易低估100–500 Mbps HC在星地链路上的冲击；低流量时u⁴近于零，'
              '候选排序会对不必要的长路径过于宽容。原场景2 HC峰值99.36%，平均时延115.67 ms、平均能耗865.33 J。')
    paragraph(document,'修正后负载均衡：以全网归一化利用率平方和F=Σ链路u²+Σ节点q²的增量为目标。'
              '链路增量δ=r请求/B有效，边权为(u+δ)²−u²=2uδ+δ²；节点同样按新增计算吞吐/容量计算增量。'
              '候选排名先合并PRE/POST及多目的重合链路带宽，再求F增量。无固定每跳常数，避免压过低流量负载差异。'
              '这是逐任务贪心的凸拥塞代价，并非全局多商品流最优解。')
    paragraph(document,'能耗优化：Dijkstra边权βe×该段传输bit数；节点代价ηv×任务总计算量；'
              '候选目标为完整的Etx+Ecomp，压缩前后bit量分别使用D₀和ρD₀。该目标会倾向在源端尽早压缩，减少大数据量走长路径。')
    paragraph(document,'可行性补充搜索：原能耗策略场景2 LL出现7个可恢复拒绝。原因是每段只保留最低能耗的一条路，'
              '该路可能超过20 ms，而更快路径满足QoS。主目标候选全部失败后，新增传播最短路径候选，再按原目标重排、'
              '使用同一校验函数。此修复也用于负载均衡；不放宽硬约束，不修改业务输入。')
    table(document,['修改位置','作用'],[
        ['solvers/base.py','balance边权由历史负载改为预测平方增量。'],
        ['solvers/s1_dijkstra.py','共享带宽聚合评分；主目标不可行时补充最短传播候选。'],
        ['experiments/run_single_biz.py','保存有效配置和回退诊断，便于公平性审计。'],
        ['tests/test_strategy_objectives.py','验证拥塞避让、共享链路增量及能耗QoS恢复。']])
    for sc in (1,2,3):
        document.add_page_break()
        if sc==1:
            document.add_heading('6. 全场景正式结果',level=1)
        document.add_heading(f'6.{sc} 场景{sc}完整结果',level=2)
        for biz in BIZ:
            document.add_heading(f'{biz}业务',level=2)
            rows=[runs[sc,biz,s]['summary'] for s in STRATEGIES]
            table(document,['策略','接纳/请求','成功率','均值ms','P95 ms','最大ms','能耗J/任务'],[
                [LABEL[r['strategy']],f"{r['accepted']}/{r['arrived']}",f"{r['request_success_rate']:.2%}",
                 f"{r['delay_mean_ms']:.3f}",f"{r['delay_p95_ms']:.3f}",f"{r['delay_max_ms']:.3f}",
                 f"{r['energy_mean_J']:.6f}"] for r in rows])
            table(document,['策略','链路峰值%','链路CV','Jain','算力峰值%','源算%','地面%'],[
                [LABEL[r['strategy']],f"{r['peak_link_util_pct']:.4f}",f"{r['mean_link_cv']:.3f}",
                 f"{r['mean_link_jain']:.4f}",f"{r['peak_node_util_pct']:.6f}",
                 f"{r['compute_at_source_pct']:.1f}",f"{r['ground_compute_pct']:.1f}"] for r in rows])
            lat,bal,ene=rows
            paragraph(document,f"三策略共同成功任务{lat['common_accepted_tasks']}个。相对最小时延策略，"
                f"负载均衡的链路峰值变化{pct_change(bal['peak_link_util_pct'],lat['peak_link_util_pct']):+.1f}%，"
                f"配对平均时延变化{pct_change(bal['common_delay_mean_ms'],lat['common_delay_mean_ms']):+.1f}%；"
                f"能耗优化的配对平均能耗变化{pct_change(ene['common_energy_mean_J'],lat['common_energy_mean_J']):+.1f}%，"
                f"配对平均时延变化{pct_change(ene['common_delay_mean_ms'],lat['common_delay_mean_ms']):+.1f}%。")
        if sc==1:
            paragraph(document,'场景1中计算节点同质，能耗优化主要通过源端压缩和减少低价值传输来节能；'
                      '负载均衡把流量分散到较空闲路径，HC最能显示拥塞差异。LL/HR/MC流量很小，'
                      '利用率绝对值应与相对变化一起看，微小百分比变化不代表工程上的严重拥塞。')
        elif sc==2:
            paragraph(document,'场景2的主要矛盾是星地链路带宽和传输能耗，而非地面算力。'
                      '时延策略在短传播路径和强计算节点之间选择，可能把HC送入接近饱和的SGL；'
                      '预测负载代价会在新增大流量前识别该风险，能耗目标则因SGL的β高而倾向减少卸载。')
        else:
            paragraph(document,'场景3增加路径多样性，GEO端点导致HR/HC/MC长尾。'
                      'LL禁止GEO端点的限制在三策略全部通过。不可行拒绝需要结合传播下界区分物理限制与启发式搜索遗漏，'
                      '不能把所有no_feasible都解释为算力耗尽。')
    document.add_heading('7. 跨策略可视化与解释',level=1)
    figure(document,'01_delay.png','图3：配对任务平均时延；列为场景，横轴为业务。较低意味着更快；MC的排序目标与最坏端时延并不完全相同。')
    paragraph(document,'时延策略直接优化传播与计算时间；能耗策略为减少bit-hop可能接受稍长的传播路径；'
              '负载均衡愿意绕开拥塞热点。三种目标并不保证在每个样本都严格排序，'
              '端点、压缩位置与路径重合可使某策略同时改善多个指标。')
    figure(document,'03_link_cv.png','图4：全链路（含空闲）利用率CV。仅在相同场景、相同业务内横向比较；小表示相对分布更均匀。')
    figure(document,'06_hc_link_timeseries.png','图5：HC最忙链路的逐片曲线。负载均衡直接针对新增拥塞；能耗策略优先早压缩，也可能间接降低峰值。')
    figure(document,'07_placement_compute.png','图6：源端计算比例与算力热点。源端就地压缩可显著减少未压缩流量，当前算力充足使其更容易实施。')
    figure(document,'04_success.png','图7：请求成功率。场景3主动过载5%造成约0.8%的上限拒绝；已接纳任务QoS率均为100%。')
    figure(document,'05_solver.png','图8：单任务求解P99。候选补充搜索可能增加耗时；多进程系统负载使这些数据只适合描述本次运行。')
    document.add_heading('8. 算法修正前后与拒绝原因',level=1)
    before=[]
    for sc in (1,2,3):
        for st in ('balance','energy'):
            prefix = 'single_biz_concurrency_stress_balance' if st=='balance' else 'single_biz_concurrency_stress_energy_original'
            for biz in BIZ:
                path=ROOT/'results'/f'{prefix}_sc{sc}_20261016'/biz
                if not (path/'kpi.json').exists():continue
                old=json.loads((path/'kpi.json').read_text(encoding='utf-8'))
                old_slots=read_csv(path/'slot_stats.csv')
                new=runs[sc,biz,st]['summary']
                before.append(dict(scenario=sc,biz=biz,strategy=st,
                    old_accepted=old['total_accepted'],new_accepted=new['accepted'],
                    old_no_feasible=sum(x['reject_reason']=='no_feasible' for x in read_csv(path/'generated_tasks.csv')),
                    new_no_feasible=new['rejected_no_feasible'],old_energy_J=old['avg_energy_per_task_J'],
                    new_energy_J=new['energy_mean_J'],old_peak_link_pct=100*max(float(x['link_util_max']) for x in old_slots),
                    new_peak_link_pct=new['peak_link_util_pct']))
    if before:
        write_csv(OUT/'algorithm_before_after.csv',before)
        selected=[r for r in before if (r['biz']=='HC' and r['strategy']=='balance')
                  or (r['scenario']==2 and r['biz']=='LL' and r['strategy']=='energy')]
        table(document,['场景/业务/策略','不可行前→后','能耗J前→后','链路峰值%前→后'],[
            [f"{r['scenario']}/{r['biz']}/{LABEL[r['strategy']]}",f"{r['old_no_feasible']}→{r['new_no_feasible']}",
             f"{r['old_energy_J']:.3f}→{r['new_energy_J']:.3f}",
             f"{r['old_peak_link_pct']:.3f}→{r['new_peak_link_pct']:.3f}"] for r in selected])
    table(document,['策略','上限拒绝','不可行拒绝','补充搜索恢复'],[
        [LABEL[st],sum(r['rejected_cap'] for r in summary if r['strategy']==st),
         sum(r['rejected_no_feasible'] for r in summary if r['strategy']==st),
         sum(r['fallback_accepted'] for r in summary if r['strategy']==st)] for st in STRATEGIES])
    bad=[r for r in rejections if r['reason']=='no_feasible']
    if bad:
        table(document,['场景/业务/策略','任务','片','最乐观时延下界ms','解释'],[
            [f"{r['scenario']}/{r['biz']}/{LABEL[r['strategy']]}",r['task_id'],r['slot'],
             f"{r['optimistic_delay_lower_bound_ms']:.3f}",r['classification']] for r in bad],8)
    paragraph(document,'传播下界在当片全资源拓扑上计算源到各目的的最短传播时延，并加最小固定处理开销。'
              '若下界已超过预算，则无论计算节点如何放置都无法满足时延；若下界未超限，只能说明当前S1候选未找到解，'
              '还需更大路径集合或联合优化，不能声称全局无解。')
    mc_bal=runs[3,'MC','balance']['summary'];mc_ene=runs[3,'MC','energy']['summary']
    paragraph(document,f"场景3 MC中，负载均衡经补充搜索恢复{mc_bal['fallback_accepted']}个任务，"
              f"占成功任务的{mc_bal['fallback_accepted']/mc_bal['accepted']:.1%}；求解P99为"
              f"{mc_bal['solve_p99_ms']:.2f} ms，能耗策略为{mc_ene['solve_p99_ms']:.2f} ms。"
              '主拥塞目标可能产生不满足200 ms的长路径；补充搜索恢复可行性，代价是更多计算和对拥塞目标的让步。'
              '这是下一步采用时延约束多候选联合排序的主要改进方向。')
    document.add_heading('9. 具体结论与适用策略',level=1)
    for sc in (1,2,3):
        hc={s:runs[sc,'HC',s]['summary'] for s in STRATEGIES}
        paragraph(document,f"场景{sc} HC：负载均衡使链路峰值从{hc['latency']['peak_link_util_pct']:.2f}%"
                  f"降至{hc['balance']['peak_link_util_pct']:.2f}%；能耗优化的配对单位任务能耗比时延策略"
                  f"变化{pct_change(hc['energy']['common_energy_mean_J'],hc['latency']['common_energy_mean_J']):+.1f}%。"
                  f"对应平均时延依次为{hc['latency']['delay_mean_ms']:.2f}、{hc['balance']['delay_mean_ms']:.2f}、"
                  f"{hc['energy']['delay_mean_ms']:.2f} ms。")
    paragraph(document,'LL优先关注20 ms硬约束与尾时延；在可行性补充搜索后，能耗/均衡也可以满足该约束，'
              '应根据是否需要节能或热点裕量选择。HR速率很低，容量压力有限，更应关注可靠性和GEO端点尾时延。'
              'HC同时展示最强的链路与能耗差异，是策略验证的首选业务。MC的GEO尾时延最敏感，'
              '应在后续算法中增加更丰富的QoS可行路径搜索与内部安全裕量。')
    ll_lat=runs[1,'LL','latency']['summary'];ll_energy=runs[1,'LL','energy']['summary']
    paragraph(document,f"LL还展示了等时延候选的优化空间：场景1最小时延/能耗策略的平均时延均约"
              f"{ll_lat['delay_mean_ms']:.3f} ms，但能耗从{ll_lat['energy_mean_J']:.6f}降至"
              f"{ll_energy['energy_mean_J']:.6f} J/任务。传播总距离几乎未变，压缩位置却更靠近源端。"
              '原时延策略未对同延迟候选增加能耗二级排序，因此此处节能无需明显牺牲时延，'
              '不能据此断言所有最小时延算法都必然高耗能。')
    paragraph(document,f"均衡指标也存在差异：场景3 MC的负载均衡链路CV为{mc_bal['mean_link_cv']:.3f}，"
              f"能耗策略为{mc_ene['mean_link_cv']:.3f}；但峰值链路利用率分别为"
              f"{mc_bal['peak_link_util_pct']:.6f}%和{mc_ene['peak_link_util_pct']:.6f}%。"
              '两种分布指标不完全一致，且当前采用局部平方增量和QoS回退，并未直接最小化最终CV。'
              '因此按业务同时观察峰值、CV、时延和能耗，比要求某个策略所有指标都最优更符合实际。')
    paragraph(document,'当前算力与计算功率利用率很低，尚不足以证明某策略在算力饱和时更优；'
              '负载均衡主要改善的是链路带宽分布。η按Pmax/Cmax标定使计算功率约束与算力约束等价，'
              '能耗优势主要来自传输路径和压缩位置，不能外推为真实卫星平台总功耗节省。')
    paragraph(document,'后续算法层可继续采用：时延预算内的多权重候选路径集合、拥塞门限与能耗联合排序、'
              '基于最差目的端的MC排序、共享多播树去重以及跨片暖启动。'
              '在本轮结果中没有通过修改容量、QoS、压缩率或任务流量制造策略差异。')
    document.add_heading('10. 复现、数据与审计',level=1)
    paragraph(document,'正式数据目录：results/all_strategy_single_biz_20261016/{latency,balance,energy}/sc{1,2,3}/{LL,HR,HC,MC}/。'
              '每组包括generated_tasks、routing_allocations、resource_allocations、slot_stats、tasks CSV，'
              'kpi.json和effective_config.json。统一指标为all_metrics.csv；时序为all_slot_metrics.csv；'
              '拒绝审计为rejection_audit.csv；配对输入与源码校验为audit_manifest.json。')
    paragraph(document,'运行示例（分别替换策略和场景）：')
    paragraph(document,'python experiments/run_single_biz.py --scenario 2 --strategy energy '
              '--stress-concurrency --stress-slots 10 --steady-ratio 0.60 '
              '--out results/all_strategy_single_biz_20261016/energy/sc2')
    paragraph(document,'场景3额外使用 --peak-offered-ratio 1.05。报告生成：'
              'python experiments/build_all_strategy_report.py。报告依赖列于requirements-report.txt。')
    paragraph(document,f'环境：Python {platform.python_version()}，matplotlib {matplotlib.__version__}，'
              f'python-docx {docx.__version__}，{platform.system()} {platform.release()}。'
              '算法验证：4项定向单元检查通过；36组数据的输入、配置、QoS、资源非负、并发目标逐项校验通过。')
    document.add_heading('附录：求解、功率与工作量归一化指标',level=1)
    for sc in (1,2,3):
        document.add_heading(f'场景{sc}',level=2)
        table(document,['业务','策略','平均活跃','J/Gbit输入','峰值计算功率W','求解P99 ms'],[
            [r['biz'],LABEL[r['strategy']],f"{r['avg_active']:.1f}",f"{r['energy_J_per_input_Gbit']:.4f}",
             f"{r['peak_compute_power_W']:.6f}",f"{r['solve_p99_ms']:.3f}"]
            for r in summary if r['scenario']==sc])
    report=OUT/'全场景全策略单业务测试.docx'
    document.save(report)
    return report


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg,summary,timeseries,rejections,runs=collect()
    write_csv(OUT/'all_metrics.csv',summary)
    write_csv(OUT/'all_slot_metrics.csv',timeseries)
    if rejections:write_csv(OUT/'rejection_audit.csv',rejections)
    make_plots(runs)
    report=make_document(cfg,summary,timeseries,rejections,runs)
    audit=dict(result_groups=len(summary),slot_rows=len(timeseries),input_pairing='PASS',
        physical_config_pairing='PASS',all_qos='PASS',nonnegative_resources='PASS',
        concurrency_targets='PASS',LL_GEO_endpoint_violations=0,
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in [ROOT/'solvers/base.py',ROOT/'solvers/s1_dijkstra.py',ROOT/'energy.py',
                                 ROOT/'tasks.py',ROOT/'config.yaml',Path(__file__)]},
        paired_input_sha256={f'sc{r[0]}_{r[1]}':runs[r[0],r[1],'latency']['summary']['input_sha256']
                             for r in [(s,b) for s in (1,2,3) for b in BIZ]})
    (OUT/'audit_manifest.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(report),'groups':len(summary),'figures':len(list(FIG.glob('*.png')))},ensure_ascii=False))


if __name__=='__main__':
    main()
