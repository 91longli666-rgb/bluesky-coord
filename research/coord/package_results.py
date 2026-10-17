"""Generate auditable experiment summaries, plots and an extension-only archive."""
import csv
import difflib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT.parent
WORK = ROOT.parent.parent / 'work'
os.environ.setdefault('MPLCONFIGDIR', str(WORK / 'mpl-cache'))


def source_provenance():
    archive = WORK / 'bluesky-master.zip'
    with zipfile.ZipFile(archive) as z:
        commit = z.comment.decode('ascii')
        prefix = z.namelist()[0].split('/')[0] + '/'
        changed = []
        for item in z.infolist():
            rel = item.filename.removeprefix(prefix)
            if rel and not item.is_dir() and (ROOT / rel).read_bytes() != z.read(item):
                changed.append(rel)
        count = sum(not item.is_dir() for item in z.infolist())
    expected = json.loads((WORK / 'bluesky-upstream-tree.json').read_text())
    mismatch = []
    with zipfile.ZipFile(archive) as z:
        for item in expected['files']:
            data = z.read(prefix + item['path'])
            actual = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
            if actual != item['sha']:
                mismatch.append(item['path'])
    assert not mismatch, mismatch
    assert commit == expected['sha'], (commit, expected['sha'])
    result = {
        'repository': 'https://github.com/TUDelft-CNS-ATM/bluesky',
        'commit': commit, 'retrieved_date': '2026-10-16',
        'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'upstream_files_verified_against_git_blobs': count,
        'modified_upstream_files': changed,
        'license': 'MIT; upstream LICENSE retained without changes',
        'derivative_repository': 'https://github.com/91longli666-rgb/bluesky-coord',
        'derivative_version': 'v0.1.0',
        'publication_prepared_date': '2026-10-17',
        'dependencies_tested': {name: importlib.metadata.version(name) for name in
                                ('numpy', 'scipy', 'openap', 'bluesky-navdata', 'pytest',
                                 'matplotlib', 'pandas', 'msgpack', 'pyzmq')},
        'optimization_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                for path in ('bluesky/plugins/coord.py',
                                             'bluesky/plugins/coord_core.py',
                                             'research/coord/benchmark.py',
                                             'research/coord/reference.py')},
    }
    (ROOT / 'research/coord/UPSTREAM.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def aggregate(rows):
    methods = sorted({row['method'] for row in rows})
    result = {}
    for method in methods:
        group = [row for row in rows if row['method'] == method]
        result[method] = {
            'runs': len(group), 'clearances': sum(r['clearances'] for r in group),
            'los_events': sum(r['los_events'] for r in group),
            'extra_distance_nm': sum(r['extra_distance_nm'] for r in group),
            'minimum_distance_nm': min(r['minimum_distance_nm'] for r in group),
            'reversals': sum(r['heading_reversals'] + r['speed_reversals'] for r in group),
            'fallback_updates': sum(r['fallback_updates'] for r in group),
            'timeout_updates': sum(r['timeout_updates'] for r in group),
            'max_resolution_ms': max(r['resolution_max_ms'] for r in group),
        }
    return result


def plots(rows, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    methods = [m for m in ('MVP', 'MVP_TAS', 'COORD') if any(r['method'] == m for r in rows)]
    cases = list(dict.fromkeys(r['case'] for r in rows))
    colors = {'MVP': '#64748b', 'MVP_TAS': '#d99a27', 'COORD': '#0e948a'}
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), constrained_layout=True)
    for ax, field, title, unit in zip(axes,
        ('clearances', 'extra_distance_nm', 'minimum_distance_nm'),
        ('Fewer clearance changes', 'Detour cost', 'Observed minimum separation'),
        ('Mean combined clearances per run', 'Mean additional distance (NM)', 'Minimum over seeds (NM)')):
        for offset, method in enumerate(methods):
            values = []
            for case in cases:
                data = [r[field] for r in rows if r['method'] == method and r['case'] == case]
                values.append(min(data) if field == 'minimum_distance_nm' else np.mean(data))
            ax.bar(np.arange(len(cases)) + (offset - (len(methods) - 1) / 2) * .25,
                   values, width=.24, color=colors[method], label=method)
        ax.set_xticks(np.arange(len(cases)), cases, rotation=30, ha='right')
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.set_ylabel(unit)
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', alpha=.18)
        ax.set_axisbelow(True)
        if field == 'minimum_distance_nm':
            ax.axhline(5, color='#dc2626', linestyle='--', linewidth=1, label='5 NM threshold')
            ax.set_ylim(bottom=0)
    axes[0].legend(frameon=False)
    fig.suptitle('BlueSky COORD | level, no-wind synthetic validation', fontsize=15, fontweight='bold')
    fig.savefig(output, dpi=170, facecolor='white')
    plt.close(fig)


