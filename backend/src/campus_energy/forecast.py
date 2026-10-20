"""Energy demand forecasting (next 24 hours, hourly kWh).

Model: additive Holt-Winters style exponential smoothing with a level term and
one 24-hour seasonal profile per day type (weekday / Saturday / Sunday), since
a campus on Sunday behaves nothing like a campus on Tuesday.

    prediction   y_hat(t) = L + S[daytype(t), hour(t)]
    level        L <- L + alpha * (y - S - L)
    season       S <- S + gamma * (y - L - S)

It is fitted online over the stored hourly history, so it adapts to semester
changes, exams, heat waves, etc. One-step-ahead residuals give the prediction
interval and a backtest WAPE (weighted absolute % error = sum|error| / sum(actual))
that the dashboard shows as model accuracy. WAPE is used instead of MAPE because
MAPE explodes on night hours where consumption is close to zero.
Pure Python - no numpy/pandas needed inside Lambda.
"""

from __future__ import annotations

import math
import statistics

from .timeutil import HOUR, local_dt

MODEL_NAME = "Holt-Winters additive (weekday / Sat / Sun x 24 h seasonality)"
Z_95 = 1.96


def _daytype(weekday: int) -> str:
    return "sat" if weekday == 5 else "sun" if weekday == 6 else "weekday"


def _key(ts: int, offset_s: int) -> tuple[str, int]:
    dt = local_dt(ts, offset_s)
    return _daytype(dt.weekday()), dt.hour


_FALLBACK = {"weekday": ("sat", "sun"), "sat": ("weekday", "sun"), "sun": ("sat", "weekday")}


def _season(seasonal: dict, key: tuple[str, int]) -> float | None:
    if key in seasonal:
        return seasonal[key]
    for alt in _FALLBACK[key[0]]:
        if (alt, key[1]) in seasonal:
            return seasonal[(alt, key[1])]
    return None


def forecast(history: list[tuple[int, float]], start_hour: int, offset_s: int = 19800,
             horizon: int = 24, alpha: float = 0.05, gamma: float = 0.2) -> dict:
    """Forecast `horizon` hourly kWh values starting at `start_hour`.

    history: (hour_start_epoch, kwh) for complete hours before start_hour, any order, gaps allowed.
    """
    history = sorted((int(t), float(v)) for t, v in history if int(t) < start_hour)
    if len(history) < 24:
        return _naive(history, start_hour, horizon)

    level = statistics.fmean(v for _, v in history[:24])
    seasonal: dict[tuple[str, int], float] = {}
    residuals: list[float] = []
    backtest: list[tuple[float, float]] = []  # (actual, predicted)

    for ts, y in history:
        key = _key(ts, offset_s)
        if key not in seasonal:  # first time we see this (daytype, hour): initialise, don't score
            seasonal[key] = y - level
            continue
        s = seasonal[key]
        pred = level + s
        residuals.append(y - pred)
        backtest.append((y, max(pred, 0.0)))
        new_level = level + alpha * (y - s - level)
        seasonal[key] = s + gamma * (y - new_level - s)
        level = new_level

    recent = residuals[-96:] or [0.0]
    sigma = math.sqrt(statistics.fmean(r * r for r in recent))

    points = []
    for h in range(horizon):
        ts = start_hour + h * HOUR
        s = _season(seasonal, _key(ts, offset_s))
        yhat = max(0.0, level + (s if s is not None else 0.0))
        points.append({
            "hour": ts,
            "kwh": round(yhat, 2),
            "lower": round(max(0.0, yhat - Z_95 * sigma), 2),
            "upper": round(yhat + Z_95 * sigma, 2),
        })

    window = backtest[-48:]
    total = sum(a for a, _ in window)
    wape = sum(abs(a - p) for a, p in window) / total * 100 if total > 0 else None
    mae = statistics.fmean(abs(a - p) for a, p in window) if window else None
    return _result(MODEL_NAME, history, points, {
        "wape_pct": round(wape, 1) if wape is not None else None,
        "mae_kwh": round(mae, 2) if mae is not None else None,
        "n": len(window),
    })


def _naive(history: list[tuple[int, float]], start_hour: int, horizon: int) -> dict:
    """Not enough data yet: repeat the mean so the dashboard still has something to show."""
    mean = statistics.fmean(v for _, v in history) if history else 0.0
    points = [{"hour": start_hour + h * HOUR, "kwh": round(mean, 2), "lower": 0.0, "upper": round(mean * 2, 2)}
              for h in range(horizon)]
    return _result("naive mean (warming up - needs 24 h of data)", history, points,
                   {"wape_pct": None, "mae_kwh": None, "n": 0})


def _result(model: str, history: list, points: list[dict], backtest: dict) -> dict:
    peak = max(points, key=lambda p: p["kwh"]) if points else None
    return {
        "model": model,
        "history_hours": len(history),
        "points": points,
        "next_24h_kwh": round(sum(p["kwh"] for p in points[:24]), 1),
        "peak_hour": peak["hour"] if peak else None,
        # 1 kWh consumed within one hour == 1 kW average demand during that hour
        "peak_kw": peak["kwh"] if peak else None,
        "backtest": backtest,
    }
