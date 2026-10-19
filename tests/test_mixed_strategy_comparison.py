"""Pairing and common-service energy comparisons must ignore censoring bias."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.run_mixed_3600 import write_csv
from experiments.run_mixed_strategies import input_signature,common_comparison


class ComparisonTest(unittest.TestCase):
    def test_input_signature_ignores_outcomes_but_not_arrivals(self):
        a=[dict(task_id='A',arrival_t='5',rate_mbps='2',final_status='COMPLETED',reject_reason='')]
        b=[dict(a[0],final_status='DROPPED')]
        self.assertEqual(input_signature(a),input_signature(b))
        b[0]['arrival_t']='6'
        self.assertNotEqual(input_signature(a),input_signature(b))

    def test_only_common_served_intervals_are_compared(self):
        def segment(tid,start,end,energy,delay):
            return dict(task_id=tid,t_alloc=start,service_t_end=end,service_duration_s=end-start,
                        energy_total_J=energy,max_delay_ms=delay)
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'a';b=Path(tmp)/'b';a.mkdir();b.mkdir()
            write_csv(a/'generated_tasks.csv',[dict(task_id='A',biz='LL',rate_mbps=2),
                                                dict(task_id='B',biz='HR',rate_mbps=3)])
            write_csv(a/'routing_allocations.csv',[segment('A',5,30,50,10),segment('A',30,45,45,20),
                                                    segment('B',6,20,500,9)])
            write_csv(b/'routing_allocations.csv',[segment('A',5,30,25,12),segment('A',30,40,20,18)])
            rows={r['biz']:r for r in common_comparison(a,b,1,'energy')}
            result=rows['ALL']
            self.assertEqual(result['common_tasks'],1)
            self.assertEqual(result['common_service_seconds'],35)
            self.assertAlmostEqual(result['common_input_Gbit'],.07)
            self.assertEqual(result['latency_common_energy_J'],80)
            self.assertEqual(result['strategy_common_energy_J'],45)
            self.assertEqual(result['common_energy_reduction_pct'],43.75)
            self.assertAlmostEqual(result['latency_common_delay_ms'],450/35)
            self.assertAlmostEqual(result['strategy_common_delay_ms'],480/35)
            self.assertEqual(rows['HR']['common_tasks'],0)
            self.assertEqual(rows['HR']['common_energy_reduction_pct'],'')


if __name__=='__main__':unittest.main()