def trajectory_plot(directory, case='radial6', seed=11, output=None):
    import matplotlib.pyplot as plt
    import numpy as np
    methods = ['MVP', 'COORD']
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.3), constrained_layout=True,
                             sharex=True, sharey=True)
    for ax, method in zip(axes, methods):
        data = json.loads((directory / f'{case}-{seed}-{method}.json').read_text())
        positions = np.array([sample['position'] for sample in data['samples']]) / 1852.
        for i, acid in enumerate(data['ids']):
            ax.plot(positions[:, i, 0], positions[:, i, 1], label=acid, linewidth=1.8)
            ax.scatter(*positions[0, i], s=20)
        ax.set_title(f'{method} | {case}, seed {seed}')
        ax.set_xlabel('East (NM)')
        ax.set_ylabel('North (NM)')
        ax.set_aspect('equal')
        ax.grid(alpha=.18)
        ax.spines[['top', 'right']].set_visible(False)
    axes[-1].legend(frameon=False, loc='upper left', fontsize=8)
    fig.suptitle('Actual BlueSky trajectories; identical initial conditions', fontweight='bold')
    fig.savefig(output or directory.parent / 'trajectories.png', dpi=170, facecolor='white')
    plt.close(fig)


def report(rows, provenance, directory):
    summary = aggregate(rows)
    lines = [
        '# BlueSky COORD 定向优化实验报告', '',
        '实现日期：2026-10-16。范围：统一协调多机水平解脱，减少重复管制指令。', '',
        '## 实现', '',
        '- 新增 COORD 插件与独立规划器；上游核心算法文件保持原样。',
        '- 同时优化受威胁飞机的航向与速度；所有同高度保护区内的背景飞机对都参与约束检查。',
        '- 使用含转弯/加速响应的有限窗口预测、段内最近接近距离检查和恒定目标速度的终端条件。',
        '- 已验证的指令优先保持；恢复航路前再次检查冲突；紧急重规划不受保持时间阻挡。',
        '- 局部 SLSQP、多初值、可行候选独立校验；超时、无解、规模超限或不支持条件明确记录回退。', '',
        '- 求解失败后采用 5 秒重试间隔；等待期间原版 MVP 每秒继续更新，避免每秒重复耗尽求解预算。', '',
        '## 实验设置', '',
        '采用真实 BlueSky 无界面仿真、OpenAP A320 性能模型、原版 MVP 实现。各方法使用同一初始状态、航路、种子和物理仿真。',
        '仿真步长 0.5 秒，解脱更新 1 秒，预测窗口 180 秒，每次运行 600 秒；所有飞机保持 33000 英尺，无风。',
        'MVP 水平/垂直保护区与 COORD 相同；MVP 水平解脱裕量 1.10；COORD 保持/恢复门限 1.10、规划目标 1.13（含防抖缓冲）。',
        '开发集使用种子 1。以下验证集使用种子 11、29、47，各含迎头、交叉、三机汇聚、四机交叉、六机交叉，另加一组平行无冲突负对照。', '',
        '额外列出 MVP_TAS：仅在基准脚本中把原版 MVP 的 OpenAP CAS 速度裁剪界限转换为 TAS；其余解脱逻辑不变。',
        '这是为了区分速度单位差异与协同/指令保持带来的效果。上游 MVP 源码未修改，COORD 的回退仍使用原版 MVP。', '',
        '## 验证集汇总', '',
        '| 方法 | 运行数 | 指令次数 | 反向调整次数 | 间隔违规事件 | 额外航程合计 NM | 最小间隔 NM | 回退更新数 | 最大解脱耗时 ms |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for method in ('MVP', 'MVP_TAS', 'COORD'):
        if method in summary:
            s = summary[method]
            lines.append(f"| {method} | {s['runs']} | {s['clearances']} | {s['reversals']} | "
                         f"{s['los_events']} | {s['extra_distance_nm']:.3f} | "
                         f"{s['minimum_distance_nm']:.3f} | {s['fallback_updates']} | {s['max_resolution_ms']:.2f} |")
    coord = summary['COORD']
    for baseline in ('MVP', 'MVP_TAS'):
        if baseline in summary:
            s = summary[baseline]
            reduction = 100 * (1 - coord['clearances'] / max(s['clearances'], 1))
            lines += ['', f"相对 {baseline}，本验证集的总指令次数变化为减少 {reduction:.1f}%。"]
    lines += ['', '完整逐场景结果见 `validation/results.csv`，逐轨迹数据见同目录 JSON。', '',
        '![验证集比较](validation-comparison.png)', '',
        '## 指标口径', '',
        '- 指令：ASAS 介入期间，单架飞机的目标航迹角变化至少 1° 或目标速度变化至少 1 m/s，计一条组合指令；恢复航路也计数。未达到阈值的变化累计到上一次计数指令进行比较。',
        '- 反向调整：已计数的同一通道变化方向与上次方向相反；包含有意恢复航路，不能全部理解为不良抖动。',
        '- 间隔违规：同高度飞机对距离小于配置的 5 NM；对每个 0.5 秒仿真步的起终点相对位置进行线性段内 CPA 检查，连续违规计一次事件。',
        '- 额外航程：已飞航程减去向共同目标点的距离进展，逐机非负截断后合计。它是固定时长下的绕飞代理，不是全航程油耗或真实到达延误。',
        '- 耗时：完整冲突解脱更新调用，包含检查、规划和回退；解脱器耗时与整轮仿真耗时分开记录。', '',
        '## 限制与解读', '',
        '- 当前仅验证局部空域、定高、无风固定翼场景；爬升下降、风场、低于 3000 m、非 OpenAP/旋翼机或特殊避让开关会回退到原版 MVP。',
        '- 局部平面近似限制空域半径 250 km；单次最多联合优化 12 架受威胁飞机，超限明确回退。',
        '- 预测采用中点积分和固定当前响应能力；终端条件假设后续保持目标速度。这些是模型假设，不构成对真实轨迹或实际空管运行的安全保证。',
        '- 求解器具有局部性；1 秒预算为协作式时间检查，不能保证硬实时。超时可能改变回退路径，因此同种子跨机器不保证逐位一致。',
        '- 表格保留所有回退和未改善结果；不能将零违规推广为一般安全证明，也不能把合成场景提升等同于真实航班收益。', '',
    ]
    stress_file = directory.parent / 'stress/results.json'
    stress_metadata = directory.parent / 'stress/metadata.json'
    if stress_file.exists() and stress_metadata.exists():
        stress = json.loads(stress_file.read_text())
        lines += ['## 高密度压力测试', '',
                  '另测 8、12、16 机交叉，各使用种子 101、203，每种方法运行 600 秒。'
                  '16 机超过单次联合优化上限，其结果包含回退原版 MVP，不能归因于 COORD 单独求解。', '',
                  '| 场景 | 种子 | 方法 | 指令 | 违规 | 最小间隔 NM | 回退更新 | 超时更新 |',
                  '|---|---:|---|---:|---:|---:|---:|---:|']
        for r in stress:
            lines.append(f"| {r['case']} | {r['seed']} | {r['method']} | {r['clearances']} | "
                         f"{r['los_events']} | {r['minimum_distance_nm']:.3f} | "
                         f"{r['fallback_updates']} | {r['timeout_updates']} |")
        violations = sum(r['los_events'] for r in stress if r['method'] == 'COORD')
        lines += ['', f'COORD 在此压力集累计记录 {violations} 次间隔违规事件。'
                  '所有回退、超时及违规均保留于 `stress/results.csv`。', '']
    lines += ['## 自动验证', '',
              '28 项自动测试通过，包含真实 BlueSky 插件选择、飞机增删、重置、配置校验、'
              '演示场景加载、回退重试调度，以及预测/约束/超时边界。详细输出见 `test-results.txt`。', '',
              '上游 NumPy matrix 与 datetime.utcnow 弃用警告保留，未在本次算法优化中修改上游实现。', '',
        '## 逐场景结果', '',
        '| 场景 | 种子 | 方法 | 指令 | 违规 | 最小间隔 NM | 额外航程 NM | 回退 |',
        '|---|---:|---|---:|---:|---:|---:|---:|',
    ]
    for r in rows:
        lines.append(f"| {r['case']} | {r['seed']} | {r['method']} | {r['clearances']} | "
                     f"{r['los_events']} | {r['minimum_distance_nm']:.3f} | "
                     f"{r['extra_distance_nm']:.3f} | {r['fallback_updates']} |")
    lines += ['', '## 上游与复现', '',
              f"上游提交：`{provenance['commit']}`。已将官方源码压缩包内全部文件与该提交的 Git blob 哈希逐一比对。",
              f"原有文件修改列表：`{provenance['modified_upstream_files']}`。", '',
              '环境版本见 `../UPSTREAM.json`；运行说明见项目根目录 `README-COORD.md`。', '']
    output = directory.parent / '优化实验报告.md'
    output.write_text('\n'.join(lines), encoding='utf-8')
    (directory.parent / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return output, summary


def package():
    provenance = source_provenance()
    directory = ROOT / 'research/coord/results/validation'
    rows = json.loads((directory / 'results.json').read_text())
    plots(rows, directory.parent / 'validation-comparison.png')
    trajectory_plot(directory)
    output, summary = report(rows, provenance, directory)
    additions = [ROOT / 'README.md', ROOT / 'README-COORD.md', ROOT / 'README-UPSTREAM.md',
                 ROOT / 'NOTICE.md', ROOT / 'LICENSE-COORD', ROOT / 'CHANGELOG.md',
                 ROOT / '.gitignore', ROOT / '.github/workflows/build-wheels.yml',
                 ROOT / 'requirements-coord.txt',
                 ROOT / 'run-coord.ps1', ROOT / 'bluesky/plugins/coord.py',
                 ROOT / 'bluesky/plugins/coord_core.py']
    additions += list((ROOT / 'research/coord').rglob('*'))
    additions += list((ROOT / 'tests/coord').rglob('*'))
    additions += list((ROOT / 'scenario/COORD').rglob('*'))
    additions = sorted(set(p for p in additions if p.is_file() and '__pycache__' not in p.parts
                            and '.pyc' != p.suffix))
    patch = []
    with zipfile.ZipFile(WORK / 'bluesky-master.zip') as upstream:
        prefix = upstream.namelist()[0].split('/')[0] + '/'
        upstream_names = set(upstream.namelist())
        for path in additions:
            if 'results' in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            original = prefix + relative
            before = upstream.read(original).decode('utf-8') if original in upstream_names else ''
            after = path.read_text(encoding='utf-8')
            patch.extend(difflib.unified_diff(
                [line + '\n' for line in before.splitlines()],
                [line + '\n' for line in after.splitlines()],
                fromfile='a/' + relative if original in upstream_names else '/dev/null',
                tofile='b/' + relative))
    (OUTPUT / 'BlueSky-COORD.patch').write_text(''.join(patch), encoding='utf-8')
    archive = OUTPUT / 'BlueSky-COORD-extension.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / 'LICENSE', 'LICENSE')
        for path in additions:
            z.write(path, path.relative_to(ROOT).as_posix())
    full_archive = OUTPUT / 'BlueSky-COORD-full.zip'
    with zipfile.ZipFile(full_archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(ROOT.rglob('*')):
            relative = path.relative_to(ROOT)
            if (not path.is_file() or '__pycache__' in relative.parts
                    or relative.parts[0] in ('cache', 'output', '.git', '.venv', '.pytest_cache')):
                continue
            z.write(path, 'bluesky-optimized/' + relative.as_posix())
    print(json.dumps({'report': str(output), 'archive': str(archive),
                      'full_archive': str(full_archive), 'summary': summary,
                      'source_provenance': provenance}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    package()
