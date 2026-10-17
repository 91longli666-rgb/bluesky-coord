# BlueSky：多机协同解脱与管制指令稳定性优化

本版本增加 **COORD**，在 BlueSky 中统一优化多架飞机的目标航向、速度，并保持已经验证的指令，减少反复调整。

已有的 MVP、冲突检测、飞机动力学等上游核心实现均保留。新增功能通过插件接入，可随时切回原方法。上游根目录 `LICENSE` 为 MIT，原版权与许可声明保持原样；本次新增代码也按 MIT 许可证提供。

## 先看结果

- [定向优化实验报告](research/coord/results/优化实验报告.md)
- [验证集对照图](research/coord/results/validation-comparison.png)
- [实际 BlueSky 航迹图](research/coord/results/trajectories.png)
- [逐场景 CSV](research/coord/results/validation/results.csv)
- [上游版本和测试依赖](research/coord/UPSTREAM.json)

验证集有 16 个场景/种子组合，每种算法运行 600 秒。保留原版 MVP，并额外加入仅修正 CAS/TAS 裁剪界限的 MVP_TAS 对照。报告同时列出回退、超时、航程和间隔指标。

## 本机直接运行

在项目目录打开 PowerShell：

```powershell
.\run-coord.ps1 -Action demo
.\run-coord.ps1 -Action test
.\run-coord.ps1 -Action validation
```

脚本优先使用项目 `.venv`，其次使用本次任务在工作区准备的独立 Python 环境。缺少环境时，它会调用本机 `python` 创建项目内 `.venv` 并安装依赖。也可以通过 `-Python 'C:\path\to\python.exe'` 显式指定**已安装依赖**的解释器。

`demo` 运行开发场景并生成 `research/coord/results/demo-comparison.png`；`test` 运行新增自动测试；`validation` 重跑验证集；`stress` 测试 8/12/16 机高密度交叉。

## 其他电脑安装

建议 Python 3.12，测试版本为 3.12.14。在完整源码目录中：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-coord.txt
.\run-coord.ps1 -Action test
.\run-coord.ps1 -Action demo
```

如果拿到的是 `BlueSky-COORD-extension.zip`，它是**增量包**：先取得 `research/coord/UPSTREAM.json` 所记提交的完整官方源码，再将增量包按目录结构覆盖进去。本仓库或 `BlueSky-COORD-full.zip` 解压后的项目目录包含完整源码。

## 在 BlueSky 图形界面加载

界面运行需要另外安装上游 Qt 依赖；本次算法与性能验证使用无界面模式，未验证 Qt 窗口操作。

```powershell
.\.venv\Scripts\python.exe -m pip install PyQt6 PyQt6-WebEngine pyopengl bluesky-guidata
.\.venv\Scripts\python.exe BlueSky.py --workdir . --configfile research/coord/demo.cfg --scenfile scenario/COORD/radial4.scn
```

也可以在已有界面的命令栏输入：

```text
PLUGINS LOAD COORD
CDMETHOD STATEBASED
RESO COORD
COORDSTATUS
```

切换回原方法：`RESO MVP`。比较时使用水平航向＋速度模式：`RMETHH BOTH`。

修改预测窗口、最短指令保持时间、求解预算：

```text
COORDCFG 180,20,1
```

单位都是秒。非法配置会被拒绝，原配置不变。更详细的参数在 `bluesky/plugins/coord_core.py` 的 `Config` 中，包括航向偏移上限、速度变化范围和最大联合优化飞机数。

## 优化内容

1. **多机联合决策**：选取受威胁飞机的航向与速度作为联合变量，同时检查所有相关背景飞机对，降低引入二次冲突的风险。
2. **包含执行过程的预测**：考虑当前转弯率、加减速能力；检查每个预测线段内部的最近距离，避免只看离散采样点。
3. **预测窗口末端约束**：在恒定目标速度假设下检查窗口后的最近接近，避免把冲突不断推到窗口外。
4. **稳定指令与受控恢复**：可行指令优先保持，达到保持时间后再尝试恢复正常航路；恢复也要重新验证。发现风险时，保持时间不会阻止重新规划。
5. **明确处理失败**：容量超限、局部求解无解、超时或不支持条件时回退原版 MVP，并通过 `COORDSTATUS` 和结果 JSON 留下记录。求解失败后默认间隔 5 秒再试，期间 MVP 每秒继续更新，避免反复耗尽预算。回退不等于已经验证安全。

这是一个限制控制策略为“当前周期内确定、后续保持目标航向/速度”的滚动时域优化器，使用 SLSQP 局部求解；它并非包含任意多阶段控制序列的完整 MPC 求解器。

## 当前适用范围

- OpenAP 固定翼，保持高度、无风，飞行高度不低于 3000 米。
- 局部空域半径不超过 250 km；最多联合调整 12 架受威胁飞机。
- 默认预测 180 秒、预测步长 5 秒；10% 保持/恢复裕量，规划额外增加 3% 防抖缓冲。
- 航向相对规划时的正常航路指令最多偏移 35°，速度默认在参考速度 ±15% 与飞机性能包线的交集中优化。
- 1 秒求解预算是协作式检查，无法保证硬实时；遇到超时，同种子跨机器也可能走不同回退路径。

本版本的证据来自合成场景与 BlueSky 的仿真模型，尚未覆盖风场、爬升下降、监视误差、通信/执行延迟、尾流间隔、机场进近排序或真实运行数据。

## 代码位置

| 文件 | 用途 |
|---|---|
| `bluesky/plugins/coord_core.py` | 独立预测、联合优化、候选校验与指令保持 |
| `bluesky/plugins/coord.py` | BlueSky 插件适配、速度单位转换、回退与状态命令 |
| `tests/coord/` | 数值约束与真实 BlueSky 生命周期测试 |
| `research/coord/benchmark.py` | 同初始状态的真实仿真对照实验 |
| `research/coord/reference.py` | 仅用于实验的速度包线单位修正对照 |
| `scenario/COORD/` | 可在上游界面加载的演示场景 |

上游主 README 中的论文信息仍应在学术使用时引用；本次优化不是上游作者发布的官方版本。
