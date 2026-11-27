#!/usr/bin/env python3
"""150 t auxiliary-crane sensitivity harness.

The modelling lead has not yet supplied ``aux_max_load_t``, so the shipped policy
keeps the 150 t cranes idle. This harness is ready for that number: pass
``--threshold`` values (tonnes) and it re-runs the whole-day scenario-1 plan with
those thresholds.

**Placeholder runs are labelled as such** in the output: they exercise the pipeline,
they are not the contractual result.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

import scene1_day as day_mod

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "runs" / "scene1_aux"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", action="append", type=float)
    parser.add_argument("--day", action="append")
    parser.add_argument("--layout", default="BASE_3E")
    parser.add_argument("--label", default="placeholder")
    args = parser.parse_args()

    thresholds = args.threshold or [100.0, 120.0, 140.0]
    days = args.day or ["2026-09-28", "2026-10-02"]
    rows: list[dict[str, Any]] = []
    for day in days:
        for threshold in [None] + thresholds:
            policy = day_mod.load_capacity_policy(threshold)
            payload = day_mod.run_day(day, args.layout, out_root=OUT_DIR / f"aux_{threshold}", policy=policy)
            row = payload["result"]
            row["aux_threshold_t"] = threshold
            row["label"] = "disabled" if threshold is None else args.label
            rows.append(row)
            print(f"{day} thr={str(threshold):>5} done={row['completed']:>4}/{row['tasks']:>4} "
                  f"wait={row['total_wait_min']:>8.1f} late={row['total_lateness_min']:>7.1f} "
                  f"cranes={row['cranes_used']} gate={row['gate']}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "aux_sensitivity.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (OUT_DIR / "aux_sensitivity.json").write_text(
        json.dumps({"label": args.label,
                    "status": "awaiting_wj_weight_threshold" if args.label == "placeholder" else "contractual",
                    "policy_file": "code/configs/capacity_policy_v1.json",
                    "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
