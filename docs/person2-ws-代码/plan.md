# Person2 个人计划

- 当前身份与分工：Person2（ws，代码手）；用户已明确确认该身份，公共职责见 [PROJECT.md](../../PROJECT.md) §3。
- 当前阶段：已完成全源审计、计划表分母复核、结构/时间完整行基础统计及 `6C0754`（6 炉次）场景1历史回放最小数据闭环；统计目前按唯一熔炼号连接，不代表 Tier-1/Tier-2 实体关系全部通过。**全量实体修复、场景1优化调度和物理轨迹/安全认证尚未完成**。
- 当前目标：先由建模手复核样例组中的实体/时钟异常和暂定 Tier-2 阈值；再将已核准的关系扩展至完整任务集，构建可验证的安全预约调度。
- 当前代码与产物：新增 `code/src/clean_row_statistics.py`、`scenario1_historical_replay.py`；统计与回放产物见 `data/audit/section2_basic_stats.json` 和 `data/runs/scene1_replay_6C0754/`；测试位于 `code/tests/`（9 项通过）。场景1产物是固定行车指派/实际时刻历史回放，不代表优化结果。
- 当前环境：`.research-lab/venv/bin/python`（Python 3.13.2）；已验证 pandas/openpyxl/xlrd。该环境 `pip` 命令脚本仍指向旧目录，安装包请用 `.research-lab/venv/bin/python -m pip`，勿依赖失效的 `venv/bin/pip` 启动脚本。

## 下一步

1. 请 Person1（建模）/Person3（方案）确认 `工序时间信息.xlsx` 重复表头 X/Y 的正式语义与字段名；保留两列，未确认前不静默去重。并确认 Tier-2 匹配窗/重量容差是否沿用方案的 90 min/3 t。
2. 将 Tier-1 临时罐号和 Tier-2（熔炼号＋钢包号＋时间单调窗＋重量一致）扩展到全部炉次，输出带来源文件/工作表/Excel 行号、修复规则和置信度的审计记录；优先回归 `26BD6651`。
3. 依模型契约生成 heats/events/operations/moves/resources 规范表，做完整浇次任务与安全预约闭环，并输出独立校验结果。
4. 补做六类边界案例（安全重叠、双工位单处理、总热龄超限、同侧恢复超时、降档制动、信息一致性），再按资源与时间预算扩展随机/故障场景。
5. 每阶段以实际产物和测试结果更新 history/plan/handoff；项目级状态同步 PROJECT.md。
