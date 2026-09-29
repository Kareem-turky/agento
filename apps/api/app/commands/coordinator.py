"""WriteCommandCoordinator: durable idempotency above governed execution.

    trusted RequestContext + ActionScope, untrusted intent + parameters, caller key
    -> no actor?                      AnonymousWriteCommandError   (no claim)
    -> invalid key?                   InvalidIdempotencyKeyError   (no claim)
    -> unknown action?                UnknownWriteActionError      (no claim)
    -> READ action?                   ReadActionNotAllowedError    (no claim)
    -> ONE detached plain-JSON snapshot of the parameters (else InvalidCommandParametersError)
    -> key hash + request fingerprint OF THE SNAPSHOT
    -> store.claim  (atomic; NEW is COMMITTED IN_PROGRESS before it returns)
       CONFLICT -> IdempotencyConflictError           nothing runs
       REPLAY   -> the stored command, replayed=True  nothing runs (IN_PROGRESS too)
       NEW      -> ExecutionCoordinator.run (govern, validate, execute, verify, audit)
                -> store.complete(terminal outcome)
                   fails -> REQUIRES_HUMAN / command_persistence_incomplete,
                            persistence_complete=False; the row stays IN_PROGRESS,
                            so a retry replays IN_PROGRESS and never re-executes.

The caller's parameter mapping is read once, into the snapshot, and never again: the
request that executes is exactly the request that was fingerprinted and claimed.

The namespace is the trusted actor's ``(company_id, actor_id)``; the fingerprint covers
the action and the trusted scope (company and store) plus the canonical parameters.

The idempotency key is not authorization: authority comes only from the trusted
request and scope, and governance still decides every NEW command. A replay returns
the original outcome even if the actor's permissions changed since; a new attempt
needs a new key. Nothing here retries, resumes or takes over an IN_PROGRESS command.
"""

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from app.commands.errors import (
    AnonymousWriteCommandError,
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    ReadActionNotAllowedError,
    UnknownWriteActionError,
    WriteCommandStoreError,
)
from app.commands.fingerprint import (
    hash_idempotency_key,
    request_fingerprint,
    snapshot_parameters,
)
from app.commands.models import (
    ACTION_RUN_STATUS_TO_COMMAND,
    ClaimOutcome,
    ClaimResult,
    CommandReason,
    CommandStatus,
    IdempotencyKey,
    WriteCommandClaim,
    WriteCommandOutcome,
    WriteCommandRecord,
    WriteCommandResult,
)
from app.commands.store import WriteCommandStore
from app.context.models import RequestContext
from app.execution import ActionRun, ExecutionCoordinator
from app.governance import ActionCatalog, ActionIntent, ActionRisk, ActionScope

_KEY = TypeAdapter(IdempotencyKey)


def _outcome_from_run(run: ActionRun) -> WriteCommandOutcome:
    execution = run.execution_result
    return WriteCommandOutcome(
        status=ACTION_RUN_STATUS_TO_COMMAND[run.status],
        reason=CommandReason(run.reason.value),
        action_run_id=run.run_id,
        execution_reference_id=execution.reference_id if execution else None,
        audit_complete=run.audit_complete,
    )


_EXECUTION_ERROR = WriteCommandOutcome(
    status=CommandStatus.REQUIRES_HUMAN, reason=CommandReason.COMMAND_EXECUTION_ERROR
)


def _result(record: WriteCommandRecord, *, replayed: bool) -> WriteCommandResult:
    return WriteCommandResult(
        command_id=record.command_id,
        action_name=record.action_name,
        status=record.status,
        reason=record.reason,
        action_run_id=record.action_run_id,
        execution_reference_id=record.execution_reference_id,
        audit_complete=record.audit_complete,
        replayed=replayed,
        persistence_complete=True,
    )


