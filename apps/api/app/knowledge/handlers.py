"""Governed handlers of the Knowledge mutations (Task 035).

Each mutation is an ``ActionHandler`` run by ``ExecutionCoordinator`` (governance ->
validate -> execute -> verify -> audit). Company and actor come only from the trusted
``ActionExecutionContext``; raw parameters are re-validated here even though the service
validated them first. Execution results carry only safe references (a version number or
a document UUID), never content, so the audit trail holds no body, query or model JSON.
"""

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from app.execution import (
    ActionExecutionContext,
    ExecutionFailedWithoutEffect,
    ExecutionOutcomeUncertain,
    ExecutionResult,
    VerificationResult,
)
from app.execution.errors import ActionInputError
from app.knowledge import actions
from app.knowledge.chunking import TextChunk, chunk_text
from app.knowledge.contracts import (
    KnowledgeConflictError,
    KnowledgeDocumentRepository,
    KnowledgeDuplicateError,
    OperatingModelRepository,
)
from app.knowledge.documents import (
    DocumentContent,
    DocumentLifecycle,
    KnowledgeCategory,
    validate_category,
    validate_content,
)
from app.knowledge.errors import KnowledgeRepositoryError, KnowledgeValidationError
from app.knowledge.operating_context import prepare_configuration

_FROZEN = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
_VERSION_REFERENCE = re.compile(r"^(?:operating-model|[0-9a-f-]{36}):v([1-9][0-9]{0,8})$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Configuration(BaseModel):
    model_config = _FROZEN
    configuration: dict[str, Any]


class _NewDocument(BaseModel):
    model_config = _FROZEN
    document_id: UUID
    category: KnowledgeCategory
    content: DocumentContent
    chunks: tuple[TextChunk, ...]


class _NewVersion(BaseModel):
    model_config = _FROZEN
    document_id: UUID
    content: DocumentContent
    chunks: tuple[TextChunk, ...]


class _Target(BaseModel):
    model_config = _FROZEN
    document_id: UUID


def _document_id(parameters: Mapping[str, Any]) -> UUID:
    raw = parameters.get("document_id")
    try:
        return raw if isinstance(raw, UUID) else UUID(str(raw))
    except ValueError:
        raise ActionInputError("invalid knowledge input") from None


def _content(parameters: Mapping[str, Any]) -> tuple[DocumentContent, tuple[TextChunk, ...]]:
    try:
        content = validate_content(parameters.get("title"), parameters.get("content_type"),
                                   parameters.get("body"))  # fmt: skip
        return content, chunk_text(content.body)
    except KnowledgeValidationError:
        raise ActionInputError("invalid knowledge input") from None


def _version_from(result: ExecutionResult | None) -> int | None:
    if result is None or result.reference_id is None:
        return None
    match = _VERSION_REFERENCE.match(result.reference_id)
    return int(match.group(1)) if match else None


def _actor(context: ActionExecutionContext) -> str:
    if not context.actor_id:
        raise ExecutionFailedWithoutEffect()
    return context.actor_id


class PublishOperatingModelHandler:
    action_name = actions.OPERATING_MODEL_PUBLISH.name

    def __init__(self, repository: OperatingModelRepository, installed_agents: frozenset[str],
                 clock: Callable[[], datetime]) -> None:  # fmt: skip
        self._repository = repository
        self._installed = installed_agents
        self._clock = clock

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        try:
            return _Configuration.model_validate(dict(parameters))
        except ValidationError:
            raise ActionInputError("invalid knowledge input") from None

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_Configuration, validated_input)
        try:  # company identity is the trusted context's, never the payload's
            configuration, digest = prepare_configuration(
                data.configuration, context.company_id, self._installed
            )
        except KnowledgeValidationError:
            raise ExecutionFailedWithoutEffect() from None
        try:
            version = await self._repository.publish_operating_model(
                context.company_id, configuration, digest, _actor(context), self._clock()
            )
        except (KnowledgeDuplicateError, KnowledgeConflictError):
            raise ExecutionFailedWithoutEffect() from None
        except KnowledgeRepositoryError:
            raise ExecutionOutcomeUncertain() from None  # the commit may have happened
        return ExecutionResult(reference_id=f"operating-model:v{version}")

    async def verify(
        self, context: ActionExecutionContext, validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:  # fmt: skip
        data = cast(_Configuration, validated_input)
        version = _version_from(execution_result)
        if version is None:
            return VerificationResult(verified=False, reason_code="operating_model_unknown")
        try:
            _, digest = prepare_configuration(data.configuration, context.company_id,
                                              self._installed)  # fmt: skip
            stored = await self._repository.operating_model_version(context.company_id, version)
        except (KnowledgeValidationError, KnowledgeRepositoryError):
            return VerificationResult(verified=False, reason_code="operating_model_unknown")
        if stored is None or stored.content_hash != digest:
            return VerificationResult(verified=False, reason_code="operating_model_not_published")
        return VerificationResult(verified=True, reason_code="operating_model_published")


class _DocumentHandler:
    def __init__(self, repository: KnowledgeDocumentRepository,
                 clock: Callable[[], datetime]) -> None:  # fmt: skip
        self._repository = repository
        self._clock = clock

    async def _verify_version(
        self, context: ActionExecutionContext, document_id: UUID, version: int | None,
        content: DocumentContent, chunks: int,
    ) -> VerificationResult:  # fmt: skip
        if version is None:
            return VerificationResult(verified=False, reason_code="document_unknown")
        try:
            stored = await self._repository.document_version(context.company_id, document_id,
                                                             version)  # fmt: skip
            count = await self._repository.chunk_count(context.company_id, document_id, version)
        except KnowledgeRepositoryError:
            return VerificationResult(verified=False, reason_code="document_unknown")
        if stored is None or stored.content_hash != content.content_hash or count != chunks:
            return VerificationResult(verified=False, reason_code="document_not_published")
        return VerificationResult(verified=True, reason_code="document_version_published")


class CreateDocumentHandler(_DocumentHandler):
    action_name = actions.DOCUMENT_CREATE.name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        try:
            category = validate_category(parameters.get("category"))
        except KnowledgeValidationError:
            raise ActionInputError("invalid knowledge input") from None
        content, chunks = _content(parameters)
        return _NewDocument(document_id=_document_id(parameters), category=category,
                            content=content, chunks=chunks)  # fmt: skip

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_NewDocument, validated_input)
        try:
            await self._repository.create_document(
                context.company_id, data.document_id, data.category, data.content,
                data.chunks, _actor(context), self._clock())  # fmt: skip
        except (KnowledgeDuplicateError, KnowledgeConflictError):
            raise ExecutionFailedWithoutEffect() from None
        except KnowledgeRepositoryError:
            raise ExecutionOutcomeUncertain() from None
        return ExecutionResult(reference_id=f"{data.document_id}:v1")

    async def verify(
        self, context: ActionExecutionContext, validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:  # fmt: skip
        data = cast(_NewDocument, validated_input)
        version = _version_from(execution_result) if execution_result else 1
        return await self._verify_version(context, data.document_id, version, data.content,
                                          len(data.chunks))  # fmt: skip


class PublishDocumentVersionHandler(_DocumentHandler):
    action_name = actions.DOCUMENT_PUBLISH_VERSION.name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        content, chunks = _content(parameters)
        return _NewVersion(document_id=_document_id(parameters), content=content,
                           chunks=chunks)  # fmt: skip

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_NewVersion, validated_input)
        try:
            version = await self._repository.publish_document_version(
                context.company_id, data.document_id, data.content, data.chunks,
                _actor(context), self._clock())  # fmt: skip
        except (KnowledgeDuplicateError, KnowledgeConflictError):
            raise ExecutionFailedWithoutEffect() from None
        except KnowledgeRepositoryError:
            raise ExecutionOutcomeUncertain() from None
        return ExecutionResult(reference_id=f"{data.document_id}:v{version}")

    async def verify(
        self, context: ActionExecutionContext, validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:  # fmt: skip
        data = cast(_NewVersion, validated_input)
        # An uncertain outcome has no receipt: the version cannot be confirmed.
        return await self._verify_version(context, data.document_id,
                                          _version_from(execution_result), data.content,
                                          len(data.chunks))  # fmt: skip


class ArchiveDocumentHandler(_DocumentHandler):
    action_name = actions.DOCUMENT_ARCHIVE.name

    def validate(self, parameters: Mapping[str, Any]) -> BaseModel:
        return _Target(document_id=_document_id(parameters))

    async def execute(
        self, context: ActionExecutionContext, validated_input: BaseModel
    ) -> ExecutionResult:
        data = cast(_Target, validated_input)
        try:
            await self._repository.archive_document(context.company_id, data.document_id,
                                                    self._clock())  # fmt: skip
        except KnowledgeConflictError:
            raise ExecutionFailedWithoutEffect() from None
        except KnowledgeRepositoryError:
            raise ExecutionOutcomeUncertain() from None
        return ExecutionResult(reference_id=str(data.document_id))

    async def verify(
        self, context: ActionExecutionContext, validated_input: BaseModel,
        execution_result: ExecutionResult | None,
    ) -> VerificationResult:  # fmt: skip
        data = cast(_Target, validated_input)
        try:
            stored = await self._repository.get_document(context.company_id, data.document_id)
        except KnowledgeRepositoryError:
            return VerificationResult(verified=False, reason_code="document_unknown")
        if stored is None or stored.lifecycle is not DocumentLifecycle.ARCHIVED:
            return VerificationResult(verified=False, reason_code="document_not_archived")
        return VerificationResult(verified=True, reason_code="document_archived")


def build_knowledge_handlers(
    operating_models: OperatingModelRepository,
    documents: KnowledgeDocumentRepository,
    installed_agents: frozenset[str],
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> tuple[Any, ...]:
    return (
        PublishOperatingModelHandler(operating_models, installed_agents, clock),
        CreateDocumentHandler(documents, clock),
        PublishDocumentVersionHandler(documents, clock),
        ArchiveDocumentHandler(documents, clock),
    )
