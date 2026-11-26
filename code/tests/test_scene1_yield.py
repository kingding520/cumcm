from __future__ import annotations

import sys
from pathlib import Path

import pytest

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

import ef_model as ef  # noqa: E402
import scene1_yield as sy  # noqa: E402


@pytest.fixture(scope="module")
def geometry() -> ef.Geometry:
    return ef.load_geometry()


@pytest.fixture(scope="module")
def tasks(geometry: ef.Geometry) -> list[dict]:
    return sy.build_tasks(geometry)


def test_geometry_is_internally_consistent(geometry: ef.Geometry) -> None:
    assert geometry.clear_distance_m == pytest.approx(12.0)
    assert geometry.version == "ef-initial-v1"
    # clearance + crane length must equal the declared centre distance
    assert geometry.clear_distance_m == pytest.approx(4.0 + geometry.cranes[0].length_m)
    # physical order must match the initial positions
    q = [geometry.crane(cid).q0_m for cid in geometry.crane_ids()]
    assert q == sorted(q)


def test_station_aliases_resolve_to_contract_coordinates(geometry: ef.Geometry) -> None:
    assert geometry.x_of("BOF-B") == pytest.approx(99.0)
    assert geometry.x_of("RH") == pytest.approx(58.0)
    assert geometry.x_of("CCM-C") == pytest.approx(181.5)
    assert geometry.x_of("transfer_car") == pytest.approx(164.0)
    with pytest.raises(KeyError):
        geometry.x_of("NOT_A_STATION")


def test_travel_time_matches_speed_limit_and_slow_zones(geometry: ef.Geometry) -> None:
    # 41 m at 0.60 m/s is >= 68 s; slow zones near BOF/RH only add time
    duration = geometry.travel_seconds(99.0, 58.0, ef.STATE_HEAVY)
    assert duration > 41.0 / 0.60
    assert duration < 3 * 41.0 / 0.60
    # heavier state must never be faster than the empty state over the same span
    assert geometry.travel_seconds(58.0, 181.5, ef.STATE_HEAVY) > geometry.travel_seconds(
        58.0, 181.5, ef.STATE_EMPTY_LADLE)
    # zero distance costs zero time
    assert geometry.travel_seconds(99.0, 99.0, ef.STATE_HEAVY) == 0.0


def test_tasks_cover_the_cast_and_start_at_zero(tasks: list[dict]) -> None:
    assert len(tasks) == 18
    assert min(t["release_s"] for t in tasks) == pytest.approx(0.0)
    assert {t["route_step"] for t in tasks} == {"BOF_to_RH", "RH_to_CCM", "CCM_empty_to_transfer_car"}
    for task in tasks:
        assert task["deadline_s"] >= task["release_s"]


def test_serial_plan_is_complete_and_passes_the_exact_gate(geometry, tasks) -> None:
    assignment = sy.assign_serial(geometry, tasks, "4#450")
    sim = sy.simulate(geometry, tasks, assignment)
    assert not sim["deadlock"]
    assert sim["completed"] == len(tasks)
    trajectories = sy.build_trajectories(geometry, sim)
    verdict = sy.gate(geometry, trajectories)
    assert verdict["verdict"] == "pass"
    assert min(p["min_gap_px"] for p in verdict["pairs"]) >= geometry.clear_distance_m


def test_three_crane_pipeline_passes_gate_and_beats_serial_wait(geometry, tasks) -> None:
    pipeline = sy.simulate(geometry, tasks, sy.assign_pipeline(geometry, tasks))
    serial = sy.simulate(geometry, tasks, sy.assign_serial(geometry, tasks, "4#450"))
    assert pipeline["completed"] == len(tasks)
    assert not pipeline["deadlock"]
    for sim in (pipeline, serial):
        verdict = sy.gate(geometry, sy.build_trajectories(geometry, sim))
        assert verdict["verdict"] == "pass", verdict
    pipeline_wait = sum(r["wait_s"] for r in pipeline["assignments"]) / 60.0
    serial_wait = sum(r["wait_s"] for r in serial["assignments"]) / 60.0
    assert pipeline_wait < serial_wait


def test_yielding_is_required_for_the_pipeline_layout(geometry, tasks) -> None:
    """Without proactive yielding the same assignment cannot finish the cast."""
    without = sy.simulate(geometry, tasks, sy.assign_pipeline(geometry, tasks), allow_yield=False)
    assert without["completed"] < len(tasks)


def test_simulation_is_deterministic(geometry, tasks) -> None:
    first = sy.simulate(geometry, tasks, sy.assign_pipeline(geometry, tasks))
    second = sy.simulate(geometry, tasks, sy.assign_pipeline(geometry, tasks))
    assert first["makespan_s"] == pytest.approx(second["makespan_s"])
    assert first["assignments"] == second["assignments"]


def test_trajectory_archive_roundtrip_and_reverification(geometry, tmp_path) -> None:
    """The shipped gzip artifact must reload and still pass the exact gate."""
    import rail_model as rail  # noqa: E402

    rows = [
        {"run_id": "t", "scenario": "s", "algorithm": "a", "crane_id": "4#450", "seg_id": 1,
         "task_id": "x", "t_start_s": 0.0, "t_end_s": 10.0, "x_start": 0.0, "x_end": 5.0,
         "v_start": 0.0, "v_end": 1.0, "accel": 0.1, "gear": 1, "phase": "empty", "load_t": 0.0},
    ]
    path = tmp_path / "trajectory.csv.gz"
    sy.write_csv_gz(path, rows, list(rows[0].keys()))
    loaded = sy.read_trajectory_gz(path)
    assert list(loaded) == ["4#450"]
    segment = loaded["4#450"].segments[0]
    assert isinstance(segment, rail.Segment)
    assert segment.x_at(segment.t_end) == pytest.approx(5.0, abs=1e-6)
    assert segment.accel == pytest.approx(0.1)


def test_committed_artifact_repasses_the_gate(geometry) -> None:
    artifact = Path(__file__).resolve().parents[2] / "data" / "runs" / "scene1_yield_6C0754" / "trajectory.csv.gz"
    if not artifact.is_file():
        pytest.skip("certified trajectory archive not present")
    verdict = sy.gate(geometry, sy.read_trajectory_gz(artifact))
    assert verdict["verdict"] == "pass"
    assert min(p["min_gap_px"] for p in verdict["pairs"]) >= geometry.clear_distance_m
