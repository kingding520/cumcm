# Person2 交接

## 2026-11-26｜输入审计阶段交接

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| Person1（建模）、Person3（方案） | 已回复C1、待实现 | `工序时间信息.xlsx` 有重复列名：X/Y（第24/25列）均有644个非空值，但X为时间戳、Y为时长文本；BK/BL（第63/64列）同名且目前均为空。请确认 X/Y 的规范字段名和消费方式；审计脚本保留原列，不静默合并。 | 对字段语义/命名给出书面决定，供全量 ETL 使用 |
| Person1（建模） | 已回复C2、待实现 | 确认 Tier-2 的时间窗 90 min、重量差阈值 3 t 可否作为正式匹配契约；本轮样例 `26BD6651` 按方案阈值验证通过，但尚未全量执行。 | 更新参数来源/实体关联契约 |
| Person3（论文） | 已可使用 | 首轮输入清单、字段字典、出钢计划复核和样例链结果已落地；论文不得把 216 次更换标志直接写成有效炉次数。有效行是 205；216 是含占位行的原始行计数。 | `data/audit/plan_audit.json` 等脚本产物及本轮测试 |

### 已交付代码和产物

- `code/src/input_audit.py` → `data/audit/data_manifest.csv`、`sheet_inventory.csv`、`field_dictionary.csv`。
- `code/src/plan_audit.py` → `plan_audit.json`、`plan_route_counts.csv`、`plan_duplicate_heat_rows.csv`。
- `code/src/trace_sample_heat.py` → `sample_heat_26BD6651_trace.csv`、`sample_heat_26BD6651_join_audit.json`。
- `code/tests/`：7 项测试全部通过。
- 6 个 XLSX 有不准确的 XML 声明维度 A1，读取时不要使用会依赖该维度的只读模式；使用当前脚本的正常模式。

## 2026-11-26｜全量质量扫描与求解器冒烟交接

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| Person1（建模） | 已回复C3、待复算 | `source_quality_findings.csv` 中四组时间字段负差候选：出钢结束→重罐吊起 113 行、LF 出处理位→LF 吊起 115 行、RH 处理结束→RH 吊起 143 行、停浇→空包吊起 238 行。请结合实体号/设备时钟判断哪些是 A 炉 +1 错位、时钟偏差或其他情况；本次未修复、未定性。 | 每行含来源文件、工作表、Excel 行号、炉次号、时间原值与 SHA-256 |
| Person1（建模） | 已回复C3、待复算 | 3#LF 待机位吊起时间空 323 行、吊包天车空 323 行；B 炉钢水罐临时号空 435 行（A/C 分别 3/2）。请确认匹配修复契约与低置信度回退的记录方式。 | `source_quality_findings.csv` 中 `3lf_missing_candidate_field`、`bof_missing_steel_ladle_temp_id` 记录 |
| Person3（方案/论文） | 仅供核验 | 两 RH 表有 533 行脱气开始/结束时间相等；不要把它们当作真实零秒处理时长，需沿用已定的处理周期口径；299 行属于重复熔炼号候选行，包含占位及多阶段场景，不等于 299 个已确认坏行。 | 分规则源行清单；未做自动删除或改写 |

- `code/src/solver_preflight.py` 已跑通 CP-SAT 2 资源/4 任务合成模型（OPTIMAL）及 Pyomo+HiGHS 合成 LP（74/3）；**仅为依赖/API 冒烟，非项目调度结果**。记录在 `data/audit/solver_preflight.json`。
- 全源扫描输出 `data/audit/source_quality_summary.csv`、`source_quality_findings.csv`、`source_quality_audit.json`；12 份工作簿、6758 行，2529 条 review candidates，源文件哈希前后不变。

## 2026-11-26｜基础统计与单浇次回放交接

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| Person1（建模） | 已回复C3、待复算 | 完整历史统计已就绪；检查 `duration_outlier_candidates.csv`，尤其 LF 非负完整时间对最大 215.87 min（N=751、median=36.92、P90=47.68），决定是否按数据来源/工艺规则细分，而不是直接截尾。对 `6C0754` 中 26BD6455 RH/CCM 临时罐号不一致及4个 RH 接受标记晚于处理开始的时序冲突给出实体/时钟解释。 | `section2_basic_stats.json`、`clean_complete_heats.csv`、`scenario1_replay_6C0754/violations.csv` |
| Person1（建模） | 已回复C2、待实现 | 90 min / 3 t Tier-2 仍为暂定阈值；本组6/6通过。请确认后再将其写入正式匹配配置和场景1门禁。 | `scenario1_replay_6C0754/metrics.json` 和 `run_manifest.json` |
| Person3（方案/论文） | 可使用、待核对 | Section 2 初步统计已输出计划与实际、分路线、逐指标 N 和异常候选；引用数字前须标注各自分母，不把候选标记说成已修复数据。统计按唯一熔炼号连接，未通过全量 Tier-1/Tier-2 临时罐号校验，论文暂勿把它描述为完全实体核验后的最终统计。 | `data/audit/basic_duration_stats.csv` 与 `section2_basic_stats.json` |

