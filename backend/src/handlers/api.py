"""Lambda: dashboard REST API (API Gateway HTTP API, payload v2)."""

import json
import logging
import time

from campus_energy.config import Settings
from campus_energy.router import route
from campus_energy.store import DynamoStore

log = logging.getLogger()
log.setLevel(logging.INFO)

SETTINGS = Settings.from_env()
_store = None


def _get_store():
    global _store
    if _store is None:
        _store = DynamoStore(SETTINGS)
    return _store


def handler(event, context):
    http = (event.get("requestContext") or {}).get("http") or {}
    method = http.get("method", "GET")
    path = event.get("rawPath") or "/"
    try:
        status, body = route(method, path, event.get("queryStringParameters"), _get_store(), SETTINGS, int(time.time()))
    except Exception:
        log.exception("API error on %s %s", method, path)
        status, body = 500, {"error": "internal error"}
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, default=str),
    }
