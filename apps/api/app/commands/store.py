"""The durable command store contract (persistence-independent).

Semantics every implementation must provide:

``claim`` atomically establishes exactly one of, for the namespace
``(company_id, actor_id, idempotency_key_hash)``:

- NEW: no command existed; an IN_PROGRESS row has been durably COMMITTED before
  ``claim`` returns, so execution may begin;
- REPLAY: a command exists with the same request fingerprint; it is returned as
  stored (whatever its status, IN_PROGRESS included) and must not be executed;
- CONFLICT: a command exists with a different fingerprint; nothing is returned.

Concurrent claims for one namespace must yield exactly one NEW. A read-then-insert
without a database uniqueness guarantee is not acceptable.

``complete`` records the terminal outcome of an IN_PROGRESS command, by
``command_id``, and returns the stored record. It must refuse (raise) when the
command does not exist or is no longer IN_PROGRESS.

``get`` returns the stored record or ``None``.

Any failure raises (preferably ``WriteCommandStoreError``). Stored state that cannot
be mapped safely (unknown status or reason, inconsistent fields) must raise, never be
read as success. Commands are never deleted by the product.
"""

from typing import Protocol, runtime_checkable
from uuid import UUID

from app.commands.models import (
    ClaimResult,
    WriteCommandClaim,
    WriteCommandOutcome,
    WriteCommandRecord,
)


@runtime_checkable
class WriteCommandStore(Protocol):
    async def claim(self, claim: WriteCommandClaim) -> ClaimResult: ...

    async def get(self, command_id: UUID) -> WriteCommandRecord | None: ...

    async def complete(
        self, command_id: UUID, outcome: WriteCommandOutcome
    ) -> WriteCommandRecord: ...
