"""REST routes (GET only). Kept free of Lambda specifics so tests and the demo generator can call it."""

from __future__ import annotations

from . import campus, service
from .config import Settings

ROUTES_HELP = {
    "GET /campus": "buildings, rooms, tariff",
    "GET /summary": "live campus KPIs",
    "GET /rooms": "latest state of every room",
    "GET /rooms/{room_id}/readings?hours=6": "raw readings for one room",
    "GET /hourly?scope=CAMPUS&hours=48": "hourly kWh (scope = CAMPUS, building id or room id)",
    "GET /alerts?hours=24&limit=50": "recent anomalies",
    "GET /forecast?scope=CAMPUS": "next-24h demand forecast",
    "GET /report?days=7": "daily kWh / waste / cost per building",
    "POST /readings": "ingest readings (header x-device-key)",
}


def _int(qs: dict, name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(qs.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def route(method: str, path: str, qs: dict | None, store, settings: Settings, now: int) -> tuple[int, dict]:
    qs = qs or {}
    parts = [p for p in path.strip("/").split("/") if p]
    if method != "GET":
        return 405, {"error": f"{method} not allowed here"}
    if not parts or parts == ["health"]:
        return 200, {"status": "ok", "service": "smart-campus-energy", "time": now, "routes": ROUTES_HELP}
    head = parts[0]
    if parts == ["campus"]:
        return 200, service.campus_info(settings)
    if parts == ["summary"]:
        return 200, service.summary(store, settings, now)
    if parts == ["rooms"]:
        return 200, service.rooms(store, settings, now)
    if head == "rooms" and len(parts) == 3 and parts[2] == "readings":
        return 200, service.room_readings(store, settings, parts[1], _int(qs, "hours", 6, 1, 48), now)
    if parts == ["hourly"]:
        scope = qs.get("scope") or campus.CAMPUS_SCOPE
        return 200, service.hourly(store, settings, scope, _int(qs, "hours", 48, 1, 24 * 21), now)
    if parts == ["alerts"]:
        return 200, service.alerts(store, settings, _int(qs, "hours", 24, 1, 24 * 30), _int(qs, "limit", 50, 1, 500), now)
    if parts == ["forecast"]:
        return 200, service.get_forecast(store, qs.get("scope") or campus.CAMPUS_SCOPE)
    if parts == ["report"]:
        return 200, service.report(store, settings, _int(qs, "days", 7, 1, 31), now)
    return 404, {"error": f"no route for {path}", "routes": ROUTES_HELP}
