#!/usr/bin/env python3
"""Reproduce basic, non-destructive quality checks for the tapping-plan XLS.

This script deliberately does not repair or normalize records. It separates the
55 rows marked ``配包flag=-2`` from operational heats before checking uniqueness
and exports row-level duplicate evidence for later ETL review.
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
DEFAULT_SOURCE = ROOT / "question" / "B题：炼钢厂钢包周转与排产优化问题" / "附数据文件" / "出钢计划表单.xls"
DEFAULT_OUT = ROOT / "data" / "audit"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def csv_write(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def audit_plan(source: Path = DEFAULT_SOURCE, out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    df = pd.read_excel(source, engine="xlrd")
    required = {"熔炼号", "配包flag", "连连浇号", "浇次号", "更换中包", "精炼路径", "转炉开始时间", "浇铸结束时间"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    source_hash = sha256_file(source)
    placeholder = df["配包flag"].eq(-2)
    operational = df.loc[~placeholder].copy()
    valid_heat = operational["熔炼号"].notna() & operational["熔炼号"].astype(str).str.strip().ne("")
    operational_with_heat = operational.loc[valid_heat]
    dup_mask = df["熔炼号"].notna() & df["熔炼号"].duplicated(keep=False)
    dup_rows: list[dict[str, Any]] = []
    for idx, row in df.loc[dup_mask].iterrows():
        dup_rows.append({
            "source_file": source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else source.as_posix(),
            "source_sha256": source_hash,
            "excel_row": int(idx) + 2,
            "heat_id": row["熔炼号"],
            "package_flag": row["配包flag"],
            "is_placeholder": bool(row["配包flag"] == -2),
            "route": row.get("精炼路径", ""),
        })

    mismatch = operational.loc[
        operational["更换中包"].eq(1).ne(operational["浇次号"].eq(1))
    ]
    route_counts = operational["精炼路径"].fillna("<missing>").astype(str).value_counts(dropna=False)
    route_rows = [
        {"source_file": source.name, "source_sha256": source_hash, "route": str(route), "rows_excluding_placeholders": int(n)}
        for route, n in sorted(route_counts.items(), key=lambda item: str(item[0]))
    ]

    start = pd.to_datetime(operational["转炉开始时间"], errors="coerce")
    cast_end = pd.to_datetime(operational["浇铸结束时间"], errors="coerce")
    checks = [
        {"check": "source_rows", "value": int(len(df)), "expected": "inventory only", "status": "info", "meaning": "raw worksheet rows excluding header"},
        {"check": "placeholder_rows_flag_minus_2", "value": int(placeholder.sum()), "expected": "preserve and exclude from operational heat counts", "status": "info", "meaning": "rows marked as non-operational placeholders"},
        {"check": "operational_rows", "value": int((~placeholder).sum()), "expected": "source_rows - placeholder_rows", "status": "info", "meaning": "rows retained for operational checks; no rows deleted"},
        {"check": "nonempty_operational_heat_ids", "value": int(valid_heat.sum()), "expected": "manual review if differs from operational_rows", "status": "pass" if valid_heat.all() else "warning", "meaning": "valid rows with a nonempty 熔炼号"},
        {"check": "unique_operational_heat_ids", "value": int(operational_with_heat["熔炼号"].nunique()), "expected": "compare with nonempty_operational_heat_ids", "status": "pass" if operational_with_heat["熔炼号"].nunique() == valid_heat.sum() else "warning", "meaning": "uniqueness checked only after excluding -2 placeholders"},
        {"check": "duplicate_operational_heat_rows", "value": int(operational_with_heat["熔炼号"].duplicated(keep=False).sum()), "expected": 0, "status": "pass" if not operational_with_heat["熔炼号"].duplicated(keep=False).any() else "warning", "meaning": "number of operational rows participating in duplicate heat IDs"},
        {"check": "unique_continuous_cast_ids", "value": int(operational["连连浇号"].nunique(dropna=True)), "expected": "descriptive", "status": "info", "meaning": "distinct 连连浇号 among non-placeholder rows"},
        {"check": "tundish_change_vs_cast_sequence_mismatch_rows", "value": int(len(mismatch)), "expected": 0, "status": "pass" if mismatch.empty else "warning", "meaning": "operational rows where (更换中包 == 1) differs from (浇次号 == 1)"},
        {"check": "tundish_change_rows", "value": int(operational["更换中包"].eq(1).sum()), "expected": "review against sequence semantics", "status": "info", "meaning": "operational rows with 更换中包 == 1"},
        {"check": "cast_sequence_1_rows", "value": int(operational["浇次号"].eq(1).sum()), "expected": "review against source semantics", "status": "info", "meaning": "operational rows with 浇次号 == 1"},
        {"check": "route_counts", "value": json.dumps({str(k): int(v) for k, v in route_counts.items()}, ensure_ascii=False, sort_keys=True), "expected": "compute after excluding placeholders", "status": "info", "meaning": "counts by 精炼路径; placeholder rows are excluded"},
        {"check": "converter_start_missing_operational", "value": int(start.isna().sum()), "expected": "inspect if nonzero", "status": "pass" if not start.isna().any() else "warning", "meaning": "missing/unparseable 转炉开始时间 among non-placeholder rows"},
        {"check": "cast_end_missing_operational", "value": int(cast_end.isna().sum()), "expected": "inspect if nonzero", "status": "pass" if not cast_end.isna().any() else "warning", "meaning": "missing/unparseable 浇铸结束时间 among non-placeholder rows"},
        {"check": "operational_start_min", "value": start.min().isoformat() if start.notna().any() else "", "expected": "descriptive", "status": "info", "meaning": "minimum parsed converter start time"},
        {"check": "operational_cast_end_max", "value": cast_end.max().isoformat() if cast_end.notna().any() else "", "expected": "descriptive", "status": "info", "meaning": "maximum parsed cast end time"},
    ]

    out_dir = out_dir.resolve()
    csv_write(out_dir / "plan_route_counts.csv", route_rows, ["source_file", "source_sha256", "route", "rows_excluding_placeholders"])
    csv_write(out_dir / "plan_duplicate_heat_rows.csv", dup_rows, ["source_file", "source_sha256", "excel_row", "heat_id", "package_flag", "is_placeholder", "route"])
    with (out_dir / "plan_audit.json").open("w", encoding="utf-8") as f:
        json.dump({
            "source_file": source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else source.as_posix(),
            "source_sha256": source_hash,
            "engine": {"pandas": pd.__version__, "xls_reader": "xlrd"},
            "checks": checks,
            "notes": [
                "This script reports source facts only; it does not repair rows or assign semantic meaning beyond documented field labels.",
                "Duplicate heat IDs are reported with Excel row numbers and placeholder flags; do not treat placeholder collisions as operational duplicates.",
            ],
        }, f, ensure_ascii=False, indent=2)
    return {
        "source_rows": len(df),
        "placeholders": int(placeholder.sum()),
        "operational_rows": int((~placeholder).sum()),
        "unique_operational_heats": int(operational_with_heat["熔炼号"].nunique()),
        "duplicate_operational_rows": int(operational_with_heat["熔炼号"].duplicated(keep=False).sum()),
        "continuous_cast_ids": int(operational["连连浇号"].nunique(dropna=True)),
        "tundish_sequence_mismatches": int(len(mismatch)),
        "route_counts": {str(k): int(v) for k, v in route_counts.items()},
        "output_dir": str(out_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = audit_plan(args.source, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["tundish_sequence_mismatches"] == 0 and result["duplicate_operational_rows"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
