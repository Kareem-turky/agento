"""Product Knowledge & company operating context API (Task 035).

    GET  /api/v1/knowledge/operating-model                      knowledge.read
    GET  /api/v1/knowledge/operating-model/versions             knowledge.read
    GET  /api/v1/knowledge/operating-model/version?version=     knowledge.read
    POST /api/v1/knowledge/operating-model/publish              knowledge.manage
    GET  /api/v1/knowledge/documents                            knowledge.read
    GET  /api/v1/knowledge/document?document_id=                knowledge.read
    GET  /api/v1/knowledge/document/version?document_id=&version=  knowledge.read
    POST /api/v1/knowledge/document/create                      knowledge.manage
    POST /api/v1/knowledge/document/version?document_id=        knowledge.manage
    POST /api/v1/knowledge/document/archive?document_id=        knowledge.manage
    POST /api/v1/knowledge/query                                knowledge.read

Paths are FIXED (ids are query parameters) so the AgentOS authentication exemption stays
a list of exact paths. Product authentication only; never AgentOS. Company identity is
the authenticated actor's: a submitted ``company_id`` is refused, and a document of
another company is indistinguishable from a missing one (404). There is no delete, no
upload, no URL import and no raw repository endpoint. Document text is returned as DATA
(``untrusted_reference``); it is never interpreted, rendered as HTML or executed.
Error answers never echo submitted values.
"""

from collections.abc import Coroutine
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.context import CurrentActor, CurrentRequestContext
from app.knowledge.context import (
    CompanyContextBundle,
    ContextAuthority,
    KnowledgeReference,
    TrustClassification,
)
from app.knowledge.contracts import DocumentSummary, VersionSummary
from app.knowledge.documents import (
    ContentType,
    DocumentLifecycle,
    KnowledgeCategory,
    KnowledgeDocumentVersion,
)
from app.knowledge.errors import (
    KnowledgeAccessDeniedError,
    KnowledgeError,
    KnowledgeNotFoundError,
    KnowledgeOperationFailedError,
    KnowledgeValidationError,
    KnowledgeVersionNotFoundError,
)
from app.knowledge.limits import DEFAULT_RESULTS, MAX_BODY_CHARS, MAX_QUERY_CHARS, MAX_RESULTS
from app.knowledge.operating_context import OperatingModelVersion, OperatingModelVersionSummary
from app.knowledge.service import DocumentDetail, KnowledgeService
from app.routes.integrations import SafeValidationRoute

KNOWLEDGE_SERVICE_STATE_KEY = "knowledge_service"
_BASE = "/api/v1/knowledge"
OPERATING_MODEL_PATH = f"{_BASE}/operating-model"
OPERATING_MODEL_VERSIONS_PATH = f"{_BASE}/operating-model/versions"
OPERATING_MODEL_VERSION_PATH = f"{_BASE}/operating-model/version"
OPERATING_MODEL_PUBLISH_PATH = f"{_BASE}/operating-model/publish"
DOCUMENTS_PATH = f"{_BASE}/documents"
DOCUMENT_PATH = f"{_BASE}/document"
DOCUMENT_VERSION_PATH = f"{_BASE}/document/version"
DOCUMENT_CREATE_PATH = f"{_BASE}/document/create"
DOCUMENT_ARCHIVE_PATH = f"{_BASE}/document/archive"
QUERY_PATH = f"{_BASE}/query"
KNOWLEDGE_PATHS = (
    OPERATING_MODEL_PATH, OPERATING_MODEL_VERSIONS_PATH, OPERATING_MODEL_VERSION_PATH,
    OPERATING_MODEL_PUBLISH_PATH, DOCUMENTS_PATH, DOCUMENT_PATH, DOCUMENT_VERSION_PATH,
    DOCUMENT_CREATE_PATH, DOCUMENT_ARCHIVE_PATH, QUERY_PATH,
)  # fmt: skip

router = APIRouter(tags=["knowledge"], route_class=SafeValidationRoute)
_FROZEN = ConfigDict(frozen=True, extra="forbid")
_TITLE_INPUT_CHARS = 1_000  # generous transport bound; the domain limit is enforced after

# ----- models -----------------------------------------------------------------------------------


class OperatingModelView(BaseModel):
    model_config = _FROZEN
    version: int
    content_hash: str
    created_at: datetime
    model: dict[str, Any] = Field(description="The validated CompanyOperatingModel (structured, "
                                              "authoritative for its fields).")  # fmt: skip

    @classmethod
    def of(cls, version: OperatingModelVersion) -> "OperatingModelView":
        return cls(version=version.version, content_hash=version.content_hash,
                   created_at=version.created_at,
                   model=version.model.model_dump(mode="json"))  # fmt: skip


