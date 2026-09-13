# -*- coding: utf-8 -*-
"""config_q1.py — 问题 1 的唯一建模配置（Python 版）。

对应原 MATLAB 工程 make_config.m 中问题 1 的部分：几何、初始条件、
边界系数、附录 2 物性、输出要求与数值设置。所有参数只在本文件维护，
求解器内部不得硬编码本文件已有参数。

约定（见 公用/模型方程与边界符号_三人走读.md）：
- 程序内部长度用 m、时间用 s，温度状态保存为 °C；
- 经验式 D(C,T_K) 只接受开尔文温度 T_K = T + 273.15；
- r 轴正方向由中心指向表面，表面向外热/质通量分别为
  q_out = -k dT/dr 与 j_out = -D dC/dr。
"""

from pathlib import Path

import numpy as np

# 工程根目录（本文件位于 <根>/代码/ 下）
ROOT = Path(__file__).resolve().parents[1]

CFG = {
    "paths": {
        "root": str(ROOT),
        "attachment1": str(ROOT / "附件" / "附件1.xlsx"),
    },
    "units": {
        "length": "m",
        "time": "s",
        "temperature_state": "degC",
        "temperature_in_arrhenius": "K",
        "moisture": "kg/kg (dry basis)",
        "celsius_to_kelvin": 273.15,
    },
    "geometry": {
        "length": 0.25,   # m
        "radius0": 0.02,  # m
    },
    "initial": {
        "T": 28.0,   # °C
        "C": 2.55,   # kg/kg 干基
    },
    "boundary": {
        "h": 25.0,    # W/(m^2 K)
        "hm": 8e-7,   # m/s
        "interpolation": "linear",
        "last_observed_time": 14400.0,  # s，附件 1 末观测时刻
        "post_observation": {
            "mode": "nominal_setpoint",  # 主方案：t>14400 s 固定设定值
            "Tinf": 50.0,   # °C
            "Cinf": 0.05,   # kg/kg
            "alternatives": ["last_observation", "last_30min_mean"],
        },
    },
    "properties_q1": {  # 附录 2
        "rho": 820.0,   # kg/m^3
        "cp": 2600.0,   # J/(kg K)
        "k": 0.36,      # W/(m K)
    },
    "output": {
        "table1_times_s": [100, 300, 600, 900, 1200, 1500, 1800],
        "table_radius_cm": [0.0, 0.5, 1.0, 1.5, 2.0],
        "radius_cm_to_m": 1e-2,
        "round_digits": 4,
    },
    "numerics": {
        "active_cells": 160,
        "convergence_cells": [80, 160, 320],
        "max_step": 10.0,
        "dt": 2.0,           # θ-法固定步长 (s)
        "theta": 0.5,        # 0.5=Crank–Nicolson（二阶）；1.0=隐式欧拉
        "picard_tol": 1e-12, # 水分方程 Picard 迭代绝对容差
        "picard_max_iter": 100,
    },
    "validation": {
        "temperature_grid_tolerance": 5e-4,  # °C
        "moisture_grid_tolerance": 5e-5,     # kg/kg
    },
}


def to_kelvin(t_degc):
    """摄氏温度 → 开尔文。Arrhenius 型经验式只接受 K。"""
    return np.asarray(t_degc, dtype=float) + CFG["units"]["celsius_to_kelvin"]


def diff_coeff_q1(C, T_kelvin=None):
    """附录 2 水分浓度扩散系数 D(C) [m^2/s]。

    D = 7e-9 * exp(-0.89 / C)。

    问题 1 的 D 与温度无关，但调用约定与问题 2-4 统一：
    T_kelvin 必须传开尔文值（本式不使用）。C 取 max(C,1e-12) 防除零。
    """
    C = np.maximum(np.asarray(C, dtype=float), 1e-12)
    return 7e-9 * np.exp(-0.89 / C)
