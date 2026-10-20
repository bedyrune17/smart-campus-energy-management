"""Read-side queries behind the REST API (also used to build the offline demo snapshot)."""

from __future__ import annotations

from collections import defaultdict

from . import campus
from .config import Settings
from .forecast import forecast
from .timeutil import DAY, HOUR, day_key, day_start, hour_start, local_dt

FORECAST_HISTORY_DAYS = 21


def _sum(rows: list[dict], key: str) -> float:
    return sum(float(r.get(key) or 0) for r in rows)


def campus_info(settings: Settings) -> dict:
    info = campus.describe()
    info.update(
        currency=settings.currency,
        tariff_per_kwh=settings.tariff_per_kwh,
        co2_kg_per_kwh=settings.co2_kg_per_kwh,
        utc_offset_minutes=settings.utc_offset_minutes,
        reading_interval_s=settings.reading_interval_s,
    )
    return info


def rooms(store, settings: Settings, now: int) -> dict:
    states = {s["room_id"]: s for s in store.scan_room_states()}
    midnight = day_start(now, settings.utc_offset_s)
    offline_after = 3 * settings.reading_interval_s
    ids = list(campus.ROOMS) + sorted(set(states) - set(campus.ROOMS))
    out = []
    for rid in ids:
        st = states.get(rid, {})
        room = campus.get_room(rid, st.get("building_id"))
        last = st.get("last_reading") or {}
        last_ts = int(st.get("last_ts") or 0)
        online = bool(last_ts) and now - last_ts <= offline_after
        today = store.query_hourly(rid, midnight, now)
        baseline = (st.get("stats") or {}).get("occupied") or {}
        out.append({
            "room_id": rid,
            "name": room["name"],
            "building_id": room["building_id"],
            "type": room["type"],
            "capacity": room["capacity"],
            "rated_w": room["rated_w"],
            "status": st.get("status", "idle") if online else "offline",
            "power_w": float(last.get("power_w") or 0) if online else 0.0,
            "occupancy": last.get("occupancy"),
            "temperature_c": last.get("temperature_c"),
            "voltage_v": last.get("voltage_v"),
            "lights_on": last.get("lights_on"),
            "hvac_on": last.get("hvac_on"),
            "last_ts": last_ts or None,
            "today_kwh": round(_sum(today, "energy_kwh"), 2),
            "today_waste_kwh": round(_sum(today, "waste_kwh"), 2),
            "baseline_occupied_w": round(float(baseline.get("mean", 0))) if baseline else None,
        })
    return {"generated_at": now, "rooms": out}


def summary(store, settings: Settings, now: int) -> dict:
    off = settings.utc_offset_s
    room_list = rooms(store, settings, now)["rooms"]
    midnight = day_start(now, off)
    local_now = local_dt(now, off)

    buildings = []
    for bid, b in campus.BUILDINGS.items():
        today = store.query_hourly(bid, midnight, now)
        br = [r for r in room_list if r["building_id"] == bid]
        buildings.append({
            "id": bid,
            "name": b["name"],
            "power_kw": round(sum(r["power_w"] for r in br) / 1000, 2),
            "today_kwh": round(_sum(today, "energy_kwh"), 1),
            "today_waste_kwh": round(_sum(today, "waste_kwh"), 1),
            "rooms": len(br),
            "occupied": sum(1 for r in br if r["status"] == "occupied"),
            "wasting": sum(1 for r in br if r["status"] == "wasting"),
            "open": campus.is_open(bid, local_now),
        })

    today = store.query_hourly(campus.CAMPUS_SCOPE, midnight, now)
    yesterday = store.query_hourly(campus.CAMPUS_SCOPE, midnight - DAY, now - DAY)
    today_kwh = _sum(today, "energy_kwh")
    waste_kwh = _sum(today, "waste_kwh")
    alerts = store.query_alerts(now - DAY, 500, now)
    fc = store.get_forecast(campus.CAMPUS_SCOPE)
    tariff = settings.tariff_per_kwh

    return {
        "generated_at": now,
        "currency": settings.currency,
        "tariff_per_kwh": tariff,
        "co2_kg_per_kwh": settings.co2_kg_per_kwh,
        "campus": {
            "power_kw": round(sum(r["power_w"] for r in room_list) / 1000, 2),
            "today_kwh": round(today_kwh, 1),
            "today_cost": round(today_kwh * tariff, 0),
            "today_co2_kg": round(today_kwh * settings.co2_kg_per_kwh, 1),
            "today_waste_kwh": round(waste_kwh, 1),
            "today_waste_cost": round(waste_kwh * tariff, 0),
            "yesterday_same_time_kwh": round(_sum(yesterday, "energy_kwh"), 1),
            "rooms_total": len(room_list),
            "rooms_online": sum(1 for r in room_list if r["status"] != "offline"),
            "rooms_occupied": sum(1 for r in room_list if r["status"] == "occupied"),
            "rooms_wasting": sum(1 for r in room_list if r["status"] == "wasting"),
            "rooms_fault": sum(1 for r in room_list if r["status"] in ("fault", "power_issue")),
            "alerts_24h": len(alerts),
            "alerts_24h_high": sum(1 for a in alerts if a["severity"] in ("high", "critical")),
        },
        "buildings": buildings,
        "forecast": None if not fc else {
            "model": fc["model"],
            "generated_at": fc.get("generated_at"),
            "next_24h_kwh": fc["next_24h_kwh"],
            "next_24h_cost": round(fc["next_24h_kwh"] * tariff, 0),
            "peak_kw": fc["peak_kw"],
            "peak_hour": fc["peak_hour"],
            "wape_pct": (fc.get("backtest") or {}).get("wape_pct"),
        },
    }