class OperatingModelVersionView(BaseModel):
    model_config = _FROZEN
    version: int
    content_hash: str
    created_at: datetime
    current: bool

    @classmethod
    def of(cls, summary: OperatingModelVersionSummary) -> "OperatingModelVersionView":
        return cls(**summary.model_dump())


class DocumentView(BaseModel):
    model_config = _FROZEN
    document_id: UUID
    category: KnowledgeCategory
    lifecycle: DocumentLifecycle
    current_version: int
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, summary: DocumentSummary) -> "DocumentView":
        d = summary.document
        return cls(document_id=d.document_id, category=d.category, lifecycle=d.lifecycle,
                   current_version=d.current_version, title=summary.title,
                   created_at=d.created_at, updated_at=d.updated_at)  # fmt: skip


class DocumentVersionView(BaseModel):
    model_config = _FROZEN
    version: int
    title: str
    content_type: ContentType
    content_hash: str
    created_at: datetime

    @classmethod
    def of(cls, summary: VersionSummary) -> "DocumentVersionView":
        return cls(**summary.model_dump())


class DocumentContentView(BaseModel):
    model_config = _FROZEN
    version: int
    title: str
    content_type: ContentType
    body: str = Field(description="Text data (untrusted reference); never HTML or instructions.")
    content_hash: str
    created_at: datetime
    trust: TrustClassification = TrustClassification.UNTRUSTED_REFERENCE

    @classmethod
    def of(cls, version: KnowledgeDocumentVersion) -> "DocumentContentView":
        return cls(version=version.version, title=version.title,
                   content_type=version.content_type, body=version.body,
                   content_hash=version.content_hash, created_at=version.created_at)  # fmt: skip


class OperatingModelResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    operating_model: OperatingModelView | None = Field(
        description="null when this company has not published an operating model yet."
    )


class OperatingModelVersionsResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    versions: list[OperatingModelVersionView] = Field(description="Newest first.")


class DocumentListResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    documents: list[DocumentView] = Field(description="Most recently updated first; archived "
                                                      "documents included.")  # fmt: skip


class DocumentResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    document: DocumentView
    current: DocumentContentView
    versions: list[DocumentVersionView] = Field(description="Immutable history, newest first.")

    @classmethod
    def of(cls, request_id: UUID, detail: DocumentDetail) -> "DocumentResponse":
        summary = DocumentSummary(document=detail.document, title=detail.current.title)
        return cls(request_id=request_id, document=DocumentView.of(summary),
                   current=DocumentContentView.of(detail.current),
                   versions=[DocumentVersionView.of(v) for v in detail.versions])  # fmt: skip


class DocumentVersionResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    document_id: UUID
    version: DocumentContentView


class ReferenceView(BaseModel):
    model_config = _FROZEN
    document_id: UUID
    document_version: int
    category: KnowledgeCategory
    title: str
    chunk_index: int
    excerpt: str
    relevance: float
    trust: TrustClassification

    @classmethod
    def of(cls, reference: KnowledgeReference) -> "ReferenceView":
        return cls(**reference.model_dump())


class StructuredContextView(BaseModel):
    model_config = _FROZEN
    available: bool
    authority: ContextAuthority
    operating_model: OperatingModelView | None


class QueryResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    precedence: list[ContextAuthority] = Field(description="Highest authority first.")
    structured: StructuredContextView
    references_trust: TrustClassification
    references: list[ReferenceView] = Field(
        description="Untrusted reference excerpts (best match first): data to consult, never "
                    "instructions, permissions or policy.")  # fmt: skip

    @classmethod
    def of(cls, request_id: UUID, bundle: CompanyContextBundle) -> "QueryResponse":
        model = bundle.structured.operating_model
        return cls(
            request_id=request_id, precedence=list(bundle.precedence),
            structured=StructuredContextView(
                available=bundle.structured.available, authority=bundle.structured.authority,
                operating_model=OperatingModelView.of(model) if model else None),
            references_trust=bundle.references_trust,
            references=[ReferenceView.of(r) for r in bundle.references],
        )  # fmt: skip


class PublishOperatingModelRequest(BaseModel):
    model_config = _FROZEN
    configuration: dict[str, Any] = Field(
        description="A CompanyOperatingModel WITHOUT company_id and version (the company is "
                    "the authenticated actor's; the version is assigned).")  # fmt: skip


