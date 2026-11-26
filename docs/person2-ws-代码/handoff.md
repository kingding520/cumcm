# Person2 交接

## 2026-11-26｜输入审计阶段交接

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| Person1（建模）、Person3（方案） | 待确认 | `工序时间信息.xlsx` 有重复列名：X/Y（第24/25列）均有644个非空值，但X为时间戳、Y为时长文本；BK/BL（第63/64列）同名且目前均为空。请确认 X/Y 的规范字段名和消费方式；审计脚本保留原列，不静默合并。 | 对字段语义/命名给出书面决定，供全量 ETL 使用 |
| Person1（建模） | 待确认 | 确认 Tier-2 的时间窗 90 min、重量差阈值 3 t 可否作为正式匹配契约；本轮样例 `26BD6651` 按方案阈值验证通过，但尚未全量执行。 | 更新参数来源/实体关联契约 |
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
| Person1（建模） | 待复核 | `source_quality_findings.csv` 中四组时间字段负差候选：出钢结束→重罐吊起 113 行、LF 出处理位→LF 吊起 115 行、RH 处理结束→RH 吊起 143 行、停浇→空包吊起 238 行。请结合实体号/设备时钟判断哪些是 A 炉 +1 错位、时钟偏差或其他情况；本次未修复、未定性。 | 每行含来源文件、工作表、Excel 行号、炉次号、时间原值与 SHA-256 |
| Person1（建模） | 待复核 | 3#LF 待机位吊起时间空 323 行、吊包天车空 323 行；B 炉钢水罐临时号空 435 行（A/C 分别 3/2）。请确认匹配修复契约与低置信度回退的记录方式。 | `source_quality_findings.csv` 中 `3lf_missing_candidate_field`、`bof_missing_steel_ladle_temp_id` 记录 |
| Person3（方案/论文） | 仅供核验 | 两 RH 表有 533 行脱气开始/结束时间相等；不要把它们当作真实零秒处理时长，需沿用已定的处理周期口径；299 行属于重复熔炼号候选行，包含占位及多阶段场景，不等于 299 个已确认坏行。 | 分规则源行清单；未做自动删除或改写 |

- `code/src/solver_preflight.py` 已跑通 CP-SAT 2 资源/4 任务合成模型（OPTIMAL）及 Pyomo+HiGHS 合成 LP（74/3）；**仅为依赖/API 冒烟，非项目调度结果**。记录在 `data/audit/solver_preflight.json`。
- 全源扫描输出 `data/audit/source_quality_summary.csv`、`source_quality_findings.csv`、`source_quality_audit.json`；12 份工作簿、6758 行，2529 条 review candidates，源文件哈希前后不变。

### 未完成与协作请求

- M1-01（完整浇次闭环）仍在进行：目前仅验证单炉跨表实体链和事件时间轴，尚未生成完整浇次 moves/schedule/trajectory/metrics，也未运行安全调度。
- M1-02 六类调度案例仍待实现；当前 7 个测试覆盖数据登记、计划统计、样例关联、源表异常扫描和求解器安装冒烟，不是项目调度验证。
- 收到字段语义与匹配契约确认后，Person2 继续全量 Tier-1/Tier-2 实体解析；不确定记录会保留原值、规则与置信度，不静默删除。
