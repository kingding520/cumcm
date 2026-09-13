# -*- coding: utf-8 -*-
"""load_inputs.py — 读取附件 1，生成 T∞(t)、C∞(t) 的分段线性插值。

规则（公用/模型方程与边界符号_三人走读.md 第 4 节）：
1. 附件 1 从 0 到 14400 s、每 60 s 记录环境数据；正式主方案采用
   分段线性插值（np.interp），不做全局高阶拟合。
2. t > 14400 s 的边界延拓由 cfg["boundary"]["post_observation"] 控制：
   - nominal_setpoint : 固定 (50 °C, 0.05 kg/kg)（主方案）
   - last_observation : 保持附件 1 最后一个观测点
   - last_30min_mean  : 保持末 30 min 的均值
3. 读取 xlsx 仅用标准库（zipfile + XML），不依赖 pandas/openpyxl。

用法:
    from config_q1 import CFG
    env = load_inputs(CFG)              # 主方案延拓
    env = load_inputs(CFG, extension_mode="last_observation")
    env.eval_T(600.0)                   # T∞(600 s)
    env.eval_C([100.0, 200.0])          # 向量求值
"""

import re
import zipfile
from xml.etree import ElementTree as ET

import numpy as np

from config_q1 import CFG

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class Environment:
    """分段线性环境边界。

    参数
    ----
    t, Tinf, Cinf : 一维数组，节点时间 (s)、烘房温度 (°C)、烘房水分 (kg/kg)。
    t_end : 观测数据结束时间 (s)，默认取 t[-1]。
    post_Tinf, post_Cinf : nominal_setpoint 模式下的延拓设定值。
    mode : 延拓模式，见模块 docstring。
    """

    def __init__(self, t, Tinf, Cinf, t_end=None,
                 post_Tinf=None, post_Cinf=None, mode="last_observation"):
        t = np.asarray(t, dtype=float)
        Tinf = np.asarray(Tinf, dtype=float)
        Cinf = np.asarray(Cinf, dtype=float)
        assert t.ndim == 1 and t.size >= 2, "环境时间必须为一维且至少 2 个节点。"
        assert t[0] == 0.0 and np.all(np.diff(t) > 0), \
            "环境时间必须从 0 开始且严格递增。"
        assert Tinf.size == Cinf.size == t.size, \
            "环境时间、Tinf、Cinf 长度必须相同。"
        assert np.all(np.isfinite(t)) and np.all(np.isfinite(Tinf)) \
            and np.all(np.isfinite(Cinf)), "环境数据不得包含 NaN/Inf。"

        self.t = t
        self.Tinf = Tinf
        self.Cinf = Cinf
        self.t_end = float(t_end) if t_end is not None else float(t[-1])
        assert self.t_end >= t[0], "t_end 不得小于首节点。"
        self.mode = mode

        if mode == "last_observation":
            self.post_Tinf = float(Tinf[-1])
            self.post_Cinf = float(Cinf[-1])
        elif mode == "last_30min_mean":
            mask = t >= self.t_end - 1800.0
            assert np.any(mask), "末 30 min 无数据节点。"
            self.post_Tinf = float(np.mean(Tinf[mask]))
            self.post_Cinf = float(np.mean(Cinf[mask]))
        else:
            assert mode == "nominal_setpoint", f"未知延拓模式: {mode!r}"
            assert post_Tinf is not None and post_Cinf is not None, \
                "nominal_setpoint 模式必须提供 post_Tinf/post_Cinf。"
            self.post_Tinf = float(post_Tinf)
            self.post_Cinf = float(post_Cinf)

    def eval_T(self, tt):
        """T∞(t)，°C。标量/向量皆可。"""
        return self._eval(tt, self.Tinf, self.post_Tinf)

    def eval_C(self, tt):
        """C∞(t)，kg/kg。标量/向量皆可。"""
        return self._eval(tt, self.Cinf, self.post_Cinf)

    def _eval(self, tt, values, post_value):
        scalar = np.ndim(tt) == 0
        tq = np.atleast_1d(np.asarray(tt, dtype=float))
        v = np.interp(tq, self.t, values)          # 节点间分段线性
        v[tq > self.t_end] = post_value            # t > 末观测：延拓
        v[tq < self.t[0]] = values[0]              # t < 0：取首节点
        return float(v[0]) if scalar else v


def load_inputs(cfg=None, extension_mode=None):
    """读取附件 1 并返回 Environment（分段线性插值 + 延拓）。

    cfg : 配置字典，默认 config_q1.CFG。
    extension_mode : 覆盖 cfg 的延拓模式
        ('nominal_setpoint' | 'last_observation' | 'last_30min_mean')。
    """
    cfg = CFG if cfg is None else cfg
    path = cfg["paths"]["attachment1"]
    rows = _read_xlsx_first_sheet(path)
    assert rows, f"附件 1 为空或无法解析: {path}"

    header = rows[0]
    t_list, tinf_list, cinf_list = [], [], []
    for row in rows[1:]:
        if "A" not in row or row["A"] == "":
            continue  # 跳过空行
        t_list.append(float(row["A"]))
        tinf_list.append(float(row["B"]))
        cinf_list.append(float(row["C"]))
    assert t_list, f"附件 1 无数据行: {path}"
    t = np.asarray(t_list, dtype=float)
    Tinf = np.asarray(tinf_list, dtype=float)
    Cinf = np.asarray(cinf_list, dtype=float)

    # 校验附件格式：0 起、60 s 等间隔、有限值
    assert t[0] == 0.0 and np.all(np.diff(t) > 0), "附件 1 时间列异常。"
    assert np.allclose(np.diff(t), 60.0), "附件 1 采样间隔应为 60 s。"
    assert np.all(np.isfinite(t)) and np.all(np.isfinite(Tinf)) \
        and np.all(np.isfinite(Cinf)), "附件 1 数据含 NaN/Inf。"

    po = cfg["boundary"]["post_observation"]
    mode = extension_mode if extension_mode is not None else po["mode"]
    return Environment(
        t, Tinf, Cinf,
        t_end=cfg["boundary"]["last_observed_time"],
        post_Tinf=po["Tinf"], post_Cinf=po["Cinf"], mode=mode,
    )


def _read_xlsx_first_sheet(path):
    """读取 xlsx 第一个工作表为 list[dict{列字母: 值}]。

    仅使用标准库。支持共享字符串（t="s"）与数值单元格。
    """
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        sheets = sorted(n for n in names
                        if n.startswith("xl/worksheets/") and n.endswith(".xml"))
        if not sheets:
            raise ValueError(f"{path} 中没有工作表。")
        xml = z.read(sheets[0]).decode("utf-8")

        shared = []
        if "xl/sharedStrings.xml" in names:
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(si.itertext()) for si in sroot.iter(_NS + "si")]

        rows = []
        for row_m in re.finditer(r"<row[^>]*>(.*?)</row>", xml, re.S):
            cells = {}
            for cell_m in re.finditer(
                    r'<c r="([A-Z]+)\d+"([^>]*)>(?:<v>(.*?)</v>)?',
                    row_m.group(1)):
                ref, attrs, val = cell_m.groups()
                ttype = re.search(r't="([^"]*)"', attrs)
                ttype = ttype.group(1) if ttype else None
                if ttype == "s":
                    cells[ref] = shared[int(val)]
                elif val is not None:
                    cells[ref] = val
                else:
                    cells[ref] = ""
            if cells:
                rows.append(cells)
        return rows
