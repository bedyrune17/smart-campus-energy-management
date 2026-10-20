"""Persistence layer.

DynamoStore talks to the five DynamoDB tables created by template.yaml.
MemoryStore implements the same interface in memory; it powers the unit
tests and the offline demo-data generator for the dashboard.

Tables
  Readings  PK room_id  SK ts      raw meter readings          (TTL 7 days)
  Rooms     PK room_id             latest state + baselines
  Hourly    PK scope    SK hour    kWh per room/building/campus (TTL 90 days)
  Alerts    PK day      SK sk      detected anomalies           (TTL 30 days)
  Forecasts PK scope               latest 24 h forecast
"""

from __future__ import annotations

import copy
import json
import os
from collections import defaultdict
from decimal import Decimal

from .config import Settings
from .timeutil import DAY, day_key

TABLE_ENV = {
    "readings": "READINGS_TABLE",
    "rooms": "ROOMS_TABLE",
    "hourly": "HOURLY_TABLE",
    "alerts": "ALERTS_TABLE",
    "forecasts": "FORECAST_TABLE",
}


def _hourly_item(scope: str, hour: int) -> dict:
    return {"scope": scope, "hour": hour, "energy_kwh": 0.0, "waste_kwh": 0.0, "samples": 0}


class MemoryStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self._readings: dict[str, dict[int, dict]] = defaultdict(dict)
        self._rooms: dict[str, dict] = {}
        self._hourly: dict[str, dict[int, dict]] = defaultdict(dict)
        self._alerts: dict[str, dict] = {}
        self._forecasts: dict[str, dict] = {}

    # readings
    def put_readings(self, readings: list[dict]) -> None:
        for r in readings:
            self._readings[r["room_id"]][int(r["ts"])] = dict(r)

    def query_readings(self, room_id: str, start: int, end: int) -> list[dict]:
        rows = self._readings.get(room_id, {})
        return [dict(rows[t]) for t in sorted(rows) if start <= t <= end]

    # room state
    def get_room_states(self, room_ids: list[str]) -> dict[str, dict]:
        return {rid: copy.deepcopy(self._rooms[rid]) for rid in room_ids if rid in self._rooms}

    def put_room_states(self, states: list[dict]) -> None:
        for s in states:
            self._rooms[s["room_id"]] = copy.deepcopy(s)

    def scan_room_states(self) -> list[dict]:
        return [copy.deepcopy(s) for s in self._rooms.values()]

    # hourly aggregates
    def add_hourly(self, scope: str, hour: int, energy_kwh: float, waste_kwh: float, samples: int) -> None:
        item = self._hourly[scope].setdefault(hour, _hourly_item(scope, hour))
        item["energy_kwh"] += energy_kwh
        item["waste_kwh"] += waste_kwh
        item["samples"] += samples

    def put_hourly(self, items: list[dict]) -> None:
        for it in items:
            self._hourly[it["scope"]][int(it["hour"])] = dict(it)

    def query_hourly(self, scope: str, start: int, end: int) -> list[dict]:
        rows = self._hourly.get(scope, {})
        return [dict(rows[h]) for h in sorted(rows) if start <= h <= end]

    # alerts
    def put_alerts(self, alerts: list[dict]) -> None:
        for a in alerts:
            self._alerts[a["alert_id"]] = dict(a)

    def query_alerts(self, since_ts: int, limit: int = 100, now: int | None = None) -> list[dict]:
        rows = sorted((a for a in self._alerts.values() if a["ts"] >= since_ts), key=lambda a: -a["ts"])
        return [dict(a) for a in rows[:limit]]

    # forecasts
    def put_forecast(self, scope: str, item: dict) -> None:
        self._forecasts[scope] = dict(item, scope=scope)

    def get_forecast(self, scope: str) -> dict | None:
        f = self._forecasts.get(scope)
        return copy.deepcopy(f) if f else None


