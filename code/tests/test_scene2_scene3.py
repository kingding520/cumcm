from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pytest

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

import ef_model as ef  # noqa: E402
import scene1_day as day_mod  # noqa: E402
import scene2_stochastic as s2  # noqa: E402
import scene3_disruption as s3  # noqa: E402

RUNS2 = Path(__file__).resolve().parents[2] / "data" / "runs" / "scene2"
RUNS3 = Path(__file__).resolve().parents[2] / "data" / "runs" / "scene3"


@pytest.fixture(scope="module")
def geometry() -> ef.Geometry:
    return day_mod.geometry_for(day_mod.CART_LAYOUTS["BASE_3E"])


def test_analytic_travel_formula_branches() -> None:
    v, a = 0.6, 0.05
    short = 1.0                      # L <= v^2/a -> 2*sqrt(L/a)
    assert s2.travel_time_analytic(short, v, a) == pytest.approx(2 * math.sqrt(short / a))
    long = 100.0
    assert s2.travel_time_analytic(long, v, a) == pytest.approx(long / v + v / a)
    assert s2.travel_time_analytic(0.0, v, a) == 0.0


def test_gear_envelope_brakes_before_slow_zone(geometry) -> None:
    envelope = s2.gear_envelope(geometry, 99.0, 181.5, ef.STATE_HEAVY)
    assert envelope["braking_fits"] is True
    assert 1 <= envelope["gear"] <= len(geometry.gear_ratios)
    assert envelope["cruise_m_s"] <= geometry.speed_limit_m_s[ef.STATE_HEAVY] + 1e-9
    # the analytic lower bound cannot exceed the slow-zone-aware integration
    assert envelope["analytic_s"] <= envelope["integrated_s"] + 0.05


def test_duration_factors_normalise_to_one() -> None:
    table, raw = s2.fit_duration_factors(["2026-09-28"])
    assert not table.empty
    normalised = s2.normalise(raw)
    for key, values in normalised.items():
        assert abs(sum(values) / len(values) - 1.0) < 1e-9, key


def test_scene2_artifacts_have_required_scenarios() -> None:
    files = sorted(RUNS2.glob("scene2_summary_*.json"))
    if not files:
        pytest.skip("scene2 not generated")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    contract = json.loads((Path(__file__).resolve().parents[2] / "code" / "configs" / "model_contract_v1.json").read_text(encoding="utf-8"))
    assert summary["optimisation_scenarios"] == contract["scene2"]["optimization_scenarios"]
    assert summary["evaluation_scenarios"] == contract["scene2"]["evaluation_scenarios"]
    assert summary["seed"] == contract["scene2"]["seed"]
    assert summary["alpha"] == pytest.approx(contract["scene2"]["cvar_alpha"])
    assert {"mean_plan", "saa_cvar"} <= set(summary["policies"])
    for policy in summary["policies"].values():
        assert policy["cvar90_lateness_min"] >= policy["mean_lateness_min"] - 1e-9


def test_scene2_zero_noise_degenerates_to_deterministic() -> None:
    files = sorted(RUNS2.glob("scene2_summary_*.json"))
    if not files:
        pytest.skip("scene2 not generated")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    check = summary["degenerate_zero_noise"]
    if check["matches_scene1_day"] is None:
        pytest.skip("scenario-1 day reference not generated")
    assert check["matches_scene1_day"] is True


def test_scene3_fault_families_cover_the_contract() -> None:
    contract = json.loads((Path(__file__).resolve().parents[2] / "code" / "configs" / "model_contract_v1.json").read_text(encoding="utf-8"))
    assert contract["scene3"]["failure_duration_s"] == [900, 1800, 3600]
    assert contract["scene3"]["ready_delay_s"] == [300, 600, 1200]
    assert contract["scene3"]["repair_time_visibility"] == "observed_estimate_only"


def test_scene3_artifacts_report_every_fault_family() -> None:
    files = sorted(RUNS3.glob("scene3_summary_*.json"))
    if not files:
        pytest.skip("scene3 not generated")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    kinds = {row["kind"] for row in summary["scenarios"]}
    assert {"none", "crane", "refining", "converter", "caster", "ready_delay", "rail_break"} <= kinds
    for row in summary["scenarios"]:
        assert row["status"] in {"FOUND_FEASIBLE", "PROVEN_INFEASIBLE", "TIME_LIMIT_UNKNOWN"}
        assert 0.0 <= row["service_rate"] <= 1.0


def test_scene3_crane_and_rail_faults_reduce_service() -> None:
    files = sorted(RUNS3.glob("scene3_summary_*.json"))
    if not files:
        pytest.skip("scene3 not generated")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    rows = {row["kind"]: row for row in summary["scenarios"] if row["kind"] != "none"}
    cranes = [r for k, r in rows.items() if k == "crane"]
    assert cranes and cranes[0]["service_rate"] < 1.0
    rail = [r for k, r in rows.items() if k == "rail_break"]
    assert rail and rail[0]["service_rate"] < 1.0


def test_scene3_three_level_recovery_is_recorded() -> None:
    files = sorted(RUNS3.glob("scene3_summary_*.json"))
    if not files:
        pytest.skip("scene3 not generated")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    for row in summary["scenarios"]:
        assert row["recovery_level"] in {1, 2, 3}
        assert row["substitution_tried"] >= row["substituted_tasks"]
        # a substitution is only committed when it actually reduces window violations
        if row["recovery_level"] == 2:
            assert row["substituted_tasks"] > 0


def test_scene3_refining_faults_evaluate_substitution() -> None:
    files = sorted(RUNS3.glob("scene3_summary_*.json"))
    if not files:
        pytest.skip("scene3 not generated")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    refining = [r for r in summary["scenarios"] if r["kind"] == "refining"]
    assert refining
    # every refining outage must have been offered a compatible device
    assert all(r["substitution_tried"] > 0 for r in refining)


def test_rolling_execution_is_deterministic_and_capacity_aware(geometry) -> None:
    tasks, _ = s2.make_tasks(geometry, "2026-09-28")
    scenario = {"tap": 1.15, "slag": 1.1}
    first = s2.rolling_execute(tasks, scenario)
    second = s2.rolling_execute(tasks, scenario)
    assert [r["task_id"] for r in first] == [r["task_id"] for r in second]
    assert [round(r["dropoff_s"], 6) for r in first] == [round(r["dropoff_s"], 6) for r in second]
    heavy = {t["task_id"] for t in tasks if t["task_type"] == "TRAVEL_HEAVY"}
    light_cranes = {c.id for c in geometry.cranes if c.capacity_t < 450.0}
    for record in first:
        if record["task_id"] in heavy:
            assert record["crane_id"] not in light_cranes


def test_rolling_beats_open_loop_on_the_same_scenario(geometry) -> None:
    tasks, _ = s2.make_tasks(geometry, "2026-09-28")
    scenario = {"tap": 1.2, "slag": 1.2}
    open_loop = s2.replay(s2.plan_under(tasks, 1.0), tasks, scenario)
    rolling = s2.rolling_execute(tasks, scenario)
    open_late = sum(r["lateness_s"] for r in open_loop) / 60.0
    rolling_late = sum(r["lateness_s"] for r in rolling) / 60.0
    assert rolling_late < open_late


def test_scene2_reports_rolling_policies() -> None:
    files = sorted(RUNS2.glob("scene2_summary_*.json"))
    if not files:
        pytest.skip("scene2 not generated")
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    assert {"rolling_mean", "rolling_risk"} <= set(summary["policies"])
    for name in ("rolling_mean", "rolling_risk"):
        assert summary["policies"][name]["mean_lateness_min"] >= 0.0
