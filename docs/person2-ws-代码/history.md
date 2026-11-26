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

## 2026-11-26｜M1-01 单浇次闭环：轨迹 + 防碰撞门禁 + 优化对比

- 新增 `code/src/rail_model.py`：分段常加速（梯形/巡航主导）轨迹构造 + **精确**连续时间相邻行车间距校验。相对间距在每段时间内为二次函数，故除端点外还检查内部极值点（对应方案命题 2b），不使用采样网格做安全判定。
- 新增 `code/src/scenario1_closed_loop.py`：在 `6C0754`（6 炉、18 任务）上生成 5 种候选方案（观测历史回放、FCFS、EDF-Nearest、CP-SAT 精确空驶建模、保守单机串行），逐一生成 `trajectory_*.csv` 并送入独立门禁，产出 `optimization_comparison.csv`、`collision_matrix.csv`、`collision_pairs_base.csv`、`empty_speed_sensitivity.csv`、`trajectory_diagnostics.csv`、`profile_issues.csv`、`leg_calibration.csv`、`closed_loop_summary.json`。
- 新增 `code/configs/scene1_rail_params.yml`：相对像素站坐标、行车物理顺序与能力、初始位置情景、运动学与安全间距情景，全部标注 `confirmed_by_modeler: false`，来源逐项注明（继承方案 §3.2 / 本浇次观测 / 情景假设）。
- **验收结论**：观测回放、FCFS、EDF-Nearest、CP-SAT 精确空驶建模四种三机并行方案全部被门禁判为违规（相邻行车最小间距 -1235 ~ -1520 px，即发生跨越），仅保守单机串行通过（+60 px ≥ 50 px）。原因是在不可跨越共享轨道上，本浇次任务从 RH(880) 到 CCM3(2115) 的跨越必须借助主动避让或合法换车点，而未建模。空驶速度系数在 1.0–1.5 全扫描下结论不变，说明该结论不依赖这一临时参数。
- **效率代价（同任务集、同标定）**：保守串行总任务等待 109.48 min、迟到 18.37 min；FCFS 等待 21.92 min、迟到 12.09 min；CP-SAT 精确空驶模型 33.65/12.08。即安全串行相对 FCFS 多 87.6 min 等待，这一差额正是主动避让/换车建模的潜在收益空间（尚待建模确认）。
- **标定对照**：观测中位 BOF→RH 4.675 min（继承回归 3.782，残差 +0.893）；RH→CCM 7.083（回归 6.213，+0.870）；CCM 空包→渡车 **16.017**（回归 3.414，**+12.603**）。最后一项表明重包 OD 回归不能套用于空包回程，空包腿须单列或拆分翻渣/铸余作业。
- 轨迹自检：5 份 trajectory 共 133–144 段，phase ∈ {loaded, empty, idle}，gear ∈ 1–5，x 全程 375–2570 且在各自轨道界内，0 越界；三机并行方案峰值速度 ≤ 8.64 px/s（上限 9.0）；观测回放因空包回程含翻渣/铸余，有 10 段峰值达 20.68 px/s（2.3×上限），故观测回放不作为物理有效轨迹，仅作对照。
- 新增 `code/tests/test_scene1_closed_loop.py`（13 项）：覆盖剖面的时间/位移精确性、运动学一致性、档位范围、**端点安全但内部极值违规**的命题 2b 反例、安全通过/跨越判定、闭环产物完整性、逐炉工序先后、JSON 可序列化。全套 `python -m pytest code/tests -q`：**22 passed**。
- 未做：全量 17.5 天 ETL（按要求等待 wj 确认）、三维/横向几何、主动避让与合法换车建模、150t 辅车策略、随机与故障场景。

## 2026-11-26｜场景1切换到 ef_initial_v1 + 主动避让（Yield）

- 新增 `code/src/ef_model.py`：从 `code/configs/ef_initial_v1.json` 读取米制几何与运动学（κ=0.10 m/px、D=12 m、重包/空包/空钩 0.60/0.80/1.00 m/s、分级加速度、站区减速带 0.4×、场景1零操作时间），并对 D 与「净距+车长」做一致性校验，缺键即报错，不静默回退。
- 新增 `code/src/scene1_yield.py`：1 s 步进的主动避让仿真器。每步按真实速度/加速度限制推进，并对相邻行车施加 D 间距不变量（先限幅再顺序投影，防倒序）；当某车需越过邻车时，按「截止期优先」仲裁，让非吊运中的邻车整体东移/西移让出走廊，事后回归。轨迹由仿真器逐步位置日志重建（每步用端点精确加速度），再经 `rail_model.verify_schedule` 连续时间精确复核。
- **首个通过门禁的三机可用方案**（6C0754，18 任务，D=12 m）：`fcfs_nearest` 18/18 完成、门禁 pass、最小间距 **12.24 m**、总等待 **0.52 min**（2 台车实际使用）；`pipeline_roles` 18/18、pass、12.35 m、3.30 min、避让 11 次；三个单机串行基线分别为 7.50/8.32/9.83 min，全部 pass。
- **避让必要性得到直接证据**：同一角色分配在关闭避让后只完成 1/18 即死锁（`pipeline_no_yield`）。几何盲的 CP-SAT 指派（不建模轨道顺序）产生 531 次避让且仅完成 11/18，属负结果。
- 结构性结论：本浇次**只需要 2 台车**。空包回程必须由把钢包送到 CCM3(181.5) 的那台车完成——东侧 5#450 受物理顺序约束无法在 3#450 位于 181.5 时到达 164，除非引入契约未定义的换车/交接点。三机真正并行需先定义交接位。
- 口径提醒（已写入 summary）：`ef_initial_v1` 下场景1为零操作时间的纯运动学，单台车已能吸收单浇次作业，**行车不是瓶颈资源**；上一轮 109.48 min 来自观测时长模型，两者不可直接相减。本模型的对比结论是 7.50 → 0.52 min。
- 产物：`data/runs/scene1_yield_6C0754/` 下 `comparison_yield.csv`、`task_trace.csv`、`trajectory.csv.gz`（1 s 分辨率认证轨迹，gzip 后 968 KB）、`yield_summary.json`。新增 `write_csv_gz`/`read_trajectory_gz`（固定 mtime、可离线复核）；新增 `code/tests/test_scene1_yield.py`（10 项，含压缩包往返与对已提交产物复跑门禁）；全套 **32 passed**。
- 未做：第二步全量 17.5 天 ETL 与典型日/峰值日、150t 辅车与平车选址灵敏度；第三步场景2/3。
