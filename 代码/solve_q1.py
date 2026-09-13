# -*- coding: utf-8 -*-
"""solve_q1.py — 问题 1：固定半径圆柱有限体积离散 + 隐式时间积分。

方程与边界（公用/模型方程与边界符号_三人走读.md 第 2 节）：
    rho*cp * dT/dt = (1/r) d/dr ( r * k * dT/dr )
    dC/dt          = (1/r) d/dr ( r * D(C) * dC/dr )
  初始：T(r,0)=28 °C，C(r,0)=2.55 kg/kg；
  中心：dT/dr(0,t)=0，dC/dr(0,t)=0（零通量）；
  表面 Robin：
    -k  dT/dr(R,t) = h  [T(R,t) - T∞(t)]
    -D  dC/dr(R,t) = hm [C(R,t) - C∞(t)]

离散：环状控制体 FVM。第 i 个控制体为 r∈[r_i, r_{i+1}] 的环，
体积 V_i = π(r_{i+1}² - r_i²)L，面面积 A = 2πrL；
内部面 k、D 取调和平均（配置 nonlinear_face_average=harmonic）；
表面用串联热阻 Robin 离散：
    q_in = A·(T∞ - T_n) / (Δr/2/k_n + 1/h)，j_in 同理用 hm。

时间积分：环境无 scipy，采用 θ-法（默认 θ=0.5 即 Crank–Nicolson，
二阶精度；θ=1 退化为隐式欧拉）：
    [I - θ·dt·M] y^{n+1} = [I + (1-θ)·dt·M] y^n
                           + dt·[(1-θ)·b(t^n) + θ·b(t^{n+1})]
- T 方程对问题 1 是常系数（k、ρ、c_p、h 均恒定），三对角矩阵只组装一次；
- C 方程的系数随 C 变化（D(C)），每个时间步内 Picard 迭代；
- 三对角系统用 Thomas 算法求解（无 scipy 依赖）。
系数矩阵是 M-矩阵，配合边界设定天然保证水分非负。

返回 SimpleNamespace:
    t, r, r_faces, T, C, environment, radius, meta
"""

from types import SimpleNamespace

import numpy as np

from config_q1 import CFG, to_kelvin, diff_coeff_q1
from load_inputs import Environment, load_inputs


