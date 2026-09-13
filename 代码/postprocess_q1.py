# -*- coding: utf-8 -*-
"""postprocess_q1.py — 导出表 1 / 表 2 需要的位置与时间采样（内存返回）。

表 1（温度）、表 2（水分浓度）：
    时间：100, 300, 600, 900, 1200, 1500, 1800 s
    到药材中心距离：0, 0.5, 1, 1.5, 2 cm
    结果保留 4 位小数（题目要求）。

约定（公用/模型方程与边界符号_三人走读.md）：
- r=0 处取中心单元值；r=2 cm 即表面，用 Robin 边界从最后一层单元中心
  外推重构真实表面值（不拿最近内点冒充）；
- 按团队纪律，本阶段只返回内存数据结构，不写 Excel 也不写 CSV
  （result1.xlsx 的导出待模板格式冻结后另行实现）。
"""

import numpy as np

from config_q1 import CFG, to_kelvin, diff_coeff_q1


def postprocess_q1(out, cfg=None):
    """对 solve_q1 的输出采样表 1 / 表 2 数据。

    返回 dict:
        times_s          : 表时间 (7,)，s
        radius_cm        : 表位置 (5,)，cm
        temperature_table: (7,5) 温度，°C，4 位小数
        moisture_table   : (7,5) 水分浓度，kg/kg 干基，4 位小数
        units            : 各量单位说明
    """
    cfg = CFG if cfg is None else cfg
    times = np.asarray(cfg["output"]["table1_times_s"], dtype=float)
    radius_cm = np.asarray(cfg["output"]["table_radius_cm"], dtype=float)
    radius_m = radius_cm * cfg["output"]["radius_cm_to_m"]

    h = float(cfg["boundary"]["h"])
    hm = float(cfg["boundary"]["hm"])
    digits = int(cfg["output"]["round_digits"])

    n_t, n_r = times.size, radius_m.size
    T_table = np.empty((n_t, n_r))
    C_table = np.empty((n_t, n_r))

    for j in range(n_t):
        tt = float(times[j])
        Trow = _interp_row(out.t, out.T, tt)
        Crow = _interp_row(out.t, out.C, tt)
        Tinf = out.environment.eval_T(tt)
        Cinf = out.environment.eval_C(tt)

        # 表面真实值：Robin 串联热阻外推
        n_cells = Trow.size
        dr = out.radius / n_cells
        k = float(cfg["properties_q1"]["k"])
        D_last = float(diff_coeff_q1(Crow[-1], to_kelvin(Trow[-1])))
        q_out = (Trow[-1] - Tinf) / (dr / (2.0 * k) + 1.0 / h)
        j_out = (Crow[-1] - Cinf) / (dr / (2.0 * D_last) + 1.0 / hm)
        Tsurf = Tinf + q_out / h
        Csurf = Cinf + j_out / hm

        radial_coord = np.concatenate(([0.0], out.r, [out.radius]))
        T_rad = np.concatenate(([Trow[0]], Trow, [Tsurf]))
        C_rad = np.concatenate(([Crow[0]], Crow, [Csurf]))
        T_table[j, :] = np.interp(radius_m, radial_coord, T_rad)
        C_table[j, :] = np.interp(radius_m, radial_coord, C_rad)

    return {
        "times_s": times,
        "radius_cm": radius_cm,
        "temperature_table": np.round(T_table, digits),
        "moisture_table": np.round(C_table, digits),
        "units": {
            "time": "s",
            "radius": "cm",
            "temperature": "degC",
            "moisture": "kg/kg (dry basis)",
            "round_digits": digits,
        },
    }


def _interp_row(t, field, tt):
    """在时间轴 t 上线性插值 field（(n_t, n_cells)）到时刻 tt，返回一行。"""
    return np.array([np.interp(tt, t, field[:, k])
                     for k in range(field.shape[1])])
