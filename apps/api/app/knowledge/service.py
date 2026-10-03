"""``KnowledgeService``: Product Knowledge and company operating context (Task 035).

    reads      trusted RequestContext -> GovernanceGate (knowledge.read)    -> 403 / data
    mutations  trusted RequestContext -> GovernanceGate (knowledge.manage)
                 denied  -> ExecutionCoordinator (audits the denial)       -> 403
                 allowed -> validation (422, stable reason codes)
                         -> target of THIS company? (404, no existence oracle)
                         -> duplicate of the current version? (422 duplicate_content)
                         -> ExecutionCoordinator -> handler -> verify -> AUDIT
    retrieval  trusted RequestContext -> knowledge.read -> KnowledgeContextReader

Identity and company come only from the trusted ``RequestContext``; a submitted
``company_id`` is refused. Knowledge is DATA: nothing here grants a permission, builds or
runs an Agent, injects context into a prompt, calls a model or a network service.
Observability records only fixed operation names, outcomes and enum reasons.
"""

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from app.context.models import ActorContext, RequestContext
from app.execution import ActionRunStatus, ExecutionCoordinator
from app.governance import (
    ActionDefinition,
    ActionIntent,
    ActionScope,
    GovernanceGate,
    PolicyOutcome,
)
from app.knowledge import actions
from app.knowledge.chunking import chunk_text
from app.knowledge.context import CompanyContextBundle, KnowledgeContextReader, validate_query
from app.knowledge.contracts import (
    DocumentSummary,
    KnowledgeDocumentRepository,
    OperatingModelRepository,
    VersionSummary,
)
from app.knowledge.documents import (
    DocumentLifecycle,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    validate_category,
    validate_content,
)
from app.knowledge.errors import (
    KnowledgeAccessDeniedError,
    KnowledgeError,
    KnowledgeNotFoundError,
    KnowledgeOperationFailedError,
    KnowledgeRepositoryError,
    KnowledgeUnavailableError,
    KnowledgeValidationError,
    KnowledgeValidationReason,
    KnowledgeVersionNotFoundError,
)
from app.knowledge.limits import DEFAULT_RESULTS, MAX_DOCUMENTS_LISTED
from app.knowledge.operating_context import (
    OperatingModelVersion,
    OperatingModelVersionSummary,
    prepare_configuration,
)
from app.observability.contracts import (
    BusinessDetails,
    ObservationDetails,
    ObservationOutcome,
    ProductObservability,
    ProductOperation,
    observe,
)

R = KnowledgeValidationReason


class KnowledgeMutation(StrEnum):
    """Low-cardinality observability label of a governed Knowledge write."""

    OPERATING_MODEL_PUBLISH = "operating_model_publish"
    DOCUMENT_CREATE = "document_create"
    DOCUMENT_PUBLISH_VERSION = "document_publish_version"
    DOCUMENT_ARCHIVE = "document_archive"


def _outcome(error: BaseException) -> ObservationOutcome:
    if isinstance(error, KnowledgeAccessDeniedError):
        return ObservationOutcome.DENIED
    if isinstance(error, KnowledgeValidationError):
        return ObservationOutcome.INVALID
    if isinstance(error, (KnowledgeNotFoundError, KnowledgeVersionNotFoundError)):
        return ObservationOutcome.NOT_FOUND
    if isinstance(error, KnowledgeOperationFailedError):
        return ObservationOutcome.CONFLICT
    if isinstance(error, KnowledgeUnavailableError):
        return ObservationOutcome.UNAVAILABLE
    return ObservationOutcome.ERROR


def _details(status: StrEnum | None, error: BaseException | None = None) -> ObservationDetails:
    reason = error.reason if isinstance(error, KnowledgeValidationError) else None
    return ObservationDetails(business=BusinessDetails(status=status, reason=reason))


def _document_uuid(document_id: object) -> UUID:
    """A malformed id is indistinguishable from a missing document (404)."""
    try:
        return document_id if isinstance(document_id, UUID) else UUID(str(document_id))
    except ValueError:
        raise KnowledgeNotFoundError() from None


class DocumentDetail:
    """A document, its current version and its version history (read model)."""

    __slots__ = ("current", "document", "versions")

    def __init__(self, document: KnowledgeDocument, current: KnowledgeDocumentVersion,
                 versions: tuple[VersionSummary, ...]) -> None:  # fmt: skip
        self.document = document
        self.current = current
        self.versions = versions


