"""Runtime settings, read from environment variables (set by template.yaml)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Campus clock. India has no DST, so a fixed offset is enough (IST = +330 min).
    utc_offset_minutes: int = 330
    # Expected seconds between readings from one room (used for kWh integration).
    reading_interval_s: int = 300
    # Money / carbon conversion.
    tariff_per_kwh: float = 8.0
    currency: str = "INR"
    co2_kg_per_kwh: float = 0.71  # India grid emission factor (CEA)
    # Anomaly detection tuning.
    waste_min_readings: int = 2  # consecutive "unoccupied but loaded" readings before alerting
    alert_cooldown_s: int = 1800  # one alert per room per type per 30 min
    zscore_threshold: float = 4.0
    overload_factor: float = 1.1  # power above 110% of the room's rated load = overload
    under_voltage: float = 207.0  # 230 V -10%
    over_voltage: float = 253.0  # 230 V +10%
    notify_min_severity: str = "high"
    # Data retention (DynamoDB TTL).
    readings_ttl_days: int = 7
    hourly_ttl_days: int = 90
    alerts_ttl_days: int = 30

    @property
    def utc_offset_s(self) -> int:
        return self.utc_offset_minutes * 60

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            utc_offset_minutes=int(_env("CAMPUS_UTC_OFFSET_MINUTES", "330")),
            reading_interval_s=int(_env("READING_INTERVAL_S", "300")),
            tariff_per_kwh=float(_env("TARIFF_PER_KWH", "8.0")),
            currency=_env("CURRENCY", "INR"),
            co2_kg_per_kwh=float(_env("CO2_KG_PER_KWH", "0.71")),
            waste_min_readings=int(_env("WASTE_MIN_READINGS", "2")),
            alert_cooldown_s=int(_env("ALERT_COOLDOWN_S", "1800")),
            zscore_threshold=float(_env("ZSCORE_THRESHOLD", "4.0")),
            overload_factor=float(_env("OVERLOAD_FACTOR", "1.1")),
            notify_min_severity=_env("NOTIFY_MIN_SEVERITY", "high"),
            readings_ttl_days=int(_env("READINGS_TTL_DAYS", "7")),
        )