- 当前样例选择：连连浇号 6C0754，26BD6452–26BD6457，R0 路线、C 线映射实际 3#CCM。CP-SAT 只验证固定实际区间下同车不重叠；共享轨道空间防碰撞、真实轨迹和优化派车均未完成。

## 2026-11-26｜M1-01 单浇次闭环交接（待 wj 拍板）

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| Person1（wj，建模） | **待拍板，阻塞全量 ETL** | 请确认 5 项参数/取舍：① 站坐标用相对像素是否可接受、κ(m/px) 取多少；② 三台 450t 行车的初始位置（源数据没有）；③ 轨道相邻安全间距 D_r；④ 加/减速度与档位速度；⑤ 采用「主动避让」还是「合法换车点」来恢复三机并行。当前全部为临时值并在配置中标注 `confirmed_by_modeler: false`。 | `code/configs/scene1_rail_params.yml` 逐项 provenance |
| Person1（建模） | 待复核（关键结论） | 闭环门禁结论：在不可跨越共享轨道且不建模避让/换车时，观测回放、FCFS、EDF-Nearest、CP-SAT 精确空驶建模**四种三机并行方案全部违规**（相邻行车最小间距 -1235~-1520 px），仅保守单机串行通过（+60 px ≥ 50 px）。请判断这与现场实际是否矛盾——若现场确实三机并行，则说明必须补避让/换车建模，而不是收紧参数。 | `collision_pairs_base.csv`、`collision_matrix.csv` |
| Person1（建模） | 待复核（标定反例） | 本浇次观测中位 CCM 空包→渡车 16.02 min，而继承重包 OD 回归预测 3.41 min（残差 +12.60 min）；BOF→RH、RH→CCM 残差仅 +0.89/+0.87 min。建议空包回程单列标定，或拆分「翻渣/铸余作业时长 + 纯行车时长」。 | `leg_calibration.csv` |
| Person3（论文） | 可使用、须标注条件性 | 可引用的量化对比（同一 18 任务集）：安全串行等待 109.48 min / 迟到 18.37 min；FCFS 21.92 / 12.09；CP-SAT 精确空驶 33.65 / 12.08。安全串行相对 FCFS 多 87.6 min 等待，可作为「避让/换车建模收益空间」的论据。**引用时必须声明参数未确认、且四方案被门禁拒绝**，不得写成已求得可行优化解。 | `optimization_comparison.csv`、`closed_loop_summary.json` |
| 全体 | 状态说明 | 全量 17.5 天 Tier-1/Tier-2 ETL 与典型日/峰值日选样**尚未开始**，按用户要求等 wj 确认后再做。 | 本项目尚无 `data/processed/` 规范表 |

- 轨迹产物：`trajectory_{observed_historical,fcfs,edf_nearest,cp_sat_exact_setup,conservative_serial}.csv`，共 133–144 段/方案，字段符合 PROJECT.md §7 契约（run_id/crane_id/seg_id/t_start_s/t_end_s/x_start/x_end/v_start/v_end/accel/gear/phase/load_t）。
- 独立校验器：`code/src/rail_model.py::verify_pair/verify_schedule`，对分段二次相对间距做端点 + 内部极值点精确判定（命题 2b），并用「端点均安全、内部 -15 px」的构造反例做了回归测试。
- 观测回放不是物理有效轨迹：空包回程含翻渣/铸余，10 段峰值达 20.68 px/s（2.3×临时上限），仅作对照，不得作为轨迹认证对象。

