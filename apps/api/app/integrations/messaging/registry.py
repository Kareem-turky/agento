"""``MessagingIntegrationRegistry``: the static allowlist of messaging adapters (Task 037).

Keyed by Product Integration id and built explicitly in Product source code: there is no
discovery, entry point, filesystem or configuration-driven import and no remote code.
Construction is validated against the installed integrations (the Product
``IntegrationCatalog``, seen through the narrow ``InstalledIntegrations`` view so this
contract layer never imports integration management): every adapter must belong to an
INSTALLED integration of category ``messaging`` with the SAME id, and may only claim
capabilities that definition declares. Different ids are never silently bound.

``build_default_messaging_registry`` returns this build's registry: EMPTY (no real
messaging provider exists). Tests inject deterministic fakes.
"""

from collections.abc import Iterable
from typing import Protocol

from app.integrations.messaging.capabilities import MESSAGING_CAPABILITIES
from app.integrations.messaging.contract import MessagingIntegration
from app.integrations.messaging.errors import MessagingRegistryError

MESSAGING_CATEGORY = "messaging"


class _Definition(Protocol):
    @property
    def integration_id(self) -> str: ...
    @property
    def category(self) -> str: ...
    @property
    def capabilities(self) -> frozenset[str]: ...


class _Installed(Protocol):
    @property
    def definition(self) -> _Definition: ...


class InstalledIntegrations(Protocol):
    """What the registry reads from the Product IntegrationCatalog (lookup only)."""

    def get(self, integration_id: str) -> _Installed | None: ...


class MessagingIntegrationRegistry:
    __slots__ = ("_by_id",)

    def __init__(
        self, adapters: Iterable[MessagingIntegration], *, catalog: InstalledIntegrations
    ) -> None:
        by_id: dict[str, MessagingIntegration] = {}
        for adapter in adapters:
            if not isinstance(adapter, MessagingIntegration):
                raise TypeError("a MessagingIntegrationRegistry holds MessagingIntegration only")
            integration_id = adapter.integration_id
            installed = catalog.get(integration_id)
            if installed is None:
                raise MessagingRegistryError("messaging adapter for an uninstalled integration")
            definition = installed.definition
            if definition.integration_id != integration_id:
                raise MessagingRegistryError("messaging adapter and definition ids differ")
            if definition.category != MESSAGING_CATEGORY:
                raise MessagingRegistryError("messaging adapter for a non-messaging integration")
            claimed = frozenset(adapter.capabilities)
            if not claimed <= MESSAGING_CAPABILITIES or not claimed <= definition.capabilities:
                raise MessagingRegistryError("messaging adapter claims an undeclared capability")
            if integration_id in by_id:
                raise MessagingRegistryError("duplicate messaging adapter")
            by_id[integration_id] = adapter
        object.__setattr__(self, "_by_id", dict(sorted(by_id.items())))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("the messaging registry is immutable")

    def get(self, integration_id: str) -> MessagingIntegration | None:
        return self._by_id.get(integration_id) if isinstance(integration_id, str) else None

    @property
    def integration_ids(self) -> frozenset[str]:
        return frozenset(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)


def build_default_messaging_registry(
    catalog: InstalledIntegrations,
) -> MessagingIntegrationRegistry:
    """This build registers no messaging adapter: no real provider exists yet."""
    return MessagingIntegrationRegistry((), catalog=catalog)