class KnowledgeService:
    def __init__(
        self,
        operating_models: OperatingModelRepository,
        documents: KnowledgeDocumentRepository,
        reader: KnowledgeContextReader,
        gate: GovernanceGate,
        coordinator: ExecutionCoordinator,
        *,
        installed_agents: frozenset[str],
        observability: ProductObservability | None = None,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._operating_models = operating_models
        self._documents = documents
        self._reader = reader
        self._gate = gate
        self._coordinator = coordinator
        self._installed = frozenset(installed_agents)
        self._observability = observability
        self._new_id = new_id

    # ----- authorization ----------------------------------------------------------------------

    @staticmethod
    def _actor(context: RequestContext) -> ActorContext:
        if context.actor is None:
            raise KnowledgeAccessDeniedError()
        return context.actor

    def _allowed(self, actor: ActorContext, action: ActionDefinition) -> bool:
        decision = self._gate.decide(
            actor, ActionIntent(name=action.name), ActionScope(company_id=actor.company_id)
        )
        return decision.outcome is PolicyOutcome.ALLOW

    def _authorize_read(self, context: RequestContext, action: ActionDefinition) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, action):
            raise KnowledgeAccessDeniedError()
        return actor

    async def _authorize_mutation(
        self, context: RequestContext, action: ActionDefinition
    ) -> ActorContext:
        actor = self._actor(context)
        if not self._allowed(actor, action):
            # The coordinator records the denial in the audit trail, then stops.
            await self._coordinator.run(
                context, ActionIntent(name=action.name),
                ActionScope(company_id=actor.company_id), {},
            )  # fmt: skip
            raise KnowledgeAccessDeniedError()
        return actor

    async def _run(self, context: RequestContext, actor: ActorContext, action: ActionDefinition,
                   parameters: Mapping[str, Any]) -> None:  # fmt: skip
        run = await self._coordinator.run(
            context, ActionIntent(name=action.name),
            ActionScope(company_id=actor.company_id), parameters,
        )  # fmt: skip
        if run.status is ActionRunStatus.DENIED:
            raise KnowledgeAccessDeniedError()
        if run.status is not ActionRunStatus.VERIFIED:
            raise KnowledgeOperationFailedError()

    # ----- operating model --------------------------------------------------------------------

    async def current_operating_model(
        self, context: RequestContext
    ) -> OperatingModelVersion | None:
        actor = self._authorize_read(context, actions.OPERATING_MODEL_READ)
        try:
            return await self._operating_models.current_operating_model(actor.company_id)
        except KnowledgeRepositoryError:
            raise KnowledgeUnavailableError() from None

    async def operating_model_versions(
        self, context: RequestContext
    ) -> tuple[OperatingModelVersionSummary, ...]:
        actor = self._authorize_read(context, actions.OPERATING_MODEL_READ)
        try:
            return await self._operating_models.operating_model_versions(actor.company_id)
        except KnowledgeRepositoryError:
            raise KnowledgeUnavailableError() from None

    async def operating_model_version(
        self, context: RequestContext, version: int
    ) -> OperatingModelVersion:
        actor = self._authorize_read(context, actions.OPERATING_MODEL_READ)
        try:
            stored = await self._operating_models.operating_model_version(actor.company_id, version)
        except KnowledgeRepositoryError:
            raise KnowledgeUnavailableError() from None
        if stored is None:
            raise KnowledgeVersionNotFoundError()
        return stored

    async def publish_operating_model(
        self, context: RequestContext, configuration: object
    ) -> OperatingModelVersion:
        kind = KnowledgeMutation.OPERATING_MODEL_PUBLISH
        with observe(self._observability, ProductOperation.KNOWLEDGE_MUTATION,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = await self._authorize_mutation(context, actions.OPERATING_MODEL_PUBLISH)
                prepared, digest = prepare_configuration(configuration, actor.company_id,
                                                         self._installed)  # fmt: skip
                try:
                    current = await self._operating_models.current_operating_model(actor.company_id)
                except KnowledgeRepositoryError:
                    raise KnowledgeUnavailableError() from None
                if current is not None and current.content_hash == digest:
                    raise KnowledgeValidationError(R.DUPLICATE_CONTENT)
                await self._run(context, actor, actions.OPERATING_MODEL_PUBLISH,
                                {"configuration": prepared})  # fmt: skip
                try:
                    published = await self._operating_models.current_operating_model(
                        actor.company_id
                    )
                except KnowledgeRepositoryError:
                    raise KnowledgeUnavailableError() from None
                if published is None:
                    raise KnowledgeUnavailableError()
            except KnowledgeError as error:
                obs.finish(_outcome(error), _details(kind, error))
                raise
            obs.finish(ObservationOutcome.COMPLETED, _details(kind))
            return published

    # ----- documents: reads -------------------------------------------------------------------

    async def list_documents(self, context: RequestContext) -> tuple[DocumentSummary, ...]:
        actor = self._authorize_read(context, actions.DOCUMENTS_READ)
        try:
            return await self._documents.list_documents(actor.company_id, MAX_DOCUMENTS_LISTED)
        except KnowledgeRepositoryError:
            raise KnowledgeUnavailableError() from None

    async def _detail(self, company_id: str, document_id: UUID) -> DocumentDetail:
        try:
            document = await self._documents.get_document(company_id, document_id)
            if document is None:
                raise KnowledgeNotFoundError()
            current = await self._documents.document_version(company_id, document_id,
                                                             document.current_version)  # fmt: skip
            versions = await self._documents.document_versions(company_id, document_id)
        except KnowledgeRepositoryError:
            raise KnowledgeUnavailableError() from None
        if current is None:
            raise KnowledgeUnavailableError()  # an inconsistent pointer fails closed
        return DocumentDetail(document, current, versions)

    async def get_document(self, context: RequestContext, document_id: object) -> DocumentDetail:
        actor = self._authorize_read(context, actions.DOCUMENTS_READ)
        return await self._detail(actor.company_id, _document_uuid(document_id))

    async def document_version(
        self, context: RequestContext, document_id: object, version: int
    ) -> KnowledgeDocumentVersion:
        actor = self._authorize_read(context, actions.DOCUMENTS_READ)
        target = _document_uuid(document_id)
        try:
            if await self._documents.get_document(actor.company_id, target) is None:
                raise KnowledgeNotFoundError()
            stored = await self._documents.document_version(actor.company_id, target, version)
        except KnowledgeRepositoryError:
            raise KnowledgeUnavailableError() from None
        if stored is None:
            raise KnowledgeVersionNotFoundError()
        return stored

    # ----- documents: governed writes ---------------------------------------------------------

    async def _mutate(self, context: RequestContext, kind: KnowledgeMutation,
                      operation: Callable[[], Any]) -> DocumentDetail:  # fmt: skip
        with observe(self._observability, ProductOperation.KNOWLEDGE_MUTATION,
                     context.request_id) as obs:  # fmt: skip
            try:
                detail = await operation()
            except KnowledgeError as error:
                obs.finish(_outcome(error), _details(kind, error))
                raise
            obs.finish(ObservationOutcome.COMPLETED, _details(kind))
            return detail

    async def create_document(self, context: RequestContext, *, category: object, title: object,
                              content_type: object, body: object) -> DocumentDetail:  # fmt: skip
        async def operation() -> DocumentDetail:
            actor = await self._authorize_mutation(context, actions.DOCUMENT_CREATE)
            valid_category = validate_category(category)
            content = validate_content(title, content_type, body)
            chunk_text(content.body)  # TOO_MANY_CHUNKS is a 422, not a failed run
            document_id = self._new_id()
            await self._run(context, actor, actions.DOCUMENT_CREATE, {
                "document_id": str(document_id), "category": valid_category.value,
                "title": content.title, "content_type": content.content_type.value,
                "body": content.body,
            })  # fmt: skip
            return await self._detail(actor.company_id, document_id)

        return await self._mutate(context, KnowledgeMutation.DOCUMENT_CREATE, operation)

    async def publish_document_version(
        self, context: RequestContext, document_id: object, *, title: object,
        content_type: object, body: object,
    ) -> DocumentDetail:  # fmt: skip
        async def operation() -> DocumentDetail:
            actor = await self._authorize_mutation(context, actions.DOCUMENT_PUBLISH_VERSION)
            content = validate_content(title, content_type, body)
            chunk_text(content.body)
            target = _document_uuid(document_id)
            current = await self._detail(actor.company_id, target)
            if current.document.lifecycle is not DocumentLifecycle.ACTIVE:
                raise KnowledgeValidationError(R.DOCUMENT_ARCHIVED)
            if current.current.content_hash == content.content_hash:
                raise KnowledgeValidationError(R.DUPLICATE_CONTENT)
            await self._run(context, actor, actions.DOCUMENT_PUBLISH_VERSION, {
                "document_id": str(target), "title": content.title,
                "content_type": content.content_type.value, "body": content.body,
            })  # fmt: skip
            return await self._detail(actor.company_id, target)

        return await self._mutate(context, KnowledgeMutation.DOCUMENT_PUBLISH_VERSION, operation)

    async def archive_document(
        self, context: RequestContext, document_id: object
    ) -> DocumentDetail:
        async def operation() -> DocumentDetail:
            actor = await self._authorize_mutation(context, actions.DOCUMENT_ARCHIVE)
            target = _document_uuid(document_id)
            current = await self._detail(actor.company_id, target)
            if current.document.lifecycle is not DocumentLifecycle.ACTIVE:
                raise KnowledgeValidationError(R.DOCUMENT_ARCHIVED)
            await self._run(context, actor, actions.DOCUMENT_ARCHIVE, {"document_id": str(target)})
            return await self._detail(actor.company_id, target)

        return await self._mutate(context, KnowledgeMutation.DOCUMENT_ARCHIVE, operation)

    # ----- retrieval --------------------------------------------------------------------------

    async def query(self, context: RequestContext, text: object,
                    limit: object = DEFAULT_RESULTS) -> CompanyContextBundle:  # fmt: skip
        with observe(self._observability, ProductOperation.KNOWLEDGE_QUERY,
                     context.request_id) as obs:  # fmt: skip
            try:
                actor = self._authorize_read(context, actions.QUERY)
                query = validate_query(text, limit)
                try:
                    bundle = await self._reader.retrieve_context(actor.company_id, query.text,
                                                                 query.limit)  # fmt: skip
                except KnowledgeRepositoryError:
                    raise KnowledgeUnavailableError() from None
            except KnowledgeError as error:
                obs.finish(_outcome(error), _details(None, error))
                raise
            obs.finish(ObservationOutcome.COMPLETED, _details(None))
            return bundle
