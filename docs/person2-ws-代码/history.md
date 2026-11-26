# Person2 已完成工作记录

## 2026-11-26｜输入审计与样例实体链首轮实现

- 按协作规范复核项目状态与交接；确认原始题面和 12 份附件位于 `question/B题：炼钢厂钢包周转与排产优化问题/`，而 `code/`、`data/` 初始仅含 `.gitkeep`。未移动或改写原始数据。
- 补齐当前项目 venv 的 `openpyxl 3.1.5`、`xlrd 2.0.2`；使用 Python 3.13.2 执行。因 `pip` 启动脚本残留旧绝对路径，本次用 `python -m pip` 管理依赖。
- 新增 `code/src/input_audit.py`：13 个输入文件（1 DOCX、11 XLSX、1 XLS）全部通过格式/ZIP 结构读取，记录 SHA-256、工作表维度和 684 个字段；统计 12 张工作表共 6,758 个数据行。输出 `data/audit/data_manifest.csv`、`sheet_inventory.csv`、`field_dictionary.csv`。
- 审计识别 6 个 XLSX 的 XML 声明维度为 A1、实际却含完整数据；脚本采用 openpyxl 正常模式读取并标注，避免 read-only 误读。识别 `工序时间信息.xlsx` 重复表头；X/Y 同名列含不同格式数据（时间戳与时长），BK/BL 同名列当前为空，待团队确认语义。
- 新增 `code/src/plan_audit.py` 与 `data/audit/plan_audit.json`、`plan_route_counts.csv`、`plan_duplicate_heat_rows.csv`：复核计划表 1321 行、55 条 `配包flag=-2` 占位、1266 条有效行与 1266 个唯一有效熔炼号；有效路线计数 L0 668/R0 420/R0L0 108/L0R0 70；有效行中 `更换中包==1` 与 `浇次号==1` 逐行一致（205 行、0 例不匹配）。既有 216/1105 是包含占位行的原始计数，不应直接作为有效炉次计数。
- 新增 `code/src/trace_sample_heat.py`，生成 `data/audit/sample_heat_26BD6651_trace.csv` 和 join audit JSON：43 条按时间排序的记录，保留来源行与哈希；B炉→LF Tier-2 样例校验通过（钢包号一致、重量差 0 t、时间差约 21.78 min）；LF/RH/3#连铸临时罐号一致。
- 新增 `code/tests/test_input_audit.py`；`python -m pytest code/tests -q`：5 passed。3 条警告均为输入文件样式缺省或 pandas 对 xlrd 引擎的弃用提示，测试无失败。
- 本阶段**未**实施全量实体修复、规范表、排产求解器或六类调度边界测试；继续任务见 plan/handoff。

## 2026-11-26｜独立源表异常复核与求解器环境冒烟

- 新增 `code/src/source_quality_audit.py`，扫描 12 份工作簿、12 张工作表、6,758 行，输出 `data/audit/source_quality_summary.csv`、`source_quality_findings.csv`、`source_quality_audit.json`。共列出 2,529 条待复核证据；原始文件哈希前后相同，未自动填补或修正任何源单元格。
- 复现重点候选计数：3#LF 待机位吊起时间空 323 行、吊包天车空 323 行；B 炉钢水罐临时号空 435 行；1#/2#RH 脱气起止相等共 533 行；工序表 4 组预设时间字段的负差候选为 113/115/143/238 行。所有候选均带源行号和 hash；数目不代表已确认错误，重复热号还可能来自占位/多阶段记录。
- 新增 `code/src/solver_preflight.py`，CP-SAT 2 资源/4 任务合成小模型得到 OPTIMAL、makespan=6、bound=6；Pyomo+HiGHS 合成 LP 得到 74/3。结果记录于 `data/audit/solver_preflight.json`，仅证明安装与调用可用，不是项目排产结果。
- 新增 `code/tests/test_independent_audits.py`；全套 `python -m pytest code/tests -q` 为 7 passed。警告仅为来源 XLSX 样式缺省和 pandas 对 xlrd 引擎的弃用提示。
- 仍未做实体自动修复、规范表、项目调度模型或六项调度边界验证；异常解释和字段口径交接见 handoff。

## 2026-11-26｜完整行统计与单浇次场景1历史回放

- 新增 `code/src/clean_row_statistics.py`，按显式、可追溯条件生成 `data/audit/clean_complete_heats.csv`、`basic_duration_stats.csv`、`duration_outlier_candidates.csv`、`cast_group_candidates.csv` 和 `section2_basic_stats.json`。过滤规则：排除 `配包flag=-2`；要求计划热号唯一、计划核心字段齐全且时间顺序有效；实际统计按单一唯一工序行和指标所需时间对取各自 N。原值均保留。
- 统计 cohort：计划完整有效行 1266；工序表唯一热号行 1206，其中 1136 行匹配计划；核心实际时间完整、路线处理时间有效的 841 行。计划转炉时长 1266 条、固定 30 min；计划浇铸时长中位 40 min（N=1266）。实际出钢/LF/RH/CCM 时长中位数分别 5.63（N=1065）、36.92（N=751）、39.00（N=516）、40.65（N=1117）min；详细按路线统计和分母见 CSV。Tukey 1.5×IQR 只生成异常候选，不从统计中自动剔除；例如 LF 时间最大值 215.87 min 留待复核。**统计阶段仅按唯一熔炼号连接，没有将跨表 Tier-1/Tier-2 临时罐号校验作为完整行条件**，不能据此声称所有实体匹配已验证。
- 选择 `连连浇号=6C0754`，计划序列 1–6、6 个 R0 炉次、计划 C 线映射实际 3# 连铸。新增 `code/src/scenario1_historical_replay.py`，生成 `data/runs/scene1_replay_6C0754/` 下 moves/schedule/operations/side_events/metrics/violations/run_manifest。共18个主移动段及6个附属事件；观察行车固定时刻的 CP-SAT 同车不重叠模型 OPTIMAL、0 个重叠。
- 回放中发现并保留：26BD6455 的 RH/CCM 临时罐号不一致；4 炉 RH “接受位”时间标记晚于处理开始；8 个来源标记相对下游截止需复核。Tier-2 90min/3t 候选规则 6/6 通过，但仍是方案暂定阈值。热龄、连铸间隙和计划偏差只报告观察值，不按未确认阈值判定合规。
- 物理共享轨道安全与真实连续轨迹**没有**认证或生成；当前回放是历史实际基线，不是 FCFS/EDF 优化结果。缺少经建模手确认的初始位置、轨道安全间距、速度/加速度等参数，已在 metrics/violations 明示 not_assessed。
- 新增 `code/tests/test_stats_and_scenario1.py`；全套 `python -m pytest code/tests -q`：9 passed。警告为 WPS 工作簿样式缺省及 pandas/xlrd 引擎弃用提示。
