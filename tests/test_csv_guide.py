"""Regression checks for the read-only CSV documentation helpers."""
import csv
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.build_csv_guide import inventory
from experiments.csv_guide_content import INFO, definition
from experiments.inspect_csv_outputs import timeline_integrals


class CsvGuideTests(unittest.TestCase):
    def test_every_existing_header_has_explicit_definitions(self):
        schemas, _ = inventory()
        with (ROOT / 'results/technical_scheme_experiments_20261016/metric_dictionary.csv').open(
                encoding='utf-8-sig', newline='') as stream:
            report = {r['field']: (r['unit'], r['definition']) for r in csv.DictReader(stream)}
        self.assertEqual({s['filename'] for s in schemas}, set(INFO))
        for schema in schemas:
            for field in schema['columns']:
                unit, meaning = definition(schema['filename'], field, report)
                self.assertTrue(unit)
                self.assertTrue(meaning)

    def test_integral_uses_previous_state_and_same_time_file_order(self):
        rows = [dict(t=t, active=n, compute_power_W=p) for t, n, p in [
            (0, 0, 0), (10, 0, 0), (10, 2, 3), (20, 2, 3), (20, 0, 0), (30, 0, 0)]]
        self.assertEqual(timeline_integrals(rows), (20, 30))

    def test_nonmonotonic_timeline_fails(self):
        rows = [dict(t=t, active=1, compute_power_W=2) for t in (10, 9)]
        with self.assertRaises(ValueError):
            timeline_integrals(rows)

    def test_legacy_energy_name_has_per_task_unit(self):
        for key in ('old_energy_J', 'new_energy_J'):
            self.assertEqual(definition('algorithm_before_after.csv', key, {})[0], 'J/任务')

    def test_prefix_change_not_percentage_points(self):
        unit, meaning = definition('cross_batch_comparison.csv', 'change_pct_initial_accept_rate', {})
        self.assertEqual(unit, '%')
        self.assertIn('不是百分点差', meaning)


if __name__ == '__main__':
    unittest.main()
