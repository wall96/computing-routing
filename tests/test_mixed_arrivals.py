"""Sparse arrivals retain business profiles and global proportions, not dense slots."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yaml
from experiments.run_mixed_3600 import experiment_config,WEIGHTS,planned_timing


class MixedArrivalTest(unittest.TestCase):
    def test_sparse_profile_is_reproducible_and_preserves_physics(self):
        base=yaml.safe_load((Path(__file__).resolve().parents[1]/'config.yaml').read_text(encoding='utf-8'))
        for sc,cap in ((1,200),(2,250),(3,500)):
            a=experiment_config(base,sc,'variable_random',Path('unused'),arrival_profile='sparse_peak')
            b=experiment_config(base,sc,'variable_random',Path('unused'),arrival_profile='sparse_peak')
            self.assertEqual(a,b)
            t=a['traffic'];counts={biz:sum(row[biz] for row in t['fixed_arrival_overrides'].values()) for biz in WEIGHTS}
            self.assertTrue(all(counts[biz]==w*counts['MC'] for biz,w in WEIGHTS.items()))
            self.assertEqual(t['qos'],base['traffic']['qos'])
            self.assertEqual(a['compute_capacity_tflops'],base['compute_capacity_tflops'])
            self.assertFalse(t['arrival_at_slot_start'])
            self.assertEqual(planned_timing(t,120)[0],t['arrival_profile']['offered_peak'])
            self.assertGreaterEqual(t['arrival_profile']['offered_peak'],cap*1.05)
            self.assertLess(sum(counts.values()),cap*3)
            self.assertTrue(any(row['MC']==0 for row in t['fixed_arrival_overrides'].values()))


if __name__=='__main__':unittest.main()
