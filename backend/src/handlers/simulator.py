"""Lambda: cloud-side sensor simulator.

Events
  (EventBridge schedule)                          -> one reading for every room, right now
  {"mode": "backfill", "days": 14, "live_hours": 4}
                                                  -> seed history after first deploy
  {"mode": "inject", "scenario": "waste", "room_id": "AB1-101"}
                                                  -> force an anomaly for a live demo
     scenarios: waste | spike | undervoltage | overvoltage
"""

import json
import logging
import time

from campus_energy import campus, service
from campus_energy.config import Settings
from campus_energy.notifier import SnsNotifier
from campus_energy.pipeline import backfill, process_readings
from campus_energy.simulator import SCENARIOS, simulate_campus, simulate_reading
from campus_energy.store import DynamoStore

log = logging.getLogger()
log.setLevel(logging.INFO)

SETTINGS = Settings.from_env()


def handler(event, context):
    event = event if isinstance(event, dict) else {}
    mode = event.get("mode", "live")
    store = DynamoStore(SETTINGS)
    notifier = SnsNotifier.from_env(SETTINGS)
    now = int(time.time())
    off, step = SETTINGS.utc_offset_s, SETTINGS.reading_interval_s

    if mode == "backfill":
        days = max(1, min(int(event.get("days", 14)), 60))
        live_hours = max(0, min(int(event.get("live_hours", 4)), 12))
        result = backfill(store, SETTINGS, days=days, now=now, live_hours=live_hours)
        result["forecasts"] = service.refresh_forecasts(store, SETTINGS, now)
    elif mode == "inject":
        scenario = event.get("scenario", "waste")
        if scenario not in SCENARIOS:
            return {"error": f"scenario must be one of {SCENARIOS}"}
        room = campus.get_room(event.get("room_id", "AB1-101"))
        count = SETTINGS.waste_min_readings if scenario == "waste" else 1
        readings = [simulate_reading(room, now - (count - 1 - i) * 10, off, interval_s=10, scenario=scenario)
                    for i in range(count)]
        for r in readings:
            r["source"] = "simulator-inject"
        result = process_readings(readings, store, SETTINGS, notifier, now=now)
    else:
        ts = now // step * step
        result = process_readings(simulate_campus(ts, off, step), store, SETTINGS, notifier, now=now)

    log.info("simulator %s: %s", mode, json.dumps(result, default=str)[:2000])
    return result
