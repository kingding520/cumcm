from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

import ef_model as ef  # noqa: E402
import scene1_day as day_mod  # noqa: E402

RUNS = Path(__file__).resolve().parents[2] / "data" / "runs" / "scene1_aux"


@pytest.fixture(scope="module")
def geometry() -> ef.Geometry:
    return day_mod.geometry_for(day_mod.CART_LAYOUTS["BASE_3E"])


def test_policy_defaults_to_aux_disabled() -> None:
    policy = day_mod.load_capacity_policy()
    assert policy["aux_max_load_t"] is None
    assert policy["status"] == "awaiting_wj_weight_threshold"


def test_aux_crane_is_rejected_without_threshold(geometry) -> None:
    policy = day_mod.load_capacity_policy()
    light = geometry.crane("2#150")
    heavy = geometry.crane("4#450")
    empty_task = {"task_type": "TRAVEL_TO_CART", "load_t": 50.0}
    heavy_task = {"task_type": "TRAVEL_HEAVY", "load_t": 400.0}
    assert day_mod.capable(light, empty_task, policy) is False
    assert day_mod.capable(light, heavy_task, policy) is False
    assert day_mod.capable(heavy, heavy_task, policy) is True
    assert day_mod.capable(heavy, empty_task, policy) is True


def test_aux_threshold_gates_by_recorded_load(geometry) -> None:
    light = geometry.crane("2#150")
    policy = day_mod.load_capacity_policy(120.0)
    assert day_mod.capable(light, {"task_type": "TRAVEL_TO_CART", "load_t": 100.0}, policy) is True
    assert day_mod.capable(light, {"task_type": "TRAVEL_TO_CART", "load_t": 130.0}, policy) is False
    # heavy ladle legs never go to a 150 t crane, whatever the threshold
    assert day_mod.capable(light, {"task_type": "TRAVEL_HEAVY", "load_t": 50.0}, policy) is False


def test_aux_sweep_artifacts_are_labelled_placeholder() -> None:
    path = RUNS / "aux_sensitivity.json"
    if not path.is_file():
        pytest.skip("aux sweep not generated")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] in {"awaiting_wj_weight_threshold", "contractual"}
    assert payload["rows"]
    for row in payload["rows"]:
        assert row["gate"] == "pass"
        assert row["completed"] == row["tasks"]