## 2026-11-26｜场景1 主动避让（ef_initial_v1）交接

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| Person1（wj） | **已落地、待复核一个结构结论** | 已按 `ef_initial_v1.json` 重写场景1：D=12 m、0.60/0.80/1.00 m/s、分级加速度、站区 0.4× 减速带、场景1零操作时间。主动避让生效后，`fcfs_nearest` 18/18 完成且精确门禁通过（最小间距 12.24 m），总等待 0.52 min；`pipeline_roles` 3.30 min；单机串行 7.50/8.32/9.83 min。**关闭避让同一指派只完成 1/18 即死锁**，可作为"必须主动避让"的直接证据。 | `data/runs/scene1_yield_6C0754/comparison_yield.csv`、`yield_summary.json` |
| Person1（wj） | **待拍板：是否需要交接位** | 本浇次实际只需 **2 台车**：空包回程必须由把包送到 CCM3(181.5) 的车完成；东侧 5#450 在 3#450 位于 181.5 时无法到达 164（物理顺序 + D=12 m）。若要让三机真正同时作业，需要契约定义**交接位/转接台**，否则论文应写成"两机安全并行 + 三机需交接位"的边界结论。 | `task_trace.csv` 中 3 条 CCM_EMPTY_RETURN 均由 RH_CCM 同一台车完成 |
| Person1（wj） | 待复核：口径不可比 | 上一轮 109.48 min 由观测时长模型（含装卸与翻渣）得到；本轮为零操作时间纯运动学。**两者不可直接相减**，本轮对比是 7.50 → 0.52 min。另外单浇次下 1 台车已能吸收全部作业，行车不是瓶颈资源，真正体现调度价值的是整日多浇次竞争。 | `yield_summary.json` 的 `notes` |
| Person3（论文） | 可使用（须标注条件） | 可引用：同一 18 任务、同一 D=12 m、同一运动学下，串行 7.50–9.83 min vs 安全并行 0.52–3.30 min；避让次数 2–29；几何盲 CP-SAT 指派（531 次避让、11/18 完成）作为负面对照。引用时须说明"单浇次只需 2 台车"与"109.48 不可比"。 | `comparison_yield.csv` |
| 全体 | 状态说明 | 第二步（全量 ETL → `data/processed/`、常规日/峰值日、150t 与平车选址灵敏度）**尚未开始**；第三步场景2/3 等 wj 06 契约落地后开展。 | 仓库仍无 `data/processed/` |

- 认证轨迹：`data/runs/scene1_yield_6C0754/trajectory.csv.gz`，1 s 分辨率、12.4 万段，gzip 后 **968 KB**（未压缩 15 MB，不入库）。gzip 固定 mtime 可复现；用 `scene1_yield.read_trajectory_gz` + `gate` 可离线复验，仓库内已有一条测试直接对产物复验（pass，12.244 m）。注意：把轨迹压缩成"等速段合并"会跌破 12 m（实测 -820 m），该压缩方式不可用于认证。
- 测试：新增 `code/tests/test_scene1_yield.py` 8 项（几何自洽、别名、运动学单调性、任务覆盖、串行/并行门禁、避让必要性、确定性）；全套 30 passed。

### 未完成与协作请求

- M1-01（完整浇次闭环）：`6C0754` 已完成一轮（trajectory + 独立防碰撞门禁 + 5 方案对比），但**仅在临时参数下成立**；参数确认后需重跑，并补主动避让/换车建模才能给出被接受的三机并行方案。
- M1-02 六类调度边界仍待实现；当前 22 个测试覆盖数据登记、计划统计、样例实体链、全源异常扫描、基础统计、单浇次历史回放、轨迹运动学、防碰撞门禁与闭环产物，但六类边界案例本身尚未逐一构造。
- 收到字段语义与匹配契约确认后，Person2 继续全量 Tier-1/Tier-2 实体解析；不确定记录会保留原值、规则与置信度，不静默删除。

## 2026-11-26｜建模手回复：C1—C3

三项确认的结论与执行规则如下。输入复核见[data/audit/model_confirmation_evidence_20261126.json](../../data/audit/model_confirmation_evidence_20261126.json)，参数见[model_contract_v1.json](../../code/configs/model_contract_v1.json)。

