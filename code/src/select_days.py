#!/usr/bin/env python3
"""Pick one normal day and one peak day from the matched heats.

Selection rule (documented, not hand-picked):
* operating day = calendar date of the planned tap start;
* only days whose matched heats form a usable sample are eligible
  (at least ``MIN_HEATS`` matched heats and at least one complete cast);
* **normal day** = the day whose matched-heat count is closest to the median of
  eligible days (ties broken by more complete casts, then earlier date);
* **peak day** = the eligible day with the most matched heats (ties: more casts,
  then earlier date).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
MIN_HEATS = 10


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed", type=Path, default=PROCESSED)
    args = parser.parse_args()

    heats = pd.read_csv(args.processed / "heats.csv")
    moves = pd.read_csv(args.processed / "moves.csv")
    heats["day"] = pd.to_datetime(heats["plan_tap_start"], errors="coerce").dt.strftime("%Y-%m-%d")
    matched = heats[heats["match_status"] == "MATCHED_UNIQUE"].copy()

    rows = []
    for day, group in matched.groupby("day"):
        cast_ids = set(group["cast_id"].dropna())
        complete_casts = sum(1 for c in cast_ids if c in set(moves["cast_id"].dropna()))
        rows.append({
            "day": day,
            "matched_heats": len(group),
            "casts": len(cast_ids),
            "casts_with_moves": complete_casts,
            "routes": "|".join(sorted(group["route"].dropna().unique())),
            "casters": "|".join(sorted({str(v) for v in group["caster"].dropna()})),
        })
    frame = pd.DataFrame(rows).sort_values("day").reset_index(drop=True)
    eligible = frame[(frame["matched_heats"] >= MIN_HEATS) & (frame["casts_with_moves"] >= 1)].copy()

    median_heats = eligible["matched_heats"].median()
    eligible["distance_to_median"] = (eligible["matched_heats"] - median_heats).abs()
    normal = eligible.sort_values(
        ["distance_to_median", "casts_with_moves", "day"],
        ascending=[True, False, True]).iloc[0]
    peak = eligible.sort_values(
        ["matched_heats", "casts_with_moves", "day"],
        ascending=[False, False, True]).iloc[0]

    selection = [
        {"role": "normal", "day": normal["day"], "matched_heats": int(normal["matched_heats"]),
         "casts": int(normal["casts"]), "casts_with_moves": int(normal["casts_with_moves"]),
         "routes": normal["routes"], "casters": normal["casters"],
         "rule": f"closest to median of eligible days ({median_heats:g} heats)"},
        {"role": "peak", "day": peak["day"], "matched_heats": int(peak["matched_heats"]),
         "casts": int(peak["casts"]), "casts_with_moves": int(peak["casts_with_moves"]),
         "routes": peak["routes"], "casters": peak["casters"],
         "rule": "maximum matched heats among eligible days"},
    ]

    with (args.processed / "day_calendar.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(frame.columns))
        writer.writeheader()
        writer.writerows(frame.to_dict("records"))
    with (args.processed / "day_selection.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(selection[0].keys()))
        writer.writeheader()
        writer.writerows(selection)

    summary = {
        "eligible_days": len(eligible),
        "calendar_days": len(frame),
        "median_matched_heats_on_eligible_days": float(median_heats),
        "min_heats_threshold": MIN_HEATS,
        "selection": selection,
        "span": {"first": str(frame["day"].min()), "last": str(frame["day"].max())},
        "notes": [
            "Days are calendar dates of the planned tap start.",
            "Only matched (MATCHED_UNIQUE) heats are counted; unresolved heats are excluded and reported.",
        ],
    }
    (args.processed / "day_selection.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