def _to_ddb(obj):
    """Python floats -> Decimal (DynamoDB rejects float)."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _from_ddb(obj):
    if isinstance(obj, list):
        return [_from_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _from_ddb(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


class DynamoStore:
    def __init__(self, settings: Settings | None = None, table_names: dict[str, str] | None = None, resource=None):
        import boto3
        from boto3.dynamodb.conditions import Key

        self._Key = Key
        self.settings = settings or Settings.from_env()
        self.ddb = resource or boto3.resource("dynamodb")
        names = table_names or {k: os.environ[env] for k, env in TABLE_ENV.items()}
        self.names = names
        self.readings = self.ddb.Table(names["readings"])
        self.rooms = self.ddb.Table(names["rooms"])
        self.hourly = self.ddb.Table(names["hourly"])
        self.alerts = self.ddb.Table(names["alerts"])
        self.forecasts = self.ddb.Table(names["forecasts"])

    def _query_all(self, table, **kwargs) -> list[dict]:
        items: list[dict] = []
        while True:
            resp = table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp or ("Limit" in kwargs and len(items) >= kwargs["Limit"]):
                return [_from_ddb(i) for i in items]
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # readings
    def put_readings(self, readings: list[dict]) -> None:
        ttl = self.settings.readings_ttl_days * DAY
        with self.readings.batch_writer(overwrite_by_pkeys=["room_id", "ts"]) as bw:
            for r in readings:
                bw.put_item(Item=_to_ddb(dict(r, expires_at=int(r["ts"]) + ttl)))

    def query_readings(self, room_id: str, start: int, end: int) -> list[dict]:
        K = self._Key
        return self._query_all(self.readings, KeyConditionExpression=K("room_id").eq(room_id) & K("ts").between(start, end))

    # room state
    def get_room_states(self, room_ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        ids = list(dict.fromkeys(room_ids))
        for i in range(0, len(ids), 100):
            request = {self.names["rooms"]: {"Keys": [{"room_id": rid} for rid in ids[i:i + 100]]}}
            while request:
                resp = self.ddb.batch_get_item(RequestItems=request)
                for item in resp["Responses"].get(self.names["rooms"], []):
                    out[item["room_id"]] = _from_ddb(item)
                request = resp.get("UnprocessedKeys") or None
        return out

    def put_room_states(self, states: list[dict]) -> None:
        with self.rooms.batch_writer(overwrite_by_pkeys=["room_id"]) as bw:
            for s in states:
                bw.put_item(Item=_to_ddb(s))

    def scan_room_states(self) -> list[dict]:
        items, kwargs = [], {}
        while True:
            resp = self.rooms.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return [_from_ddb(i) for i in items]
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # hourly aggregates
    def add_hourly(self, scope: str, hour: int, energy_kwh: float, waste_kwh: float, samples: int) -> None:
        self.hourly.update_item(
            Key={"scope": scope, "hour": int(hour)},
            UpdateExpression="ADD #e :e, #w :w, #n :n SET #x = :x",
            ExpressionAttributeNames={"#e": "energy_kwh", "#w": "waste_kwh", "#n": "samples", "#x": "expires_at"},
            ExpressionAttributeValues={
                ":e": Decimal(str(round(energy_kwh, 6))),
                ":w": Decimal(str(round(waste_kwh, 6))),
                ":n": int(samples),
                ":x": int(hour) + self.settings.hourly_ttl_days * DAY,
            },
        )

    def put_hourly(self, items: list[dict]) -> None:
        ttl = self.settings.hourly_ttl_days * DAY
        with self.hourly.batch_writer(overwrite_by_pkeys=["scope", "hour"]) as bw:
            for it in items:
                bw.put_item(Item=_to_ddb(dict(it, expires_at=int(it["hour"]) + ttl)))

    def query_hourly(self, scope: str, start: int, end: int) -> list[dict]:
        K = self._Key
        return self._query_all(self.hourly, KeyConditionExpression=K("scope").eq(scope) & K("hour").between(start, end))

    # alerts
    def put_alerts(self, alerts: list[dict]) -> None:
        ttl = self.settings.alerts_ttl_days * DAY
        with self.alerts.batch_writer(overwrite_by_pkeys=["day", "sk"]) as bw:
            for a in alerts:
                sk = f"{int(a['ts']):010d}#{a['room_id']}#{a['type']}"
                bw.put_item(Item=_to_ddb(dict(a, sk=sk, expires_at=int(a["ts"]) + ttl)))

    def query_alerts(self, since_ts: int, limit: int = 100, now: int | None = None) -> list[dict]:
        import time

        K = self._Key
        off = self.settings.utc_offset_s
        out: list[dict] = []
        t = int(now or time.time())
        days = []
        while t >= since_ts - DAY:  # newest day first
            days.append(day_key(t, off))
            t -= DAY
        for day in dict.fromkeys(days):
            if len(out) >= limit:
                break
            out.extend(self._query_all(
                self.alerts,
                KeyConditionExpression=K("day").eq(day) & K("sk").gte(f"{since_ts:010d}"),
                ScanIndexForward=False,
                Limit=limit - len(out),
            ))
        return out[:limit]

    # forecasts
    def put_forecast(self, scope: str, item: dict) -> None:
        self.forecasts.put_item(Item=_to_ddb(dict(item, scope=scope)))

    def get_forecast(self, scope: str) -> dict | None:
        item = self.forecasts.get_item(Key={"scope": scope}).get("Item")
        return _from_ddb(item) if item else None