| 确认项 | 建模决定 | 伍帅执行动作 |
|---|---|---|
| C1 字段语义与命名 | X列定为`rh_rotation_arrival_ts`，Y列定为`lift_to_rh_rotation_duration_s_raw`；BK/BL分别保留`empty_de_to_ef_col63_raw`、`empty_de_to_ef_col64_raw`，空值类型为UNKNOWN_RESERVED | 按物理列号映射，Y的分钟/秒文本转秒，保存原值；用X减同一事件链的重罐吊起时刻另算规范时差，记录与Y的偏差 |
| C2 Tier-2匹配阈值 | 确认90min和3t作为contract-v1基准筛选值；接受匹配同时满足同炉次、同物理钢包、路线/设备一致、时间有序、质量平衡残差≤3t、候选唯一及每路线阶段的一对一关联 | 时间窗为转炉出钢结束至紧邻的首道精炼接受位，0—5400秒；重量先扣除已记录加料/回收变化。多候选进入冲突表。扫描60/90/120min与2/3/5t，报告匹配覆盖和歧义率 |
| C3 长LF、别名及时间冲突 | 采用下述分组规则，描述统计、工时训练和空间回放分别设置可用性 | 保留原始字段与各自N，生成异常处置台账；重算对应分组和验证状态 |

### C1补充：重名字段的数值关系

X、Y各644个非空值，其中X减重罐吊起时间与Y在1秒内一致566条，差异78条。Y作为原始派生时长留存，规范时差从核验后的时间戳计算；差异写入`duration_source_mismatch`。负规范时差按C3核验。BK/BL保留源列位置和空值，待新增观测后确定事件语义。

### C2补充：主键、状态与缺失回退

- 物理钢包号按数值编号规范化，例如08→8；源值另存。临时罐号作为阶段别名，身份关联同时检查钢包、炉次和时间。
- 接受状态用`MATCHED_UNIQUE`，别名冲突用`MATCHED_WITH_ALIAS_CONFLICT`，多候选用`AMBIGUOUS`，证据不足用`UNRESOLVED`；时间质量单独记录。
- 同一临时罐号在多个阶段出现时，核对有效时间和实体一致性。唯一字符串匹配属于候选检索条件。
- 3#LF吊起和车号分别恢复：跨源唯一确认才回填为VERIFIED_REPAIR；下一站到位用于形成吊离上界；历史车号缺失保留UNKNOWN。新调度的车号由优化器决定。
- A炉错位按连续异常段修复，保留逐行前后对应；模型中的可吊时刻仍需完成出钢、钢包车和工位释放检查。

### C3补充：三类异常的具体决定

1. **LF长时长**：3#LF源表中26CD5323为2026-10-08 21:07:31—10-09 00:43:23，215.87min；26CD5324为23:21:00—次日02:11:44，170.73min；26BD6396为167.07min。源表与综合表时间相同，按`LONG_PROCESS_REVIEW`分组。前两条在基准单加热装置下时间重叠，另标`PROCESS_RESOURCE_CONFLICT`。原始描述统计保留这些值；常规处理工时训练池使用身份/时间/资源一致的子集，另报该子集N；长时值进入扰动压力实验。完成工位/处理模式核验后再细分故障、返工、等待或记录错误。
2. **26BD6455别名**：B炉、1#RH、3#连铸的物理钢包均为35；RH临时号x179112209298720769，连铸临时号x179113552487720769。RH吊起02:52:13、连铸落座03:01:07，前后重量412.2t/411.1t，连接时差8min54s、重量差1.1t。将该链列为`MATCHED_WITH_ALIAS_CONFLICT`，保存两个阶段别名；进入单炉历史时序回放，Tier-1字符串一致性仍标冲突。全量一对一候选检查完成后进入正式规范任务集。
3. **6C0754的4个RH到位标记**：26BD6452、6453、6454、6456的“到RH接受位”分别晚于处理开始160、58、117、19秒。标记`ARRIVAL_PROCESS_CLOCK_OR_SEMANTIC_CONFLICT`。处理开始/结束可保留为本系统占用观测；跨系统到位标记暂以原值保留，规范到位时刻记录为“重罐吊起至RH处理开始”间的区间；有可信物理最短运输时间时收紧下界。连铸到位晚于开浇的记录沿用相同分层办法。当前4个偏差采用逐事件标记，时钟校正另用跨日期高可信锚点估计系统偏移。

### 接续接口

物理初值与参数使用[05表](../person1-wj-建模/05_E-F跨物理坐标与运动参数初值表.md)及[ef_initial_v1.json](../../code/configs/ef_initial_v1.json)。场景2、3按[06简明契约](../person1-wj-建模/06_场景2与场景3简明建模契约.md)实现。先交付字段映射、匹配与处置台账，再推进场景1空间基线、场景2运动学和场景3恢复状态机。