def solve_q1(cfg=None, n_cells=None, t_eval=None, t_end=None,
             environment=None, dt=None, theta=None):
    """求解问题 1 的径向热-质传递。

    参数
    ----
    cfg : 配置字典，默认 config_q1.CFG。
    n_cells : 空间网格数，默认 cfg["numerics"]["active_cells"]。
    t_eval : 输出时间点 (s)，默认取全部积分节点 0..t_end。
    t_end : 结束时间 (s)，默认 1800（问题 1 要求 30 min）。
    environment : Environment 对象；默认 load_inputs(cfg)。
    dt : 时间步长 (s)，默认 cfg["numerics"]["dt"]。
    theta : θ 法参数，默认 0.5（Crank–Nicolson）；1.0 为隐式欧拉。
    """
    cfg = CFG if cfg is None else cfg
    n_cells = int(cfg["numerics"]["active_cells"] if n_cells is None else n_cells)
    assert n_cells >= 3, "n_cells 至少为 3。"
    dt = float(cfg["numerics"]["dt"] if dt is None else dt)
    assert dt > 0, "dt 必须为正。"
    theta = float(cfg["numerics"].get("theta", 0.5) if theta is None else theta)
    assert 0.5 <= theta <= 1.0, "θ 取值须在 [0.5, 1]（0.5=Crank-Nicolson）。"

    if environment is None:
        environment = load_inputs(cfg)
    else:
        assert isinstance(environment, Environment), \
            "environment 必须是 load_inputs.Environment 实例。"
    assert environment.t[0] == 0.0, "环境时间必须从 0 开始。"

    if t_end is None:
        t_end = float(np.max(t_eval)) if t_eval is not None else 1800.0
    t_end = float(t_end)
    assert t_end > 0, "t_end 必须为正。"

    if t_eval is None:
        t_eval = np.arange(0.0, t_end + dt / 2.0, dt)
    t_eval = np.sort(np.asarray(t_eval, dtype=float))
    t_eval = t_eval[t_eval >= 0.0]
    assert t_eval.size >= 1, "t_eval 为空。"

    # ---------------- 几何 ----------------
    R = float(cfg["geometry"]["radius0"])
    L = float(cfg["geometry"]["length"])
    dr = R / n_cells
    r_faces = np.linspace(0.0, R, n_cells + 1)
    r = 0.5 * (r_faces[:-1] + r_faces[1:])            # 单元中心
    vol = np.pi * (r_faces[1:] ** 2 - r_faces[:-1] ** 2) * L  # 环体积
    area_faces = 2.0 * np.pi * r_faces * L            # 面面积
    A_surf = area_faces[-1]

    # ---------------- 物性（附录 2，问题 1 恒定） ----------------
    rho = float(cfg["properties_q1"]["rho"])
    cp = float(cfg["properties_q1"]["cp"])
    k = float(cfg["properties_q1"]["k"])
    h = float(cfg["boundary"]["h"])
    hm = float(cfg["boundary"]["hm"])
    rho_cp_vol = rho * cp * vol

    # 内部面电导（k 恒定 → 调和平均即 k 本身）
    G_T = k * area_faces[1:-1] / dr                     # 长度 n_cells-1
    G_bT = A_surf / (dr / (2.0 * k) + 1.0 / h)          # 表面串联热阻电导

    # ---------------- T 系统：常系数，只组装一次 ----------------
    lo_T, diag_T, up_T = _build_M(n_cells, rho_cp_vol, G_T, G_bT)
    aT, bT, cT = _assemble_system(n_cells, theta * dt, lo_T, diag_T, up_T)

    # ---------------- 初始条件 ----------------
    T = np.full(n_cells, float(cfg["initial"]["T"]))
    C = np.full(n_cells, float(cfg["initial"]["C"]))

    # ---------------- 时间积分（θ-法） ----------------
    picard_tol = float(cfg["numerics"]["picard_tol"])
    picard_max_iter = int(cfg["numerics"]["picard_max_iter"])

    times = [0.0]
    T_hist = [T.copy()]
    C_hist = [C.copy()]

    t_now = 0.0
    Tinf_now = environment.eval_T(t_now)
    Cinf_now = environment.eval_C(t_now)
    while t_now < t_end - 1e-12:
        h_step = min(dt, t_end - t_now)
        t_next = t_now + h_step
        Tinf_next = environment.eval_T(t_next)
        Cinf_next = environment.eval_C(t_next)

        # --- T：常系数线性系统 ---
        rhsT = _rhs_theta(T, lo_T, diag_T, up_T, h_step, theta,
                          G_bT, rho_cp_vol[-1], Tinf_now, Tinf_next)
        T = _solve_tridiagonal(aT, bT, cT, rhsT)

        # --- C：Picard 迭代（系数随 C 变化） ---
        D_now = diff_coeff_q1(C, to_kelvin(T))
        G_C_now, G_bC_now = _face_conductances_C(D_now, area_faces, dr, hm, A_surf)
        loC_now, diagC_now, upC_now = _build_M(n_cells, vol, G_C_now, G_bC_now)
        rhsC_base = C + (1.0 - theta) * h_step * _matvec(loC_now, diagC_now, upC_now, C)
        rhsC_base[-1] += (1.0 - theta) * h_step * G_bC_now * Cinf_now / vol[-1]

        Ck = C.copy()
        converged = False
        for _ in range(picard_max_iter):
            Dk = diff_coeff_q1(Ck, to_kelvin(T))
            G_Ck, G_bCk = _face_conductances_C(Dk, area_faces, dr, hm, A_surf)
            loC, diagC, upC = _build_M(n_cells, vol, G_Ck, G_bCk)
            aC, bC, cC = _assemble_system(n_cells, theta * h_step, loC, diagC, upC)
            rhsC = rhsC_base.copy()
            rhsC[-1] += theta * h_step * G_bCk * Cinf_next / vol[-1]
            Cnext = _solve_tridiagonal(aC, bC, cC, rhsC)
            if np.max(np.abs(Cnext - Ck)) <= picard_tol * max(1.0, np.max(np.abs(Ck))):
                C = Cnext
                converged = True
                break
            Ck = Cnext
        assert converged, f"水分 Picard 迭代未收敛 (t={t_next:.3f} s)。"

        t_now = t_next
        Tinf_now, Cinf_now = Tinf_next, Cinf_next
        times.append(t_now)
        T_hist.append(T.copy())
        C_hist.append(C.copy())

    times = np.asarray(times)
    T_hist = np.asarray(T_hist)
    C_hist = np.asarray(C_hist)

    # ---------------- 在 t_eval 上重采样 ----------------
    T_out = np.column_stack(
        [np.interp(t_eval, times, T_hist[:, j]) for j in range(n_cells)])
    C_out = np.column_stack(
        [np.interp(t_eval, times, C_hist[:, j]) for j in range(n_cells)])

    return SimpleNamespace(
        t=t_eval,
        r=r,
        r_faces=r_faces,
        T=T_out,
        C=C_out,
        environment=environment,
        radius=R,
        meta={
            "question": 1,
            "method": "cylindrical FVM + theta-method time integration",
            "theta": theta,
            "n_cells": n_cells,
            "dt": dt,
            "time_unit": "s",
            "temperature_unit": "degC",
            "moisture_unit": "kg/kg dry basis",
        },
    )


