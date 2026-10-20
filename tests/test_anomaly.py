from conftest import TUESDAY_11AM, ist

from campus_energy import anomaly, campus

ROOM = campus.get_room("AB1-101")


def reading(ts=TUESDAY_11AM, power=2000.0, occupancy=30, voltage=231.0):
    return {"room_id": ROOM["room_id"], "ts": ts, "power_w": power, "occupancy": occupancy, "voltage_v": voltage}


def run(readings, settings, state=None):
    state = state or anomaly.new_state(ROOM)
    alerts = []
    ev = None
    for r in readings:
        ev = anomaly.evaluate(r, ROOM, state, settings)
        alerts += ev.alerts
        state = ev.state
    return ev, alerts


def types(alerts):
    return [a["type"] for a in alerts]


def test_normal_occupied_reading_raises_nothing(settings):
    ev, alerts = run([reading()], settings)
    assert alerts == [] and ev.status == "occupied"


def test_waste_needs_consecutive_readings(settings):
    first = reading(power=3000, occupancy=0)
    second = reading(ts=TUESDAY_11AM + 300, power=3000, occupancy=0)
    ev, alerts = run([first], settings)
    assert ev.status == "wasting" and alerts == []
    ev, alerts = run([first, second], settings)
    assert types(alerts) == ["ENERGY_WASTE"]
    assert alerts[0]["severity"] == "medium"
    assert "₹" in alerts[0]["message"]


def test_after_hours_waste(settings):
    night = ist(2026, 10, 20, 22)
    _, alerts = run([reading(ts=night, power=3500, occupancy=0), reading(ts=night + 300, power=3500, occupancy=0)], settings)
    assert types(alerts) == ["AFTER_HOURS"]
    assert alerts[0]["severity"] == "high"


def test_unknown_occupancy_is_never_waste(settings):
    r = reading(power=3500)
    r.pop("occupancy")
    ev, alerts = run([r, dict(r, ts=r["ts"] + 300)], settings)
    assert alerts == [] and not ev.wasting


def test_overload_and_voltage(settings):
    _, alerts = run([reading(power=ROOM["rated_w"] * 1.6, voltage=198)], settings)
    assert set(types(alerts)) == {"OVERLOAD", "UNDER_VOLTAGE"}
    overload = next(a for a in alerts if a["type"] == "OVERLOAD")
    assert overload["severity"] == "critical"
    _, alerts = run([reading(voltage=258)], settings)
    assert types(alerts) == ["OVER_VOLTAGE"]


def test_cooldown_suppresses_repeats(settings):
    rs = [reading(ts=TUESDAY_11AM + i * 300, power=3000, occupancy=0) for i in range(6)]
    _, alerts = run(rs, settings)
    assert types(alerts) == ["ENERGY_WASTE"]  # 30 min cooldown


def test_statistical_baseline_flags_unusual_consumption(settings):
    normal = [reading(ts=TUESDAY_11AM + i * 300, power=2000 + (i % 3) * 20) for i in range(20)]
    ev, alerts = run(normal, settings)
    assert alerts == []
    assert abs(ev.state["stats"]["occupied"]["mean"] - 2020) < 30
    # well below the rated 3.77 kW (so not an overload) but far outside this room's normal pattern
    odd = reading(ts=TUESDAY_11AM + 21 * 300, power=3400)
    ev, alerts = run([odd], settings, ev.state)
    assert types(alerts) == ["UNUSUAL_CONSUMPTION"]
