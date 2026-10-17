"""Create scenario/config artifacts from the same seeded benchmark generator."""
from pathlib import Path
from benchmark import case_data, latlon


def main():
    root = Path(__file__).resolve().parents[2]
    target = root / 'scenario/COORD'
    target.mkdir(parents=True, exist_ok=True)
    for case in ('headon2', 'crossing2', 'merge3', 'radial4', 'radial6'):
        position, goal, heading, speed = case_data(case, 1)
        lat, lon = latlon(position)
        glat, glon = latlon(goal)
        commands = ['DT 0.5', 'PLUGINS LOAD COORD', 'CDMETHOD STATEBASED']
        for i in range(len(position)):
            commands += [f'CRE AC{i:02d},A320,{lat[i]:.9f},{lon[i]:.9f},{heading[i]:.9f},33000,{speed[i]:.9f}',
                         f'ADDWPT AC{i:02d},{glat[i]:.9f},{glon[i]:.9f}',
                         f'LNAV AC{i:02d},ON']
        commands += ['RESO COORD', 'PAN 52,4', 'ZOOM 0.5', 'OP']
        lines = ['# COORD demo; same generator as the development benchmark.']
        lines += ['00:00:00>' + command for command in commands]
        lines += ['00:10:00>HOLD', '00:10:00>COORDSTATUS']
        (target / (case + '.scn')).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    defaults = (root / 'bluesky/resources/default.cfg').read_text(encoding='utf-8')
    (root / 'research/coord/demo.cfg').write_text(defaults +
        "\nenabled_plugins=[]\nprefer_compiled=False\nsimdt=0.5\nasas_dt=1.0\n"
        "asas_dtlookahead=180.0\nasas_marh=1.10\nasas_marv=1.10\n",
        encoding='utf-8')


if __name__ == '__main__':
    main()
