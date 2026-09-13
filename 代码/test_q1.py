# -*- coding: utf-8 -*-
"""test_q1.py — 问题 1 代码验收（纯脚本，无 pytest 依赖）。

运行：python test_q1.py   （先 source .venv/bin/activate）

覆盖（对应 公用/模型方程与边界符号_三人走读.md 走读清单与任务要求）：
  1. load_inputs : 附件 1 分段线性插值（节点精确、中点线性）+ 4 h 后延拓
  2. 量纲        : SI 单位、K 转换、扩散系数量级、边界系数为正
  3. solve_q1    : 中心/表面物理方向、非负水分、结果有限
  4. 简化算例    : 恒定平衡环境 → 状态保持不变
  5. 网格收敛    : 80/160/320 三档，160 vs 320 满足配置容差
  6. 质量/能量收支: 库存变化 = 表面外流积分（机器精度）
  7. postprocess : 表 1/2 的位置与时间、4 位小数、单调性

任一检查失败以 AssertionError 终止并打印失败项；全部通过打印
"test_q1: PASS" 与关键数值摘要。
"""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_q1 import CFG, to_kelvin, diff_coeff_q1  # noqa: E402
from load_inputs import Environment, load_inputs      # noqa: E402
from postprocess_q1 import postprocess_q1             # noqa: E402
from solve_q1 import solve_q1                         # noqa: E402

_passed = []


def check(name, cond, detail=""):
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    _passed.append(name)
    print(f"  [PASS] {name}")


def test_load_inputs():
    print("-- load_inputs: 附件 1 分段线性插值与延拓 --")
    env = load_inputs(CFG)
    check("时间轴 0..14400 s、60 s 间隔、241 行",
          env.t[0] == 0.0 and env.t[-1] == 14400.0
          and env.t.size == 241 and np.allclose(np.diff(env.t), 60.0),
          f"(t.size={env.t.size})")
    check("延拓主方案为 nominal_setpoint",
          env.mode == "nominal_setpoint"
          and env.post_Tinf == 50.0 and env.post_Cinf == 0.05)
    check("节点值精确复现（分段线性经过数据点）",
          np.allclose(env.eval_T(env.t), env.Tinf, atol=1e-12)
          and np.allclose(env.eval_C(env.t), env.Cinf, atol=1e-12))
    mid = 0.5 * (env.t[10] + env.t[11])
    check("中点线性插值",
          abs(env.eval_T(mid) - 0.5 * (env.Tinf[10] + env.Tinf[11])) < 1e-12
          and abs(env.eval_C(mid) - 0.5 * (env.Cinf[10] + env.Cinf[11])) < 1e-12)
    check("t>14400 s 延拓为 (50 °C, 0.05)",
          abs(env.eval_T(20000.0) - 50.0) < 1e-12
          and abs(env.eval_C(20000.0) - 0.05) < 1e-12)
    check("向量求值",
          np.allclose(env.eval_T([100.0, 600.0, 20000.0]),
                      [env.eval_T(100.0), env.eval_T(600.0), 50.0], atol=1e-12))

    env_last = load_inputs(CFG, extension_mode="last_observation")
    check("last_observation 延拓",
          abs(env_last.eval_T(20000.0) - env_last.Tinf[-1]) < 1e-12
          and abs(env_last.eval_C(20000.0) - env_last.Cinf[-1]) < 1e-12)
    env_mean = load_inputs(CFG, extension_mode="last_30min_mean")
    mask = env_mean.t >= 14400.0 - 1800.0
    check("last_30min_mean 延拓",
          abs(env_mean.eval_T(20000.0) - float(np.mean(env_mean.Tinf[mask]))) < 1e-12
          and abs(env_mean.eval_C(20000.0) - float(np.mean(env_mean.Cinf[mask]))) < 1e-12)


