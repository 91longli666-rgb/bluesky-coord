import numpy as np
import pytest

from bluesky.plugins.coord_core import (
    Config, Coordinator, FleetState, aircraft_pairs, angle_difference,
    predict, separation_ratios, terminal_ratios, PlanningTimeout,
)


def fleet(position, track, altitude=None, ids=None):
    position = np.asarray(position, dtype=float)
    n = len(position)
    return FleetState(
        tuple(ids or [f'AC{i}' for i in range(n)]), position,
        np.full(n, 10000.) if altitude is None else np.asarray(altitude, dtype=float),
        np.asarray(track, dtype=float), np.full(n, 220.),
        np.asarray(track, dtype=float), np.full(n, 220.),
        np.full(n, 180.), np.full(n, 260.), np.full(n, 1.2), np.full(n, 0.6),
        np.full(n, 9260.), np.full(n, 304.8))


def test_between_sample_conflict_is_not_missed():
    state = fleet([[-100, 0], [100, 0]], [90, 270])
    state.horizontal_separation[:] = 50
    # Endpoints are separated, but the aircraft pass through each other.
    path = np.array([[[-100., 0], [100., 0]], [[100., 0], [-100., 0]]])
    assert separation_ratios(path, state, aircraft_pairs(state))[0] == pytest.approx(0)


def test_head_on_joint_plan_is_feasible_and_bounded():
    state = fleet([[-45000, 0], [45000, 0]], [90, 270])
    cfg = Config(horizon=240, time_budget=3)
    coordinator = Coordinator(cfg)
    plan = coordinator.plan(state, 0)
    assert plan.feasible, plan.status
    assert plan.minimum_ratio >= cfg.separation_margin - 1e-6
    assert np.all(np.abs(angle_difference(plan.track, state.reference_track)) <= 35.00001)
    assert np.all((plan.speed >= state.minimum_speed) & (plan.speed <= state.maximum_speed))
    # Same physical state: a validated command is held, not solved again.
    held = coordinator.plan(state, 1)
    assert held.status == 'held'
    np.testing.assert_allclose(held.track, plan.track)
    np.testing.assert_allclose(held.speed, plan.speed)


def test_background_aircraft_is_included_in_final_constraints():
    state = fleet([[-45000, 0], [45000, 0], [0, 35000]], [90, 270, 180])
    cfg = Config(horizon=240, time_budget=3)
    plan = Coordinator(cfg).plan(state, 0)
    assert plan.feasible, plan.status
    ratios = separation_ratios(predict(state, plan.track, plan.speed, cfg),
                               state, aircraft_pairs(state))
    assert np.all(ratios >= cfg.separation_margin - 1e-6)


def test_vertical_separation_does_not_trigger_horizontal_manoeuvre():
    state = fleet([[-20000, 0], [20000, 0]], [90, 270], altitude=[10000, 11000])
    plan = Coordinator().plan(state, 0)
    assert plan.feasible
    assert not plan.active.any()


def test_already_violated_separation_is_reported_not_claimed_safe():
    state = fleet([[0, 0], [100, 0]], [90, 270])
    plan = Coordinator(Config(time_budget=0.1)).plan(state, 0)
    assert not plan.feasible
    assert 'fallback' in plan.status
    assert plan.minimum_ratio < 1


def test_safety_replanning_overrides_hold_time():
    state = fleet([[-45000, 0], [45000, 0]], [90, 270])
    coordinator = Coordinator(Config(horizon=240, minimum_hold=1000, time_budget=3))
    # Pretend the prior clearance was issued just now and is now threatened.
    coordinator.commands = {'AC0': (90., 220.), 'AC1': (270., 220.)}
    coordinator.last_change = {'AC0': 0, 'AC1': 0}
    plan = coordinator.plan(state, 1)
    assert plan.feasible and plan.status != 'held'


def test_capacity_limit_reports_fallback():
    state = fleet([[-45000, 0], [45000, 0]], [90, 270])
    plan = Coordinator(Config(horizon=240, max_controlled=1)).plan(state, 0)
    assert not plan.feasible and plan.status == 'capacity_fallback'


def test_invalid_state_rejected_before_solver():
    state = fleet([[0, 0]], [0])
    state.speed[0] = np.nan
    with pytest.raises(ValueError, match='speed'):
        Coordinator().plan(state, 0)


def test_deleted_aircraft_does_not_reassign_commands_to_new_index():
    state = fleet([[0, 0]], [0], ids=['NEW'])
    coordinator = Coordinator()
    coordinator.commands['DELETED'] = (90, 240)
    plan = coordinator.plan(state, 0)
    assert not plan.active.any()
    assert 'DELETED' not in coordinator.commands


def test_empty_and_single_aircraft():
    empty = fleet(np.empty((0, 2)), [])
    assert Coordinator().plan(empty, 0).feasible
    assert not Coordinator().plan(fleet([[0, 0]], [0]), 0).active.any()


def test_heading_wrap_and_finite_response():
    state = fleet([[0, 0]], [359])
    state.validate()
    cfg = Config(horizon=10, prediction_step=1)
    path = predict(state, np.array([1]), np.array([240]), cfg)
    assert np.all(np.isfinite(path))
    assert path[-1, 0, 1] > 2100
    assert abs(path[-1, 0, 0]) < 50


def test_nominal_route_changes_are_not_frozen_as_clearances():
    coordinator = Coordinator()
    state = fleet([[0, 0]], [90])
    assert not coordinator.plan(state, 0).active.any()
    state.reference_track[:] = 92
    plan = coordinator.plan(state, 1)
    assert not plan.active.any()
    assert plan.track[0] == 92
    assert not coordinator.commands


def test_terminal_constraint_detects_conflict_beyond_window():
    state = fleet([[-45000, 0], [45000, 0]], [90, 270])
    cfg = Config(horizon=60)
    path = predict(state, state.track, state.speed, cfg)
    pairs = aircraft_pairs(state)
    assert separation_ratios(path, state, pairs)[0] > 1.1
    assert terminal_ratios(path, state, pairs, state.track, state.speed)[0] < 1e-6


def test_timeout_without_feasible_incumbent_is_explicit():
    state = fleet([[-45000, 0], [45000, 0]], [90, 270])
    plan = Coordinator(Config(horizon=240, time_budget=1e-9)).plan(state, 0)
    assert plan.timed_out and not plan.feasible
    assert plan.status == 'timeout_fallback'
    assert np.all(np.isfinite(plan.track))


def test_feasible_seed_survives_solver_timeout(monkeypatch):
    state = fleet([[-45000, 0], [45000, 0]], [90, 270])

    def timeout(*args, **kwargs):
        raise PlanningTimeout()

    monkeypatch.setattr('bluesky.plugins.coord_core.minimize', timeout)
    plan = Coordinator(Config(horizon=240, time_budget=3)).plan(state, 0)
    assert plan.feasible and plan.timed_out
    assert plan.minimum_ratio >= 1.1


@pytest.mark.parametrize('kwargs', [dict(time_budget=0), dict(horizon=np.nan),
    dict(minimum_hold=-1), dict(separation_margin=0.5), dict(speed_fraction=2)])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)
