from conftest import TUESDAY_11AM

from campus_energy import pipeline
from campus_energy.simulator import simulate_campus
from campus_energy.timeutil import hour_start


def test_validation_rejects_bad_readings(store, settings):
    res = pipeline.process_readings([
        {"room_id": "AB1-101", "power_w": 1200, "occupancy": 20},
        {"room_id": "AB1-101"},                                   # no power
        {"room_id": "bad id!", "power_w": 5},                     # invalid id
        {"room_id": "AB1-102", "power_w": -5},                    # out of range
        {"room_id": "AB1-103", "power_w": "lots"},                # not a number
        "not an object",
    ], store, settings, now=TUESDAY_11AM)
    assert res["accepted"] == 1
    assert [r["index"] for r in res["rejected"]] == [1, 2, 3, 4, 5]


def test_duplicates_rejected_and_energy_integrated(store, settings):
    base = {"room_id": "AB1-101", "power_w": 3600, "occupancy": 10}
    pipeline.process_readings([dict(base, ts=TUESDAY_11AM)], store, settings, now=TUESDAY_11AM)
    res = pipeline.process_readings([dict(base, ts=TUESDAY_11AM)], store, settings, now=TUESDAY_11AM)
    assert res["accepted"] == 0 and "duplicate" in res["rejected"][0]["error"]
    pipeline.process_readings([dict(base, ts=TUESDAY_11AM + 120)], store, settings, now=TUESDAY_11AM + 120)
    rows = store.query_readings("AB1-101", 0, TUESDAY_11AM + 999)
    assert abs(rows[1]["energy_kwh"] - 3600 * 120 / 3.6e6) < 1e-6  # 2 minutes at 3.6 kW = 0.12 kWh


def test_device_clock_not_synced_uses_server_time(store, settings):
    pipeline.process_readings([{"room_id": "IOT-LAB-1", "power_w": 100, "ts": 12345}], store, settings, now=TUESDAY_11AM)
    assert store.query_readings("IOT-LAB-1", 0, TUESDAY_11AM)[0]["ts"] == TUESDAY_11AM


def test_hourly_rollups_match_readings(store, settings):
    readings = simulate_campus(TUESDAY_11AM)
    pipeline.process_readings(readings, store, settings, now=TUESDAY_11AM)
    h = hour_start(TUESDAY_11AM, settings.utc_offset_s)
    campus_kwh = store.query_hourly("CAMPUS", h, h)[0]["energy_kwh"]
    assert abs(campus_kwh - sum(r["energy_kwh"] for r in readings)) < 1e-6
    lab_kwh = store.query_hourly("LAB", h, h)[0]["energy_kwh"]
    assert abs(lab_kwh - sum(r["energy_kwh"] for r in readings if r["building_id"] == "LAB")) < 1e-6


def test_alerts_stored_and_only_high_severity_notified(store, settings, notifier):
    waste = {"room_id": "AB1-301", "power_w": 900, "occupancy": 0}        # small room -> low severity
    fault = {"room_id": "LAB-CS1", "power_w": 30000, "occupancy": 40}     # overload -> critical
    pipeline.process_readings([dict(waste, ts=TUESDAY_11AM), fault | {"ts": TUESDAY_11AM}], store, settings,
                              notifier=None, now=TUESDAY_11AM)
    res = pipeline.process_readings([dict(waste, ts=TUESDAY_11AM + 300)], store, settings, now=TUESDAY_11AM + 300)
    assert len(res["alerts"]) == 1
    stored = store.query_alerts(0, 10)
    assert {a["type"] for a in stored} == {"ENERGY_WASTE", "OVERLOAD"}
    assert store.query_hourly("AB1-301", 0, TUESDAY_11AM + 999)[0]["waste_kwh"] > 0

    from campus_energy.notifier import SnsNotifier

    class FakeSns:
        def __init__(self):
            self.calls = []

        def publish(self, **kw):
            self.calls.append(kw)

    sns = FakeSns()
    sent = SnsNotifier("arn:aws:sns:ap-south-1:123456789012:t", settings, client=sns).notify(stored)
    assert sent == 1 and len(sns.calls) == 1  # only the critical overload is e-mailed
    assert "Overload in Computer Lab 1" in sns.calls[0]["Subject"]
    assert len(sns.calls[0]["Subject"]) < 100


def test_backfill_seeds_history(store, settings):
    res = pipeline.backfill(store, settings, days=3, now=TUESDAY_11AM, live_hours=1)
    assert res["hourly_items"] > 3 * 24 * 20
    assert len(store.scan_room_states()) == 17
    h = hour_start(TUESDAY_11AM, settings.utc_offset_s)
    hours = store.query_hourly("CAMPUS", h - 72 * 3600, h)
    assert len(hours) >= 72
