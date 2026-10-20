#!/usr/bin/env python3
"""Build dashboard/demo-data.json by running the real backend against an in-memory store.

The GitHub Pages dashboard falls back to this snapshot when no AWS API is
configured, so the project can be shown before (or without) deploying.

    python tools/generate_demo_data.py                       # most recent weekday, 14:30 IST
    python tools/generate_demo_data.py --at 2026-10-20T11:00 # any campus-local time
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from campus_energy import campus, pipeline, service  # noqa: E402
from campus_energy.config import Settings  # noqa: E402
from campus_energy.router import route  # noqa: E402
from campus_energy.store import MemoryStore  # noqa: E402


def default_time(tz: timezone) -> datetime:
    t = datetime.now(tz).replace(hour=14, minute=30, second=0, microsecond=0)
    if t > datetime.now(tz):
        t -= timedelta(days=1)
    while t.weekday() >= 5:
        t -= timedelta(days=1)
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="campus-local time YYYY-MM-DDTHH:MM (default: last weekday 14:30)")
    ap.add_argument("--out", default=str(ROOT / "dashboard" / "demo-data.json"))
    args = ap.parse_args()

    settings = Settings()
    tz = timezone(timedelta(minutes=settings.utc_offset_minutes))
    at = datetime.fromisoformat(args.at).replace(tzinfo=tz) if args.at else default_time(tz)
    now = int(at.timestamp()) // settings.reading_interval_s * settings.reading_interval_s

    store = MemoryStore(settings)
    pipeline.backfill(store, settings, days=21, now=now, live_hours=24)
    service.refresh_forecasts(store, settings, now)

    paths = ["/campus", "/summary", "/rooms", "/alerts?hours=24&limit=100", "/report?days=7"]
    for scope in campus.scopes():
        paths += [f"/hourly?scope={scope}&hours=168", f"/forecast?scope={scope}"]
    paths += [f"/rooms/{rid}/readings?hours=6" for rid in campus.ROOMS]

    endpoints = {}
    for p in paths:
        path, _, query = p.partition("?")
        qs = dict(kv.split("=", 1) for kv in query.split("&")) if query else {}
        status, body = route("GET", path, qs, store, settings, now)
        if status != 200:
            raise SystemExit(f"{p} returned {status}: {body}")
        endpoints[p] = body

    out = Path(args.out)
    out.write_text(json.dumps({"generated_at": now, "endpoints": endpoints}, separators=(",", ":")), encoding="utf-8")
    s = endpoints["/summary"]["campus"]
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB) for {at:%a %d %b %Y %H:%M} IST: "
          f"{s['power_kw']} kW live, {s['today_kwh']} kWh today, {s['alerts_24h']} alerts")


if __name__ == "__main__":
    main()
