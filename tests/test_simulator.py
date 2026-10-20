import pytest
from conftest import TUESDAY_11AM, ist

from campus_energy import anomaly, campus
from campus_energy.simulator import simulate_campus, simulate_reading


def test_readings_are_deterministic():
    room = campus.get_room("LAB-CS1")
    assert simulate_reading(room, TUESDAY_11AM) == simulate_reading(room, TUESDAY_11AM)


def test_reading_is_physically_consistent():
    for r in simulate_campus(TUESDAY_11AM):
        room = campus.get_room(r["room_id"])
        assert r["power_w"] >= room["standby_w"] * 0.8
        assert 190 <= r["voltage_v"] <= 265
        assert 0.7 <= r["power_factor"] <= 0.99
        # I = P / (V * PF); PF is rounded to 2 decimals in the payload, hence 1 % tolerance
        assert r["current_a"] == pytest.approx(r["power_w"] / (r["voltage_v"] * r["power_factor"]), rel=0.01)
        assert abs(r["energy_kwh"] - r["power_w"] * 300 / 3.6e6) < 1e-4


def test_classrooms_empty_at_3am_on_a_weekday():
    ts = ist(2026, 10, 20, 3)
    for rid in ("AB1-101", "AB1-102", "AB1-201"):
        assert simulate_reading(campus.get_room(rid), ts)["occupancy"] == 0


def test_campus_busier_at_11am_than_3am():
    day = sum(r["power_w"] for r in simulate_campus(TUESDAY_11AM))
    night = sum(r["power_w"] for r in simulate_campus(ist(2026, 10, 20, 3)))
    assert day > 3 * night


def test_waste_scenario_is_detectable():
    room = campus.get_room("AB1-101")
    r = simulate_reading(room, TUESDAY_11AM, scenario="waste")
    assert r["occupancy"] == 0
    assert anomaly.is_wasting(r, room)


def test_spike_and_voltage_scenarios():
    room = campus.get_room("LAB-CS1")
    assert simulate_reading(room, TUESDAY_11AM, scenario="spike")["power_w"] > room["rated_w"] * 1.1
    assert simulate_reading(room, TUESDAY_11AM, scenario="undervoltage")["voltage_v"] < 207
    assert simulate_reading(room, TUESDAY_11AM, scenario="overvoltage")["voltage_v"] > 253


def test_waste_happens_sometimes_after_classes():
    """Over a week the simulator should leave some empty rooms powered (otherwise nothing to detect)."""
    start = ist(2026, 10, 19, 0)
    wasted = 0
    for ts in range(start, start + 7 * 86400, 1800):
        for r in simulate_campus(ts):
            wasted += anomaly.is_wasting(r, campus.get_room(r["room_id"]))
    assert wasted > 20
