"""Ending within the observation window changes actual input, not terminal labels."""
import copy
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yaml
from tasks import TaskGenerator
from tests.test_event_accounting import tiny_snapshot,TinySimulator


class BoundedTaskTest(unittest.TestCase):
    def test_duration_volume_and_work_are_clipped_without_resampling(self):
        cfg=yaml.safe_load((Path(__file__).resolve().parents[1]/'config.yaml').read_text(encoding='utf-8'))['traffic']
        cfg.update(endpoint_mode='satellite')
        cfg.update(arrival_rate_per_slot={'LL':1},fixed_arrivals_per_slot={'LL':1},
                   independent_time_rng=True,time_seed=1042,arrival_at_slot_start=False)
        before=TaskGenerator(copy.deepcopy(cfg),[],30,seed=42)
        after=TaskGenerator(copy.deepcopy(cfg),[],30,seed=42,end_time_sec=3600)
        old=before.gen_for_slot(3570,119,tiny_snapshot(3570))[0]
        new=after.gen_for_slot(3570,119,tiny_snapshot(3570))[0]
        self.assertEqual((old.id,old.src,old.dst_set,old.rate_mbps,old.arrival_t),
                         (new.id,new.src,new.dst_set,new.rate_mbps,new.arrival_t))
        self.assertEqual(new.t_end,3600)
        self.assertLess(new.duration_s,old.duration_s)
        self.assertEqual(new.data_volume_bits,new.rate_bps*new.duration_s)
        self.assertEqual(new.cycles_total,new.data_volume_bits*new.effective_cycles_per_bit)
        self.assertEqual(old.compute_rate,new.compute_rate)
        self.assertEqual(after.duration_adjustments[0]['sampled_duration_s'],old.duration_s)
        self.assertEqual(after.duration_adjustments[0]['duration_clipped'],1)
        self.assertEqual(after.gen_for_slot(3600,120,tiny_snapshot(3600)),[])

    def test_finish_events_at_horizon_are_completed_and_resources_released(self):
        cfg=yaml.safe_load((Path(__file__).resolve().parents[1]/'config.yaml').read_text(encoding='utf-8'))
        cfg['simulation'].update(t_start_sec=0,t_end_sec=60,slot_sec=30,event_driven_within_slot=True)
        cfg['traffic'].update(endpoint_mode='satellite')   # tiny_snapshot 只有两颗无轨道卫星
        cfg['traffic'].update(arrival_rate_per_slot={'LL':1},fixed_arrivals_per_slot={'LL':1},
            independent_time_rng=True,time_seed=1042,arrival_at_slot_start=False,truncate_at_simulation_end=True)
        cfg['traffic']['qos']['LL']['duration_s']=[600,600]
        with tempfile.TemporaryDirectory() as tmp:
            cfg['simulation']['event_output_dir']=tmp
            sim=TinySimulator(cfg,scenario=1)
            with patch('event_simulation.build_snapshot',side_effect=tiny_snapshot),redirect_stdout(io.StringIO()):
                m=sim.run();m.export(tmp)
            self.assertEqual(len(m.attempts),2)
            self.assertTrue(all(t.t_end==60 for t in m.attempts))
            self.assertTrue(all(v=='COMPLETED' for v in m.task_terminal.values()))
            self.assertEqual(m._slot_stats[-1]['active'],0)
            for t in m.attempts:self.assertAlmostEqual(m.task_service_seconds[t.id],t.duration_s)


if __name__=='__main__':unittest.main()
