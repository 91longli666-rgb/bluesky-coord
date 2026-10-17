"""An additional diagnostic baseline, NOT a replacement for upstream MVP.

OpenAP fixed-wing vmin/vmax are CAS, while MVP returns a ground-speed command.
The original MVP directly uses those fields as ground-speed clipping limits.
MVP_TAS changes ONLY the clipping bounds to the OpenAP true-airspeed envelope,
in these no-wind experiments. Including it separates that unit difference from
the benefit/cost of the new coordination and holding logic.
"""
from bluesky.traffic.asas import MVP


class View:
    def __init__(self, original, **changes):
        self.original = original
        self.__dict__.update(changes)

    def __getattr__(self, name):
        return getattr(self.original, name)


class MVP_TAS(MVP):
    def resolve(self, conf, ownship, intruder):
        vmin, vmax, _, _ = ownship.perf.currentlimits()
        performance = View(ownship.perf, vmin=vmin, vmax=vmax)
        ownview = View(ownship, perf=performance)
        intview = ownview if intruder is ownship else intruder
        return super().resolve(conf, ownview, intview)
