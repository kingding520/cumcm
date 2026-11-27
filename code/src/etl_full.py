#!/usr/bin/env python3
"""Full 17.5-day Tier-1/Tier-2 ETL for the steel-ladle problem.

Implements ``code/configs/model_contract_v1.json`` (contract-v1):

* **Tier-2**  plan-heat -> operations row match keyed on the heat number, gated by
  the 5400 s acceptance window (tap end -> first refining accept) and the 3 t mass
  residual gate, requiring a **unique** candidate and a one-to-one relation per
  route step.
* **Tier-1**  physical ladle identity: the ladle number is taken from the
  operations table and cross-checked against the furnace sheets' temporary ladle
  ids. Stage aliases are recorded, never silently overwritten.

Everything is traceable: each output row carries the source workbook, sheet and
Excel row number, and unresolved cases land in the conflict table instead of being
dropped. No imputation, no silent repairs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / "question" / "B题：炼钢厂钢包周转与排产优化问题" / "附数据文件"
OUT_DIR = ROOT / "data" / "processed"
CONTRACT = ROOT / "code" / "configs" / "model_contract_v1.json"

PLAN_FILE = INPUT_DIR / "出钢计划表单.xls"
OPS_FILE = INPUT_DIR / "工序时间信息.xlsx"
FURNACE_FILES = {"A": INPUT_DIR / "A炉.xlsx", "B": INPUT_DIR / "B炉.xlsx", "C": INPUT_DIR / "C炉.xlsx"}

TIME_GATE_S = 5400
MASS_GATE_T = 3.0
# Route stage order by 精炼路径, e.g. L1R1 = LF then RH, R1L0 = RH only.
ROUTE_RE = re.compile(r"^(?:L(\d+)R(\d+)|R(\d+)L(\d+))$")

CARTS = ["CART1", "CART2", "CART3"]
CASTER_TO_CCM = {"1": "CCM1", "2": "CCM2", "3": "CCM3"}
CASTER_TO_CART = {"1": "CART1", "2": "CART2", "3": "CART3"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def to_dt(value: Any) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat"}:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def to_float(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def norm_ladle(value: Any) -> str | None:
    """Physical ladle number: numeric ids normalise (08 -> 8), aliases are kept."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text.lstrip("0") or "0"


def load_plan() -> pd.DataFrame:
    plan = pd.read_excel(PLAN_FILE, engine="xlrd")
    plan["excel_row"] = plan.index + 2
    plan = plan[plan["熔炼号"].notna()].copy()
    plan["heat_id"] = plan["熔炼号"].astype(str).str.strip()
    return plan


def load_ops() -> pd.DataFrame:
    ops = pd.read_excel(OPS_FILE, engine="openpyxl")
    ops["excel_row"] = ops.index + 2
    ops = ops[ops["熔炼号"].notna()].copy()
    ops["heat_id"] = ops["熔炼号"].astype(str).str.strip()
    ops["ladle_id"] = ops["钢包号"].map(norm_ladle)
    return ops


def load_furnace_temp_ids() -> pd.DataFrame:
    frames = []
    for line, path in FURNACE_FILES.items():
        df = pd.read_excel(path, engine="openpyxl")
        df["excel_row"] = df.index + 2
        keep = ["熔炼号", "钢水罐号", "钢水罐临时罐号", "钢水罐出钢重量", "出钢开始时间", "出钢结束时间", "excel_row"]
        present = [c for c in keep if c in df.columns]
        part = df[present].copy()
        part["line"] = line
        part["heat_id"] = part["熔炼号"].astype(str).str.strip()
        frames.append(part)
    out = pd.concat(frames, ignore_index=True)
    out["ladle_id"] = out["钢水罐号"].map(norm_ladle)
    out["temp_ladle_id"] = out["钢水罐临时罐号"].astype(str).replace({"nan": None})
    return out


