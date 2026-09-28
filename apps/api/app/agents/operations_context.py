"""Trusted run context for the Operations Agent.

``TrustedOperationsRunContext`` carries the trusted ``RequestContext`` (whose
``actor`` is the only actor authority) and the trusted, store-scoped
``ActionScope``. Only the product-owned ``OperationsAgentRunner`` creates it, from
application-layer inputs: never from model arguments, the user's message, or Agno
metadata, session state or ``user_id``.

It reaches the tools through Agno's native ``RunContext`` dependency injection under
``OPERATIONS_CONTEXT_KEY``. Tools accept it only if the dependency is a real
``TrustedOperationsRunContext`` instance; a dict, JSON or any other value is
rejected, so client-supplied dependencies can never become trusted context.

``requested_write_actions`` is the trusted per-run write intent: which write
actions the application boundary says were explicitly requested for THIS run
(empty by default, i.e. a read-only run). It is not actor authorization and does
not replace governance: a write needs both this run intent AND the actor's
permission (checked by GovernanceGate inside ExecutionCoordinator). The LLM
decides neither.
"""

from typing import Self

from agno.run import RunContext
from pydantic import BaseModel, ConfigDict, model_validator

from app.context.models import RequestContext
from app.governance import ActionScope
from app.operations import CREATE_TICKET_ACTION

OPERATIONS_CONTEXT_KEY = "operations_run_context"

# The write actions an Operations Agent run can be enabled for.
OPERATIONS_WRITE_ACTIONS = frozenset({CREATE_TICKET_ACTION.name})


class TrustedOperationsRunContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: RequestContext
    scope: ActionScope
    requested_write_actions: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _store_scoped(self) -> Self:
        # Operations Agent runs are store scoped; the model never chooses a store.
        if self.scope.store_id is None:
            raise ValueError("an Operations Agent run requires a store-scoped ActionScope")
        unknown = self.requested_write_actions - OPERATIONS_WRITE_ACTIONS
        if unknown:
            raise ValueError("requested_write_actions contains an unknown write action")
        return self


def trusted_context_from(run_context: object) -> TrustedOperationsRunContext | None:
    """The trusted context injected for this run, or ``None`` (callers fail closed)."""
    if not isinstance(run_context, RunContext):
        return None
    dependencies = run_context.dependencies
    if not isinstance(dependencies, dict):
        return None
    value = dependencies.get(OPERATIONS_CONTEXT_KEY)
    return value if isinstance(value, TrustedOperationsRunContext) else None
