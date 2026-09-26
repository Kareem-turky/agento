"""Shared value objects and field types for the canonical commerce domain.

Only Pydantic and the standard library are used here: the domain must not depend on
any runtime, web framework, persistence layer or external commerce system.
"""

from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, StringConstraints

# Every domain model: immutable (and therefore hashable) and strict about unknown fields.
DOMAIN_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


# Optional text: blank or whitespace-only input normalizes to ``None``; otherwise trimmed.
OptionalStr = Annotated[NonEmptyStr | None, BeforeValidator(_blank_to_none)]


def _reject_float(value: Any) -> Any:
    # bool is an int subclass; float would carry binary rounding error into the domain.
    if isinstance(value, (float, bool)):
        raise ValueError("use Decimal, int or a decimal string, not float or bool")
    return value


def _require_finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("must be a finite decimal")
    return value


# Exact decimal number: accepts Decimal, int or a decimal string; rejects float/NaN/Infinity.
# Precision and scale are preserved as given (no rounding, no fixed number of places).
DecimalValue = Annotated[Decimal, BeforeValidator(_reject_float), AfterValidator(_require_finite)]


def _normalize_currency(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


# ISO 4217-style alphabetic currency code, normalized to upper case (e.g. "usd" -> "USD").
CurrencyCode = Annotated[
    str, BeforeValidator(_normalize_currency), StringConstraints(pattern=r"^[A-Z]{3}$")
]


class ExternalReference(BaseModel):
    """Where an entity came from in an external system, e.g. (system, external_id).

    External IDs live only here; they are never the meaning of a canonical ``id``.
    """

    model_config = DOMAIN_MODEL_CONFIG

    system: NonEmptyStr
    external_id: NonEmptyStr


ExternalReferences = frozenset[ExternalReference]


class Money(BaseModel):
    """An exact amount in one currency. No exchange-rate logic."""

    model_config = DOMAIN_MODEL_CONFIG

    amount: DecimalValue
    currency: CurrencyCode