def test_dimensions():
    print("-- 量纲: SI 单位、K 转换、物性量级 --")
    u = CFG["units"]
    check("长度/时间/温度/水分单位约定",
          u["length"] == "m" and u["time"] == "s"
          and u["temperature_state"] == "degC"
          and u["temperature_in_arrhenius"] == "K"
          and u["moisture"] == "kg/kg (dry basis)")
    check("几何与初值（半径 2 cm、长 25 cm、28 °C、2.55）",
          CFG["geometry"]["radius0"] == 0.02
          and CFG["geometry"]["length"] == 0.25
          and CFG["initial"]["T"] == 28.0 and CFG["initial"]["C"] == 2.55)
    check("摄氏→开尔文转换（28 °C = 301.15 K）",
          abs(to_kelvin(28.0) - 301.15) < 1e-12)
    D0 = float(diff_coeff_q1(2.55))
    check("扩散系数量级与正性 (D≈4.94e-9 m^2/s)",
          1e-10 < D0 < 1e-7 and abs(D0 - 7e-9 * np.exp(-0.89 / 2.55)) < 1e-15,
          f"(D={D0:.4e})")
    check("问题 1 的 D 与温度无关（调用约定仍传 K）",
          np.allclose(diff_coeff_q1(2.55, to_kelvin(28.0)),
                      diff_coeff_q1(2.55, 9999.0)))
    check("Robin 传递系数为正且量级合理",
          CFG["boundary"]["h"] > 0 and 0 < CFG["boundary"]["hm"] < 1e-3)
    check("输出/收敛设置与题面一致",
          CFG["output"]["table1_times_s"] == [100, 300, 600, 900, 1200, 1500, 1800]
          and CFG["output"]["table_radius_cm"] == [0.0, 0.5, 1.0, 1.5, 2.0]
          and CFG["numerics"]["convergence_cells"] == [80, 160, 320])


def test_solve_directions():
    print("-- solve_q1: 中心/表面物理方向、非负水分 --")
    out = solve_q1(CFG, n_cells=160, t_eval=[0, 60, 600, 1200, 1800])
    check("结果维度", out.T.shape == (5, 160) and out.C.shape == (5, 160))
    check("结果有限（无 NaN/Inf）",
          np.all(np.isfinite(out.T)) and np.all(np.isfinite(out.C)))
    check("非负水分", out.C.min() >= -1e-10, f"(min={out.C.min():.3e})")

    after = slice(1, None)
    T_gap = out.T[after, -1] - out.T[after, 0]   # 表面单元 - 中心单元
    C_gap = out.C[after, -1] - out.C[after, 0]
    check("温度方向：升温期表面不低于中心",
          np.all(T_gap >= -1e-8), f"(min gap={T_gap.min():.3e})")
    check("水分方向：干燥期表面不高于中心",
          np.all(C_gap <= 1e-8), f"(max gap={C_gap.max():.3e})")

    # 表面 Robin 重构方向的最终时刻检查
    Trow, Crow = out.T[-1], out.C[-1]
    Tinf = out.environment.eval_T(out.t[-1])
    Cinf = out.environment.eval_C(out.t[-1])
    dr = out.radius / 160.0
    k = CFG["properties_q1"]["k"]
    D_last = float(diff_coeff_q1(Crow[-1], to_kelvin(Trow[-1])))
    q_out = (Trow[-1] - Tinf) / (dr / (2.0 * k) + 1.0 / CFG["boundary"]["h"])
    j_out = (Crow[-1] - Cinf) / (dr / (2.0 * D_last) + 1.0 / CFG["boundary"]["hm"])
    Tsurf = Tinf + q_out / CFG["boundary"]["h"]
    Csurf = Cinf + j_out / CFG["boundary"]["hm"]
    check("Robin 重构表面温度不低于中心",
          Tsurf >= Trow[0] - 1e-8, f"(Tsurf={Tsurf:.6f}, Tcenter={Trow[0]:.6f})")
    check("Robin 重构表面含水率不高于中心",
          Csurf <= Crow[0] + 1e-8, f"(Csurf={Csurf:.6f}, Ccenter={Crow[0]:.6f})")
    check("1800 s 中心温度高于初值（预热升温）",
          out.T[-1, 0] > 28.0)
    check("1800 s 中心含水率低于初值（干燥推进）",
          out.C[-1, 0] < 2.55)


