#!/usr/bin/env python3
"""Provisional 1-D rail kinematics and an independent safety verifier.

Two distinct responsibilities live here:

1. ``profile_segments`` builds a piecewise constant-acceleration (trapezoidal)
   motion profile that completes a requested displacement within a requested
   duration.
2. ``Trajectory`` / ``verify_pair`` perform exact continuous-time gap checking
   between adjacent cranes. Because each crane's position is piecewise
   quadratic, the relative gap is piecewise quadratic too, so the minimum on a
   segment is attained at an endpoint or at the interior critical point where
   the relative velocity vanishes (proposal proposition 2b). No sampling grid is
   used for the safety decision.

All parameters are provisional: see ``code/configs/scene1_rail_params.yml``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARAMS = ROOT / "code" / "configs" / "scene1_rail_params.yml"


def load_params(path: Path | str = DEFAULT_PARAMS) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def station_x(params: dict[str, Any], name: str) -> float:
    """Resolve a task endpoint label to a relative-rail coordinate."""
    mapping = params["location_map"]
    key = mapping.get(name, name)
    stations = params["stations"]
    if key not in stations:
        raise KeyError(f"unknown station or location {name!r} (resolved key {key!r})")
    return float(stations[key])


def crane_spec(params: dict[str, Any], crane_id: str) -> dict[str, Any]:
    for spec in params["cranes"]:
        if spec["id"] == crane_id:
            return spec
    raise KeyError(f"unknown crane {crane_id!r}")


def crane_order(params: dict[str, Any]) -> list[str]:
    return [spec["id"] for spec in params["cranes"]]


def travel_minutes_from_model(params: dict[str, Any], dx_px: float) -> float:
    """Inherited q0.10 regression: total leg time including handling."""
    model = params["travel_model"]
    return model["intercept_min"] + model["slope_min_per_1000px"] * abs(dx_px) / 1000.0


def reposition_minutes(params: dict[str, Any], dx_px: float) -> float:
    """Empty repositioning time: slope only, scaled by the empty-speed factor."""
    model = params["travel_model"]
    factor = float(model.get("empty_speed_factor", 1.0))
    return model["slope_min_per_1000px"] * abs(dx_px) / 1000.0 / max(factor, 1e-9)


@dataclass
class Segment:
    t_start: float
    t_end: float
    x_start: float
    v_start: float
    accel: float
    phase: str
    gear: int
    load_t: float
    task_id: str

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    def x_at(self, t: float) -> float:
        tau = min(max(t, self.t_start), self.t_end) - self.t_start
        return self.x_start + self.v_start * tau + 0.5 * self.accel * tau * tau

    def v_at(self, t: float) -> float:
        tau = min(max(t, self.t_start), self.t_end) - self.t_start
        return self.v_start + self.accel * tau


def gear_for_ratio(ratio: float, edges: Iterable[float]) -> int:
    for index, edge in enumerate(edges, start=1):
        if ratio <= edge + 1e-12:
            return index
    return len(list(edges))


def _ramp_profile(
    dist_px: float,
    duration_s: float,
    accel: float,
    v_max: float,
) -> tuple[list[tuple[float, float, float, str]], float]:
    """Return ``([(duration, distance_x, v_entry, phase)], cap_ratio)``.

    The profile always consumes exactly ``duration_s`` and covers exactly
    ``dist_px``. The cruise speed is solved from the empirical total time, so the
    requested timing is honoured; ``cap_ratio`` reports how far the implied peak
    speed exceeds the provisional cap (``>1`` means the cap was exceeded and the
    caller must record it). Kinematic infeasibility (duration below the minimum
    ramp time at ``accel``) raises ``ValueError``; the caller is expected to raise
    the acceleration first and record that choice.
    """
    sign = 1.0 if dist_px >= 0 else -1.0
    dist = abs(dist_px)
    if dist < 1e-9:
        return [(max(duration_s, 0.0), 0.0, 0.0, "idle")], 0.0
    if accel <= 0:
        raise ValueError("accel must be positive")
    t_min = 2.0 * math.sqrt(dist / accel)
    if duration_s < t_min - 1e-6:
        raise ValueError(
            f"duration {duration_s:.3f}s shorter than minimum ramp time {t_min:.3f}s at accel {accel}"
        )

    disc = accel * accel * duration_s * duration_s - 4.0 * accel * dist
    v_cruise = (accel * duration_s - math.sqrt(max(disc, 0.0))) / 2.0
    v_cruise = max(v_cruise, 1e-9)

    t_acc = v_cruise / accel
    d_acc = 0.5 * accel * t_acc * t_acc
    t_cruise = max((dist - 2.0 * d_acc) / v_cruise, 0.0)
    motion = 2.0 * t_acc + t_cruise
    hold = max(duration_s - motion, 0.0)

    pieces: list[tuple[float, float, float, str]] = []
    if hold > 1e-9:
        pieces.append((hold, 0.0, 0.0, "idle"))
    pieces.append((t_acc, d_acc, 0.0, "accel"))
    if t_cruise > 1e-9:
        pieces.append((t_cruise, v_cruise * t_cruise, v_cruise, "cruise"))
    pieces.append((t_acc, d_acc, v_cruise, "decel"))

    signed = [(dur, sign * dx, sign * v0, phase) for dur, dx, v0, phase in pieces]
    cap_ratio = v_cruise / v_max if v_max > 0 else 0.0
    return signed, cap_ratio



def safe_profile(
    dx_px: float,
    duration_s: float,
    accel: float,
    v_max: float,
) -> tuple[list[tuple[float, float, float, str]], float, float, bool]:
    """Build a profile that always matches ``duration_s`` exactly.

    Returns ``(pieces, cap_ratio, accel_used, accel_raised)``. If the declared
    duration is shorter than the minimum ramp time at the nominal acceleration,
    the acceleration is raised for this segment and the fact is reported, because
    the segment duration comes from observed data while acceleration is a
    provisional scenario parameter.
    """
    dist = abs(dx_px)
    used = accel
    raised = False
    if dist > 1e-9 and duration_s > 1e-9:
        t_min = 2.0 * math.sqrt(dist / accel)
        if duration_s < t_min - 1e-6:
            used = 4.0 * dist / (duration_s * duration_s)
            raised = True
    pieces, cap_ratio = _ramp_profile(dx_px, duration_s, used, v_max)
    return pieces, cap_ratio, used, raised


@dataclass
class Trajectory:
    crane_id: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def t_start(self) -> float:
        return min((s.t_start for s in self.segments), default=0.0)

    @property
    def t_end(self) -> float:
        return max((s.t_end for s in self.segments), default=0.0)

    def x_at(self, t: float) -> float:
        if not self.segments:
            raise ValueError("empty trajectory")
        if t <= self.segments[0].t_start:
            return self.segments[0].x_start
        for seg in self.segments:
            if seg.t_start <= t <= seg.t_end:
                return seg.x_at(t)
        last = self.segments[-1]
        return last.x_at(last.t_end)

    def v_at(self, t: float) -> float:
        if not self.segments:
            return 0.0
        for seg in self.segments:
            if seg.t_start <= t <= seg.t_end:
                return seg.v_at(t)
        return 0.0

    def accel_at(self, t: float) -> float:
        for seg in self.segments:
            if seg.t_start <= t <= seg.t_end:
                return seg.accel
        return 0.0

    def to_rows(self, run_id: str, scenario: str, algorithm: str, origin_s: float) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for idx, seg in enumerate(self.segments, start=1):
            rows.append({
                "run_id": run_id,
                "scenario": scenario,
                "algorithm": algorithm,
                "crane_id": self.crane_id,
                "seg_id": idx,
                "task_id": seg.task_id,
                "t_start_s": round(seg.t_start - origin_s, 3),
                "t_end_s": round(seg.t_end - origin_s, 3),
                "x_start": round(seg.x_start, 3),
                "x_end": round(seg.x_at(seg.t_end), 3),
                "v_start": round(seg.v_start, 4),
                "v_end": round(seg.v_at(seg.t_end), 4),
                "accel": round(seg.accel, 4),
                "gear": seg.gear,
                "phase": seg.phase,
                "load_t": seg.load_t,
            })
        return rows


def build_trajectory(
    params: dict[str, Any],
    crane_id: str,
    initial_x: float,
    moves: list[dict[str, Any]],
    origin_s: float,
) -> tuple[Trajectory, list[dict[str, Any]]]:
    """Build one crane's piecewise-quadratic trajectory.

    Each move dict needs: task_id, origin_x, destination_x, start_s, end_s,
    load_t, phase (``loaded``/``empty``), and an optional
    ``stationary_minutes`` dwell inserted at the origin before travelling.
    """
    motion = params["motion"]
    accel = float(motion["accel_px_s2"])
    v_max = float(motion["v_max_px_s"])
    handling_s = float(motion["handling_min_per_leg"]) * 60.0 / 2.0
    edges = motion["gear_ratio_edges"]

    traj = Trajectory(crane_id=crane_id)
    issues: list[dict[str, Any]] = []
    cursor_x = initial_x
    cursor_t = origin_s
    for move in moves:
        start_s = float(move["start_s"])
        end_s = float(move["end_s"])
        if start_s < cursor_t - 1e-6:
            issues.append({
                "crane_id": crane_id,
                "task_id": move["task_id"],
                "issue": "schedule_overlap_before_profile",
                "severity": "blocking",
                "detail": f"start {start_s:.1f}s precedes previous end {cursor_t:.1f}s",
            })
            start_s = cursor_t
        if end_s <= start_s:
            issues.append({
                "crane_id": crane_id,
                "task_id": move["task_id"],
                "issue": "non_positive_duration",
                "severity": "blocking",
                "detail": f"start {start_s:.1f}s end {end_s:.1f}s",
            })
            continue
        origin_x = float(move["origin_x"])
        destination_x = float(move["destination_x"])

        # Reposition empty to the pickup point, never overrunning the task start.
        if abs(origin_x - cursor_x) > 1e-9:
            needed = max(reposition_minutes(params, origin_x - cursor_x) * 60.0, 1.0)
            available = start_s - cursor_t
            if available <= 0.5:
                issues.append({
                    "crane_id": crane_id,
                    "task_id": move["task_id"],
                    "issue": "reposition_time_shortfall",
                    "severity": "blocking",
                    "detail": f"need {needed:.1f}s but only {available:.1f}s before start",
                })
                reposition_s = max(available, 0.5)
            else:
                reposition_s = min(needed, available)
                if needed - available > 1.0:
                    issues.append({
                        "crane_id": crane_id,
                        "task_id": move["task_id"],
                        "issue": "reposition_time_shortfall",
                        "severity": "blocking",
                        "detail": f"need {needed:.1f}s but only {available:.1f}s before start",
                    })
            pieces, cap_ratio, _, raised = safe_profile(origin_x - cursor_x, reposition_s, accel, v_max)
            if raised:
                issues.append({
                    "crane_id": crane_id,
                    "task_id": move["task_id"],
                    "issue": "accel_raised_for_reposition",
                    "severity": "info",
                    "detail": f"{abs(origin_x - cursor_x):.1f}px in {reposition_s:.1f}s requires accel above nominal {accel}",
                })
            if cap_ratio > 1.0 + 1e-9:
                issues.append({
                    "crane_id": crane_id,
                    "task_id": move["task_id"],
                    "issue": "reposition_speed_cap_exceeded",
                    "severity": "info",
                    "detail": f"implied peak {cap_ratio:.2f}x provisional cap during reposition",
                })
            cursor_t = _append_pieces(traj, pieces, cursor_x, cursor_t, "empty", 0.0,
                                      f"{move['task_id']}:reposition", params, issues)
            cursor_x = origin_x
        if cursor_t < start_s:
            traj.segments.append(Segment(cursor_t, start_s, cursor_x, 0.0, 0.0, "idle", 1, 0.0,
                                         f"{move['task_id']}:wait"))
            cursor_t = start_s

        # Allocate the declared window exactly: hoist + dwell + travel + place.
        # Travel keeps enough time to be kinematically possible at the nominal
        # acceleration; dwell is shortened first, then handling is trimmed, and
        # only as a last resort is the acceleration raised (reported as info).
        duration = end_s - start_s
        dx = abs(destination_x - cursor_x)
        t_min_travel = 2.0 * math.sqrt(dx / accel) if dx > 1e-9 else 0.0
        seg_accel = accel
        if t_min_travel > duration:
            seg_accel = 4.0 * dx / (duration * duration)
            t_min_travel = duration
            issues.append({
                "crane_id": crane_id,
                "task_id": move["task_id"],
                "issue": "accel_raised_for_declared_duration",
                "severity": "info",
                "detail": f"{dx:.1f}px in {duration:.1f}s needs a={seg_accel:.3f} px/s^2 > nominal {accel}",
            })
        budget = max(duration - t_min_travel, 0.0)
        hoist = min(handling_s, 0.4 * budget)
        place = min(handling_s, 0.4 * budget)
        dwell = min(float(move.get("stationary_minutes") or 0.0) * 60.0,
                    max(budget - hoist - place, 0.0))
        travel_duration = max(duration - hoist - place - dwell, 1e-3)

        if hoist > 0:
            traj.segments.append(Segment(cursor_t, cursor_t + hoist, cursor_x, 0.0, 0.0, "idle", 1,
                                         float(move["load_t"]), f"{move['task_id']}:hoist"))
            cursor_t += hoist
        if dwell > 0:
            traj.segments.append(Segment(cursor_t, cursor_t + dwell, cursor_x, 0.0, 0.0, "idle", 1,
                                         float(move["load_t"]), f"{move['task_id']}:dwell"))
            cursor_t += dwell

        phase = str(move.get("phase", "loaded"))
        pieces, cap_ratio, seg_accel, raised = safe_profile(
            destination_x - cursor_x, travel_duration, seg_accel, v_max)
        if raised:
            issues.append({
                "crane_id": crane_id,
                "task_id": move["task_id"],
                "issue": "accel_raised_for_travel",
                "severity": "info",
                "detail": f"{abs(destination_x - cursor_x):.1f}px in {travel_duration:.1f}s requires accel above nominal {accel}",
            })
        if cap_ratio > 1.0 + 1e-9:
            issues.append({
                "crane_id": crane_id,
                "task_id": move["task_id"],
                "issue": "speed_cap_exceeded",
                "severity": "info",
                "detail": f"implied peak {cap_ratio:.2f}x provisional cap ({cap_ratio * v_max:.2f} px/s)",
            })
        cursor_t = _append_pieces(traj, pieces, cursor_x, cursor_t, phase,
                                  float(move["load_t"]), move["task_id"], params, issues)
        cursor_x = destination_x

        if place > 0:
            traj.segments.append(Segment(cursor_t, cursor_t + place, cursor_x, 0.0, 0.0, "idle", 1,
                                         float(move["load_t"]), f"{move['task_id']}:place"))
            cursor_t += place
        cursor_t = end_s if abs(cursor_t - end_s) <= 1e-6 else max(cursor_t, end_s)

    spec = crane_spec(params, crane_id)
    for seg in traj.segments:
        for t in (seg.t_start, seg.t_end):
            x = seg.x_at(t)
            if x < spec["min_x"] - 1e-6 or x > spec["max_x"] + 1e-6:
                issues.append({
                    "crane_id": crane_id,
                    "task_id": seg.task_id,
                    "issue": "track_bound_violation",
                    "severity": "blocking",
                    "detail": f"x={x:.1f} outside [{spec['min_x']}, {spec['max_x']}] at t={t:.1f}s",
                })
    return traj, issues


def _append_pieces(
    traj: Trajectory,
    pieces: list[tuple[float, float, float, str]],
    x0: float,
    t0: float,
    phase: str,
    load_t: float,
    task_id: str,
    params: dict[str, Any],
    issues: list[dict[str, Any]],
) -> float:
    """Append pieces, solving constant acceleration from (dx, v0, duration).

    For a constant-acceleration piece the displacement is
    ``dx = v0*T + a*T^2/2``, so ``a = 2*(dx - v0*T)/T^2`` exactly. The resulting
    segment therefore ends at ``x0 + dx`` by construction.
    """
    v_max = float(params["motion"]["v_max_px_s"])
    edges = params["motion"]["gear_ratio_edges"]
    x = x0
    t = t0
    for duration, dx, v_entry, kind in pieces:
        if duration <= 1e-9:
            continue
        if kind == "idle":
            accel = 0.0
            v0 = 0.0
        else:
            v0 = v_entry
            accel = 2.0 * (dx - v0 * duration) / (duration * duration)
            v_end = v0 + accel * duration
            if abs(v_end) > v_max + 1e-6 and kind == "cruise":
                issues.append({
                    "crane_id": traj.crane_id,
                    "task_id": task_id,
                    "issue": "speed_cap_exceeded",
                    "severity": "info",
                    "detail": f"cruise end speed {v_end:.3f} px/s above cap {v_max}",
                })
        ratio = min(abs(v0) / v_max, 1.0) if v_max > 0 else 0.0
        traj.segments.append(Segment(t, t + duration, x, v0, accel, phase, gear_for_ratio(ratio, edges),
                                     load_t, task_id))
        x = x + dx
        t = t + duration
    return t


@dataclass
class PairResult:
    west: str
    east: str
    min_gap: float
    min_gap_time: float
    required_gap: float
    violations: list[dict[str, Any]]


def _breakpoints(t1: Trajectory, t2: Trajectory, horizon: tuple[float, float]) -> list[float]:
    marks = {horizon[0], horizon[1]}
    for traj in (t1, t2):
        for seg in traj.segments:
            if horizon[0] <= seg.t_start <= horizon[1]:
                marks.add(seg.t_start)
            if horizon[0] <= seg.t_end <= horizon[1]:
                marks.add(seg.t_end)
    return sorted(marks)


def verify_pair(
    west: Trajectory,
    east: Trajectory,
    required_gap: float,
    horizon: tuple[float, float],
) -> PairResult:
    """Exact continuous-time minimum gap between two adjacent cranes."""
    marks = _breakpoints(west, east, horizon)
    best_gap = math.inf
    best_t = horizon[0]
    violations: list[dict[str, Any]] = []
    for left, right in zip(marks, marks[1:]):
        if right - left <= 1e-9:
            continue
        candidates = [left, right]
        # Relative gap is quadratic on the open interval; find interior critical point.
        a_w = west.accel_at(left + 1e-9)
        a_e = east.accel_at(left + 1e-9)
        v_w = west.v_at(left)
        v_e = east.v_at(left)
        a_rel = a_e - a_w
        v_rel = v_e - v_w
        if abs(a_rel) > 1e-12:
            t_star = left - v_rel / a_rel
            if left + 1e-9 < t_star < right - 1e-9 and a_rel > 0:
                candidates.append(t_star)
        for t in candidates:
            gap = east.x_at(t) - west.x_at(t)
            if gap < best_gap:
                best_gap = gap
                best_t = t
            if gap < required_gap - 1e-9:
                violations.append({
                    "west_crane": west.crane_id,
                    "east_crane": east.crane_id,
                    "time_s": round(t, 3),
                    "gap": round(gap, 3),
                    "required_gap": required_gap,
                    "deficit": round(required_gap - gap, 3),
                })
    if violations:
        # Collapse to the worst point per interval pair to keep the report readable.
        worst = min(violations, key=lambda v: v["gap"])
        violations = [worst]
    return PairResult(west.crane_id, east.crane_id, best_gap, best_t, required_gap, violations)


def verify_schedule(
    params: dict[str, Any],
    trajectories: dict[str, Trajectory],
    safety_gap_px: float,
    horizon: tuple[float, float],
) -> dict[str, Any]:
    order = crane_order(params)
    pairs: list[dict[str, Any]] = []
    all_violations: list[dict[str, Any]] = []
    for west_id, east_id in zip(order, order[1:]):
        west = trajectories[west_id]
        east = trajectories[east_id]
        result = verify_pair(west, east, safety_gap_px, horizon)
        pairs.append({
            "west_crane": result.west,
            "east_crane": result.east,
            "min_gap_px": round(result.min_gap, 3),
            "min_gap_time_s": round(result.min_gap_time, 3),
            "required_gap_px": safety_gap_px,
            "status": "pass" if result.min_gap >= safety_gap_px - 1e-9 else "violation",
            "violation_count": len(result.violations),
        })
        all_violations.extend(result.violations)
    return {
        "safety_gap_px": safety_gap_px,
        "pairs": pairs,
        "violations": all_violations,
        "verdict": "pass" if not all_violations else "violation",
        "method": "exact piecewise-quadratic gap minimisation with interior critical-point check",
    }
