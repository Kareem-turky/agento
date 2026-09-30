"""The Product-owned, immutable allowlist of business backend plugins.

    APP_BUSINESS_BACKEND=<backend id>   (an identifier, never code or an import path)
      -> BusinessBackendRegistry.resolve(id) -> BusinessBackendRegistration
           backend_id, allowed_environments, builder (chosen by Product source code),
           input_spec (the NAMES of the config/secret inputs the builder needs)

Registrations are explicit and reviewed: there is no discovery (entry points, directory
scanning, remote downloads), no runtime mutation API and no import driven by
configuration. The reserved "disabled" sentinel is not a plugin and cannot be
registered. Selecting a backend is deployment configuration, never authorization.
A registration declares input names only: never a token, password, secret value,
Authorization header, database or provider credential.

A real backend is added only after it passes the CommerceIntegration conformance
harness and provider-specific review, by adding ONE registration below.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import get_args

from agno.models.base import Model

from app.composition.backend_inputs import (
    EMPTY_INPUT_SPEC,
    BusinessBackendInputs,
    BusinessBackendInputSpec,
)
from app.composition.contracts import (
    BACKEND_INPUTS_UNAVAILABLE,
    BusinessBackendBuilder,
    DeploymentComposition,
    DeploymentCompositionError,
)
from app.config import DISABLED_BUSINESS_BACKEND, Environment, Settings

_BACKEND_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ENVIRONMENTS = frozenset(get_args(Environment))


@dataclass(frozen=True)
class BusinessBackendRegistration:
    """One allowlisted backend: its id, where it may run, its Product-owned builder and
    the names of the inputs that builder requires (none by default)."""

    backend_id: str
    allowed_environments: frozenset[str]
    builder: BusinessBackendBuilder
    input_spec: BusinessBackendInputSpec = EMPTY_INPUT_SPEC

    def __post_init__(self) -> None:
        if not isinstance(self.backend_id, str) or not _BACKEND_ID.fullmatch(self.backend_id):
            raise ValueError("invalid business backend id")
        if self.backend_id == DISABLED_BUSINESS_BACKEND:
            raise ValueError("'disabled' is a reserved Product sentinel, not a backend")
        if not isinstance(self.allowed_environments, frozenset) or not self.allowed_environments:
            raise ValueError("a backend must allow at least one environment")
        if not self.allowed_environments <= _ENVIRONMENTS:
            raise ValueError("unknown environment in allowed_environments")
        if not callable(self.builder):
            raise ValueError("a backend builder must be callable")
        if not isinstance(self.input_spec, BusinessBackendInputSpec):
            raise ValueError("a backend input spec must be a BusinessBackendInputSpec")


class BusinessBackendRegistry:
    """Immutable after construction: duplicates are rejected, never 'last one wins'."""

    __slots__ = ("_by_id",)

    def __init__(self, registrations: Iterable[BusinessBackendRegistration]) -> None:
        by_id: dict[str, BusinessBackendRegistration] = {}
        for registration in registrations:
            if not isinstance(registration, BusinessBackendRegistration):
                raise TypeError("registry entries must be BusinessBackendRegistration")
            if registration.backend_id in by_id:
                raise ValueError("duplicate business backend registration")
            by_id[registration.backend_id] = registration
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("the business backend registry is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("the business backend registry is immutable")

    def resolve(self, backend_id: str) -> BusinessBackendRegistration | None:
        return self._by_id.get(backend_id) if isinstance(backend_id, str) else None

    @property
    def backend_ids(self) -> frozenset[str]:
        return frozenset(self._by_id)


def _build_mock_backend(
    settings: Settings, *, model: Model | None = None, inputs: BusinessBackendInputs
) -> DeploymentComposition:
    # The mock declares no inputs; anything else means the composition is miswired.
    if not isinstance(inputs, BusinessBackendInputs) or not inputs.is_empty:
        raise DeploymentCompositionError(BACKEND_INPUTS_UNAVAILABLE) from None
    # The one static, Product-chosen lazy import: the mock stack loads only after this
    # backend was resolved AND its environment allowed. Configuration never names it.
    from app.composition.local_mock import build_local_mock_composition

    return build_local_mock_composition(settings, model=model)


def build_default_backend_registry() -> BusinessBackendRegistry:
    """The Product's allowlist. Today exactly one plugin: the local/test mock backend,
    which declares no inputs (it reads no configuration or secret files)."""
    return BusinessBackendRegistry(
        (
            BusinessBackendRegistration(
                backend_id="mock",
                allowed_environments=frozenset({"local", "test"}),
                builder=_build_mock_backend,
            ),
        )
    )
