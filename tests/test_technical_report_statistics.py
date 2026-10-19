"""Reporting-only compute-utilization reconstruction uses actual live intervals."""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from experiments.build_technical_scheme_report import stable_node_moments
from experiments.run_mixed_3600 import write_csv


class ReportStatisticsTest(unittest.TestCase):
    def evaluate(self,allocations):
        with tempfile.TemporaryDirectory(prefix='report_node_stats_') as folder:
            path=Path(folder)
            (path/'effective_config.json').write_text(json.dumps({'compute_capacity_tflops':{'LEO':1}}),encoding='utf-8')
            write_csv(path/'routing_allocations.csv',allocations)
            return stable_node_moments(path,1)

    def allocation(self,aid,node,start,end,rate=1e11):
        return dict(allocation_id=aid,compute_node=node,compute_node_type='LEO',compute_rate_used=rate,
                    t_alloc=start,service_t_end=end,service_duration_s=end-start)

    def test_one_used_node_includes_idle_population_and_idle_time(self):
        result=self.evaluate([self.allocation('A1','N1',10,110)])
        self.assertAlmostEqual(result['node_cv'],math.sqrt(499)*100/3600)
        self.assertAlmostEqual(result['node_jain'],1/500*100/3600)
        self.assertAlmostEqual(result['node_peak'],10.)

    def test_equal_nodes_and_simultaneous_finish_start(self):
        result=self.evaluate([self.allocation('A1','N1',0,100),self.allocation('A2','N2',0,100),
                              self.allocation('A3','N1',100,200),self.allocation('A4','N2',100,200)])
        self.assertAlmostEqual(result['node_cv'],math.sqrt(249)*200/3600)
        self.assertAlmostEqual(result['node_jain'],2/500*200/3600)

    def test_overlapping_tasks_sum_on_same_node_without_residual_load(self):
        result=self.evaluate([self.allocation('A1','N1',0,100),self.allocation('A2','N1',50,150)])
        self.assertAlmostEqual(result['node_cv'],math.sqrt(499)*150/3600)
        self.assertAlmostEqual(result['node_peak'],20.)


if __name__=='__main__':unittest.main()
