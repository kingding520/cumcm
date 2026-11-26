# Person2 个人计划

- 当前身份与分工：Person2（ws，代码手）；用户已明确确认该身份，公共职责见 [PROJECT.md](../../PROJECT.md) §3。
- 当前阶段：已切换到 wj 锁定的 `ef_initial_v1.json` 并实现**主动避让**。`6C0754` 上已有通过精确门禁且完整的方案：`fcfs_nearest` 18/18、最小间距 12.24 m、总等待 0.52 min；`pipeline_roles` 3.30 min；单机串行 7.50–9.83 min。关闭避让即死锁（1/18），证明该机制是必要条件。
- 当前目标：① 就「单浇次只需 2 台车 / 三机需交接位」这一结构结论与 wj 对齐；② 执行第二步：按 `model_contract_v1.json` C1–C3 跑全量 17.5 天 Tier-1/Tier-2 ETL → `data/processed/`，选常规日与峰值日，跑整日场景1对照 + 150t 辅车/平车选址灵敏度。
- 当前代码与产物：`code/src/` 下 `ef_model.py`、`scene1_yield.py`（新），以及 `rail_model.py`、`scenario1_closed_loop.py`、历史回放与统计脚本；参数 `code/configs/ef_initial_v1.json` + `model_contract_v1.json`；避让产物 `data/runs/scene1_yield_6C0754/`；`code/tests/` 共 **30 项通过**。
- 当前环境：`.research-lab/venv/bin/python`（Python 3.13.2）；已验证 pandas/openpyxl/xlrd。该环境 `pip` 命令脚本仍指向旧目录，安装包请用 `.research-lab/venv/bin/python -m pip`，勿依赖失效的 `venv/bin/pip` 启动脚本。

## 下一步

1. 几何与运动参数已由 wj 用 `ef_initial_v1.json` 锁定，不再阻塞。剩余待确认：三机并行是否需要**交接位**（当前 D=12 m 与物理顺序使本浇次只需 2 台车）。
2. 请 Person1（建模）/Person3（方案）确认 `工序时间信息.xlsx` 重复表头 X/Y 的正式语义与字段名；保留两列，未确认前不静默去重。并确认 Tier-2 匹配窗/重量容差是否沿用方案的 90 min/3 t。
3. 将 Tier-1 临时罐号和 Tier-2（熔炼号＋钢包号＋时间单调窗＋重量一致）扩展到全部炉次，输出带来源文件/工作表/Excel 行号、修复规则和置信度的审计记录；优先回归 `26BD6651`。
4. 依模型契约生成 heats/events/operations/moves/resources 规范表；`6C0754` 闭环已可复用为回归基线。
5. 补做六类边界案例（安全重叠、双工位单处理、总热龄超限、同侧恢复超时、降档制动、信息一致性），再按资源与时间预算扩展随机/故障场景。
6. 每阶段以实际产物和测试结果更新 history/plan/handoff；项目级状态同步 PROJECT.md。
