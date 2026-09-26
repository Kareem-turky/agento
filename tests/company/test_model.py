import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from pydantic import ValidationError

import app.company
from app.company.operating_model import (
    AgentCapability,
    CompanyOperatingModel,
    EscalationCondition,
    KPIKey,
)
from tests.company.factories import operating_model_data

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_YAML = REPO_ROOT / "company" / "operating_model" / "operating-model.example.yaml"


def test_valid_model() -> None:
    model = CompanyOperatingModel.model_validate(operating_model_data())

    assert model.company_id == UUID("00000000-0000-4000-8000-000000000001")
    assert model.version == 1
    assert model.escalations[0].condition_key is EscalationCondition.ORDER_LATE
    assert model.kpis.primary_kpis == (KPIKey.LATE_ORDERS,)
    assert model.capabilities.enabled_agents == {AgentCapability.OPERATIONS}


@pytest.mark.parametrize("field", ["company_id", "version", "order_sla", "shipment_sla",
                                   "kpis", "reporting", "capabilities"])  # fmt: skip
def test_sections_are_required(field: str) -> None:
    data = operating_model_data()
    del data[field]
    with pytest.raises(ValidationError):
        CompanyOperatingModel.model_validate(data)


def test_escalations_default_to_none() -> None:
    data = operating_model_data()
    del data["escalations"]
    assert CompanyOperatingModel.model_validate(data).escalations == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 0},
        {"version": -1},
        {"version": "1"},
        {"version": 1.0},
        {"company_id": "not-a-uuid"},
        {"model_provider": "any"},
        {"api_key": "placeholder"},
        {"system_prompt": "You are..."},
        {"webhook_url": "https://example.invalid"},
        {"permissions": {"admin": True}},
    ],
)
def test_invalid_or_unknown_top_level_values_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        CompanyOperatingModel.model_validate(operating_model_data(**overrides))


def test_nested_unknown_fields_are_rejected() -> None:
    data = operating_model_data(reporting={"timezone": "UTC", "email_to": "ops"})
    with pytest.raises(ValidationError):
        CompanyOperatingModel.model_validate(data)


def test_duplicate_escalation_ids_are_rejected() -> None:
    rule = operating_model_data()["escalations"][0]
    with pytest.raises(ValidationError, match="unique"):
        CompanyOperatingModel.model_validate(operating_model_data(escalations=[rule, rule]))


def test_model_is_frozen() -> None:
    model = CompanyOperatingModel.model_validate(operating_model_data())
    with pytest.raises(ValidationError):
        model.version = 2
    with pytest.raises(AttributeError):
        model.escalations.append(model.escalations[0])  # type: ignore[attr-defined]


def test_json_round_trip_is_lossless_and_deterministic() -> None:
    model = CompanyOperatingModel.model_validate(operating_model_data())
    dumped = model.model_dump(mode="json")

    assert dumped["order_sla"]["processing_sla"] == 86400
    assert dumped["escalations"][0]["threshold"] == {"kind": "count", "value": 5}
    assert CompanyOperatingModel.model_validate(dumped) == model
    assert CompanyOperatingModel.model_validate_json(model.model_dump_json()) == model


def test_json_output_is_identical_across_hash_seeds() -> None:
    code = (
        "from app.company.operating_model import CompanyOperatingModel;"
        "from tests.company.factories import operating_model_data as d;"
        "print(CompanyOperatingModel.model_validate(d(kpis={'enabled_kpis':"
        "['late_orders','orders_created','fulfillment_rate','late_shipments']},"
        "capabilities={'enabled_agents':['operations','analytics','growth']})).model_dump_json())"
    )
    python_path = f"{Path(app.company.__file__).parents[2]}{os.pathsep}{REPO_ROOT}"
    outputs = set()
    for seed in ("1", "2", "3", "4"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": python_path}
        result = subprocess.run(  # noqa: S603 - fixed interpreter and code
            [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
        )
        outputs.add(result.stdout)

    assert len(outputs) == 1


def test_yaml_round_trip() -> None:
    model = CompanyOperatingModel.model_validate(operating_model_data())
    text = yaml.safe_dump(model.model_dump(mode="json"), sort_keys=False)

    assert CompanyOperatingModel.model_validate(yaml.safe_load(text)) == model


def test_example_yaml_is_valid_and_generic() -> None:
    text = EXAMPLE_YAML.read_text()
    model = CompanyOperatingModel.model_validate(yaml.safe_load(text))

    assert model.version == 1
    assert len(model.escalations) == 4
    assert "fulfly" not in text.lower()
    for marker in ("password", "secret:", "token", "api_key", "http://", "https://"):
        assert marker not in text.lower()
