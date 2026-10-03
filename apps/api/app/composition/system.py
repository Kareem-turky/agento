"""System readiness composition (Task 039), independent of the business backend.

    settings -> (database configured?)  no  -> no probe (the instance is never ready)
             -> a dedicated pool-less engine (bounded connect timeout)
             -> PostgresReadinessProbe(expected = EXPECTED_PRODUCT_SCHEMA_REVISION)

Read-only: the probe runs ``SELECT 1`` and reads ``product.alembic_version``. Nothing here
migrates, creates tables, reads business data or makes a network call other than that
bounded database check.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings

if TYPE_CHECKING:
    from app.system_operations import DatabaseReadinessProbe

RELEASE_FAILED = "system readiness resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class SystemComposition:
    probe: "DatabaseReadinessProbe | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_system_readiness(settings: Settings) -> SystemComposition:
    if settings.database_url is None:
        return SystemComposition()
    from app.persistence import PostgresReadinessProbe, create_readiness_engine
    from app.persistence.system_readiness import DEFAULT_TIMEOUT_SECONDS
    from app.system_operations import EXPECTED_PRODUCT_SCHEMA_REVISION

    engine = create_readiness_engine(str(settings.database_url),
                                     timeout_seconds=DEFAULT_TIMEOUT_SECONDS)  # fmt: skip
    released = False

    async def close() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    def discard() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            engine.sync_engine.dispose()
        except Exception:  # noqa: BLE001 - never surface driver/URL details
            raise DeploymentCompositionError(RELEASE_FAILED) from None

    probe = PostgresReadinessProbe(engine, expected_revision=EXPECTED_PRODUCT_SCHEMA_REVISION,
                                   timeout_seconds=DEFAULT_TIMEOUT_SECONDS)  # fmt: skip
    return SystemComposition(probe=probe, close=close, discard=discard)
