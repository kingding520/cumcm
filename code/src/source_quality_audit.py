#!/usr/bin/env python3
"""Read-only quality scan across all supplied Excel source tables.

The scan records evidence only. It does not drop, rename, fill, or repair source
values. Findings are review candidates with exact worksheet row/column provenance.
XLSX files are read in normal mode because several source exports have an
incorrect XML dimension declaration (A1); XLS rows are accessed defensively for
ragged row lengths.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import openpyxl
import xlrd

from input_audit import DEFAULT_INPUT, DEFAULT_OUTPUT, sha256_file

ROOT = Path(__file__).resolve().parents[2]

# These intervals are chronological expectations already named in PROJECT.md /
# the modeling plan. A negative interval is reported as evidence for review; it
# is not automatically classified as a source defect or repaired.
CHRONOLOGY_PAIRS = (
    ("出钢结束时间", "重罐吊起时间", "converter_tap_end_before_heavy_ladle_lift"),
    ("LF出处理位时间", "LF重罐吊起时间", "lf_clear_before_lift"),
    ("RH处理结束时间", "RH吊起时间", "rh_clear_before_lift"),
    ("停浇时间", "空包在连铸位吊起时间", "cast_end_before_empty_ladle_lift"),
)


def is_nonempty(value: Any) -> bool:
    return value is not None and value != ""


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    raw = text(value)
    if not raw:
        return None
    # Source timestamps are usually strings. Try explicit source formats only;
    # do not let locale-ambiguous parsing silently coerce durations or IDs.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def csv_write(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_xlsx_tables(path: Path) -> Iterable[dict[str, Any]]:
    wb = openpyxl.load_workbook(path, read_only=False, data_only=True, keep_links=False)
    for ws in wb.worksheets:
        ncols = ws.max_column or 0
        headers = [ws.cell(1, c).value for c in range(1, ncols + 1)]
        rows = [
            [ws.cell(r, c).value for c in range(1, ncols + 1)]
            for r in range(2, (ws.max_row or 0) + 1)
        ]
        yield {"sheet": ws.title, "headers": headers, "rows": rows, "header_excel_row": 1, "first_data_excel_row": 2}
    wb.close()


def read_xls_tables(path: Path) -> Iterable[dict[str, Any]]:
    wb = xlrd.open_workbook(str(path), on_demand=True)
    datemode = wb.datemode
    try:
        for sheet in wb.sheets():
            if sheet.nrows == 0:
                continue
            headers = [sheet.cell_value(0, c) for c in range(sheet.ncols)]
            rows: list[list[Any]] = []
            for r in range(1, sheet.nrows):
                values: list[Any] = []
                for c in range(sheet.ncols):
                    if c >= sheet.row_len(r):
                        values.append("")
                        continue
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        values.append(xlrd.xldate_as_datetime(cell.value, datemode))
                    else:
                        values.append(cell.value)
                rows.append(values)
            yield {"sheet": sheet.name, "headers": headers, "rows": rows, "header_excel_row": 1, "first_data_excel_row": 2}
    finally:
        wb.release_resources()


def indices_for(headers: list[Any], name: str) -> list[int]:
    return [i for i, value in enumerate(headers) if text(value) == name]


def value_at(headers: list[Any], row: list[Any], name: str) -> Any:
    indexes = indices_for(headers, name)
    if len(indexes) != 1:
        return None
    idx = indexes[0]
    return row[idx] if idx < len(row) else None


def column_label(headers: list[Any], idx: int) -> str:
    name = text(headers[idx])
    count = sum(1 for h in headers if text(h) == name)
    return f"{name} [col {idx + 1}]" if count > 1 else name


def scan_table(source: Path, digest: str, table: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    headers: list[Any] = table["headers"]
    rows: list[list[Any]] = table["rows"]
    duplicate_header_names = sorted(
        name for name, count in Counter(text(h) for h in headers if text(h)).items() if count > 1
    )
    heat_cols = indices_for(headers, "熔炼号")
    temp_cols = sorted(set(indices_for(headers, "临时罐号") + indices_for(headers, "钢水罐临时罐号")))
    ladle_cols = sorted(set(indices_for(headers, "钢包号") + indices_for(headers, "钢水罐号")))
    heat_values = [text(row[heat_cols[0]]) if heat_cols and heat_cols[0] < len(row) else "" for row in rows]
    heat_counter = Counter(value for value in heat_values if value)
    duplicate_ids = {value for value, n in heat_counter.items() if n > 1}

    nonempty_key_ids = sum(bool(v) for v in heat_values)
    missing_temp_rows = sum(
        1 for row in rows if temp_cols and not any(idx < len(row) and is_nonempty(row[idx]) for idx in temp_cols)
    )
    missing_ladle_rows = sum(
        1 for row in rows if ladle_cols and not any(idx < len(row) and is_nonempty(row[idx]) for idx in ladle_cols)
    )
    summary = {
        "source_file": source.relative_to(ROOT).as_posix(),
        "source_sha256": digest,
        "sheet_name": table["sheet"],
        "data_rows": len(rows),
        "columns": len(headers),
        "duplicate_header_names": ";".join(duplicate_header_names),
        "heat_id_nonempty_rows": nonempty_key_ids if heat_cols else "",
        "heat_id_missing_rows": len(rows) - nonempty_key_ids if heat_cols else "",
        "unique_nonempty_heat_ids": len(heat_counter) if heat_cols else "",
        "duplicate_heat_id_count": len(duplicate_ids) if heat_cols else "",
        "rows_with_duplicate_heat_id": sum(heat_counter[k] for k in duplicate_ids) if heat_cols else "",
        "temp_id_missing_rows": missing_temp_rows if temp_cols else "",
        "ladle_number_missing_rows": missing_ladle_rows if ladle_cols else "",
    }

    findings: list[dict[str, Any]] = []
    for name in duplicate_header_names:
        positions = [i + 1 for i, h in enumerate(headers) if text(h) == name]
        findings.append({
            "rule_id": "duplicate_header_name",
            "review_status": "needs_semantic_review",
            "source_file": summary["source_file"],
            "source_sha256": digest,
            "sheet_name": table["sheet"],
            "excel_row": 1,
            "heat_id": "",
            "field_a": name,
            "value_a": "",
            "field_b": "duplicate columns",
            "value_b": ",".join(str(i) for i in positions),
            "difference": "",
            "evidence": f"same header occurs in columns {positions}; source columns preserved",
        })

    # Keep each physical row of duplicate heat IDs, including placeholders; the
    # status remains review-only because duplicated IDs may have different causes.
    for row_idx, heat_id in enumerate(heat_values):
        if heat_id and heat_id in duplicate_ids:
            excel_row = table["first_data_excel_row"] + row_idx
            findings.append({
                "rule_id": "duplicate_heat_id_within_sheet",
                "review_status": "needs_entity_resolution",
                "source_file": summary["source_file"],
                "source_sha256": digest,
                "sheet_name": table["sheet"],
                "excel_row": excel_row,
                "heat_id": heat_id,
                "field_a": "熔炼号",
                "value_a": heat_id,
                "field_b": "配包flag",
                "value_b": text(value_at(headers, rows[row_idx], "配包flag")),
                "difference": "",
                "evidence": "all source rows with this repeated heat ID are retained; placeholder flag is shown when available",
            })

    filename = source.name
    if filename in {"A炉.xlsx", "B炉.xlsx", "C炉.xlsx"}:
        temp_ladle_cols = indices_for(headers, "钢水罐临时罐号")
        if len(temp_ladle_cols) == 1:
            col = temp_ladle_cols[0]
            for row_idx, row in enumerate(rows):
                heat_id = text(value_at(headers, row, "熔炼号"))
                temp_id = row[col] if col < len(row) else None
                if heat_id and not is_nonempty(temp_id):
                    findings.append({
                        "rule_id": "bof_missing_steel_ladle_temp_id",
                        "review_status": "tier2_fallback_candidate_not_joined",
                        "source_file": summary["source_file"],
                        "source_sha256": digest,
                        "sheet_name": table["sheet"],
                        "excel_row": table["first_data_excel_row"] + row_idx,
                        "heat_id": heat_id,
                        "field_a": "钢水罐临时罐号",
                        "value_a": "",
                        "field_b": "钢包号/钢水罐号",
                        "value_b": text(value_at(headers, row, "钢包号")) or text(value_at(headers, row, "钢水罐号")),
                        "difference": "",
                        "evidence": "source temporary-ladle ID is blank; candidate fallback evidence retained, no join attempted",
                    })
    if filename == "3#LF.xlsx":
        for field in ("LF重包从待机位吊起时间", "吊包天车", "临时罐号"):
            columns = indices_for(headers, field)
            if len(columns) != 1:
                continue
            col = columns[0]
            for row_idx, row in enumerate(rows):
                if col < len(row) and not is_nonempty(row[col]):
                    heat_id = text(value_at(headers, row, "熔炼号"))
                    findings.append({
                        "rule_id": "3lf_missing_candidate_field",
                        "review_status": "candidate_for_cross_source_recovery",
                        "source_file": summary["source_file"],
                        "source_sha256": digest,
                        "sheet_name": table["sheet"],
                        "excel_row": table["first_data_excel_row"] + row_idx,
                        "heat_id": heat_id,
                        "field_a": field,
                        "value_a": "",
                        "field_b": "临时罐号/熔炼号",
                        "value_b": text(value_at(headers, row, "临时罐号")) or heat_id,
                        "difference": "",
                        "evidence": "source cell is blank; no automatic fill attempted",
                    })

    if filename in {"1#RH.xlsx", "2#RH.xlsx"}:
        start_cols = indices_for(headers, "脱气开始时间")
        end_cols = indices_for(headers, "脱气结束时间")
        if len(start_cols) == len(end_cols) == 1:
            for row_idx, row in enumerate(rows):
                start = row[start_cols[0]] if start_cols[0] < len(row) else None
                end = row[end_cols[0]] if end_cols[0] < len(row) else None
                if is_nonempty(start) and is_nonempty(end) and text(start) == text(end):
                    findings.append({
                        "rule_id": "rh_degas_start_equals_end",
                        "review_status": "source_fact_modeling_semantics_required",
                        "source_file": summary["source_file"],
                        "source_sha256": digest,
                        "sheet_name": table["sheet"],
                        "excel_row": table["first_data_excel_row"] + row_idx,
                        "heat_id": text(value_at(headers, row, "熔炼号")),
                        "field_a": "脱气开始时间",
                        "value_a": text(start),
                        "field_b": "脱气结束时间",
                        "value_b": text(end),
                        "difference": 0,
                        "evidence": "both nonempty source cells are equal; no duration inferred",
                    })

    if filename == "工序时间信息.xlsx":
        for start_name, end_name, rule_id in CHRONOLOGY_PAIRS:
            start_cols, end_cols = indices_for(headers, start_name), indices_for(headers, end_name)
            if len(start_cols) != 1 or len(end_cols) != 1:
                continue
            for row_idx, row in enumerate(rows):
                start_raw = row[start_cols[0]] if start_cols[0] < len(row) else None
                end_raw = row[end_cols[0]] if end_cols[0] < len(row) else None
                start, end = parse_datetime(start_raw), parse_datetime(end_raw)
                if start is None or end is None:
                    continue
                minutes = (end - start).total_seconds() / 60
                if minutes < 0:
                    findings.append({
                        "rule_id": rule_id,
                        "review_status": "chronology_candidate_no_repair",
                        "source_file": summary["source_file"],
                        "source_sha256": digest,
                        "sheet_name": table["sheet"],
                        "excel_row": table["first_data_excel_row"] + row_idx,
                        "heat_id": text(value_at(headers, row, "熔炼号")),
                        "field_a": start_name,
                        "value_a": text(start_raw),
                        "field_b": end_name,
                        "value_b": text(end_raw),
                        "difference": round(minutes, 3),
                        "evidence": "end timestamp precedes start timestamp; row retained for entity/clock review",
                    })

    return summary, findings, []


def run_audit(input_dir: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    input_dir, output_dir = input_dir.resolve(), output_dir.resolve()
    sources = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in {".xlsx", ".xls"})
    if not sources:
        raise FileNotFoundError(f"no XLS/XLSX files in {input_dir}")
    summaries: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for source in sources:
        digest = sha256_file(source)
        tables = read_xlsx_tables(source) if source.suffix.lower() == ".xlsx" else read_xls_tables(source)
        for table in tables:
            summary, sheet_findings, _ = scan_table(source, digest, table)
            summaries.append(summary)
            findings.extend(sheet_findings)

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_cols = [
        "source_file", "source_sha256", "sheet_name", "data_rows", "columns", "duplicate_header_names",
        "heat_id_nonempty_rows", "heat_id_missing_rows", "unique_nonempty_heat_ids", "duplicate_heat_id_count",
        "rows_with_duplicate_heat_id", "temp_id_missing_rows", "ladle_number_missing_rows",
    ]
    finding_cols = [
        "rule_id", "review_status", "source_file", "source_sha256", "sheet_name", "excel_row", "heat_id",
        "field_a", "value_a", "field_b", "value_b", "difference", "evidence",
    ]
    csv_write(output_dir / "source_quality_summary.csv", summaries, summary_cols)
    csv_write(output_dir / "source_quality_findings.csv", findings, finding_cols)
    by_rule = Counter(f["rule_id"] for f in findings)
    result = {
        "source_workbooks": len(sources),
        "sheets": len(summaries),
        "data_rows": sum(int(s["data_rows"]) for s in summaries),
        "findings": len(findings),
        "findings_by_rule": dict(sorted(by_rule.items())),
        "files_modified": 0,
        "interpretation": "review candidates only; source inputs were not changed or repaired",
    }
    with (output_dir / "source_quality_audit.json").open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT / "附数据文件")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_audit(args.input_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
