import math

from conftest import ist

from campus_energy.forecast import forecast
from campus_energy.timeutil import HOUR, local_dt

OFF = 19800
START = ist(2026, 9, 28, 0)  # a Monday


def synthetic(ts):
    """Campus-like load: busy weekday daytime, quiet nights, lighter weekends."""
    dt = local_dt(ts, OFF)
    daily = 40 + 60 * max(0.0, math.sin(math.pi * (dt.hour - 7) / 12)) if 7 <= dt.hour <= 19 else 40
    return daily * (0.5 if dt.weekday() >= 5 else 1.0)


def history(days):
    return [(START + h * HOUR, synthetic(START + h * HOUR)) for h in range(days * 24)]


def test_learns_daily_and_weekly_pattern():
    hist = history(21)
    start = START + 21 * 24 * HOUR
    fc = forecast(hist, start, OFF)
    assert len(fc["points"]) == 24
    errors = [abs(p["kwh"] - synthetic(p["hour"])) for p in fc["points"]]
    actual = sum(synthetic(p["hour"]) for p in fc["points"])
    assert sum(errors) / actual < 0.05  # < 5 % WAPE on a clean signal
    assert fc["backtest"]["wape_pct"] < 10
    assert fc["peak_kw"] > 80


def test_intervals_and_non_negative():
    fc = forecast(history(14), START + 14 * 24 * HOUR, OFF)
    for p in fc["points"]:
        assert 0 <= p["lower"] <= p["kwh"] <= p["upper"]


def test_ignores_future_and_unsorted_history():
    hist = history(10)
    start = START + 7 * 24 * HOUR
    a = forecast(list(reversed(hist)), start, OFF)
    b = forecast([x for x in hist if x[0] < start], start, OFF)
    assert a["points"] == b["points"]


def test_warm_up_with_little_data():
    fc = forecast(history(1)[:10], START + 10 * HOUR, OFF)
    assert fc["model"].startswith("naive")
    assert len(fc["points"]) == 24
