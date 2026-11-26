#!/usr/bin/env python3
"""Non-destructive inventory of the contest statement and source workbooks.

The source files remain untouched under ``question/``. Outputs are deterministic
CSV manifests under ``data/audit/``. XLSX files are loaded in normal (not
read_only) mode because the supplied exports declare worksheet dimension A1
while containing hundreds of populated rows; openpyxl's read-only iterator can
therefore silently return only the first row.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import openpyxl
import xlrd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "question" / "B题：炼钢厂钢包周转与排产优化问题"
DEFAULT_OUTPUT = ROOT / "data" / "audit"

XLSX_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nonempty(value: Any) -> bool:
    return value is not None and value != ""


def value_type(value: Any, *, formula: bool = False) -> str:
    if formula:
        return "formula"
    if value is None or value == "":
        return "blank"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return "datetime"
    return "text"


def xlsx_declared_dimensions(path: Path) -> list[str]:
    """Return raw worksheet <dimension> values in workbook sheet order."""
    dimensions: list[str] = []
    with zipfile.ZipFile(path) as zf:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
        rel_map = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels_root.findall("r:Relationship", rel_ns)
        }
        sheets = workbook.findall("x:sheets/x:sheet", XLSX_NS)
        for sheet in sheets:
            rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rel_map.get(rid, "")
            member = target.lstrip("/")
            if not member.startswith("xl/"):
                member = "xl/" + member
            root = ET.fromstring(zf.read(member))
            dim = root.find("x:dimension", XLSX_NS)
            dimensions.append(dim.attrib.get("ref", "") if dim is not None else "")
    return dimensions


def audit_xlsx(path: Path, relpath: str, digest: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[str] = []
    with zipfile.ZipFile(path) as zf:
        bad_member = zf.testzip()
        if bad_member:
            raise ValueError(f"corrupt ZIP member: {bad_member}")
    declared = xlsx_declared_dimensions(path)
    # Critical: not read_only. Supplied source XLSX files carry stale A1 dimension metadata.
    wb = openpyxl.load_workbook(path, read_only=False, data_only=False, keep_links=False)
    sheet_rows: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    raw_dimensions: list[str] = []
    total_data_rows = 0
    total_columns = 0

    for sheet_index, ws in enumerate(wb.worksheets):
        raw_dim = declared[sheet_index] if sheet_index < len(declared) else ""
        raw_dimensions.append(raw_dim)
        actual_dim = ws.calculate_dimension()
        if raw_dim == "A1" and actual_dim != "A1":
            warnings.append(
                f"{ws.title}: worksheet XML declares A1 but actual populated range is {actual_dim}; read_only mode undercounts"
            )
        max_row = ws.max_row or 0
        max_col = ws.max_column or 0
        total_columns += max_col
        headers = [ws.cell(1, c).value for c in range(1, max_col + 1)]
        header_counts = Counter(str(h).strip() for h in headers if nonempty(h))
        duplicate_headers = sorted(k for k, n in header_counts.items() if n > 1)
        if duplicate_headers:
            warnings.append(f"{ws.title}: duplicate headers: {duplicate_headers}")

        nonempty_data_rows = 0
        for r in range(2, max_row + 1):
            if any(nonempty(ws.cell(r, c).value) for c in range(1, max_col + 1)):
                nonempty_data_rows += 1
        total_data_rows += nonempty_data_rows
        sheet_warnings = [w for w in warnings if w.startswith(f"{ws.title}:")]
        sheet_rows.append({
            "source_file": relpath,
            "file_sha256": digest,
            "sheet_name": ws.title,
            "sheet_state": ws.sheet_state,
            "declared_dimension": raw_dim,
            "actual_dimension": actual_dim,
            "header_row": 1,
            "max_row_including_header": max_row,
            "max_column": max_col,
            "nonempty_data_rows": nonempty_data_rows,
            "duplicate_headers": ";".join(duplicate_headers),
            "warnings": " | ".join(sheet_warnings),
        })

        for c, header in enumerate(headers, start=1):
            type_counts: Counter[str] = Counter()
            nonempty_count = 0
            for r in range(2, max_row + 1):
                cell = ws.cell(r, c)
                val = cell.value
                if nonempty(val):
                    nonempty_count += 1
                    type_counts[value_type(val, formula=(cell.data_type == "f"))] += 1
            col_letter = openpyxl.utils.get_column_letter(c)
            fields.append({
                "source_file": relpath,
                "file_sha256": digest,
                "sheet_name": ws.title,
                "column_index": c,
                "column_letter": col_letter,
                "field_name": str(header).strip() if nonempty(header) else "",
                "nonempty_data_cells": nonempty_count,
                "data_types": json.dumps(dict(sorted(type_counts.items())), ensure_ascii=False, sort_keys=True),
                "header_blank": not nonempty(header),
            })

    manifest = {
        "path": relpath,
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "format": "xlsx",
        "integrity": "ok",
        "sheet_count": len(wb.worksheets),
        "sheet_names": ";".join(ws.title for ws in wb.worksheets),
        "data_rows_total": total_data_rows,
        "columns_total": total_columns,
        "declared_dimensions": ";".join(raw_dimensions),
        "warnings": " | ".join(warnings),
        "status": "warning" if warnings else "ok",
    }
    wb.close()
    return manifest, sheet_rows, fields


def audit_xls(path: Path, relpath: str, digest: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    book = xlrd.open_workbook(str(path), on_demand=True)
    sheet_rows: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_rows = 0
    total_columns = 0
    for sheet in book.sheets():
        max_col = sheet.ncols
        max_row = sheet.nrows
        total_columns += max_col
        nonempty_data_rows = sum(
            1 for r in range(1, max_row)
            if any(sheet.cell_value(r, c) not in (None, "") for c in range(max_col))
        )
        total_rows += nonempty_data_rows
        headers = [sheet.cell_value(0, c) if max_row else "" for c in range(max_col)]
        counts = Counter(str(h).strip() for h in headers if h not in (None, ""))
        dupes = sorted(k for k, n in counts.items() if n > 1)
        if dupes:
            warnings.append(f"{sheet.name}: duplicate headers: {dupes}")
        sheet_rows.append({
            "source_file": relpath,
            "file_sha256": digest,
            "sheet_name": sheet.name,
            "sheet_state": "visible",
            "declared_dimension": "",
            "actual_dimension": f"1:{max_row},1:{max_col}",
            "header_row": 1,
            "max_row_including_header": max_row,
            "max_column": max_col,
            "nonempty_data_rows": nonempty_data_rows,
            "duplicate_headers": ";".join(dupes),
            "warnings": "",
        })
        for col0, header in enumerate(headers):
            t: Counter[str] = Counter()
            nonempty_count = 0
            for r in range(1, max_row):
                # xlrd uses zero-based indexes and may raise IndexError for ragged-row cells.
                if col0 >= sheet.row_len(r):
                    continue
                cell = sheet.cell(r, col0)
                if cell.value not in (None, ""):
                    nonempty_count += 1
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        kind = "datetime"
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        kind = "number"
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        kind = "boolean"
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        kind = "error"
                    else:
                        kind = "text"
                    t[kind] += 1
            fields.append({
                "source_file": relpath,
                "file_sha256": digest,
                "sheet_name": sheet.name,
                "column_index": col0 + 1,
                "column_letter": xlrd.colname(col0),
                "field_name": str(header).strip() if header not in (None, "") else "",
                "nonempty_data_cells": nonempty_count,
                "data_types": json.dumps(dict(sorted(t.items())), ensure_ascii=False, sort_keys=True),
                "header_blank": header in (None, ""),
            })
    book.release_resources()
    return {
        "path": relpath,
        "sha256": digest,
        "size_bytes": path.stat().st_size,
        "format": "xls",
        "integrity": "ok",
        "sheet_count": len(sheet_rows),
        "sheet_names": ";".join(r["sheet_name"] for r in sheet_rows),
        "data_rows_total": total_rows,
        "columns_total": total_columns,
        "declared_dimensions": "",
        "warnings": " | ".join(warnings),
        "status": "warning" if warnings else "ok",
    }, sheet_rows, fields


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_audit(input_dir: Path = DEFAULT_INPUT, output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"input directory does not exist: {input_dir}")
    root = ROOT.resolve()
    paths = sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".docx", ".xlsx", ".xls"})
    if not paths:
        raise FileNotFoundError(f"no .docx/.xlsx/.xls inputs under {input_dir}")

    manifest_rows: list[dict[str, Any]] = []
    sheet_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    for path in paths:
        relpath = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        digest = sha256_file(path)
        suffix = path.suffix.lower()
        try:
            if suffix == ".xlsx":
                record, sheets, fields = audit_xlsx(path, relpath, digest)
            elif suffix == ".xls":
                record, sheets, fields = audit_xls(path, relpath, digest)
            else:
                with zipfile.ZipFile(path) as zf:
                    bad_member = zf.testzip()
                    if bad_member:
                        raise ValueError(f"corrupt DOCX ZIP member: {bad_member}")
                    required = {"[Content_Types].xml", "word/document.xml"}
                    missing = required.difference(zf.namelist())
                    if missing:
                        raise ValueError(f"not a valid DOCX package; missing {sorted(missing)}")
                record = {
                    "path": relpath,
                    "sha256": digest,
                    "size_bytes": path.stat().st_size,
                    "format": "docx",
                    "integrity": "ok",
                    "sheet_count": 0,
                    "sheet_names": "",
                    "data_rows_total": "",
                    "columns_total": "",
                    "declared_dimensions": "",
                    "warnings": "",
                    "status": "ok",
                }
                sheets, fields = [], []
            manifest_rows.append(record)
            sheet_rows.extend(sheets)
            field_rows.extend(fields)
        except Exception as exc:
            manifest_rows.append({
                "path": relpath,
                "sha256": digest,
                "size_bytes": path.stat().st_size,
                "format": suffix.lstrip("."),
                "integrity": "error",
                "sheet_count": "",
                "sheet_names": "",
                "data_rows_total": "",
                "columns_total": "",
                "declared_dimensions": "",
                "warnings": f"{type(exc).__name__}: {exc}",
                "status": "error",
            })

    write_csv(output_dir / "data_manifest.csv", manifest_rows, [
        "path", "sha256", "size_bytes", "format", "integrity", "sheet_count",
        "sheet_names", "data_rows_total", "columns_total", "declared_dimensions", "warnings", "status",
    ])
    write_csv(output_dir / "sheet_inventory.csv", sheet_rows, [
        "source_file", "file_sha256", "sheet_name", "sheet_state", "declared_dimension",
        "actual_dimension", "header_row", "max_row_including_header", "max_column",
        "nonempty_data_rows", "duplicate_headers", "warnings",
    ])
    write_csv(output_dir / "field_dictionary.csv", field_rows, [
        "source_file", "file_sha256", "sheet_name", "column_index", "column_letter",
        "field_name", "nonempty_data_cells", "data_types", "header_blank",
    ])
    return {
        "input_files": len(paths),
        "successful_files": sum(r["integrity"] == "ok" for r in manifest_rows),
        "error_files": sum(r["integrity"] != "ok" for r in manifest_rows),
        "workbook_sheets": len(sheet_rows),
        "fields": len(field_rows),
        "data_rows": sum(int(r["data_rows_total"] or 0) for r in manifest_rows),
        "warnings": sum(bool(r["warnings"]) for r in manifest_rows),
        "output_dir": str(output_dir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = run_audit(args.input_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["error_files"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
