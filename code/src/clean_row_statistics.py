#!/usr/bin/env python3
"""Build transparent descriptive statistics from clean, complete source rows.

No source values are repaired. The plan cohort excludes placeholder rows and
requires unique heat IDs plus complete plan anchors. Actual process-duration
statistics use one non-duplicated operations row per heat and metric-specific
complete, nonnegative timestamp pairs; sample sizes are reported per metric.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / "question" / "B题：炼钢厂钢包周转与排产优化问题" / "附数据文件"
OUTPUT_DIR = ROOT / "data" / "audit"
PLAN_FILE = INPUT_DIR / "出钢计划表单.xls"
OPS_FILE = INPUT_DIR / "工序时间信息.xlsx"
CASTER_MAP = {"A": 1, "B": 2, "C": 3}

PLAN_REQUIRED = [
    "熔炼号", "连连浇号", "浇次号", "连铸机号", "转炉设备号", "精炼路径",
    "转炉开始时间", "转炉结束时间", "浇铸开始时间", "浇铸结束时间",
]
CORE_OPS_FIELDS = [
    "出钢开始时间", "出钢结束时间", "开浇时间", "停浇时间",
    "空包在连铸位吊起时间", "空罐放下过渡平车时间",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_time(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def summarize_duration(
    frame: pd.DataFrame,
    metric: str,
    start_field: str,
    end_field: str,
    mask: pd.Series,
    source_hash: str,
    route: str = "ALL",
    unit: str = "minutes",
) -> dict[str, Any]:
    start = parse_time(frame[start_field])
    end = parse_time(frame[end_field])
    pair_present = mask & start.notna() & end.notna()
    diff = (end[pair_present] - start[pair_present]).dt.total_seconds() / 60.0
    valid = diff[diff >= 0]
    stats = {
        "metric": metric,
        "route": route,
        "start_field": start_field,
        "end_field": end_field,
        "unit": unit,
        "source_sha256": source_hash,
        "eligible_heat_rows": int(mask.sum()),
        "complete_pair_rows": int(pair_present.sum()),
        "negative_pair_rows": int((diff < 0).sum()),
        "clean_nonnegative_rows": int(len(valid)),
    }
    if len(valid):
        stats.update({
            "mean": float(valid.mean()),
            "std_sample": float(valid.std(ddof=1)) if len(valid) > 1 else 0.0,
            "p10": float(valid.quantile(0.10)),
            "median": float(valid.median()),
            "p90": float(valid.quantile(0.90)),
            "min": float(valid.min()),
            "max": float(valid.max()),
        })
    else:
        stats.update({k: None for k in ("mean", "std_sample", "p10", "median", "p90", "min", "max")})
    return stats


def duration_outlier_candidates(
    frame: pd.DataFrame,
    metric: str,
    start_field: str,
    end_field: str,
    mask: pd.Series,
    source_hash: str,
    source_file: str,
    route: str = "ALL",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start, end = parse_time(frame[start_field]), parse_time(frame[end_field])
    present = mask & start.notna() & end.notna()
    minutes = (end[present] - start[present]).dt.total_seconds() / 60.0
    valid = minutes[minutes >= 0]
    if len(valid) < 4:
        return {"metric": metric, "route": route, "method": "Tukey 1.5*IQR review flag only", "n": len(valid), "lower": None, "upper": None}, []
    q1, q3 = float(valid.quantile(.25)), float(valid.quantile(.75))
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    finding_rows: list[dict[str, Any]] = []
    for idx, value in minutes.items():
        if value < 0 or (lower <= value <= upper):
            continue
        row = frame.loc[idx]
        finding_rows.append({
            "source_file": source_file,
            "source_sha256": source_hash,
            "heat_id": str(row.get("熔炼号", "")),
            "continuous_cast_id": str(row.get("连连浇号", "")),
            "route": route,
            "metric": metric,
            "start_field": start_field,
            "start_time": start.loc[idx].isoformat(sep=" "),
            "end_field": end_field,
            "end_time": end.loc[idx].isoformat(sep=" "),
            "duration_minutes": float(value),
            "tukey_lower_review_bound": lower,
            "tukey_upper_review_bound": upper,
            "review_status": "statistical_outlier_candidate_not_removed",
        })
    return {"metric": metric, "route": route, "method": "Tukey 1.5*IQR review flag only", "n": len(valid), "lower": lower, "upper": upper, "candidate_count": len(finding_rows)}, finding_rows


def prepare_cohorts(plan: pd.DataFrame, ops: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    required_plan_missing = sorted(set(PLAN_REQUIRED).difference(plan.columns))
    if required_plan_missing:
        raise ValueError(f"plan table missing required fields: {required_plan_missing}")
    required_ops_missing = sorted(set(CORE_OPS_FIELDS + ["熔炼号"]).difference(ops.columns))
    if required_ops_missing:
        raise ValueError(f"operations table missing required fields: {required_ops_missing}")

    p = plan.copy()
    p["plan_excel_row"] = p.index + 2
    p["熔炼号"] = p["熔炼号"].astype("string").str.strip()
    p["is_placeholder"] = p["配包flag"].eq(-2)
    operational = p.loc[~p["is_placeholder"] & p["熔炼号"].notna() & p["熔炼号"].ne("")].copy()
    plan_counts = operational["熔炼号"].value_counts(dropna=True)
    operational["plan_heat_unique"] = operational["熔炼号"].map(plan_counts).eq(1)
    operational["plan_fields_complete"] = operational[PLAN_REQUIRED].notna().all(axis=1)
    for field in ("转炉开始时间", "转炉结束时间", "浇铸开始时间", "浇铸结束时间"):
        operational[f"parsed_{field}"] = parse_time(operational[field])
    operational["plan_chronology_ok"] = (
        operational["parsed_转炉开始时间"].notna()
        & operational["parsed_转炉结束时间"].notna()
        & operational["parsed_转炉结束时间"].ge(operational["parsed_转炉开始时间"])
        & operational["parsed_浇铸开始时间"].notna()
        & operational["parsed_浇铸结束时间"].notna()
        & operational["parsed_浇铸结束时间"].ge(operational["parsed_浇铸开始时间"])
    )
    operational["clean_plan_row"] = operational["plan_heat_unique"] & operational["plan_fields_complete"] & operational["plan_chronology_ok"]

    o = ops.copy()
    o["ops_excel_row"] = o.index + 2
    o["熔炼号"] = o["熔炼号"].astype("string").str.strip()
    ops_counts = o["熔炼号"].value_counts(dropna=True)
    o["ops_heat_unique"] = o["熔炼号"].map(ops_counts).eq(1)
    unique_ops = o.loc[o["ops_heat_unique"] & o["熔炼号"].notna() & o["熔炼号"].ne("")].copy()
    merged = operational.merge(unique_ops, on="熔炼号", how="left", validate="one_to_one", suffixes=("_plan", "_ops"))
    merged["ops_row_present"] = merged["ops_excel_row"].notna()

    for field in CORE_OPS_FIELDS:
        merged[f"parsed_{field}"] = parse_time(merged[field])
    merged["core_actual_fields_complete"] = merged[[f"parsed_{f}" for f in CORE_OPS_FIELDS]].notna().all(axis=1)
    merged["core_actual_chronology_ok"] = (
        merged["parsed_出钢结束时间"].ge(merged["parsed_出钢开始时间"])
        & merged["parsed_停浇时间"].ge(merged["parsed_开浇时间"])
        & merged["parsed_空包在连铸位吊起时间"].ge(merged["parsed_停浇时间"])
        & merged["parsed_空罐放下过渡平车时间"].ge(merged["parsed_空包在连铸位吊起时间"])
    )
    routes = merged["精炼路径"].astype(str)
    uses_lf = routes.str.contains("L0", regex=False)
    uses_rh = routes.str.contains("R0", regex=False)
    merged["parsed_LF进处理位时间"] = parse_time(merged["LF进处理位时间"])
    merged["parsed_LF出处理位时间"] = parse_time(merged["LF出处理位时间"])
    merged["parsed_RH处理开始时间"] = parse_time(merged["RH处理开始时间"])
    merged["parsed_RH处理结束时间"] = parse_time(merged["RH处理结束时间"])
    merged["route_stage_complete"] = (
        (~uses_lf | (merged["parsed_LF进处理位时间"].notna() & merged["parsed_LF出处理位时间"].notna()))
        & (~uses_rh | (merged["parsed_RH处理开始时间"].notna() & merged["parsed_RH处理结束时间"].notna()))
    )
    merged["route_stage_chronology_ok"] = (
        (~uses_lf | merged["parsed_LF出处理位时间"].ge(merged["parsed_LF进处理位时间"]))
        & (~uses_rh | merged["parsed_RH处理结束时间"].ge(merged["parsed_RH处理开始时间"]))
    )
    merged["clean_core_history_row"] = (
        merged["clean_plan_row"] & merged["ops_row_present"] & merged["core_actual_fields_complete"]
        & merged["core_actual_chronology_ok"] & merged["route_stage_complete"] & merged["route_stage_chronology_ok"]
    )
    return operational, merged, {
        "raw_plan_rows": int(len(plan)),
        "placeholder_rows_flag_minus_2": int(p["is_placeholder"].sum()),
        "operational_plan_rows": int(len(operational)),
        "unique_plan_heat_rows": int(operational["plan_heat_unique"].sum()),
        "plan_complete_chronology_rows": int(operational["clean_plan_row"].sum()),
        "operations_rows": int(len(ops)),
        "unique_operations_heat_rows": int(o["ops_heat_unique"].sum()),
        "unique_operations_rows_joined_to_plan": int(merged["ops_row_present"].sum()),
        "clean_core_history_rows": int(merged["clean_core_history_row"].sum()),
        "duplicate_operations_heat_ids": int((ops_counts > 1).sum()),
    }


def cast_group_candidates(clean_plan: pd.DataFrame, merged: pd.DataFrame) -> list[dict[str, Any]]:
    complete_lookup = merged.set_index("熔炼号")["clean_core_history_row"].to_dict()
    actual_caster = merged.set_index("熔炼号")["连铸机设备号"].to_dict() if "连铸机设备号" in merged.columns else {}
    rows: list[dict[str, Any]] = []
    for group_id, group in clean_plan.groupby("连连浇号", dropna=False):
        sequence = pd.to_numeric(group["浇次号"], errors="coerce").dropna().astype(int).tolist()
        seq_sorted = sorted(sequence)
        sequence_contiguous = bool(seq_sorted) and len(seq_sorted) == len(group) and seq_sorted == list(range(1, len(group) + 1))
        plan_casters = sorted(group["连铸机号"].dropna().astype(str).unique().tolist())
        actual_casters = sorted({str(actual_caster.get(h)) for h in group["熔炼号"] if pd.notna(actual_caster.get(h))})
        planned_code = plan_casters[0] if len(plan_casters) == 1 else ""
        planned_num = CASTER_MAP.get(planned_code)
        mapping_ok = bool(planned_num is not None and len(actual_casters) == 1 and actual_casters[0] in {str(planned_num), f"{planned_num}.0"})
        heat_complete = all(bool(complete_lookup.get(h, False)) for h in group["熔炼号"])
        rows.append({
            "continuous_cast_id": str(group_id),
            "heat_count": len(group),
            "sequence_min": min(seq_sorted) if seq_sorted else "",
            "sequence_max": max(seq_sorted) if seq_sorted else "",
            "sequence_contiguous_1_to_n": sequence_contiguous,
            "planned_caster_codes": ";".join(plan_casters),
            "actual_caster_codes": ";".join(actual_casters),
            "one_planned_caster": len(plan_casters) == 1,
            "planned_actual_caster_mapping_ok": mapping_ok,
            "all_heats_clean_core_history": heat_complete,
            "eligible_complete_group": sequence_contiguous and len(plan_casters) == 1 and mapping_ok and heat_complete,
            "heat_ids": ";".join(group.sort_values("浇次号")["熔炼号"].astype(str)),
            "route_counts": json.dumps(group["精炼路径"].value_counts().to_dict(), ensure_ascii=False, sort_keys=True),
            "plan_start_min": group["parsed_转炉开始时间"].min().isoformat(sep=" "),
            "plan_cast_end_max": group["parsed_浇铸结束时间"].max().isoformat(sep=" "),
        })
    return sorted(rows, key=lambda r: (not r["eligible_complete_group"], r["heat_count"], r["continuous_cast_id"]))


def basic_stats(plan_path: Path = PLAN_FILE, ops_path: Path = OPS_FILE, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    plan, ops = pd.read_excel(plan_path, engine="xlrd"), pd.read_excel(ops_path, engine="openpyxl")
    clean_plan, merged, cohort = prepare_cohorts(plan, ops)
    plan_hash, ops_hash = sha256(plan_path), sha256(ops_path)

    # Statistics use complete, nonnegative timestamp pairs. Outliers are flagged
    # by a descriptive Tukey rule but kept in the primary summaries; no values are
    # removed without a modeling decision. Per-metric and per-route N are explicit.
    stats_rows: list[dict[str, Any]] = []
    outlier_thresholds: list[dict[str, Any]] = []
    outlier_rows: list[dict[str, Any]] = []

    def add_duration(frame: pd.DataFrame, metric: str, start_field: str, end_field: str,
                     mask: pd.Series, source_hash: str, source_file: str, route: str = "ALL") -> None:
        stats_rows.append(summarize_duration(frame, metric, start_field, end_field, mask, source_hash, route=route))
        bounds, candidates = duration_outlier_candidates(frame, metric, start_field, end_field, mask,
                                                          source_hash, source_file, route=route)
        outlier_thresholds.append(bounds)
        outlier_rows.extend(candidates)

    add_duration(clean_plan, "plan_converter_duration", "转炉开始时间", "转炉结束时间",
                 clean_plan["clean_plan_row"], plan_hash, plan_path.relative_to(ROOT).as_posix())
    add_duration(clean_plan, "plan_cast_duration", "浇铸开始时间", "浇铸结束时间",
                 clean_plan["clean_plan_row"], plan_hash, plan_path.relative_to(ROOT).as_posix())
    metric_specs = [
        ("actual_tapping_duration", "出钢开始时间", "出钢结束时间", None),
        ("actual_lf_process_duration", "LF进处理位时间", "LF出处理位时间", "L0"),
        ("actual_rh_process_duration", "RH处理开始时间", "RH处理结束时间", "R0"),
        ("actual_ccm_cast_duration", "开浇时间", "停浇时间", None),
    ]
    joined = merged.copy()
    routes = joined["精炼路径"].astype(str)
    base_mask = joined["clean_plan_row"] & joined["ops_row_present"] & joined["ops_heat_unique"]
    ops_rel = ops_path.relative_to(ROOT).as_posix()
    for metric, start_field, end_field, route_token in metric_specs:
        mask = base_mask.copy()
        if route_token:
            mask &= routes.str.contains(route_token, regex=False)
        add_duration(joined, metric, start_field, end_field, mask, ops_hash, ops_rel)

    for route in sorted(clean_plan["精炼路径"].dropna().astype(str).unique()):
        plan_route_mask = clean_plan["clean_plan_row"] & clean_plan["精炼路径"].astype(str).eq(route)
        add_duration(clean_plan, "plan_converter_duration", "转炉开始时间", "转炉结束时间",
                     plan_route_mask, plan_hash, plan_path.relative_to(ROOT).as_posix(), route=route)
        add_duration(clean_plan, "plan_cast_duration", "浇铸开始时间", "浇铸结束时间",
                     plan_route_mask, plan_hash, plan_path.relative_to(ROOT).as_posix(), route=route)
        joined_route = base_mask & routes.eq(route)
        for metric, start_field, end_field, route_token in metric_specs:
            if route_token and route_token not in route:
                continue
            add_duration(joined, metric, start_field, end_field, joined_route, ops_hash, ops_rel, route=route)

    group_rows = cast_group_candidates(clean_plan, merged)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "basic_duration_stats.csv", stats_rows, list(stats_rows[0].keys()))
    outlier_columns = [
        "source_file", "source_sha256", "heat_id", "continuous_cast_id", "route", "metric", "start_field", "start_time",
        "end_field", "end_time", "duration_minutes", "tukey_lower_review_bound", "tukey_upper_review_bound", "review_status",
    ]
    write_csv(output_dir / "duration_outlier_candidates.csv", outlier_rows, outlier_columns)
    plan_rows = merged.copy()
    # Keep source row/hash and explicit inclusion flags; leave all source values intact.
    plan_rows.insert(0, "plan_source_file", plan_path.relative_to(ROOT).as_posix())
    plan_rows.insert(1, "plan_source_sha256", plan_hash)
    plan_rows.insert(2, "operations_source_file", ops_path.relative_to(ROOT).as_posix())
    plan_rows.insert(3, "operations_source_sha256", ops_hash)
    plan_rows["included_clean_plan"] = plan_rows["clean_plan_row"]
    plan_rows["complete_core_history"] = plan_rows["clean_core_history_row"]
    visible_cols = [
        "plan_source_file", "plan_source_sha256", "plan_excel_row", "operations_source_file", "operations_source_sha256", "ops_excel_row",
        "熔炼号", "连连浇号", "浇次号", "配包flag", "连铸机号", "转炉设备号_plan", "精炼路径",
        "转炉开始时间", "转炉结束时间", "浇铸开始时间", "浇铸结束时间",
        "plan_heat_unique", "plan_fields_complete", "plan_chronology_ok", "clean_plan_row", "ops_row_present",
        "core_actual_fields_complete", "core_actual_chronology_ok", "route_stage_complete", "route_stage_chronology_ok",
        "clean_core_history_row", "complete_core_history",
    ]
    write_csv(output_dir / "clean_complete_heats.csv", plan_rows[visible_cols].to_dict(orient="records"), visible_cols)
    group_cols = list(group_rows[0].keys()) if group_rows else [
        "continuous_cast_id", "heat_count", "sequence_min", "sequence_max", "sequence_contiguous_1_to_n",
        "planned_caster_codes", "actual_caster_codes", "one_planned_caster", "planned_actual_caster_mapping_ok",
        "all_heats_clean_core_history", "eligible_complete_group", "heat_ids", "route_counts", "plan_start_min", "plan_cast_end_max",
    ]
    write_csv(output_dir / "cast_group_candidates.csv", group_rows, group_cols)

    route_counts = clean_plan.loc[clean_plan["clean_plan_row"], "精炼路径"].value_counts().to_dict()
    eligible_groups = [g for g in group_rows if g["eligible_complete_group"]]
    result = {
        "sources": {
            "plan_file": plan_path.relative_to(ROOT).as_posix(),
            "plan_sha256": plan_hash,
            "operations_file": ops_path.relative_to(ROOT).as_posix(),
            "operations_sha256": ops_hash,
        },
        "filter_definition": {
            "clean_plan_row": "not 配包flag=-2; nonempty heat/group/sequence/caster/converter/route; all plan start/end anchors parse; planned end >= start; heat ID unique within non-placeholder plan rows",
            "clean_duration_row": "metric-specific required time pair is parseable and nonnegative; actual duration metrics also require one unique operations row joined to a clean plan heat",
            "complete_cast_group": "sequence numbers exactly 1..N; one planned caster; planned-to-actual caster code maps A/B/C -> 1/2/3; every heat has a unique operations row and complete core actual timeline; this does not certify Tier-1/Tier-2 temporary-ladle linkage",
            "entity_linkage": "statistics join operations to plan by unique heat ID; temporary-ladle Tier-1/Tier-2 consistency is not a cohort filter and must be checked before final calibration claims",
            "no_repairs": "source rows are never changed; duplicate operation rows are excluded from actual-duration cohort and kept visible in row flags",
        },
        "cohort_counts": cohort,
        "route_counts_clean_plan": {str(k): int(v) for k, v in route_counts.items()},
        "duration_stats": stats_rows,
        "duration_outlier_thresholds": outlier_thresholds,
        "duration_outlier_candidates_csv": "data/audit/duration_outlier_candidates.csv",
        "eligible_complete_cast_group_count": len(eligible_groups),
        "eligible_complete_cast_group_ids": [g["continuous_cast_id"] for g in eligible_groups],
        "files_modified": 0,
        "caveats": [
            "These are descriptive statistics for explicit complete-row cohorts, not corrected/causal estimates.",
            "Per-metric sample sizes differ; do not compare metrics without their N and filter definitions.",
            "The statistics join by heat ID; the cohort is not certified by cross-source temporary-ladle identity. Check Tier-1/Tier-2 links before final calibration claims.",
            "No metric uses the duplicate X/Y field name in 工序时间信息.xlsx; those columns require modeler confirmation.",
        ],
    }
    with (output_dir / "section2_basic_stats.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=PLAN_FILE)
    parser.add_argument("--operations", type=Path, default=OPS_FILE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    result = basic_stats(args.plan, args.operations, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