def test_equilibrium():
    print("-- 简化算例: 恒定平衡环境 → 状态不变 --")
    eq = Environment(t=[0.0, 300.0], Tinf=[28.0, 28.0], Cinf=[2.55, 2.55])
    out = solve_q1(CFG, n_cells=40, t_eval=[0, 30, 150, 300],
                   t_end=300.0, environment=eq)
    check("平衡算例温度保持不变",
          np.max(np.abs(out.T - 28.0)) < 1e-8,
          f"(max err={np.max(np.abs(out.T - 28.0)):.2e})")
    check("平衡算例水分保持不变",
          np.max(np.abs(out.C - 2.55)) < 1e-8,
          f"(max err={np.max(np.abs(out.C - 2.55)):.2e})")


def test_convergence():
    print("-- 网格收敛: 80/160/320 --")
    cells = [80, 160, 320]
    outs = [solve_q1(CFG, n_cells=nc, t_eval=[0, 600, 1200, 1800])
            for nc in cells]
    r_ref = outs[1].r  # 160 单元中心为公共比较网格
    T80 = np.interp(r_ref, outs[0].r, outs[0].T[-1])
    T160 = np.interp(r_ref, outs[1].r, outs[1].T[-1])
    T320 = np.interp(r_ref, outs[2].r, outs[2].T[-1])
    C80 = np.interp(r_ref, outs[0].r, outs[0].C[-1])
    C160 = np.interp(r_ref, outs[1].r, outs[1].C[-1])
    C320 = np.interp(r_ref, outs[2].r, outs[2].C[-1])

    errT = np.max(np.abs(T160 - T320))
    errC = np.max(np.abs(C160 - C320))
    check("温度 160 vs 320 收敛",
          errT <= CFG["validation"]["temperature_grid_tolerance"],
          f"(err={errT:.3e} > {CFG['validation']['temperature_grid_tolerance']:.1e})")
    check("水分 160 vs 320 收敛",
          errC <= CFG["validation"]["moisture_grid_tolerance"],
          f"(err={errC:.3e} > {CFG['validation']['moisture_grid_tolerance']:.1e})")

    # 报告用：80 vs 160 与 160 vs 320 的误差比（收敛阶参考）
    return {
        "T80v160": float(np.max(np.abs(T80 - T160))),
        "T160v320": errT,
        "C80v160": float(np.max(np.abs(C80 - C160))),
        "C160v320": errC,
    }


