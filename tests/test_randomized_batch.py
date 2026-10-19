"""Random batch inputs change by seed/scene, but replay exactly and retain physics."""
import json
from pathlib import Path
import sys
import unittest
import tempfile
import shutil
from contextlib import redirect_stdout
from io import StringIO
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.run_randomized_batch import PREVIOUS,fresh_config,derived_seed,finalize_exported_run
from experiments.run_mixed_3600 import WEIGHTS,planned_timing,sparse_peak_profile
from experiments.run_mixed_strategies import read,number


class RandomBatchTests(unittest.TestCase):
    def test_fresh_seed_and_replay(self):
        seeds=set()
        for sc,cap in ((1,200),(2,250),(3,500)):
            base=json.loads((PREVIOUS/'latency'/f'sc{sc}'/'effective_config.json').read_text(encoding='utf-8'))
            a,s=fresh_config(base,sc,123456789)
            replay,_=fresh_config(base,sc,123456789)
            other,_=fresh_config(base,sc,123456790)
            self.assertEqual(a,replay)
            self.assertNotEqual(a['traffic']['fixed_arrival_overrides'],other['traffic']['fixed_arrival_overrides'])
            self.assertNotEqual(a['simulation']['random_seed'],other['simulation']['random_seed'])
            self.assertEqual(a['traffic']['qos'],base['traffic']['qos'])
            self.assertEqual(a['compute_capacity_tflops'],base['compute_capacity_tflops'])
            self.assertEqual(a['algorithm'],base['algorithm'])
            self.assertEqual(a['constellation']['shells'],base['constellation']['shells'])
            seeds.update(s.values())
            t=a['traffic'];counts={b:sum(r[b] for r in t['fixed_arrival_overrides'].values()) for b in WEIGHTS}
            self.assertTrue(all(counts[b]==w*counts['MC'] for b,w in WEIGHTS.items()))
            self.assertGreaterEqual(planned_timing(t,120)[0],cap*1.05)
            self.assertTrue(t['truncate_at_simulation_end'])
        self.assertEqual(len(seeds),9)

    def test_legacy_schedule_default_unchanged(self):
        cfg=json.loads((PREVIOUS/'latency'/'sc1'/'effective_config.json').read_text(encoding='utf-8'))
        old=cfg['traffic']['fixed_arrival_overrides']
        sparse_peak_profile(cfg['traffic'],200,120)
        self.assertEqual({str(k):v for k,v in cfg['traffic']['fixed_arrival_overrides'].items()},old)
        self.assertEqual(cfg['traffic']['arrival_profile']['schedule_seed'],2042)

    def test_every_policy_and_scene_has_independent_random_streams(self):
        seeds=set();schedules=[]
        for sc in (1,2,3):
            base=json.loads((PREVIOUS/'latency'/f'sc{sc}'/'effective_config.json').read_text(encoding='utf-8'))
            for st in ('latency','balance','energy'):
                cfg,streams=fresh_config(base,sc,127424259714900,st)
                seeds.update(streams.values())
                schedules.append(json.dumps(cfg['traffic']['fixed_arrival_overrides'],sort_keys=True))
                self.assertEqual(cfg['simulation']['strategy'],st)
        self.assertEqual(len(seeds),27)
        self.assertEqual(len(set(schedules)),9)

    def test_rebuild_supplemental_metrics_matches_original_raw_export(self):
        source=PREVIOUS/'latency'/'sc1'
        with tempfile.TemporaryDirectory(prefix='mixed_recovery_') as temp:
            batch=Path(temp);target=batch/'latency'/'sc1';target.mkdir(parents=True)
            for name in ('effective_config.json','generated_tasks.csv','routing_allocations.csv','slot_stats.csv','kpi.json'):
                shutil.copy2(source/name,target/name)
            with redirect_stdout(StringIO()):finalize_exported_run(batch,1,'latency')
            a={r['task_id']:number(r) for r in read(source/'lifecycle_tasks.csv')}
            b={r['task_id']:number(r) for r in read(target/'lifecycle_tasks.csv')}
            self.assertEqual(a.keys(),b.keys())
            for tid,row in a.items():
                for key,value in row.items():
                    if isinstance(value,(int,float)):
                        self.assertAlmostEqual(value,b[tid][key],delta=max(1e-6,abs(value)*1e-12))
                    else:self.assertEqual(value,b[tid][key])


if __name__=='__main__':unittest.main()
