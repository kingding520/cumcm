#!/usr/bin/env python3
"""Reservation-based scenario-1 scheduler on the locked metric rail.

Why this exists
---------------
The reactive yield simulator (``scene1_yield.simulate``) works for a single cast
but jams at day scale: retreating "away from the requestor" can push several
cranes into a rail end where no legal retreat point remains. This module instead
*plans every move explicitly*, so a corridor is cleared before it is used and an
infeasible clearance is detected up front instead of becoming a deadlock.

Semantics (contract 04 §4.3, "先让后行")
---------------------------------------
1. A crane may only traverse the open interval between its current position and
   its target if every other crane sits at least ``D`` outside that interval
   (cranes cannot pass each other, so the physical order is preserved).
2. Clearance moves are themselves scheduled as ordinary single-crane moves, east
   side first when pushing east (so each retreating crane has room).
3. Each move reserves a time window and a corridor. Two moves may overlap in time
   only if their corridors are disjoint, otherwise the later one waits.
4. If a corridor cannot be cleared inside the rail and reach limits, the dispatch
   falls back to a crane that can do the job alone, and finally to waiting for the
   next release event (04 §4.3.4) — never to a silent violation.

Output: an explicit move list per crane, converted into a piecewise
constant-acceleration trajectory and re-checked by ``rail_model.verify_schedule``.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ef_model as ef
import rail_model as rail

ROOT = Path(__file__).resolve().parents[2]
HEAVY_CAPACITY_T = 450.0
EPS = 1e-6


@dataclass
class CranePlan:
    crane_id: str
    order_index: int
    x: float
    capacity_t: float
    free_at: float = 0.0
    moves: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Reservation:
    t0: float
    t1: float
    lo: float
    hi: float
    crane_id: str


def travel(geometry: ef.Geometry, x0: float, x1: float, state: str) -> float:
    return geometry.travel_seconds(x0, x1, state)


def corridor(x0: float, x1: float, gap: float) -> tuple[float, float]:
    lo, hi = min(x0, x1), max(x0, x1)
    return lo - gap, hi + gap


class ReservationScheduler:
    def __init__(self, geometry: ef.Geometry, blocked: list[tuple[float, float]] | None = None,
                 pinned: dict[str, float] | None = None,
                 capability: Any = None,
                 initial_positions: dict[str, float] | None = None) -> None:
        self.geometry = geometry
        # Scenario-3 hooks: intervals a crane may not enter or cross, and cranes
        # frozen at a position (a failed crane keeps occupying the rail).
        self.blocked = list(blocked or [])
        self.pinned = dict(pinned or {})
        # Optional capability gate: capability(crane, task) -> bool. When absent the
        # default rule (450 t for loaded legs, anyone else) applies.
        self.capability = capability
        self.cranes = [
            CranePlan(c.id, idx, geometry.crane(c.id).q0_m, geometry.crane(c.id).capacity_t)
            for idx, c in enumerate(sorted(geometry.cranes, key=lambda c: c.order))
        ]
        if initial_positions:
            for crane in self.cranes:
                if crane.crane_id in initial_positions:
                    crane.x = float(initial_positions[crane.crane_id])
        self.by_id = {c.crane_id: c for c in self.cranes}
        self.reservations: list[Reservation] = []
        self.rejected: list[dict[str, Any]] = []
        # planning margin absorbs projection/rounding so the certified gap never dips
        # below the contractual centre distance
        self.gap = geometry.clear_distance_m + 0.5

    # ------------------------------------------------------------------ helpers
    def _inside_blocked(self, x: float, gap: float) -> bool:
        """True when ``x`` is inside or too close to a blocked rail interval."""
        for lo, hi in self.blocked:
            if lo - gap <= x <= hi + gap:
                return True
        return False

    def _move_crosses_blocked(self, x0: float, x1: float) -> bool:
        lo, hi = min(x0, x1), max(x0, x1)
        for b_lo, b_hi in self.blocked:
            if not (hi < b_lo or lo > b_hi):
                return True
        return False

    def _neighbours(self, idx: int) -> tuple[CranePlan | None, CranePlan | None]:
        west = self.cranes[idx - 1] if idx > 0 else None
        east = self.cranes[idx + 1] if idx + 1 < len(self.cranes) else None
        return west, east

    def _limits(self, idx: int) -> tuple[float, float]:
        west, east = self._neighbours(idx)
        lo = (west.x + self.gap) if west else self.geometry.rail_min_m
        hi = (east.x - self.gap) if east else self.geometry.rail_max_m
        spec = self.geometry.crane(self.cranes[idx].crane_id)
        return max(lo, spec.reach_min_m), min(hi, spec.reach_max_m)

    def _reservation_conflict(self, t0: float, t1: float, lo: float, hi: float) -> float | None:
        """Latest end time among reservations that conflict with this window."""
        latest = None
        for res in self.reservations:
            if res.t1 <= t0 + EPS or res.t0 >= t1 - EPS:
                continue
            if res.hi <= lo + EPS or res.lo >= hi - EPS:
                continue
            latest = res.t1 if latest is None else max(latest, res.t1)
        return latest

    def _schedule_move(self, idx: int, x_to: float, state: str, phase: str, task_id: str,
                       load_t: float, earliest: float) -> dict[str, Any] | None:
        crane = self.cranes[idx]
        x_from = crane.x
        if abs(x_to - x_from) < EPS:
            return {"crane_id": crane.crane_id, "t0": earliest, "t1": earliest,
                    "x0": x_from, "x1": x_to, "phase": phase, "state": state,
                    "task_id": task_id, "load_t": load_t}
        lo, hi = corridor(x_from, x_to, self.gap)
        duration = travel(self.geometry, x_from, x_to, state)
        t0 = max(earliest, crane.free_at)
        for _ in range(200):
            conflict = self._reservation_conflict(t0, t0 + duration, lo, hi)
            if conflict is None:
                break
            t0 = conflict
        move = {"crane_id": crane.crane_id, "t0": t0, "t1": t0 + duration,
                "x0": x_from, "x1": x_to, "phase": phase, "state": state,
                "task_id": task_id, "load_t": load_t}
        self.reservations.append(Reservation(t0, t0 + duration, lo, hi, crane.crane_id))
        crane.moves.append(move)
        crane.x = x_to
        crane.free_at = t0 + duration
        return move

    def _ensure_slot(self, idx: int, target: float, t: float, depth: int = 0) -> float | None:
        """Push neighbours out of the way so crane ``idx`` may occupy ``target``."""
        if depth > len(self.cranes) + 1:
            return None
        spec = self.geometry.crane(self.cranes[idx].crane_id)
        if target < spec.reach_min_m - EPS or target > spec.reach_max_m + EPS:
            return None
        gap = self.gap
        if idx + 1 < len(self.cranes) and target > self.cranes[idx + 1].x - gap:
            t = self._place(idx + 1, target + gap, t, depth + 1)
            if t is None:
                return None
        if idx - 1 >= 0 and target < self.cranes[idx - 1].x + gap:
            t = self._place(idx - 1, target - gap, t, depth + 1)
            if t is None:
                return None
        return t

    def _place(self, idx: int, target: float, t: float, depth: int = 0) -> float | None:
        """Recursively push neighbours so crane ``idx`` may legally occupy ``target``.

        This is the 1-D sliding rule: a crane can take a slot only if its neighbours
        stay at least ``D`` away, and pushing a neighbour may require pushing the
        next one. Every push is an ordinary scheduled move, so the resulting plan
        is executable and preserves the physical order by construction.
        """
        if depth > len(self.cranes) + 1:
            return None
        spec = self.geometry.crane(self.cranes[idx].crane_id)
        if target < spec.reach_min_m - EPS or target > spec.reach_max_m + EPS:
            return None
        gap = self.gap
        if idx + 1 < len(self.cranes) and target > self.cranes[idx + 1].x - gap:
            t = self._place(idx + 1, target + gap, t, depth + 1)
            if t is None:
                return None
        if idx - 1 >= 0 and target < self.cranes[idx - 1].x + gap:
            t = self._place(idx - 1, target - gap, t, depth + 1)
            if t is None:
                return None
        ready = self._ensure_slot(idx, target, t, depth)
        if ready is None:
            return None
        move = self._schedule_move(idx, target, ef.STATE_EMPTY_HOOK, "empty", "yield", 0.0, ready)
        return None if move is None else move["t1"]

    # ------------------------------------------------------------------ dispatch
    def feasible(self, idx: int, target: float) -> bool:
        lo, hi = self._limits(idx)
        return lo - EPS <= target <= hi + EPS

    def schedule_task(self, task: dict[str, Any], capable: list[int]) -> dict[str, Any] | None:
        """Try each capable crane; return the accepted dispatch record."""
        best: dict[str, Any] | None = None
        for idx in capable:
            snapshot = self._snapshot()
            ok = self._attempt(idx, task)
            if ok is not None and (best is None or ok["finish_s"] < best["finish_s"]):
                best = ok
            # a trial may move *other* cranes during clearance, so restore everything
            self._restore(snapshot)
            if ok is None:
                self.rejected.append({"task_id": task["task_id"], "crane": self.cranes[idx].crane_id,
                                      "reason": "clearance_infeasible"})
        if best is None:
            return None
        idx = next(i for i, c in enumerate(self.cranes) if c.crane_id == best["crane_id"])
        committed = self._attempt(idx, task)
        return committed

    def _snapshot(self) -> tuple:
        return ([(c.x, c.free_at, len(c.moves)) for c in self.cranes], len(self.reservations))

    def _restore(self, snapshot: tuple) -> None:
        states, reservation_len = snapshot
        for crane, (x, free_at, move_len) in zip(self.cranes, states):
            crane.x = x
            crane.free_at = free_at
            del crane.moves[move_len:]
        del self.reservations[reservation_len:]

    def _attempt(self, idx: int, task: dict[str, Any]) -> dict[str, Any] | None:
        crane = self.cranes[idx]
        start = max(crane.free_at, task["release_s"])
        ready = self._place(idx, task["x_from"], start)
        if ready is None:
            return None
        if abs(self.cranes[idx].x - task["x_from"]) > EPS:
            # _place already moved it to the slot; a further reposition is only a
            # no-op, so the pickup window opens at the placement completion.
            ready = self.cranes[idx].free_at
        to_origin = {"t1": ready}
        hold_until = max(to_origin["t1"], task["release_s"])
        crane.free_at = hold_until
        ready = self._ensure_slot(idx, task["x_to"], hold_until)
        if ready is None:
            return None
        crane.free_at = max(crane.free_at, ready)
        state = task["state"]
        haul = self._schedule_move(idx, task["x_to"], state, "loaded" if task["task_type"] == "TRAVEL_HEAVY" else "empty",
                                   task["task_id"], task["load_t"], crane.free_at)
        if haul is None:
            return None
        dropoff = haul["t1"] + float(task.get("hold_s", 0.0))
        crane.free_at = dropoff
        return {"crane_id": crane.crane_id, "task_id": task["task_id"],
                "pickup_s": haul["t0"], "dropoff_s": dropoff,
                "wait_s": max(0.0, haul["t0"] - task["release_s"]),
                "lateness_s": max(0.0, dropoff - task["deadline_s"]),
                "finish_s": dropoff}

    def _plan_task(self, positions: list[float], clock: float, idx: int,
                   task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[float], float] | None:
        """Plan one task on a hypothetical state; returns (moves, positions, clock)."""
        pos = list(positions)
        moves: list[dict[str, Any]] = []
        gap = self.gap
        t = clock

        def slot(j: int, target: float, depth: int = 0) -> float | None:
            """Recursively clear neighbours; returns the time when the slot is free."""
            nonlocal t
            if depth > len(pos) + 1:
                return None
            if self.cranes[j].crane_id in self.pinned:
                return None
            if self._inside_blocked(target, gap):
                return None
            spec = self.geometry.crane(self.cranes[j].crane_id)
            if target < spec.reach_min_m - EPS or target > spec.reach_max_m + EPS:
                return None
            if j + 1 < len(pos) and target > pos[j + 1] - gap:
                if slot(j + 1, target + gap, depth + 1) is None:
                    return None
            if j - 1 >= 0 and target < pos[j - 1] + gap:
                if slot(j - 1, target - gap, depth + 1) is None:
                    return None
            need = target + gap
            if j + 1 < len(pos) and pos[j + 1] < need:
                dur = travel(self.geometry, pos[j + 1], need, ef.STATE_EMPTY_HOOK)
                moves.append({"crane_id": self.cranes[j + 1].crane_id, "t0": t, "t1": t + dur,
                              "x0": pos[j + 1], "x1": need, "phase": "empty",
                              "state": ef.STATE_EMPTY_HOOK, "task_id": "yield", "load_t": 0.0})
                pos[j + 1] = need
                t += dur
            need_w = target - gap
            if j - 1 >= 0 and pos[j - 1] > need_w:
                dur = travel(self.geometry, pos[j - 1], need_w, ef.STATE_EMPTY_HOOK)
                moves.append({"crane_id": self.cranes[j - 1].crane_id, "t0": t, "t1": t + dur,
                              "x0": pos[j - 1], "x1": need_w, "phase": "empty",
                              "state": ef.STATE_EMPTY_HOOK, "task_id": "yield", "load_t": 0.0})
                pos[j - 1] = need_w
                t += dur
            return t

        if self.cranes[idx].crane_id in self.pinned:
            return None
        if slot(idx, task["x_from"]) is None:
            return None
        dur = travel(self.geometry, pos[idx], task["x_from"], ef.STATE_EMPTY_HOOK)
        moves.append({"crane_id": self.cranes[idx].crane_id, "t0": t, "t1": t + dur,
                      "x0": pos[idx], "x1": task["x_from"], "phase": "empty",
                      "state": ef.STATE_EMPTY_HOOK, "task_id": f"{task['task_id']}:reposition",
                      "load_t": 0.0})
        pos[idx] = task["x_from"]
        t += dur
        if slot(idx, task["x_to"]) is None:
            return None
        # the corridor must be clear before the loaded leg starts, and the pickup
        # cannot precede the task release
        pickup = max(t, task["release_s"])
        dur = travel(self.geometry, pos[idx], task["x_to"], task["state"])
        moves.append({"crane_id": self.cranes[idx].crane_id, "t0": pickup, "t1": pickup + dur,
                      "x0": pos[idx], "x1": task["x_to"],
                      "phase": "loaded" if task["task_type"] == "TRAVEL_HEAVY" else "empty",
                      "state": task["state"], "task_id": task["task_id"], "load_t": task["load_t"]})
        pos[idx] = task["x_to"]
        dropoff = pickup + dur + float(task.get("hold_s", 0.0))
        return moves, pos, dropoff

    def run(self, tasks: list[dict[str, Any]], strategy: str = "edf") -> dict[str, Any]:
        """Strictly time-ordered greedy dispatch (planning order == execution order).

        Every move is appended after the current clock, so positions at planning
        time are exactly the positions at execution time. That removes the stale
        bookkeeping that could otherwise let a crane slide past its neighbour.
        """
        positions = [c.x for c in self.cranes]
        clock = 0.0
        records: list[dict[str, Any]] = []
        for task in sorted(tasks, key=lambda t: (t["release_s"], t["deadline_s"], t["task_id"])):
            if self.capability is not None:
                capable = [i for i, c in enumerate(self.cranes)
                           if self.capability(self.geometry.crane(c.crane_id), task)]
            else:
                capable = [i for i, c in enumerate(self.cranes)
                           if c.capacity_t >= HEAVY_CAPACITY_T or task["task_type"] != "TRAVEL_HEAVY"]
            best = None
            for idx in capable:
                planned = self._plan_task(positions, clock, idx, task)
                if planned is None:
                    continue
                moves, new_pos, finish = planned
                if best is None or finish < best["finish"]:
                    best = {"idx": idx, "moves": moves, "pos": new_pos, "finish": finish}
            if best is None:
                self.rejected.append({"task_id": task["task_id"], "reason": "no_feasible_crane"})
                continue
            for move in best["moves"]:
                crane = next(c for c in self.cranes if c.crane_id == move["crane_id"])
                crane.moves.append(move)
            positions = best["pos"]
            clock = max(best["finish"], max(m["t1"] for m in best["moves"]))
            haul = next((m for m in best["moves"] if m["task_id"] == task["task_id"]), None)
            if haul is None:
                continue
            records.append({"crane_id": haul["crane_id"], "task_id": task["task_id"],
                            "pickup_s": haul["t0"], "dropoff_s": best["finish"],
                            "wait_s": max(0.0, haul["t0"] - task["release_s"]),
                            "lateness_s": max(0.0, best["finish"] - task["deadline_s"]),
                            "finish_s": best["finish"]})
        for crane, x in zip(self.cranes, positions):
            crane.x = x
        return {"records": records, "cranes": self.cranes, "rejected": self.rejected,
                "reservations": self.reservations}


# --------------------------------------------------------------------- trajectory
def build_trajectories(geometry: ef.Geometry, cranes: list[CranePlan]) -> dict[str, rail.Trajectory]:
    out: dict[str, rail.Trajectory] = {}
    cap_max = max(geometry.speed_limit_m_s.values())
    for crane in cranes:
        traj = rail.Trajectory(crane_id=crane.crane_id)
        cursor_t, cursor_x = 0.0, geometry.crane(crane.crane_id).q0_m
        for move in sorted(crane.moves, key=lambda m: m["t0"]):
            if move["t0"] > cursor_t + EPS:
                traj.segments.append(rail.Segment(cursor_t, move["t0"], cursor_x, 0.0, 0.0,
                                                  "idle", 1, 0.0, "idle"))
            duration = max(move["t1"] - move["t0"], 1e-3)
            if abs(move["x1"] - move["x0"]) < EPS:
                traj.segments.append(rail.Segment(move["t0"], move["t1"], move["x0"], 0.0, 0.0,
                                                  "idle", 1, 0.0, move["task_id"]))
            else:
                cap = geometry.speed_limit_m_s[move["state"]]
                pieces, _, _, _ = rail.safe_profile(move["x1"] - move["x0"], duration,
                                                    geometry.accel_m_s2[move["state"]], cap)
                t, x = move["t0"], move["x0"]
                for dur, dx, v0, kind in pieces:
                    if dur <= 1e-9:
                        continue
                    accel = 0.0 if kind == "idle" else 2.0 * (dx - v0 * dur) / (dur * dur)
                    ratio = abs(v0) / max(cap_max, 1e-9)
                    gear = next((k + 1 for k, edge in enumerate(geometry.gear_ratios)
                                 if ratio <= edge + 1e-12), len(geometry.gear_ratios))
                    traj.segments.append(rail.Segment(t, t + dur, x, v0, accel, move["phase"],
                                                      gear, move["load_t"], move["task_id"]))
                    x += dx
                    t += dur
            cursor_t = max(move["t1"], cursor_t)
            cursor_x = move["x1"]
        traj.segments.append(rail.Segment(cursor_t, cursor_t + 1.0, cursor_x, 0.0, 0.0, "idle", 1, 0.0, "final"))
        out[crane.crane_id] = traj
    return out


def verify(geometry: ef.Geometry, trajectories: dict[str, rail.Trajectory]) -> dict[str, Any]:
    params = {"cranes": [{"id": c.id} for c in sorted(geometry.cranes, key=lambda c: c.order)]}
    horizon = (0.0, max(t.t_end for t in trajectories.values()))
    return rail.verify_schedule(params, trajectories, geometry.clear_distance_m, horizon)


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
