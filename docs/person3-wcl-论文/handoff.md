# Person3 交接

## 已确认归属

当前用户为 Person3（wcl），本目录四份方案及 `paper/` 英文论文骨架为 Person3 成果；Person1（wj）建模与 Person2（ws）代码产物按各自目录保留。

## 2026-11-27 10:35｜论文全量合龙与最终收口交接

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| **Person2（ws / 代码）** | **主线算例全齐，待最后 2 项收口** | 1. 补跑正式 **145 t 辅车对照**（`--aux-threshold 145.0` 替换占位 100–140 t）；<br>2. 确认 **`code/run_all.py`** 一键复现入口跑通、全套 **`pytest` 保持 green**；<br>3. 不再新增复杂模型，注意保留 `63.8%` 匹配覆盖率与保守上界口径。 | `data/runs/scene1_aux/`、`code/run_all.py` |
| **Person1（wj / 建模）** | **待审校 PDF** | 中午 `paper/` 上 Overleaf 编译出 PDF 后，请审校第 3–8 节公式符号、物理边界（平车西移添堵、`4#450` 单点脆弱性、L2 替代否决）与第 8 节管理建议。 | `paper/main.tex`、`claims_to_runs.csv` |
| **Person3（wcl / 论文）** | **已完成正文合龙，待试编译** | 已生成 **Fig 1–9** 并填满 `paper/main.tex` 全部 `\pending` 槽位（0 残留、纯 ASCII、23 条声明绑定 `claims_to_runs.csv`），中午传 Overleaf 编译查 25 页上限。 | `paper/main.tex`、`paper/figures/` |

## 2026-11-26 19:30｜赛题口径、配置统一与规划瘦身同步

| 接收人 | 状态 | 内容与下一步 | 完成依据 |
|---|---|---|---|
| **全体（wj、ws）** | **已决策、请同步执行** | 组织者明确校赛无唯一标准答案且过渡平车位置/数量可自定义。`PROJECT.md` 已修订：**① 锁定 `code/configs/ef_initial_v1.json` 为唯一基准物理配置**（不再阻塞在未确认状态）；**② 暂停 `LBBD / Copula / ALNS / 经典 VSS-EVPI / mmflow / MLflow`**，全力冲刺主线；**③ E7 新增过渡平车数量(2/3/4)与西移选址灵敏度**。 | `PROJECT.md` §1, §5, §8, §9, §12 |
| **Person1（wj / 建模）** | 待补充 | ① 确认 `ef_initial_v1.json` 为正式参数并在 `person1/handoff.md` 与 `person2/handoff.md` 闭环；② 给出简洁可编码的**主动避让（Yield）规则**以消除 `6C0754` 安全串行多等的 87.6 min；③ 修复 `docs/person1-wj-建模/` 内 4 处指向旧文件名 `E-F流程.md` 的链接（现为 `E-F流程给人看.md`）。 | `optimization_comparison.csv`、`04_场景1`、`05_初值表` |
| **Person2（ws / 代码）** | 待执行 | ① 统一切换至 `ef_initial_v1.json`，在 `6C0754` 补齐主动避让（Yield）拿到**安全三机并行解**，并将空包回程拆分为“作业停顿 + 纯行驶”（消除 `leg_calibration.csv` +12.60 min 残差）；② 按 C1–C3 跑全量 ETL 并选 1 常规日 + 1 峰值日；③ 按 `06` 契约推进场景 2/3。 | `ef_initial_v1.json`、`model_contract_v1.json` |
| **Person3（wcl / 论文）** | **已落地骨架、待接续结果** | `paper/main.tex`、`refs.bib`、`ai_report.tex`、`claims_to_runs.csv`、`figure_manifest.csv` 及 Fig 1–5 已就绪；待收到新 `run_id` 后直接出图并替换 `\pending{...}`。 | `paper/` 全部文件与 `code/src/viz/generate_paper_figures.py` |

## 原槽位清单状态更新

| 原编号 | 接收人/角色 | 最新状态 | 说明 |
|---|---|---|---|
| M1-01 | Person2 / 代码 | **进行中（首轮闭环已交付）** | `6C0754` 5 方案轨迹与门禁诊断已完成；下一步切至 `ef_initial_v1.json` 并补主动避让三机并行 |
| M1-02 | Person2 / 代码 | **简化保留核心验证** | 已通过 22 项 pytest（含二次轨迹极值点反例），其余边界案例随主动避让与场景 2/3 同步补充 |
| M1-03 | Person3 / 论文 | **持续同步中** | 已按实际口径写明条件性结论（`main.tex` Tab 2 & Fig 5） |
| M2-01 | Person1 / 建模 | **已完成** | 已交付 `model_contract_v1.json`、`ef_initial_v1.json` 与 `06` 契约 |
| M2-02 | Person3 / 论文 | **已落地** | `claims_to_runs.csv`（C01–C15）与 `figure_manifest.csv`（Fig 1–5）已建立 |
| M3-01 | Person1 / 建模 | **核心已交付** | 补完主动避让规则与平车灵敏度物理边界即可收口 |
| M3-02 | Person2 / 代码 | **首轮已对接** | `scene1_replay_6C0754` 与 `scene1_closed_loop_6C0754` 已写入论文，待全量与场景 2/3 `run_id` |

## 2026-11-27｜本轮成果评审接续

| 接收人 | 状态 | 内容与操作 |
|---|---|---|
| Person2（代码）、Person1（建模） | 待核验修正 | scene1_day.py::day_tasks 放宽 deadline；整日接受条件未含生产/热窗完整验证。保留原始截止并独立报告不可行与迟到。 |
| Person2（代码） | 待核验修正 | scene2_stochastic.py::rolling_execute 不建避让，与安全预约基线的约束不同；统一空间和生产约束后重跑配对评价。metrics 中迟到率不可同时作为断浇率和热窗率。 |
| Person2、Person1 | 待核验修正 | scene3_disruption.py 的 recover 按初始状态锁车/封路重排整日；补真实事件状态、冻结任务、故障起止和修复释放。window_violations 实际按迟到60min判定，需与30min总热龄分开。 |
| Person3（论文） | 待修订 | main.tex 中“三机并行”“随机/故障均已全面校验”“30min热窗”等须与真实实现对齐；更新场景2 09-28锚点268.2→258.0。统一算例和约束后再引用改善百分比。 |