def test_balance():
    print("-- 离散质量/能量收支（库存变化 = 表面外流积分） --")
    out = solve_q1(CFG, n_cells=160)  # 全部积分节点
    cfg = CFG
    V = np.pi * (out.r_faces[1:] ** 2 - out.r_faces[:-1] ** 2) \
        * cfg["geometry"]["length"]
    A = 2.0 * np.pi * out.radius * cfg["geometry"]["length"]
    dr = out.radius / out.C.shape[1]
    hm = cfg["boundary"]["hm"]
    h = cfg["boundary"]["h"]
    k = cfg["properties_q1"]["k"]

    fluxC = np.empty(len(out.t))
    fluxT = np.empty(len(out.t))
    for j in range(len(out.t)):
        tt = out.t[j]
        Crow, Trow = out.C[j], out.T[j]
        Cinf = out.environment.eval_C(tt)
        Tinf = out.environment.eval_T(tt)
        Dlast = float(diff_coeff_q1(Crow[-1], to_kelvin(Trow[-1])))
        fluxC[j] = (Crow[-1] - Cinf) / (dr / (2.0 * Dlast) + 1.0 / hm)
        fluxT[j] = (Trow[-1] - Tinf) / (dr / (2.0 * k) + 1.0 / h)
    dt = np.diff(out.t)
    accC = float(np.sum(0.5 * (fluxC[:-1] + fluxC[1:]) * dt) * A)
    accT = float(np.sum(0.5 * (fluxT[:-1] + fluxT[1:]) * dt) * A)

    I = out.C @ V
    relC = abs((I[0] - I[-1]) - accC) / I[0]

    rho = cfg["properties_q1"]["rho"]
    cp = cfg["properties_q1"]["cp"]
    E = out.T @ (rho * cp * V)
    E0 = cfg["initial"]["T"] * rho * cp * float(np.sum(V))
    relE = abs((E[-1] - E[0]) + accT) / (abs(accT) + E0)

    check("水分质量收支", relC <= 1e-10, f"(rel={relC:.1e})")
    check("能量收支", relE <= 1e-10, f"(rel={relE:.1e})")
    return {"relC": relC, "relE": relE}


def test_postprocess():
    print("-- postprocess_q1: 表 1/2 位置与时间 --")
    out = solve_q1(CFG, n_cells=160)  # 默认 t_end=1800 s
    pp = postprocess_q1(out, CFG)
    check("表时间与位置",
          np.array_equal(pp["times_s"], [100, 300, 600, 900, 1200, 1500, 1800])
          and np.array_equal(pp["radius_cm"], [0.0, 0.5, 1.0, 1.5, 2.0]))
    check("表维度 (7 时间 × 5 位置)",
          pp["temperature_table"].shape == (7, 5)
          and pp["moisture_table"].shape == (7, 5))
    check("保留 4 位小数",
          np.allclose(pp["temperature_table"], np.round(pp["temperature_table"], 4))
          and np.allclose(pp["moisture_table"], np.round(pp["moisture_table"], 4)))
    check("位置 0 取中心、位置 2 cm 为表面重构值",
          np.allclose(pp["temperature_table"][:, 0], out.T[
              [np.searchsorted(out.t, tt) for tt in pp["times_s"]], 0], atol=1e-8)
          and np.all(pp["temperature_table"][:, -1] > pp["temperature_table"][:, 0]))
    check("预热期温度随位置和时间单调不减",
          np.all(np.diff(pp["temperature_table"], axis=1) >= -1e-10)
          and np.all(np.diff(pp["temperature_table"], axis=0) >= -1e-10))
    check("干燥期水分随位置单调不增、随时间单调不增",
          np.all(np.diff(pp["moisture_table"], axis=1) <= 1e-10)
          and np.all(np.diff(pp["moisture_table"], axis=0) <= 1e-10))
    return pp


def main():
    t0 = time.time()
    test_load_inputs()
    test_dimensions()
    test_solve_directions()
    test_equilibrium()
    conv = test_convergence()
    bal = test_balance()
    pp = test_postprocess()
    print()
    print("网格收敛摘要 (1800 s 径向最大差):")
    print(f"  T 80vs160 = {conv['T80v160']:.3e}, 160vs320 = {conv['T160v320']:.3e}")
    print(f"  C 80vs160 = {conv['C80v160']:.3e}, 160vs320 = {conv['C160v320']:.3e}")
    print(f"质量/能量收支相对残差: C={bal['relC']:.1e}, E={bal['relE']:.1e}")
    print("表 1 温度 (°C) / 表 2 水分 (kg/kg)，行=时间 s，列=0..2 cm：")
    print(np.array2string(pp["temperature_table"], precision=4, suppress_small=False))
    print(np.array2string(pp["moisture_table"], precision=4, suppress_small=False))
    print(f"test_q1: PASS  （{len(_passed)} 项检查，耗时 {time.time() - t0:.1f} s）")


if __name__ == "__main__":
    main()
