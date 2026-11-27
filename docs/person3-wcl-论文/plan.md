# Person3 个人计划

- 身份：当前用户（wcl）；负责论文、图表与总体统筹，仓库四份方案作者。
- 当前阶段（2026-11-26 20:00）：已完成公共规划瘦身修订、题面图 1/2 提取、英文 LaTeX 论文骨架（`paper/main.tex`、`refs.bib`、`ai_report.tex`）、真实实证图 Fig 1–5 以及 `claims_to_runs.csv` / `figure_manifest.csv` 溯源映射。
- 当前目标：推动团队按 `ef_initial_v1.json` 跑通**主动避让三机并行**、全量 ETL 及场景 2/3，并随时将新 `run_id` 结果与图回填至 `paper/main.tex` 的 `\pending{...}` 槽位。

## 下一步

1. **今晚同步团队**：确认以 `ef_initial_v1.json` 为统一基准物理配置、暂停 LBBD/Copula/ALNS/mmflow/MLflow、全力打通主动避让与全量 ETL。
2. **Overleaf 试编译**：将 `paper/` 上传 Overleaf（`pdfLaTeX`）验证首版英文排版、页数余量与 Fig 1–5 渲染。
3. **接力回填正文**：待 Person2 产出“主动避让三机并行安全解”、典型日/峰值日、150t 与平车选址灵敏度、以及场景 2/3 正式 `run_id` 后，生成对应矢量图并替换 `paper/main.tex` 中的 `\pending{...}`。
4. 同步维护 `paper/claims_to_runs.csv`、`paper/figure_manifest.csv` 与本目录记录。

## 当前上下文

- `paper/main.tex` 中所有已填数字均绑定 `data/audit/` 与 `data/runs/scene1_replay_6C0754/`、`data/runs/scene1_closed_loop_6C0754/`，未完成实验均显式标记 `\pending{...}`。
- 组织者明确校赛无唯一标准答案、过渡平车轨道位置与数量可自定义；已在论文 Section 3 写明东侧 3 轨基准假设，并在 Section 7/8 预留数量与西移选址灵敏度分析。

## 2026-11-27 当前评审接续

优先与 Person1/Person2 统一三场景的空间/生产约束、原始截止与故障事件状态；待重跑后同步摘要和对照结论。评审项见本人 handoff 的“本轮成果评审接续”，评审执行记录见 history。
