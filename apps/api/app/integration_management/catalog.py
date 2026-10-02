"""The immutable Integration Catalog: which integration types THIS build installs.

    IntegrationCatalog((InstalledIntegration(definition, driver), ...))

An explicit, statically reviewed allowlist built in Product source code. There is no
plugin discovery, entry point, configuration-driven import, filesystem module loading,
remote code or HTTP mutation: adding a provider is a deliberate, reviewed code change.

``build_default_integration_catalog`` returns the catalog of this build: it installs NO
integration (no real provider adapter exists yet). Tests inject deterministic fakes.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.integration_management.definitions import IntegrationDefinition
from app.integration_management.drivers import IntegrationConnectionDriver


@dataclass(frozen=True, slots=True)
class InstalledIntegration:
    definition: IntegrationDefinition
    driver: IntegrationConnectionDriver


class IntegrationCatalog:
    __slots__ = ("_installed",)

    def __init__(self, installed: Iterable[InstalledIntegration]) -> None:
        by_id: dict[str, InstalledIntegration] = {}
        for entry in installed:
            if not isinstance(entry, InstalledIntegration):
                raise TypeError("an IntegrationCatalog holds InstalledIntegration entries only")
            if not isinstance(entry.definition, IntegrationDefinition):
                raise TypeError("an installed integration needs an IntegrationDefinition")
            if not isinstance(entry.driver, IntegrationConnectionDriver):
                raise TypeError("an installed integration needs a connection driver")
            integration_id = entry.definition.integration_id
            if entry.driver.integration_id != integration_id:
                raise ValueError("driver and definition integration ids differ")
            if integration_id in by_id:
                raise ValueError(f"duplicate integration id: {integration_id}")
            by_id[integration_id] = entry
        ordered = sorted(by_id.values(), key=lambda e: (e.definition.category.value,
                                                        e.definition.integration_id))  # fmt: skip
        self._installed: Mapping[str, InstalledIntegration] = MappingProxyType(
            {e.definition.integration_id: e for e in ordered}
        )

    def get(self, integration_id: str) -> InstalledIntegration | None:
        return self._installed.get(integration_id)

    def definitions(self) -> tuple[IntegrationDefinition, ...]:
        """Deterministic order: category, then integration id."""
        return tuple(e.definition for e in self._installed.values())

    def drivers(self) -> tuple[IntegrationConnectionDriver, ...]:
        return tuple(e.driver for e in self._installed.values())

    @property
    def integration_ids(self) -> frozenset[str]:
        return frozenset(self._installed)

    def __len__(self) -> int:
        return len(self._installed)

    def __contains__(self, integration_id: object) -> bool:
        return integration_id in self._installed


def build_default_integration_catalog() -> IntegrationCatalog:
    """This build installs no integration: no real provider adapter exists yet."""
    return IntegrationCatalog(())
