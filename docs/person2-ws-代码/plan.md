# Person2 个人计划

- 当前身份与分工：Person2（ws，代码手）；用户已明确确认该身份，公共职责见 [PROJECT.md](../../PROJECT.md) §3。
- 当前阶段：`6C0754` 单浇次 **M1-01 闭环已完成一轮**——轨迹文件、独立连续时间防碰撞门禁、5 种方案对比均已产出；门禁判定只有保守单机串行可接受，三机并行方案因跨越轨道被拒。全部运动学/安全参数仍为临时值（`confirmed_by_modeler: false`）。
- 当前目标：请 wj 确认几何/运动学/安全间距/初始位置四项参数与「主动避让或合法换车」的建模取舍；参数确认后重跑同一门禁并给出可接受的三机并行方案，再按确认的实体契约开展全量 ETL。
- 当前代码与产物：`code/src/` 下 `clean_row_statistics.py`、`scenario1_historical_replay.py`、`rail_model.py`、`scenario1_closed_loop.py`；参数在 `code/configs/scene1_rail_params.yml`；闭环产物在 `data/runs/scene1_closed_loop_6C0754/`；`code/tests/` 共 **22 项通过**。
- 当前环境：`.research-lab/venv/bin/python`（Python 3.13.2）；已验证 pandas/openpyxl/xlrd。该环境 `pip` 命令脚本仍指向旧目录，安装包请用 `.research-lab/venv/bin/python -m pip`，勿依赖失效的 `venv/bin/pip` 启动脚本。

## 下一步

1. **等 wj 确认（阻塞全量 ETL）**：① 相对像素坐标与 κ 换算；② 行车初始位置；③ 轨道安全间距 D_r；④ 加/减速度、档位速度；⑤ 是否采用主动避让 / 合法换车点建模。在此之前，闭环结论只作为条件性结论引用。
2. 请 Person1（建模）/Person3（方案）确认 `工序时间信息.xlsx` 重复表头 X/Y 的正式语义与字段名；保留两列，未确认前不静默去重。并确认 Tier-2 匹配窗/重量容差是否沿用方案的 90 min/3 t。
3. 将 Tier-1 临时罐号和 Tier-2（熔炼号＋钢包号＋时间单调窗＋重量一致）扩展到全部炉次，输出带来源文件/工作表/Excel 行号、修复规则和置信度的审计记录；优先回归 `26BD6651`。
4. 依模型契约生成 heats/events/operations/moves/resources 规范表；`6C0754` 闭环已可复用为回归基线。
5. 补做六类边界案例（安全重叠、双工位单处理、总热龄超限、同侧恢复超时、降档制动、信息一致性），再按资源与时间预算扩展随机/故障场景。
6. 每阶段以实际产物和测试结果更新 history/plan/handoff；项目级状态同步 PROJECT.md。