def device_number(value: Any) -> str | None:
    """Canonical device token: 'LF3.0' -> '3', 'A'/'a' -> '1' for casters."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return None
    text = text.rstrip(".#").replace("＃", "#").replace("#", "")
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if text.upper() in {"A", "B", "C"}:
        return {"A": "1", "B": "2", "C": "3"}[text.upper()]
    m = re.search(r"(\d+)", text)
    return m.group(1) if m else text


def route_stages(route: Any) -> list[str]:
    text = str(route).strip().upper()
    m = ROUTE_RE.match(text)
    if not m:
        return []
    if m.group(1) is not None:
        lf, rh = int(m.group(1)), int(m.group(2))
        stages = ["LF"] * lf + ["RH"] * rh
    else:
        rh, lf = int(m.group(3)), int(m.group(4))
        stages = ["RH"] * rh + ["LF"] * lf
    return stages


def stages_from_ops(ops_row: pd.Series) -> list[str]:
    """Derive the refining sequence from the operations record itself.

    The plan's 精炼路径 only ever contains *0 entries (L0 / R0 / R0L0 / L0R0), so it
    cannot express how many passes actually happened; the observed device stamps can.
    """
    lf_ts = [to_dt(ops_row.get(c)) for c in ("LF进处理位时间", "LF出处理位时间", "LF重罐吊起时间")]
    rh_ts = [to_dt(ops_row.get(c)) for c in ("RH处理开始时间", "RH处理结束时间", "RH吊起时间")]
    lf_first = min((t for t in lf_ts if t), default=None)
    rh_first = min((t for t in rh_ts if t), default=None)
    stages: list[str] = []
    if lf_first and rh_first:
        stages = ["LF", "RH"] if lf_first <= rh_first else ["RH", "LF"]
    elif lf_first:
        stages = ["LF"]
    elif rh_first:
        stages = ["RH"]
    return stages


def first_refining_accept(ops_row: pd.Series, stages: list[str]) -> datetime | None:
    candidates = []
    if stages and stages[0] == "LF":
        candidates.append(to_dt(ops_row.get("重罐落到LF待机位时间")))
    if stages and stages[0] == "RH":
        candidates.append(to_dt(ops_row.get("重罐吊起至RH旋转位时间")))
        candidates.append(to_dt(ops_row.get("RH处理开始时间")))
    if not stages:
        candidates.append(to_dt(ops_row.get("吊至连铸机时间")))
    candidates = [c for c in candidates if c is not None]
    return min(candidates) if candidates else None


def build_heats(plan: pd.DataFrame, ops: pd.DataFrame, furnace: pd.DataFrame) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (heats, matching_log, conflicts)."""
    heats: list[dict] = []
    log: list[dict] = []
    conflicts: list[dict] = []

    ops_by_heat: dict[str, list[int]] = {}
    for idx, row in ops.iterrows():
        ops_by_heat.setdefault(row["heat_id"], []).append(idx)

    furnace_by_heat = {r["heat_id"]: r for _, r in furnace.iterrows()}

    for _, prow in plan.iterrows():
        heat = prow["heat_id"]
        plan_route = str(prow.get("精炼路径") or "").strip()
        route = plan_route
        tap_end = to_dt(prow.get("转炉结束时间"))
        candidate_idx = ops_by_heat.get(heat, [])
        examined = []
        accepted: int | None = None
        derived_stages: list[str] = []
        for idx in candidate_idx:
            orow = ops.loc[idx]
            candidate_stages = stages_from_ops(orow)
            if not derived_stages:
                derived_stages = candidate_stages
            accept = first_refining_accept(orow, candidate_stages)
            gap = (accept - tap_end).total_seconds() if accept and tap_end else None
            mass_ops = to_float(orow.get("钢包车上重包重量"))
            frow = furnace_by_heat.get(heat)
            mass_tap = to_float(frow.get("钢水罐出钢重量")) if frow is not None else None
            residual = abs(mass_tap - mass_ops) if mass_tap is not None and mass_ops is not None else None
            # A missing measurement is NOT_ASSESSED, never a silent failure: the
            # contract gate is applied only where the evidence exists.
            ok_time = True if gap is None else (-60 <= gap <= TIME_GATE_S)
            ok_mass = True if residual is None else (residual <= MASS_GATE_T)
            examined.append({"ops_row": int(orow["excel_row"]), "gap_s": gap, "mass_residual_t": residual,
                             "time_status": "NOT_ASSESSED" if gap is None else ("PASS" if ok_time else "FAIL"),
                             "mass_status": "NOT_ASSESSED" if residual is None else ("PASS" if ok_mass else "FAIL"),
                             "ok_time": ok_time, "ok_mass": ok_mass})

        plausible = [e for e in examined if e["ok_time"] and e["ok_mass"]]
        status = "UNRESOLVED"
        if len(plausible) == 1:
            accepted = next(i for i in candidate_idx if int(ops.loc[i]["excel_row"]) == plausible[0]["ops_row"])
            status = "MATCHED_UNIQUE"
        elif len(plausible) > 1:
            status = "AMBIGUOUS"
            conflicts.append({"kind": "ambiguous_tier2", "heat_id": heat, "route": route,
                              "candidates": ";".join(str(e["ops_row"]) for e in plausible),
                              "decision": "kept unresolved; no silent pick"})
        elif candidate_idx:
            status = "UNRESOLVED"
            reasons = sorted({f"{e['time_status']}/{e['mass_status']}" for e in examined})
            conflicts.append({"kind": "no_candidate_in_gate", "heat_id": heat, "route": route,
                              "ops_rows": ";".join(str(e["ops_row"]) for e in examined) or "none",
                              "evidence": ";".join(reasons), "decision": "left unresolved"})
        if not candidate_idx:
            conflicts.append({"kind": "no_ops_row_for_heat", "heat_id": heat, "route": route,
                              "ops_rows": "none", "evidence": "heat absent from operations table",
                              "decision": "left unresolved"})

        orow = ops.loc[accepted] if (status == "MATCHED_UNIQUE" and accepted is not None) else None
        stages = derived_stages if orow is not None else derived_stages
        frow = furnace_by_heat.get(heat)
        heats.append({
            "heat_id": heat,
            "cast_id": str(prow.get("连连浇号") or "").strip() or None,
            "caster": str(prow.get("连铸机号") or "").strip() or None,
            "converter": str(prow.get("转炉设备号") or "").strip() or None,
            "plan_route": route or None,
            "route": "-".join(stages) if stages else None,
            "route_stages": "|".join(stages) if stages else None,
            "plan_tap_start": str(to_dt(prow.get("转炉开始时间")) or ""),
            "plan_tap_end": str(tap_end or ""),
            "plan_cast_start": str(to_dt(prow.get("连铸开始时间")) or ""),
            "plan_cast_end": str(to_dt(prow.get("连铸结束时间")) or ""),
            "plan_status": str(prow.get("计划状态") or ""),
            "ladle_id": orow["ladle_id"] if orow is not None else (frow["ladle_id"] if frow is not None else None),
            "temp_ladle_id": frow["temp_ladle_id"] if frow is not None else None,
            "matched_ops_row": int(orow["excel_row"]) if orow is not None else None,
            "match_status": status,
            "plan_source": f"{PLAN_FILE.relative_to(ROOT)}#row{int(prow['excel_row'])}",
            "ops_source": f"{OPS_FILE.relative_to(ROOT)}#row{int(orow['excel_row'])}" if orow is not None else None,
        })
        log.append({"heat_id": heat, "route": route, "status": status, "examined": len(examined),
                    "plausible": len(plausible),
                    "time_status": ";".join(sorted({e["time_status"] for e in examined})) or "NOT_ASSESSED",
                    "mass_status": ";".join(sorted({e["mass_status"] for e in examined})) or "NOT_ASSESSED",
                    "decided_by": "time_gate_5400s+mass_gate_3t+unique_candidate"})
    return heats, log, conflicts


