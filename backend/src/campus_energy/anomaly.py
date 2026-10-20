"""Abnormal-usage detection.

Two layers run on every reading:

1. Rules that encode domain knowledge
   * ENERGY_WASTE   - room is empty but lights/AC/equipment are drawing power
   * AFTER_HOURS    - same, while the building is officially closed
   * OVERLOAD       - power above the room's rated connected load (fault / short)
   * UNDER_VOLTAGE / OVER_VOLTAGE - supply outside 230 V +/-10 %

2. An adaptive statistical baseline per room and occupancy mode
   (exponentially-weighted mean/variance). A reading more than
   `zscore_threshold` standard deviations above normal raises
   UNUSUAL_CONSUMPTION even if no fixed rule fired.

Per-room state (baseline, streaks, alert cooldowns) is stored in the Rooms table,
so detection keeps working across Lambda invocations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import campus
from .config import Settings
from .timeutil import day_key, local_dt

SEVERITIES = ["low", "medium", "high", "critical"]
WARMUP_READINGS = 12  # readings needed before the statistical baseline is trusted


def severity_rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


def is_wasting(reading: dict, room: dict) -> bool:
    """Loads running while nobody is in the room. Unknown occupancy (no sensor) is never waste."""
    occ = reading.get("occupancy")
    return occ is not None and int(occ) == 0 and reading["power_w"] > room["idle_threshold_w"]


def new_state(room: dict) -> dict:
    return {
        "room_id": room["room_id"],
        "building_id": room["building_id"],
        "stats": {},
        "waste_streak": 0,
        "last_alert": {},
        "last_ts": 0,
    }


@dataclass
class Evaluation:
    status: str
    wasting: bool
    alerts: list[dict]
    state: dict


def _money(settings: Settings, amount: float) -> str:
    symbol = "₹" if settings.currency == "INR" else settings.currency + " "
    return f"{symbol}{amount:,.0f}"


def evaluate(reading: dict, room: dict, state: dict, settings: Settings) -> Evaluation:
    """Check one reading against the rules and baseline. Mutates and returns `state`."""
    p = float(reading["power_w"])
    v = reading.get("voltage_v")
    occ = int(reading.get("occupancy") or 0)
    ts = int(reading["ts"])
    dt = local_dt(ts, settings.utc_offset_s)
    name = room["name"]
    kw = p / 1000
    found: list[tuple[str, str, str, str]] = []  # (type, severity, title, message)

    # 1. Power quality
    if v:
        v = float(v)
        if v < settings.under_voltage:
            found.append(("UNDER_VOLTAGE", "high" if v < 200 else "medium", f"Under-voltage at {name}",
                          f"Supply dropped to {v:.0f} V (nominal 230 V). Motors and lab equipment can be damaged; "
                          "check the building feeder / stabiliser."))
        elif v > settings.over_voltage:
            found.append(("OVER_VOLTAGE", "high", f"Over-voltage at {name}",
                          f"Supply rose to {v:.0f} V (nominal 230 V). Risk to electronics; check the transformer tap / surge protection."))

    # 2. Overload against rated connected load
    overload = p > room["rated_w"] * settings.overload_factor
    if overload:
        pct = p / room["rated_w"] * 100
        found.append(("OVERLOAD", "critical" if pct > 140 else "high", f"Overload in {name}",
                      f"Drawing {kw:.1f} kW = {pct:.0f}% of the rated {room['rated_w'] / 1000:.1f} kW. "
                      "Possible equipment fault or short circuit - inspect immediately."))

    # 3. Waste: loads running in an empty room
    wasting = is_wasting(reading, room)
    state["waste_streak"] = int(state.get("waste_streak", 0)) + 1 if wasting else 0
    if wasting and state["waste_streak"] >= settings.waste_min_readings:
        excess_kw = (p - room["standby_w"]) / 1000
        cost_h = _money(settings, excess_kw * settings.tariff_per_kwh)
        minutes = state["waste_streak"] * settings.reading_interval_s // 60
        if not campus.is_open(room["building_id"], dt):
            sev = "high" if excess_kw > 1 else "medium"
            found.append(("AFTER_HOURS", sev, f"After-hours usage in {name}",
                          f"{kw:.1f} kW drawn at {dt:%H:%M} while the building is closed and the room is empty "
                          f"(~{excess_kw:.1f} kWh, {cost_h} wasted every hour)."))
        else:
            sev = "high" if excess_kw > 3 else "medium" if excess_kw > 1 else "low"
            found.append(("ENERGY_WASTE", sev, f"Energy waste in {name}",
                          f"Room has been empty for {minutes}+ min but is drawing {kw:.1f} kW - lights/AC left on. "
                          f"Switching off saves ~{excess_kw:.1f} kWh ({cost_h}) per hour."))

    # 4. Adaptive statistical baseline (separate for occupied / idle behaviour)
    bucket = "occupied" if occ > 0 else "idle"
    st = state.setdefault("stats", {}).setdefault(bucket, {"mean": 0.0, "var": 0.0, "n": 0})
    n, mean, var = int(st["n"]), float(st["mean"]), float(st["var"])
    if n >= WARMUP_READINGS and not overload and not wasting:
        sigma = max(math.sqrt(var), 0.05 * mean, 50.0)
        z = (p - mean) / sigma
        if z > settings.zscore_threshold and p - mean > 500:
            found.append(("UNUSUAL_CONSUMPTION", "medium", f"Unusual consumption in {name}",
                          f"{kw:.1f} kW is {z:.1f} sigma above this room's normal {mean / 1000:.1f} kW when {bucket}."))
    if not overload and not wasting:  # keep anomalies out of the baseline
        if n == 0:
            mean, var = p, 0.0
        else:
            alpha = max(0.1, 1.0 / (n + 1))
            diff = p - mean
            mean += alpha * diff
            var = (1 - alpha) * (var + diff * alpha * diff)
        st.update(mean=round(mean, 2), var=round(var, 2), n=n + 1)

    # Cooldown: one alert per room per type per window
    last = state.setdefault("last_alert", {})
    alerts = []
    for typ, sev, title, msg in found:
        if ts - int(last.get(typ, 0)) < settings.alert_cooldown_s:
            continue
        last[typ] = ts
        alerts.append({
            "alert_id": f"{room['room_id']}#{typ}#{ts}",
            "ts": ts,
            "day": day_key(ts, settings.utc_offset_s),
            "room_id": room["room_id"],
            "room_name": name,
            "building_id": room["building_id"],
            "building_name": campus.building_name(room["building_id"]),
            "type": typ,
            "severity": sev,
            "title": title,
            "message": msg,
            "power_w": round(p, 1),
            "voltage_v": v,
            "occupancy": occ,
        })

    types = {f[0] for f in found}
    if "OVERLOAD" in types:
        status = "fault"
    elif types & {"UNDER_VOLTAGE", "OVER_VOLTAGE"}:
        status = "power_issue"
    elif wasting:
        status = "wasting"
    elif occ > 0:
        status = "occupied"
    else:
        status = "idle"

    state.update(
        room_id=room["room_id"],
        building_id=room["building_id"],
        last_ts=ts,
        status=status,
        last_reading={k: reading.get(k) for k in (
            "ts", "power_w", "voltage_v", "current_a", "power_factor", "occupancy",
            "temperature_c", "lights_on", "hvac_on", "source")},
    )
    return Evaluation(status=status, wasting=wasting, alerts=alerts, state=state)