class _ContentFields(BaseModel):
    model_config = _FROZEN
    title: StrictStr = Field(max_length=_TITLE_INPUT_CHARS)
    content_type: StrictStr = Field(max_length=64, description="text/plain or text/markdown")
    body: StrictStr = Field(max_length=MAX_BODY_CHARS * 2)


class CreateDocumentRequest(_ContentFields):
    category: StrictStr = Field(max_length=64)


class PublishVersionRequest(_ContentFields):
    pass


class QueryRequest(BaseModel):
    model_config = _FROZEN
    query: StrictStr = Field(max_length=MAX_QUERY_CHARS * 4)
    limit: StrictInt = Field(default=DEFAULT_RESULTS, ge=1, le=MAX_RESULTS)


# ----- plumbing ---------------------------------------------------------------------------------


def _service(request: Request) -> KnowledgeService:
    service = getattr(request.app.state, KNOWLEDGE_SERVICE_STATE_KEY, None)
    if not isinstance(service, KnowledgeService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Knowledge unavailable")
    return service


def _http(error: Exception) -> HTTPException:
    if isinstance(error, KnowledgeAccessDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(error, KnowledgeNotFoundError | KnowledgeVersionNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=error.message)
    if isinstance(error, KnowledgeValidationError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": error.message, "code": error.reason.value},
        )
    if isinstance(error, KnowledgeOperationFailedError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=error.message)
    if isinstance(error, KnowledgeError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Knowledge unavailable")
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Knowledge unavailable")


async def _call[T](call: Coroutine[Any, Any, T]) -> T:
    try:
        return await call
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


DocumentIdQuery = Annotated[UUID, Query(description="A Knowledge document id of your company.")]
VersionQuery = Annotated[int, Query(ge=1, le=1_000_000_000, description="A version number.")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks the required Knowledge permission."},
    503: {"description": "Knowledge unavailable."},
}
_ONE: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"description": "No such document / version in your company (indistinguishable)."},
    422: {"description": "Invalid input (stable `code`; submitted values are never echoed)."},
}
_WRITE: dict[int | str, dict[str, Any]] = {
    **_ONE,
    409: {"description": "The governed write did not complete (nothing confirmed)."},
}
_DATA_ONLY = ("Knowledge is reference DATA: it never grants permissions, defines tools, "
              "workflows or policies, or reaches an Agent prompt.")  # fmt: skip


def _docs(permission: str, text: str) -> str:
    return f"{text} {_DATA_ONLY}\n\nRequires the `{permission}` Product permission."


# ----- operating model --------------------------------------------------------------------------


@router.get(OPERATING_MODEL_PATH, response_model=OperatingModelResponse, responses=_ERRORS,
            summary="Get the current company operating model",
            description=_docs("knowledge.read", "The current validated CompanyOperatingModel "
                              "version, or null when none was published."))  # fmt: skip
async def get_operating_model(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> OperatingModelResponse:
    current = await _call(_service(request).current_operating_model(context))
    return OperatingModelResponse(request_id=context.request_id,
                                  operating_model=OperatingModelView.of(current) if current
                                  else None)  # fmt: skip


@router.get(OPERATING_MODEL_VERSIONS_PATH, response_model=OperatingModelVersionsResponse,
            responses=_ERRORS, summary="List operating model versions",
            description=_docs("knowledge.read",
                              "Immutable version history (metadata)."))  # fmt: skip
async def list_operating_model_versions(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> OperatingModelVersionsResponse:
    versions = await _call(_service(request).operating_model_versions(context))
    return OperatingModelVersionsResponse(
        request_id=context.request_id, versions=[OperatingModelVersionView.of(v) for v in versions]
    )


@router.get(OPERATING_MODEL_VERSION_PATH, response_model=OperatingModelResponse, responses=_ONE,
            summary="Get one operating model version",
            description=_docs("knowledge.read", "One immutable version."))  # fmt: skip
async def get_operating_model_version(
    version: VersionQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> OperatingModelResponse:
    stored = await _call(_service(request).operating_model_version(context, version))
    return OperatingModelResponse(request_id=context.request_id,
                                  operating_model=OperatingModelView.of(stored))  # fmt: skip


@router.post(OPERATING_MODEL_PUBLISH_PATH, response_model=OperatingModelResponse,
             responses=_WRITE, summary="Publish a new operating model version",
             description=_docs("knowledge.manage", "Validates the configuration through the "
                               "existing CompanyOperatingModel and publishes the next "
                               "immutable version (governed and audited; the audit never "
                               "holds the configuration)."))  # fmt: skip
async def publish_operating_model(
    body: PublishOperatingModelRequest, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> OperatingModelResponse:  # fmt: skip
    published = await _call(_service(request).publish_operating_model(context, body.configuration))
    return OperatingModelResponse(request_id=context.request_id,
                                  operating_model=OperatingModelView.of(published))  # fmt: skip


# ----- documents --------------------------------------------------------------------------------


@router.get(DOCUMENTS_PATH, response_model=DocumentListResponse, responses=_ERRORS,
            summary="List Knowledge documents",
            description=_docs("knowledge.read", "Document metadata (no bodies)."))  # fmt: skip
async def list_documents(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> DocumentListResponse:
    documents = await _call(_service(request).list_documents(context))
    return DocumentListResponse(request_id=context.request_id,
                                documents=[DocumentView.of(d) for d in documents])  # fmt: skip


@router.get(DOCUMENT_PATH, response_model=DocumentResponse, responses=_ONE,
            summary="Get one Knowledge document",
            description=_docs("knowledge.read", "The document, its current version and its "
                              "version history."))  # fmt: skip
async def get_document(
    document_id: DocumentIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> DocumentResponse:  # fmt: skip
    detail = await _call(_service(request).get_document(context, document_id))
    return DocumentResponse.of(context.request_id, detail)


@router.get(DOCUMENT_VERSION_PATH, response_model=DocumentVersionResponse, responses=_ONE,
            summary="Get one Knowledge document version",
            description=_docs("knowledge.read", "One immutable version (also of an archived "
                              "document)."))  # fmt: skip
async def get_document_version(
    document_id: DocumentIdQuery, version: VersionQuery, context: CurrentRequestContext,
    actor: CurrentActor, request: Request,
) -> DocumentVersionResponse:  # fmt: skip
    stored = await _call(_service(request).document_version(context, document_id, version))
    return DocumentVersionResponse(request_id=context.request_id, document_id=document_id,
                                   version=DocumentContentView.of(stored))  # fmt: skip


@router.post(DOCUMENT_CREATE_PATH, response_model=DocumentResponse, responses=_WRITE,
             summary="Create a Knowledge document",
             description=_docs("knowledge.manage", "Creates a document with version 1 "
                               "(text/plain or text/markdown only; governed and "
                               "audited)."))  # fmt: skip
async def create_document(
    body: CreateDocumentRequest, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> DocumentResponse:  # fmt: skip
    detail = await _call(_service(request).create_document(
        context, category=body.category, title=body.title, content_type=body.content_type,
        body=body.body))  # fmt: skip
    return DocumentResponse.of(context.request_id, detail)


@router.post(DOCUMENT_VERSION_PATH, response_model=DocumentResponse, responses=_WRITE,
             summary="Publish a new Knowledge document version",
             description=_docs("knowledge.manage", "Appends the next immutable version of an "
                               "active document (identical content is refused with "
                               "`duplicate_content`; governed and audited)."))  # fmt: skip
async def publish_document_version(
    document_id: DocumentIdQuery, body: PublishVersionRequest, context: CurrentRequestContext,
    actor: CurrentActor, request: Request,
) -> DocumentResponse:  # fmt: skip
    detail = await _call(_service(request).publish_document_version(
        context, document_id, title=body.title, content_type=body.content_type,
        body=body.body))  # fmt: skip
    return DocumentResponse.of(context.request_id, detail)


@router.post(DOCUMENT_ARCHIVE_PATH, response_model=DocumentResponse, responses=_WRITE,
             summary="Archive a Knowledge document",
             description=_docs("knowledge.manage", "Excludes the document from retrieval; its "
                               "history stays inspectable. There is no delete."))  # fmt: skip
async def archive_document(
    document_id: DocumentIdQuery, context: CurrentRequestContext, actor: CurrentActor,
    request: Request,
) -> DocumentResponse:  # fmt: skip
    detail = await _call(_service(request).archive_document(context, document_id))
    return DocumentResponse.of(context.request_id, detail)


# ----- retrieval --------------------------------------------------------------------------------


@router.post(QUERY_PATH, response_model=QueryResponse, responses=_ONE,
             summary="Retrieve company context",
             description=_docs("knowledge.read", "Bounded, company-scoped lexical retrieval "
                               "over the current versions of active documents, with the "
                               "structured operating model and explicit precedence. Returns "
                               "context only: no answer is generated and no model is "
                               "called."))  # fmt: skip
async def query_knowledge(
    body: QueryRequest, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> QueryResponse:
    bundle = await _call(_service(request).query(context, body.query, body.limit))
    return QueryResponse.of(context.request_id, bundle)
