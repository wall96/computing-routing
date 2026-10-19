"""Arrival, completion, reroute, censoring and energy integral invariants."""
from contextlib import redirect_stdout
import copy
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yaml
from models import Node,NodeType,Link,LinkType,Snapshot,Task,BizType
from simulator import Simulator
from solvers.s1_dijkstra import S1Dijkstra


def tiny_snapshot(*args):
    nodes={key:Node(key,NodeType.LEO,1e12,1e12,1e-12,1,1) for key in ('s','d')}
    link=Link('s','d',LinkType.ISL_LEO_LEO,100,100,1,0,0,1e-8)
    return Snapshot(float(args[0]) if args else 0,nodes,{('d','s'):link},{'LEO'})


def demand(tid,arrival,duration):
    return Task(tid,'s',['d'],BizType.LL,1,'persist',2,20,.01,50,12000,
        arrival,duration,.1,cycles_total=2e6*50*duration,
        data_volume_bits=2e6*duration,compute_rate=2e6*50)


class TinySimulator(Simulator):
    def _build_nodes(self):return list(tiny_snapshot().nodes.values())


class EventAccountingTest(unittest.TestCase):
    def test_true_arrivals_releases_cross_slot_and_horizon_energy(self):
        cfg=yaml.safe_load((Path(__file__).resolve().parents[1]/'config.yaml').read_text(encoding='utf-8'))
        cfg['constellation']['scenario_max_concurrency'][1]=1
        cfg['algorithm']['latency_lazy_candidates']=True
        cfg['simulation'].update(t_start_sec=0,t_end_sec=60,slot_sec=30,event_driven_within_slot=True)
        tasks=[demand('A',5,8),demand('B',14,20),demand('C',16,2),demand('D',42,40)]
        with tempfile.TemporaryDirectory() as out:
            cfg['simulation']['event_output_dir']=out
            sim=TinySimulator(cfg,scenario=1,no_scale=True)
            class Generator:
                def gen_for_slot(self,t,i,snapshot=None):
                    return [x for x in tasks if t<=x.arrival_t<t+30]
            sim.gen=Generator()
            with patch('event_simulation.build_snapshot',side_effect=tiny_snapshot),redirect_stdout(io.StringIO()):
                m=sim.run()
                m.export(out)
            self.assertEqual(m.max_concurrency,1)
            self.assertEqual(m.reroutes,1)
            self.assertEqual(m.task_terminal,{'A':'COMPLETED','B':'COMPLETED','D':'ACTIVE_AT_END'})
            self.assertEqual([(x.id,r) for x,r in m.rejects],[('C','scenario_concurrency_cap')])
            self.assertEqual(sum(m.task_service_seconds.values()),46)
            self.assertAlmostEqual(sum(sum(v) for v in m.task_energy.values()),m.summary()['total_energy_J'])
            self.assertAlmostEqual(m.summary()['compute_energy_J'],.0001*46)
            self.assertEqual(m.task_service_seconds['B'],20)
            self.assertEqual(m.task_service_seconds['D'],18)
            for r in m._allocation_history:
                task=next(t for t in tasks if t.id==r['task_id'])
                self.assertGreaterEqual(r['t_alloc'],task.arrival_t)
                self.assertLessEqual(r['service_t_end'],min(60,task.t_end))
            with open(Path(out)/'event_timeline.csv',encoding='utf-8-sig') as f:
                timeline=list(csv.DictReader(f))
            integral=sum(float(a['active'])*(float(b['t'])-float(a['t'])) for a,b in zip(timeline,timeline[1:]))
            self.assertAlmostEqual(integral,46)

    def test_lazy_latency_matches_original_candidate_ties(self):
        cfg={'algorithm':{}}
        original=S1Dijkstra(cfg)
        optimized=S1Dijkstra({'algorithm':{'latency_lazy_candidates':True}})
        for duration in (8,30,1200):
            snap=tiny_snapshot();task=demand('X',0,duration)
            a=original.solve(snap,task,'latency');b=optimized.solve(snap,task,'latency')
            self.assertEqual((a.compute_node,a.path_pre,a.path_post,a.energy_total_J),
                             (b.compute_node,b.path_pre,b.path_post,b.energy_total_J))


if __name__=='__main__':unittest.main()
