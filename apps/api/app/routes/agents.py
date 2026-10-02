"""Product Agent-management API (Task 032): the Product Agents installed in this build,
their manifests and this installation's enable/disable configuration.

Every path is FIXED (the Agent id is a query parameter), so the AgentOS authentication
exemption stays a list of exact paths. These are Product routes: they never expose
AgentOS (``/agents``, ``/info``, sessions) or runtime internals, and nothing here can
create an Agent, edit instructions, choose a model or tool, or load code.

    GET    /api/v1/agents/catalog                              agents.read
    GET    /api/v1/agents                                      agents.read
    GET    /api/v1/agents/agent?agent_id=                      agents.read
    POST   /api/v1/agents/agent/enable?agent_id=               agents.manage
    POST   /api/v1/agents/agent/disable?agent_id=              agents.manage
    DELETE /api/v1/agents/agent/configuration?agent_id=        agents.manage (reset)

Authentication is Product authentication (``ActorResolver``); permissions come only from
the trusted actor, and Agent actors are never granted Agent management.
"""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.agent_management import (
    AgentAvailability,
    AgentAvailabilityReason,
    AgentCategory,
    AgentDefinition,
    AgentEffectiveState,
    AgentLifecycle,
    AgentSafetyProperty,
    AgentToolAccess,
    ConfigurationSource,
)
from app.agent_management.service import (
    AgentAccessDeniedError,
    AgentManagementError,
    AgentManagementService,
    AgentNotFoundError,
    AgentOperationFailedError,
)
from app.context import CurrentActor, CurrentRequestContext
from app.routes.integrations import SafeValidationRoute

AGENTS_SERVICE_STATE_KEY = "agent_management_service"
AGENTS_CATALOG_PATH = "/api/v1/agents/catalog"
AGENTS_PATH = "/api/v1/agents"
AGENT_PATH = "/api/v1/agents/agent"
AGENT_ENABLE_PATH = "/api/v1/agents/agent/enable"
AGENT_DISABLE_PATH = "/api/v1/agents/agent/disable"
AGENT_CONFIGURATION_PATH = "/api/v1/agents/agent/configuration"
AGENTS_PATHS = (
    AGENTS_CATALOG_PATH,
    AGENTS_PATH,
    AGENT_PATH,
    AGENT_ENABLE_PATH,
    AGENT_DISABLE_PATH,
    AGENT_CONFIGURATION_PATH,
)
TAG = "agents"

router = APIRouter(tags=[TAG], route_class=SafeValidationRoute)

_FROZEN = ConfigDict(frozen=True, extra="forbid")

# ----- models -----------------------------------------------------------------------------------


class AgentToolView(BaseModel):
    model_config = _FROZEN
    tool_id: str
    access: AgentToolAccess
    action_names: list[str] = Field(description="Governed Product actions behind the tool.")
    description: str


class AgentManifestView(BaseModel):
    """Descriptive security/runtime metadata. Not an authorization authority: every tool
    call is still decided by Product governance."""

    model_config = _FROZEN
    tools: list[AgentToolView]
    action_names: list[str]
    tool_call_limit: int
    requirements: list[str] = Field(description="Product domain capabilities required "
                                                "(never a provider).")  # fmt: skip
    safety: list[AgentSafetyProperty]


class AgentDefinitionView(BaseModel):
    model_config = _FROZEN
    agent_id: str
    name: str
    description: str
    category: AgentCategory
    lifecycle: AgentLifecycle
    default_enabled: bool
    capabilities: list[str]
    manifest: AgentManifestView

    @classmethod
    def of(cls, definition: AgentDefinition) -> "AgentDefinitionView":
        manifest = definition.manifest
        return cls(
            agent_id=definition.agent_id,
            name=definition.name,
            description=definition.description,
            category=definition.category,
            lifecycle=definition.lifecycle,
            default_enabled=definition.default_enabled,
            capabilities=sorted(definition.capabilities),
            manifest=AgentManifestView(
                tools=[AgentToolView(tool_id=t.tool_id, access=t.access,
                                     action_names=list(t.action_names),
                                     description=t.description) for t in manifest.tools],
                action_names=sorted(manifest.action_names),
                tool_call_limit=manifest.tool_call_limit,
                requirements=sorted(manifest.requirements),
                safety=sorted(manifest.safety),
            ),
        )  # fmt: skip


class AgentStateView(BaseModel):
    model_config = _FROZEN
    enabled: bool = Field(description="The configured state (override or Product default).")
    source: ConfigurationSource
    availability: AgentAvailability = Field(
        description="available: may run now; disabled: turned off by configuration; "
        "unavailable: enabled but its runtime is not composed in this deployment."
    )
    reason: AgentAvailabilityReason | None
    updated_at: datetime | None = Field(description="When the override last changed "
                                                    "(None: Product default).")  # fmt: skip

    @classmethod
    def of(cls, state: AgentEffectiveState) -> "AgentStateView":
        return cls(
            enabled=state.enabled, source=state.source, availability=state.availability,
            reason=state.reason,
            updated_at=None if state.configuration is None else state.configuration.updated_at,
        )  # fmt: skip


class AgentView(BaseModel):
    model_config = _FROZEN
    definition: AgentDefinitionView
    state: AgentStateView

    @classmethod
    def of(cls, pair: tuple[AgentDefinition, AgentEffectiveState]) -> "AgentView":
        return cls(definition=AgentDefinitionView.of(pair[0]), state=AgentStateView.of(pair[1]))


class AgentCatalogResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    agents: list[AgentDefinitionView]


class AgentListResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    agents: list[AgentView]


class ProductAgentResponse(BaseModel):
    model_config = _FROZEN
    request_id: UUID
    agent: AgentView


# ----- plumbing ---------------------------------------------------------------------------------


def _service(request: Request) -> AgentManagementService:
    service = getattr(request.app.state, AGENTS_SERVICE_STATE_KEY, None)
    if not isinstance(service, AgentManagementService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Agent management unavailable")  # fmt: skip
    return service


def _http(error: Exception) -> HTTPException:
    if isinstance(error, AgentAccessDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(error, AgentNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=error.message)
    if isinstance(error, AgentOperationFailedError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=error.message)
    if isinstance(error, AgentManagementError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=error.message)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                         detail="Agent management unavailable")  # fmt: skip


async def _call[T](call: Coroutine[Any, Any, T]) -> T:
    try:
        return await call
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


def _sync[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - mapped to fixed, value-free answers
        raise _http(error) from None


AgentIdQuery = Annotated[
    str,
    Query(description="An installed Product Agent id.", min_length=2, max_length=64,
          pattern=r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$"),
]  # fmt: skip
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid Product API key."},
    403: {"description": "The actor lacks the required permission (Agents never have it)."},
    503: {"description": "Agent management unavailable."},
}
_AGENT_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    404: {"description": "No such Product Agent is installed."},
    422: {"description": "Invalid Agent id (submitted values are never echoed)."},
}
_MUTATION_ERRORS: dict[int | str, dict[str, Any]] = {
    **_AGENT_ERRORS,
    409: {"description": "The operation did not complete (nothing confirmed)."},
}


def _docs(permission: str, text: str) -> str:
    return f"{text}\n\nRequires the `{permission}` Product permission."


# ----- reads --------------------------------------------------------------------------------------


@router.get(AGENTS_CATALOG_PATH, response_model=AgentCatalogResponse, responses=_ERRORS,
            summary="List installed Product Agents",
            description=_docs("agents.read", "The Product Agents installed in this build: "
                              "immutable definitions and manifests. Runtime infrastructure "
                              "Agents are not Product Agents and are never listed."))  # fmt: skip
async def get_agent_catalog(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> AgentCatalogResponse:
    service = _service(request)
    definitions = _sync(lambda: service.catalog(context))
    return AgentCatalogResponse(
        request_id=context.request_id, agents=[AgentDefinitionView.of(d) for d in definitions]
    )


@router.get(
    AGENTS_PATH,
    response_model=AgentListResponse,
    responses=_ERRORS,
    summary="List Product Agents with their effective state",
    description=_docs(
        "agents.read",
        "Every installed Product Agent with this "
        "installation's configuration and effective availability.",
    ),
)
async def list_agents(
    context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> AgentListResponse:
    agents = await _call(_service(request).list_agents(context))
    return AgentListResponse(
        request_id=context.request_id, agents=[AgentView.of(a) for a in agents]
    )


@router.get(AGENT_PATH, response_model=ProductAgentResponse, responses=_AGENT_ERRORS,
            summary="Get one Product Agent",
            description=_docs("agents.read", "One installed Product Agent's definition, "
                              "manifest and effective state."))  # fmt: skip
async def get_agent(
    agent_id: AgentIdQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> ProductAgentResponse:
    agent = await _call(_service(request).get_agent(context, agent_id))
    return ProductAgentResponse(request_id=context.request_id, agent=AgentView.of(agent))


# ----- mutations ----------------------------------------------------------------------------------


@router.post(AGENT_ENABLE_PATH, response_model=ProductAgentResponse, responses=_MUTATION_ERRORS,
             summary="Enable a Product Agent",
             description=_docs("agents.manage", "Stores an enabled override for this "
                               "installation (governed and audited)."))  # fmt: skip
async def enable_agent(
    agent_id: AgentIdQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> ProductAgentResponse:
    agent = await _call(_service(request).set_enabled(context, agent_id, True))
    return ProductAgentResponse(request_id=context.request_id, agent=AgentView.of(agent))


@router.post(AGENT_DISABLE_PATH, response_model=ProductAgentResponse, responses=_MUTATION_ERRORS,
             summary="Disable a Product Agent",
             description=_docs("agents.manage", "Stores a disabled override (governed and "
                               "audited). A disabled Agent's Product run boundary refuses "
                               "every run before the Agent, model or any tool runs; nothing "
                               "replaces it."))  # fmt: skip
async def disable_agent(
    agent_id: AgentIdQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> ProductAgentResponse:
    agent = await _call(_service(request).set_enabled(context, agent_id, False))
    return ProductAgentResponse(request_id=context.request_id, agent=AgentView.of(agent))


@router.delete(
    AGENT_CONFIGURATION_PATH,
    response_model=ProductAgentResponse,
    responses=_MUTATION_ERRORS,
    summary="Reset a Product Agent to its default",
    description=_docs(
        "agents.manage",
        "Removes this installation's override so "
        "the Product default applies (governed and audited).",
    ),
)
async def reset_agent_configuration(
    agent_id: AgentIdQuery, context: CurrentRequestContext, actor: CurrentActor, request: Request
) -> ProductAgentResponse:
    agent = await _call(_service(request).reset_configuration(context, agent_id))
    return ProductAgentResponse(request_id=context.request_id, agent=AgentView.of(agent))
