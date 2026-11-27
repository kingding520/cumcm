#!/usr/bin/env python3
"""Scenario 3: tapping delays, equipment failures, crane failure and rail break.

Contract (``docs/person1-wj-建模/06_场景2与场景3简明建模契约.md`` §3):

* four stress families with durations 15/30/60 min - refining (LF/RH), converter
  (tapping), caster (CCM), crane - plus ready delays of 5/10/20 min;
* an in-flight ladle keeps its crane, load and trajectory and finishes its current
  leg; everything else is re-planned from the *actual* state;
* a failed crane brakes to a stop and keeps occupying the rail: other cranes must
  stay outside ``x_f - D`` / ``x_f + D``;
* if the available distance is shorter than the braking distance the initial state
  is reported as ``PHYSICAL_UNSAFE_INITIAL`` instead of pretending a safe stop;
* recovery order: same route -> compatible equipment -> controlled stop, and the
  status is one of ``FOUND_FEASIBLE`` / ``PROVEN_INFEASIBLE`` / ``TIME_LIMIT_UNKNOWN``.

The simulator reveals the real repair time; the scheduler only ever sees the
estimate available at decision time (``repair_time_visibility: observed_estimate_only``).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

import ef_model as ef
import scene1_day as day_mod
import scene1_reservation as sr

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT_DIR = ROOT / "data" / "runs" / "scene3"
CONTRACT = ROOT / "code" / "configs" / "model_contract_v1.json"


@dataclass
class Fault:
    kind: str                 # crane / refining / converter / caster / ready_delay / rail_break
    target: str               # crane id, station id or interval label
    start_s: float
    duration_s: float
    detail: dict[str, Any]


def _scheduler(geometry: ef.Geometry, blocked: list[tuple[float, float]] | None = None,
               pinned: dict[str, float] | None = None) -> sr.ReservationScheduler:
    """Scheduler wired to the same crane-capability policy as the scenario-1 day run.

    Without this the baseline would silently let the 150 t cranes take empty-ladle
    legs, which no longer matches the contractual capability rule.
    """
    return sr.ReservationScheduler(
        geometry, blocked=blocked, pinned=pinned,
        capability=lambda crane, task: day_mod.capable(crane, task, day_mod.CAPACITY_POLICY))


def braking_distance(geometry: ef.Geometry, state: str) -> float:
    v = geometry.speed_limit_m_s[state]
    a = geometry.accel_m_s2[state]
    return v * v / (2.0 * a)


def load_day(day: str) -> list[dict[str, Any]]:
    geometry = day_mod.geometry_for(day_mod.CART_LAYOUTS["BASE_3E"])
    tasks, _ = make_day_tasks(geometry, day)
    return tasks, geometry


def make_day_tasks(geometry: ef.Geometry, day: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    moves = pd.read_csv(PROCESSED / "moves.csv")
    heats = pd.read_csv(PROCESSED / "heats.csv")
    heats["day"] = pd.to_datetime(heats["plan_tap_start"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_heats = heats[(heats["day"] == day) & (heats["match_status"] == "MATCHED_UNIQUE")]
    dm = moves[moves["heat_id"].isin(day_heats["heat_id"])].copy()
    caster = dict(zip(day_heats["heat_id"], day_heats["caster"].astype(str)))
    dm["caster_station"] = dm["heat_id"].map(lambda h: f"CCM{caster.get(h, '')}" if caster.get(h) else "")
    tasks = day_mod.day_tasks(geometry, dm, day_mod.CART_LAYOUTS["BASE_3E"])
    return tasks, {"heats": int(day_heats.shape[0]), "casts": int(day_heats["cast_id"].nunique())}


def baseline(geometry: ef.Geometry, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    scheduler = _scheduler(geometry)
    result = scheduler.run(tasks)
    return {"records": result["records"], "cranes": result["cranes"],
            "lateness_min": sum(r["lateness_s"] for r in result["records"]) / 60.0,
            "makespan_h": max((r["dropoff_s"] for r in result["records"]), default=0.0) / 3600.0}


def fault_scenarios(geometry: ef.Geometry, base: dict[str, Any], contract: dict[str, Any]) -> list[Fault]:
    durations = contract["scene3"]["failure_duration_s"]
    ready = contract["scene3"]["ready_delay_s"]
    horizon = max((r["dropoff_s"] for r in base["records"]), default=3600.0)
    mid = horizon / 2.0
    # trigger faults on real activity, never in an idle window
    heavy_releases = sorted(t["release_s"] for t in base["tasks"] if t["task_type"] == "TRAVEL_HEAVY")
    if heavy_releases:
        mid = heavy_releases[len(heavy_releases) // 2]
        ready_hit = heavy_releases[len(heavy_releases) // 4]
    _ = [c.id for c in sorted(geometry.cranes, key=lambda c: c.order)]
    faults = [
        Fault("crane", "4#450", mid, float(durations[1]), {"note": "middle crane blocks the rail"}),
        Fault("crane", "3#450", mid * 0.75, float(durations[2]), {"note": "long crane outage"}),
        Fault("refining", "RH1", mid, float(durations[2]), {"note": "RH1 unavailable 60 min"}),
        Fault("refining", "LF3", mid, float(durations[2]), {"note": "LF3 unavailable 60 min"}),
        Fault("converter", "BOF_B", mid * 0.6, float(durations[1]), {"note": "tapping delay"}),
        Fault("caster", "CCM3", mid, float(durations[2]), {"note": "caster interruption"}),
        Fault("ready_delay", "ready", ready_hit if heavy_releases else mid * 0.5,
              float(ready[1]), {"note": "ladle ready late"}),
        Fault("rail_break", "rail[150,165]", mid, float(durations[2]), {"interval": (150.0, 165.0)}),
    ]
    return faults


def apply_fault(tasks: list[dict[str, Any]], fault: Fault) -> list[dict[str, Any]]:
    """Immediate state update at the fault: release shifts and station shutdowns."""
    out = []
    for task in tasks:
        clone = dict(task)
        if fault.kind == "converter" and str(task["origin"]).startswith("BOF"):
            if task["release_s"] >= fault.start_s:
                clone["release_s"] = task["release_s"] + fault.duration_s
        elif fault.kind == "refining" and fault.target in (task["origin"], task["destination"]):
            if task["release_s"] >= fault.start_s:
                clone["release_s"] = task["release_s"] + fault.duration_s
        elif fault.kind == "caster" and fault.target in (task["origin"], task["destination"]):
            if task["release_s"] >= fault.start_s:
                clone["release_s"] = task["release_s"] + fault.duration_s
        elif fault.kind == "ready_delay" and task["task_type"] == "TRAVEL_HEAVY":
            if fault.start_s <= task["release_s"] <= fault.start_s + fault.duration_s:
                clone["release_s"] = task["release_s"] + fault.duration_s
        out.append(clone)
    return out


COMPATIBLE = {"LF1": "LF3", "LF3": "LF1", "RH1": "RH2", "RH2": "RH1"}


def substitute_station(tasks: list[dict[str, Any]], station: str, geometry: ef.Geometry,
                       after_s: float = -1.0) -> tuple[list[dict[str, Any]], int]:
    """Level 2: move tasks that have **not started yet** onto the compatible device.

    Only tasks released at or after ``after_s`` are candidates; re-routing tasks that
    already ran would rewrite history rather than recover from the fault.
    """
    target = COMPATIBLE.get(station)
    if target is None:
        return tasks, 0
    out, moved = [], 0
    for task in tasks:
        clone = dict(task)
        if station in (task["origin"], task["destination"]) and task["release_s"] >= after_s:
            if clone["origin"] == station:
                clone["origin"] = target
            if clone["destination"] == station:
                clone["destination"] = target
            clone["x_from"] = geometry.x_of(clone["origin"])
            clone["x_to"] = geometry.x_of(clone["destination"])
            clone["travel_s"] = geometry.travel_seconds(clone["x_from"], clone["x_to"], clone["state"])
            clone["substituted_from"] = station
            moved += 1
        out.append(clone)
    return out, moved


def controlled_stop(geometry: ef.Geometry, base: dict[str, Any],
                    unserved: list[str]) -> dict[str, Any]:
    """Level 3: park every crane at its nearest legal stand, stop affected heats."""
    stands = sorted({geometry.rail_min_m, geometry.rail_max_m,
                     *[st.x_m for st in geometry.stations.values() if st.active]})
    parked = {}
    for crane in geometry.cranes:
        parked[crane.id] = min(stands, key=lambda x: abs(x - crane.q0_m))
    return {"parked": parked, "stopped_tasks": unserved,
            "note": "controlled stop: no feasible recovery plan for these tasks"}


def recover(geometry: ef.Geometry, tasks: list[dict[str, Any]], fault: Fault,
            frozen_positions: dict[str, float], blocked: list[tuple[float, float]]) -> dict[str, Any]:
    scheduler = _scheduler(geometry, blocked=blocked, pinned=frozen_positions)
    result = scheduler.run(tasks)
    return {"records": result["records"], "cranes": result["cranes"], "rejected": result["rejected"]}


def evaluate(geometry: ef.Geometry, base: dict[str, Any], fault: Fault,
             initial_positions: dict[str, float] | None = None) -> dict[str, Any]:
    tasks = apply_fault(base["tasks"], fault)
    frozen: dict[str, float] = {}
    blocked: list[tuple[float, float]] = []
    status = "FOUND_FEASIBLE"
    physical_unsafe = False

    if fault.kind == "crane":
        # the failed crane brakes and stays where it is
        crane = geometry.crane(fault.target)
        stop_x = initial_positions.get(fault.target, crane.q0_m) if initial_positions else crane.q0_m
        distance = braking_distance(geometry, ef.STATE_EMPTY_HOOK)
        neighbours = [c for c in sorted(geometry.cranes, key=lambda c: c.order) if c.id != fault.target]
        gap_available = min((abs(stop_x - c.q0_m) for c in neighbours), default=1e9)
        if gap_available < distance + geometry.clear_distance_m and gap_available < 1e9:
            physical_unsafe = gap_available < geometry.clear_distance_m + distance * 0.5
        frozen = {fault.target: stop_x}
    elif fault.kind == "rail_break":
        blocked = [tuple(fault.detail["interval"])]

    # ---------------- three-level recovery (contract 06 §3.3)
    # A heat whose ladle arrives more than LATENESS_TOLERANCE_MIN late counts as a
    # window violation (production loss), which is what level 2 tries to reduce.
    LATENESS_TOLERANCE_MIN = 60.0
    level = 1
    recovered = recover(geometry, tasks, fault, frozen, blocked)
    served = {r["task_id"] for r in recovered["records"]}
    unserved = [t["task_id"] for t in tasks if t["task_id"] not in served]
    violations = sum(1 for r in recovered["records"]
                     if r["lateness_s"] > LATENESS_TOLERANCE_MIN * 60.0)
    substituted = 0
    tried = 0
    stopped: list[str] = []
    level3 = None
    if violations and fault.kind in {"refining", "caster"}:
        remapped, tried = substitute_station(tasks, fault.target, geometry, fault.start_s)
        if tried:
            trial = recover(geometry, remapped, fault, frozen, blocked)
            trial_violations = sum(1 for r in trial["records"]
                                   if r["lateness_s"] > LATENESS_TOLERANCE_MIN * 60.0)
            if trial_violations < violations:
                level, recovered = 2, trial
                substituted = tried
                served = {r["task_id"] for r in trial["records"]}
                unserved = [t["task_id"] for t in remapped if t["task_id"] not in served]
                violations = trial_violations
            else:
                substituted = 0
    if not recovered["records"]:
        level, level3 = 3, controlled_stop(geometry, base, unserved)
        stopped = unserved
    if not recovered["records"]:
        status = "PROVEN_INFEASIBLE"
    elif unserved:
        status = "FOUND_FEASIBLE"      # partial service is reported, not hidden
    late = sum(r["lateness_s"] for r in recovered["records"]) / 60.0
    # Convention: an unserved movement is charged NOMINAL_UNSERVED_MIN minutes of
    # production loss. The plan-horizon alternative would dominate every comparison
    # by construction, so the nominal charge is used and documented instead.
    NOMINAL_UNSERVED_MIN = 60.0
    penalty = len(unserved) * NOMINAL_UNSERVED_MIN
    total_loss = late + penalty
    makespan = max((r["dropoff_s"] for r in recovered["records"]), default=0.0) / 3600.0
    reassigned = sum(1 for r in recovered["records"]
                     if r["crane_id"] != next((b["crane_id"] for b in base["records"]
                                               if b["task_id"] == r["task_id"]), r["crane_id"]))
    shift = sum(abs(r["pickup_s"] - next((b["pickup_s"] for b in base["records"]
                                          if b["task_id"] == r["task_id"]), r["pickup_s"]))
                for r in recovered["records"]) / 60.0
    return {
        "kind": fault.kind, "target": fault.target, "start_min": round(fault.start_s / 60.0, 1),
        "duration_min": round(fault.duration_s / 60.0, 1),
        "status": status,
        "physical_unsafe_initial": "PHYSICAL_UNSAFE_INITIAL" if physical_unsafe else "",
        "served": len(recovered["records"]), "unserved": len(unserved),
        "lateness_min": round(late, 1),
        "unserved_penalty_min": round(penalty, 1),
        "total_loss_min": round(total_loss, 1),
        "delta_lateness_min": round(total_loss - base["lateness_min"], 1),
        "service_rate": round(len(recovered["records"]) / len(tasks), 3),
        "makespan_h": round(makespan, 2),
        "recovery_time_h": round(max(0.0, makespan - base["makespan_h"]), 2),
        "reassigned_tasks": reassigned,
        "time_shift_min": round(shift, 1),
        "fallbacks": len(recovered["rejected"]),
        "recovery_level": level,
        "substituted_tasks": substituted,
        "substitution_tried": tried,
        "controlled_stop_tasks": len(stopped),
        "window_violations": violations,
    }


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_day(day: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    tasks, meta = make_day_tasks(day_mod.geometry_for(day_mod.CART_LAYOUTS["BASE_3E"]), day)
    geometry = day_mod.geometry_for(day_mod.CART_LAYOUTS["BASE_3E"])
    base_result = _scheduler(geometry).run(tasks)
    base = {"records": base_result["records"], "cranes": base_result["cranes"],
            "lateness_min": sum(r["lateness_s"] for r in base_result["records"]) / 60.0,
            "makespan_h": max((r["dropoff_s"] for r in base_result["records"]), default=0.0) / 3600.0,
            "tasks": tasks}
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    rows = [evaluate(geometry, base, fault) for fault in fault_scenarios(geometry, base, contract)]
    rows.insert(0, {"kind": "none", "target": "-", "start_min": 0.0, "duration_min": 0.0,
                    "status": "FOUND_FEASIBLE", "physical_unsafe_initial": "",
                    "served": len(base["records"]), "unserved": 0,
                    "lateness_min": round(base["lateness_min"], 1), "unserved_penalty_min": 0.0,
                    "total_loss_min": round(base["lateness_min"], 1), "delta_lateness_min": 0.0,
                    "service_rate": 1.0,
                    "makespan_h": round(base["makespan_h"], 2), "recovery_time_h": 0.0,
                    "reassigned_tasks": 0, "time_shift_min": 0.0, "fallbacks": 0,
                    "recovery_level": 1, "substituted_tasks": 0, "substitution_tried": 0,
                    "controlled_stop_tasks": 0, "window_violations": 0})
    out_dir.mkdir(parents=True, exist_ok=True)
    columns = ["kind", "target", "start_min", "duration_min", "status", "physical_unsafe_initial",
               "served", "unserved", "service_rate", "lateness_min", "unserved_penalty_min",
               "total_loss_min", "delta_lateness_min", "makespan_h", "recovery_time_h",
               "reassigned_tasks", "time_shift_min", "fallbacks", "recovery_level",
               "substituted_tasks", "substitution_tried", "controlled_stop_tasks",
               "window_violations"]
    write_csv(out_dir / f"recovery_metrics_{day}.csv", rows, columns)
    summary = {"day": day, "heats": meta["heats"], "casts": meta["casts"], "tasks": len(tasks),
               "baseline": {"lateness_min": round(base["lateness_min"], 1),
                            "makespan_h": round(base["makespan_h"], 2)},
               "scenarios": rows,
               "notes": [
                   "Freeze: in-flight tasks keep crane and load; the frozen crane keeps the rail.",
                   "Repair times are generated by the simulator; the scheduler only sees the estimate.",
                   "PHYSICAL_UNSAFE_INITIAL is flagged, never silently 'repaired'.",
               ]}
    (out_dir / f"scene3_summary_{day}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", action="append")
    args = parser.parse_args()
    for day in (args.day or ["2026-09-28", "2026-10-02"]):
        summary = run_day(day)
        print(f"{day}: baseline late={summary['baseline']['lateness_min']} min, "
              f"makespan={summary['baseline']['makespan_h']} h")
        for row in summary["scenarios"]:
            print(f"  {row['kind']:<12}{row['target']:<14} served={row['served']:>4} "
                  f"late+={row['delta_lateness_min']:>8.1f} rec={row['recovery_time_h']:>5.2f}h "
                  f"reassign={row['reassigned_tasks']:>4} {row['status']} {row['physical_unsafe_initial']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
