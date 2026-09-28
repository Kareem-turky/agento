"""Trusted per-run execution context handed to action handlers.

Real business handlers need the trusted actor, company, store and run identity, and
must never read them from untrusted action parameters. ``ExecutionCoordinator``
builds exactly one ``ActionExecutionContext`` per run from the trusted
``RequestContext``, the trusted ``ActionScope`` and the run id it generated, and
passes that same immutable object to ``execute`` and ``verify``. Raw parameters
never reach it.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.context.models import ActorType, Channel


class ActionExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    request_id: UUID
    action_name: str
    actor_id: str | None
    actor_type: ActorType | None
    company_id: str
    store_id: str | None
    channel: Channel
    session_id: str | None
