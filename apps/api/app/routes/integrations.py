"""Product integration-management API (Task 031): installed integration types and this
installation's connections to them. Connection MANAGEMENT only: no business data, no
import/sync and no external call (this build installs no provider).

Every path is FIXED (a connection id is a query parameter), so the AgentOS
authentication exemption stays a list of exact paths, never a pattern.

    GET    /api/v1/integrations/catalog                                  integrations.read
    GET    /api/v1/integrations/connections                              integrations.read
    POST   /api/v1/integrations/connections                              integrations.manage
    GET    /api/v1/integrations/connection?connection_id=                integrations.read
    PUT    /api/v1/integrations/connection?connection_id=                integrations.manage
    DELETE /api/v1/integrations/connection?connection_id=                integrations.manage
    PUT    /api/v1/integrations/connection/credentials?connection_id=    integrations.manage
    POST   /api/v1/integrations/connection/test?connection_id=           integrations.manage
    POST   /api/v1/integrations/connection/enable?connection_id=         integrations.manage
    POST   /api/v1/integrations/connection/disable?connection_id=        integrations.manage

Authentication is Product authentication (``ActorResolver``), never the AgentOS key;
permissions come only from the trusted actor. Credential values are accepted only by
create and the explicit credential replacement, go straight to the secret store, and are
never returned, logged, audited or echoed: request validation errors are reported
without submitted values (``SafeValidationRoute``).
"""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictStr,
    StringConstraints,
    model_validator,
)

from app.context import CurrentActor, CurrentRequestContext
from app.integration_management import (
    ConfigFieldKind,
    ConnectionErrorCode,
    ConnectionTestResult,
    IntegrationAuthMode,
    IntegrationCategory,
    IntegrationConnection,
    IntegrationDefinition,
)
from app.integration_management.service import (
    IntegrationAccessDeniedError,
    IntegrationConnectionNotFoundError,
    IntegrationManagementError,
    IntegrationManagementService,
    IntegrationNotConnectableError,
    IntegrationNotInstalledError,
    IntegrationOperationFailedError,
    IntegrationSecretStorageUnavailableError,
    InvalidIntegrationConfigError,
)

INTEGRATIONS_SERVICE_STATE_KEY = "integration_management_service"
INTEGRATIONS_CATALOG_PATH = "/api/v1/integrations/catalog"
INTEGRATIONS_CONNECTIONS_PATH = "/api/v1/integrations/connections"
INTEGRATIONS_CONNECTION_PATH = "/api/v1/integrations/connection"
INTEGRATIONS_CREDENTIALS_PATH = "/api/v1/integrations/connection/credentials"
INTEGRATIONS_TEST_PATH = "/api/v1/integrations/connection/test"
INTEGRATIONS_ENABLE_PATH = "/api/v1/integrations/connection/enable"
INTEGRATIONS_DISABLE_PATH = "/api/v1/integrations/connection/disable"
INTEGRATIONS_PATHS = (
    INTEGRATIONS_CATALOG_PATH,
    INTEGRATIONS_CONNECTIONS_PATH,
    INTEGRATIONS_CONNECTION_PATH,
    INTEGRATIONS_CREDENTIALS_PATH,
    INTEGRATIONS_TEST_PATH,
    INTEGRATIONS_ENABLE_PATH,
    INTEGRATIONS_DISABLE_PATH,
)
TAG = "integrations"
MAX_FIELDS = 32


