from __future__ import annotations

import csv
import sys
from pathlib import Path

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

from input_audit import DEFAULT_INPUT, audit_xls, run_audit, sha256_file  # noqa: E402
from plan_audit import DEFAULT_SOURCE as PLAN_SOURCE, audit_plan  # noqa: E402
from trace_sample_heat import trace_heat  # noqa: E402


def test_source_corpus_inventory_is_complete(tmp_path: Path) -> None:
    """Audit the actual statement + 12 attachments, without modifying source files."""
    result = run_audit(DEFAULT_INPUT, tmp_path)
    assert result["input_files"] == 13
    assert result["successful_files"] == 13
    assert result["error_files"] == 0
    assert result["workbook_sheets"] == 12
    assert result["data_rows"] == 6758
    assert result["fields"] == 684

    manifest = list(csv.DictReader((tmp_path / "data_manifest.csv").open(encoding="utf-8-sig")))
    assert sum(row["format"] == "docx" for row in manifest) == 1
    assert sum(row["format"] in {"xls", "xlsx"} for row in manifest) == 12
    assert all(row["integrity"] == "ok" for row in manifest)
    # Several WPS exports carry the incorrect A1 declared dimension; the audit must flag it.
    assert any("read_only mode undercounts" in row["warnings"] for row in manifest)
    # Duplicate source headers are recorded, not silently renamed or dropped.
    assert any("duplicate headers" in row["warnings"] for row in manifest)


def test_legacy_xls_ragged_rows_and_last_column(tmp_path: Path) -> None:
    source = DEFAULT_INPUT / "附数据文件" / "出钢计划表单.xls"
    manifest, sheets, fields = audit_xls(source, source.name, sha256_file(source))
    assert manifest["integrity"] == "ok"
    assert sheets[0]["max_row_including_header"] == 1322
    assert sheets[0]["max_column"] == 42
    assert sheets[0]["nonempty_data_rows"] == 1321
    assert len(fields) == 42
    assert fields[-1]["column_index"] == 42
    assert fields[-1]["column_letter"] == "AP"


def test_manifest_hash_is_stable() -> None:
    source = DEFAULT_INPUT / "B题：炼钢厂钢包周转与排产优化问题.docx"
    first = sha256_file(source)
    second = sha256_file(source)
    assert len(first) == 64
    assert first == second


def test_tapping_plan_audit_excludes_placeholders_before_uniqueness(tmp_path: Path) -> None:
    result = audit_plan(PLAN_SOURCE, tmp_path)
    assert result["source_rows"] == 1321
    assert result["placeholders"] == 55
    assert result["operational_rows"] == 1266
    assert result["unique_operational_heats"] == 1266
    assert result["duplicate_operational_rows"] == 0
    assert result["continuous_cast_ids"] == 152
    assert result["tundish_sequence_mismatches"] == 0
    assert sum(result["route_counts"].values()) == 1266


def test_sample_heat_trace_proves_tier2_and_tier1_links(tmp_path: Path) -> None:
    report = trace_heat("26BD6651", output_dir=tmp_path)
    assert report["event_count"] > 0
    assert report["converter_to_lf_tier2_fallback"]["status"] == "pass"
    assert report["converter_to_lf_tier2_fallback"]["absolute_weight_difference_t"] == 0.0
    assert report["converter_to_lf_tier2_fallback"]["minutes_tap_end_to_lf_arrival"] < 90
    assert report["lf_rh_ccm_tier1_temp_ladle_id"]["status"] == "pass"
    assert report["lf_rh_ccm_tier1_temp_ladle_id"]["ids"]["LF"] == report["lf_rh_ccm_tier1_temp_ladle_id"]["ids"]["CCM"]
    assert (tmp_path / "sample_heat_26BD6651_trace.csv").is_file()