def _face_conductances_C(D, area_faces, dr, hm, A_surf):
    """由单元中心扩散系数 D 计算内部面（调和平均）与表面电导。"""
    D = np.maximum(D, 1e-30)
    Df = 2.0 * D[:-1] * D[1:] / (D[:-1] + D[1:])   # 内部面调和平均
    G_C = Df * area_faces[1:-1] / dr
    G_bC = A_surf / (dr / (2.0 * D[-1]) + 1.0 / hm)
    return G_C, G_bC


def _build_M(n, vol_scale, G_faces, G_boundary):
    """组装算子 M（离散 d/dt = M·y + b）的三对角表示。

    M 行和为零（常数场无通量）；边界行的 -G_boundary 由 b 中的
    T∞/C∞ 项抵消，故平衡时 M·y + b = 0。
    """
    lo = np.zeros(n - 1)
    diag = np.zeros(n)
    up = np.zeros(n - 1)
    for i in range(n):
        g_left = G_faces[i - 1] if i > 0 else 0.0
        g_right = G_faces[i] if i < n - 1 else G_boundary
        diag[i] = -(g_left + g_right) / vol_scale[i]
        if i > 0:
            lo[i - 1] = g_left / vol_scale[i]
        if i < n - 1:
            up[i] = g_right / vol_scale[i]
    return lo, diag, up


def _assemble_system(n, scale, lo, diag, up):
    """组装 I - scale*M 的三对角表示（θ 法左侧矩阵）。"""
    a = -scale * lo
    b = 1.0 - scale * diag
    c = -scale * up
    return a, b, c


def _matvec(lo, diag, up, v):
    """三对角矩阵 M 乘以向量 v。"""
    n = v.size
    out = np.empty(n)
    for i in range(n):
        s = 0.0
        if i > 0:
            s += lo[i - 1] * v[i - 1]
        s += diag[i] * v[i]
        if i < n - 1:
            s += up[i] * v[i + 1]
        out[i] = s
    return out


def _rhs_theta(y, lo, diag, up, dt, theta, G_b, vol_scale_last,
               inf_now, inf_next):
    """θ 法右端：[I + (1-θ)dt·M] y + dt·[(1-θ)b(t^n) + θ·b(t^{n+1})]。

    b 只作用于最后一层（表面 Robin 的环境项）。
    """
    rhs = y + (1.0 - theta) * dt * _matvec(lo, diag, up, y)
    rhs[-1] += dt * ((1.0 - theta) * G_b * inf_now
                     + theta * G_b * inf_next) / vol_scale_last
    return rhs


def _solve_tridiagonal(a, b, c, d):
    """Thomas 算法解三对角系统 a[i]*x[i-1] + b[i]*x[i] + c[i]*x[i+1] = d[i]。

    a, c 长度 n-1；b, d 长度 n。对角占优（M-矩阵）时无需选主元。
    """
    n = b.size
    cp = np.empty(n - 1)
    dp = np.empty(n)
    cp[0] = c[0] / b[0]
    dp[0] = d[0] / b[0]
    for i in range(1, n):
        denom = b[i] - a[i - 1] * cp[i - 1]
        dp[i] = (d[i] - a[i - 1] * dp[i - 1]) / denom
        if i < n - 1:
            cp[i] = c[i] / denom
    x = np.empty(n)
    x[-1] = dp[-1]
    for i in range(n - 2, -1, -1):
        x[i] = dp[i] - cp[i] * x[i + 1]
    return x
