"""Shared field types for the Company Operating Model.

Pure configuration: Pydantic and the standard library only, plus the canonical
commerce domain. No environment reads, no I/O, nothing executable.
"""

from collections.abc import Iterable
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, PlainSerializer


def _seconds_to_timedelta(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("durations are whole seconds as an integer (no floats or booleans)")
    if isinstance(value, int):
        return timedelta(seconds=value)
    if isinstance(value, timedelta):
        if value.microseconds:
            raise ValueError("durations must be a whole number of seconds")
        return value
    raise ValueError("durations are whole seconds as an integer, e.g. 86400 for one day")


def _require_positive(value: timedelta) -> timedelta:
    if value <= timedelta(0):
        raise ValueError("durations must be greater than zero")
    return value


# A positive whole-second duration. Python value: ``timedelta``; JSON/YAML form: an
# integer number of seconds (86400 = 1 day). Strings such as "two days" or ISO 8601
# text are rejected on purpose so the configuration stays unambiguous.
Duration = Annotated[
    timedelta,
    BeforeValidator(_seconds_to_timedelta),
    AfterValidator(_require_positive),
    PlainSerializer(lambda value: int(value.total_seconds()), return_type=int, when_used="json"),
]


def _sorted_values(values: Iterable[StrEnum]) -> list[str]:
    return sorted(value.value for value in values)


# Serializer for ``frozenset`` fields of enum members: JSON output is a sorted list,
# so serialized configuration is deterministic regardless of hash seeds.
# Usage: ``Annotated[frozenset[SomeEnum], SortedJson]``.
SortedJson = PlainSerializer(_sorted_values, return_type=list[str], when_used="json")
