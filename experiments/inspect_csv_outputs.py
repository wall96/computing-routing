"""Read-only integration example for exact-event result CSVs.

From code/: python experiments/inspect_csv_outputs.py --run results/.../latency/sc1
Prints audited statistics as JSON; never modifies an experiment output.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path


def read_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        yield from csv.DictReader(stream)


def timeline_integrals(rows):
    """Use the previous state, not the state after the next event."""
    previous = None
    active_seconds = compute_joules = 0.0
    for row in rows:
        now = float(row['t'])
        if previous is not None:
            dt = now - float(previous['t'])
            if dt < -1e-8:
                raise ValueError('Timeline is not in chronological file order')
            active_seconds += float(previous['active']) * max(0.0, dt)
            compute_joules += float(previous['compute_power_W']) * max(0.0, dt)
        previous = row
    return active_seconds, compute_joules


def inspect(run_dir, horizon=3600.0, hard_end=None):
    """Join within one run only; route energy is never duplicated by resource rows."""
    run_dir = Path(run_dir).resolve()
    generated = list(read_rows(run_dir / 'generated_tasks.csv'))
    routes = list(read_rows(run_dir / 'routing_allocations.csv'))
    if not routes or 'service_duration_s' not in routes[0]:
        raise ValueError('This example requires the exact-event segmented CSV schema')
    tasks = {row['task_id']: row for row in generated}
    if len(tasks) != len(generated):
        raise ValueError('Duplicate task_id within a run')
    by_task = defaultdict(list)
    for row in routes:
        if row['task_id'] not in tasks:
            raise ValueError('Allocation has no generated task')
        by_task[row['task_id']].append(row)
    lives = list(read_rows(run_dir / 'lifecycle_tasks.csv'))
    if len(lives) != len(tasks) or {r['task_id'] for r in lives} != set(tasks):
        raise ValueError('Lifecycle and generated task sets differ')
    for row in lives:
        segments = by_task[row['task_id']]
        seconds = math.fsum(float(s['service_duration_s']) for s in segments)
        energy = math.fsum(float(s['energy_total_J']) for s in segments)
        if not math.isclose(seconds, float(row['service_seconds']), rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError('Service seconds do not reconcile')
        if not math.isclose(energy, float(row['energy_total_J']), rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError('Task energy does not reconcile')
    states = Counter(row['status'] for row in lives)
    accepted = [r for r in lives if int(r['initially_accepted'])]
    service = math.fsum(float(r['service_seconds']) for r in lives)
    compute = math.fsum(float(r['energy_compute_J']) for r in lives)
    energy = math.fsum(float(r['energy_total_J']) for r in lives)
    input_gbit = math.fsum(float(r['input_bits_served']) for r in lives) / 1e9
    integral_s, integral_j = timeline_integrals(read_rows(run_dir / 'event_timeline.csv'))
    if not math.isclose(service, integral_s, rel_tol=1e-9, abs_tol=1e-5):
        raise ValueError('Timeline active integral does not reconcile')
    if not math.isclose(compute, integral_j, rel_tol=1e-9, abs_tol=1e-5):
        raise ValueError('Timeline compute energy integral does not reconcile')
    beyond = sum(float(r['t_end']) > hard_end + 1e-8 for r in generated) if hard_end is not None else None
    if beyond:
        raise ValueError(f'{beyond} requested tasks extend beyond the specified hard end')
    first = routes[0]
    return dict(run_id=str(run_dir), generated=len(tasks), initially_accepted=len(accepted),
        states=dict(states), segments=len(routes), time_avg_active=service / horizon,
        lifetime_delay_mean_ms=(math.fsum(float(r['lifetime_max_delay_ms']) for r in accepted) / len(accepted) if accepted else None),
        energy_total_J=energy, observed_input_Gbit=input_gbit,
        energy_J_per_input_Gbit=energy / input_gbit if input_gbit else None,
        mean_total_dynamic_power_W=energy / horizon,
        beyond_hard_end=beyond, service_and_energy_audit='PASS',
        first_route=dict(task_id=first['task_id'], allocation_id=first['allocation_id'],
            compute_node=first['compute_node'], destinations=json.loads(first['dst_json']),
            path_pre=json.loads(first['path_pre_json']), path_post=json.loads(first['path_post_json']),
            bandwidth=json.loads(first['bw_per_link_json'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--horizon', type=float, default=3600.0, help='Observation length, not absolute end')
    parser.add_argument('--hard-end', type=float, help='Optional absolute latest task end; bounded runs use 3600')
    args = parser.parse_args()
    if args.horizon <= 0:
        parser.error('--horizon must be positive')
    print(json.dumps(inspect(args.run, args.horizon, args.hard_end), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
