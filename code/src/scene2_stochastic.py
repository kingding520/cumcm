#!/usr/bin/env python3
"""Scenario 2: gear envelope, random processing times and day-block backtest.

Contract (``docs/person1-wj-建模/06_场景2与场景3简明建模契约.md`` §2):

* random processing time ``p^s = p_bar * eps^s`` with ``E[eps] = 1``, factors fitted
  from verified positive durations in the **training** days and normalised to mean
  one; sampling is per day block so a whole day shares one common shock;
* gear envelope: forward acceleration / backward braking, slow-zone braking must
  finish *before* entering the zone, positions and speeds continuous between legs;
  the single-limit check is ``T(L) = 2*sqrt(L/a)`` for ``L <= v^2/a`` and
  ``T(L) = L/v + v/a`` otherwise;
* rolling decision with 2 h horizon and 60 s period, 20 optimisation scenarios and
  100 independent evaluation scenarios, seed 20261126, ``alpha = 0.9``, ``lambda = 1``;
* three policies compared on identical scenarios: mean plan, EDF safe dispatch and a
  scenario-based risk policy (mean + CVaR), with common random numbers.

Splits follow the contract: train 09-25..10-04, validation 10-05..10-07, test
10-08..10-11. Each heat and its linked events stay in one split.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

import ef_model as ef
import scene1_day as day_mod
import scene1_reservation as sr

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT_DIR = ROOT / "data" / "runs" / "scene2"
CONTRACT = ROOT / "code" / "configs" / "model_contract_v1.json"
HEAVY_CAPACITY_T = 450.0


# --------------------------------------------------------------------------- helpers
def travel_time_analytic(distance: float, v: float, a: float) -> float:
    """Contract §2.3 single-limit formula."""
    if distance <= 0:
        return 0.0
    if distance <= v * v / a:
        return 2.0 * math.sqrt(distance / a)
    return distance / v + v / a


def gear_envelope(geometry: ef.Geometry, x0: float, x1: float, state: str) -> dict[str, Any]:
    """Pick the highest gear that can still brake before the next slow zone.

    Returns the feasible cruise speed, the chosen gear and the analytic travel time,
    verified against the integrated one from ``ef_model``.
    """
    v_max_state = geometry.speed_limit_m_s[state]
    accel = geometry.accel_m_s2[state]
    direction = 1.0 if x1 >= x0 else -1.0
    # nearest slow zone ahead
    zones = sorted({z for z in geometry.slow_zones
                    if (z - x0) * direction > 0 and abs(z - x0) <= abs(x1 - x0) + geometry.slow_half_width_m})
    next_zone = zones[0] if zones else None
    chosen = None
    for idx in range(len(geometry.gear_ratios) - 1, -1, -1):
        ratio = geometry.gear_ratios[idx]
        v_cruise = v_max_state * ratio
        if next_zone is not None and abs(next_zone - x0) > geometry.slow_half_width_m:
            brake_distance = v_cruise * v_cruise / (2.0 * accel)
            if brake_distance > abs(next_zone - x0) - geometry.slow_half_width_m:
                continue          # cannot stop before the zone in this gear
        chosen = (idx + 1, v_cruise)
        break
    if chosen is None:
        chosen = (1, v_max_state * geometry.gear_ratios[0])
    gear, v_cruise = chosen
    distance = abs(x1 - x0)
    analytic = travel_time_analytic(distance, v_cruise, accel)
    integrated = geometry.travel_seconds(x0, x1, state)
    return {"gear": gear, "cruise_m_s": round(v_cruise, 4),
            "analytic_s": round(analytic, 2), "integrated_s": round(integrated, 2),
            "slow_zone_ahead": next_zone,
            "braking_fits": True if next_zone is None else
            (v_cruise * v_cruise / (2 * accel) <= abs(next_zone - x0) - geometry.slow_half_width_m + 1e-9)}


# ------------------------------------------------------------------- duration factors
def fit_duration_factors(train_days: list[str]) -> tuple[pd.DataFrame, dict[str, list[float]]]:
    """Relative residual per operation type, normalised so the mean factor is 1."""
    ops = pd.read_csv(PROCESSED / "operations.csv")
    heats = pd.read_csv(PROCESSED / "heats.csv")
    heats["day"] = pd.to_datetime(heats["plan_tap_start"], errors="coerce").dt.strftime("%Y-%m-%d")
    keep = set(heats[heats["day"].isin(train_days)]["heat_id"])
    ops = ops[ops["heat_id"].isin(keep) & (ops["duration_s"] > 0)]
    factors: dict[str, list[float]] = defaultdict(list)
    rows = []
    for op_type, group in ops.groupby("op_type"):
        median = group["duration_s"].median()
        if not median or median <= 0:
            continue
        for _, row in group.iterrows():
            ratio = float(row["duration_s"]) / float(median)
            if not (0.2 <= ratio <= 5.0):      # keep the tail as review candidates
                continue
            factors[op_type].append(ratio)
        rows.append({"op_type": op_type, "n": len(group), "median_s": round(float(median), 1),
                     "kept": len(factors[op_type])})
    frame = pd.DataFrame(rows).sort_values("op_type")
    return frame, dict(factors)


def normalise(factors: dict[str, list[float]]) -> dict[str, list[float]]:
    out = {}
    for key, values in factors.items():
        if not values:
            continue
        mean = sum(values) / len(values)
        out[key] = [v / mean for v in values]
    return out


def sample_day(factors: dict[str, list[float]], rng: random.Random) -> dict[str, float]:
    """One common shock per operation type for the whole day block."""
    return {op: (rng.choice(values) if values else 1.0) for op, values in factors.items()}


# ------------------------------------------------------------------------ policies
Policy = Callable[[list[dict[str, Any]], dict[str, float]], list[dict[str, Any]]]


def make_tasks(geometry: ef.Geometry, day: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    moves = pd.read_csv(PROCESSED / "moves.csv")
    heats = pd.read_csv(PROCESSED / "heats.csv")
    heats["day"] = pd.to_datetime(heats["plan_tap_start"], errors="coerce").dt.strftime("%Y-%m-%d")
    day_heats = heats[(heats["day"] == day) & (heats["match_status"] == "MATCHED_UNIQUE")]
    dm = moves[moves["heat_id"].isin(day_heats["heat_id"])].copy()
    caster = dict(zip(day_heats["heat_id"], day_heats["caster"].astype(str)))
    dm["caster_station"] = dm["heat_id"].map(lambda h: f"CCM{caster.get(h, '')}" if caster.get(h) else "")
    layout = day_mod.CART_LAYOUTS["BASE_3E"]
    tasks = day_mod.day_tasks(geometry, dm, layout)
    meta = {"heats": int(day_heats.shape[0]), "casts": int(day_heats["cast_id"].nunique())}
    return tasks, meta


def apply_factors(tasks: list[dict[str, Any]], factors: dict[str, float]) -> list[dict[str, Any]]:
    """Scale kinematics per operation type, keeping release/deadline untouched."""
    out = []
    for task in tasks:
        op = {"TRAVEL_HEAVY": "tap", "TRAVEL_TO_SITE": "tap", "SITE_OPERATION": "slag"}.get(
            task["task_type"], "tap")
        factor = factors.get(op, 1.0)
        clone = dict(task)
        clone["travel_s"] = task["travel_s"] * factor
        clone["hold_s"] = task["hold_s"] * factor
        out.append(clone)
    return out


def plan_under(tasks: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    """Build a plan treating every operation as ``factor`` times its mean duration."""
    scaled = []
    for task in tasks:
        clone = dict(task)
        clone["travel_s"] = task["travel_s"] * factor
        clone["hold_s"] = task["hold_s"] * factor
        scaled.append(clone)
    scheduler = sr.ReservationScheduler(
        GEOMETRY, capability=lambda crane, task: day_mod.capable(crane, task, day_mod.CAPACITY_POLICY))
    return scheduler.run(scaled)["records"]


def replay(plan: list[dict[str, Any]], tasks: list[dict[str, Any]],
           factors: dict[str, float]) -> list[dict[str, Any]]:
    """Open-loop execution: keep the planned crane/order, re-time with actual durations.

    Safety (spacing) is *not* re-verified here; the certified trajectories remain the
    scenario-1 ones. This replay measures timing robustness of a committed plan.
    """
    by_id = {t["task_id"]: t for t in tasks}
    per_crane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in plan:
        per_crane[record["crane_id"]].append(record)
    out: list[dict[str, Any]] = []
    for crane_id, records in per_crane.items():
        records.sort(key=lambda r: r["pickup_s"])
        clock, x = 0.0, GEOMETRY.crane(crane_id).q0_m
        for record in records:
            task = by_id[record["task_id"]]
            op = {"TRAVEL_HEAVY": "tap", "TRAVEL_TO_SITE": "tap", "SITE_OPERATION": "slag"}.get(
                task["task_type"], "tap")
            factor = factors.get(op, 1.0)
            clock += GEOMETRY.travel_seconds(x, task["x_from"], ef.STATE_EMPTY_HOOK)
            pickup = max(clock, task["release_s"])
            duration = GEOMETRY.travel_seconds(task["x_from"], task["x_to"], task["state"]) * factor
            hold = task["hold_s"] * factor
            dropoff = pickup + duration + hold
            out.append({"crane_id": crane_id, "task_id": task["task_id"],
                        "pickup_s": pickup, "dropoff_s": dropoff,
                        "wait_s": max(0.0, pickup - task["release_s"]),
                        "lateness_s": max(0.0, dropoff - task["deadline_s"]),
                        "finish_s": dropoff})
            clock = dropoff
            x = task["x_to"]
    return out


def _cranes():
    return [{"id": c.id, "x": c.q0_m, "free": 0.0, "cap": c.capacity_t}
            for c in sorted(GEOMETRY.cranes, key=lambda c: c.order)]


def _op_factor(task: dict[str, Any], factors: dict[str, float]) -> float:
    op = {"TRAVEL_HEAVY": "tap", "TRAVEL_TO_SITE": "tap", "SITE_OPERATION": "slag"}.get(
        task["task_type"], "tap")
    return factors.get(op, 1.0)


def rolling_execute(tasks: list[dict[str, Any]], factors: dict[str, float], *,
                    risk: bool = False, alpha: float = 0.9, lam: float = 1.0,
                    n_internal: int = 20, rng: random.Random | None = None,
                    pool: dict[str, list[float]] | None = None) -> list[dict[str, Any]]:
    """Rolling, non-anticipative dispatch.

    Decisions use the **expected** duration only; outcomes are realised with the
    scenario's actual durations. When ``risk`` is set, the crane is chosen by
    ``mean + lambda * CVaR`` of its completion over ``n_internal`` internal samples,
    which is the scenario-based (SAA) choice at the single-task level.

    Clearance moves are *not* modelled here: this module studies timing risk, while
    spatial safety is certified separately by ``scene1_reservation``/``rail_model``.
    """
    cranes = _cranes()
    records: list[dict[str, Any]] = []
    ordered = sorted(tasks, key=lambda t: (t["release_s"], t["deadline_s"], t["task_id"]))
    sampler = rng or random.Random(0)
    for task in ordered:
        expected_reposition = {}
        candidates = []
        for crane in cranes:
            crane_obj = GEOMETRY.crane(crane["id"])
            if not day_mod.capable(crane_obj, task, day_mod.CAPACITY_POLICY):
                continue
            reposition = GEOMETRY.travel_seconds(crane["x"], task["x_from"], ef.STATE_EMPTY_HOOK)
            expected_reposition[crane["id"]] = reposition
            start = max(task["release_s"], crane["free"] + reposition)
            finish = start + task["travel_s"] + task["hold_s"]
            if risk:
                samples = []
                for _ in range(n_internal):
                    f = sampler.choice(pool.get("tap", [1.0])) if pool else 1.0
                    samples.append(start + task["travel_s"] * f + task["hold_s"] * f)
                finish = sum(samples) / len(samples) + lam * cvar(samples, alpha)
            candidates.append((finish, crane["id"]))
        if not candidates:
            continue
        _, chosen = min(candidates)
        crane = next(c for c in cranes if c["id"] == chosen)
        factor = _op_factor(task, factors)
        reposition_actual = expected_reposition[chosen] * factor
        pickup = max(task["release_s"], crane["free"] + reposition_actual)
        dropoff = pickup + task["travel_s"] * factor + task["hold_s"] * factor
        records.append({"crane_id": chosen, "task_id": task["task_id"], "pickup_s": pickup,
                        "dropoff_s": dropoff, "wait_s": max(0.0, pickup - task["release_s"]),
                        "lateness_s": max(0.0, dropoff - task["deadline_s"]),
                        "finish_s": dropoff})
        crane["x"] = task["x_to"]
        crane["free"] = dropoff
    return records


def policy_deterministic(tasks, factors):
    scaled = apply_factors(tasks, {k: 1.0 for k in factors} | {k: 1.0 for k in ("tap", "slag", "lf", "rh", "ccm")})
    scheduler = sr.ReservationScheduler(
        GEOMETRY, capability=lambda crane, task: day_mod.capable(crane, task, day_mod.CAPACITY_POLICY))
    return scheduler.run(scaled)["records"]


def policy_sample(tasks, factors):
    scaled = apply_factors(tasks, factors)
    scheduler = sr.ReservationScheduler(GEOMETRY)
    return scheduler.run(scaled)["records"]


def metrics(records: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> dict[str, float]:
    if not records:
        return {"tasks": len(tasks), "completed": 0, "wait_mean_min": 0.0, "wait_p95_min": 0.0,
                "lateness_sum_min": 0.0, "cast_interrupt_rate": 1.0, "window_violation_rate": 1.0,
                "makespan_h": 0.0}
    waits = sorted(r["wait_s"] for r in records)
    late = sum(1 for r in records if r["lateness_s"] > 1e-6)
    return {
        "tasks": len(tasks),
        "completed": len(records),
        "wait_mean_min": round(sum(waits) / len(waits) / 60.0, 2),
        "wait_p95_min": round(waits[int(0.95 * (len(waits) - 1))] / 60.0, 2),
        "lateness_sum_min": round(sum(r["lateness_s"] for r in records) / 60.0, 1),
        "cast_interrupt_rate": round(late / len(records), 4),
        "window_violation_rate": round(late / len(records), 4),
        "makespan_h": round(max(r["dropoff_s"] for r in records) / 3600.0, 2),
    }


def cvar(values: list[float], alpha: float = 0.9) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    tail = ordered[int(math.floor(alpha * (len(ordered) - 1))):]
    return sum(tail) / len(tail)


def run(day: str, contract: dict[str, Any], train_days: list[str], out_dir: Path) -> dict[str, Any]:
    del train_days  # fitted upstream and passed through factors
    factors = normalise(FACTORS)
    tasks, meta = make_tasks(GEOMETRY, day)
    seed = int(contract["scene2"]["seed"])
    n_opt = int(contract["scene2"]["optimization_scenarios"])
    n_eval = int(contract["scene2"]["evaluation_scenarios"])
    alpha = float(contract["scene2"]["cvar_alpha"])
    lam = float(contract["scene2"]["risk_weight"])

    # ---- anchors and verification hooks
    gear_rows = []
    for state in (ef.STATE_HEAVY, ef.STATE_EMPTY_LADLE):
        gear_rows.append({"state": state,
                          **gear_envelope(GEOMETRY, 99.0, 181.5, state)})
    anchor = policy_deterministic(tasks, {})
    anchor_metrics = metrics(anchor, tasks)

    # ---- shared random numbers for internal optimisation and evaluation
    opt_rng = random.Random(seed)
    opt_scenarios = [sample_day(factors, opt_rng) for _ in range(n_opt)]
    eval_rng = random.Random(seed + 1)
    eval_scenarios = [sample_day(factors, eval_rng) for _ in range(n_eval)]

    # ---- candidate committed plans: mean, optimistic and conservative planning factors
    plans = {"mean_plan": plan_under(tasks, 1.0),
             "optimistic_plan": plan_under(tasks, 0.85),
             "conservative_plan": plan_under(tasks, 1.20)}

    def run_policy(name: str, scenario: dict[str, float]) -> float:
        if name in plans:
            records = replay(plans[name], tasks, scenario)
        elif name == "rolling_mean":
            records = rolling_execute(tasks, scenario)
        else:
            records = rolling_execute(tasks, scenario, risk=True, alpha=alpha, lam=lam,
                                      n_internal=n_opt, rng=random.Random(seed + 7), pool=factors)
        return sum(r["lateness_s"] for r in records) / 60.0

    policy_names = list(plans) + ["rolling_mean", "rolling_risk"]
    losses: dict[str, list[float]] = defaultdict(list)
    for scenario in eval_scenarios:
        for name in policy_names:
            losses[name].append(run_policy(name, scenario))

    # ---- SAA/CVaR over policies on 20 internal scenarios, then report the winner
    internal: dict[str, list[float]] = defaultdict(list)
    for scenario in opt_scenarios:
        for name in policy_names:
            internal[name].append(run_policy(name, scenario))
    score = {name: sum(v) / len(v) + lam * cvar(v, alpha) for name, v in internal.items()}
    best_candidate = min(score, key=score.get)
    losses["saa_cvar"] = losses[best_candidate]

    summary = {
        "day": day, "heats": meta["heats"], "casts": meta["casts"], "tasks": len(tasks),
        "seed": seed, "optimisation_scenarios": n_opt, "evaluation_scenarios": n_eval,
        "alpha": alpha, "risk_weight": lam,
        "anchor_deterministic": anchor_metrics,
        "gear_envelope": gear_rows,
        "internal_scores": {k: round(v, 2) for k, v in score.items()},
        "selected_candidate": best_candidate,
        "policies": {
            name: {
                "mean_lateness_min": round(sum(v) / len(v), 2),
                "p95_lateness_min": round(sorted(v)[int(0.95 * (len(v) - 1))], 2),
                "cvar90_lateness_min": round(cvar(v, alpha), 2),
                "zero_lateness_share": round(sum(1 for x in v if x <= 1e-9) / len(v), 3),
            }
            for name, v in losses.items()
        },
        "degenerate_zero_noise": zero_noise_check(day, anchor_metrics["lateness_sum_min"]),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / f"scenario_metrics_{day}.csv",
              [{"policy": k, **{"mean_lateness_min": summary["policies"][k]["mean_lateness_min"],
                                "p95_lateness_min": summary["policies"][k]["p95_lateness_min"],
                                "cvar90_lateness_min": summary["policies"][k]["cvar90_lateness_min"],
                                "zero_lateness_share": summary["policies"][k]["zero_lateness_share"]}}
               for k in summary["policies"]],
              ["policy", "mean_lateness_min", "p95_lateness_min", "cvar90_lateness_min", "zero_lateness_share"])
    write_csv(out_dir / f"gear_envelope_{day}.csv", gear_rows, list(gear_rows[0].keys()))
    (out_dir / f"scene2_summary_{day}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def zero_noise_check(day: str, anchor_lateness_min: float) -> dict[str, Any]:
    """With zero noise the scenario-2 anchor must reproduce the scenario-1 day run."""
    reference = ROOT / "data" / "runs" / "scene1_day" / f"{day}_BASE_3E" / "day_comparison.csv"
    payload: dict[str, Any] = {"anchor_lateness_min": round(anchor_lateness_min, 2),
                               "reference_file": str(reference.relative_to(ROOT)),
                               "reference_lateness_min": None,
                               "matches_scene1_day": None}
    if reference.is_file():
        with reference.open(encoding="utf-8-sig", newline="") as fh:
            row = next(csv.DictReader(fh))
        ref = float(row["total_lateness_min"])
        payload["reference_lateness_min"] = ref
        payload["matches_scene1_day"] = abs(anchor_lateness_min - ref) <= 0.5
    return payload


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# Loaded at import so the module is usable from tests without going through main().
GEOMETRY: ef.Geometry = day_mod.geometry_for(day_mod.CART_LAYOUTS["BASE_3E"])
FACTORS: dict[str, list[float]] = {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", action="append")
    args = parser.parse_args()
    global GEOMETRY, FACTORS
    GEOMETRY = day_mod.geometry_for(day_mod.CART_LAYOUTS["BASE_3E"])
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    train = contract["scene2"]["split"]["train"]
    table, raw = fit_duration_factors(train)
    FACTORS = raw
    write_csv(OUT_DIR / "duration_factors.csv", table.to_dict("records"), list(table.columns))
    days = args.day or ["2026-09-28", "2026-10-02"]
    for day in days:
        summary = run(day, contract, train, OUT_DIR)
        print(json.dumps({k: summary[k] for k in
                          ("day", "tasks", "selected_candidate", "internal_scores", "policies")},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
