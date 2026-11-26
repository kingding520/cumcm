from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

from input_audit import DEFAULT_INPUT, sha256_file  # noqa: E402
from source_quality_audit import run_audit as run_quality_audit  # noqa: E402
from solver_preflight import run_preflight  # noqa: E402

SOURCE_DIR = DEFAULT_INPUT / "附数据文件"


def test_full_source_quality_audit_is_read_only_and_reproducible(tmp_path: Path) -> None:
    inputs = sorted(p for p in SOURCE_DIR.iterdir() if p.suffix.lower() in {".xls", ".xlsx"})
    hashes_before = {p.name: sha256_file(p) for p in inputs}
    result = run_quality_audit(SOURCE_DIR, tmp_path)
    hashes_after = {p.name: sha256_file(p) for p in inputs}

    assert result["source_workbooks"] == 12
    assert result["sheets"] == 12
    assert result["data_rows"] == 6758
    assert result["files_modified"] == 0
    assert hashes_before == hashes_after

    by_rule = result["findings_by_rule"]
    assert by_rule["duplicate_header_name"] == 2
    assert by_rule["rh_degas_start_equals_end"] == 533
    assert by_rule["converter_tap_end_before_heavy_ladle_lift"] == 113
    assert by_rule["lf_clear_before_lift"] == 115
    assert by_rule["rh_clear_before_lift"] == 143
    assert by_rule["cast_end_before_empty_ladle_lift"] == 238
    assert by_rule["bof_missing_steel_ladle_temp_id"] == 440

    findings = list(csv.DictReader((tmp_path / "source_quality_findings.csv").open(encoding="utf-8-sig")))
    assert Counter(row["rule_id"] for row in findings) == Counter(by_rule)
    assert all(row["source_sha256"] for row in findings)
    assert all(row["review_status"] for row in findings)


def test_solver_preflight_only_reports_toy_installation_checks(tmp_path: Path) -> None:
    result = run_preflight(tmp_path / "solver_preflight.json")
    assert result["all_passed"] is True
    assert "synthetic toys" in result["purpose"]
    cp, lp = result["tests"]
    assert cp["status"] == "OPTIMAL"
    assert cp["task_count"] == 4 and cp["machine_count"] == 2
    assert cp["objective_makespan"] == 6
    assert lp["status"] == "optimal"
    assert abs(lp["objective"] - 74 / 3) < 1e-6
    assert (tmp_path / "solver_preflight.json").is_file()
