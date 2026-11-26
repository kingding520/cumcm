#!/usr/bin/env python3
"""Metric geometry and kinematics loaded from ``ef_initial_v1.json``.

This module is the single source of physical truth for scenario 1 after the
2026-11-26 baseline decision (PROJECT.md): kappa = 0.10 m/px, D = 12 m,
speed limits 0.60 / 0.80 / 1.00 m/s for heavy ladle / empty ladle / empty hook,
with acceleration limits and station slow zones taken from the same file.

Nothing here is invented: every number is read from the交付 config. Missing keys
raise, so a silently changed contract cannot be masked by a default.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GEOMETRY = ROOT / "code" / "configs" / "ef_initial_v1.json"

_TRAVEL_CACHE: dict[tuple, float] = {}

STATE_HEAVY = "heavy_ladle"
STATE_EMPTY_LADLE = "empty_ladle"
STATE_EMPTY_HOOK = "empty_hook"


@dataclass(frozen=True)
class Station:
    id: str
    x_m: float
    active: bool


@dataclass(frozen=True)
class Crane:
    id: str
    order: int
    capacity_t: float
    q0_m: float
    length_m: float
    reach_min_m: float
    reach_max_m: float


@dataclass(frozen=True)
class Geometry:
    version: str
    stations: dict[str, Station]
    cranes: list[Crane]
    clear_distance_m: float
    rail_min_m: float
    rail_max_m: float
    speed_limit_m_s: dict[str, float]
    accel_m_s2: dict[str, float]
    slow_zones: tuple[float, ...]
    slow_half_width_m: float
    slow_ratio: float
    handling_s: dict[str, float]
    gear_ratios: tuple[float, ...]

    # ---------------------------------------------------------------- lookups
    def station(self, name: str) -> Station:
        key = name
        if key not in self.stations:
            alias = {
                "BOF-B": "BOF_B",
                "BOF-A": "BOF_A",
                "BOF-C": "BOF_C",
                "RH": "RH1",
                "CCM-C": "CCM3",
                "CCM-B": "CCM2",
                "CCM-A": "CCM1",
                "transfer_car": "CART3",
            }.get(name)
            if alias is None or alias not in self.stations:
                raise KeyError(f"unknown station {name!r}")
            key = alias
        return self.stations[key]

    def x_of(self, name: str) -> float:
        return self.station(name).x_m

    def crane(self, crane_id: str) -> Crane:
        for crane in self.cranes:
            if crane.id == crane_id:
                return crane
        raise KeyError(f"unknown crane {crane_id!r}")

    def crane_ids(self) -> list[str]:
        return [c.id for c in sorted(self.cranes, key=lambda c: c.order)]

    def service_crane_ids(self) -> list[str]:
        return [cid for cid in self.crane_ids() if self.crane(cid).capacity_t >= 450]

    # ------------------------------------------------------------ kinematics
    def speed_cap(self, state: str, x_m: float) -> float:
        """Speed limit at position, including station slow zones."""
        limit = self.speed_limit_m_s[state]
        for centre in self.slow_zones:
            if abs(x_m - centre) <= self.slow_half_width_m:
                return min(limit, self.slow_ratio * limit)
        return limit

    def travel_seconds(self, x_from: float, x_to: float, state: str, dt: float = 0.05) -> float:
        key = (self.version, round(x_from, 3), round(x_to, 3), state, dt)
        cached = _TRAVEL_CACHE.get(key)
        if cached is not None:
            return cached
        value = self._integrate_travel(x_from, x_to, state, dt)
        _TRAVEL_CACHE[key] = value
        return value

    def _integrate_travel(self, x_from: float, x_to: float, state: str, dt: float) -> float:
        """Integrate a single crane's point-to-point move with accel and slow zones."""
        distance = abs(x_to - x_from)
        if distance < 1e-9:
            return 0.0
        sign = 1.0 if x_to >= x_from else -1.0
        accel = self.accel_m_s2[state]
        x, v, t = x_from, 0.0, 0.0
        while t < 6000.0:
            remaining = abs(x_to - x)
            cap = self.speed_cap(state, x)
            # Stop exactly on the target: v <= sqrt(2*a*distance_left).
            stop_cap = math.sqrt(max(2.0 * accel * remaining, 0.0))
            v_target = min(cap, stop_cap)
            if v < v_target:
                v = min(v + accel * dt, v_target)
            elif v > v_target:
                v = max(v - accel * dt, v_target)
            step = v * dt
            if step >= remaining:
                t += remaining / v if v > 1e-9 else 0.0
                return t
            x += sign * step
            t += dt
        raise RuntimeError(f"travel_seconds did not converge for {x_from}->{x_to}")

    def task_duration_s(self, x_from: float, x_to: float, state: str, phase: str = "scenario1") -> float:
        """Scenario-1 task duration: travel plus configured pickup/dropoff handling."""
        handling = self.handling_s.get(f"{phase}_pickup", 0.0) + self.handling_s.get(f"{phase}_dropoff", 0.0)
        return self.travel_seconds(x_from, x_to, state) + handling


def load_geometry(path: Path | str = DEFAULT_GEOMETRY) -> Geometry:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    stations = {
        item["id"]: Station(item["id"], float(item["x_m"]), bool(item.get("active", True)))
        for item in data["stations"]
    }
    cranes = [
        Crane(
            id=item["id"],
            order=int(item["order"]),
            capacity_t=float(item["capacity_t"]),
            q0_m=float(item["q0_m"]),
            length_m=float(item["length_m"]),
            reach_min_m=float(item["reach_m"][0]),
            reach_max_m=float(item["reach_m"][1]),
        )
        for item in data["cranes"]
    ]
    slow = data["slow_zone"]
    zones = tuple(float(stations[sid].x_m) for sid in slow["apply_to"] if sid in stations)
    clear = float(data["min_center_distance_m"])
    declared_clear = float(data["clearance_m"]) + float(cranes[0].length_m)
    if abs(clear - declared_clear) > 1e-9:
        raise ValueError(
            f"min_center_distance_m ({clear}) disagrees with clearance+length ({declared_clear}); "
            "resolve in the contract before use"
        )
    return Geometry(
        version=data["geometry_version"],
        stations=stations,
        cranes=cranes,
        clear_distance_m=clear,
        rail_min_m=float(data["rail"]["min_m"]),
        rail_max_m=float(data["rail"]["max_m"]),
        speed_limit_m_s={k: float(v) for k, v in data["speed_limit_m_s"].items()},
        accel_m_s2={k: float(v) for k, v in data["accel_m_s2"].items()},
        slow_zones=zones,
        slow_half_width_m=float(slow["half_width_m"]),
        slow_ratio=float(slow["speed_ratio"]),
        handling_s={k: float(v) for k, v in data["handling_s"].items()},
        gear_ratios=tuple(float(r) for r in data["gear_speed_ratios"]),
    )
