"""TEST-ONLY deterministic integration definitions, connection drivers and in-memory
stores. Never used by production code (the production catalog installs nothing).

The definitions are deliberately generic examples (``example-commerce`` ...): no real
provider is named, modelled or contacted. Drivers are in-process and deterministic:
- ``example-commerce`` (credentials): succeeds only with the api key ``VALID_KEY``;
- ``example-messaging`` (no credentials): succeeds unless its ``mode`` says otherwise
  ("unreachable" -> failure, "crash" -> raises with planted markers, "hang" -> never
  returns);
- ``example-marketing`` (delegated authorization): declared but not connectable.
"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from app.execution import AuditEvent
from app.integration_management import (
    ConfigField,
    ConfigFieldKind,
    ConnectionConfigError,
    ConnectionErrorCode,
    ConnectionTestOutcome,
    InstalledIntegration,
    IntegrationAuthMode,
    IntegrationCatalog,
    IntegrationCategory,
    IntegrationConnection,
    IntegrationDefinition,
)

VALID_KEY = "test-only-valid-integration-key-0001"  # noqa: S105 - test fixture
CRASH_MARKER = "TEST-DRIVER-INTERNAL-MARKER-9f3c"

COMMERCE = IntegrationDefinition(
    integration_id="example-commerce",
    name="Example Commerce (test)",
    category=IntegrationCategory.COMMERCE,
    description="Deterministic test-only definition with credentials.",
    auth_mode=IntegrationAuthMode.CREDENTIALS,
    fields=(
        ConfigField(name="store_url", label="Store URL", kind=ConfigFieldKind.URL),
        ConfigField(name="region", label="Region", kind=ConfigFieldKind.TEXT, required=False),
        ConfigField(name="sandbox", label="Sandbox", kind=ConfigFieldKind.BOOLEAN,
                    required=False),
        ConfigField(name="api_key", label="API key", kind=ConfigFieldKind.SECRET),
        ConfigField(name="api_secret", label="API secret", kind=ConfigFieldKind.SECRET,
                    required=False),
    ),
    capabilities=frozenset({"orders.read", "shipments.read"}),
)  # fmt: skip
MESSAGING = IntegrationDefinition(
    integration_id="example-messaging",
    name="Example Messaging (test)",
    category=IntegrationCategory.MESSAGING,
    description="Deterministic test-only definition without credentials.",
    auth_mode=IntegrationAuthMode.NONE,
    fields=(ConfigField(name="mode", label="Mode", kind=ConfigFieldKind.TEXT, required=False),),
    capabilities=frozenset({"messages.send"}),
)
MARKETING = IntegrationDefinition(
    integration_id="example-marketing",
    name="Example Marketing (test)",
    category=IntegrationCategory.MARKETING,
    description="Declares delegated authorization, which this build cannot connect.",
    auth_mode=IntegrationAuthMode.DELEGATED,
    capabilities=frozenset({"campaigns.read"}),
)


class FakeDriver:
    """Deterministic, in-process connection driver. Records what it was given."""

    def __init__(self, integration_id: str) -> None:
        self._id = integration_id
        self.tests: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.closed = False

    @property
    def integration_id(self) -> str:
        return self._id

    def validate_config(self, config: Mapping[str, Any]) -> None:
        if config.get("region") == "invalid":
            raise ConnectionConfigError("unsupported_region", "region")

    async def test_connection(
        self, config: Mapping[str, Any], secrets: Mapping[str, SecretStr]
    ) -> ConnectionTestOutcome:
        self.tests.append((dict(config), {k: v.get_secret_value() for k, v in secrets.items()}))
        mode = config.get("mode")
        if mode == "crash":
            raise RuntimeError(f"driver crashed: {CRASH_MARKER} {dict(config)}")
        if mode == "hang":
            await asyncio.sleep(3600)
        if mode == "unreachable":
            return ConnectionTestOutcome(succeeded=False, error=ConnectionErrorCode.UNREACHABLE)
        if self._id == COMMERCE.integration_id:
            key = secrets.get("api_key")
            if key is None or key.get_secret_value() != VALID_KEY:
                return ConnectionTestOutcome(
                    succeeded=False, error=ConnectionErrorCode.AUTHENTICATION_FAILED
                )
        return ConnectionTestOutcome(succeeded=True)

    async def aclose(self) -> None:
        self.closed = True


def fake_catalog() -> tuple[IntegrationCatalog, dict[str, FakeDriver]]:
    drivers = {d.integration_id: FakeDriver(d.integration_id)
               for d in (COMMERCE, MESSAGING, MARKETING)}  # fmt: skip
    catalog = IntegrationCatalog(
        InstalledIntegration(definition, drivers[definition.integration_id])
        for definition in (MESSAGING, COMMERCE, MARKETING)
    )
    return catalog, drivers


class InMemoryConnectionRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, IntegrationConnection] = {}

    async def insert(self, connection: IntegrationConnection) -> None:
        if connection.connection_id in self.rows:
            raise RuntimeError("duplicate")
        self.rows[connection.connection_id] = connection

    async def get(self, company_id: str, connection_id: UUID) -> IntegrationConnection | None:
        row = self.rows.get(connection_id)
        return row if row is not None and row.company_id == company_id else None

    async def list(self, company_id: str) -> tuple[IntegrationConnection, ...]:
        rows = [r for r in self.rows.values() if r.company_id == company_id]
        return tuple(sorted(rows, key=lambda r: (r.created_at, str(r.connection_id))))

    async def update(self, connection: IntegrationConnection) -> bool:
        if await self.get(connection.company_id, connection.connection_id) is None:
            return False
        self.rows[connection.connection_id] = connection
        return True

    async def delete(self, company_id: str, connection_id: UUID) -> bool:
        if await self.get(company_id, connection_id) is None:
            return False
        del self.rows[connection_id]
        return True


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self.events.append(event)


class StepClock:
    """Deterministic, strictly increasing clock."""

    def __init__(self) -> None:
        self._now = datetime(2031, 1, 2, 3, 4, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now
