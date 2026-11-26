# Person2 个人计划

- 当前身份与分工：Person2（ws，代码手）；用户已明确确认该身份，公共职责见 [PROJECT.md](../../PROJECT.md) §3。
- 当前阶段：已完成输入清点、全量源表只读质量/异常扫描、计划分母复核、`26BD6651` 样例链和 CP-SAT/Pyomo/HiGHS 环境冒烟；**全量实体修复、规范表、调度器尚未完成**。
- 当前目标：基于真实输入实现可追溯实体关联和规范事件表，先交付完整浇次安全闭环，再扩展场景 1/2/3。
- 当前代码与产物：`code/src/input_audit.py`、`plan_audit.py`、`trace_sample_heat.py`、`source_quality_audit.py`、`solver_preflight.py`；审计结果在 `data/audit/`；测试位于 `code/tests/`（7 项通过）。求解器 preflight 是合成玩具模型，不代表项目排产。
- 当前环境：`.research-lab/venv/bin/python`（Python 3.13.2）；已验证 pandas/openpyxl/xlrd。该环境 `pip` 命令脚本仍指向旧目录，安装包请用 `.research-lab/venv/bin/python -m pip`，勿依赖失效的 `venv/bin/pip` 启动脚本。

## 下一步

1. 请 Person1（建模）/Person3（方案）确认 `工序时间信息.xlsx` 重复表头 X/Y 的正式语义与字段名；保留两列，未确认前不静默去重。并确认 Tier-2 匹配窗/重量容差是否沿用方案的 90 min/3 t。
2. 将 Tier-1 临时罐号和 Tier-2（熔炼号＋钢包号＋时间单调窗＋重量一致）扩展到全部炉次，输出带来源文件/工作表/Excel 行号、修复规则和置信度的审计记录；优先回归 `26BD6651`。
3. 依模型契约生成 heats/events/operations/moves/resources 规范表，做完整浇次任务与安全预约闭环，并输出独立校验结果。
4. 补做六类边界案例（安全重叠、双工位单处理、总热龄超限、同侧恢复超时、降档制动、信息一致性），再按资源与时间预算扩展随机/故障场景。
5. 每阶段以实际产物和测试结果更新 history/plan/handoff；项目级状态同步 PROJECT.md。
