"""Actual BlueSky lifecycle, command selection and fallback integration."""
from pathlib import Path

import numpy as np
import pytest
import bluesky as bs


@pytest.fixture(scope='module', autouse=True)
def simulation(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    work = tmp_path_factory.mktemp('coord-bluesky')
    config = work / 'experiment.cfg'
    config.write_text((root / 'bluesky/resources/default.cfg').read_text(encoding='utf-8') +
                      "\nenabled_plugins=[]\nprefer_compiled=False\nsimdt=0.5\n"
                      "performance_model='openap'\nasas_dt=1.0\n", encoding='utf-8')
    bs.init(mode='sim', detached=True, workdir=str(work), configfile=str(config))
    from bluesky.core import plugin
    assert plugin.Plugin.load('COORD')[0]


@pytest.fixture(autouse=True)
def reset_simulation(simulation):
    bs.sim.reset()


def create(acid='TEST', lon=4.0, heading=90.0):
    assert bs.traf.cre(acid, 'A320', 52., lon, heading, 10058.4, 128.61)


def advance(seconds=4):
    for _ in range(round(seconds / 0.5)):
        bs.sim.step()


def test_plugin_selects_and_preserves_vertical_autopilot():
    create()
    assert bs.traf.cr.setmethod('COORD')[0]
    advance()
    assert bs.traf.cr.last_status == 'held'
    assert not bs.traf.cr.active.any()
    assert not bs.traf.cr.vsactive.any()
    assert not bs.traf.cr.altactive.any()
    assert np.all(np.isfinite(bs.traf.aporasas.tas))


def test_select_after_creation_delete_recreate_and_reset():
    create('FIRST')
    create('SECOND', lon=4.5)
    assert bs.traf.cr.setmethod('COORD')[0]
    advance()
    bs.traf.cr.coordinator.commands['FIRST'] = (180., 240.)
    bs.traf.delete(0)
    assert 'FIRST' not in bs.traf.cr.coordinator.commands
    create('FIRST', lon=3.5)
    advance()
    assert len(bs.traf.cr.active) == 2
    assert bs.traf.cr.coordinator.commands.get('FIRST', (90, 0))[0] != 180
    bs.sim.reset()
    assert not bs.traf.ntraf
    from bluesky.plugins.coord import Coord
    assert not Coord.implinstance().coordinator.commands


def test_unsupported_vertical_state_reports_fallback():
    create()
    assert bs.traf.cr.setmethod('COORD')[0]
    bs.traf.selalt[0] += 1000
    advance()
    assert bs.traf.cr.fallback_active
    assert bs.traf.cr.last_status == 'unsupported_fallback'


def test_configuration_validation_is_atomic():
    create()
    assert bs.traf.cr.setmethod('COORD')[0]
    original = bs.traf.cr.coordinator.config
    assert not bs.traf.cr.configure(horizon=-1)[0]
    assert bs.traf.cr.coordinator.config == original
    assert bs.traf.cr.configure(horizon=240, hold=30, budget=0.5)[0]
    assert bs.traf.cr.coordinator.config.horizon == 240


def test_can_switch_back_to_original_mvp():
    create()
    assert bs.traf.cr.setmethod('COORD')[0]
    advance()
    assert bs.traf.cr.setmethod('MVP')[0]
    advance()
    from bluesky.traffic.asas import ConflictResolution
    assert ConflictResolution.selected().__name__ == 'MVP'


def test_fallback_does_not_leave_coordinator_activation_latched():
    create()
    assert bs.traf.cr.setmethod('COORD')[0]
    advance()
    bs.traf.cr.active[:] = True
    bs.traf.cr.coordinator.commands['TEST'] = (120., 220.)
    bs.traf.selalt[0] += 1000
    advance()
    assert bs.traf.cr.fallback_active
    assert not bs.traf.cr.active.any()
    assert not bs.traf.cr.coordinator.commands


def test_generated_scenario_loads_in_actual_headless_simulator():
    from bluesky.stack import simstack
    root = Path(__file__).resolve().parents[2]
    scenario = root / 'scenario/COORD/radial4.scn'
    result = simstack.ic(str(scenario))
    assert result is None or result[0]
    advance(10)
    from bluesky.traffic.asas import ConflictResolution
    assert bs.traf.ntraf == 4
    assert bs.traf.swlnav.all()
    assert ConflictResolution.selected().__name__ == 'Coord'
    assert bs.traf.cr.last_status == 'held'


def test_failed_solver_is_throttled_while_resolution_keeps_updating(monkeypatch):
    from types import SimpleNamespace
    create()
    assert bs.traf.cr.setmethod('COORD')[0]
    calls = []

    def fail(*args):
        calls.append(bs.sim.simt)
        return SimpleNamespace(feasible=False, status='infeasible_fallback')

    monkeypatch.setattr(bs.traf.cr.coordinator, 'plan', fail)
    advance(4)
    assert len(calls) == 1
    assert bs.traf.cr.status_counts['retry_wait_fallback'] >= 2
    assert np.all(np.isfinite(bs.traf.aporasas.tas))
    advance(3)
    assert len(calls) == 2
