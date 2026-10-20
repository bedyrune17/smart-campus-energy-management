import importlib
from pathlib import Path

import pytest

pytest.importorskip("yaml")
import yaml_tables  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "backend" / "src"


def functions():
    resources = yaml_tables.load_template()["Resources"]
    return {k: v["Properties"] for k, v in resources.items() if v["Type"] == "AWS::Serverless::Function"}


def test_every_function_has_its_own_code_uri():
    # `aws cloudformation package` (used by deploy.sh) ignores Globals.CodeUri and would zip the repo root.
    for name, props in functions().items():
        assert props.get("CodeUri") == "backend/src/", name


def test_handlers_resolve_inside_code_uri():
    for name, props in functions().items():
        module, func = props["Handler"].rsplit(".", 1)
        assert (SRC / Path(*module.split("."))).with_suffix(".py").exists(), name
        assert hasattr(importlib.import_module(module), func), name
