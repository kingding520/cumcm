#!/usr/bin/env python3
"""One-click reproduction entry for the scenario-1/2/3 pipeline.

Usage
-----
    python code/run_all.py --check          # verify artifacts, run nothing
    python code/run_all.py --fast           # ETL + days + BASE_3E day + scenes 2/3 + tests
    python code/run_all.py                  # adds the full 12-run cart sensitivity and the 145 t aux run

Every step shells out to the same script a reviewer would call by hand, so a green
run here means the documented commands work. Steps are printed with their exit code
and wall time; a failing step stops the run (unless ``--keep-going``).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SRC = ROOT / "code" / "src"

STEPS: dict[str, list[list[str]]] = {
    "etl": [[PY, str(SRC / "etl_full.py")]],
    "days": [[PY, str(SRC / "select_days.py")]],
    "scene1_cast": [[PY, str(SRC / "scene1_yield.py")]],
    "scene1_day_base": [[PY, str(SRC / "scene1_day.py"), "--layout", "BASE_3E"]],
    "scene1_day_full": [[PY, str(SRC / "scene1_day.py"),
                         *sum((["--layout", name] for name in
                               ("BASE_3E", "COUNT_2E", "COUNT_4E",
                                "WEST_3_80", "WEST_3_85", "WEST_3_90")), [])]],
    "aux_145": [[PY, str(SRC / "aux_crane_sweep.py"), "--threshold", "145.0", "--label", "wcl145"]],
    "scene2": [[PY, str(SRC / "scene2_stochastic.py")]],
    "scene3": [[PY, str(SRC / "scene3_disruption.py")]],
    "tests": [[PY, "-m", "pytest", str(ROOT / "code" / "tests"), "-q"]],
}

# Artifacts a reviewer should find after a complete run.
EXPECTED = [
    "data/processed/heats.csv",
    "data/processed/operations.csv",
    "data/processed/moves.csv",
    "data/processed/etl_summary.json",
    "data/processed/day_selection.json",
    "data/runs/scene1_yield_6C0754/trajectory.csv.gz",
    "data/runs/scene1_day/2026-09-28_BASE_3E/day_comparison.csv",
    "data/runs/scene1_day/2026-10-02_BASE_3E/day_comparison.csv",
    "data/runs/scene1_day/cart_layout_sensitivity.csv",
    "data/runs/scene1_aux/aux_sensitivity_wcl145.csv",
    "data/runs/scene2/scene2_summary_2026-09-28.json",
    "data/runs/scene3/recovery_metrics_2026-09-28.csv",
]


def check() -> int:
    missing = []
    for rel in EXPECTED:
        path = ROOT / rel
        mark = "ok " if path.is_file() else "MISS"
        size = f"{path.stat().st_size:,} B" if path.is_file() else "-"
        print(f"  [{mark}] {rel:<62} {size}")
        if not path.is_file():
            missing.append(rel)
    print(f"\n{len(EXPECTED) - len(missing)}/{len(EXPECTED)} artifacts present")
    return 0 if not missing else 1


def run(steps: list[str], keep_going: bool) -> int:
    failures: list[tuple[str, int]] = []
    for name in steps:
        for command in STEPS[name]:
            print(f"\n=== {name}: {' '.join(command[-3:])} ===", flush=True)
            started = time.perf_counter()
            code = subprocess.call(command, cwd=ROOT)
            elapsed = time.perf_counter() - started
            print(f"--- {name} exit={code} ({elapsed:.1f}s)", flush=True)
            if code != 0:
                failures.append((name, code))
                if not keep_going:
                    print("\nstopping on first failure (use --keep-going to continue)")
                    return code
    if failures:
        print("\nfailures: " + ", ".join(f"{n}(exit={c})" for n, c in failures))
        return 1
    print("\nall steps passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only verify artifacts exist")
    parser.add_argument("--fast", action="store_true",
                        help="skip the 12-run sensitivity (single BASE_3E day run)")
    parser.add_argument("--only", action="append", choices=sorted(STEPS))
    parser.add_argument("--keep-going", action="store_true")
    args = parser.parse_args()

    if args.check:
        return check()

    if args.only:
        steps = args.only
    else:
        steps = ["etl", "days", "scene1_cast", "scene1_day_base", "scene2", "scene3", "tests"]
        if not args.fast:
            steps = ["etl", "days", "scene1_cast", "scene1_day_full", "aux_145",
                     "scene2", "scene3", "tests"]
    return run(steps, args.keep_going)


if __name__ == "__main__":
    raise SystemExit(main())