class SafeValidationRoute(APIRoute):
    """422 answers that never contain submitted values (``input``/``ctx`` dropped)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                detail = [
                    {
                        "type": e.get("type"),
                        "loc": [str(p) for p in e.get("loc", ())],
                        "msg": e.get("msg"),
                    }
                    for e in error.errors()
                ]
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail}
                )

        return safe_handler


router = APIRouter(tags=[TAG], route_class=SafeValidationRoute)

# ----- models -----------------------------------------------------------------------------------

_FROZEN = ConfigDict(frozen=True, extra="forbid")
FieldKey = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
DisplayName = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
]
ConfigInput = Annotated[dict[FieldKey, StrictStr | StrictBool], Field(max_length=MAX_FIELDS)]
CredentialsInput = Annotated[dict[FieldKey, SecretStr], Field(max_length=MAX_FIELDS)]


class ConfigFieldView(BaseModel):
    model_config = _FROZEN
    name: str
    label: str
    kind: ConfigFieldKind
    required: bool
    help_text: str | None


class IntegrationDefinitionView(BaseModel):
    model_config = _FROZEN
    integration_id: str
    name: str
    category: IntegrationCategory
    description: str
    auth_mode: IntegrationAuthMode
    connectable: bool = Field(
        description="False when this build cannot connect it "
        "(e.g. delegated authorization is not supported yet)."
    )
    fields: list[ConfigFieldView]
    capabilities: list[str]

    @classmethod
    def of(cls, definition: IntegrationDefinition) -> "IntegrationDefinitionView":
        return cls(
            integration_id=definition.integration_id,
            name=definition.name,
            category=definition.category,
            description=definition.description,
            auth_mode=definition.auth_mode,
            connectable=definition.connectable,
            fields=[
                ConfigFieldView(
                    name=f.name,
                    label=f.label,
                    kind=f.kind,
                    required=f.required,
                    help_text=f.help_text,
                )
                for f in definition.fields
            ],
            capabilities=sorted(definition.capabilities),
        )


class CatalogResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    integrations: list[IntegrationDefinitionView]


class ConnectionView(BaseModel):
    model_config = _FROZEN
    connection_id: UUID
    integration_id: str
    display_name: str
    config: dict[str, str | bool] = Field(description="Non-secret configuration only.")
    configured_secret_fields: list[str] = Field(
        description="NAMES of the configured secret fields. Values are never returned."
    )
    enabled: bool
    created_at: datetime
    updated_at: datetime
    last_tested_at: datetime | None = Field(description="When the last test ran (None: never).")
    last_test_result: ConnectionTestResult = Field(
        description="Last-KNOWN connectivity at last_tested_at, not a live status."
    )
    last_test_error: ConnectionErrorCode | None

    @classmethod
    def of(cls, connection: IntegrationConnection) -> "ConnectionView":
        return cls(
            connection_id=connection.connection_id,
            integration_id=connection.integration_id,
            display_name=connection.display_name,
            config=dict(connection.config),
            configured_secret_fields=sorted(connection.secret_fields),
            enabled=connection.enabled,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
            last_tested_at=connection.last_tested_at,
            last_test_result=connection.last_test_result,
            last_test_error=connection.last_test_error,
        )


class ConnectionListResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    connections: list[ConnectionView]


class ConnectionResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    connection: ConnectionView


class ConnectionDeletedResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    connection_id: UUID
    deleted: bool


class CreateConnectionRequest(BaseModel):
    model_config = _FROZEN
    integration_id: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
    ]
    display_name: DisplayName
    config: ConfigInput = Field(default_factory=dict)
    credentials: CredentialsInput = Field(
        default_factory=dict,
        description="Secret field values. Stored only in the integration secret store; "
        "never returned.",
    )


class UpdateConnectionRequest(BaseModel):
    """Name and/or NON-secret configuration. Credentials are untouched (use the explicit
    credentials endpoint); this request cannot clear them."""

    model_config = _FROZEN
    display_name: DisplayName | None = None
    config: ConfigInput | None = None

    @model_validator(mode="after")
    def _something(self) -> "UpdateConnectionRequest":
        if self.display_name is None and self.config is None:
            raise ValueError("provide display_name and/or config")
        return self


class ReplaceCredentialsRequest(BaseModel):
    model_config = _FROZEN
    credentials: Annotated[dict[FieldKey, SecretStr], Field(min_length=1, max_length=MAX_FIELDS)]


# ----- plumbing -----------------------------------------------------------------------------------


def _service(request: Request) -> IntegrationManagementService:
    service = getattr(request.app.state, INTEGRATIONS_SERVICE_STATE_KEY, None)
    if not isinstance(service, IntegrationManagementService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integration management unavailable",
        )
    return service


def _http(error: Exception) -> HTTPException:
    if isinstance(error, IntegrationAccessDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(error, IntegrationConnectionNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=error.message)
    if isinstance(error, IntegrationNotInstalledError | IntegrationNotConnectableError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=error.message)
    if isinstance(error, InvalidIntegrationConfigError):
        detail: dict[str, str] = {"message": error.message, "code": error.code}
        if error.field is not None:
            detail["field"] = error.field
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)
    if isinstance(error, IntegrationOperationFailedError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=error.message)
    if isinstance(error, IntegrationSecretStorageUnavailableError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=error.message)
    if isinstance(error, IntegrationManagementError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=error.message)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, detail="Integration management unavailable"
    )


async def _call[T](call: Coroutine[Any, Any, T]) -> T:
    try:
        return await call
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


async def _sync[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


ConnectionId = Annotated[UUID, Query(description="The connection's UUID.")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks the required permission."},
    503: {"description": "Integration management (or its secret storage) unavailable."},
}


def _docs(permission: str, text: str) -> str:
    return f"{text}\n\nRequires the `{permission}` Product permission."


# ----- reads --------------------------------------------------------------------------------------


@router.get(
    INTEGRATIONS_CATALOG_PATH,
    response_model=CatalogResponse,
    responses=_ERRORS,
    summary="List installed integration types",
    description=_docs(
        "integrations.read",
        "The integration types installed in this "
        "Product build (definitions only, never credentials). An empty "
        "list means no integration is installed.",
    ),
)
async def get_integration_catalog(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> CatalogResponse:
    service = _service(request)
    definitions = await _sync(lambda: service.catalog(context))
    return CatalogResponse(
        request_id=context.request_id,
        integrations=[IntegrationDefinitionView.of(d) for d in definitions],
    )


@router.get(
    INTEGRATIONS_CONNECTIONS_PATH,
    response_model=ConnectionListResponse,
    responses=_ERRORS,
    summary="List integration connections",
    description=_docs(
        "integrations.read",
        "This installation's connections "
        "(metadata only: configured secret field NAMES, never values).",
    ),
)
async def list_integration_connections(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> ConnectionListResponse:
    connections = await _call(_service(request).list_connections(context))
    return ConnectionListResponse(
        request_id=context.request_id, connections=[ConnectionView.of(c) for c in connections]
    )


@router.get(
    INTEGRATIONS_CONNECTION_PATH,
    response_model=ConnectionResponse,
    responses={**_ERRORS, 404: {"description": "Unknown connection."}},
    summary="Get one integration connection",
    description=_docs("integrations.read", "One connection's metadata."),
)
async def get_integration_connection(
    connection_id: ConnectionId,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionResponse:
    connection = await _call(_service(request).get_connection(context, connection_id))
    return ConnectionResponse(
        request_id=context.request_id, connection=ConnectionView.of(connection)
    )


# ----- mutations ----------------------------------------------------------------------------------

_MUTATION_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"description": "Unknown connection."},
    409: {"description": "The operation did not complete (nothing confirmed)."},
    422: {"description": "Invalid input (submitted values are never echoed)."},
}


@router.post(
    INTEGRATIONS_CONNECTIONS_PATH,
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_MUTATION_ERRORS,
    summary="Create an integration connection",
    description=_docs(
        "integrations.manage",
        "Creates a connection to an INSTALLED "
        "integration type. Credentials go only to the secret store. "
        "Delegated-authorization integrations cannot be connected by this build.",
    ),
)
async def create_integration_connection(
    body: CreateConnectionRequest,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionResponse:
    connection = await _call(
        _service(request).create_connection(
            context, body.integration_id, body.display_name, body.config, body.credentials
        )
    )
    return ConnectionResponse(
        request_id=context.request_id, connection=ConnectionView.of(connection)
    )


@router.put(
    INTEGRATIONS_CONNECTION_PATH,
    response_model=ConnectionResponse,
    responses=_MUTATION_ERRORS,
    summary="Update a connection's name or configuration",
    description=_docs(
        "integrations.manage",
        "Updates the display name and/or the "
        "NON-secret configuration. Existing credentials are kept. A "
        "configuration change resets the last-known test state.",
    ),
)
async def update_integration_connection(
    connection_id: ConnectionId,
    body: UpdateConnectionRequest,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionResponse:
    connection = await _call(
        _service(request).update_connection(context, connection_id, body.display_name, body.config)
    )
    return ConnectionResponse(
        request_id=context.request_id, connection=ConnectionView.of(connection)
    )


@router.put(
    INTEGRATIONS_CREDENTIALS_PATH,
    response_model=ConnectionResponse,
    responses=_MUTATION_ERRORS,
    summary="Replace a connection's credentials",
    description=_docs(
        "integrations.manage",
        "Explicitly replaces the COMPLETE "
        "credential set (atomic: the previous credentials stay intact "
        "unless the replacement succeeds). Values are never returned.",
    ),
)
async def replace_integration_credentials(
    connection_id: ConnectionId,
    body: ReplaceCredentialsRequest,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionResponse:
    connection = await _call(
        _service(request).replace_credentials(context, connection_id, body.credentials)
    )
    return ConnectionResponse(
        request_id=context.request_id, connection=ConnectionView.of(connection)
    )


@router.post(
    INTEGRATIONS_TEST_PATH,
    response_model=ConnectionResponse,
    responses=_MUTATION_ERRORS,
    summary="Test a connection",
    description=_docs(
        "integrations.manage",
        "Runs the installed driver's connection "
        "test once and records the result (last-known state). A failed "
        "test is a recorded result, returned with HTTP 200.",
    ),
)
async def test_integration_connection(
    connection_id: ConnectionId,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionResponse:
    connection = await _call(_service(request).test_connection(context, connection_id))
    return ConnectionResponse(
        request_id=context.request_id, connection=ConnectionView.of(connection)
    )


@router.post(
    INTEGRATIONS_ENABLE_PATH,
    response_model=ConnectionResponse,
    responses=_MUTATION_ERRORS,
    summary="Enable a connection",
    description=_docs("integrations.manage", "Enables the connection."),
)
async def enable_integration_connection(
    connection_id: ConnectionId,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionResponse:
    connection = await _call(_service(request).set_enabled(context, connection_id, True))
    return ConnectionResponse(
        request_id=context.request_id, connection=ConnectionView.of(connection)
    )


@router.post(
    INTEGRATIONS_DISABLE_PATH,
    response_model=ConnectionResponse,
    responses=_MUTATION_ERRORS,
    summary="Disable a connection",
    description=_docs(
        "integrations.manage",
        "Disables the connection (its configuration and credentials are kept).",
    ),
)
async def disable_integration_connection(
    connection_id: ConnectionId,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionResponse:
    connection = await _call(_service(request).set_enabled(context, connection_id, False))
    return ConnectionResponse(
        request_id=context.request_id, connection=ConnectionView.of(connection)
    )


@router.delete(
    INTEGRATIONS_CONNECTION_PATH,
    response_model=ConnectionDeletedResponse,
    responses=_MUTATION_ERRORS,
    summary="Delete a connection",
    description=_docs(
        "integrations.manage", "Deletes the connection's metadata and its secret material."
    ),
)
async def delete_integration_connection(
    connection_id: ConnectionId,
    context: CurrentRequestContext,
    actor: CurrentActor,
    request: Request,
) -> ConnectionDeletedResponse:
    await _call(_service(request).delete_connection(context, connection_id))
    return ConnectionDeletedResponse(
        request_id=context.request_id, connection_id=connection_id, deleted=True
    )
