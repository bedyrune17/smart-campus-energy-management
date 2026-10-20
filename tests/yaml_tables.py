"""Read the DynamoDB table definitions from template.yaml so tests use exactly the deployed key schemas."""

from pathlib import Path

import yaml

STORE_KEYS = {
    "ReadingsTable": "readings",
    "RoomsTable": "rooms",
    "HourlyTable": "hourly",
    "AlertsTable": "alerts",
    "ForecastTable": "forecasts",
}


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that tolerates CloudFormation short-form tags like !Ref / !Sub."""


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _any_tag)


def load_template() -> dict:
    path = Path(__file__).resolve().parents[1] / "template.yaml"
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_CfnLoader)


def tables_from_template() -> dict:
    resources = load_template()["Resources"]
    return {name: r["Properties"] for name, r in resources.items() if r["Type"] == "AWS::DynamoDB::Table"}