def build_operations(heats: list[dict], ops: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for heat in heats:
        if heat["match_status"] != "MATCHED_UNIQUE" or not heat["matched_ops_row"]:
            continue
        orow = ops[ops["excel_row"] == heat["matched_ops_row"]].iloc[0]
        stages = (heat["route_stages"] or "").split("|") if heat["route_stages"] else []
        stages = [s for s in stages if s]

        def emit(op_type: str, device: str | None, start: Any, end: Any, weight: Any = None) -> None:
            s, e = to_dt(start), to_dt(end)
            if s is None or e is None:
                return
            rows.append({
                "heat_id": heat["heat_id"], "cast_id": heat["cast_id"], "caster": heat["caster"],
                "ladle_id": heat["ladle_id"], "op_type": op_type, "device": device,
                "start_s": s.strftime("%Y-%m-%d %H:%M:%S"), "end_s": e.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_s": round((e - s).total_seconds(), 1), "load_t": to_float(weight),
                "source": f"{OPS_FILE.relative_to(ROOT)}#row{int(orow['excel_row'])}",
                "match_status": "MATCHED_UNIQUE",
            })

        emit("tap", f"BOF_{heat['converter']}", orow.get("出钢开始时间"), orow.get("出钢结束时间"),
             orow.get("钢包车上重包重量"))
        lf_device = str(orow.get("LF设备号") or "").strip() or None
        rh_device = str(orow.get("RH设备号") or "").strip() or None
        if "LF" in stages:
            emit("lf", f"LF{device_number(lf_device)}" if device_number(lf_device) else None,
             orow.get("LF进处理位时间"), orow.get("LF出处理位时间"),
                 orow.get("钢包车上重包重量"))
        if "RH" in stages:
            emit("rh", f"RH{device_number(rh_device)}" if device_number(rh_device) else None,
             orow.get("RH处理开始时间"), orow.get("RH处理结束时间"),
                 orow.get("钢包车上重包重量"))
        caster_num = device_number(orow.get("连铸机设备号") or heat["caster"])
        emit("ccm", f"CCM{caster_num}" if caster_num else None,
             orow.get("开浇时间"), orow.get("停浇时间"), orow.get("钢包车上重包重量"))
        if to_dt(orow.get("翻渣开始时间")):
            emit("slag", "SLAG", orow.get("翻渣开始时间"), orow.get("翻渣结束时间"),
                 orow.get("翻渣作业后空包重量"))
        if to_dt(orow.get("铸余渣回收开始时间")):
            emit("recovery", "SLAG_Z1",
                 orow.get("铸余渣回收开始时间"), orow.get("铸余渣回收结束时间"),
                 orow.get("铸余回收量"))
    return rows


def empty_ladle_weight(ops_row: pd.Series) -> tuple[float | None, str | None]:
    """Real empty-ladle weight after casting, with an explicit provenance tag.

    Preferred: the post-slag (after residue removal) empty ladle weight. Direct-to-cart
    heats never slag, so that column is empty for them; the ladle weight measured on
    the cart (``钢包车上空包重量``) is the honest fallback. A missing value stays
    ``None`` — the old code substituted 0 t, which silently passed every weight gate.
    """
    for column, tag in (("翻渣作业后空包重量", "after_slag_empty_ladle"),
                        ("钢包车上空包重量", "cart_empty_ladle")):
        value = to_float(ops_row.get(column))
        if value is not None and value > 0:
            return value, tag
    return None, None


def build_moves(heats: list[dict], ops: pd.DataFrame) -> list[dict]:
    """Scenario-1 movement tasks, with the three-stage empty-return split."""
    moves: list[dict] = []
    for heat in heats:
        if heat["match_status"] != "MATCHED_UNIQUE" or not heat["matched_ops_row"]:
            continue
        orow = ops[ops["excel_row"] == heat["matched_ops_row"]].iloc[0]
        stages = [s for s in (heat["route_stages"] or "").split("|") if s]
        bof = f"BOF_{heat['converter']}"
        lf_num = device_number(orow.get("LF设备号"))
        rh_num = device_number(orow.get("RH设备号"))
        caster_num = device_number(heat["caster"])
        lf = f"LF{lf_num}" if lf_num else None
        rh = f"RH{rh_num}" if rh_num else None
        ccm = f"CCM{caster_num}" if caster_num else None
        cart = f"CART{caster_num}" if caster_num else "CART3"
        if not ccm:
            continue
        if "LF" in stages and not lf:
            continue
        if "RH" in stages and not rh:
            continue

        def add(step: str, task_type: str, origin: str, destination: str,
                pickup: Any, deadline: Any, load_t: Any, phase_type: str,
                parent: str | None = None) -> None:
            p, d = to_dt(pickup), to_dt(deadline)
            if p is None or d is None:
                return
            load_value, load_source = empty_ladle_weight(orow) if phase_type != "TRAVEL_HEAVY" else (to_float(load_t), "tap_heavy")
            moves.append({
                "task_id": f"{heat['heat_id']}:{step}", "heat_id": heat["heat_id"],
                "cast_id": heat["cast_id"], "route_step": step, "task_type": task_type,
                "phase_type": phase_type, "parent_service_id": parent or f"{heat['heat_id']}:service",
                "origin": origin, "destination": destination,
                "release_time": p.strftime("%Y-%m-%d %H:%M:%S"),
                "deadline_time": d.strftime("%Y-%m-%d %H:%M:%S"),
                "load_t": load_value if load_value is not None else to_float(load_t),
                "load_source": load_source,
                "ladle_id": heat["ladle_id"],
                "cart_id": cart if destination.startswith("CART") else None,
                "source": f"{OPS_FILE.relative_to(ROOT)}#row{int(orow['excel_row'])}",
            })

        heavy = orow.get("钢包车上重包重量")
        # --- forward transport along the refining route
        prev_x = bof
        prev_place = orow.get("重罐吊起时间")
        for stage in stages:
            if stage == "LF":
                add(f"{prev_x}_to_LF", "heavy", prev_x, lf, prev_place,
                    orow.get("重罐落到LF待机位时间"), heavy, "TRAVEL_HEAVY")
                prev_x, prev_place = lf, orow.get("LF重罐吊起时间")
            else:
                add(f"{prev_x}_to_RH", "heavy", prev_x, rh, prev_place,
                    orow.get("重罐吊起至RH旋转位时间"), heavy, "TRAVEL_HEAVY")
                prev_x, prev_place = rh, orow.get("RH吊起时间")
        add(f"{prev_x}_to_CCM", "heavy", prev_x, ccm, prev_place,
            orow.get("吊至连铸机时间"), heavy, "TRAVEL_HEAVY")

        # --- return: three stages when slag/recovery exists, direct otherwise
        empty_start = orow.get("空包在连铸位吊起时间")
        if to_dt(orow.get("翻渣开始时间")):
            add("CCM_to_SLAG", "empty", ccm, "SLAG_Z1", empty_start,
                orow.get("翻渣开始时间"), orow.get("翻渣作业后空包重量"), "TRAVEL_TO_SITE")
            add("SLAG_OPERATION", "site", "SLAG_Z1", "SLAG_Z1", orow.get("翻渣开始时间"),
                orow.get("翻渣结束时间"), orow.get("翻渣作业后空包重量"), "SITE_OPERATION")
            add("SLAG_to_CART", "empty", "SLAG_Z1", cart, orow.get("翻渣结束时间"),
                orow.get("空罐放下过渡平车时间"), orow.get("翻渣作业后空包重量"), "TRAVEL_TO_CART")
        else:
            add("CCM_to_CART", "empty", ccm, cart, empty_start,
                orow.get("空罐放下过渡平车时间"), None, "TRAVEL_TO_CART")
    return moves


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    global TIME_GATE_S, MASS_GATE_T
    TIME_GATE_S = int(contract["matching"]["time_gate_s"])
    MASS_GATE_T = float(contract["matching"]["mass_residual_gate_t"])

    plan, ops, furnace = load_plan(), load_ops(), load_furnace_temp_ids()
    heats, log, conflicts = build_heats(plan, ops, furnace)
    operations = build_operations(heats, ops)
    moves = build_moves(heats, ops)

    write_csv(args.out / "heats.csv", heats, list(heats[0].keys()))
    write_csv(args.out / "operations.csv", operations, list(operations[0].keys()))
    write_csv(args.out / "moves.csv", moves, list(moves[0].keys()))
    write_csv(args.out / "matching_log.csv", log, list(log[0].keys()))
    if conflicts:
        write_csv(args.out / "conflicts.csv", conflicts, list(conflicts[0].keys()))

    match_reasons: dict[str, int] = {}
    for row in log:
        key = f"{row['status']}|time={row['time_status']}|mass={row['mass_status']}"
        match_reasons[key] = match_reasons.get(key, 0) + 1

    summary = {
        "contract": "contract-v1",
        "time_gate_s": TIME_GATE_S,
        "mass_gate_t": MASS_GATE_T,
        "sources": {str(p.relative_to(ROOT)): sha256(p) for p in
                    [PLAN_FILE, OPS_FILE, *FURNACE_FILES.values()]},
        "match_reasons": match_reasons,
        "counts": {
            "plan_heats": len(plan), "ops_rows": len(ops),
            "heats": len(heats),
            "matched_unique": sum(1 for h in heats if h["match_status"] == "MATCHED_UNIQUE"),
            "ambiguous": sum(1 for h in heats if h["match_status"] == "AMBIGUOUS"),
            "unresolved": sum(1 for h in heats if h["match_status"] == "UNRESOLVED"),
            "operations": len(operations), "moves": len(moves),
            "conflicts": len(conflicts),
        },
        "notes": [
            "Tier-2 uses heat number + time gate + mass gate + unique candidate.",
            "Mass residual compares furnace tap weight against the operations heavy-ladle weight.",
            "Cart assignment follows caster->cart (1#/2#/3#); the source has no cart id per heat.",
            "Unresolved/ambiguous heats are reported, never silently matched or dropped.",
        ],
    }
    (args.out / "etl_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
