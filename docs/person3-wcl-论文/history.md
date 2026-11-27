# Person3 已完成工作记录

## 2026-11-26｜用户确认方案归属

- 当前用户为 Person3，用户确认仓库原有四份方案均为本人所做：
  - [01 数学建模方案](01_数学建模方案.md)
  - [02 总体流程规划](02_总体流程规划.md)
  - [03 O奖冲刺与论文指南](03_美赛O奖冲刺与论文制胜指南.md)
  - [04 三天实战时间规划](04_三天实战时间规划（11月26日-11月29日）.md)
- 以上是方案成果；正式英文稿、实验图和运行结果尚未登记。早先“仅论文角色”的槽位描述已据用户说明更正。

## 早期维护记录迁入

以下为此前维护 Agent 的实际工作记录，原临时记在 member_1 槽位；现归入本次用户对应的 Person3 维护记录，保留 Agent 执行者标识。

## 2026-11-26｜维护 Agent：方案修订

- 按方案评审修订空间冲突筛查、占位/加工/切换资源、全过程热窗、时间坐标等价、档位轨迹、随机信息一致性及故障恢复判定。
- 同步 02 总体流程、03 论文指南、04 三天安排及公共状态；区分三场景主线与增强算法。
- 产物：docs/ 下现有四份方案及 PROJECT.md；完成的是文档修订，未产生正式求解或实验结果。

## 2026-11-26｜维护 Agent：协作文档体系初始化

- 将用户指定的《美赛团队协作规范》原样复制为根目录 AGENTS.md。
- 建立 Person1/2/3 的 plan、history、handoff；角色槽位按建模/编程/论文初始化，姓名未绑定。
- 建立 data/、code/、paper/，补充空目录占位文件；保留四份原方案的位置与文件名。
- 更新 PROJECT.md 的职责、分工、状态、索引和开工顺序；将方案中的 solution/ 路径改为根目录统一布局。
- 校验：AGENTS.md 与原规范的 SHA-256 一致；三名成员共九份文档齐备；文档链接、代码块与文本格式检查通过。


## 2026-11-26｜维护 Agent：作者目录归档与链接调整

- 成员目录统一命名 person1/person2/person3；归档 Person1 两份原始方案，迁移 Person3 四份工作方案。
- 去掉 Person3 的 01/02 文件名中下载副本后缀“ (1)”，通过作者目录区分两组方案。
- 同步 PROJECT.md、个人计划与交接，修正早期角色推定；AGENTS.md 仅调整三处成员目录示例名。
- 更新原有内部链接和工程目录示例；Person1 两份原文保持来源字节一致。

## 2026-11-26｜维护 Agent：补全 PROJECT 公共总体规划

- 按用户要求，将 Person3 总体流程中的三场景路线、分工、72 小时节点、数据准备、接口、实验矩阵、工具、验收和降级安排汇总到 PROJECT.md。
- 保留作者归属、模型决策与真实当前阶段；规划中的待执行内容未改写为已完成。
- 按现有 person1-wj-建模、person2-ws-代码、person3-wcl-论文 目录更新公共索引与本人的计划链接。
- 02 文档增加公共计划维护入口；本人的交接同步当前职责与公共规划位置。

## 2026-11-26 17:00–20:00｜英文论文骨架、Fig 1–5 实证出图与全仓规划瘦身

- 提取题面内嵌原图至 `paper/figures/fig1_plant_layout_raw.png`、`fig2_mes_gantt_raw.png`。
- 编写 `code/src/viz/generate_paper_figures.py`，基于 `data/audit/` 与 `data/runs/scene1_replay_6C0754/`、`data/runs/scene1_closed_loop_6C0754/` 生成矢量图 `fig3_data_audit_durations.pdf`、`fig4_replay_6C0754_gantt.pdf` 与 `fig5_closed_loop_6C0754.pdf`。
- 建立 `paper/main.tex`（25 页美赛英文结构，含一页 Summary Sheet、目录与 Sections 1–8，未运行槽位以 `\pending{...}` 显式标注）、`paper/refs.bib`、`paper/ai_report.tex`、`paper/claims_to_runs.csv`、`paper/figure_manifest.csv`；通过纯 ASCII、括号平衡及插图路径检查。
- 结合组织者最新口径（无唯一标准答案、过渡平车轨道位置与数量可自定义）及最新 pull 进度，全面修订 `PROJECT.md`：锁定 `code/configs/ef_initial_v1.json` 为统一基准物理配置，暂停 LBBD/Copula/ALNS/mmflow/MLflow 冗余项，新增过渡平车数量（2/3/4）与西移选址灵敏度规划，并修复 `PROJECT.md` 断链与第 12 节过期状态。
- 完成全仓一致性收口检查：同步更新本目录 `plan.md`、`handoff.md`、`history.md` 与 `04_三天实战时间规划`（v1.2）；确认 `PROJECT.md` 与 `person3/` 0 断链、`paper/main.tex` 纯 ASCII 且 Fig 1–5 完整就绪，具备直接提交条件。

## 2026-11-27 09:05｜接入 `scene1_yield_6C0754` 主动避让成果（Fig 6 & Section 4.4）

- 更新 `code/src/viz/generate_paper_figures.py` 生成 `paper/figures/fig6_yield_6C0754.pdf/.png`（7 方案对比 + 1 s 分辨率五车认证轨迹，最小间距 12.24 m）。
- 更新 `paper/main.tex` 摘要与 Section 4.4（Table 3 + Fig. 6），写入主动避让 `0.52 min` vs 最优串行 `7.50 min`（-93.1%）、无避让 1/18 死锁与几何盲 11/18 死锁对比，明确单浇次两机边界。
- 同步更新 `paper/claims_to_runs.csv`（C16–C18）与 `paper/figure_manifest.csv`（Fig 6）。

## 2026-11-27 10:30｜完成全量三场景结果回填与 Fig 7–9 矢量出图

- 更新 `code/src/viz/generate_paper_figures.py`，生成 `fig7_scene1_day_layout_aux.pdf/.png`、`fig8_scene2_stochastic_risk.pdf/.png`、`fig9_scene3_fault_recovery.pdf/.png`。
- 回填 `paper/main.tex` 全部 `\pending{...}` 槽位（覆盖 Summary Sheet、KPI 速览表、Section 4.5 整日平车/150t、Section 5 场景 2 滚动随机、Section 6 场景 3 故障脆弱性、Section 7 独立校验、Section 8 管理建议），明确标注 63.8% 匹配覆盖率、整日预约保守上界及 L2 替代否决等科学边界。
- 同步更新 `paper/claims_to_runs.csv`（C19–C23）与 `paper/figure_manifest.csv`（Fig 7–9）。

## 2026-11-27 14:35｜接入 `97f3222` 正式 145 t 辅车结果并做 Overleaf 排版加固

- 将 `data/runs/scene1_aux/aux_sensitivity_wcl145.csv` 接入 `fig7_scene1_day_layout_aux` 与 `paper/main.tex` Table 4（`AUX_145t`）、`claims_to_runs.csv`（C21）。
- 在 `paper/main.tex` Section 3 补齐《核心符号总表》（Table 2），在 Section 7 写入 `code/run_all.py` 一键复现说明，并将 Summary Sheet 调紧为 `\small` / `\footnotesize` 防首摘要溢页、Table 4 调为 `\scriptsize` 防右出界。
