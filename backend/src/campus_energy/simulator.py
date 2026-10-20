"""Virtual smart-meter + occupancy sensor for every room on campus.

The simulator stands in for real hardware (ESP32 + PZEM-004T energy meter +
PIR/people counter). Readings are generated from:

* a timetable per room type (lectures, lab sessions, library evenings, hostel nights)
* outdoor temperature, which drives AC load
* behaviour: lights/AC are sometimes left on after a class ends (energy waste)
* rare faults: equipment overloads and grid voltage sags / surges

Randomness is seeded from (room, time block), so the same timestamp always
produces the same occupancy - which means the cloud simulator, the backfill
and the demo-data generator all agree with each other.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from . import campus
from .timeutil import local_dt

BLOCK_S = 1800  # occupancy changes in 30-minute blocks
SCENARIOS = ("waste", "spike", "undervoltage", "overvoltage")


def _rng(*parts) -> random.Random:
    return random.Random(":".join(str(p) for p in parts))


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def outdoor_temp_c(hour: float) -> float:
    """Typical South-Indian day: ~25 C before dawn, ~35 C mid-afternoon."""
    return 30.0 + 5.0 * math.sin(2 * math.pi * (hour - 9) / 24)


def occupancy_profile(room_type: str, weekday: int, hour: float) -> tuple[float, float]:
    """Return (probability the room is in use, typical fraction of capacity present)."""
    weekend = weekday >= 5
    sat = weekday == 5
    if room_type in ("classroom", "smart_classroom", "tutorial"):
        if not weekend:
            if 8 <= hour < 12.5 or 13.5 <= hour < 17:
                return 0.8, 0.75
            if 12.5 <= hour < 13.5:
                return 0.15, 0.3
        if sat and 8 <= hour < 12.5:
            return 0.4, 0.5
        return 0.0, 0.0
    if room_type in ("computer_lab", "electronics_lab", "workshop"):
        if not weekend:
            if 9 <= hour < 12.5 or 14 <= hour < 17:
                return 0.85, 0.8
            if 12.5 <= hour < 14:
                return 0.2, 0.3
        if sat and 9 <= hour < 13:
            return 0.3, 0.5
        return 0.0, 0.0
    if room_type == "reading_hall":
        if 8 <= hour < 22:
            if 17 <= hour < 21:
                return 0.98, 0.75
            return (0.85, 0.5) if weekend else (0.9, 0.35)
        return 0.0, 0.0
    if room_type == "digital_library":
        if 9 <= hour < 20:
            return (0.6, 0.4) if weekend else (0.8, 0.6)
        return 0.0, 0.0
    if room_type == "office":
        if not weekend and 9 <= hour < 17.5:
            return 0.95, 0.8
        if sat and 9 <= hour < 13:
            return 0.5, 0.4
        return 0.0, 0.0
    if room_type == "auditorium":
        if not weekend and (10 <= hour < 12 or 14 <= hour < 18):
            return 0.3, 0.6
        return 0.0, 0.0
    if room_type == "seminar_hall":
        if not weekend and 10 <= hour < 17:
            return 0.35, 0.6
        return 0.0, 0.0
    if room_type == "common_room":
        if 6 <= hour < 8:
            return 0.5, 0.2
        if 17 <= hour < 23.5 or (weekend and 10 <= hour < 17):
            return (0.8, 0.4) if weekend else (0.9, 0.45)
        return 0.0, 0.0
    if room_type == "study_room":
        if 19 <= hour or hour < 1:
            return 0.9, 0.5
        if weekend and 10 <= hour < 19:
            return 0.7, 0.4
        return 0.0, 0.0
    return 0.0, 0.0


def block_start(ts: float, offset_s: int) -> int:
    return ((int(ts) + offset_s) // BLOCK_S) * BLOCK_S - offset_s


@lru_cache(maxsize=250_000)
def _block_occupancy(room_id: str, block_ts: int, offset_s: int) -> float:
    """Fraction of capacity present during a 30-minute block (0.0 = empty)."""
    room = campus.get_room(room_id)
    dt = local_dt(block_ts + BLOCK_S / 2, offset_s)
    p, frac = occupancy_profile(room["type"], dt.weekday(), dt.hour + dt.minute / 60)
    r = _rng(room_id, "occ", block_ts)
    if r.random() >= p:
        return 0.0
    return _clamp(frac * r.uniform(0.6, 1.25), 0.05, 1.0)


@lru_cache(maxsize=250_000)
def _block_waste(room_id: str, block_ts: int, offset_s: int) -> bool:
    """Were lights/AC left running in an empty room during this block?"""
    if _block_occupancy(room_id, block_ts, offset_s) > 0:
        return False
    prev = "off"
    for k in range(4, -1, -1):  # walk forward through the previous two hours
        b = block_ts - k * BLOCK_S
        if _block_occupancy(room_id, b, offset_s) > 0:
            prev = "occupied"
            continue
        threshold = {"occupied": 0.30, "waste": 0.65, "off": 0.01}[prev]
        prev = "waste" if _rng(room_id, "waste", b).random() < threshold else "off"
    return prev == "waste"


@lru_cache(maxsize=50_000)
def _voltage_event(building_id: str, block_ts: int) -> str | None:
    r = _rng(building_id, "volt", block_ts).random()
    if r < 0.004:
        return "undervoltage"
    if r < 0.006:
        return "overvoltage"
    return None


def simulate_reading(room: dict, ts: float, offset_s: int = 19800, interval_s: int = 300,
                     scenario: str | None = None) -> dict:
    """One meter reading for `room` at epoch `ts`. `scenario` forces an anomaly for demos."""
    ts = int(ts)
    room_id = room["room_id"]
    dt = local_dt(ts, offset_s)
    hour = dt.hour + dt.minute / 60
    block = block_start(ts, offset_s)
    r = _rng(room_id, "reading", ts)

    frac = _block_occupancy(room_id, block, offset_s)
    wasting = _block_waste(room_id, block, offset_s)
    if scenario == "waste":
        frac, wasting = 0.0, True

    occupancy = max(1, round(room["capacity"] * frac * r.uniform(0.9, 1.1))) if frac > 0 else 0
    outdoor = outdoor_temp_c(hour)
    hvac_duty = _clamp((outdoor - 21) / 12, 0.35, 1.0)

    if occupancy > 0:
        lights = room["lights_w"] * r.uniform(0.85, 1.0)
        hvac = room["hvac_w"] * hvac_duty * r.uniform(0.85, 1.05)
        equipment = room["equipment_w"] * (0.35 + 0.65 * frac) * r.uniform(0.85, 1.1)
    elif wasting:
        lights = room["lights_w"] * r.uniform(0.9, 1.0)
        hvac = room["hvac_w"] * hvac_duty * r.uniform(0.8, 1.0)
        equipment = room["equipment_w"] * r.uniform(0.25, 0.5)
    else:
        lights = hvac = equipment = 0.0

    power = room["standby_w"] * r.uniform(0.85, 1.15) + lights + hvac + equipment
    if scenario == "spike" or (scenario is None and r.random() < 0.0015):
        power = room["rated_w"] * r.uniform(1.2, 1.6)  # failing compressor / short circuit

    event = scenario if scenario in ("undervoltage", "overvoltage") else _voltage_event(room["building_id"], block)
    if event == "undervoltage":
        voltage = r.uniform(194, 203)
    elif event == "overvoltage":
        voltage = r.uniform(255, 262)
    else:
        voltage = r.gauss(231, 2.2)

    pf = _clamp(r.gauss(0.84 if room["type"] == "workshop" else 0.93, 0.02), 0.7, 0.99)
    hvac_on = hvac > 0
    if hvac_on and room["hvac_w"] >= 2000:
        indoor = r.gauss(24.5, 0.6)  # air-conditioned
    else:
        indoor = outdoor - 1.5 + r.gauss(0, 0.5)

    return {
        "room_id": room_id,
        "building_id": room["building_id"],
        "ts": ts,
        "power_w": round(power, 1),
        "voltage_v": round(voltage, 1),
        "current_a": round(power / (voltage * pf), 2),
        "power_factor": round(pf, 2),
        "energy_kwh": round(power * interval_s / 3.6e6, 5),
        "occupancy": occupancy,
        "temperature_c": round(indoor, 1),
        "lights_on": lights > 0,
        "hvac_on": hvac_on,
        "source": "simulator",
    }


def simulate_campus(ts: float, offset_s: int = 19800, interval_s: int = 300,
                    rooms: list[str] | None = None) -> list[dict]:
    ids = rooms or list(campus.ROOMS)
    return [simulate_reading(campus.get_room(rid), ts, offset_s, interval_s) for rid in ids]
