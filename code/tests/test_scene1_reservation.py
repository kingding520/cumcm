from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

import ef_model as ef  # noqa: E402
import scene1_reservation as sr  # noqa: E402
import scene1_yield as sy  # noqa: E402

RUNS = Path(__file__).resolve().parents[2] / "data" / "runs" / "scene1_day"


@pytest.fixture(scope="module")
def geometry() -> ef.Geometry:
    return ef.load_geometry()


def test_single_cast_is_fully_scheduled_and_safe(geometry) -> None:
    tasks = sy.build_tasks(geometry)
    scheduler = sr.ReservationScheduler(geometry)
    result = scheduler.run(tasks)
    assert len(result["records"]) == len(tasks)
    verdict = sr.verify(geometry, sr.build_trajectories(geometry, result["cranes"]))
    assert verdict["verdict"] == "pass"
    assert min(p["min_gap_px"] for p in verdict["pairs"]) >= geometry.clear_distance_m


def test_moves_never_overlap_for_the_same_crane(geometry) -> None:
    scheduler = sr.ReservationScheduler(geometry)
    result = scheduler.run(sy.build_tasks(geometry))
    for crane in result["cranes"]:
        moves = sorted(crane.moves, key=lambda m: m["t0"])
        for prev, cur in zip(moves, moves[1:]):
            assert cur["t0"] >= prev["t1"] - 1e-6, f"{crane.crane_id} overlapping moves"
        # positions must chain: each move starts where the previous ended
        for prev, cur in zip(moves, moves[1:]):
            assert abs(cur["x0"] - prev["x1"]) < 1e-6, f"{crane.crane_id} broken position chain"


def test_order_is_preserved_at_every_move_end(geometry) -> None:
    scheduler = sr.ReservationScheduler(geometry)
    result = scheduler.run(sy.build_tasks(geometry))
    ids = [c.crane_id for c in result["cranes"]]
    end_positions = {c.crane_id: geometry.crane(c.crane_id).q0_m for c in result["cranes"]}
    timeline = sorted(
        [(m["t1"], c.crane_id, m) for c in result["cranes"] for m in c.moves],
        key=lambda item: item[0])
    for _, cid, move in timeline:
        end_positions[cid] = move["x1"]
        idx = ids.index(cid)
        if idx > 0:
            assert end_positions[cid] - end_positions[ids[idx - 1]] >= geometry.clear_distance_m - 1e-6
        if idx + 1 < len(ids):
            assert end_positions[ids[idx + 1]] - end_positions[cid] >= geometry.clear_distance_m - 1e-6


def test_day_sensitivity_artifacts_are_complete_and_safe() -> None:
    path = RUNS / "cart_layout_sensitivity.csv"
    if not path.is_file():
        pytest.skip("day sensitivity not generated")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 12
    assert {r["variant"] for r in rows} == {
        "BASE_3E", "COUNT_2E", "COUNT_4E", "WEST_3_80", "WEST_3_85", "WEST_3_90"}
    assert {r["day"] for r in rows} == {"2026-09-28", "2026-10-02"}
    for row in rows:
        assert row["gate"] == "pass"
        assert row["completed"] == row["tasks"]
        assert float(row["min_gap_m"]) >= 12.0
