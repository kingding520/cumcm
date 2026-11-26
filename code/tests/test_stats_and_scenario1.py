from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

from clean_row_statistics import basic_stats  # noqa: E402
from scenario1_historical_replay import run_replay  # noqa: E402


def test_clean_row_statistics_uses_explicit_complete_cohorts(tmp_path: Path) -> None:
    result = basic_stats(output_dir=tmp_path)
    counts = result["cohort_counts"]
    assert counts["operational_plan_rows"] == 1266
    assert counts["plan_complete_chronology_rows"] == 1266
    assert counts["unique_operations_rows_joined_to_plan"] == 1136
    assert counts["clean_core_history_rows"] == 841
    assert result["eligible_complete_cast_group_count"] == 12
    assert "6C0754" in result["eligible_complete_cast_group_ids"]
    assert (tmp_path / "clean_complete_heats.csv").is_file()
    assert (tmp_path / "basic_duration_stats.csv").is_file()
    assert (tmp_path / "duration_outlier_candidates.csv").is_file()
    groups = list(csv.DictReader((tmp_path / "cast_group_candidates.csv").open(encoding="utf-8-sig")))
    chosen = next(row for row in groups if row["continuous_cast_id"] == "6C0754")
    assert chosen["eligible_complete_group"] == "True"
    assert chosen["heat_count"] == "6"


def test_scenario1_one_cast_historical_replay_artifacts_and_limits(tmp_path: Path) -> None:
    result = run_replay("6C0754", tmp_path / "scene1")
    metrics = result["metrics"]
    assert metrics["heat_count"] == 6
    assert metrics["primary_move_count"] == 18
    assert metrics["cp_sat_fixed_replay_status"] in {"OPTIMAL", "FEASIBLE"}
    assert metrics["same_crane_overlap_pairs"] == 0
    assert metrics["tier2_fallback_failure_heat_ids"] == []
    assert metrics["tier1_temp_id_mismatch_heat_ids"] == ["26BD6455"]
    assert metrics["spatial_safety"] == "not_assessed"
    for file in ["moves.csv", "schedule.csv", "operations.csv", "side_events.csv", "violations.csv", "metrics.json", "run_manifest.json"]:
        assert (tmp_path / "scene1" / file).is_file()
    saved = json.loads((tmp_path / "scene1" / "metrics.json").read_text(encoding="utf-8"))
    assert saved["trajectory"].startswith("not_generated")
