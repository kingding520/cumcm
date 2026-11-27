#!/usr/bin/env python3
"""Whole-day scenario-1 run: multi-cast, three 450 t cranes, no virtual handoffs.

Builds the day's movement tasks from ``data/processed/moves.csv``, maps device
names to the locked metric geometry, and schedules them with the proactive-yield
engine in ``scene1_yield``. Parallelism comes from the plant itself: three casters
(1#/2#/3# CCM) run concurrently, so different heats need different cranes at the
same time - no artificial handoff point is inserted anywhere.

Cart layouts (massive-transport channel experiments, per the 05 confirmation):
``BASE_3E``  CART1=219, CART2=212, CART3=164
``COUNT_2E`` drops CART3, heats that used it fall back to CART2
``COUNT_4E`` adds CART4=190 as an extra eastern channel for CCM3 heats
``WEST_3_<x>`` moves CART3 to x in {80, 85, 90}
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import ef_model as ef
import rail_model as rail
import scene1_yield as sy
import scene1_reservation as sr

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT_ROOT = ROOT / "data" / "runs" / "scene1_day"
CONFIG_DIR = ROOT / "code" / "configs"
CAPACITY_POLICY_FILE = CONFIG_DIR / "capacity_policy_v1.json"
HEAVY_CAPACITY_T = 450.0


def load_capacity_policy(aux_threshold: float | None = None) -> dict[str, Any]:
    """Load the crane-capability policy; ``aux_threshold`` overrides the pending value."""
    policy = json.loads(CAPACITY_POLICY_FILE.read_text(encoding="utf-8"))
    if aux_threshold is not None:
        policy["aux_max_load_t"] = float(aux_threshold)
    return policy

CAPACITY_POLICY: dict[str, Any] = json.loads(CAPACITY_POLICY_FILE.read_text(encoding="utf-8"))

CART_LAYOUTS: dict[str, dict[str, Any]] = {
    "BASE_3E": {"carts": {"CART1": 219.0, "CART2": 212.0, "CART3": 164.0}, "remap": {}},
    "COUNT_2E": {"carts": {"CART1": 219.0, "CART2": 212.0}, "remap": {"CART3": "CART2"}},
    "COUNT_4E": {"carts": {"CART1": 219.0, "CART2": 212.0, "CART3": 164.0, "CART4": 190.0},
                 "remap": {"CCM3": "CART4"}},
}
for _x in (80.0, 85.0, 90.0):
    CART_LAYOUTS[f"WEST_3_{int(_x)}"] = {
        "carts": {"CART1": 219.0, "CART2": 212.0, "CART3": _x},
        "remap": {},
    }


def parse_ts(value: str) -> float:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()


def geometry_for(layout: dict[str, Any]) -> ef.Geometry:
    base = ef.load_geometry()
    stations = dict(base.stations)
    for cart, x in layout["carts"].items():
        stations[cart] = ef.Station(cart, float(x), True)
    slow = tuple(float(stations[s].x_m) for s in base.slow_zones and [] ) if False else base.slow_zones
    # carts are loading interfaces: they participate in the slow zone as well
    zones = set(base.slow_zones) | {float(x) for x in layout["carts"].values()}
    return ef.Geometry(
        version=f"{base.version}+{layout_name(layout)}",
        stations=stations, cranes=base.cranes,
        clear_distance_m=base.clear_distance_m,
        rail_min_m=base.rail_min_m, rail_max_m=base.rail_max_m,
        speed_limit_m_s=base.speed_limit_m_s, accel_m_s2=base.accel_m_s2,
        slow_zones=tuple(sorted(zones)), slow_half_width_m=base.slow_half_width_m,
        slow_ratio=base.slow_ratio, handling_s=base.handling_s, gear_ratios=base.gear_ratios,
    )


def layout_name(layout: dict[str, Any]) -> str:
    for name, spec in CART_LAYOUTS.items():
        if spec is layout:
            return name
    return "custom"


def day_tasks(geometry: ef.Geometry, moves: pd.DataFrame, layout: dict[str, Any]) -> list[dict[str, Any]]:
    base = min(parse_ts(v) for v in moves["release_time"])
    tasks: list[dict[str, Any]] = []
    for _, row in moves.iterrows():
        origin, destination = row["origin"], row["destination"]
        caster_station = row.get("caster_station") or ""
        if layout.get("remap", {}).get(caster_station):
            if destination.startswith("CART"):
                destination = layout["remap"][caster_station]
        if layout.get("remap", {}).get(destination):
            destination = layout["remap"][destination]
        if origin not in geometry.stations or destination not in geometry.stations:
            continue
        x_from, x_to = geometry.x_of(origin), geometry.x_of(destination)
        if row["phase_type"] == "SITE_OPERATION":
            state, hold = ef.STATE_EMPTY_LADLE, max(parse_ts(row["deadline_time"]) - parse_ts(row["release_time"]), 0.0)
        elif row["phase_type"] == "TRAVEL_HEAVY":
            state, hold = ef.STATE_HEAVY, 0.0
        else:
            state, hold = ef.STATE_EMPTY_LADLE, 0.0
        travel = geometry.travel_seconds(x_from, x_to, state)
        tasks.append({
            "task_id": row["task_id"], "heat_id": row["heat_id"], "cast_id": row["cast_id"],
            "route_step": row["route_step"], "task_type": row["phase_type"],
            "state": state, "origin": origin, "destination": destination,
            "x_from": x_from, "x_to": x_to,
            "release_s": parse_ts(row["release_time"]) - base,
            "deadline_s": parse_ts(row["deadline_time"]) - base,
            "load_t": float(row["load_t"] or 0.0), "travel_s": travel, "hold_s": hold,
        })
    for task in tasks:
        task["deadline_s"] = max(task["deadline_s"], task["release_s"] + task["travel_s"] + task["hold_s"])
    tasks.sort(key=lambda t: (t["release_s"], t["task_id"]))
    return tasks


def capable(crane: ef.Crane, task: dict[str, Any], policy: dict[str, Any] | None = None) -> bool:
    """Crane-capability gate driven by ``capacity_policy_v1.json``.

    Heavy ladle legs always need the 450 t cranes. The 150 t auxiliary cranes are
    only admitted when the modelling lead has supplied ``aux_max_load_t``; until then
    they stay out of service, which is why the default runs use three cranes.
    """
    policy = policy or CAPACITY_POLICY
    if task["task_type"] == "TRAVEL_HEAVY":
        return crane.capacity_t >= float(policy["heavy_min_capacity_t"])
    if crane.capacity_t >= float(policy["heavy_min_capacity_t"]):
        return True
    if crane.id not in policy.get("aux_cranes", []):
        return False
    if task["task_type"] not in policy.get("aux_allowed_task_types", []):
        return False
    limit = policy.get("aux_max_load_t")
    if limit is None:
        return False                      # threshold not decided yet -> aux disabled
    return float(task.get("load_t") or 0.0) <= float(limit)


def assign_nearest(geometry: ef.Geometry, tasks: list[dict[str, Any]],
                   policy: dict[str, Any] | None = None) -> dict[str, str]:
    policy = policy or CAPACITY_POLICY
    free = {c.id: (c.q0_m, 0.0) for c in geometry.cranes}
    assignment: dict[str, str] = {}
    for task in tasks:
        best = None
        for crane in geometry.cranes:
            if not capable(crane, task, policy):
                continue
            pos, avail = free[crane.id]
            reposition = geometry.travel_seconds(pos, task["x_from"], ef.STATE_EMPTY_HOOK)
            start = max(task["release_s"], avail + reposition)
            finish = start + task["travel_s"] + task["hold_s"]
            score = (finish, crane.order)
            if best is None or score < best[0]:
                best = (score, crane.id, task["x_to"], finish)
        if best is None:
            raise RuntimeError(f"no capable crane for {task['task_id']}")
        _, cid, end_x, finish = best
        assignment[task["task_id"]] = cid
        free[cid] = (end_x, finish)
    return assignment


def assign_serial(geometry: ef.Geometry, tasks: list[dict[str, Any]], crane_id: str) -> dict[str, str]:
    return {task["task_id"]: crane_id for task in tasks}


def summarise(name: str, geometry: ef.Geometry, tasks: list[dict[str, Any]], sim: dict[str, Any],
              verdict: dict[str, Any]) -> dict[str, Any]:
    rows = sim["assignments"]
    waits = [r["wait_s"] for r in rows]
    used = Counter(r["crane_id"] for r in rows)
    return {
        "variant": name,
        "tasks": len(tasks),
        "completed": len(rows),
        "cranes_used": ";".join(f"{k}:{v}" for k, v in sorted(used.items())),
        "makespan_h": round((max((r["dropoff_s"] for r in rows), default=0.0)) / 3600.0, 2),
        "total_wait_min": round(sum(waits) / 60.0, 1),
        "mean_wait_min": round(sum(waits) / len(waits) / 60.0, 2) if waits else 0.0,
        "p95_wait_min": round(sorted(waits)[int(0.95 * (len(waits) - 1))] / 60.0, 1) if waits else 0.0,
        "total_lateness_min": round(sum(r["lateness_s"] for r in rows) / 60.0, 1),
        "yield_events": sim["yield_events"],
        "min_gap_m": round(min(p["min_gap_px"] for p in verdict["pairs"]), 2),
        "aux_threshold_t": policy.get("aux_max_load_t"),
        "gate": verdict["verdict"],
        "completed_all": len(rows) == len(tasks),
        "deadlock": sim["deadlock"],
        "accepted": verdict["verdict"] == "pass" and len(rows) == len(tasks) and not sim["deadlock"],
    }


def run_day(day: str, layout_key: str = "BASE_3E", out_root: Path = OUT_ROOT,
            policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or CAPACITY_POLICY
    layout = CART_LAYOUTS[layout_key]
    geometry = geometry_for(layout)
    moves = pd.read_csv(PROCESSED / "moves.csv")
    heats = pd.read_csv(PROCESSED / "heats.csv")
    heats["day"] = pd.to_datetime(heats["plan_tap_start"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_heats = heats[(heats["day"] == day) & (heats["match_status"] == "MATCHED_UNIQUE")]
    day_moves = moves[moves["heat_id"].isin(day_heats["heat_id"])].copy()
    caster_by_heat = dict(zip(day_heats["heat_id"], day_heats["caster"].astype(str)))
    day_moves["caster_station"] = day_moves["heat_id"].map(
        lambda h: f"CCM{caster_by_heat.get(h, '')}" if caster_by_heat.get(h) else "")
    tasks = day_tasks(geometry, day_moves, layout)

    scheduler = sr.ReservationScheduler(geometry, capability=lambda crane, task: capable(crane, task, policy))
    result = scheduler.run(tasks)
    trajectories = sr.build_trajectories(geometry, result["cranes"])
    verdict = sr.verify(geometry, trajectories)
    records = result["records"]
    waits = [r["wait_s"] for r in records]
    used = Counter(r["crane_id"] for r in records)
    makespan_h = round(max((r["dropoff_s"] for r in records), default=0.0) / 3600.0, 2)
    row = {
        "variant": layout_key,
        "day": day,
        "tasks": len(tasks),
        "completed": len(records),
        "cranes_used": ";".join(f"{k}:{v}" for k, v in sorted(used.items())),
        "makespan_h": makespan_h,
        "overtime_h": round(max(0.0, makespan_h - 24.0), 2),
        "total_wait_min": round(sum(waits) / 60.0, 1),
        "mean_wait_min": round(sum(waits) / len(waits) / 60.0, 2) if waits else 0.0,
        "p95_wait_min": round(sorted(waits)[int(0.95 * (len(waits) - 1))] / 60.0, 1) if waits else 0.0,
        "total_lateness_min": round(sum(r["lateness_s"] for r in records) / 60.0, 1),
        "yield_moves": sum(1 for c in result["cranes"] for m in c.moves if m["task_id"] == "yield"),
        "rejected_trials": len(result["rejected"]),
        "min_gap_m": round(min(p["min_gap_px"] for p in verdict["pairs"]), 2),
        "aux_threshold_t": policy.get("aux_max_load_t"),
        "gate": verdict["verdict"],
        "completed_all": len(records) == len(tasks),
        "accepted": verdict["verdict"] == "pass" and len(records) == len(tasks),
    }
    out_dir = out_root / f"{day}_{layout_key}"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "day_comparison.csv", [row], list(row.keys()))
    if layout_key == "BASE_3E":
        rows = []
        for cid, traj in trajectories.items():
            rows.extend(traj.to_rows(f"scene1-day-{day}", "1_deterministic", layout_key, 0.0))
        sy.write_csv_gz(out_dir / "trajectory.csv.gz", rows,
                        ["run_id", "scenario", "algorithm", "crane_id", "seg_id", "task_id",
                         "t_start_s", "t_end_s", "x_start", "x_end", "v_start", "v_end",
                         "accel", "gear", "phase", "load_t"])
        write_csv(out_dir / "task_trace.csv",
                  [{"task_id": r["task_id"], "crane_id": r["crane_id"], "pickup_s": round(r["pickup_s"], 1),
                    "dropoff_s": round(r["dropoff_s"], 1), "wait_min": round(r["wait_s"] / 60, 2),
                    "late_min": round(r["lateness_s"] / 60, 2)} for r in records],
                  ["task_id", "crane_id", "pickup_s", "dropoff_s", "wait_min", "late_min"])
    payload = {"day": day, "cart_layout": layout_key, "heats": int(day_heats.shape[0]),
               "casts": int(day_heats["cast_id"].nunique()), "tasks": len(tasks),
               "geometry_version": geometry.version, "result": row}
    (out_dir / "day_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", action="append")
    parser.add_argument("--layout", action="append")
    parser.add_argument("--aux-threshold", type=float, default=None,
                        help="override aux_max_load_t (t); omit to keep the 150 t cranes idle")
    args = parser.parse_args()
    global CAPACITY_POLICY
    CAPACITY_POLICY = load_capacity_policy(args.aux_threshold)
    days = args.day or ["2026-09-28", "2026-10-02"]
    layouts = args.layout or ["BASE_3E"]
    for day in days:
        for layout in layouts:
            payload = run_day(day, layout)
            row = payload["result"]
            print(f"{day} {layout:<12} tasks={row['tasks']:>4} done={row['completed']:>4} "
                  f"mk={row['makespan_h']:>6.2f}h ot={row['overtime_h']:>5.2f} wait={row['total_wait_min']:>8.1f} "
                  f"late={row['total_lateness_min']:>7.1f} gap={row['min_gap_m']:>5.2f} gate={row['gate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
