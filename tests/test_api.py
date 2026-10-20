import base64
import json

import pytest
from conftest import TUESDAY_11AM

from campus_energy import pipeline, service
from campus_energy.router import route


@pytest.fixture
def seeded(store, settings):
    pipeline.backfill(store, settings, days=7, now=TUESDAY_11AM, live_hours=2)
    service.refresh_forecasts(store, settings, TUESDAY_11AM)
    return store


def get(store, settings, path, **qs):
    return route("GET", path, {k: str(v) for k, v in qs.items()}, store, settings, TUESDAY_11AM)


def test_health_and_errors(seeded, settings):
    assert get(seeded, settings, "/")[0] == 200
    assert get(seeded, settings, "/nope")[0] == 404
    assert route("DELETE", "/rooms", {}, seeded, settings, TUESDAY_11AM)[0] == 405


def test_summary(seeded, settings):
    status, body = get(seeded, settings, "/summary")
    assert status == 200
    c = body["campus"]
    assert c["rooms_total"] == 17 and c["rooms_online"] == 17
    assert c["power_kw"] > 10 and c["today_kwh"] > 0
    assert c["today_cost"] == pytest.approx(c["today_kwh"] * 8, abs=8)
    assert len(body["buildings"]) == 5
    assert body["forecast"]["next_24h_kwh"] > 0


def test_rooms_readings_hourly_alerts_forecast_report(seeded, settings):
    _, rooms = get(seeded, settings, "/rooms")
    assert len(rooms["rooms"]) == 17
    assert {r["status"] for r in rooms["rooms"]} <= {"occupied", "idle", "wasting", "fault", "power_issue"}

    _, rr = get(seeded, settings, "/rooms/LAB-CS1/readings", hours=2)
    assert len(rr["readings"]) >= 20

    _, hourly = get(seeded, settings, "/hourly", scope="CAMPUS", hours=48)
    assert len(hourly["points"]) == 48

    _, fc = get(seeded, settings, "/forecast", scope="LIB")
    assert len(fc["points"]) == 24 and fc["model"].startswith("Holt-Winters")

    _, rep = get(seeded, settings, "/report", days=7)
    assert len(rep["days"]) == 7 and all(v > 0 for v in rep["campus"]["kwh"][:-1])

    _, alerts = get(seeded, settings, "/alerts", hours=24, limit=5)
    assert len(alerts["alerts"]) <= 5


def test_api_handler_wraps_router(monkeypatch, seeded):
    from handlers import api

    monkeypatch.setattr(api, "_store", seeded)
    monkeypatch.setattr(api.time, "time", lambda: TUESDAY_11AM)
    resp = api.handler({"rawPath": "/summary", "requestContext": {"http": {"method": "GET"}}}, None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["campus"]["rooms_total"] == 17


def test_ingest_handler_http_and_iot(monkeypatch, store, notifier):
    from handlers import ingest

    monkeypatch.setattr(ingest, "_store", store)
    monkeypatch.setattr(ingest, "_notifier", notifier)
    monkeypatch.setenv("DEVICE_API_KEY", "secret-key")
    body = json.dumps({"readings": [{"room_id": "AB1-101", "power_w": 1500, "occupancy": 12}]})
    event = {"requestContext": {"http": {"method": "POST"}}, "headers": {"x-device-key": "wrong"}, "body": body}

    assert ingest.handler(event, None)["statusCode"] == 401
    event["headers"] = {"X-Device-Key": "secret-key"}
    ok = ingest.handler(event, None)
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["accepted"] == 1

    b64 = dict(event, body=base64.b64encode(b'{"room_id": "AB1-102", "power_w": 900}').decode(), isBase64Encoded=True)
    assert ingest.handler(b64, None)["statusCode"] == 200
    assert ingest.handler(dict(event, body="{not json"), None)["statusCode"] == 400

    # AWS IoT Core rule: raw MQTT payload, room id taken from the topic (device_id)
    res = ingest.handler({"power_w": 4100, "occupancy": 0, "device_id": "IOT-LAB-1"}, None)
    assert res["accepted"] == 1
    assert store.query_readings("IOT-LAB-1", 0, 2**40)[0]["source"] == "iot-core"
