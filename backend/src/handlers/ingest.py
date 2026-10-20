"""Lambda: ingest sensor readings.

Two triggers share this function:
* API Gateway  POST /readings   (Python simulator, ESP32 over HTTPS) - needs header x-device-key
* AWS IoT Core rule on MQTT topic campus/energy/<room_id>/readings - authenticated by X.509 certs

Body: one reading, a list of readings, or {"readings": [...]}.
"""

import base64
import hmac
import json
import logging
import os

from campus_energy.config import Settings
from campus_energy.notifier import SnsNotifier
from campus_energy.pipeline import process_readings
from campus_energy.store import DynamoStore

log = logging.getLogger()
log.setLevel(logging.INFO)

SETTINGS = Settings.from_env()
MAX_BODY = 256_000
_store = None
_notifier = None


def _deps():
    global _store, _notifier
    if _store is None:
        _store = DynamoStore(SETTINGS)
        _notifier = SnsNotifier.from_env(SETTINGS)
    return _store, _notifier


def _http(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def _as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("readings"), list):
        return payload["readings"]
    return [payload]


def handler(event, context):
    store, notifier = _deps()

    if "requestContext" not in event:  # AWS IoT Core topic rule: event is the MQTT payload
        readings = _as_list(event)
        for r in readings:
            if isinstance(r, dict):
                r.setdefault("source", "iot-core")
        result = process_readings(readings, store, SETTINGS, notifier)
        log.info("iot ingest: %s", json.dumps({k: result[k] for k in ("accepted", "notified")}))
        return result

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    expected = os.environ.get("DEVICE_API_KEY", "")
    if expected and not hmac.compare_digest(headers.get("x-device-key", ""), expected):
        return _http(401, {"error": "missing or invalid x-device-key header"})

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8", "replace")
    if len(body) > MAX_BODY:
        return _http(413, {"error": "body too large"})
    try:
        payload = json.loads(body)
    except ValueError:
        return _http(400, {"error": "body must be JSON"})

    try:
        result = process_readings(_as_list(payload), store, SETTINGS, notifier)
    except Exception:
        log.exception("ingest failed")
        return _http(500, {"error": "internal error"})
    log.info("http ingest: accepted=%s rejected=%s alerts=%s", result["accepted"], len(result["rejected"]), len(result["alerts"]))
    return _http(200 if result["accepted"] else 400, result)
