"""Ingestion pipeline: validate -> enrich -> detect anomalies -> store -> notify.

Used by every entry point: HTTP API (simulator / ESP32), AWS IoT Core rule,
and the scheduled cloud simulator.
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict

from . import anomaly, campus
from .config import Settings
from .simulator import simulate_campus, simulate_reading
from .timeutil import DAY, HOUR, hour_start

MAX_BATCH = 200
_ROOM_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_LIMITS = {
    "power_w": (0, 500_000),
    "voltage_v": (0, 500),
    "current_a": (0, 2_000),
    "power_factor": (0, 1),
    "occupancy": (0, 5_000),
    "temperature_c": (-20, 70),
    "energy_kwh": (0, 1_000),
}


class ValidationError(ValueError):
    pass


def normalize(raw: dict, now: int) -> dict:
    """Validate one incoming reading and coerce it to the canonical shape."""
    if not isinstance(raw, dict):
        raise ValidationError("reading must be a JSON object")
    room_id = str(raw.get("room_id") or raw.get("device_id") or "").strip()
    if not _ROOM_ID.match(room_id):
        raise ValidationError("room_id is required (letters, digits, - _ . : only, max 64 chars)")
    if raw.get("power_w") is None:
        raise ValidationError("power_w is required")

    out: dict = {"room_id": room_id}
    for key, (lo, hi) in _LIMITS.items():
        if raw.get(key) is None:
            continue
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            raise ValidationError(f"{key} must be a number") from None
        if math.isnan(value) or not lo <= value <= hi:
            raise ValidationError(f"{key}={raw[key]} is outside the allowed range {lo}..{hi}")
        out[key] = int(round(value)) if key == "occupancy" else value

    ts = raw.get("ts")
    try:
        ts = int(float(ts)) if ts is not None else now
    except (TypeError, ValueError):
        raise ValidationError("ts must be epoch seconds") from None
    if ts > 10**12:  # milliseconds
        ts //= 1000
    if abs(ts - now) > 2 * DAY:  # device clock not synced (e.g. ESP32 before NTP) -> use server time
        ts = now
    out["ts"] = ts
    for flag in ("lights_on", "hvac_on"):
        if flag in raw:
            out[flag] = bool(raw[flag])
    out["source"] = str(raw.get("source") or "device")[:32]
    return out


def process_readings(raw_readings: list, store, settings: Settings, notifier=None, now: int | None = None) -> dict:
    now = int(now or time.time())
    accepted: list[tuple[dict, str | None]] = []
    rejected: list[dict] = []
    for i, raw in enumerate(raw_readings[:MAX_BATCH]):
        try:
            accepted.append((normalize(raw, now), raw.get("building_id")))
        except ValidationError as e:
            rejected.append({"index": i, "error": str(e)})
    if len(raw_readings) > MAX_BATCH:
        rejected.append({"index": MAX_BATCH, "error": f"batch limited to {MAX_BATCH} readings; rest dropped"})
    if not accepted:
        return {"accepted": 0, "rejected": rejected, "alerts": [], "notified": 0}

    rooms = {r["room_id"]: campus.get_room(r["room_id"], hint) for r, hint in accepted}
    states = store.get_room_states(list(rooms))
    hourly: dict[tuple[str, int], list] = defaultdict(lambda: [0.0, 0.0, 0])
    stored: list[dict] = []
    alerts: list[dict] = []
    touched: set[str] = set()

    for reading, _ in sorted(accepted, key=lambda x: x[0]["ts"]):
        room = rooms[reading["room_id"]]
        state = states.get(room["room_id"]) or anomaly.new_state(room)
        last_ts = int(state.get("last_ts") or 0)
        if reading["ts"] <= last_ts:
            rejected.append({"room_id": room["room_id"], "ts": reading["ts"], "error": "duplicate or older than last reading"})
            continue
        reading["building_id"] = room["building_id"]
        if reading.get("energy_kwh") is None:  # integrate power over the gap, but not across outages
            gap = reading["ts"] - last_ts if last_ts else settings.reading_interval_s
            gap = min(gap, 3 * settings.reading_interval_s)
            reading["energy_kwh"] = round(reading["power_w"] * gap / 3.6e6, 6)

        ev = anomaly.evaluate(reading, room, state, settings)
        reading["status"] = ev.status
        reading["wasting"] = ev.wasting
        states[room["room_id"]] = ev.state
        touched.add(room["room_id"])
        alerts.extend(ev.alerts)
        stored.append(reading)

        waste = 0.0
        if ev.wasting and reading["power_w"] > 0:  # only the load above standby is waste
            waste = reading["energy_kwh"] * max(0.0, 1 - room["standby_w"] / reading["power_w"])
        h = hour_start(reading["ts"], settings.utc_offset_s)
        for scope in (room["room_id"], room["building_id"], campus.CAMPUS_SCOPE):
            acc = hourly[(scope, h)]
            acc[0] += reading["energy_kwh"]
            acc[1] += waste
            acc[2] += 1

    if stored:
        store.put_readings(stored)
        store.put_room_states([states[rid] for rid in touched])
        for (scope, h), (energy, waste, n) in hourly.items():
            store.add_hourly(scope, h, energy, waste, n)
    if alerts:
        store.put_alerts(alerts)
    notified = notifier.notify(alerts) if notifier and alerts else 0
    return {"accepted": len(stored), "rejected": rejected, "alerts": [a["alert_id"] for a in alerts], "notified": notified}


def backfill(store, settings: Settings, days: int = 14, now: int | None = None, live_hours: int = 2) -> dict:
    """Seed history right after deployment so charts and the forecast have data immediately.

    Older history is written as hourly aggregates only (cheap); the last
    `live_hours` run through the full pipeline so rooms, readings and alerts exist too.
    """
    now = int(now or time.time())
    step = settings.reading_interval_s
    off = settings.utc_offset_s
    cut = hour_start(now, off) - live_hours * HOUR
    start = hour_start(now - days * DAY, off)

    acc: dict[tuple[str, int], list] = defaultdict(lambda: [0.0, 0.0, 0])
    for ts in range(start, cut, step):
        h = hour_start(ts, off)
        for room in campus.ROOMS.values():
            r = simulate_reading(room, ts, off, step)
            waste = 0.0
            if anomaly.is_wasting(r, room):
                waste = r["energy_kwh"] * max(0.0, 1 - room["standby_w"] / r["power_w"])
            for scope in (room["room_id"], room["building_id"], campus.CAMPUS_SCOPE):
                a = acc[(scope, h)]
                a[0] += r["energy_kwh"]
                a[1] += waste
                a[2] += 1
    items = [{"scope": s, "hour": h, "energy_kwh": round(e, 4), "waste_kwh": round(w, 4), "samples": n}
             for (s, h), (e, w, n) in acc.items()]
    store.put_hourly(items)

    alerts = 0
    t = (cut // step) * step
    steps = 0
    while t <= now:
        res = process_readings(simulate_campus(t, off, step), store, settings, notifier=None, now=now)
        alerts += len(res["alerts"])
        t += step
        steps += 1
    return {"hourly_items": len(items), "live_steps": steps, "alerts": alerts}
