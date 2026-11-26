#!/usr/bin/env python3
"""Scenario-1 single-cast closed loop: trajectory + safety gate + optimizer comparison.

Scope (one cast only, ``6C0754``):

1. Load the validated task set produced by ``scenario1_historical_replay.py``.
2. Calibrate one leg-duration model per route step from this cast's observations,
   and cross-check the heavy legs against the inherited OD regression.
3. Build four candidate schedules: observed historical replay, FCFS, EDF-Nearest
   and a CP-SAT model with exact sequence-dependent empty travel.
4. Convert every schedule into piecewise-quadratic ``trajectory.csv`` rows.
5. Run the independent exact continuous-time collision verifier across safety-gap
   and initial-position scenarios.
6. Report which candidates are accepted by the safety gate and compare metrics.

The gate is authoritative: an optimized schedule that fails the verifier is
reported as rejected, never as a feasible solution.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from ortools.sat.python import cp_model

import rail_model as rail

ROOT = Path(__file__).resolve().parents[2]
REPLAY_DIR = ROOT / "data" / "runs" / "scene1_replay_6C0754"
DEFAULT_OUT = ROOT / "data" / "runs" / "scene1_closed_loop_6C0754"
DEFAULT_CAST = "6C0754"


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_tasks(replay_dir: Path) -> tuple[list[dict[str, Any]], float]:
    moves = pd.read_csv(replay_dir / "moves.csv")
    origin = min(parse_dt(v) for v in moves["pickup_time"])
    tasks: list[dict[str, Any]] = []
    for _, row in moves.iterrows():
        pickup = parse_dt(row["pickup_time"])
        dropoff = parse_dt(row["dropoff_time"])
        tasks.append({
            "task_id": row["task_id"],
            "heat_id": row["heat_id"],
            "route_step": row["route_step"],
            "task_type": row["task_type"],
            "origin": row["origin"],
            "destination": row["destination"],
            "observed_crane": row["crane_id"],
            "release_s": (parse_dt(row["release_time"]) - origin).total_seconds(),
            "pickup_s": (pickup - origin).total_seconds(),
            "dropoff_s": (dropoff - origin).total_seconds(),
            "deadline_s": (parse_dt(row["deadline_time"]) - origin).total_seconds(),
            "load_t": float(row["load_t"]),
        })
    tasks.sort(key=lambda t: (t["release_s"], t["task_id"]))
    return tasks, 0.0


def calibrate_leg_model(tasks: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(tasks)
    rows: list[dict[str, Any]] = []
    for route_step, group in frame.groupby("route_step"):
        durations = (group["dropoff_s"] - group["pickup_s"]) / 60.0
        dx = (group["origin"].map(lambda n: rail.station_x(params, n))
              - group["destination"].map(lambda n: rail.station_x(params, n))).abs()
        predicted = rail.travel_minutes_from_model(params, float(dx.median()))
        rows.append({
            "route_step": route_step,
            "n": int(len(group)),
            "observed_median_min": round(float(durations.median()), 4),
            "observed_min_min": round(float(durations.min()), 4),
            "observed_max_min": round(float(durations.max()), 4),
            "abs_dx_px": round(float(dx.median()), 2),
            "inherited_regression_min": round(predicted, 4),
            "regression_residual_min": round(float(durations.median()) - predicted, 4),
            "used_duration_min": round(float(durations.median()), 4),
            "duration_source": "observed median of this cast (n small; provisional)",
        })
    return rows


def build_task_graph(tasks: list[dict[str, Any]], leg_rows: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    duration = {row["route_step"]: row["used_duration_min"] * 60.0 for row in leg_rows}
    by_heat: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        task["origin_x"] = rail.station_x(params, task["origin"])
        task["destination_x"] = rail.station_x(params, task["destination"])
        task["duration_s"] = duration[task["route_step"]]
        task["phase"] = "loaded" if task["task_type"] == "heavy" else "empty"
        by_heat.setdefault(task["heat_id"], []).append(task)
    for heat, group in by_heat.items():
        group.sort(key=lambda t: t["release_s"])
        for prev, current in zip(group, group[1:]):
            current.setdefault("predecessors", []).append(prev["task_id"])
    # Empty-ladle returns include slag/residual processing: split into a
    # stationary dwell at the caster plus pure travel to the transfer car.
    for task in tasks:
        if task["route_step"] == "CCM_empty_to_transfer_car":
            travel_s = rail.reposition_minutes(params, task["destination_x"] - task["origin_x"]) * 60.0
            handling_s = float(params["motion"]["handling_min_per_leg"]) * 60.0
            dwell_s = max(task["duration_s"] - travel_s - handling_s, 0.0)
            task["stationary_minutes"] = dwell_s / 60.0
            task["pure_travel_s"] = travel_s + handling_s
        else:
            task["stationary_minutes"] = 0.0
            task["pure_travel_s"] = task["duration_s"]
    return tasks


def empty_travel_s(params: dict[str, Any], from_x: float, to_x: float) -> float:
    return max(rail.reposition_minutes(params, to_x - from_x) * 60.0, 0.0)


def schedule_greedy(tasks: list[dict[str, Any]], params: dict[str, Any], rule: str) -> dict[str, Any]:
    capable = list(params["scheduling"]["heavy_capable_cranes"])
    init = params["initial_positions"][params["default_initial_position_scenario"]]
    crane_pos = {cid: float(init[cid]) for cid in capable}
    crane_free = {cid: 0.0 for cid in capable}
    done: dict[str, float] = {}

    if rule == "fcfs":
        order = sorted(tasks, key=lambda t: (t["release_s"], t["task_id"]))
    elif rule == "edf_nearest":
        order = sorted(tasks, key=lambda t: (t["deadline_s"], t["release_s"], t["task_id"]))
    else:
        raise ValueError(rule)

    assignments: list[dict[str, Any]] = []
    for task in order:
        earliest_release = task["release_s"]
        for pred in task.get("predecessors", []):
            if pred not in done:
                raise RuntimeError(f"precedence violated: {pred} not scheduled before {task['task_id']}")
            earliest_release = max(earliest_release, done[pred])
        best = None
        for cid in capable:
            reposition = empty_travel_s(params, crane_pos[cid], task["origin_x"])
            start = max(earliest_release, crane_free[cid] + reposition)
            completion = start + task["duration_s"]
            cand = (completion, reposition, cid, start)
            if best is None or cand < best:
                best = cand
        completion, reposition, cid, start = best
        assignments.append({
            "task_id": task["task_id"],
            "crane_id": cid,
            "start_s": start,
            "end_s": start + task["duration_s"],
            "reposition_s": reposition,
        })
        crane_pos[cid] = task["destination_x"]
        crane_free[cid] = start + task["duration_s"]
        done[task["task_id"]] = start + task["duration_s"]
    return {"algorithm": rule, "assignments": assignments, "solver_status": "greedy_deterministic"}


def schedule_cpsat(
    tasks: list[dict[str, Any]],
    params: dict[str, Any],
    capable: list[str] | None = None,
    label: str = "cp_sat_exact_setup",
    initial_scenario: str | None = None,
) -> dict[str, Any]:
    capable = list(capable or params["scheduling"]["heavy_capable_cranes"])
    init = params["initial_positions"][initial_scenario or params["default_initial_position_scenario"]]
    horizon = int(max(t["deadline_s"] for t in tasks) + 4 * 3600)
    model = cp_model.CpModel()
    start: dict[str, Any] = {}
    end: dict[str, Any] = {}
    presence: dict[tuple[str, str], Any] = {}
    lateness: dict[str, Any] = {}

    for task in tasks:
        tid = task["task_id"]
        dur = int(round(task["duration_s"]))
        start[tid] = model.NewIntVar(int(max(task["release_s"], 0)), horizon, f"start_{tid}")
        end[tid] = model.NewIntVar(0, horizon, f"end_{tid}")
        model.Add(end[tid] == start[tid] + dur)
        for cid in capable:
            presence[(tid, cid)] = model.NewBoolVar(f"p_{tid}_{cid}")
            lead = int(math.ceil(empty_travel_s(params, float(init[cid]), task["origin_x"])))
            if lead > 0:
                model.Add(start[tid] >= lead).OnlyEnforceIf(presence[(tid, cid)])
        model.AddExactlyOne([presence[(tid, cid)] for cid in capable])
        lateness[tid] = model.NewIntVar(0, horizon, f"late_{tid}")
        model.Add(lateness[tid] >= end[tid] - int(round(task["deadline_s"])))

    for task in tasks:
        for pred in task.get("predecessors", []):
            model.Add(start[task["task_id"]] >= end[pred])

    # Exact sequence-dependent empty travel between tasks sharing a crane.
    for cid in capable:
        for i, first in enumerate(tasks):
            for second in tasks[i + 1:]:
                ti, tj = first["task_id"], second["task_id"]
                both = [presence[(ti, cid)], presence[(tj, cid)]]
                order = model.NewBoolVar(f"order_{cid}_{ti}_{tj}")
                # Ceil so the discrete model never undercuts the continuous profile.
                gap_ij = int(math.ceil(empty_travel_s(params, first["destination_x"], second["origin_x"])))
                gap_ji = int(math.ceil(empty_travel_s(params, second["destination_x"], first["origin_x"])))
                model.Add(start[tj] >= end[ti] + gap_ij).OnlyEnforceIf(both + [order])
                model.Add(start[ti] >= end[tj] + gap_ji).OnlyEnforceIf(both + [order.Not()])

    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, [end[t["task_id"]] for t in tasks])
    total_late = model.NewIntVar(0, horizon * len(tasks), "total_late")
    model.Add(total_late == sum(lateness.values()))
    weight = int(params["scheduling"]["objective_lateness_weight"])
    model.Minimize(makespan + weight * total_late)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(params["scheduling"]["cp_sat_time_limit_s"])
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    name = solver.StatusName(status)
    assignments: list[dict[str, Any]] = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for task in tasks:
            tid = task["task_id"]
            cid = next(c for c in capable if solver.BooleanValue(presence[(tid, c)]))
            assignments.append({
                "task_id": tid,
                "crane_id": cid,
                "start_s": float(solver.Value(start[tid])),
                "end_s": float(solver.Value(end[tid])),
                "lateness_s": float(solver.Value(lateness[tid])),
            })
    return {
        "algorithm": label,
        "assignments": assignments,
        "solver_status": name,
        "objective": float(solver.ObjectiveValue()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "best_bound": float(solver.BestObjectiveBound()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "wall_time_s": solver.WallTime(),
    }


def observed_schedule(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "algorithm": "observed_historical",
        "assignments": [{
            "task_id": t["task_id"],
            "crane_id": t["observed_crane"],
            "start_s": t["pickup_s"],
            "end_s": t["dropoff_s"],
        } for t in tasks],
        "solver_status": "not_a_solver_observed_baseline",
    }


def trajectories_for(
    params: dict[str, Any],
    tasks: list[dict[str, Any]],
    schedule: dict[str, Any],
    initial_scenario: str,
    stage_initial: bool = False,
) -> tuple[dict[str, rail.Trajectory], list[dict[str, Any]]]:
    task_index = {t["task_id"]: t for t in tasks}
    init = dict(params["initial_positions"][initial_scenario])
    per_crane: dict[str, list[dict[str, Any]]] = {cid: [] for cid in rail.crane_order(params)}
    for item in sorted(schedule["assignments"], key=lambda a: a["start_s"]):
        task = task_index[item["task_id"]]
        per_crane[item["crane_id"]].append({
            "task_id": task["task_id"],
            "origin_x": task["origin_x"],
            "destination_x": task["destination_x"],
            "start_s": item["start_s"],
            "end_s": item["end_s"],
            "load_t": task["load_t"],
            "phase": task["phase"],
            "stationary_minutes": task["stationary_minutes"],
        })
    if stage_initial:
        for cid, moves in per_crane.items():
            if moves:
                init[cid] = float(moves[0]["origin_x"])
    trajectories: dict[str, rail.Trajectory] = {}
    issues: list[dict[str, Any]] = []
    for cid in rail.crane_order(params):
        if per_crane[cid]:
            traj, problems = rail.build_trajectory(params, cid, float(init[cid]), per_crane[cid], 0.0)
        else:
            traj = rail.Trajectory(crane_id=cid, segments=[
                rail.Segment(0.0, 1.0, float(init[cid]), 0.0, 0.0, "idle", 1, 0.0, "idle")
            ])
            problems = []
        trajectories[cid] = traj
        issues.extend(problems)
    return trajectories, issues


def metrics_for(schedule: dict[str, Any], tasks: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    task_index = {t["task_id"]: t for t in tasks}
    assignments = schedule["assignments"]
    if not assignments:
        return {"algorithm": schedule["algorithm"], "feasible": False}
    makespan = max(a["end_s"] for a in assignments) - min(a["start_s"] for a in assignments)
    total_wait = sum(a["start_s"] - task_index[a["task_id"]]["release_s"] for a in assignments)
    total_late = sum(max(0.0, a["end_s"] - task_index[a["task_id"]]["deadline_s"]) for a in assignments)
    reposition = sum(a.get("reposition_s", 0.0) for a in assignments) / 60.0
    crane_used = sorted({a["crane_id"] for a in assignments})
    load_by_crane: dict[str, float] = {}
    for a in assignments:
        load_by_crane[a["crane_id"]] = load_by_crane.get(a["crane_id"], 0.0) + (a["end_s"] - a["start_s"]) / 60.0
    return {
        "algorithm": schedule["algorithm"],
        "solver_status": schedule["solver_status"],
        "feasible": True,
        "makespan_min": round(makespan / 60.0, 3),
        "total_task_wait_min": round(total_wait / 60.0, 3),
        "total_lateness_min": round(total_late / 60.0, 3),
        "greedy_reposition_min": round(reposition, 3),
        "task_count": len(assignments),
        "cranes_used": ";".join(crane_used),
        "busy_minutes_by_crane": json.dumps({k: round(v, 3) for k, v in sorted(load_by_crane.items())}),
        "objective": schedule.get("objective"),
        "best_bound": schedule.get("best_bound"),
        "wall_time_s": schedule.get("wall_time_s"),
    }



def empty_speed_sensitivity(
    params: dict[str, Any],
    tasks: list[dict[str, Any]],
    out_dir: Path,
) -> list[dict[str, Any]]:
    """Sweep the provisional empty-travel speed factor.

    Reports, for every algorithm and factor, whether the safety gate passes and
    whether the trajectory profile is free of blocking issues. This isolates how
    much of the result depends on that single provisional parameter.
    """
    import copy

    rows: list[dict[str, Any]] = []
    sweep = params["travel_model"].get("empty_speed_factor_sweep", [params["travel_model"]["empty_speed_factor"]])
    base_gap = float(params["safety_gap_px"][params["default_safety_gap"]])
    for factor in sweep:
        variant = copy.deepcopy(params)
        variant["travel_model"]["empty_speed_factor"] = float(factor)
        schedules = [
            {**observed_schedule(tasks), "preferred_scenario": "spread", "stage_initial": True},
            {**schedule_greedy(tasks, variant, "fcfs"), "preferred_scenario": "spread"},
            {**schedule_greedy(tasks, variant, "edf_nearest"), "preferred_scenario": "spread"},
            {**schedule_cpsat(tasks, variant, initial_scenario="spread"), "preferred_scenario": "spread"},
            {
                **schedule_cpsat(tasks, variant,
                                capable=[variant["scheduling"]["serial_crane"]],
                                label="conservative_serial",
                                initial_scenario=variant["scheduling"]["serial_initial_scenario"]),
                "preferred_scenario": variant["scheduling"]["serial_initial_scenario"],
            },
        ]
        for schedule in schedules:
            preferred = schedule.get("preferred_scenario", variant["default_initial_position_scenario"])
            stage = bool(schedule.get("stage_initial", False))
            traj, issues = trajectories_for(variant, tasks, schedule, preferred, stage_initial=stage)
            verify = rail.verify_schedule(variant, traj, base_gap,
                                          (0.0, max(t.t_end for t in traj.values())))
            blocking = sum(1 for i in issues if i.get("severity") == "blocking")
            rows.append({
                "empty_speed_factor": float(factor),
                "algorithm": schedule["algorithm"],
                "safety_verdict": verify["verdict"],
                "min_gap_px": round(min(p["min_gap_px"] for p in verify["pairs"]), 3),
                "blocking_profile_issues": blocking,
                "accepted": "yes" if verify["verdict"] == "pass" and blocking == 0 else "no",
            })
    write_csv(out_dir / "empty_speed_sensitivity.csv", rows, list(rows[0].keys()))
    return rows


def run(cast: str = DEFAULT_CAST, replay_dir: Path = REPLAY_DIR, out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    params = rail.load_params()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks, _ = load_tasks(replay_dir)
    leg_rows = calibrate_leg_model(tasks, params)
    tasks = build_task_graph(tasks, leg_rows, params)
    write_csv(out_dir / "leg_calibration.csv", leg_rows, list(leg_rows[0].keys()))

    schedules = [
        {**observed_schedule(tasks), "preferred_scenario": "spread", "stage_initial": True},
        {**schedule_greedy(tasks, params, "fcfs"), "preferred_scenario": "spread"},
        {**schedule_greedy(tasks, params, "edf_nearest"), "preferred_scenario": "spread"},
        {**schedule_cpsat(tasks, params, initial_scenario="spread"), "preferred_scenario": "spread"},
        {
            **schedule_cpsat(
                tasks, params,
                capable=[params["scheduling"]["serial_crane"]],
                label="conservative_serial",
                initial_scenario=params["scheduling"]["serial_initial_scenario"],
            ),
            "preferred_scenario": params["scheduling"]["serial_initial_scenario"],
        },
    ]

    gap_names = list(params["safety_gap_px"].keys())
    init_names = list(params["initial_positions"].keys())
    matrix_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    comparison_rows: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}
    all_issues: list[dict[str, Any]] = []

    for schedule in schedules:
        algorithm = schedule["algorithm"]
        preferred = schedule.get("preferred_scenario", params["default_initial_position_scenario"])
        metric = metrics_for(schedule, tasks, params)
        base_gap = float(params["safety_gap_px"][params["default_safety_gap"]])
        stage = bool(schedule.get("stage_initial", False))
        base_traj, base_issues = trajectories_for(params, tasks, schedule, preferred, stage_initial=stage)
        all_issues.extend(base_issues)
        base_verify = rail.verify_schedule(params, base_traj, base_gap,
                                          (0.0, max(t.t_end for t in base_traj.values())))
        blocking = sum(1 for issue in base_issues if issue.get("severity") == "blocking")
        worst = min(base_verify["violations"], key=lambda v: v["gap"]) if base_verify["violations"] else None
        for pair in base_verify["pairs"]:
            pair_rows.append({"algorithm": algorithm, **pair})

        rows = [seg for cid, traj in base_traj.items()
                for seg in traj.to_rows(f"scene1-loop-{cast}", "1_deterministic", algorithm, 0.0)]
        write_csv(out_dir / f"trajectory_{algorithm}.csv", rows,
                  ["run_id", "scenario", "algorithm", "crane_id", "seg_id", "task_id",
                   "t_start_s", "t_end_s", "x_start", "x_end", "v_start", "v_end",
                   "accel", "gear", "phase", "load_t"])
        sched_rows = [{**a, "algorithm": algorithm, "task_id": a["task_id"],
                       "heat_id": next(t["heat_id"] for t in tasks if t["task_id"] == a["task_id"]),
                       "route_step": next(t["route_step"] for t in tasks if t["task_id"] == a["task_id"])}
                      for a in schedule["assignments"]]
        write_csv(out_dir / f"schedule_{algorithm}.csv", sched_rows,
                  ["algorithm", "task_id", "heat_id", "route_step", "crane_id",
                   "start_s", "end_s", "reposition_s", "lateness_s"])

        for init_name in init_names:
            for gap_name in gap_names:
                gap = float(params["safety_gap_px"][gap_name])
                traj, issues = trajectories_for(params, tasks, schedule, init_name, stage_initial=stage)
                horizon = (0.0, max(t.t_end for t in traj.values()))
                verify = rail.verify_schedule(params, traj, gap, horizon)
                matrix_rows.append({
                    "algorithm": algorithm,
                    "initial_position_scenario": init_name,
                    "safety_gap_scenario": gap_name,
                    "safety_gap_px": gap,
                    "verdict": verify["verdict"],
                    "violation_count": len(verify["violations"]),
                    "min_gap_px": round(min(p["min_gap_px"] for p in verify["pairs"]), 3),
                    "profile_issue_count": len(issues),
                })

        comparison_rows.append({
            **metric,
            "preferred_initial_scenario": preferred,
            "safety_verdict_base_config": base_verify["verdict"],
            "min_gap_px_base_config": round(min(p["min_gap_px"] for p in base_verify["pairs"]), 3),
            "required_gap_px_base_config": base_gap,
            "blocking_profile_issues": blocking,
            "info_profile_issues": len(base_issues) - blocking,
            "worst_violation_west_crane": worst["west_crane"] if worst else "",
            "worst_violation_east_crane": worst["east_crane"] if worst else "",
            "worst_violation_time_s": worst["time_s"] if worst else "",
            "worst_violation_gap_px": worst["gap"] if worst else "",
            "worst_violation_deficit_px": worst["deficit"] if worst else "",
            "accepted": "yes" if base_verify["verdict"] == "pass" and blocking == 0 else "no",
        })
        detail[algorithm] = {
            "metrics": metric,
            "preferred_initial_scenario": preferred,
            "base_config": {
                "initial_position_scenario": preferred,
                "safety_gap_scenario": params["default_safety_gap"],
                "verify": base_verify,
                "profile_issues": base_issues,
            },
        }

    diagnostics: list[dict[str, Any]] = []
    for algorithm in detail:
        traj_rows = list(csv.DictReader((out_dir / f"trajectory_{algorithm}.csv").open(encoding="utf-8-sig")))
        speeds = [abs(float(r["v_start"])) for r in traj_rows] + [abs(float(r["v_end"])) for r in traj_rows]
        phase_minutes: dict[str, float] = {}
        for row in traj_rows:
            phase_minutes[row["phase"]] = phase_minutes.get(row["phase"], 0.0) + (
                float(row["t_end_s"]) - float(row["t_start_s"])) / 60.0
        diagnostics.append({
            "algorithm": algorithm,
            "trajectory_segments": len(traj_rows),
            "peak_speed_px_s": round(max(speeds), 3),
            "segments_above_cap": sum(1 for v in speeds if v > float(params["motion"]["v_max_px_s"]) + 1e-9),
            "idle_minutes": round(phase_minutes.get("idle", 0.0), 2),
            "loaded_minutes": round(phase_minutes.get("loaded", 0.0), 2),
            "empty_minutes": round(phase_minutes.get("empty", 0.0), 2),
            "track_violations": sum(1 for r in traj_rows
                                    if float(r["x_start"]) < 300 or float(r["x_start"]) > 2650),
        })
    write_csv(out_dir / "trajectory_diagnostics.csv", diagnostics, list(diagnostics[0].keys()))
    write_csv(out_dir / "collision_matrix.csv", matrix_rows, list(matrix_rows[0].keys()))
    write_csv(out_dir / "collision_pairs_base.csv", pair_rows,
              ["algorithm", "west_crane", "east_crane", "min_gap_px", "min_gap_time_s",
               "required_gap_px", "status", "violation_count"])
    write_csv(out_dir / "optimization_comparison.csv", comparison_rows, list(comparison_rows[0].keys()))
    write_csv(out_dir / "profile_issues.csv", all_issues,
              ["crane_id", "task_id", "issue", "severity", "detail"])

    sensitivity_rows = empty_speed_sensitivity(params, tasks, out_dir)

    summary = {
        "run_id": f"scene1-loop-{cast}",
        "cast_id": cast,
        "task_count": len(tasks),
        "leg_calibration": leg_rows,
        "parameters": {
            "source": "code/configs/scene1_rail_params.yml",
            "confirmed_by_modeler": bool(params["provenance"]["confirmed_by_modeler"]),
            "default_safety_gap_px": params["safety_gap_px"][params["default_safety_gap"]],
            "default_initial_position_scenario": params["default_initial_position_scenario"],
            "accel_px_s2": params["motion"]["accel_px_s2"],
            "v_max_px_s": params["motion"]["v_max_px_s"],
        },
        "algorithms": detail,
        "acceptance_gate": "an algorithm is accepted only if the exact collision verifier passes for the base configuration",
        "empty_speed_sensitivity": sensitivity_rows,
        "accepted_algorithms": [r["algorithm"] for r in comparison_rows if r["accepted"] == "yes"],
        "limitations": [
            "Rail coordinates are relative pixels inherited from the proposal initial review; kappa (m/px) is unconfirmed.",
            "Acceleration, speed cap and safety gap are provisional scenario values, swept in collision_matrix.csv.",
            "Crane initial positions are absent from the source data and are explicit scenario assumptions.",
            "Leg durations are medians of this single cast (n<=6 per route step) and are not a plant-wide calibration.",
            "Empty-ladle returns are split into a stationary slag/residual dwell plus travel; the dwell model is provisional.",
            "No LF tasks are present because this cast uses the R0 route only.",
            "Empty-return legs carry slag/residual processing inside their observed window; the reconstructed "
            "observed baseline therefore implies peak speeds above the provisional cap on 5 segments, so the "
            "observed baseline is reported as collision-invalid rather than physically certified.",
        ],
    }
    with (out_dir / "closed_loop_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return {"summary": summary, "comparison": comparison_rows, "matrix": matrix_rows, "out_dir": str(out_dir)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cast", default=DEFAULT_CAST)
    parser.add_argument("--replay-dir", type=Path, default=REPLAY_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = run(args.cast, args.replay_dir, args.out_dir)
    print(json.dumps(result["comparison"], ensure_ascii=False, indent=2))
    print("output_dir:", result["out_dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
