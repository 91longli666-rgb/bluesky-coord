"""Centralised, bounded receding-horizon conflict resolution (SI units).

MIT licensed, like BlueSky. This is a research controller for level, no-wind
traffic. Feasibility refers to the prediction model, not a safety guarantee.
"""
from dataclasses import dataclass
from time import perf_counter

import numpy as np
from scipy.optimize import minimize


def angle_difference(a, b):
    """Signed shortest difference a - b, in degrees."""
    return (np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0


@dataclass(frozen=True)
class Config:
    horizon: float = 180.0
    prediction_step: float = 5.0
    separation_margin: float = 1.10
    max_heading_change: float = 35.0
    speed_fraction: float = 0.15
    minimum_hold: float = 20.0
    recovery_interval: float = 10.0
    retry_interval: float = 5.0
    heading_deadband: float = 1.0
    speed_deadband: float = 1.0
    switch_weight: float = 0.6
    max_controlled: int = 12
    max_iterations: int = 70
    time_budget: float = 1.0

    def __post_init__(self):
        for name in ('horizon', 'prediction_step', 'max_heading_change',
                     'heading_deadband', 'speed_deadband', 'time_budget'):
            if not np.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if self.prediction_step > self.horizon:
            raise ValueError('prediction_step exceeds horizon')
        if not 1.0 < self.separation_margin or not np.isfinite(self.separation_margin):
            raise ValueError('separation_margin must exceed one')
        if not 0.0 < self.speed_fraction < 1.0:
            raise ValueError('speed_fraction must be in (0, 1)')
        if any(not np.isfinite(x) or x < 0 for x in
               (self.minimum_hold, self.recovery_interval, self.retry_interval, self.switch_weight)):
            raise ValueError('hold/recovery/weight must be finite and nonnegative')
        if self.max_controlled < 1 or self.max_iterations < 1:
            raise ValueError('controller limits must be positive')


@dataclass
class FleetState:
    ids: tuple[str, ...]
    position: np.ndarray             # (n, 2), east/north in a local metric plane
    altitude: np.ndarray             # m, constant over horizon
    track: np.ndarray                # deg, actual ground track
    speed: np.ndarray                # m/s, actual ground speed (no wind)
    reference_track: np.ndarray      # nominal autopilot route track
    reference_speed: np.ndarray      # nominal autopilot true airspeed, m/s
    minimum_speed: np.ndarray        # TRUE airspeed envelope, m/s (not CAS)
    maximum_speed: np.ndarray
    turn_rate: np.ndarray            # positive deg/s at current bank/speed
    acceleration: np.ndarray         # positive m/s^2
    horizontal_separation: np.ndarray
    vertical_separation: np.ndarray

    def validate(self):
        n = len(self.ids)
        if len(set(self.ids)) != n:
            raise ValueError('aircraft identifiers must be unique')
        for name in self.__dataclass_fields__:
            if name == 'ids':
                continue
            value = np.asarray(getattr(self, name), dtype=float)
            expected = (n, 2) if name == 'position' else (n,)
            if value.shape != expected or not np.all(np.isfinite(value)):
                raise ValueError(f'invalid {name}: expected finite {expected}')
            setattr(self, name, value)
        if np.any(self.minimum_speed <= 0) or np.any(self.speed <= 0):
            raise ValueError('only airborne, positive-speed traffic is supported')
        if np.any(self.maximum_speed < self.minimum_speed):
            raise ValueError('invalid speed envelope')
        if any(np.any(getattr(self, name) <= 0) for name in
               ('turn_rate', 'acceleration', 'horizontal_separation', 'vertical_separation')):
            raise ValueError('response and separation bounds must be positive')


@dataclass
class Plan:
    track: np.ndarray
    speed: np.ndarray
    active: np.ndarray
    feasible: bool
    status: str
    minimum_ratio: float
    elapsed: float
    controlled: int = 0
    solver_success: bool = False
    timed_out: bool = False


def predict(state, track, speed, config):
    """Rate-limited response, midpoint integration, piecewise linear paths.

    The number of intervals is rounded UP, so the whole requested horizon is
    covered. No instantaneous heading or speed changes are assumed.
    """
    count = int(np.ceil(config.horizon / config.prediction_step))
    dt = config.horizon / count
    time = (np.arange(count) + 0.5)[:, None] * dt
    delta = angle_difference(track, state.track)[None, :]
    heading = state.track[None, :] + np.clip(
        delta, -state.turn_rate[None, :] * time, state.turn_rate[None, :] * time)
    velocity = state.speed[None, :] + np.clip(
        np.asarray(speed)[None, :] - state.speed[None, :],
        -state.acceleration[None, :] * time, state.acceleration[None, :] * time)
    radians = np.deg2rad(heading)
    displacement = dt * velocity[..., None] * np.stack(
        (np.sin(radians), np.cos(radians)), axis=-1)
    path = np.concatenate((np.zeros((1, len(state.ids), 2)),
                           np.cumsum(displacement, axis=0)), axis=0)
    return path + state.position[None, :, :]


def aircraft_pairs(state):
    """All potentially vertically conflicting pairs, including background aircraft."""
    i, j = np.triu_indices(len(state.ids), 1)
    keep = np.abs(state.altitude[i] - state.altitude[j]) < np.maximum(
        state.vertical_separation[i], state.vertical_separation[j])
    return i[keep], j[keep]


def separation_ratios(path, state, pairs):
    """Exact CPA on every linear segment; not just distances at sample times."""
    i, j = pairs
    if not len(i):
        return np.empty(0)
    relative = path[:, i, :] - path[:, j, :]
    delta = np.diff(relative, axis=0)
    start = relative[:-1]
    denominator = np.sum(delta * delta, axis=-1)
    fraction = np.clip(-np.sum(start * delta, axis=-1) /
                       np.maximum(denominator, 1e-15), 0.0, 1.0)
    distance = np.linalg.norm(start + fraction[..., None] * delta, axis=-1)
    required = np.maximum(state.horizontal_separation[i], state.horizontal_separation[j])
    return np.min(distance, axis=0) / required


def terminal_ratios(path, state, pairs, track, speed):
    """CPA of constant target velocities beyond the finite prediction horizon.

    A terminal condition prevents a clearance that only pushes a conflict just
    outside the moving window. It assumes the commanded velocity is maintained;
    recovery is checked again before releasing a clearance.
    """
    i, j = pairs
    radians = np.deg2rad(track)
    velocity = np.asarray(speed)[:, None] * np.column_stack((np.sin(radians), np.cos(radians)))
    relative = path[-1, i] - path[-1, j]
    dv = velocity[i] - velocity[j]
    t = np.maximum(0., -np.sum(relative * dv, axis=1) /
                   np.maximum(np.sum(dv * dv, axis=1), 1e-15))
    distance = np.linalg.norm(relative + t[:, None] * dv, axis=1)
    return distance / np.maximum(state.horizontal_separation[i], state.horizontal_separation[j])


class PlanningTimeout(Exception):
    pass


class Coordinator:
    """Joint constrained optimisation, validated incumbent, and guarded recovery.

    Only aircraft in threatened pairs become decision variables. Constraints
    still check ALL pairs, including aircraft outside that set. Feasible held
    commands take priority over replanning; hold times NEVER suppress a needed
    conflict intervention. SLSQP is local, so explicit infeasible/timeout results
    are returned to the BlueSky adapter for fallback.
    """
    def __init__(self, config=None):
        self.config = config or Config()
        self.commands = {}
        self.last_change = {}
        self.last_recovery = -np.inf
        self.last_plan = None

    def reset(self):
        self.commands.clear()
        self.last_change.clear()
        self.last_recovery = -np.inf
        self.last_plan = None

    def plan(self, state, now):
        started = perf_counter()
        state.validate()
        if not np.isfinite(now):
            raise ValueError('simulation time must be finite')
        cfg = self.config
        reference_speed = np.clip(state.reference_speed, state.minimum_speed, state.maximum_speed)
        reference_track = state.reference_track % 360.0
        ids = set(state.ids)
        self.commands = {k: v for k, v in self.commands.items() if k in ids}
        self.last_change = {k: v for k, v in self.last_change.items() if k in ids}
        track = np.array([self.commands.get(k, (h, v))[0]
                          for k, h, v in zip(state.ids, reference_track, reference_speed)])
        speed = np.array([self.commands.get(k, (h, v))[1]
                          for k, h, v in zip(state.ids, reference_track, reference_speed)])
        speed = np.clip(speed, state.minimum_speed, state.maximum_speed)
        pairs = aircraft_pairs(state)

        def ratios(h, v, terminal=False):
            path = predict(state, h, v, cfg)
            finite = separation_ratios(path, state, pairs)
            return np.minimum(finite, terminal_ratios(path, state, pairs, h, v)) if terminal else finite

        def safe(h, v):
            return bool(np.all(ratios(h, v, terminal=True) >= cfg.separation_margin - 1e-6))

        def finish(h, v, feasible, status, controlled=0, success=False, timeout=False):
            active = ((np.abs(angle_difference(h, reference_track)) > 1e-5) |
                      (np.abs(v - reference_speed) > 1e-5))
            for idx, key in enumerate(state.ids):
                old = self.commands.get(key, (reference_track[idx], reference_speed[idx]))
                if abs(angle_difference(h[idx], old[0])) > 1e-6 or abs(v[idx] - old[1]) > 1e-6:
                    self.last_change[key] = now
                # Only store intervention clearances. Normal LNAV guidance must
                # follow the autopilot instead of freezing an old route track.
                if active[idx]:
                    self.commands[key] = (float(h[idx]), float(v[idx]))
                else:
                    self.commands.pop(key, None)
                    self.last_change.pop(key, None)
            minimum = float(np.min(ratios(h, v), initial=np.inf))
            result = Plan(h.copy(), v.copy(), active, feasible, status, minimum,
                          perf_counter() - started, controlled, success, timeout)
            self.last_plan = result
            return result

        initial_ratios = ratios(track, speed)
        if np.all(initial_ratios >= cfg.separation_margin - 1e-6):
            recovered = False
            if now - self.last_recovery >= cfg.recovery_interval:
                self.last_recovery = now
                # Deterministic call-sign order also makes deletion/reordering safe.
                for idx in sorted(range(len(state.ids)), key=lambda k: state.ids[k]):
                    key = state.ids[idx]
                    if now - self.last_change.get(key, -np.inf) < cfg.minimum_hold:
                        continue
                    if (abs(angle_difference(track[idx], reference_track[idx])) < cfg.heading_deadband
                            and abs(speed[idx] - reference_speed[idx]) < cfg.speed_deadband):
                        continue
                    candidate_h, candidate_v = track.copy(), speed.copy()
                    candidate_h[idx], candidate_v[idx] = reference_track[idx], reference_speed[idx]
                    if safe(candidate_h, candidate_v):
                        track, speed = candidate_h, candidate_v
                        recovered = True
            return finish(track, speed, True, 'recovered' if recovered else 'held')

        # Include aircraft threatened beyond the finite window when an
        # intervention is already needed. Otherwise the first pair could be
        # optimised while soon-arriving neighbours are incorrectly held fixed.
        terminal_current = terminal_ratios(predict(state, track, speed, cfg),
                                           state, pairs, track, speed)
        bad = np.minimum(initial_ratios, terminal_current) < cfg.separation_margin
        indices = np.unique(np.concatenate((pairs[0][bad], pairs[1][bad])))
        indices = np.array(sorted(indices, key=lambda k: state.ids[k]), dtype=int)
        if len(indices) > cfg.max_controlled:
            return finish(track, speed, False, 'capacity_fallback', len(indices))

        base_h, base_v = track.copy(), speed.copy()
        speed_scale = np.maximum(reference_speed[indices] * cfg.speed_fraction, 1.0)
        ref_h, ref_v = reference_track[indices], reference_speed[indices]
        lower_speed = np.maximum(state.minimum_speed[indices], ref_v - speed_scale)
        upper_speed = np.minimum(state.maximum_speed[indices], ref_v + speed_scale)
        count = len(indices)
        bounds = [(-1.0, 1.0)] * count + list(zip(
            (lower_speed - ref_v) / speed_scale, (upper_speed - ref_v) / speed_scale))

        def encode(h, v):
            return np.concatenate((angle_difference(h[indices], ref_h) / cfg.max_heading_change,
                                   (v[indices] - ref_v) / speed_scale))

        def decode(z):
            h, v = base_h.copy(), base_v.copy()
            h[indices] = (ref_h + z[:count] * cfg.max_heading_change) % 360.0
            v[indices] = ref_v + z[count:] * speed_scale
            return h, v

        previous = encode(base_h, base_v)
        low, high = np.array(bounds).T
        previous = np.clip(previous, low, high)
        deadline = started + cfg.time_budget
        best = None
        best_cost = np.inf
        solver_success = False
        timed_out = False

        def check_time():
            if perf_counter() >= deadline:
                raise PlanningTimeout()

        def objective(z):
            check_time()
            # Penalise route deviation, speed deviation, and command switching.
            return float(np.dot(z[:count], z[:count]) + 1.5 * np.dot(z[count:], z[count:])
                         + cfg.switch_weight * np.dot(z - previous, z - previous))

        def constraint(z):
            nonlocal best, best_cost
            check_time()
            h, v = decode(z)
            # Small planning hysteresis keeps model noise from retriggering the
            # optimiser immediately after an accepted boundary solution.
            residual = ratios(h, v, terminal=True) - (cfg.separation_margin + 0.03)
            if np.all(np.isfinite(residual)) and np.all(residual >= -1e-6):
                cost = objective(z)
                if cost < best_cost:
                    best, best_cost = z.copy(), cost
            return residual

        # Right/left coordinated turns break symmetric head-on/crossing cases.
        starts = [previous, np.concatenate((np.full(count, 0.6), np.zeros(count))),
                  np.concatenate((np.full(count, -0.6), np.zeros(count)))]
        try:
            # Validate seeds before spending time on local refinement.
            for seed in starts:
                constraint(np.clip(seed, low, high))
            for seed in starts:
                result = minimize(objective, np.clip(seed, low, high), method='SLSQP',
                                  bounds=bounds, constraints={'type': 'ineq', 'fun': constraint},
                                  options={'maxiter': cfg.max_iterations, 'ftol': 1e-5})
                # Never trust result.success without a separate feasibility check.
                residual = constraint(result.x)
                solver_success |= bool(result.success and np.all(residual >= -1e-6))
        except PlanningTimeout:
            timed_out = True

        if best is None:
            return finish(track, speed, False,
                          'timeout_fallback' if timed_out else 'infeasible_fallback',
                          count, timeout=timed_out)
        h, v = decode(best)
        return finish(h, v, True, 'feasible_timeout' if timed_out else 'optimized',
                      count, solver_success, timed_out)