def room_readings(store, settings: Settings, room_id: str, hours: int, now: int) -> dict:
    rows = store.query_readings(room_id, now - hours * HOUR, now)
    keys = ("ts", "power_w", "occupancy", "voltage_v", "temperature_c", "status")
    room = campus.get_room(room_id)
    return {
        "room_id": room_id,
        "name": room["name"],
        "rated_w": room["rated_w"],
        "idle_threshold_w": room["idle_threshold_w"],
        "readings": [{k: r.get(k) for k in keys} for r in rows],
    }


def hourly(store, settings: Settings, scope: str, hours: int, now: int) -> dict:
    start = hour_start(now, settings.utc_offset_s) - (hours - 1) * HOUR
    rows = store.query_hourly(scope, start, now)
    return {"scope": scope, "points": [
        {"hour": r["hour"], "energy_kwh": round(float(r["energy_kwh"]), 3), "waste_kwh": round(float(r.get("waste_kwh") or 0), 3)}
        for r in rows
    ]}


def alerts(store, settings: Settings, hours: int, limit: int, now: int) -> dict:
    return {"alerts": store.query_alerts(now - hours * HOUR, limit, now)}


def get_forecast(store, scope: str) -> dict:
    return store.get_forecast(scope) or {"scope": scope, "points": [], "model": None}


def report(store, settings: Settings, days: int, now: int) -> dict:
    """Daily kWh / waste / cost per building for the last `days` days (today included)."""
    off = settings.utc_offset_s
    start = day_start(now, off) - (days - 1) * DAY
    day_keys = [day_key(start + i * DAY + 3600, off) for i in range(days)]

    def by_day(scope: str):
        kwh, waste, peak = defaultdict(float), defaultdict(float), defaultdict(float)
        for r in store.query_hourly(scope, start, now):
            d = day_key(r["hour"], off)
            kwh[d] += float(r["energy_kwh"])
            waste[d] += float(r.get("waste_kwh") or 0)
            peak[d] = max(peak[d], float(r["energy_kwh"]))
        return kwh, waste, peak

    buildings = []
    for bid, b in campus.BUILDINGS.items():
        kwh, waste, _ = by_day(bid)
        buildings.append({"id": bid, "name": b["name"],
                          "kwh": [round(kwh[d], 1) for d in day_keys],
                          "waste_kwh": [round(waste[d], 1) for d in day_keys]})
    kwh, waste, peak = by_day(campus.CAMPUS_SCOPE)
    return {
        "days": day_keys,
        "buildings": buildings,
        "campus": {
            "kwh": [round(kwh[d], 1) for d in day_keys],
            "waste_kwh": [round(waste[d], 1) for d in day_keys],
            "cost": [round(kwh[d] * settings.tariff_per_kwh) for d in day_keys],
            "peak_kw": [round(peak[d], 1) for d in day_keys],
        },
    }


def refresh_forecasts(store, settings: Settings, now: int) -> dict:
    """Re-fit the forecast for every building and the whole campus (runs hourly)."""
    off = settings.utc_offset_s
    current = hour_start(now, off)
    out = {}
    for scope in campus.scopes():
        rows = store.query_hourly(scope, current - FORECAST_HISTORY_DAYS * DAY, current - 1)
        fc = forecast([(r["hour"], r["energy_kwh"]) for r in rows], current, off)
        fc["generated_at"] = now
        store.put_forecast(scope, fc)
        out[scope] = {"next_24h_kwh": fc["next_24h_kwh"], "wape_pct": fc["backtest"]["wape_pct"]}
    return out
