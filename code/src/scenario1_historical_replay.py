#!/usr/bin/env python3
"""Build a minimal Scenario-1 historical replay for one complete cast group.

This is an evidence-preserving replay from observed timestamps/crane labels, not
an optimized or safety-certified dispatch. It creates movement/schedule/metrics
artifacts and checks task coverage, crane ratings, time windows, handoffs and
same-crane temporal non-overlap. Shared-rail spatial separation/continuous
trajectories remain NOT ASSESSED until geometry, initial positions, speed and
safety-gap parameters are confirmed by the modeling lead.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from ortools.sat.python import cp_model

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / "question" / "B题：炼钢厂钢包周转与排产优化问题" / "附数据文件"
PLAN_FILE = INPUT_DIR / "出钢计划表单.xls"
OPS_FILE = INPUT_DIR / "工序时间信息.xlsx"
DEFAULT_CAST_ID = "6C0754"
CASTER_MAP = {"A": 1, "B": 2, "C": 3}


@lru_cache(maxsize=None)
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def txt(value: Any) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def dt(value: Any) -> datetime | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def normalized_ladle(value: Any) -> str:
    raw = txt(value)
    if not raw:
        return ""
    try:
        number = float(raw)
        if number.is_integer():
            return str(int(number))
    except ValueError:
        pass
    return raw.lstrip("0") or "0"


def unique_heat_row(frame: pd.DataFrame, heat_id: str) -> tuple[pd.Series | None, int | None, int]:
    hits = frame.loc[frame["熔炼号"].astype(str).str.strip().eq(heat_id)]
    if len(hits) != 1:
        return None, None, len(hits)
    row = hits.iloc[0]
    source_row = int(hits.index[0]) + 2
    return row, source_row, 1


def source_ref(name: str, sheet: str, row: int | None, path: Path) -> dict[str, Any]:
    return {"file": path.relative_to(ROOT).as_posix(), "sha256": sha256(path), "sheet": sheet, "excel_row": row}


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def cp_sat_fixed_replay(moves: list[dict[str, Any]], anchor: datetime) -> dict[str, Any]:
    model = cp_model.CpModel()
    by_crane: dict[str, list[Any]] = {}
    included = 0
    for move in moves:
        start_dt = dt(move.get("pickup_time"))
        end_dt = dt(move.get("dropoff_time"))
        crane = txt(move.get("crane_id"))
        if start_dt is None or end_dt is None or end_dt < start_dt or not crane:
            continue
        start_s = int((start_dt - anchor).total_seconds())
        end_s = int((end_dt - anchor).total_seconds())
        duration_s = end_s - start_s
        start = model.NewIntVar(start_s, start_s, f"start_{included}")
        end = model.NewIntVar(end_s, end_s, f"end_{included}")
        interval = model.NewIntervalVar(start, duration_s, end, f"move_{included}")
        by_crane.setdefault(crane, []).append(interval)
        included += 1
    for intervals in by_crane.values():
        model.AddNoOverlap(intervals)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    return {
        "solver": "OR-Tools CP-SAT fixed historical intervals",
        "status": solver.StatusName(status),
        "intervals_in_model": included,
        "cranes": sorted(by_crane),
        "scope": "same-crane temporal non-overlap only; no shared-rail spatial or trajectory constraints",
        "wall_time_s": solver.WallTime(),
    }


def run_replay(cast_id: str = DEFAULT_CAST_ID, output_root: Path | None = None) -> dict[str, Any]:
    output_root = (output_root or ROOT / "data" / "runs" / f"scene1_replay_{cast_id}").resolve()
    plan = pd.read_excel(PLAN_FILE, engine="xlrd")
    ops = pd.read_excel(OPS_FILE, engine="openpyxl")
    plan["熔炼号"] = plan["熔炼号"].astype("string").str.strip()
    group = plan.loc[
        plan["连连浇号"].astype(str).str.strip().eq(cast_id)
        & plan["配包flag"].ne(-2)
        & plan["熔炼号"].notna()
    ].copy()
    if group.empty:
        raise ValueError(f"cast group not found or contains no operational rows: {cast_id}")
    group["sequence_numeric"] = pd.to_numeric(group["浇次号"], errors="coerce")
    group = group.sort_values("sequence_numeric").reset_index(drop=False).rename(columns={"index": "source_plan_index"})
    heats = group["熔炼号"].astype(str).tolist()
    seqs = group["sequence_numeric"].dropna().astype(int).tolist()
    seq_contiguous = seqs == list(range(1, len(group) + 1))
    if not seq_contiguous:
        raise ValueError(f"cast group {cast_id} sequence is not 1..N: {seqs}")
    if group["连铸机号"].nunique(dropna=True) != 1:
        raise ValueError(f"cast group {cast_id} spans multiple planned casters")
    planned_caster = txt(group["连铸机号"].iloc[0])
    actual_caster_num = CASTER_MAP.get(planned_caster)
    if actual_caster_num is None:
        raise ValueError(f"unrecognized caster code {planned_caster}")
    if not group["精炼路径"].astype(str).eq("R0").all():
        raise ValueError("this minimal replay currently implements only all-R0 cast groups; no LF tasks are silently omitted")
    ops_group = ops.loc[ops["熔炼号"].astype(str).str.strip().isin(heats)]
    if len(ops_group) != len(group) or ops_group["熔炼号"].duplicated().any():
        raise ValueError(f"operations rows are not one-to-one for cast group {cast_id}")
    actual_ccm_numbers = pd.to_numeric(ops_group["连铸机设备号"], errors="coerce").dropna().astype(int).unique().tolist()
    if actual_ccm_numbers != [actual_caster_num]:
        raise ValueError(f"actual caster mapping mismatch: planned {planned_caster}->{actual_caster_num}, actual={actual_ccm_numbers}")

    # Read station tables only for source rows in the chosen cast group.
    converter_frames = {n: pd.read_excel(INPUT_DIR / f"{n}炉.xlsx", engine="openpyxl") for n in "ABC"}
    rh_frames = [pd.read_excel(INPUT_DIR / "1#RH.xlsx", engine="openpyxl"), pd.read_excel(INPUT_DIR / "2#RH.xlsx", engine="openpyxl")]
    caster_frame = pd.read_excel(INPUT_DIR / f"{actual_caster_num}#连铸.xlsx", engine="openpyxl")
    paths = [PLAN_FILE, OPS_FILE] + [INPUT_DIR / f"{n}炉.xlsx" for n in "ABC"] + [INPUT_DIR / "1#RH.xlsx", INPUT_DIR / "2#RH.xlsx", INPUT_DIR / f"{actual_caster_num}#连铸.xlsx"]
    input_hashes = {p.relative_to(ROOT).as_posix(): sha256(p) for p in sorted(set(paths))}

    moves: list[dict[str, Any]] = []
    ops_rows: list[dict[str, Any]] = []
    side_events: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    tier1_mismatches: list[str] = []
    marker_after_process: list[str] = []
    tier2_failures: list[dict[str, Any]] = []
    crane_mismatches: list[str] = []
    row_refs: dict[str, Any] = {}

    for _, pr in group.iterrows():
        heat = txt(pr["熔炼号"])
        plan_excel_row = int(pr["source_plan_index"]) + 2
        op, op_row, op_count = unique_heat_row(ops, heat)
        if op is None:
            raise ValueError(f"operations rows for {heat} are not unique: {op_count}")
        converter_code = txt(pr["转炉设备号"])
        converter, conv_row, conv_count = unique_heat_row(converter_frames[converter_code], heat)
        if converter is None:
            raise ValueError(f"converter rows for {heat} are not unique/present in {converter_code}炉.xlsx: {conv_count}")
        rh_hits = [(f, *unique_heat_row(f, heat)) for f in rh_frames]
        rh_hits = [x for x in rh_hits if x[2] is not None]
        if len(rh_hits) != 1:
            raise ValueError(f"RH source row is not unique for {heat}: {len(rh_hits)} hits")
        rh_frame, rh_row_data, rh_row, rh_count = rh_hits[0]
        caster, caster_row, caster_count = unique_heat_row(caster_frame, heat)
        if caster is None:
            raise ValueError(f"caster source row for {heat} not unique/present: {caster_count}")
        rh_file = INPUT_DIR / ("1#RH.xlsx" if rh_frame is rh_frames[0] else "2#RH.xlsx")
        rh_sheet_name = "RH精炼位1信息数据" if rh_file.name == "1#RH.xlsx" else "RH精炼位2信息数据"
        caster_file = INPUT_DIR / f"{actual_caster_num}#连铸.xlsx"
        plan_source = source_ref(PLAN_FILE.name, "steel_tapping_plan_info(2)", plan_excel_row, PLAN_FILE)
        ops_source = source_ref(OPS_FILE.name, "工序信息数据", op_row, OPS_FILE)
        converter_source = source_ref(f"{converter_code}炉.xlsx", f"转炉{converter_code}信息数据", conv_row, INPUT_DIR / f"{converter_code}炉.xlsx")
        rh_source = source_ref(rh_file.name, rh_sheet_name, rh_row, rh_file)
        caster_source = source_ref(caster_file.name, f"连铸{planned_caster}信息数据", caster_row, caster_file)
        row_refs[heat] = {"plan": plan_source, "operations": ops_source, "converter": converter_source, "rh": rh_source, "caster": caster_source}

        # Stage-level Tier-2 evidence. The proposal's 90-minute / 3-ton values
        # are provisional until the modeling lead confirms them.
        b_ladle = normalized_ladle(converter.get("钢水罐号"))
        rh_ladle = normalized_ladle(rh_row_data.get("钢包号"))
        tap_end = dt(converter.get("出钢结束时间"))
        rh_accept = dt(rh_row_data.get("重包到RH接受位时间"))
        b_weight = num(converter.get("钢水罐出钢重量"))
        rh_in_weight = num(rh_row_data.get("进入重量"))
        weight_diff = abs(b_weight - rh_in_weight) if b_weight is not None and rh_in_weight is not None else None
        tap_to_rh_min = (rh_accept - tap_end).total_seconds() / 60 if tap_end and rh_accept else None
        tier2 = {
            "same_heat_id": True,
            "ladle_number_match": bool(b_ladle and rh_ladle and b_ladle == rh_ladle),
            "tap_end_to_rh_accept_monotone": tap_to_rh_min is not None and tap_to_rh_min >= 0,
            "tap_end_to_rh_accept_le_90min_provisional": tap_to_rh_min is not None and 0 <= tap_to_rh_min <= 90,
            "weight_diff_le_3t_provisional": weight_diff is not None and weight_diff <= 3.0,
        }
        tier2_ok = all(tier2.values())
        if not tier2_ok:
            tier2_failures.append({"heat_id": heat, "criteria": tier2, "minutes": tap_to_rh_min, "weight_diff_t": weight_diff})

        rh_temp_id, ccm_temp_id = txt(rh_row_data.get("临时罐号")), txt(caster.get("临时罐号"))
        tier1_ok = bool(rh_temp_id and rh_temp_id == ccm_temp_id)
        if not tier1_ok:
            tier1_mismatches.append(heat)
        process_start = dt(rh_row_data.get("进处理位时间"))
        if rh_accept and process_start and rh_accept > process_start:
            marker_after_process.append(heat)

        b_crane = txt(converter.get("炉后吊包天车"))
        rh_place_crane = txt(rh_row_data.get("放包天车"))
        rh_lift_crane = txt(rh_row_data.get("吊包天车"))
        ccm_place_crane = txt(caster.get("放包天车"))
        ccm_empty_crane = txt(caster.get("吊包天车"))
        crane1_ok = bool(b_crane and b_crane == rh_place_crane)
        crane2_ok = bool(rh_lift_crane and rh_lift_crane == ccm_place_crane)
        if not crane1_ok or not crane2_ok:
            crane_mismatches.append(heat)

        actual_fields = {
            "tap_start": dt(converter.get("出钢开始时间")),
            "tap_end": tap_end,
            "bof_heavy_pick": dt(op.get("重罐吊起时间")),
            "rh_arrival_marker": rh_accept,
            "rh_process_start": process_start,
            "rh_process_end": dt(rh_row_data.get("出处理位时间")),
            "rh_heavy_lift": dt(rh_row_data.get("重包从RH吊起时间")),
            "ccm_seat": dt(caster.get("重包到铸机接受位座包时间")),
            "ccm_cast_start": dt(op.get("开浇时间")),
            "ccm_cast_end": dt(op.get("停浇时间")),
            "ccm_empty_lift": dt(caster.get("空包在铸机接受位吊起时间")),
            "empty_transfer_drop": dt(caster.get("空罐放下过渡平车时间")),
        }
        missing_time_names = [name for name, value in actual_fields.items() if value is None]
        if missing_time_names:
            raise ValueError(f"cast group {cast_id} has incomplete core times for {heat}: {missing_time_names}")

        task_specs = [
            {
                "task_suffix": "BOF_RH",
                "task_type": "heavy",
                "route_step": "BOF_to_RH",
                "origin": f"BOF-{converter_code}",
                "destination": "RH",
                "release_time": actual_fields["tap_end"],
                "pickup_time": actual_fields["bof_heavy_pick"],
                "dropoff_time": actual_fields["rh_arrival_marker"],
                "deadline_time": actual_fields["rh_process_start"],
                "crane_id": b_crane,
                "load_t": b_weight,
                "weight_limit_t": 450.0,
                "source_refs": {"release": converter_source, "pickup": ops_source, "dropoff": rh_source, "deadline": rh_source},
                "handoff_match": crane1_ok,
            },
            {
                "task_suffix": "RH_CCM",
                "task_type": "heavy",
                "route_step": "RH_to_CCM",
                "origin": "RH",
                "destination": f"CCM-{planned_caster}",
                "release_time": actual_fields["rh_process_end"],
                "pickup_time": actual_fields["rh_heavy_lift"],
                "dropoff_time": actual_fields["ccm_seat"],
                "deadline_time": actual_fields["ccm_cast_start"],
                "crane_id": rh_lift_crane,
                "load_t": num(rh_row_data.get("RH起吊重包重量")) or num(caster.get("铸机接受位重包放下重量")),
                "weight_limit_t": 450.0,
                "source_refs": {"release": rh_source, "pickup": rh_source, "dropoff": caster_source, "deadline": ops_source},
                "handoff_match": crane2_ok,
            },
            {
                "task_suffix": "CCM_EMPTY_RETURN",
                "task_type": "empty",
                "route_step": "CCM_empty_to_transfer_car",
                "origin": f"CCM-{planned_caster}",
                "destination": "transfer_car",
                "release_time": actual_fields["ccm_empty_lift"],
                "pickup_time": actual_fields["ccm_empty_lift"],
                "dropoff_time": actual_fields["empty_transfer_drop"],
                "deadline_time": actual_fields["empty_transfer_drop"],
                "crane_id": ccm_empty_crane,
                "load_t": num(caster.get("铸机接受位空包吊起重量")),
                "weight_limit_t": 450.0,
                "source_refs": {"pickup": caster_source, "dropoff": caster_source},
                "handoff_match": True,
            },
        ]
        plan_cast_start = dt(pr.get("浇铸开始时间"))
        plan_cast_end = dt(pr.get("浇铸结束时间"))
        actual_cast_start, actual_cast_end = actual_fields["ccm_cast_start"], actual_fields["ccm_cast_end"]
        ops_rows.append({
            "run_id": f"scene1-replay-{cast_id}",
            "cast_id": cast_id,
            "heat_id": heat,
            "sequence": int(pr["sequence_numeric"]),
            "route": txt(pr.get("精炼路径")),
            "converter": converter_code,
            "plan_caster_code": planned_caster,
            "actual_caster_number": actual_caster_num,
            "planned_cast_start": plan_cast_start.isoformat(sep=" ") if plan_cast_start else "",
            "actual_cast_start": actual_cast_start.isoformat(sep=" "),
            "planned_cast_end": plan_cast_end.isoformat(sep=" ") if plan_cast_end else "",
            "actual_cast_end": actual_cast_end.isoformat(sep=" "),
            "actual_rh_end": actual_fields["rh_process_end"].isoformat(sep=" "),
            "thermal_age_rh_end_to_cast_start_min": (actual_cast_start - actual_fields["rh_process_end"]).total_seconds() / 60,
            "cast_start_delay_vs_plan_min": (actual_cast_start - plan_cast_start).total_seconds() / 60 if plan_cast_start else None,
            "tier2_status_provisional": "pass" if tier2_ok else "review",
            "tier1_rh_ccm_temp_id_status": "pass" if tier1_ok else "mismatch_or_missing",
            "rh_accept_marker_vs_process_start_min": (rh_accept - process_start).total_seconds() / 60,
            "plan_source_row": plan_excel_row,
            "operations_source_row": op_row,
        })
        for spec in task_specs:
            task_id = f"{heat}:{spec['task_suffix']}"
            moves.append({
                "run_id": f"scene1-replay-{cast_id}",
                "scenario": "1_deterministic_historical_replay",
                "algorithm": "observed_crane_assignment_fixed_time_replay",
                "task_id": task_id,
                "heat_id": heat,
                "temp_ladle_id_rh": rh_temp_id,
                "temp_ladle_id_ccm": ccm_temp_id,
                "task_type": spec["task_type"],
                "route_step": spec["route_step"],
                "origin": spec["origin"],
                "destination": spec["destination"],
                "release_time": spec["release_time"].isoformat(sep=" "),
                "pickup_time": spec["pickup_time"].isoformat(sep=" "),
                "dropoff_time": spec["dropoff_time"].isoformat(sep=" "),
                "deadline_time": spec["deadline_time"].isoformat(sep=" "),
                "duration_min": (spec["dropoff_time"] - spec["pickup_time"]).total_seconds() / 60,
                "crane_id": spec["crane_id"],
                "load_t": spec["load_t"],
                "weight_limit_t": spec["weight_limit_t"],
                "handoff_crane_match": spec["handoff_match"],
                "deadline_status": "pass" if spec["dropoff_time"] <= spec["deadline_time"] else "late_marker_review",
                "source_refs_json": json.dumps(spec["source_refs"], ensure_ascii=False, sort_keys=True),
            })

        # Auxiliary slag/residual events are recorded as nested events. Their
        # resource semantics are not promoted into separate scheduled tasks here.
        for i in (1, 2):
            slag_start = dt(caster.get(f"翻渣开始时间{i}"))
            slag_end = dt(caster.get(f"翻渣结束时间{i}"))
            if slag_start or slag_end:
                side_events.append({
                    "run_id": f"scene1-replay-{cast_id}", "heat_id": heat, "event_type": "slag_operation",
                    "event_index": i, "start_time": slag_start.isoformat(sep=" ") if slag_start else "",
                    "end_time": slag_end.isoformat(sep=" ") if slag_end else "",
                    "equipment": txt(caster.get(f"翻渣设备号{i}")), "receiver_heat_id": "",
                    "source_ref_json": json.dumps(caster_source, ensure_ascii=False, sort_keys=True),
                    "scheduling_status": "recorded_as_nested_event_not_independent_move",
                })
            resid_start = dt(caster.get(f"铸余开始时间{i}"))
            resid_end = dt(caster.get(f"铸余结束时间{i}"))
            receiver = txt(caster.get(f"铸余接收罐子的熔炼号{i}"))
            if receiver or resid_start or resid_end:
                side_events.append({
                    "run_id": f"scene1-replay-{cast_id}", "heat_id": heat, "event_type": "residual_recovery",
                    "event_index": i, "start_time": resid_start.isoformat(sep=" ") if resid_start else "",
                    "end_time": resid_end.isoformat(sep=" ") if resid_end else "",
                    "equipment": txt(caster.get(f"铸余回收位置{i}")), "receiver_heat_id": receiver,
                    "source_ref_json": json.dumps(caster_source, ensure_ascii=False, sort_keys=True),
                    "scheduling_status": "recorded_as_nested_event_not_independent_move",
                })

    moves.sort(key=lambda r: (r["pickup_time"], r["task_id"]))
    earliest = min(dt(r["pickup_time"]) for r in moves)
    # Relative seconds aid reproducibility and downstream scheduling; keep absolute
    # timestamps in the same file for auditability.
    for move in moves:
        move["pickup_s"] = int((dt(move["pickup_time"]) - earliest).total_seconds())
        move["dropoff_s"] = int((dt(move["dropoff_time"]) - earliest).total_seconds())
        move["release_s"] = int((dt(move["release_time"]) - earliest).total_seconds())
        move["deadline_s"] = int((dt(move["deadline_time"]) - earliest).total_seconds())

    # Checks: the replay model has fixed observed times/assignments and only
    # checks temporal no-overlap per crane. Spatial shared-rail safety is outside
    # the available confirmed-parameter scope.
    violations: list[dict[str, Any]] = []
    def check(check_id: str, status: str, count: int | float | str, details: str) -> None:
        violations.append({"run_id": f"scene1-replay-{cast_id}", "check_id": check_id, "status": status, "count_or_value": count, "details": details})

    check("plan_sequence_1_to_n", "pass" if seqs == list(range(1, len(group) + 1)) else "fail", len(group), f"cast_id={cast_id}; sequence={seqs}")
    check("one_actual_caster_and_plan_mapping", "pass" if actual_caster_num == CASTER_MAP.get(planned_caster) else "fail", f"planned={planned_caster},actual={actual_caster_num}", "mapping A/B/C -> 1/2/3")
    check("primary_movement_task_coverage", "pass" if len(moves) == 3 * len(group) else "fail", f"{len(moves)}/{3*len(group)}", "three source-observed move legs per heat: BOF-RH, RH-CCM, CCM-empty-return")
    check("tier2_bof_rh_fallback", "pass" if not tier2_failures else "review", f"{len(group)-len(tier2_failures)}/{len(group)}", "90 min / 3 t thresholds are provisional from the proposal, pending WJ confirmation")
    check("tier1_rh_ccm_temp_id", "pass" if not tier1_mismatches else "review", f"{len(group)-len(tier1_mismatches)}/{len(group)}", f"mismatch heats={tier1_mismatches}")
    check("crane_handoff_labels", "pass" if not crane_mismatches else "review", f"{len(group)-len(crane_mismatches)}/{len(group)}", f"mismatch heats={crane_mismatches}")
    late = [r for r in moves if r["deadline_status"] != "pass"]
    check("source_marker_deadlines", "pass" if not late else "review", len(late), "source destination marker compared with process/cast anchor; review time-base discrepancies")
    capacity_bad = [r for r in moves if r["load_t"] is None or r["load_t"] > r["weight_limit_t"]]
    check("observed_load_within_assigned_450t_rating", "pass" if not capacity_bad else "fail", len(capacity_bad), "recorded movement crane capacity check only; no design safety factor applied")

    # Detect source-timestamp overlaps per observed crane for all movement intervals.
    overlap_pairs: list[tuple[str, str, str]] = []
    per_crane: dict[str, list[dict[str, Any]]] = {}
    for move in moves:
        per_crane.setdefault(move["crane_id"], []).append(move)
    for crane, records in per_crane.items():
        records.sort(key=lambda x: x["pickup_time"])
        for left, right in zip(records, records[1:]):
            if dt(right["pickup_time"]) < dt(left["dropoff_time"]):
                overlap_pairs.append((crane, left["task_id"], right["task_id"]))
    check("same_crane_temporal_nonoverlap", "pass" if not overlap_pairs else "review", len(overlap_pairs), json.dumps(overlap_pairs, ensure_ascii=False))
    check("shared_rail_spatial_safety", "not_assessed", "", "confirmed position map, initial positions, safety gaps and speed/acceleration parameters are not yet signed off")
    check("continuous_trajectory_validation", "not_assessed", "", "this replay uses source event windows; it does not invent interpolated physical trajectories")
    check("thermal_window_threshold", "not_assessed", "", "per-heat RH-end-to-cast-start ages are reported; official per-heat threshold remains unconfirmed")

    # CP-SAT checks whether the fixed historical intervals satisfy per-crane
    # no-overlap; it does not reschedule or optimize assignments.
    model = cp_model.CpModel()
    intervals_by_crane: dict[str, list[Any]] = {}
    for i, move in enumerate(moves):
        s, e = move["pickup_s"], move["dropoff_s"]
        if e < s:
            continue
        start = model.NewIntVar(s, s, f"s_{i}")
        end = model.NewIntVar(e, e, f"e_{i}")
        interval = model.NewIntervalVar(start, e - s, end, f"i_{i}")
        intervals_by_crane.setdefault(move["crane_id"], []).append(interval)
    for intervals in intervals_by_crane.values():
        model.AddNoOverlap(intervals)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    solver.parameters.num_search_workers = 1
    solver_status = solver.StatusName(solver.Solve(model))
    check("cp_sat_fixed_replay_resource_model", "pass" if solver_status in {"OPTIMAL", "FEASIBLE"} else "fail", solver_status, "fixed historical intervals, observed crane assignments, same-crane NoOverlap only; no reoptimization")

    # Actual casting intervals and plan-vs-actual start differences.
    ordered_ops = sorted(ops_rows, key=lambda x: x["sequence"])
    cast_gaps = []
    for prev, current in zip(ordered_ops, ordered_ops[1:]):
        gap = (dt(current["actual_cast_start"]) - dt(prev["actual_cast_end"])).total_seconds() / 60
        cast_gaps.append({"from_heat": prev["heat_id"], "to_heat": current["heat_id"], "gap_min": gap})
    thermal_ages = [float(r["thermal_age_rh_end_to_cast_start_min"]) for r in ops_rows]
    crane_busy = {}
    for crane, records in per_crane.items():
        crane_busy[crane] = sum((dt(x["dropoff_time"]) - dt(x["pickup_time"])).total_seconds() / 60 for x in records)

    input_paths = [PLAN_FILE, OPS_FILE] + [INPUT_DIR / f"{n}炉.xlsx" for n in "ABC"] + [INPUT_DIR / "1#RH.xlsx", INPUT_DIR / "2#RH.xlsx"] + [INPUT_DIR / f"{actual_caster_num}#连铸.xlsx"]
    manifest = {
        "run_id": f"scene1-replay-{cast_id}",
        "cast_id": cast_id,
        "selection": {"filter": "valid non-placeholder plan rows; sequence 1..N; one plan/actual caster mapping; unique operations rows; complete core actual timestamps", "heat_ids": heats, "sequence": seqs},
        "sources": {p.relative_to(ROOT).as_posix(): sha256(p) for p in sorted(set(input_paths))},
        "mode": "observed historical replay with fixed actual crane assignments/times; not an optimization",
        "limitations": [
            "No spatial rail-separation/continuous trajectory certification: required parameters are pending modeler confirmation.",
            "One RH-vs-CCM temporary-ladle ID mismatch is flagged and not repaired.",
            "RH acceptance marker can appear after RH processing-entry timestamp; both raw fields are retained and deadline checks are review-only.",
            "90-minute/3-ton Tier-2 thresholds are provisional from the proposal.",
        ],
    }
    metrics = {
        "run_id": f"scene1-replay-{cast_id}",
        "scenario": "1_deterministic_historical_replay",
        "cast_id": cast_id,
        "heat_count": len(group),
        "heat_ids": heats,
        "planned_caster": planned_caster,
        "actual_caster_number": actual_caster_num,
        "route_counts": group["精炼路径"].value_counts().to_dict(),
        "primary_move_count": len(moves),
        "side_event_count": len(side_events),
        "cp_sat_fixed_replay_status": solver_status,
        "same_crane_overlap_pairs": len(overlap_pairs),
        "tier1_temp_id_mismatch_heat_ids": tier1_mismatches,
        "tier2_fallback_failure_heat_ids": [r["heat_id"] for r in tier2_failures],
        "rh_accept_marker_after_process_start_heat_ids": marker_after_process,
        "actual_cast_gap_minutes": cast_gaps,
        "thermal_age_rh_end_to_cast_start_minutes": {r["heat_id"]: r["thermal_age_rh_end_to_cast_start_min"] for r in ops_rows},
        "plan_to_actual_cast_start_delay_minutes": {r["heat_id"]: r["cast_start_delay_vs_plan_min"] for r in ops_rows},
        "crane_busy_minutes_by_observed_id": crane_busy,
        "weights_max_t": max((num(m["load_t"]) or 0) for m in moves),
        "spatial_safety": "not_assessed",
        "trajectory": "not_generated; no physical positions/speeds/safety gaps are confirmed",
    }

    output_root.mkdir(parents=True, exist_ok=True)
    move_cols = list(moves[0].keys())
    write_csv(output_root / "moves.csv", moves, move_cols)
    schedule_cols = ["run_id", "scenario", "algorithm", "task_id", "heat_id", "route_step", "crane_id", "release_time", "pickup_time", "dropoff_time", "deadline_time", "release_s", "pickup_s", "dropoff_s", "deadline_s", "duration_min", "load_t", "weight_limit_t", "deadline_status", "source_refs_json"]
    write_csv(output_root / "schedule.csv", moves, schedule_cols)
    write_csv(output_root / "operations.csv", ops_rows, list(ops_rows[0].keys()))
    side_cols = list(side_events[0].keys()) if side_events else ["run_id", "heat_id", "event_type", "event_index", "start_time", "end_time", "equipment", "receiver_heat_id", "source_ref_json", "scheduling_status"]
    write_csv(output_root / "side_events.csv", side_events, side_cols)
    write_csv(output_root / "violations.csv", violations, ["run_id", "check_id", "status", "count_or_value", "details"])
    with (output_root / "metrics.json").open("w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    with (output_root / "run_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return {"metrics": metrics, "manifest": manifest, "checks": violations, "output_dir": str(output_root)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cast-id", default=DEFAULT_CAST_ID)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    result = run_replay(args.cast_id, args.output_dir)
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print("output_dir:", result["output_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
