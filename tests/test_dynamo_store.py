"""Runs the real DynamoDB code path against moto's in-process AWS mock (no AWS account needed)."""

import pytest

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")
pytest.importorskip("yaml")

import yaml_tables  # noqa: E402
from conftest import TUESDAY_11AM  # noqa: E402

from campus_energy import pipeline, service  # noqa: E402
from campus_energy.router import route  # noqa: E402
from campus_energy.store import DynamoStore  # noqa: E402


@pytest.fixture
def dynamo(monkeypatch, settings):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with moto.mock_aws():
        ddb = boto3.resource("dynamodb", region_name="ap-south-1")
        names = {}
        for logical, spec in yaml_tables.tables_from_template().items():
            ddb.create_table(TableName=logical, BillingMode="PAY_PER_REQUEST",
                             AttributeDefinitions=spec["AttributeDefinitions"], KeySchema=spec["KeySchema"])
            names[yaml_tables.STORE_KEYS[logical]] = logical
        yield DynamoStore(settings, table_names=names, resource=ddb)


def test_full_flow_on_dynamodb(dynamo, settings, notifier):
    pipeline.backfill(dynamo, settings, days=2, now=TUESDAY_11AM, live_hours=1)
    service.refresh_forecasts(dynamo, settings, TUESDAY_11AM)
    res = pipeline.process_readings(
        [{"room_id": "LAB-CS1", "power_w": 30000, "occupancy": 40, "voltage_v": 198, "ts": TUESDAY_11AM + 1}],
        dynamo, settings, notifier=notifier, now=TUESDAY_11AM + 1)
    assert res["accepted"] == 1 and len(notifier.sent) == 2

    status, body = route("GET", "/summary", {}, dynamo, settings, TUESDAY_11AM + 1)
    assert status == 200 and body["campus"]["rooms_total"] == 17 and body["campus"]["rooms_fault"] >= 1
    _, alerts = route("GET", "/alerts", {"hours": "24"}, dynamo, settings, TUESDAY_11AM + 1)
    assert alerts["alerts"][0]["room_id"] == "LAB-CS1"  # newest first
    _, fc = route("GET", "/forecast", {"scope": "CAMPUS"}, dynamo, settings, TUESDAY_11AM + 1)
    assert len(fc["points"]) == 24 and isinstance(fc["points"][0]["kwh"], (int, float))
    _, rr = route("GET", "/rooms/LAB-CS1/readings", {"hours": "1"}, dynamo, settings, TUESDAY_11AM + 1)
    assert rr["readings"][-1]["power_w"] == 30000
