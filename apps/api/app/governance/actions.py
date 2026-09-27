"""Action definitions (trusted), the action catalog (trusted) and action intents (untrusted).

An ``ActionDefinition`` carries everything that governs an action: its risk, the
exact permission it requires and its scope requirement. Definitions live only in a
backend-built ``ActionCatalog``.

An ``ActionIntent`` is what a caller (a user, an API client, later an agent) asks
for. It is untrusted and can carry nothing but an action name: risk, permission, scope
requirement and identity can never be supplied or overridden by it.
"""

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.context.models import Identifier

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# Dotted lowercase names such as "orders.read" or "shipments.cancel". No wildcards.
DottedName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$", max_length=128)
]


class ActionRisk(StrEnum):
    READ = "read"
    LOW_RISK_WRITE = "low_risk_write"
    MEDIUM_RISK = "medium_risk"
    HIGH_RISK = "high_risk"


class ActionScopeRequirement(StrEnum):
    """COMPANY: the actor's company must be the target company.
    STORE: additionally, the target store must be one the actor is granted.
    A COMPANY action is not constrained by any store in the scope."""

    COMPANY = "company"
    STORE = "store"


class ActionDefinition(BaseModel):
    """Trusted description of an action. Built by backend code, never from input."""

    model_config = _FROZEN

    name: DottedName
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    risk: ActionRisk
    required_permission: DottedName
    scope_requirement: ActionScopeRequirement


class ActionCatalog:
    """Immutable registry of trusted action definitions, keyed by name.

    Lookups of unknown names return ``None``; callers treat that as a denial.
    """

    def __init__(self, definitions: Iterable[ActionDefinition]) -> None:
        by_name: dict[str, ActionDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, ActionDefinition):
                raise TypeError("an ActionCatalog holds ActionDefinition objects only")
            if definition.name in by_name:
                raise ValueError(f"duplicate action definition: {definition.name}")
            by_name[definition.name] = definition
        self._definitions: Mapping[str, ActionDefinition] = MappingProxyType(by_name)

    def get(self, name: str) -> ActionDefinition | None:
        return self._definitions.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._definitions)


class ActionIntent(BaseModel):
    """Untrusted request to perform an action: the action name and nothing else."""

    model_config = _FROZEN

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ActionScope(BaseModel):
    """Trusted target of an action, resolved by backend code (not by the caller).

    ``store_id`` is required for STORE-scoped actions and ignored by COMPANY-scoped ones.
    """

    model_config = _FROZEN

    company_id: Identifier
    store_id: Identifier | None = None
