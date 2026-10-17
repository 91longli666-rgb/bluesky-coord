"""COORD: centralised horizontal resolution and stable, validated clearances.

Load with PLUGINS LOAD COORD, then RESO COORD. Research scope: airborne,
level, no-wind traffic, local airspace. Unsupported states use the original
MVP algorithm and explicitly report fallback; that fallback is not certified.
"""
from dataclasses import replace

import numpy as np
import bluesky as bs
from bluesky import stack
from bluesky.core.entity import getproxied
from bluesky.traffic.asas import MVP, ResumeNavigation
from bluesky.tools import aero
from bluesky.plugins.coord_core import Config, Coordinator, FleetState


def init_plugin():
    config = {'plugin_name': 'COORD', 'plugin_type': 'sim'}
    return config


class CoordResume(ResumeNavigation):
    """COORD itself validates and activates recovery; do not overwrite its mask."""
    def resumenav(self, conf, ownship, intruder):
        pass


class Coord(MVP):
    def __init__(self):
        super().__init__()
        self.coordinator = Coordinator()
        self.last_update = -np.inf
        self.last_status = 'idle'
        self.fallback_active = False
        self.retry_after = -np.inf
        self.update_times = []
        self.status_counts = {}

    def reset(self):
        super().reset()
        self.coordinator.reset()
        self.last_update = -np.inf
        self.last_status = 'idle'
        self.fallback_active = False
        self.retry_after = -np.inf
        self.update_times.clear()
        self.status_counts.clear()

    def delete(self, idx):
        # IDs, not array positions, key persistent commands. Deleting then
        # reusing the same callsign must not resurrect an old clearance.
        for i in np.atleast_1d(idx):
            key = bs.traf.id[int(i)]
            self.coordinator.commands.pop(key, None)
            self.coordinator.last_change.pop(key, None)
        super().delete(idx)
        self.retry_after = -np.inf

    @property
    def vsactive(self):
        return self.active if self.fallback_active else np.zeros(len(self.active), dtype=bool)

    @property
    def altactive(self):
        return self.active if self.fallback_active else np.zeros(len(self.active), dtype=bool)

    def _fallback(self, conf, ownship, intruder, reason):
        if not self.fallback_active:
            # Do not leave COORD's activation mask latched when the original
            # resume-navigation implementation takes over.
            self.active[:] = False
        self.fallback_active = True
        ResumeNavigation.select_default()
        self.coordinator.reset()
        if conf.confpairs:
            values = super().resolve(conf, ownship, intruder)
            if all(np.all(np.isfinite(value)) for value in values):
                self.trk, self.tas, self.vs, self.alt = values
            else:
                # A finite-command guard is a last resort, not a safe solution.
                self.trk = ownship.ap.trk.copy()
                self.tas = ownship.gs.copy()
                self.vs = ownship.ap.vs.copy()
                self.alt = ownship.ap.alt.copy()
                self.active[:] = False
                reason += ':nonfinite_mvp'
        elif not np.any(self.active):
            self.trk, self.tas = ownship.ap.trk.copy(), ownship.gs.copy()
            self.vs, self.alt = ownship.ap.vs.copy(), ownship.ap.alt.copy()
        self.last_status = reason
        self.status_counts[reason] = self.status_counts.get(reason, 0) + 1

    def update(self, conf, ownship, intruder):
        if not ownship.ntraf:
            return
        from time import perf_counter
        started = perf_counter()
        unsupported = (
            getproxied(ownship.perf).__class__.__name__ != 'OpenAP'
            or ownship.wind.winddim > 0 or np.any(np.abs(ownship.vs) > 0.1)
            or np.any(np.abs(ownship.ap.alt - ownship.alt) > 5.0)
            or np.any(self.resooffac) or np.any(self.noresoac)
            or np.any(ownship.alt < 3000.0)
            or np.any(ownship.perf.lifttype != 1))
        if unsupported:
            self._fallback(conf, ownship, intruder, 'unsupported_fallback')
            self.update_times.append(perf_counter() - started)
            return

        if float(bs.sim.simt) < self.retry_after:
            # Original MVP remains active on EVERY update. This is a solver
            # retry backoff, not a pause in conflict detection or resolution.
            self._fallback(conf, ownship, intruder, 'retry_wait_fallback')
            self.update_times.append(perf_counter() - started)
            return

        # Local equirectangular projection, wrapped at the date line.
        latitude = np.deg2rad(ownship.lat)
        origin_lon = ownship.lon[0]
        delta_lon = (ownship.lon - origin_lon + 180.0) % 360.0 - 180.0
        position = aero.Rearth * np.column_stack((
            np.deg2rad(delta_lon) * np.cos(np.mean(latitude)), latitude - np.mean(latitude)))
        if np.max(np.linalg.norm(position, axis=1), initial=0.0) > 250000.0:
            self._fallback(conf, ownship, intruder, 'airspace_extent_fallback')
            self.update_times.append(perf_counter() - started)
            return
        # OpenAP's vmin/vmax are CAS for fixed-wing aircraft. Convert them to
        # TAS before using them as no-wind ground-speed optimisation bounds.
        minimum_speed = aero.vcas2tas(ownship.perf.vmin, ownship.alt)
        maximum_speed = np.minimum(aero.vcas2tas(ownship.perf.vmax, ownship.alt),
                                   aero.vmach2tas(ownship.perf.mmo, ownship.alt))
        bank = np.where(ownship.ap.turnphi > ownship.eps ** 2,
                        ownship.ap.turnphi, ownship.ap.bankdef)
        turn_rate = np.rad2deg(aero.g0 * np.tan(bank) / np.maximum(ownship.tas, 1.0))
        state = FleetState(
            tuple(ownship.id), position, ownship.alt.copy(), ownship.trk.copy(),
            ownship.gs.copy(), ownship.ap.trk.copy(), ownship.ap.tas.copy(),
            minimum_speed, maximum_speed, np.maximum(turn_rate, 0.1),
            np.maximum(ownship.perf.axmax, 0.1), conf.rpz.copy(), conf.hpz.copy())
        try:
            plan = self.coordinator.plan(state, float(bs.sim.simt))
        except ValueError as exc:
            self._fallback(conf, ownship, intruder, 'invalid_state_fallback:' + str(exc))
            self.update_times.append(perf_counter() - started)
            return
        if not plan.feasible:
            self.retry_after = float(bs.sim.simt) + self.coordinator.config.retry_interval
            self._fallback(conf, ownship, intruder, plan.status)
        else:
            if self.fallback_active:
                self.active[:] = False
            self.fallback_active = False
            CoordResume.select()
            self.trk, self.tas = plan.track, plan.speed
            self.vs, self.alt = ownship.ap.vs.copy(), ownship.ap.alt.copy()
            self.active = plan.active
            self.last_status = plan.status
            self.status_counts[plan.status] = self.status_counts.get(plan.status, 0) + 1
        self.last_update = float(bs.sim.simt)
        self.update_times.append(perf_counter() - started)

    @stack.command(name='COORDSTATUS')
    def status(self):
        """Show controller state, timings, fallback and configuration."""
        plan = self.coordinator.last_plan
        ratio = 'n/a' if plan is None else f'{plan.minimum_ratio:.3f}'
        return True, (f'COORD status={self.last_status}; predicted separation ratio={ratio}; '
                      f'active={int(np.sum(self.active))}; counts={self.status_counts}; '
                      f'config={self.coordinator.config}')

    @stack.command(name='COORDCFG')
    def configure(self, horizon: float = None, hold: float = None, budget: float = None):
        """COORDCFG [horizon_seconds, minimum_hold_seconds, solver_budget_seconds]."""
        if horizon is None and hold is None and budget is None:
            return True, str(self.coordinator.config)
        changes = {name: value for name, value in
                   (('horizon', horizon), ('minimum_hold', hold), ('time_budget', budget))
                   if value is not None}
        try:
            config = replace(self.coordinator.config, **changes)
        except ValueError as exc:
            return False, str(exc)
        self.coordinator = Coordinator(config)
        self.retry_after = -np.inf
        return True, f'COORD configuration updated: {config}'
