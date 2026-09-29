"""Test-only in-memory WriteCommandStore (never production idempotency storage)."""

import hmac
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.commands import (
    ClaimOutcome,
    ClaimResult,
    CommandStatus,
    WriteCommandClaim,
    WriteCommandOutcome,
    WriteCommandRecord,
    WriteCommandStoreError,
)
from app.execution import ActionRun, ExecutionCoordinator

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


class InMemoryWriteCommandStore:
    def __init__(self, *, fail_claim: bool = False, fail_complete: bool = False) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}
        self.fail_claim = fail_claim
        self.fail_complete = fail_complete
        self.claims: list[WriteCommandClaim] = []
        self.completions: list[WriteCommandOutcome] = []

    @staticmethod
    def _record(row: dict[str, Any]) -> WriteCommandRecord:
        return WriteCommandRecord.model_validate(
            {
                k: v
                for k, v in row.items()
                if k not in {"idempotency_key_hash", "request_fingerprint"}
            }
        )

    async def claim(self, claim: WriteCommandClaim) -> ClaimResult:
        self.claims.append(claim)
        if self.fail_claim:
            raise ConnectionError("db down SENSITIVE")
        for row in self.rows.values():
            if (row["company_id"], row["actor_id"], row["idempotency_key_hash"]) == (
                claim.company_id, claim.actor_id, claim.idempotency_key_hash,
            ):  # fmt: skip
                if hmac.compare_digest(row["request_fingerprint"], claim.request_fingerprint):
                    return ClaimResult(outcome=ClaimOutcome.REPLAY, record=self._record(row))
                return ClaimResult(outcome=ClaimOutcome.CONFLICT)
        row = claim.model_dump() | {
            "status": CommandStatus.IN_PROGRESS, "reason": None, "action_run_id": None,
            "execution_reference_id": None, "audit_complete": None,
            "created_at": T0, "updated_at": T0,
        }  # fmt: skip
        self.rows[claim.command_id] = row
        return ClaimResult(outcome=ClaimOutcome.NEW, record=self._record(row))

    async def get(self, command_id: UUID) -> WriteCommandRecord | None:
        row = self.rows.get(command_id)
        return None if row is None else self._record(row)

    async def complete(self, command_id: UUID, outcome: WriteCommandOutcome) -> WriteCommandRecord:
        self.completions.append(outcome)
        if self.fail_complete:
            raise ConnectionError("db down SENSITIVE")
        row = self.rows.get(command_id)
        if row is None or row["status"] is not CommandStatus.IN_PROGRESS:
            raise WriteCommandStoreError()
        row.update(outcome.model_dump())
        return self._record(row)


class CountingExecutionCoordinator(ExecutionCoordinator):
    """The real ExecutionCoordinator, counting entries (optionally raising instead)."""

    def __init__(self, *args: Any, raise_error: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls = 0
        self.raise_error = raise_error

    async def run(self, *args: Any, **kwargs: Any) -> ActionRun:
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("coordinator exploded SENSITIVE-EXCEPTION-TEXT")
        return await super().run(*args, **kwargs)
