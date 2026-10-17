"""Run a small actual BlueSky headless simulation (no UI dependency)."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / 'cache' / 'matplotlib'))

import bluesky as bs
from bluesky.core import plugin


def main():
    work = ROOT / 'cache' / 'coord-smoke'
    work.mkdir(parents=True, exist_ok=True)
    os.environ['MPLCONFIGDIR'] = str(work / 'mpl-cache')
    config = work / 'experiment.cfg'
    defaults = (ROOT / 'bluesky/resources/default.cfg').read_text(encoding='utf-8')
    config.write_text(defaults + "\nenabled_plugins=[]\nprefer_compiled=False\nsimdt=0.5\n"
                      "performance_model='openap'\nasas_dt=1.0\n", encoding='utf-8')
    bs.init(mode='sim', detached=True, workdir=str(work), configfile=str(config))
    bs.sim.reset()
    print('PLUGIN', plugin.Plugin.load('COORD'))
    print('CREATE', bs.traf.cre('TEST', 'A320', 52., 4., 90., 10058.4, 128.61))
    print('RESO', bs.traf.cr.setmethod('COORD'))
    for _ in range(20):
        bs.sim.step()
    print('SIMULATION', bs.sim.simt, bs.traf.tas, bs.traf.cr.status())


if __name__ == '__main__':
    main()
