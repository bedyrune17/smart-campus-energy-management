"""Lambda: hourly demand forecast for every building and the whole campus."""

import json
import logging
import time

from campus_energy import service
from campus_energy.config import Settings
from campus_energy.store import DynamoStore

log = logging.getLogger()
log.setLevel(logging.INFO)

SETTINGS = Settings.from_env()


def handler(event, context):
    result = service.refresh_forecasts(DynamoStore(SETTINGS), SETTINGS, int(time.time()))
    log.info("forecasts: %s", json.dumps(result))
    return result
