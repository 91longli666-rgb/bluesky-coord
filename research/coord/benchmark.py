"""Reproducible A320 experiments using ACTUAL BlueSky dynamics and original MVP.

Run from the checkout: python research/coord/benchmark.py --suite pilot
Every method uses identical aircraft, routes, seeds, separation and time steps.
No independent toy simulator stands in for BlueSky or its MVP implementation.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
WORK = Path(os.environ.get('COORD_WORKDIR', ROOT / 'cache' / 'coord-benchmark'))
os.environ.setdefault('MPLCONFIGDIR', str(WORK / 'mpl-cache'))

import numpy as np
import bluesky as bs

EARTH = 6371000.0
NM = 1852.0
LATITUDE, LONGITUDE = 52.0, 4.0


def latlon(position):
    position = np.asarray(position)
    return (LATITUDE + np.rad2deg(position[..., 1] / EARTH),
            LONGITUDE + np.rad2deg(position[..., 0] / (EARTH * np.cos(np.deg2rad(LATITUDE)))))


def local_position():
    return EARTH * np.column_stack((
        np.deg2rad(bs.traf.lon - LONGITUDE) * np.cos(np.deg2rad(LATITUDE)),
        np.deg2rad(bs.traf.lat - LATITUDE)))


def case_data(name, seed):
    rng = np.random.default_rng(seed)
    if name == 'headon2':
        angles = np.array([0., 180.])
    elif name == 'crossing2':
        angles = np.array([0., 90.])
    elif name == 'merge3':
        angles = np.array([0., 70., 290.])
    elif name.startswith('radial'):
        n = int(name.removeprefix('radial'))
        angles = np.arange(n) * 360.0 / n
    elif name == 'parallel6':
        position = np.column_stack((np.full(6, -60000.), np.arange(6) * 15000. - 37500.))
        return position, position + [200000., 0.], np.full(6, 90.), np.full(6, 250.)
    else:
        raise ValueError(name)
    angles += rng.uniform(-3., 3., len(angles))
    radius = 60000. + rng.uniform(-2000., 2000., len(angles))
    radians = np.deg2rad(angles)
    direction = np.column_stack((np.sin(radians), np.cos(radians)))
    position = radius[:, None] * direction
    goal = -100000. * direction
    heading = (angles + 180.) % 360.
    cas_knots = 250. + rng.uniform(-4., 4., len(angles))
    return position, goal, heading, cas_knots


def initialize(work):
    work.mkdir(parents=True, exist_ok=True)
    config = work / 'experiment.cfg'
    config.write_text((ROOT / 'bluesky/resources/default.cfg').read_text(encoding='utf-8') +
                      "\nenabled_plugins=[]\nprefer_compiled=False\nsimdt=0.5\n"
                      "performance_model='openap'\nasas_dt=1.0\n"
                      "asas_dtlookahead=180.0\nasas_marh=1.10\nasas_marv=1.10\n",
                      encoding='utf-8')
    bs.init(mode='sim', detached=True, workdir=str(work), configfile=str(config))
    from bluesky.core import plugin
    assert plugin.Plugin.load('COORD')[0]
    from reference import MVP_TAS  # register the additional diagnostic baseline


def build_case(name, seed, method):
    bs.sim.reset()
    position, goal, heading, knots = case_data(name, seed)
    lat, lon = latlon(position)
    goal_lat, goal_lon = latlon(goal)
    for i in range(len(position)):
        acid = f'AC{i:02d}'
        assert bs.traf.cre(acid, 'A320', float(lat[i]), float(lon[i]),
                           float(heading[i]), 33000. * 0.3048, float(knots[i] * NM / 3600.))
        route = bs.traf.ap.route[i]
        route.addwpt(i, f'GOAL{i:02d}', route.wplatlon, float(goal_lat[i]), float(goal_lon[i]))
        route.direct(i, route.wpname[0])
        bs.traf.swlnav[i] = True
    assert bs.traf.cd.setmethod('STATEBASED')[0]
    assert bs.traf.cr.setmethod(method)[0]
    if method in ('MVP', 'MVP_TAS'):
        # Identical horizontal scope: original MVP, its BOTH (speed+track) mode.
        bs.traf.cr.setresometh('BOTH')
        bs.traf.cr.swprio = False
    return position, goal


def run_case(name, seed, method, duration, outdir):
    start, goal = build_case(name, seed, method)
    n = len(start)
    i, j = np.triu_indices(n, 1)
    last_target_h = bs.traf.ap.trk.copy()
    last_target_v = bs.traf.ap.tas.copy()
    previous_active = np.zeros(n, dtype=bool)
    last_hsign = np.zeros(n)
    last_vsign = np.zeros(n)
    instructions = heading_reversals = speed_reversals = 0
    los_events = 0
    los_pair_seconds = 0.0
    was_los = np.zeros(len(i), dtype=bool)
    minimum_distance = np.inf
    samples, resolution_times = [], []
    previous_position = local_position()
    distance = np.zeros(n)
    original_update = bs.traf.cr.update

    def timed_update(*args):
        tick = perf_counter()
        original_update(*args)
        resolution_times.append(perf_counter() - tick)

    # Wrap the instance proxy callable, without modifying MVP source or dynamics.
    bs.traf.cr.__dict__['update'] = timed_update
    started = perf_counter()
    try:
        while bs.sim.simt < duration - 1e-9:
            bs.sim.step()
            position = local_position()
            relative_start = previous_position[i] - previous_position[j]
            relative_end = position[i] - position[j]
            delta = relative_end - relative_start
            fraction = np.clip(-np.sum(relative_start * delta, axis=1) /
                               np.maximum(np.sum(delta * delta, axis=1), 1e-15), 0., 1.)
            separation = np.linalg.norm(relative_start + fraction[:, None] * delta, axis=1)
            # All benchmark aircraft are level at the same altitude.
            threshold = np.maximum(bs.traf.cd.rpz[i], bs.traf.cd.rpz[j])
            los = separation < threshold - 1e-6
            los_events += int(np.sum(los & ~was_los))
            los_pair_seconds += float(np.sum(los) * bs.sim.simdt)
            was_los = los
            minimum_distance = min(minimum_distance, float(np.min(separation, initial=np.inf)))
            distance += np.linalg.norm(position - previous_position, axis=1)
            previous_position = position

            # A combined per-aircraft clearance is counted when its commanded
            # track/speed changes >= 1 degree / 1 m/s while ASAS is/was active.
            h = bs.traf.aporasas.trk
            v = bs.traf.aporasas.tas
            active = bs.traf.cr.active.copy()
            dh = (h - last_target_h + 180.) % 360. - 180.
            dv = v - last_target_v
            changed_h, changed_v = np.abs(dh) >= 1., np.abs(dv) >= 1.
            changed = (changed_h | changed_v) & (active | previous_active)
            instructions += int(np.sum(changed))
            hsign, vsign = np.sign(dh), np.sign(dv)
            heading_reversals += int(np.sum(changed & changed_h & (last_hsign * hsign < 0)))
            speed_reversals += int(np.sum(changed & changed_v & (last_vsign * vsign < 0)))
            last_hsign = np.where(changed & changed_h, hsign, last_hsign)
            last_vsign = np.where(changed & changed_v, vsign, last_vsign)
            last_target_h = np.where(changed | ~active, h, last_target_h)
            last_target_v = np.where(changed | ~active, v, last_target_v)
            previous_active = active
            if abs(bs.sim.simt % 5.0) < 1e-8:
                samples.append({'time': bs.sim.simt, 'position': position.tolist(),
                                'track': bs.traf.trk.tolist(), 'speed': bs.traf.gs.tolist(),
                                'command_track': h.tolist(), 'command_speed': v.tolist(),
                                'active': active.tolist()})
    finally:
        bs.traf.cr.__dict__['update'] = original_update
    wall = perf_counter() - started
    progress = np.linalg.norm(goal - start, axis=1) - np.linalg.norm(goal - local_position(), axis=1)
    counts = dict(bs.traf.cr.status_counts) if method == 'COORD' else {}
    result = {
        'case': name, 'seed': seed, 'method': method, 'aircraft': n,
        'duration_s': duration, 'los_events': los_events, 'los_pair_seconds': los_pair_seconds,
        'minimum_distance_nm': minimum_distance / NM, 'clearances': instructions,
        'heading_reversals': heading_reversals, 'speed_reversals': speed_reversals,
        'extra_distance_nm': float(np.sum(np.maximum(0., distance - progress)) / NM),
        'mean_progress_nm': float(np.mean(progress) / NM),
        'resolution_p50_ms': float(np.percentile(resolution_times, 50) * 1000.),
        'resolution_p95_ms': float(np.percentile(resolution_times, 95) * 1000.),
        'resolution_max_ms': float(np.max(resolution_times) * 1000.),
        'fallback_updates': sum(v for k, v in counts.items() if 'fallback' in k),
        'timeout_updates': sum(v for k, v in counts.items() if 'timeout' in k),
        'wall_s': wall, 'status_counts': counts,
    }
    trajectory = {'case': name, 'seed': seed, 'method': method, 'start': start.tolist(),
                  'goal': goal.tolist(), 'ids': list(bs.traf.id), 'samples': samples}
    (outdir / f'{name}-{seed}-{method}.json').write_text(json.dumps(trajectory), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=['pilot', 'validation', 'stress'], default='pilot')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--duration', type=float, default=600.)
    parser.add_argument('--envelope-baseline', action='store_true',
                        help='Also test MVP with its CAS/TAS clipping difference removed')
    args = parser.parse_args()
    outdir = args.output or ROOT / 'research/coord/results' / args.suite
    outdir.mkdir(parents=True, exist_ok=True)
    initialize(WORK / args.suite)
    if args.suite == 'pilot':
        cases = [(case, 1) for case in ('headon2', 'crossing2', 'merge3', 'radial4', 'parallel6')]
    elif args.suite == 'validation':
        cases = [(case, seed) for case in ('headon2', 'crossing2', 'merge3', 'radial4', 'radial6')
                 for seed in (11, 29, 47)] + [('parallel6', 11)]
    else:
        cases = [(case, seed) for case in ('radial8', 'radial12', 'radial16') for seed in (101, 203)]
    results = []
    for case, seed in cases:
        methods = ('MVP', 'MVP_TAS', 'COORD') if args.envelope_baseline else ('MVP', 'COORD')
        for method in methods:
            results.append(run_case(case, seed, method, args.duration, outdir))
            (outdir / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    fields = [key for key in results[0] if key != 'status_counts']
    with (outdir / 'results.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    metadata = {'python': sys.version, 'platform': platform.platform(),
                'numpy': np.__version__, 'simulation_dt_s': 0.5,
                'asas_dt_s': 1.0, 'lookahead_s': 180., 'suite': args.suite,
                'duration_s': args.duration, 'separation_nm': 5.,
                'note': 'Synthetic, level, no-wind A320 scenarios. Not operational validation.'}
    (outdir / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
