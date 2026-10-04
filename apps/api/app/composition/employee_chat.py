"""Employee Chat persistence composition (Task 042), independent of the business backend.

    settings -> (database configured?)  no  -> nothing (the Chat routes answer 503)
             -> engine + sessions -> PostgresEmployeeChatRepository

Only the Product-owned chat transcript store is built here. The Operations chat runner
(the Agent) comes from the business backend composition and the ticket confirmation uses
the EXISTING ticket WriteCommand service; ``create_app`` assembles the
``EmployeeChatService`` from the three. Nothing here migrates or creates tables, reads a
secret or makes a network call.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.composition.contracts import DeploymentCompositionError
from app.config import Settings

if TYPE_CHECKING:
    from app.employee_chat.contracts import EmployeeChatRepository

RELEASE_FAILED = "employee chat resources could not be released"


async def _nothing() -> None:
    return None


@dataclass(frozen=True)
class EmployeeChatComposition:
    repository: "EmployeeChatRepository | None" = None
    close: Callable[[], Awaitable[None]] = _nothing
    discard: Callable[[], None] = lambda: None


def build_employee_chat(settings: Settings) -> EmployeeChatComposition:
    if settings.database_url is None:
        return EmployeeChatComposition()
    from app.persistence import (
        PostgresEmployeeChatRepository,
        create_product_engine,
        create_session_factory,
    )

    engine = create_product_engine(str(settings.database_url))
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

    try:
        repository = PostgresEmployeeChatRepository(create_session_factory(engine))
        return EmployeeChatComposition(repository=repository, close=close, discard=discard)
    except BaseException:
        discard()
        raise
