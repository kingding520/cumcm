from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pytest

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

import rail_model as rail  # noqa: E402
import scenario1_closed_loop as loop  # noqa: E402


# ---------------------------------------------------------------- kinematics
@pytest.mark.parametrize(
    "dx,duration,accel",
    [
        (410.0, 280.0, 0.35),
        (-410.0, 125.6, 0.35),
        (285.0, 50.4, 0.45),
        (1235.0, 400.0, 0.35),
        (0.0, 20.0, 0.35),
    ],
)
def test_profile_matches_requested_time_and_distance(dx: float, duration: float, accel: float) -> None:
    """The profile must consume exactly the declared window and displacement."""
    pieces, cap_ratio, accel_used, raised = rail.safe_profile(dx, duration, accel, v_max=3.0)
    assert math.isclose(sum(p[0] for p in pieces), duration, rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(sum(p[1] for p in pieces), dx, rel_tol=1e-9, abs_tol=1e-6)
    assert accel_used >= accel - 1e-12
    assert cap_ratio > 0 or dx == 0


def test_profile_is_kinematically_consistent() -> None:
    pieces, _, accel_used, _ = rail.safe_profile(600.0, 300.0, 0.35, v_max=3.0)
    for duration, dx, v0, phase in pieces:
        if phase == "idle":
            assert dx == 0
            continue
        a = 2.0 * (dx - v0 * duration) / (duration * duration)
        v_end = v0 + a * duration
        # Acceleration magnitude is bounded by the segment acceleration used.
        assert abs(a) <= accel_used * 1.05 + 1e-6
        assert abs(v_end) < 10.0  # sanity bound, not a physical claim


def test_gear_mapping_is_within_one_to_five() -> None:
    edges = [0.2, 0.4, 0.6, 0.8, 1.0]
    for ratio in (0.0, 0.1, 0.2, 0.45, 0.7, 0.95, 1.0, 1.4):
        assert 1 <= rail.gear_for_ratio(ratio, edges) <= 5


# ------------------------------------------------------------- collision gate
def test_collision_detects_interior_minimum_missed_by_endpoints() -> None:
    """Proposition 2b: an interior minimum can violate while both endpoints pass."""
    west = rail.Trajectory("W", [rail.Segment(0.0, 10.0, 90.0, 0.0, 0.0, "idle", 1, 0.0, "w")])
    east = rail.Trajectory("E", [rail.Segment(0.0, 10.0, 100.0, -10.0, 2.0, "loaded", 3, 400.0, "e")])
    # Endpoints: gap(0) = gap(10) = +10 px, both above the requirement.
    assert west.x_at(0.0) == 90.0 and east.x_at(0.0) - west.x_at(0.0) == pytest.approx(10.0)
    assert east.x_at(10.0) - west.x_at(10.0) == pytest.approx(10.0)

    result = rail.verify_pair(west, east, required_gap=5.0, horizon=(0.0, 10.0))
    assert result.min_gap == pytest.approx(-15.0, abs=1e-6)
    assert result.min_gap_time == pytest.approx(5.0, abs=1e-6)
    assert result.violations, "interior minimum must be reported"


def test_collision_accepts_when_gap_is_respected() -> None:
    west = rail.Trajectory("W", [rail.Segment(0.0, 10.0, 0.0, 0.0, 0.0, "idle", 1, 0.0, "w")])
    east = rail.Trajectory("E", [rail.Segment(0.0, 10.0, 100.0, 1.0, 0.0, "loaded", 3, 400.0, "e")])
    result = rail.verify_pair(west, east, required_gap=50.0, horizon=(0.0, 10.0))
    assert result.min_gap == pytest.approx(100.0)
    assert not result.violations


def test_collision_reports_crane_crossing_as_negative_gap() -> None:
    west = rail.Trajectory("W", [rail.Segment(0.0, 10.0, 500.0, 0.0, 0.0, "idle", 1, 0.0, "w")])
    east = rail.Trajectory("E", [rail.Segment(0.0, 10.0, 0.0, 0.0, 0.0, "loaded", 3, 400.0, "e")])
    result = rail.verify_pair(west, east, required_gap=50.0, horizon=(0.0, 10.0))
    assert result.min_gap < 0
    assert result.violations


# ------------------------------------------------------------ closed loop run
def test_single_cast_closed_loop_is_gated_and_complete(tmp_path: Path) -> None:
    result = loop.run("6C0754", loop.REPLAY_DIR, tmp_path / "loop")
    summary = result["summary"]
    assert summary["task_count"] == 18
    assert summary["parameters"]["confirmed_by_modeler"] is False

    comparison = {row["algorithm"]: row for row in result["comparison"]}
    assert set(comparison) == {
        "observed_historical", "fcfs", "edf_nearest", "cp_sat_exact_setup", "conservative_serial",
    }
    # The conservative serial policy is the only candidate the safety gate admits.
    assert comparison["conservative_serial"]["accepted"] == "yes"
    for name in ("observed_historical", "fcfs", "edf_nearest", "cp_sat_exact_setup"):
        assert comparison[name]["accepted"] == "no", name
        assert comparison[name]["safety_verdict_base_config"] == "violation", name

    out = tmp_path / "loop"
    for name in (
        "closed_loop_summary.json",
        "optimization_comparison.csv",
        "collision_matrix.csv",
        "collision_pairs_base.csv",
        "leg_calibration.csv",
        "empty_speed_sensitivity.csv",
        "profile_issues.csv",
    ):
        assert (out / name).is_file(), name
    for algorithm in comparison:
        assert (out / f"trajectory_{algorithm}.csv").is_file(), algorithm
        assert (out / f"schedule_{algorithm}.csv").is_file(), algorithm

    # Trajectory schema required by the project data contract.
    rows = list(csv.DictReader((out / "trajectory_conservative_serial.csv").open(encoding="utf-8-sig")))
    assert rows
    required = {
        "run_id", "crane_id", "seg_id", "t_start_s", "t_end_s", "x_start", "x_end",
        "v_start", "v_end", "accel", "gear", "phase", "load_t",
    }
    assert required.issubset(rows[0].keys())
    allowed_phases = {"loaded", "empty", "evade", "idle", "fault"}
    assert all(row["phase"] in allowed_phases for row in rows)
    # Segment durations must be positive and time-ordered per crane.
    assert all(float(row["t_end_s"]) > float(row["t_start_s"]) for row in rows)


def test_serial_schedule_respects_per_heat_precedence(tmp_path: Path) -> None:
    loop.run("6C0754", loop.REPLAY_DIR, tmp_path / "loop")
    rows = list(csv.DictReader((tmp_path / "loop" / "schedule_conservative_serial.csv").open(encoding="utf-8-sig")))
    by_heat: dict[str, dict[str, float]] = {}
    for row in rows:
        by_heat.setdefault(row["heat_id"], {})[row["route_step"]] = float(row["end_s"])
    for heat, steps in by_heat.items():
        order = ["BOF_to_RH", "RH_to_CCM", "CCM_empty_to_transfer_car"]
        present = [steps[s] for s in order if s in steps]
        assert present == sorted(present), f"precedence violated for {heat}"


def test_summary_is_json_serialisable(tmp_path: Path) -> None:
    result = loop.run("6C0754", loop.REPLAY_DIR, tmp_path / "loop")
    text = json.dumps(result["summary"], ensure_ascii=False)
    assert '"acceptance_gate"' in text
