#!/usr/bin/env python3
"""Trace one requested heat through original source workbooks without edits.

For B-furnace rows whose temporary ladle ID is blank, the script records the
specified Tier-2 fallback evidence (heat ID + ladle number + monotone time
window + weight difference). For LF/RH/CCM rows it checks consistency of the
source temporary-ladle IDs. The output is an audit trace, not a repaired dataset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
import xlrd

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / "question" / "B题：炼钢厂钢包周转与排产优化问题" / "附数据文件"
OUTPUT_DIR = ROOT / "data" / "audit"
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_record(matched: dict[str, list[dict[str, Any]]], names: list[str]) -> dict[str, Any] | None:
    candidates = [record for name in names for record in matched.get(name, [])]
    return candidates[0] if len(candidates) == 1 else None


def as_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = as_text(value)
    if not TIMESTAMP_RE.match(text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def row_dict(headers: list[Any], values: list[Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    result: dict[str, Any] = {}
    for i, (header, value) in enumerate(zip(headers, values), start=1):
        name = as_text(header)
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        key = name if headers.count(header) == 1 else f"{name} [col {i}]"
        result[key] = value
    return result


def read_xlsx_match(path: Path, heat_id: str) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(path, read_only=False, data_only=True, keep_links=False)
    found: list[dict[str, Any]] = []
    for ws in wb.worksheets:
        max_col = ws.max_column or 0
        headers = [ws.cell(1, c).value for c in range(1, max_col + 1)]
        heat_cols = [i for i, h in enumerate(headers) if as_text(h) == "熔炼号"]
        if not heat_cols:
            continue
        for r in range(2, (ws.max_row or 0) + 1):
            values = [ws.cell(r, c).value for c in range(1, max_col + 1)]
            if any(as_text(values[i]) == heat_id for i in heat_cols):
                found.append({
                    "file": path,
                    "sheet": ws.title,
                    "excel_row": r,
                    "headers": headers,
                    "values": values,
                    "data": row_dict(headers, values),
                })
    wb.close()
    return found


def read_xls_match(path: Path, heat_id: str) -> list[dict[str, Any]]:
    wb = xlrd.open_workbook(str(path), on_demand=True)
    found: list[dict[str, Any]] = []
    for sheet in wb.sheets():
        if sheet.nrows == 0:
            continue
        headers = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
        heat_cols = [i for i, h in enumerate(headers) if as_text(h) == "熔炼号"]
        if not heat_cols:
            continue
        for r in range(1, sheet.nrows):
            values = [sheet.cell_value(r, c) if c < sheet.row_len(r) else "" for c in range(sheet.ncols)]
            if any(as_text(values[i]) == heat_id for i in heat_cols):
                found.append({
                    "file": path,
                    "sheet": sheet.name,
                    "excel_row": r + 1,
                    "headers": headers,
                    "values": values,
                    "data": row_dict(headers, values),
                })
    wb.release_resources()
    return found


def find_field(record: dict[str, Any] | None, name: str) -> Any:
    if not record:
        return None
    for key, value in record["data"].items():
        if key == name or key.startswith(name + " [col "):
            return value
    return None


def source_ref(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "file": record["file"].relative_to(ROOT).as_posix(),
        "sheet": record["sheet"],
        "excel_row": record["excel_row"],
    }


def trace_heat(heat_id: str = "26BD6651", input_dir: Path = INPUT_DIR, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    workbook_paths = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in {".xls", ".xlsx"})
    matched: dict[str, list[dict[str, Any]]] = {}
    for path in workbook_paths:
        rows = read_xls_match(path, heat_id) if path.suffix.lower() == ".xls" else read_xlsx_match(path, heat_id)
        if rows:
            matched[path.name] = rows

    process = unique_record(matched, ["工序时间信息.xlsx"])
    converter_names = [n for n in ("A炉.xlsx", "B炉.xlsx", "C炉.xlsx") if matched.get(n)]
    lf_names = [n for n in ("1#LF.xlsx", "3#LF.xlsx") if matched.get(n)]
    rh_names = [n for n in ("1#RH.xlsx", "2#RH.xlsx") if matched.get(n)]
    ccm_names = [n for n in ("1#连铸.xlsx", "2#连铸.xlsx", "3#连铸.xlsx") if matched.get(n)]
    converter = unique_record(matched, converter_names)
    lf = unique_record(matched, lf_names)
    rh = unique_record(matched, rh_names)
    ccm = unique_record(matched, ccm_names)
    plan = unique_record(matched, ["出钢计划表单.xls"])

    matched_paths = sorted({record["file"] for records in matched.values() for record in records})
    source_hashes = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in matched_paths
    }
    events: list[dict[str, Any]] = []
    for source_name, records in matched.items():
        for record in records:
            for i, (header, value) in enumerate(zip(record["headers"], record["values"]), start=1):
                dt = as_datetime(value)
                if dt is None:
                    continue
                # Keep duplicate source headings distinguishable by original column index.
                label = as_text(header) or f"column_{i}"
                duplicates = sum(1 for h in record["headers"] if as_text(h) == label)
                if duplicates > 1:
                    label = f"{label} [col {i}]"
                events.append({
                    "heat_id": heat_id,
                    "event_time": dt.isoformat(sep=" "),
                    "event_field": label,
                    "source_file": record["file"].relative_to(ROOT).as_posix(),
                    "source_sha256": source_hashes[record["file"].relative_to(ROOT).as_posix()],
                    "source_sheet": record["sheet"],
                    "source_excel_row": record["excel_row"],
                    "match_key": "熔炼号 exact",
                    "temporary_ladle_id": as_text(find_field(record, "临时罐号")) or as_text(find_field(record, "钢水罐临时罐号")),
                })
    events.sort(key=lambda e: (e["event_time"], e["source_file"], e["source_excel_row"], e["event_field"]))

    b_ladle = as_text(find_field(converter, "钢水罐号")) or as_text(find_field(converter, "钢包号"))
    b_weight = as_float(find_field(converter, "钢水罐出钢重量"))
    lf_ladle = as_text(find_field(lf, "钢包号"))
    lf_weight = as_float(find_field(lf, "进入重量"))
    b_tap_end = as_datetime(find_field(converter, "出钢结束时间"))
    lf_arrival = as_datetime(find_field(lf, "重包进站到LF精炼待机位时间"))
    time_delta_minutes = (lf_arrival - b_tap_end).total_seconds() / 60 if b_tap_end and lf_arrival else None
    weight_delta = abs(b_weight - lf_weight) if b_weight is not None and lf_weight is not None else None
    b_temp_id = as_text(find_field(converter, "钢水罐临时罐号"))

    temp_ids = {
        "LF": as_text(find_field(lf, "临时罐号")),
        "RH": as_text(find_field(rh, "临时罐号")),
        "CCM": as_text(find_field(ccm, "临时罐号")),
    }
    present_tier1_ids = [v for v in temp_ids.values() if v]
    tier1_consistent = bool(present_tier1_ids) and len(set(present_tier1_ids)) == 1
    tier2_checks = {
        "same_heat_id": bool(converter and lf),
        "same_ladle_number": bool(b_ladle and lf_ladle and b_ladle == lf_ladle),
        "time_monotone": time_delta_minutes is not None and time_delta_minutes >= 0,
        "time_window_le_90min": time_delta_minutes is not None and time_delta_minutes <= 90,
        "weight_diff_le_3t": weight_delta is not None and weight_delta <= 3.0,
    }
    tier2_pass = bool(converter and lf) and all(tier2_checks.values())

    rows_by_source = {name: [source_ref(r) for r in records] for name, records in matched.items()}
    report = {
        "heat_id": heat_id,
        "source_hashes": source_hashes,
        "source_rows_by_workbook": rows_by_source,
        "event_count": len(events),
        "event_trace_csv": f"sample_heat_{heat_id}_trace.csv",
        "converter_to_lf_tier2_fallback": {
            "status": "pass" if tier2_pass else "incomplete_or_review",
            "criteria": tier2_checks,
            "converter_ladle_id": b_ladle,
            "lf_ladle_id": lf_ladle,
            "converter_temp_ladle_id": b_temp_id or None,
            "converter_tap_weight_t": b_weight,
            "lf_incoming_weight_t": lf_weight,
            "absolute_weight_difference_t": weight_delta,
            "tap_end_time": b_tap_end.isoformat(sep=" ") if b_tap_end else None,
            "lf_arrival_time": lf_arrival.isoformat(sep=" ") if lf_arrival else None,
            "minutes_tap_end_to_lf_arrival": time_delta_minutes,
            "source_rows": {"converter": source_ref(converter), "lf": source_ref(lf)},
        },
        "lf_rh_ccm_tier1_temp_ladle_id": {
            "status": "pass" if tier1_consistent else "incomplete_or_review",
            "ids": temp_ids,
            "consistent_nonempty_ids": tier1_consistent,
            "source_rows": {"lf": source_ref(lf), "rh": source_ref(rh), "ccm": source_ref(ccm)},
        },
        "notes": [
            "The trace preserves source timestamps and row references; it does not repair raw cells or claim a full plant schedule.",
            "The B-furnace temporary steel-ladle ID is allowed to be blank only because the Tier-2 fallback checks are explicitly reported.",
            "Duplicate source headers are written with their original column number in event_field.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    import csv
    trace_path = output_dir / f"sample_heat_{heat_id}_trace.csv"
    with trace_path.open("w", encoding="utf-8-sig", newline="") as f:
        columns = ["heat_id", "event_time", "event_field", "source_file", "source_sha256", "source_sheet", "source_excel_row", "match_key", "temporary_ladle_id"]
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(events)
    with (output_dir / f"sample_heat_{heat_id}_join_audit.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heat-id", default="26BD6651")
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.heat_id):
        parser.error("--heat-id must contain only letters, digits, hyphen, or underscore")
    report = trace_heat(args.heat_id, args.input_dir, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    checks = report["converter_to_lf_tier2_fallback"]["status"] == "pass" and report["lf_rh_ccm_tier1_temp_ladle_id"]["status"] == "pass"
    return 0 if checks else 2


if __name__ == "__main__":
    raise SystemExit(main())
