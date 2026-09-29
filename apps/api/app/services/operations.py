"""The product-owned Operations run service contract.

The Product API depends on this narrow interface, never on Agno, integrations or
execution internals. ``OperationsAgentRunner`` implements it.

``run_product`` is READ-ONLY: implementations must run with no requested write
actions. Writes need the durable write-command boundary (``app.commands``), which
is deliberately not exposed over HTTP yet.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from app.context.models import RequestContext
from app.governance import ActionScope


class ProductOperationsRunResult(BaseModel):
    """What a product caller gets back: the assistant's final text only. It is
    untrusted display text, never an authorization or result contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str


@runtime_checkable
class OperationsRunService(Protocol):
    async def run_product(
        self, request: RequestContext, scope: ActionScope, message: str
    ) -> ProductOperationsRunResult:
        """Run one read-only Operations request for a trusted request and scope."""
        ...
