#!/usr/bin/env python3
"""Smoke-test solver availability with tiny synthetic models only.

These examples validate installation/API integration, not the competition
schedule model, its physical assumptions, or its optimality claims.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any

import highspy
import ortools
import pyomo
import pyomo.environ as pyo
from ortools.sat.python import cp_model
from pyomo.opt import SolverFactory

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "audit" / "solver_preflight.json"


def run_cp_sat() -> dict[str, Any]:
    model = cp_model.CpModel()
    durations = [3, 2, 4, 1]
    releases = [0, 0, 1, 2]
    deadlines = [7, 7, 9, 9]
    starts, ends, assignments = [], [], []
    machine_intervals = {m: [] for m in range(2)}

    for j, (duration, release, deadline) in enumerate(zip(durations, releases, deadlines)):
        start = model.NewIntVar(release, deadline - duration, f"start_{j}")
        end = model.NewIntVar(release + duration, deadline, f"end_{j}")
        model.Add(end == start + duration)
        starts.append(start)
        ends.append(end)
        job_assignment = []
        for m in range(2):
            present = model.NewBoolVar(f"job_{j}_crane_{m}")
            interval = model.NewOptionalIntervalVar(start, duration, end, present, f"interval_{j}_{m}")
            machine_intervals[m].append(interval)
            job_assignment.append(present)
        model.AddExactlyOne(job_assignment)
        assignments.append(job_assignment)

    for intervals in machine_intervals.values():
        model.AddNoOverlap(intervals)
    makespan = model.NewIntVar(0, max(deadlines), "makespan")
    model.AddMaxEquality(makespan, ends)
    model.Minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 2.0
    solver.parameters.num_search_workers = 1
    t0 = time.perf_counter()
    status = solver.Solve(model)
    wall_s = time.perf_counter() - t0
    status_name = solver.StatusName(status)
    result = {
        "solver": "OR-Tools CP-SAT",
        "status": status_name,
        "wall_time_s": round(wall_s, 6),
        "objective_makespan": int(solver.Value(makespan)) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "best_bound": float(solver.BestObjectiveBound()) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "task_count": len(durations),
        "machine_count": len(machine_intervals),
        "schedule": [
            {
                "task": j,
                "machine": next(m for m in range(2) if solver.BooleanValue(assignments[j][m])) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                "start": int(solver.Value(starts[j])) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                "end": int(solver.Value(ends[j])) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
            }
            for j in range(len(durations))
        ] if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else [],
    }
    if status != cp_model.OPTIMAL:
        raise RuntimeError(f"CP-SAT smoke model did not prove OPTIMAL: {status_name}")
    return result


def run_pyomo_highs() -> dict[str, Any]:
    model = pyo.ConcreteModel()
    model.x = pyo.Var(domain=pyo.NonNegativeReals, bounds=(0, 10))
    model.y = pyo.Var(domain=pyo.NonNegativeReals, bounds=(0, 10))
    model.obj = pyo.Objective(expr=3 * model.x + 4 * model.y, sense=pyo.maximize)
    model.c1 = pyo.Constraint(expr=2 * model.x + model.y <= 12)
    model.c2 = pyo.Constraint(expr=model.x + 2 * model.y <= 10)
    opt = SolverFactory("appsi_highs")
    if not opt.available(exception_flag=False):
        raise RuntimeError("Pyomo appsi_highs is not available")
    t0 = time.perf_counter()
    result = opt.solve(model)
    wall_s = time.perf_counter() - t0
    status = str(result.solver.termination_condition)
    objective = float(pyo.value(model.obj)) if status == str(pyo.TerminationCondition.optimal) else None
    payload = {
        "solver": "Pyomo + HiGHS (appsi_highs)",
        "status": status,
        "wall_time_s": round(wall_s, 6),
        "objective": objective,
        "x": float(pyo.value(model.x)) if objective is not None else None,
        "y": float(pyo.value(model.y)) if objective is not None else None,
        "known_optimum": 74 / 3,
    }
    if objective is None or abs(objective - 74 / 3) > 1e-6:
        raise RuntimeError(f"Pyomo+HiGHS smoke result incorrect: {payload}")
    return payload


def run_preflight(output_path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    cp = run_cp_sat()
    lp = run_pyomo_highs()
    payload = {
        "purpose": "installation/API smoke test with synthetic toys; not project results",
        "python": platform.python_version(),
        "versions": {
            "ortools": ortools.__version__,
            "pyomo": pyomo.version.version,
            "highspy": highspy.Highs().version(),
        },
        "tests": [cp, lp],
        "all_passed": True,
    }
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_preflight(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
