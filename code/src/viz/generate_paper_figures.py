"""Generate publication-grade figures for the MCM/ICM paper from verified audit and run outputs."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
AUDIT_DIR = ROOT / "data" / "audit"
REPLAY_DIR = ROOT / "data" / "runs" / "scene1_replay_6C0754"
CLOSED_LOOP_DIR = ROOT / "data" / "runs" / "scene1_closed_loop_6C0754"
YIELD_DIR = ROOT / "data" / "runs" / "scene1_yield_6C0754"
FIG_DIR = ROOT / "paper" / "figures"

# Okabe-Ito colorblind-safe palette bound semantically to cranes & stages
COLORS = {
    "3#450": "#E69F00",  # warm amber (bridge crane)
    "4#450": "#0072B2",  # deep sea blue (west crane)
    "5#450": "#009E73",  # teal green (east crane)
    "plan": "#7A7D9B",   # muted slate
    "actual": "#0072B2",
    "alert": "#D55E00",  # vermilion
    "neutral": "#56B4E9",
}


def setup_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linestyle": "--",
            "figure.dpi": 150,
            "savefig.dpi": 300,
        }
    )


def save_both(fig: plt.Figure, stem: str) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_DIR / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(FIG_DIR / f"{stem}.png", bbox_inches="tight", dpi=300)
    plt.close(fig)


def plot_data_audit_durations() -> None:
    stats_path = AUDIT_DIR / "basic_duration_stats.csv"
    rows = list(csv.DictReader(stats_path.open(encoding="utf-8-sig")))
    by_key = {(r["metric"], r["route"]): r for r in rows}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.2), gridspec_kw={"width_ratios": [1.15, 1.0]})

    # Panel (a): Planned vs Actual Durations (ALL route) with P10-P90 intervals
    stages = [
        ("Plan\nConverter", "plan_converter_duration", "plan"),
        ("Actual\nTapping", "actual_tapping_duration", "actual"),
        ("Actual\nLF", "actual_lf_process_duration", "actual"),
        ("Actual\nRH", "actual_rh_process_duration", "actual"),
        ("Plan\nCasting", "plan_cast_duration", "plan"),
        ("Actual\nCasting", "actual_ccm_cast_duration", "actual"),
    ]
    x = np.arange(len(stages))
    medians, p10s, p90s, colors, ns = [], [], [], [], []
    for _, metric, kind in stages:
        r = by_key[(metric, "ALL")]
        med = float(r["median"])
        medians.append(med)
        p10s.append(med - float(r["p10"]))
        p90s.append(float(r["p90"]) - med)
        colors.append(COLORS[kind])
        ns.append(int(r["clean_nonnegative_rows"]))

    bars = ax1.bar(x, medians, color=colors, width=0.58, alpha=0.88, edgecolor="white")
    ax1.errorbar(x, medians, yerr=[p10s, p90s], fmt="none", ecolor="#222222", elinewidth=1.2, capsize=3.5)
    for idx, (b, med, n) in enumerate(zip(bars, medians, ns)):
        top = med + p90s[idx]
        ax1.text(b.get_x() + b.get_width() / 2, top + 1.5, f"{med:.1f}m\n(n={n})", ha="center", va="bottom", fontsize=7.5)

    ax1.set_xticks(x)
    ax1.set_xticklabels([s[0] for s in stages])
    ax1.set_ylabel("Duration (minutes)")
    ax1.set_ylim(0, 62)
    ax1.set_title("(a) Planned vs. Actual Stage Durations (Median & P10–P90)")
    ax1.legend(
        handles=[
            mpatches.Patch(color=COLORS["plan"], label="Nominal Plan (1,266 heats)"),
            mpatches.Patch(color=COLORS["actual"], label="Clean Observed Logs"),
        ],
        frameon=False,
        loc="upper left",
        fontsize=8,
    )

    # Panel (b): Route Mix (valid 1,266 heats) & Source Audit Findings
    routes = [("L0\n(LF only)", 668), ("R0\n(RH only)", 420), ("R0L0\n(RH→LF)", 108), ("L0R0\n(LF→RH)", 70)]
    rx = np.arange(len(routes))
    counts = [r[1] for r in routes]
    route_colors = ["#0072B2", "#009E73", "#E69F00", "#CC79A7"]
    rbars = ax2.bar(rx, counts, color=route_colors, width=0.54, alpha=0.9)
    total = sum(counts)
    for b, c in zip(rbars, counts):
        ax2.text(b.get_x() + b.get_width() / 2, c + 14, f"{c}\n({100*c/total:.1f}%)", ha="center", va="bottom", fontsize=8)

    ax2.set_xticks(rx)
    ax2.set_xticklabels([r[0] for r in routes])
    ax2.set_ylabel("Operational Heats (count)")
    ax2.set_ylim(0, 790)
    ax2.set_title("(b) Valid Refining Route Distribution (n = 1,266; 55 '-2' Excluded)")

    # Inset callout for audit totals
    ax2.text(
        0.97,
        0.93,
        "12 Workbooks Audited\n6,758 Raw Rows\n2,529 Review Flags\n(0 Silent Deletions)",
        transform=ax2.transAxes,
        ha="right",
        va="top",
        fontsize=7.8,
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#F7F7F7", edgecolor="#CCCCCC"),
    )

    fig.tight_layout()
    save_both(fig, "fig3_data_audit_durations")


def plot_replay_6c0754() -> None:
    moves = list(csv.DictReader((REPLAY_DIR / "moves.csv").open(encoding="utf-8-sig")))
    ops = list(csv.DictReader((REPLAY_DIR / "operations.csv").open(encoding="utf-8-sig")))
    metrics = json.loads((REPLAY_DIR / "metrics.json").read_text(encoding="utf-8"))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.4), gridspec_kw={"width_ratios": [1.35, 1.0]})

    # Panel (a): Observed Crane Gantt for Cast 6C0754
    cranes = ["4#450", "3#450", "5#450"]
    y_map = {c: i for i, c in enumerate(cranes)}

    for m in moves:
        cid = m["crane_id"]
        y = y_map[cid]
        t0 = float(m["pickup_s"]) / 60.0
        dur = max(float(m["dropoff_s"]) - float(m["pickup_s"]), 1.0) / 60.0
        is_empty = m["task_type"] == "empty"
        hatch = "///" if is_empty else None
        alpha = 0.65 if is_empty else 0.92
        ax1.barh(
            y,
            dur,
            left=t0,
            height=0.52,
            color=COLORS[cid],
            alpha=alpha,
            edgecolor="#222222" if is_empty else "white",
            hatch=hatch,
            linewidth=0.7,
        )
        if dur >= 8.0:
            short_h = m["heat_id"][-4:]
            tag = "E" if is_empty else m["route_step"].replace("_to_", "→").replace("BOF", "B").replace("CCM", "C")
            ax1.text(t0 + dur / 2, y, f"{short_h}\n{tag}", ha="center", va="center", fontsize=6.5, color="#111111")

    busy = metrics["crane_busy_minutes_by_observed_id"]
    ax1.set_yticks(range(len(cranes)))
    ax1.set_yticklabels([f"{c}\n({busy[c]:.1f}m)" for c in cranes])
    ax1.set_xlabel("Elapsed Time from First Pickup (minutes)")
    ax1.set_title("(a) Observed Crane Replay for Cast 6C0754 (18 Tasks, 0 Same-Crane Overlap)")
    ax1.legend(
        handles=[
            mpatches.Patch(facecolor="#888888", alpha=0.9, label="Heavy Ladle (405.5–421.3 t)"),
            mpatches.Patch(facecolor="#888888", alpha=0.65, hatch="///", edgecolor="#222222", label="Empty Return (137.0–149.9 t)"),
        ],
        frameon=False,
        loc="upper left",
        fontsize=7.8,
    )

    # Panel (b): Cast Start Delay & Thermal Age Across the 6-Heat Sequence
    seqs = [int(o["sequence"]) for o in ops]
    heats = [o["heat_id"][-4:] for o in ops]
    delays = [float(o["cast_start_delay_vs_plan_min"]) for o in ops]
    thermals = [float(o["thermal_age_rh_end_to_cast_start_min"]) for o in ops]

    ax2.plot(seqs, delays, marker="o", color=COLORS["alert"], linewidth=2.0, label="Cast-Start Delay vs. Plan (min)")
    ax2.plot(seqs, thermals, marker="s", color=COLORS["4#450"], linewidth=2.0, linestyle="--", label="Thermal Age: RH End → Cast Start (min)")
    ax2.axhline(0, color="#666666", linewidth=0.9, linestyle=":")

    for s, d, th in zip(seqs, delays, thermals):
        if abs(th - d) < 2.5:
            ax2.text(s, max(d, th) + 1.1, f"{max(d, th):+.1f}" if d >= th else f"{th:.1f}", ha="center", va="bottom", fontsize=7.5, color=COLORS["alert"] if d >= th else COLORS["4#450"])
            ax2.text(s, min(d, th) - 2.2, f"{min(d, th):.1f}" if th <= d else f"{d:+.1f}", ha="center", va="top", fontsize=7.5, color=COLORS["4#450"] if th <= d else COLORS["alert"])
        else:
            ax2.text(s, d - 2.3 if d < 5 else d + 1.1, f"{d:+.1f}", ha="center", va="bottom", fontsize=7.5, color=COLORS["alert"])
            ax2.text(s, th + 1.2, f"{th:.1f}", ha="center", va="bottom", fontsize=7.5, color=COLORS["4#450"])

    ax2.set_xticks(seqs)
    ax2.set_xticklabels([f"#{s}\n({h})" for s, h in zip(seqs, heats)])
    ax2.set_ylabel("Time (minutes)")
    ax2.set_ylim(-5, 38)
    ax2.set_title("(b) Schedule Drift & Thermal Age Escalation (Cast 6C0754)")
    ax2.legend(frameon=False, loc="upper left", fontsize=7.8)

    fig.tight_layout()
    save_both(fig, "fig4_replay_6C0754_gantt")


def plot_closed_loop_6c0754() -> None:
    comp = list(csv.DictReader((CLOSED_LOOP_DIR / "optimization_comparison.csv").open(encoding="utf-8-sig")))
    traj_cpsat = list(csv.DictReader((CLOSED_LOOP_DIR / "trajectory_cp_sat_exact_setup.csv").open(encoding="utf-8-sig")))
    traj_serial = list(csv.DictReader((CLOSED_LOOP_DIR / "trajectory_conservative_serial.csv").open(encoding="utf-8-sig")))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.8, 4.5), gridspec_kw={"width_ratios": [1.05, 1.25]})

    # Panel (a): 5-Strategy Waiting & Lateness Comparison + Safety Verdict
    label_map = {
        "observed_historical": "Observed\nReplay",
        "fcfs": "FCFS\n(No Yield)",
        "edf_nearest": "EDF-Nearest\n(No Yield)",
        "cp_sat_exact_setup": "CP-SAT\n(No Yield)",
        "conservative_serial": "Safe Serial\n(1 Crane)",
    }
    labels = [label_map[r["algorithm"]] for r in comp]
    waits = [float(r["total_task_wait_min"]) for r in comp]
    lates = [float(r["total_lateness_min"]) for r in comp]
    accepted = [r["accepted"].strip().lower() == "yes" for r in comp]

    x = np.arange(len(comp))
    w = 0.38
    bar_colors = [COLORS["5#450"] if ok else COLORS["alert"] for ok in accepted]
    b1 = ax1.bar(x - w / 2, waits, width=w, color=bar_colors, alpha=0.88, label="Total Task Wait (min)")
    b2 = ax1.bar(x + w / 2, lates, width=w, color="#7A7D9B", alpha=0.85, label="Total Lateness (min)")

    for rect, val, ok in zip(b1, waits, accepted):
        tag = f"{val:.1f}\n(PASS)" if ok else f"{val:.1f}\n(FAIL)"
        ax1.text(rect.get_x() + rect.get_width() / 2, val + 3.0, tag, ha="center", va="bottom", fontsize=7.0,
                 color=COLORS["5#450"] if ok else COLORS["alert"], fontweight="bold")
    for rect, val in zip(b2, lates):
        ax1.text(rect.get_x() + rect.get_width() / 2, val + 3.0, f"{val:.1f}", ha="center", va="bottom", fontsize=7.0, color="#444444")

    ax1.annotate(
        "+87.6 min safety\npenalty without yielding",
        xy=(4 - w / 2, 109.5),
        xytext=(2.35, 150),
        arrowprops=dict(arrowstyle="->", color="#222222", lw=1.1),
        fontsize=7.8,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#FFF8E7", edgecolor="#E69F00"),
    )
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.set_ylabel("Time (minutes)")
    ax1.set_ylim(0, 240)
    ax1.set_title("(a) 6C0754 Closed-Loop Comparison (Provisional Params)")
    ax1.legend(
        handles=[
            mpatches.Patch(color=COLORS["alert"], alpha=0.88, label="Wait (Track Collision)"),
            mpatches.Patch(color=COLORS["5#450"], alpha=0.88, label="Wait (Track Safe: +60 px)"),
            mpatches.Patch(color="#7A7D9B", alpha=0.85, label="Cast-Readiness Lateness"),
        ],
        frameon=False,
        loc="upper right",
        fontsize=7.3,
    )

    # Panel (b): Space-Time Trajectory Contrast (CP-SAT No-Yield Collision vs Safe Serial)
    cranes_to_plot = ["4#450", "3#450", "5#450"]
    for cid in cranes_to_plot:
        segs = [r for r in traj_cpsat if r["crane_id"] == cid]
        xs, ts = [], []
        for s in segs:
            ts.extend([float(s["t_start_s"]) / 60.0, float(s["t_end_s"]) / 60.0])
            xs.extend([float(s["x_start"]), float(s["x_end"])])
        ax2.plot(ts, xs, color=COLORS[cid], linewidth=1.35, alpha=0.88, label=f"{cid} (CP-SAT No-Yield)")

    # Overlay safe serial 5#450 dashed
    segs_ser = [r for r in traj_serial if r["crane_id"] == "5#450"]
    ts_s, xs_s = [], []
    for s in segs_ser:
        ts_s.extend([float(s["t_start_s"]) / 60.0, float(s["t_end_s"]) / 60.0])
        xs_s.extend([float(s["x_start"]), float(s["x_end"])])
    ax2.plot(ts_s, xs_s, color="#111111", linestyle="--", linewidth=1.2, alpha=0.8, label="5#450 (Safe Serial)")

    # Highlight worst CP-SAT violation at t = 9663.78 s (161.06 min)
    ax2.axvline(9663.78 / 60.0, color=COLORS["alert"], linestyle=":", linewidth=1.2)
    ax2.annotate(
        "Worst CP-SAT crossing\n-1520 px (4#450 vs 3#450)",
        xy=(9663.78 / 60.0, 2000),
        xytext=(12, 2320),
        arrowprops=dict(arrowstyle="->", color=COLORS["alert"], lw=1.1),
        fontsize=7.3,
        color=COLORS["alert"],
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="#FFF5F0", edgecolor=COLORS["alert"], alpha=0.95),
    )

    ax2.set_xlabel("Elapsed Time (minutes)")
    ax2.set_ylabel("Rail Coordinate (px, West → East)")
    ax2.set_ylim(450, 2500)
    ax2.set_title("(b) Space-Time Trajectory: Crossing Collision vs. Safe Serial")
    ax2.legend(frameon=False, loc="lower right", fontsize=7.2, ncol=2)

    fig.tight_layout()
    save_both(fig, "fig5_closed_loop_6C0754")


def plot_yield_6c0754() -> None:
    comp = list(csv.DictReader((YIELD_DIR / "comparison_yield.csv").open(encoding="utf-8-sig")))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.0, 4.6), gridspec_kw={"width_ratios": [1.05, 1.25]})

    # Panel (a): 7-candidate comparison under ef_initial_v1.json
    label_map = {
        "fcfs_nearest": "FCFS-Nearest\n(Yield)",
        "pipeline_roles": "Pipeline\n(Yield)",
        "serial_4#450": "Serial\n(4#450)",
        "serial_3#450": "Serial\n(3#450)",
        "serial_5#450": "Serial\n(5#450)",
        "cp_sat_assignment": "CP-SAT\n(Blind)",
        "pipeline_no_yield": "Pipeline\n(No Yield)",
    }
    order = ["fcfs_nearest", "pipeline_roles", "serial_4#450", "serial_3#450", "serial_5#450", "cp_sat_assignment", "pipeline_no_yield"]
    by_alg = {r["algorithm"]: r for r in comp}
    ordered = [by_alg[k] for k in order]

    labels = [label_map[r["algorithm"]] for r in ordered]
    waits = [float(r["total_wait_min"]) for r in ordered]
    done = [int(r["completed_tasks"]) for r in ordered]
    yields = [int(r["yield_events"]) for r in ordered]
    accepted = [r["accepted"].strip().lower() == "true" for r in ordered]

    x = np.arange(len(ordered))
    colors = [COLORS["5#450"] if ok else COLORS["alert"] for ok in accepted]
    bars = ax1.bar(x, waits, width=0.58, color=colors, alpha=0.88, edgecolor="white")

    for rect, w, d, yld, ok in zip(bars, waits, done, yields, accepted):
        status = f"{w:.2f}m\n({d}/18, y={yld})" if ok else f"DEADLOCK\n({d}/18, y={yld})"
        ax1.text(
            rect.get_x() + rect.get_width() / 2,
            w + 0.35,
            status,
            ha="center",
            va="bottom",
            fontsize=6.8,
            color="#114B3A" if ok else COLORS["alert"],
            fontweight="bold",
        )

    ax1.annotate(
        "-6.98 min (-93.1%)\nvs. best serial",
        xy=(0, 1.35),
        xytext=(0.15, 5.8),
        arrowprops=dict(arrowstyle="->", color="#222222", lw=1.1),
        fontsize=7.6,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#E8F6F0", edgecolor=COLORS["5#450"]),
    )
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=7.6)
    ax1.set_ylabel("Total Task Wait (minutes)")
    ax1.set_ylim(0, 12.2)
    ax1.set_title("(a) Proactive Yielding vs. Serial & Deadlock (ef-initial-v1)")

    # Panel (b): Certified 5-Crane Space-Time Trajectory (fcfs_nearest, min gap 12.24 m)
    crane_colors = {
        "2#150": "#7A7D9B",
        "4#450": COLORS["4#450"],
        "3#450": COLORS["3#450"],
        "5#450": COLORS["5#450"],
        "1#150": "#999933",
    }
    crane_pts: dict[str, tuple[list[float], list[float]]] = {c: ([], []) for c in crane_colors}
    with gzip.open(YIELD_DIR / "trajectory.csv.gz", "rt", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            cid = row["crane_id"]
            if cid not in crane_pts:
                continue
            # Keep every 10s or any moving/non-idle segment to preserve all motion peaks cleanly
            if row["phase"] != "idle" or idx % 10 == 0:
                crane_pts[cid][0].append(float(row["t_start_s"]) / 60.0)
                crane_pts[cid][1].append(float(row["x_start"]))

    for cid, (ts, xs) in crane_pts.items():
        lw = 1.6 if cid in ("4#450", "3#450") else 1.1
        ls = "-" if cid in ("4#450", "3#450") else "--"
        ax2.plot(ts, xs, color=crane_colors[cid], linewidth=lw, linestyle=ls, alpha=0.92, label=cid)

    ax2.set_xlabel("Elapsed Time (minutes)")
    ax2.set_ylabel("Rail Position x (meters, West → East)")
    ax2.set_ylim(0, 240)
    ax2.set_title("(b) Certified 5-Crane Non-Crossing Trajectory (min gap = 12.24 m ≥ 12 m)")
    ax2.legend(frameon=False, loc="center right", fontsize=7.5, ncol=2)

    fig.tight_layout()
    save_both(fig, "fig6_yield_6C0754")


if __name__ == "__main__":
    setup_style()
    plot_data_audit_durations()
    plot_replay_6c0754()
    plot_closed_loop_6c0754()
    plot_yield_6c0754()
    print("Generated figures in", FIG_DIR)
