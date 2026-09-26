from datetime import timedelta

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.company.operating_model import Duration


class Holder(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: Duration


def test_integer_seconds_become_a_timedelta() -> None:
    assert Holder(value=86400).value == timedelta(days=1)


def test_whole_second_timedelta_is_accepted() -> None:
    assert Holder(value=timedelta(hours=2)).value == timedelta(hours=2)


def test_serializes_as_integer_seconds_and_round_trips() -> None:
    holder = Holder(value=timedelta(hours=1))
    dumped = holder.model_dump(mode="json")

    assert dumped == {"value": 3600}
    assert isinstance(dumped["value"], int)
    assert Holder.model_validate(dumped) == holder
    assert Holder.model_validate_json(holder.model_dump_json()) == holder


@pytest.mark.parametrize(
    "bad",
    [0, -1, 1.5, 86400.0, True, False, "86400", "2 days", "P1D", None,
     timedelta(0), timedelta(seconds=-5), timedelta(seconds=1, microseconds=500000)],
)  # fmt: skip
def test_rejects_non_positive_fractional_or_non_integer_values(bad: object) -> None:
    with pytest.raises(ValidationError):
        Holder(value=bad)


def test_rejects_numeric_strings_in_json_too() -> None:
    with pytest.raises(ValidationError):
        Holder.model_validate_json('{"value": "3600"}')
    with pytest.raises(ValidationError):
        Holder.model_validate_json('{"value": 3600.5}')