class WriteCommandCoordinator:
    def __init__(
        self,
        store: WriteCommandStore,
        executor: ExecutionCoordinator,
        catalog: ActionCatalog,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._store = store
        self._executor = executor
        self._catalog = catalog
        self._new_id = id_factory

    async def submit(
        self,
        request: RequestContext,
        scope: ActionScope,
        intent: ActionIntent,
        parameters: Mapping[str, Any],
        idempotency_key: str,
    ) -> WriteCommandResult:
        """Run a side-effect-capable action at most once per idempotency key.

        ``request`` and ``scope`` are trusted; ``intent`` and ``parameters`` are not.
        Raises a ``WriteCommandError`` subclass for rejections before the claim, for a
        conflict, and for a failed claim (in all those cases nothing was executed).
        """
        actor = request.actor
        if actor is None:
            raise AnonymousWriteCommandError()
        try:
            key = _KEY.validate_python(idempotency_key)
        except ValidationError:
            raise InvalidIdempotencyKeyError() from None
        definition = self._catalog.get(intent.name)
        if definition is None:
            raise UnknownWriteActionError()
        if definition.risk is ActionRisk.READ:
            raise ReadActionNotAllowedError()

        # The only read of the caller's parameters. Everything below uses the snapshot.
        snapshot = snapshot_parameters(parameters)
        claim = WriteCommandClaim(
            command_id=self._new_id(),
            company_id=actor.company_id,
            actor_id=actor.actor_id,
            store_id=scope.store_id,
            action_name=definition.name,
            idempotency_key_hash=hash_idempotency_key(key),
            request_fingerprint=request_fingerprint(
                action_name=definition.name,
                company_id=scope.company_id,
                store_id=scope.store_id,
                parameters=snapshot,
            ),
        )
        claimed = await self._claim(claim)

        if claimed.outcome is ClaimOutcome.CONFLICT:
            raise IdempotencyConflictError()
        record = claimed.record
        if record is None:  # guaranteed by ClaimResult; kept for type narrowing
            raise WriteCommandStoreError()
        if claimed.outcome is ClaimOutcome.REPLAY:
            return _result(record, replayed=True)
        if record.command_id != claim.command_id:
            raise WriteCommandStoreError()  # a NEW claim must be the one just made

        # The IN_PROGRESS claim is committed: only now may a side effect happen.
        outcome = await self._execute(request, scope, definition.name, snapshot)
        return await self._complete(record, outcome)

    async def _claim(self, claim: WriteCommandClaim) -> ClaimResult:
        try:
            claimed = await self._store.claim(claim)
        except WriteCommandStoreError:
            raise
        except Exception:  # noqa: BLE001 - no claim, no execution; nothing leaks
            raise WriteCommandStoreError() from None
        if not isinstance(claimed, ClaimResult):
            raise WriteCommandStoreError()
        return claimed

    async def _execute(
        self,
        request: RequestContext,
        scope: ActionScope,
        action_name: str,
        snapshot: dict[str, Any],
    ) -> WriteCommandOutcome:
        """Exactly one ExecutionCoordinator call, on the fingerprinted snapshot. Never
        retried, whatever happens."""
        try:
            run = await self._executor.run(request, ActionIntent(name=action_name), scope, snapshot)
            if not isinstance(run, ActionRun):
                raise TypeError("invalid action run")
            return _outcome_from_run(run)
        except Exception:  # noqa: BLE001 - an effect may exist: conservative, no text kept
            return _EXECUTION_ERROR

    async def _complete(
        self, claimed: WriteCommandRecord, outcome: WriteCommandOutcome
    ) -> WriteCommandResult:
        try:
            stored = await self._store.complete(claimed.command_id, outcome)
            if (
                not isinstance(stored, WriteCommandRecord)
                or stored.command_id != claimed.command_id
                or stored.status is not outcome.status
                or stored.reason is not outcome.reason
            ):
                raise WriteCommandStoreError()
        except Exception:  # noqa: BLE001 - the durable row stays IN_PROGRESS
            return WriteCommandResult(
                command_id=claimed.command_id,
                action_name=claimed.action_name,
                status=CommandStatus.REQUIRES_HUMAN,
                reason=CommandReason.COMMAND_PERSISTENCE_INCOMPLETE,
                action_run_id=outcome.action_run_id,
                execution_reference_id=outcome.execution_reference_id,
                audit_complete=outcome.audit_complete,
                replayed=False,
                persistence_complete=False,
            )
        return _result(stored, replayed=False)
