#!/usr/bin/env python3
"""Scenario-1 proactive-yielding engine on the locked metric geometry.

Physical truth: ``code/configs/ef_initial_v1.json`` (D = 12 m, heavy/empty/empty-hook
speed limits 0.60/0.80/1.00 m/s, per-state acceleration, station slow zones, zero
scenario-1 handling). No physical number is invented here.

Mechanism
---------
The simulator advances every crane at 1 s steps under the real speed and
acceleration limits, and enforces the shared-rail invariant continuously:
adjacent cranes never come closer than ``D`` metres. When a crane's route needs to
pass a neighbour, the neighbour - unless it is currently carrying a ladle -
executes a **proactive yielding retreat** and returns afterwards. That retreat is
the manoeuvre the earlier closed loop was missing.

Verification
------------
Simulated timings are re-integrated as piecewise constant-acceleration legs and
then handed to the independent exact verifier (``rail_model.verify_schedule``).
A plan that fails the gate is reported as rejected, never as feasible.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from ortools.sat.python import cp_model

import ef_model as ef
import rail_model as rail

ROOT = Path(__file__).resolve().parents[2]
REPLAY_DIR = ROOT / "data" / "runs" / "scene1_replay_6C0754"
OUT_ROOT = ROOT / "data" / "runs" / "scene1_yield_6C0754"

DT = 1.0
MARGIN_M = 0.4
YIELD_TRIGGER_S = 2.0
YIELD_CLEAR_M = 2.0

STATE_FOR_TYPE = {"heavy": ef.STATE_HEAVY, "empty": ef.STATE_EMPTY_LADLE}


def parse_ts(value: str) -> float:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()


# --------------------------------------------------------------------------- tasks
def build_tasks(geometry: ef.Geometry, replay_dir: Path = REPLAY_DIR) -> list[dict[str, Any]]:
    moves = pd.read_csv(replay_dir / "moves.csv")
    t_origin = min(parse_ts(v) for v in moves["pickup_time"])
    tasks: list[dict[str, Any]] = []
    for _, row in moves.iterrows():
        state = STATE_FOR_TYPE.get(row["task_type"], ef.STATE_HEAVY)
        x_from, x_to = geometry.x_of(row["origin"]), geometry.x_of(row["destination"])
        tasks.append({
            "task_id": row["task_id"],
            "heat_id": row["heat_id"],
            "route_step": row["route_step"],
            "task_type": row["task_type"],
            "state": state,
            "origin": row["origin"],
            "destination": row["destination"],
            "x_from": x_from,
            "x_to": x_to,
            "release_s": parse_ts(row["release_time"]) - t_origin,
            "deadline_s": parse_ts(row["deadline_time"]) - t_origin,
            "load_t": float(row["load_t"]),
            "travel_s": geometry.travel_seconds(x_from, x_to, state),
        })
    base = min(t["release_s"] for t in tasks)
    for task in tasks:
        task["release_s"] -= base
        task["deadline_s"] -= base
    for task in tasks:
        task["deadline_s"] = max(task["deadline_s"], task["release_s"] + task["travel_s"])
    tasks.sort(key=lambda t: (t["release_s"], t["task_id"]))
    return tasks


# ------------------------------------------------------------------------- simulator
@dataclass
class Leg:
    t0: float
    t1: float
    x0: float
    x1: float
    phase: str
    state: str
    task_id: str
    load_t: float


@dataclass
class CraneRun:
    crane_id: str
    x: float
    v: float = 0.0
    queue: list[dict[str, Any]] = field(default_factory=list)
    active: dict[str, Any] | None = None
    stage: str = "idle"           # idle | to_origin | wait_pickup | to_dest
    pickup_s: float = 0.0
    leg_t0: float = 0.0
    leg_x0: float = 0.0
    leg_state: str = ef.STATE_EMPTY_HOOK
    legs: list[Leg] = field(default_factory=list)
    done: list[dict[str, Any]] = field(default_factory=list)
    yield_target: float | None = None
    blocked_since: float | None = None
    yield_count: int = 0
    trace: list[tuple[float, float, float, str, str, float]] = field(default_factory=list)
    projection_shift_m: float = 0.0

    def __post_init__(self) -> None:
        # Leg bookkeeping must start at the crane's real initial position, not 0.
        self.leg_x0 = self.x
        self.leg_t0 = 0.0

    def goal(self) -> float | None:
        if self.yield_target is not None:
            return float(self.yield_target)
        if self.active is not None:
            if self.stage == "to_origin":
                return float(self.active["x_from"])
            if self.stage == "wait_pickup":
                return float(self.active["x_from"])
            if self.stage == "to_dest":
                return float(self.active["x_to"])
        return None

    def hauling(self) -> bool:
        return self.stage == "to_dest" and self.active is not None

    def state_now(self) -> str:
        if self.hauling() and self.active is not None:
            return STATE_FOR_TYPE.get(self.active["task_type"], ef.STATE_HEAVY)
        return ef.STATE_EMPTY_HOOK

    def open_leg(self, t: float) -> None:
        self.leg_t0 = t
        self.leg_x0 = self.x
        self.leg_state = self.state_now()

    def close_leg(self, t: float, phase: str, task_id: str, load_t: float) -> None:
        if t - self.leg_t0 <= 1e-9 and abs(self.x - self.leg_x0) <= 1e-9:
            return
        self.legs.append(Leg(self.leg_t0, max(t, self.leg_t0 + 1e-6), self.leg_x0, self.x,
                             phase, self.leg_state, task_id, load_t))


def priority(crane: CraneRun) -> tuple[float, int]:
    """Right-of-way key: smaller is stronger (earlier deadline first)."""
    if crane.active is not None:
        return (float(crane.active["deadline_s"]), 0)
    if crane.queue:
        return (float(crane.queue[0]["deadline_s"]), 1)
    return (float("inf"), 2)


def _ask_retreat(geometry: ef.Geometry, mover: CraneRun, other: CraneRun,
                 desired: float, t: float) -> None:
    """Ask ``other`` to move to ``desired``; never preempt a haul, never double-ask."""
    if other.hauling() or other.yield_target is not None:
        return
    if priority(mover) >= priority(other):
        return
    spec = geometry.crane(other.crane_id)
    target = min(max(desired, spec.reach_min_m), spec.reach_max_m)
    if abs(target - other.x) < 0.5:
        return
    other.yield_count += 1
    other.close_leg(t, "empty", "yield", 0.0)
    other.yield_target = target
    other.open_leg(t)


def simulate(
    geometry: ef.Geometry,
    tasks: list[dict[str, Any]],
    assignment: dict[str, str],
    allow_yield: bool = True,
    horizon_s: float = 60000.0,
) -> dict[str, Any]:
    ids = geometry.crane_ids()
    runs = {cid: CraneRun(cid, geometry.crane(cid).q0_m) for cid in ids}
    for task in tasks:
        runs[assignment[task["task_id"]]].queue.append(task)
    for run in runs.values():
        run.queue.sort(key=lambda t: (t["release_s"], t["task_id"]))

    gap = geometry.clear_distance_m + MARGIN_M
    total_travel = sum(task["travel_s"] for task in tasks)
    releases = sorted(task["release_s"] for task in tasks)
    max_release_gap = max((b - a for a, b in zip(releases, releases[1:])), default=0.0)
    idle_watchdog_s = max(7200.0, 2.0 * max_release_gap + 600.0)
    horizon_s = min(horizon_s, max(task["deadline_s"] for task in tasks) + 6 * total_travel + 7200)
    t = 0.0
    last_progress_t, last_progress_n = 0.0, 0
    deadlock = False
    while t < horizon_s:
        # start next task for free cranes
        for crane in runs.values():
            if crane.active is None and crane.queue:
                crane.active = crane.queue.pop(0)
                crane.stage = "to_origin"
                crane.yield_target = None
                crane.open_leg(t)

        targets: dict[str, float] = {}
        for idx, cid in enumerate(ids):
            crane = runs[cid]
            goal = crane.goal()
            state = crane.state_now()
            accel = geometry.accel_m_s2[state]
            cap = geometry.speed_cap(state, crane.x)
            if goal is None or abs(goal - crane.x) < 1e-9:
                crane.v = max(crane.v - accel * DT, 0.0)
                targets[cid] = crane.x
                continue
            direction = 1.0 if goal > crane.x else -1.0
            remaining = abs(goal - crane.x)
            v_want = min(cap, math.sqrt(max(2.0 * accel * remaining, 0.0)))
            v = crane.v
            if v < v_want:
                v = min(v + accel * DT, v_want)
            elif v > v_want:
                v = max(v - accel * DT, v_want)
            step = min(v * DT, remaining)
            if step <= 1e-9:
                v = 0.0
            target = crane.x + direction * step
            if direction > 0 and idx + 1 < len(ids):
                limit = runs[ids[idx + 1]].x - gap
                if target > limit:
                    target, v = max(crane.x, limit), 0.0
            if direction < 0 and idx > 0:
                limit = runs[ids[idx - 1]].x + gap
                if target < limit:
                    target, v = min(crane.x, limit), 0.0
            crane.v = v
            targets[cid] = target

        # proactive yielding: clear the whole corridor between mover and its goal
        if allow_yield:
            for idx, cid in enumerate(ids):
                crane = runs[cid]
                goal = crane.goal()
                if goal is None or abs(goal - crane.x) < 1e-9:
                    crane.blocked_since = None
                    continue
                if abs(targets[cid] - crane.x) > 1e-9:
                    crane.blocked_since = None      # still moving, nothing to clear
                    continue
                if crane.blocked_since is None:
                    crane.blocked_since = t
                    continue
                if t - crane.blocked_since < YIELD_TRIGGER_S:
                    continue
                step_gap = gap + YIELD_CLEAR_M
                if goal > crane.x:
                    prev: float | None = None
                    for k in range(idx + 1, len(ids)):
                        other = runs[ids[k]]
                        required = goal + step_gap if prev is None else prev + step_gap
                        planned = other.yield_target if other.yield_target is not None else other.x
                        if other.x < required - 0.5:
                            _ask_retreat(geometry, crane, other, required, t)
                            planned = other.yield_target if other.yield_target is not None else other.x
                        prev = max(required, planned)
                else:
                    prev = None
                    for k in range(idx - 1, -1, -1):
                        other = runs[ids[k]]
                        required = goal - step_gap if prev is None else prev - step_gap
                        planned = other.yield_target if other.yield_target is not None else other.x
                        if other.x > required + 0.5:
                            _ask_retreat(geometry, crane, other, required, t)
                            planned = other.yield_target if other.yield_target is not None else other.x
                        prev = min(required, planned)

        # ---- step cap first, then order-preserving projection (gap is exact)
        for cid in ids:
            crane = runs[cid]
            limit = geometry.speed_limit_m_s[crane.state_now()] * DT
            delta = targets[cid] - crane.x
            if abs(delta) > limit:
                targets[cid] = crane.x + math.copysign(limit, delta)
        gap_min = geometry.clear_distance_m + MARGIN_M
        for k in range(1, len(ids)):
            low = targets[ids[k - 1]] + gap_min
            if targets[ids[k]] < low:
                targets[ids[k]] = low
        for k in range(len(ids) - 1, -1, -1):
            spec = geometry.crane(ids[k])
            targets[ids[k]] = min(targets[ids[k]], spec.reach_max_m)
            if k + 1 < len(ids):
                high = targets[ids[k + 1]] - gap_min
                if targets[ids[k]] > high:
                    targets[ids[k]] = high
        for k in range(len(ids)):
            spec = geometry.crane(ids[k])
            if targets[ids[k]] < spec.reach_min_m:
                targets[ids[k]] = spec.reach_min_m
        for cid in ids:
            adjustment = abs(targets[cid] - runs[cid].x)
            if adjustment > 1.0 + 1e-9:
                runs[cid].projection_shift_m += adjustment
        # commit and detect arrivals
        for cid in ids:
            crane = runs[cid]
            prev_goal = crane.goal()
            crane.x = targets[cid]
            _phase = "loaded" if crane.hauling() else ("idle" if crane.stage in ("idle", "wait_pickup") else "empty")
            _tid = crane.active["task_id"] if crane.active is not None else "idle"
            _load = crane.active["load_t"] if crane.hauling() and crane.active is not None else 0.0
            crane.trace.append((t + DT, round(crane.x, 4), round(crane.v, 4), _phase, _tid, _load))
            if prev_goal is not None and abs(prev_goal - crane.x) <= 0.05:
                crane.x = prev_goal
                if crane.stage == "to_origin":
                    crane.close_leg(t + DT, "empty", f"{crane.active['task_id']}:reposition", 0.0)
                    crane.stage = "wait_pickup"
                    crane.open_leg(t + DT)
                elif crane.stage == "to_dest" and crane.active is not None:
                    task = crane.active
                    phase = "loaded" if task["task_type"] == "heavy" else "empty"
                    crane.close_leg(t + DT, phase, task["task_id"], task["load_t"])
                    crane.done.append({
                        "task_id": task["task_id"], "crane_id": cid,
                        "pickup_s": crane.pickup_s, "dropoff_s": t + DT,
                        "wait_s": crane.pickup_s - task["release_s"],
                        "lateness_s": max(0.0, (t + DT) - task["deadline_s"]),
                        "release_s": task["release_s"],
                    })
                    crane.active = None
                    crane.stage = "idle"
                    crane.open_leg(t + DT)
                elif crane.yield_target is not None:
                    crane.close_leg(t + DT, "empty", "yield", 0.0)
                    crane.yield_target = None
                    crane.open_leg(t + DT)

            if crane.stage == "wait_pickup" and crane.active is not None:
                if t + DT >= crane.active["release_s"] - 1e-9:
                    crane.pickup_s = t + DT
                    crane.close_leg(t + DT, "idle", f"{crane.active['task_id']}:wait_pickup", 0.0)
                    crane.stage = "to_dest"
                    crane.open_leg(t + DT)

        done_n = sum(len(r.done) for r in runs.values())
        if done_n != last_progress_n:
            last_progress_n, last_progress_t = done_n, t
        elif t - last_progress_t > idle_watchdog_s and not all(
                r.active is None and not r.queue for r in runs.values()):
            deadlock = True
            break
        if all(r.active is None and not r.queue and r.yield_target is None for r in runs.values()):
            break
        t += DT

    for crane in runs.values():
        crane.close_leg(t, "idle", "final", 0.0)

    return {
        "cranes": runs,
        "makespan_s": max((d["dropoff_s"] for r in runs.values() for d in r.done), default=0.0),
        "completed": sum(len(r.done) for r in runs.values()),
        "assignments": [d for r in runs.values() for d in r.done],
        "sim_end_s": t,
        "deadlock": deadlock,
        "yield_events": sum(r.yield_count for r in runs.values()),
        "projection_shift_m": round(sum(r.projection_shift_m for r in runs.values()), 2),
    }


# ------------------------------------------------------------------------ trajectory
def build_trajectories(geometry: ef.Geometry, sim: dict[str, Any],
                       compress: bool = False) -> dict[str, rail.Trajectory]:
    """Turn the simulator's per-step position log into a verified trajectory.

    One segment per simulation step is the only reconstruction that is exactly
    faithful to the simulated motion: each step is fitted with the acceleration
    that maps ``(x0, v0)`` onto ``(x1, t1)``. ``compress=True`` merges steps whose
    velocity did not change, which is readable for reporting but is not used for
    the safety gate.
    """
    trajectories: dict[str, rail.Trajectory] = {}
    cap = geometry.speed_limit_m_s[ef.STATE_HEAVY]
    for cid, crane in sim["cranes"].items():
        trace = [(0.0, geometry.crane(cid).q0_m, 0.0, "idle", "idle", 0.0)] + crane.trace
        segments: list[rail.Segment] = []
        i = 0
        while i < len(trace) - 1:
            t0, x0, v0, phase, task_id, load_t = trace[i]
            j = i + 1
            if compress:
                # Merge only when every step has the *same displacement*, so the
                # merged segment is exactly collinear with the logged positions.
                step_dx = trace[i + 1][1] - x0
                while (j < len(trace) - 1
                       and trace[j][3] == phase and trace[j][4] == task_id
                       and abs((trace[j + 1][1] - trace[j][1]) - step_dx) < 1e-9):
                    j += 1
            t1, x1, _, _, _, _ = trace[j]
            duration = t1 - t0
            if duration > 1e-9:
                accel = 2.0 * ((x1 - x0) - v0 * duration) / (duration * duration)
                ratio = abs(v0) / max(cap, 1e-9)
                gear = next((k + 1 for k, edge in enumerate(geometry.gear_ratios)
                             if ratio <= edge + 1e-12), len(geometry.gear_ratios))
                segments.append(rail.Segment(t0, t1, x0, v0, accel, phase, gear, load_t, task_id))
            i = j
        traj = rail.Trajectory(crane_id=cid)
        traj.segments = segments
        trajectories[cid] = traj
    return trajectories


def gate(geometry: ef.Geometry, trajectories: dict[str, rail.Trajectory]) -> dict[str, Any]:
    params = {"cranes": [{"id": c.id} for c in sorted(geometry.cranes, key=lambda c: c.order)]}
    horizon = (0.0, max(t.t_end for t in trajectories.values()))
    return rail.verify_schedule(params, trajectories, geometry.clear_distance_m, horizon)


# ------------------------------------------------------------------------ strategies
def assign_serial(geometry: ef.Geometry, tasks: list[dict[str, Any]], crane_id: str) -> dict[str, str]:
    return {task["task_id"]: crane_id for task in tasks}


def assign_fcfs_nearest(geometry: ef.Geometry, tasks: list[dict[str, Any]], service: list[str]) -> dict[str, str]:
    free = {cid: (geometry.crane(cid).q0_m, 0.0) for cid in service}
    assignment: dict[str, str] = {}
    for task in sorted(tasks, key=lambda t: (t["release_s"], t["task_id"])):
        best = None
        for cid in service:
            pos, avail = free[cid]
            reposition = geometry.travel_seconds(pos, task["x_from"], ef.STATE_EMPTY_HOOK)
            start = max(task["release_s"], avail + reposition)
            finish = start + task["travel_s"]
            if best is None or (finish, cid) < best[:2]:
                best = (finish, cid, start)
        assert best is not None
        finish, cid, start = best
        assignment[task["task_id"]] = cid
        free[cid] = (task["x_to"], finish)
    return assignment


def assign_pipeline(geometry: ef.Geometry, tasks: list[dict[str, Any]]) -> dict[str, str]:
    """Station-role pipeline: west crane feeds RH, next crane hauls east and returns."""
    feeder, hauler = "4#450", "3#450"
    assignment: dict[str, str] = {}
    for task in tasks:
        # West-feeder hauls BOF->RH; the middle crane hauls east and takes the
        # empty-ladle return. Putting the return on the eastern 5#450 would force
        # it to cross 3#450, which the rail order forbids.
        if task["route_step"] == "BOF_to_RH":
            assignment[task["task_id"]] = feeder
        else:
            assignment[task["task_id"]] = hauler
    return assignment


def assign_cpsat(geometry: ef.Geometry, tasks: list[dict[str, Any]], service: list[str],
                 time_limit_s: float = 15.0) -> dict[str, str] | None:
    horizon = int(max(max(t["deadline_s"] for t in tasks) + 7200, 20000))
    model = cp_model.CpModel()
    start, end, presence = {}, {}, {}
    for task in tasks:
        tid, dur = task["task_id"], int(math.ceil(task["travel_s"]))
        start[tid] = model.NewIntVar(int(max(task["release_s"], 0)), horizon, f"s_{tid}")
        end[tid] = model.NewIntVar(0, horizon, f"e_{tid}")
        model.Add(end[tid] == start[tid] + dur)
        for cid in service:
            presence[(tid, cid)] = model.NewBoolVar(f"p_{tid}_{cid}")
            lead = int(math.ceil(geometry.travel_seconds(geometry.crane(cid).q0_m, task["x_from"],
                                                         ef.STATE_EMPTY_HOOK)))
            model.Add(start[tid] >= lead).OnlyEnforceIf(presence[(tid, cid)])
        model.AddExactlyOne([presence[(tid, cid)] for cid in service])

    by_heat: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        by_heat.setdefault(task["heat_id"], []).append(task)
    for group in by_heat.values():
        group.sort(key=lambda t: t["release_s"])
        for prev, cur in zip(group, group[1:]):
            model.Add(start[cur["task_id"]] >= end[prev["task_id"]])

    for cid in service:
        for i, a in enumerate(tasks):
            for b in tasks[i + 1:]:
                both = [presence[(a["task_id"], cid)], presence[(b["task_id"], cid)]]
                order = model.NewBoolVar(f"o_{cid}_{a['task_id']}_{b['task_id']}")
                ab = int(math.ceil(geometry.travel_seconds(a["x_to"], b["x_from"], ef.STATE_EMPTY_HOOK)))
                ba = int(math.ceil(geometry.travel_seconds(b["x_to"], a["x_from"], ef.STATE_EMPTY_HOOK)))
                model.Add(start[b["task_id"]] >= end[a["task_id"]] + ab).OnlyEnforceIf(both + [order])
                model.Add(start[a["task_id"]] >= end[b["task_id"]] + ba).OnlyEnforceIf(both + [order.Not()])

    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, [end[t["task_id"]] for t in tasks])
    lateness = {t["task_id"]: model.NewIntVar(0, horizon, f"l_{t['task_id']}") for t in tasks}
    for task in tasks:
        model.Add(lateness[task["task_id"]] >= end[task["task_id"]] - int(task["deadline_s"]))
    model.Minimize(makespan + 5 * sum(lateness.values()))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {
        task["task_id"]: next(cid for cid in service if solver.BooleanValue(presence[(task["task_id"], cid)]))
        for task in tasks
    }


# ----------------------------------------------------------------------------- metrics
def summarise(name: str, geometry: ef.Geometry, sim: dict[str, Any], verdict: dict[str, Any],
              assignment: dict[str, str], expected_tasks: int) -> dict[str, Any]:
    rows = sim["assignments"]
    total_wait = sum(r["wait_s"] for r in rows) / 60.0
    total_late = sum(r["lateness_s"] for r in rows) / 60.0
    makespan = (max(r["dropoff_s"] for r in rows) - min(r["pickup_s"] for r in rows)) / 60.0 if rows else 0.0
    per_crane = sorted({r["crane_id"] for r in rows})
    return {
        "algorithm": name,
        "completed_tasks": len(rows),
        "cranes_used": ";".join(per_crane),
        "makespan_min": round(makespan, 2),
        "total_wait_min": round(total_wait, 2),
        "total_lateness_min": round(total_late, 2),
        "yield_events": sim["yield_events"],
        "min_gap_m": round(min(p["min_gap_px"] for p in verdict["pairs"]), 2),
        "required_gap_m": geometry.clear_distance_m,
        "gate": verdict["verdict"],
        "completed_all": len(rows) == expected_tasks,
        "deadlock": sim["deadlock"],
        # A plan is only accepted when it is both safe and complete.
        "accepted": verdict["verdict"] == "pass" and len(rows) == expected_tasks and not sim["deadlock"],
    }


def run(out_dir: Path = OUT_ROOT, replay_dir: Path = REPLAY_DIR,
        use_cpsat: bool = True) -> dict[str, Any]:
    geometry = ef.load_geometry()
    tasks = build_tasks(geometry, replay_dir)
    service = geometry.service_crane_ids()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[tuple[str, dict[str, str], bool]] = [
        ("pipeline_roles", assign_pipeline(geometry, tasks), True),
        ("pipeline_no_yield", assign_pipeline(geometry, tasks), False),
        ("fcfs_nearest", assign_fcfs_nearest(geometry, tasks, service), True),
    ]
    for cid in service:
        candidates.append((f"serial_{cid}", assign_serial(geometry, tasks, cid), True))
    if use_cpsat:
        cpsat = assign_cpsat(geometry, tasks, service)
        if cpsat:
            candidates.append(("cp_sat_assignment", cpsat, True))

    rows: list[dict[str, Any]] = []
    traced: dict[str, dict[str, Any]] = {}
    for name, assignment, allow_yield in candidates:
        sim = simulate(geometry, tasks, assignment, allow_yield=allow_yield)
        trajectories = build_trajectories(geometry, sim)       # 1 s resolution, certified
        verdict = gate(geometry, trajectories)
        row = summarise(name, geometry, sim, verdict, assignment, len(tasks))
        rows.append(row)
        traced[name] = {"sim": sim, "trajectories": trajectories, "verdict": verdict,
                        "assignment": assignment}

    write_csv(out_dir / "comparison_yield.csv", rows, list(rows[0].keys()))

    trace_rows: list[dict[str, Any]] = []
    for name, bundle in traced.items():
        for rec in bundle["sim"]["assignments"]:
            task = next(t for t in tasks if t["task_id"] == rec["task_id"])
            trace_rows.append({
                "algorithm": name, "task_id": rec["task_id"], "heat_id": task["heat_id"],
                "route_step": task["route_step"], "crane_id": rec["crane_id"],
                "pickup_s": round(rec["pickup_s"], 2), "dropoff_s": round(rec["dropoff_s"], 2),
                "wait_s": round(rec["wait_s"], 2), "lateness_s": round(rec["lateness_s"], 2),
                "x_from_m": task["x_from"], "x_to_m": task["x_to"],
            })
    write_csv(out_dir / "task_trace.csv", trace_rows,
              ["algorithm", "task_id", "heat_id", "route_step", "crane_id", "pickup_s",
               "dropoff_s", "wait_s", "lateness_s", "x_from_m", "x_to_m"])

    accepted = [r for r in rows if r["accepted"]]
    best = min(accepted, key=lambda r: (r["total_wait_min"], r["makespan_min"])) if accepted else None
    serial_rows = [r for r in rows if r["algorithm"].startswith("serial_")]
    serial_best = min(serial_rows, key=lambda r: r["total_wait_min"]) if serial_rows else None

    if best is not None:
        bundle = traced[best["algorithm"]]
        traj_rows: list[dict[str, Any]] = []
        for cid, traj in bundle["trajectories"].items():
            traj_rows.extend(traj.to_rows("scene1-yield-6C0754", "1_deterministic",
                                          best["algorithm"], 0.0))
        write_csv_gz(out_dir / "trajectory.csv.gz", traj_rows,
                     ["run_id", "scenario", "algorithm", "crane_id", "seg_id", "task_id",
                      "t_start_s", "t_end_s", "x_start", "x_end", "v_start", "v_end",
                      "accel", "gear", "phase", "load_t"])

    payload = {
        "run_id": "scene1-yield-6C0754",
        "geometry": {"version": geometry.version, "source": "code/configs/ef_initial_v1.json",
                     "clearance_m": geometry.clear_distance_m,
                     "speed_limit_m_s": geometry.speed_limit_m_s,
                     "accel_m_s2": geometry.accel_m_s2},
        "task_count": len(tasks),
        "legacy_serial_wait_min_previous_model": 109.48,
        "serial_best_this_model": serial_best,
        "best_accepted": best,
        "improvement_vs_best_serial_min": (round(serial_best["total_wait_min"] - best["total_wait_min"], 2)
                                           if serial_best and best else None),
        "candidates": rows,
        "verification": {
            "gate": "rail_model.verify_schedule, exact continuous-time, all five cranes",
            "resolution": "1 s certified trajectory; the reported verdict uses that resolution",
            "trajectory_artifact": "trajectory.csv.gz (gzip, fixed mtime); reload with read_trajectory_gz",
        },
        "notes": [
            "Scenario-1 durations are zero-handling kinematic travel per contract-1.",
            "Empty-ladle return is pure travel; slag/residual work is a separate operation.",
            "Proactive yielding is executed by the simulator; the exact gate re-verifies it.",
            "The 109.48 min figure came from the superseded observed-duration model and is not comparable.",
            "With zero handling, one crane already absorbs a single cast: crane capacity is not the binding "
            "resource for 6 heats, so the meaningful contention test is a full day (step 2).",
        ],
    }
    with (out_dir / "yield_summary.json").open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def write_csv_gz(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Write a gzip-compressed CSV with a fixed mtime so reruns are byte-stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    import gzip
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(buffer.getvalue().encode("utf-8"))


def read_trajectory_gz(path: Path) -> dict[str, rail.Trajectory]:
    """Reload a certified ``trajectory.csv.gz`` so the gate can be re-run offline."""
    import gzip

    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    trajectories: dict[str, rail.Trajectory] = {}
    for row in rows:
        traj = trajectories.setdefault(row["crane_id"], rail.Trajectory(crane_id=row["crane_id"]))
        traj.segments.append(rail.Segment(
            t_start=float(row["t_start_s"]), t_end=float(row["t_end_s"]),
            x_start=float(row["x_start"]), v_start=float(row["v_start"]),
            accel=float(row["accel"]), phase=row["phase"], gear=int(row["gear"]),
            load_t=float(row["load_t"]), task_id=row["task_id"],
        ))
    return trajectories


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-dir", type=Path, default=REPLAY_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_ROOT)
    args = parser.parse_args()
    payload = run(args.out_dir, args.replay_dir)
    for row in payload["candidates"]:
        print(f"{row['algorithm']:<20} wait={row['total_wait_min']:>7.2f} min  "
              f"makespan={row['makespan_min']:>7.2f}  gap={row['min_gap_m']:>7.2f} m  "
              f"yield={row['yield_events']:>3}  gate={row['gate']}")
    print("\nbest accepted:", json.dumps(payload["best_accepted"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
