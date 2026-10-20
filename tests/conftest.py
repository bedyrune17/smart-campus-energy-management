import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from campus_energy.config import Settings  # noqa: E402
from campus_energy.store import MemoryStore  # noqa: E402

IST = timezone(timedelta(minutes=330))


def ist(year, month, day, hour, minute=0) -> int:
    """Epoch seconds for a campus-local (IST) wall-clock time."""
    return int(datetime(year, month, day, hour, minute, tzinfo=IST).timestamp())


# Tuesday 20 Oct 2026, 11:00 IST - classes in session
TUESDAY_11AM = ist(2026, 10, 20, 11)


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def store(settings):
    return MemoryStore(settings)


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, alerts):
        self.sent.extend(alerts)
        return len(alerts)


@pytest.fixture
def notifier():
    return RecordingNotifier()
