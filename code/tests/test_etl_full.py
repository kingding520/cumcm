from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

CODE_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(CODE_SRC))

import etl_full as etl  # noqa: E402

PROCESSED = Path(__file__).resolve().parents[2] / "data" / "processed"


def read(name: str) -> list[dict]:
    path = PROCESSED / name
    if not path.is_file():
        pytest.skip(f"{name} not generated")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_contract_gate_values_are_read_from_config() -> None:
    contract = json.loads((Path(__file__).resolve().parents[2] / "code" / "configs" / "model_contract_v1.json").read_text(encoding="utf-8"))
    assert contract["matching"]["time_gate_s"] == 5400
    assert contract["matching"]["mass_residual_gate_t"] == 3


def test_route_is_derived_from_observations_not_the_plan() -> None:
    # every plan route in the workbook is a *0 variant, so it cannot express passes
    assert etl.route_stages("L0R0") == []
    assert etl.route_stages("R0") == []
    assert etl.route_stages("L1R1") == ["LF", "RH"]
    assert etl.route_stages("R1L1") == ["RH", "LF"]


def test_heats_table_is_complete_and_traceable() -> None:
    heats = read("heats.csv")
    assert len(heats) > 1200
    matched = [h for h in heats if h["match_status"] == "MATCHED_UNIQUE"]
    assert matched, "no matched heats"
    for heat in matched:
        assert heat["plan_source"].startswith("question/")
        assert heat["ops_source"] and "#row" in heat["ops_source"]
        assert heat["route"] in {"LF", "RH", "LF-RH", "RH-LF", ""}


def test_moves_use_the_three_stage_empty_return_split() -> None:
    moves = read("moves.csv")
    phases = {m["phase_type"] for m in moves}
    assert {"TRAVEL_HEAVY", "TRAVEL_TO_CART", "TRAVEL_TO_SITE", "SITE_OPERATION"} <= phases
    # a site operation must sit between the two travel legs of the same service
    for site in [m for m in moves if m["phase_type"] == "SITE_OPERATION"][:20]:
        siblings = [m for m in moves if m["parent_service_id"] == site["parent_service_id"]]
        assert any(s["phase_type"] == "TRAVEL_TO_SITE" for s in siblings)
        assert any(s["phase_type"] == "TRAVEL_TO_CART" for s in siblings)


def test_unresolved_heats_are_reported_not_dropped() -> None:
    heats = read("heats.csv")
    summary = json.loads((PROCESSED / "etl_summary.json").read_text(encoding="utf-8"))
    counts = summary["counts"]
    assert counts["heats"] == len(heats)
    assert counts["matched_unique"] + counts["ambiguous"] + counts["unresolved"] == counts["heats"]
    assert "match_reasons" in summary


def test_day_selection_is_deterministic_and_eligible() -> None:
    selection = json.loads((PROCESSED / "day_selection.json").read_text(encoding="utf-8"))
    roles = {row["role"]: row for row in selection["selection"]}
    assert set(roles) == {"normal", "peak"}
    assert roles["peak"]["matched_heats"] >= roles["normal"]["matched_heats"]
    assert roles["normal"]["casts_with_moves"] >= 1
    assert set(roles) == {"normal", "peak"}
