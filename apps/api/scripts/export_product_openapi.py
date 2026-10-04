# ruff: noqa: E501 - long documentation strings
"""Generate (or check) the Product-only OpenAPI contract and the generated part of the
Agento Product API reference (Task 043). Offline: no database, Redis, Docker, model,
provider credential or network.

    uv run python apps/api/scripts/export_product_openapi.py           # write
    uv run python apps/api/scripts/export_product_openapi.py --check   # fail on drift

Writes ``docs/openapi/agento-product-api-v1.json`` and the block between the
``GENERATED ENDPOINT REFERENCE`` markers in ``docs/API_REFERENCE.md``.

How it works (documentation tooling only; no runtime behavior changes):

* The REAL application factory ``app.main.create_app`` builds the app with every Product
  router, Pydantic model and ``SafeValidationRoute`` exactly as deployed. Only the AgentOS
  attachment is replaced by a no-op here, so Agno's runtime routes are never mounted and
  no Agno database is touched.
* Only routes whose handler lives in the ``app`` package are kept (never AgentOS, never
  FastAPI's own docs routes, never the Web BFF ``/api/product/*``). The two public probes
  ``/health/live`` and ``/health/ready`` keep ``include_in_schema=False`` at runtime; the
  generator documents copies of them.
* Transport semantics FastAPI cannot infer are DERIVED from the code, not listed by hand:
  Product authentication from the ``require_actor_context`` dependency (``CurrentActor``),
  ``Idempotency-Key`` from a handler that reads ``IDEMPOTENCY_KEY_HEADER``, and the
  ``SafeValidationRoute`` 422 body. ``X-Request-ID`` is set on every response by the
  outermost ``RequestContextMiddleware``.
* The only hand-written input is ``ENDPOINT_NOTES`` (Product permission + one safety
  note per operation). Its keys must equal the generated operations exactly, so it cannot
  drift silently.

Output is deterministic: sorted keys, two-space indent, UTF-8, one trailing newline.
"""

import copy
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

API_DIR = Path(__file__).resolve().parents[1]
ROOT = API_DIR.parents[1]
sys.path.insert(0, str(API_DIR))  # apps/api (the ``app`` package)

OPENAPI_PATH = ROOT / "docs" / "openapi" / "agento-product-api-v1.json"
REFERENCE_PATH = ROOT / "docs" / "API_REFERENCE.md"
BEGIN = "<!-- BEGIN GENERATED ENDPOINT REFERENCE (apps/api/scripts/export_product_openapi.py) -->"
END = "<!-- END GENERATED ENDPOINT REFERENCE -->"

SECURITY_SCHEME = "ProductApiKey"
IDEMPOTENCY_HEADER = "Idempotency-Key"
REQUEST_ID = "X-Request-ID"
BFF_PREFIX = "/api/product/"
METHODS = ("get", "put", "post", "delete", "patch")

TITLE = "Agento Product API"
DESCRIPTION = """\
The HTTP contract of the **Agento Product API** (version 1, `/api/v1/...`).

* **Product API only.** The internal Agno AgentOS runtime API that is attached to the same \
process is NOT part of this contract and is not described here. The Agento Web app's \
browser-to-server routes (`/api/product/*`) are not part of it either.
* **Server URL is deployment-specific**; no server is listed. See `docs/API_REFERENCE.md`.
* **Authentication:** `Authorization: Bearer <Product API key>` (the `ProductApiKey` \
scheme). The AgentOS `OS_SECURITY_KEY` is NOT a Product credential.
* Every response carries a server-generated `X-Request-ID` for correlation only.

Generated from the Product source by `apps/api/scripts/export_product_openapi.py`; do not \
edit by hand."""

SECURITY_DESCRIPTION = (
    "The Agento **Product API key**: send `Authorization: Bearer <Product API key>`. "
    "It identifies one Product principal (company, actor, permissions and granted "
    "stores) configured for this installation. It is NOT the AgentOS `OS_SECURITY_KEY` "
    "(the internal agent-runtime guard), which is never accepted as a Product credential."
)

IDEMPOTENCY_DESCRIPTION = (
    "Required, exactly once. An opaque client-generated key (1-128 characters) that "
    "identifies ONE write intent. Reuse the same key only to retry the SAME intent: the "
    "durable command is replayed, never executed twice. A different payload with a used "
    "key is 409. Never a business value; never returned, logged or stored in plaintext."
)

# Product permission and one important note per operation. Keys MUST equal the generated
# operations (checked below); permissions are the ones stated by the route modules.
ENDPOINT_NOTES: dict[tuple[str, str], tuple[str | None, str]] = {
    ("GET", "/health"): (
        None,
        "Public, legacy general health: status, application name, version, environment and agent-runtime status. No authentication.",
    ),
    ("GET", "/health/live"): (
        None,
        'Public liveness. Always `{"status": "alive"}`; touches no dependency and discloses nothing else.',
    ),
    ("GET", "/health/ready"): (
        None,
        'Public readiness: `{"status": "ready"}` (200) or `{"status": "not_ready"}` (503). Never a reason, version or host.',
    ),
    ("GET", "/api/v1/system/status"): (
        "system.read",
        "Read-only installation status: component states and stable not-ready reasons; never a URL, host, identifier or secret.",
    ),
    ("POST", "/api/v1/operations/runs"): (
        "orders.read, shipments.read, stores.read (per tool, in the selected store)",
        "Runs the Operations Agent READ-ONLY on one granted store: it can never write. 409 when the Operations Agent is disabled (refused before any model or tool runs).",
    ),
    ("GET", "/api/v1/operations/reports/daily"): (
        "stores.read, orders.read, shipments.read",
        "Deterministic daily report (no model). The store's own timezone decides the business day; `business_date` is optional (YYYY-MM-DD).",
    ),
    ("POST", "/api/v1/operations/tickets"): (
        "tickets.create",
        "The governed, durable ticket write (`operations.ticket.create`). Requires exactly one `Idempotency-Key`. HTTP status reports processing; only `status: verified` means the ticket was created.",
    ),
    ("GET", "/api/v1/operations/tickets/commands"): (
        None,
        "Read-only status of one durable ticket command of THIS principal in a currently granted store; anything else is the same 404. Never executes anything.",
    ),
    ("GET", "/api/v1/integrations/catalog"): (
        "integrations.read",
        "Installed integration types (this build installs none). Metadata only.",
    ),
    ("GET", "/api/v1/integrations/connections"): (
        "integrations.read",
        "This installation's connections. Credential values are never returned.",
    ),
    ("POST", "/api/v1/integrations/connections"): (
        "integrations.manage",
        "Create a connection. Credentials go straight to the secret store and are never returned or echoed. Connection management only; no data sync.",
    ),
    ("GET", "/api/v1/integrations/connection"): (
        "integrations.read",
        "One connection by `connection_id`. Credential values are never returned.",
    ),
    ("PUT", "/api/v1/integrations/connection"): (
        "integrations.manage",
        "Update a connection's display name and non-secret configuration.",
    ),
    ("DELETE", "/api/v1/integrations/connection"): (
        "integrations.manage",
        "Remove a connection and its stored credentials.",
    ),
    ("PUT", "/api/v1/integrations/connection/credentials"): (
        "integrations.manage",
        "Replace a connection's credentials explicitly. Values are never returned, logged or echoed.",
    ),
    ("POST", "/api/v1/integrations/connection/test"): (
        "integrations.manage",
        "Test a connection through its installed driver; the result is a stable code.",
    ),
    ("POST", "/api/v1/integrations/connection/enable"): (
        "integrations.manage",
        "Enable a connection.",
    ),
    ("POST", "/api/v1/integrations/connection/disable"): (
        "integrations.manage",
        "Disable a connection.",
    ),
    ("GET", "/api/v1/agents/catalog"): (
        "agents.read",
        "The Product Agents installed in this build (manifests). Not AgentOS.",
    ),
    ("GET", "/api/v1/agents"): (
        "agents.read",
        "Installed Agents with this installation's effective enable/disable state.",
    ),
    ("GET", "/api/v1/agents/agent"): ("agents.read", "One Agent by `agent_id`."),
    ("POST", "/api/v1/agents/agent/enable"): (
        "agents.manage",
        "Enable an Agent for this installation. Cannot create Agents, edit instructions or choose models/tools.",
    ),
    ("POST", "/api/v1/agents/agent/disable"): (
        "agents.manage",
        "Disable an Agent: its runs (Operations runs, Employee Chat turns) are refused before it runs.",
    ),
    ("DELETE", "/api/v1/agents/agent/configuration"): (
        "agents.manage",
        "Reset an Agent to its Product default (removes this installation's override).",
    ),
    ("GET", "/api/v1/skills/catalog"): (
        "agents.read",
        "Read-only Skill catalog (reviewed Product source). No install or run endpoint exists.",
    ),
    ("GET", "/api/v1/skills/skill"): ("agents.read", "One Skill by `skill_id`."),
    ("GET", "/api/v1/tasks/catalog"): (
        "agents.read",
        "Read-only Task catalog. There is no Task executor endpoint.",
    ),
    ("GET", "/api/v1/tasks/task"): ("agents.read", "One Task by `task_id`."),
    ("GET", "/api/v1/workflows/catalog"): (
        "workflows.read",
        "Read-only Workflow catalog. There is no public Workflow run endpoint.",
    ),
    ("GET", "/api/v1/workflows/workflow"): (
        "workflows.read",
        "One Workflow definition by `workflow_id`.",
    ),
    ("GET", "/api/v1/workflows/runs"): (
        "workflows.read",
        "This company's Workflow run history (metadata only; never inputs, checkpoints or outputs).",
    ),
    ("GET", "/api/v1/workflows/run"): (
        "workflows.read",
        "One Workflow run with Step attempts and events (metadata only).",
    ),
    ("GET", "/api/v1/knowledge/operating-model"): (
        "knowledge.read",
        "The current company operating model (versioned).",
    ),
    ("GET", "/api/v1/knowledge/operating-model/versions"): (
        "knowledge.read",
        "Operating-model version history.",
    ),
    ("GET", "/api/v1/knowledge/operating-model/version"): (
        "knowledge.read",
        "One operating-model version.",
    ),
    ("POST", "/api/v1/knowledge/operating-model/publish"): (
        "knowledge.manage",
        "Publish a new operating-model version. A submitted `company_id` is refused.",
    ),
    ("GET", "/api/v1/knowledge/documents"): (
        "knowledge.read",
        "Knowledge documents of this company.",
    ),
    ("GET", "/api/v1/knowledge/document"): (
        "knowledge.read",
        "One document; another company's document is the same 404.",
    ),
    ("GET", "/api/v1/knowledge/document/version"): (
        "knowledge.read",
        "One document version (text returned as untrusted data).",
    ),
    ("POST", "/api/v1/knowledge/document/create"): (
        "knowledge.manage",
        "Create a text document. No upload or URL import.",
    ),
    ("POST", "/api/v1/knowledge/document/version"): (
        "knowledge.manage",
        "Publish a new version of a document.",
    ),
    ("POST", "/api/v1/knowledge/document/archive"): (
        "knowledge.manage",
        "Archive a document. There is no delete.",
    ),
    ("POST", "/api/v1/knowledge/query"): (
        "knowledge.read",
        "Bounded, company-scoped Knowledge retrieval. Results are untrusted reference data.",
    ),
    ("GET", "/api/v1/approvals"): (
        "approvals.read",
        "Approval requests. There is NO create endpoint: requests come only from governance.",
    ),
    ("GET", "/api/v1/approvals/approval"): (
        "approvals.read",
        "One approval request with its append-only events.",
    ),
    ("POST", "/api/v1/approvals/approval/approve"): (
        "approvals.decide",
        "Approve a request (the requester cannot decide their own request).",
    ),
    ("POST", "/api/v1/approvals/approval/reject"): ("approvals.decide", "Reject a request."),
    ("POST", "/api/v1/approvals/approval/cancel"): (
        "approvals.cancel",
        "Cancel a pending request.",
    ),
    ("POST", "/api/v1/approvals/approval/resume-workflow"): (
        "approvals.read (the requester)",
        "Explicitly continue the Workflow paused by an approved request.",
    ),
    ("GET", "/api/v1/conversations"): (
        "conversations.read",
        "Canonical conversations (READ-ONLY). There is no ingest, webhook, send or reply endpoint.",
    ),
    ("GET", "/api/v1/conversations/conversation"): (
        "conversations.read",
        "One conversation; one of another company or an inaccessible store is the same 404.",
    ),
    ("GET", "/api/v1/conversations/messages"): (
        "conversations.read",
        "A page of a conversation's messages. Message text is untrusted external data.",
    ),
    ("GET", "/api/v1/chat/threads"): (
        None,
        "Employee Chat: the caller's own threads in one granted store (newest first, at most 50). Never calls the model.",
    ),
    ("POST", "/api/v1/chat/threads"): (
        None,
        "Employee Chat: create an empty thread in a granted store. Never calls the model.",
    ),
    ("GET", "/api/v1/chat/thread"): (
        None,
        "Employee Chat: one own thread with its turns and ticket proposals. Another actor's or company's thread is the same 404.",
    ),
    ("GET", "/api/v1/chat/turns"): (
        None,
        "Employee Chat: the turns and proposals of one own thread.",
    ),
    ("POST", "/api/v1/chat/turns"): (
        "tool permissions of the Operations Agent (orders.read, shipments.read, stores.read)",
        "Employee Chat: one idempotent turn (client `turn_id`) with the Operations Agent. Replay of a completed turn returns the stored answer without a model call. The Agent may only PROPOSE `operations.ticket.create`; a proposal turn's answer is always the Product-owned `Ticket prepared. Confirm the action to create it.` 409 when the Agent is disabled.",
    ),
    ("POST", "/api/v1/chat/ticket-proposals/confirm"): (
        "tickets.create",
        "Explicit human confirmation of a STORED proposal: send only `proposal_id` and exactly one `Idempotency-Key`, never title, description, action or store. Runs the governed ticket WriteCommand; same-key retry replays it; another key is 409.",
    ),
    ("POST", "/api/v1/chat/ticket-proposals/cancel"): (
        None,
        "Cancel a stored proposal (terminal). A confirmed proposal cannot be cancelled (409).",
    ),
}


# Statuses these routes return but do not declare in their decorators (read from the route
# code: apps/api/app/routes/operations*.py and chat.py). Documentation only; added to the
# operation when FastAPI did not already list the status.
_TICKET_WRITE_STATUSES = {
    "200": "Processed: a replay of the same Idempotency-Key, or a denied/failed business outcome (see `status`).",
    "202": "Accepted: in progress, awaiting approval, requires a human, or persistence incomplete. Not a created ticket.",
}
_CHAT = {"404": "Chat thread not found (also another actor's or company's, or a store no longer granted).",
         "503": "Employee chat unavailable."}  # fmt: skip
UNDECLARED_RESPONSES: dict[tuple[str, str], dict[str, str]] = {
    ("POST", "/api/v1/operations/runs"): {
        "403": "Forbidden: the store is not granted to the actor.",
        "409": "Operations Agent is disabled.",
        "503": "Operations service unavailable.",
    },
    ("GET", "/api/v1/operations/reports/daily"): {
        "403": "Forbidden: store not granted, or a required read permission is missing.",
        "503": "Daily operations report unavailable.",
    },
    ("POST", "/api/v1/operations/tickets"): {
        **_TICKET_WRITE_STATUSES,
        "403": "Forbidden: the store is not granted to the actor.",
        "409": "Idempotency conflict: the key was used for a different request.",
        "503": "Operations ticket service unavailable.",
    },
    ("GET", "/api/v1/operations/tickets/commands"): {
        "404": "Ticket command not found (unknown, another principal's, or a store not currently granted).",
        "503": "Operations ticket query service unavailable.",
    },
    ("GET", "/api/v1/chat/threads"): {
        "403": "Forbidden: the store is not granted to the actor.",
        "503": _CHAT["503"],
    },
    ("POST", "/api/v1/chat/threads"): {
        "403": "Forbidden: the store is not granted to the actor.",
        "503": _CHAT["503"],
    },
    ("GET", "/api/v1/chat/thread"): dict(_CHAT),
    ("GET", "/api/v1/chat/turns"): dict(_CHAT),
    ("POST", "/api/v1/chat/turns"): {
        **_CHAT,
        "200": "Replay of a completed turn (same turn_id and message): the stored answer, no model call.",
        "409": "Operations Agent is disabled, chat turn conflict (same turn_id, different message or thread), or chat turn in progress.",
    },
    ("POST", "/api/v1/chat/ticket-proposals/confirm"): {
        **_TICKET_WRITE_STATUSES,
        "201": "Ticket command processed and verified (see `ticket.status`).",
        "404": "Ticket proposal not found (also another actor's or company's, or a store no longer granted).",
        "409": "Ticket proposal was cancelled, was already confirmed with another key, or idempotency conflict.",
        "503": _CHAT["503"],
    },
    ("POST", "/api/v1/chat/ticket-proposals/cancel"): {
        "404": "Ticket proposal not found.",
        "409": "Ticket proposal was already confirmed.",
        "503": _CHAT["503"],
    },
}


def _clean_environment() -> None:
    """No environment value can reach the contract (configuration, keys, URLs)."""
    for name in list(os.environ):
        if name.startswith(("APP_", "AGNO_", "OS_", "OPENAI", "ANTHROPIC", "DATABASE", "OTEL")):
            del os.environ[name]


def build_product_app() -> Any:
    """The real Product application, without AgentOS (never mounted, no database)."""
    _clean_environment()
    from agno.os.settings import AgnoAPISettings

    import app.main as main
    from app.config import Settings

    settings = Settings(_env_file=None, environment="test")
    # A throwaway value that only satisfies the factory's validation; AgentOS is not attached.
    runtime = AgnoAPISettings(os_security_key="contract-generation-only-" + "x" * 16)
    with mock.patch.object(main, "attach_agent_os", lambda *args, **kwargs: None):
        return main.create_app(settings, runtime)


def _api_routes(routes: list[Any]) -> list[Any]:
    from fastapi.routing import APIRoute

    found: list[Any] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):  # FastAPI >= 0.141 included-router wrapper
            found.extend(_api_routes(route.original_router.routes))
    return found


def product_routes(application: Any) -> list[Any]:
    """Product API routes only: handlers in the ``app`` package; never AgentOS or BFF."""
    routes = []
    for route in _api_routes(application.routes):
        if not route.endpoint.__module__.startswith("app."):
            continue
        if route.path.startswith(BFF_PREFIX):
            raise SystemExit(f"a Web BFF path is served by the Product API: {route.path}")
        routes.append(route)
    return routes


def _depends_on(dependant: Any, target: Any) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def requires_product_auth(route: Any) -> bool:
    from app.context import require_actor_context

    return _depends_on(route.dependant, require_actor_context)


def reads_idempotency_key(route: Any) -> bool:
    return "IDEMPOTENCY_KEY_HEADER" in inspect.getsource(route.endpoint)


def operations_of(routes: list[Any]) -> list[tuple[str, str]]:
    ops = [(m, r.path) for r in routes for m in sorted(r.methods or ()) if m != "HEAD"]
    duplicates = sorted({op for op in ops if ops.count(op) > 1})
    if duplicates:
        raise SystemExit(f"duplicate Product operations: {duplicates}")
    return sorted(ops)


def build_openapi() -> dict[str, Any]:
    from fastapi.openapi.utils import get_openapi

    from app import __version__
    from app.commands import IDEMPOTENCY_KEY_PATTERN
    from app.routes.validation import SafeValidationRoute

    application = build_product_app()
    routes = product_routes(application)
    ops = operations_of(routes)
    if set(ops) != set(ENDPOINT_NOTES):
        raise SystemExit(
            "ENDPOINT_NOTES does not match the Product operations: missing "
            f"{sorted(set(ops) - set(ENDPOINT_NOTES))}, stale {sorted(set(ENDPOINT_NOTES) - set(ops))}"
        )
    documented = []
    for route in routes:
        if not route.include_in_schema:  # /health/live, /health/ready: documented copies
            route = copy.copy(route)
            route.include_in_schema = True
        documented.append(route)
    spec = get_openapi(title=TITLE, version=__version__, openapi_version="3.1.0",
                       description=DESCRIPTION, routes=documented)  # fmt: skip

    components = spec.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    components["securitySchemes"] = {
        SECURITY_SCHEME: {"type": "http", "scheme": "bearer",
                          "bearerFormat": "Agento Product API key",
                          "description": SECURITY_DESCRIPTION},
    }  # fmt: skip
    components["headers"] = {
        REQUEST_ID: {
            "description": "Server-generated request id (UUID) for correlation and support. "
            "Any client-sent X-Request-ID is ignored. Never identity, scope or authority.",
            "schema": {"type": "string", "format": "uuid"},
        }
    }
    schemas["ErrorDetail"] = {
        "title": "ErrorDetail", "type": "object", "required": ["detail"],
        "properties": {"detail": {"type": "string", "description": "A fixed message; never a submitted value."}},
    }  # fmt: skip
    schemas["SafeValidationError"] = {
        "title": "SafeValidationError", "type": "object", "required": ["detail"],
        "description": "Request validation failure. Only the structural type, location and "
        "message of each error: submitted values (`input`) and `ctx` are never returned.",
        "properties": {"detail": {"type": "array", "items": {
            "type": "object", "required": ["type", "loc", "msg"],
            "properties": {"type": {"type": "string"},
                           "loc": {"type": "array", "items": {"type": "string"}},
                           "msg": {"type": "string"}}}}},
    }  # fmt: skip
    for name, words in (("LivenessStatus", ["alive"]), ("ReadinessStatus", ["ready", "not_ready"])):
        schemas[name] = {
            "title": name, "type": "object", "required": ["status"], "additionalProperties": False,
            "properties": {"status": {"type": "string", "enum": words}},
        }  # fmt: skip

    by_op = {(m, r.path): r for r in routes for m in (r.methods or ()) if m != "HEAD"}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            route = by_op[(method.upper(), path)]
            permission, note = ENDPOINT_NOTES[(method.upper(), path)]
            text = [operation.get("description", "").strip(), note]
            text.append(f"**Product permission:** {permission}." if permission else "")
            operation["description"] = "\n\n".join(t for t in text if t)
            responses = operation["responses"]
            for code, text in UNDECLARED_RESPONSES.get((method.upper(), path), {}).items():
                if code in responses and not code.startswith("2"):
                    raise SystemExit(f"{method.upper()} {path} already declares {code}")
                responses.setdefault(code, {})["description"] = text
            if requires_product_auth(route):
                operation["security"] = [{SECURITY_SCHEME: []}]
                responses.setdefault("401", {"description": "Missing or invalid Product API key."})
            if reads_idempotency_key(route):
                operation.setdefault("parameters", []).append({
                    "name": IDEMPOTENCY_HEADER, "in": "header", "required": True,
                    "description": IDEMPOTENCY_DESCRIPTION,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 128,
                               "pattern": IDEMPOTENCY_KEY_PATTERN},
                })  # fmt: skip
                responses.setdefault("400", {"description": "Idempotency-Key missing, "
                                             "sent more than once, or invalid."})  # fmt: skip
            if "422" in responses:
                if not isinstance(route, SafeValidationRoute):
                    raise SystemExit(
                        f"{method.upper()} {path} validates without SafeValidationRoute"
                    )
                responses["422"] = {
                    "description": "Request validation failed (values are never echoed).",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SafeValidationError"}}},
                }  # fmt: skip
            if path in ("/health/live", "/health/ready"):
                name = "LivenessStatus" if path == "/health/live" else "ReadinessStatus"
                ok = {"application/json": {"schema": {"$ref": f"#/components/schemas/{name}"}}}
                responses["200"] = {"description": "Healthy.", "content": ok}
                if path == "/health/ready":
                    responses["503"] = {"description": "Not ready.", "content": ok}
            primary = next(
                (responses[c] for c in ("200", "201") if "content" in responses.get(c, {})), None
            )
            for code, response in responses.items():
                if code.startswith("2") and "content" not in response and primary is not None:
                    response["content"] = copy.deepcopy(primary["content"])
                if code != "422" and code != "200" and code != "201" and code != "202" \
                        and "content" not in response and code.startswith(("4", "5")):  # fmt: skip
                    response["content"] = {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ErrorDetail"}}
                    }
                response.setdefault("headers", {})[REQUEST_ID] = {
                    "$ref": f"#/components/headers/{REQUEST_ID}"
                }
    # FastAPI's default validation schemas (which include `input`/`ctx`) are not used.
    for unused in ("HTTPValidationError", "ValidationError"):  # in reference order
        if f'"#/components/schemas/{unused}"' in json.dumps(spec):
            raise SystemExit(f"FastAPI's {unused} (which echoes input) is still referenced")
        schemas.pop(unused, None)
    _verify(spec, ops)
    return spec


def _verify(spec: dict[str, Any], ops: list[tuple[str, str]]) -> None:
    found = sorted((m.upper(), p) for p, item in spec["paths"].items() for m in item)
    if found != ops:
        raise SystemExit(f"OpenAPI operations differ from the Product routes: {found} != {ops}")
    ids = [o["operationId"] for item in spec["paths"].values() for o in item.values()]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate operationId in the Product OpenAPI")


def render_json(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ----- Markdown endpoint reference (generated from the same OpenAPI) -------------------------


AREAS = [
    ("Health", lambda p: p.startswith("/health")),
    ("System", lambda p: p.startswith("/api/v1/system")),
    ("Operations", lambda p: p.startswith("/api/v1/operations")),
    ("Integrations", lambda p: p.startswith("/api/v1/integrations")),
    ("Agents", lambda p: p.startswith("/api/v1/agents")),
    ("Skills", lambda p: p.startswith("/api/v1/skills")),
    ("Tasks", lambda p: p.startswith("/api/v1/tasks")),
    ("Workflows", lambda p: p.startswith("/api/v1/workflows")),
    ("Knowledge", lambda p: p.startswith("/api/v1/knowledge")),
    ("Approvals", lambda p: p.startswith("/api/v1/approvals")),
    ("Conversations", lambda p: p.startswith("/api/v1/conversations")),
    ("Employee Chat", lambda p: p.startswith("/api/v1/chat")),
]


def _type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return f"[`{name}`](#schema-{name.lower()})"
    if "anyOf" in schema:
        return " \\| ".join(_type(s) for s in schema["anyOf"])
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _type(schema["allOf"][0])
    kind = schema.get("type", "any")
    if "enum" in schema:
        return " \\| ".join(f"`{v}`" if v is not None else "`null`" for v in schema["enum"])
    if "const" in schema:
        return f"`{schema['const']}`"
    if kind == "array":
        return f"array of {_type(schema.get('items', {}))}"
    if kind == "object" and "additionalProperties" in schema and isinstance(
            schema["additionalProperties"], dict):  # fmt: skip
        return f"object of {_type(schema['additionalProperties'])}"
    fmt = schema.get("format")
    return f"{kind} ({fmt})" if fmt else str(kind)


def _limits(schema: dict[str, Any]) -> str:
    parts = []
    for key, label in (("minLength", "min length"), ("maxLength", "max length"),
                       ("minimum", "min"), ("maximum", "max"), ("pattern", "pattern")):  # fmt: skip
        if key in schema:
            parts.append(f"{label} `{schema[key]}`")
    if "default" in schema:
        parts.append(f"default `{json.dumps(schema['default'])}`")
    return ", ".join(parts)


def _ref_name(content: dict[str, Any] | None) -> str | None:
    schema = (content or {}).get("application/json", {}).get("schema", {})
    return _type(schema) if schema else None


def render_markdown(spec: dict[str, Any]) -> str:
    lines = [BEGIN, "", "## 11. Endpoint reference (generated)", "",
             "Generated from the Product source with the OpenAPI artifact. Types link to the "
             "[schemas](#appendix-a-schemas-generated). `Auth: Product API key` means "
             "`Authorization: Bearer <Product API key>`.", ""]  # fmt: skip
    ops = [(p, m, o) for p, item in spec["paths"].items() for m, o in item.items()]
    for area, matches in AREAS:
        selected = sorted((p, m, o) for p, m, o in ops if matches(p))
        if not selected:
            raise SystemExit(f"no operation for area {area}")
        lines += [f"### {area}", ""]
        for path, method, op in selected:
            permission, note = ENDPOINT_NOTES[(method.upper(), path)]
            auth = "Product API key" if op.get("security") else "none (public)"
            lines += [f"#### `{method.upper()} {path}`", "", note, "",
                      f"- **Auth:** {auth}",
                      "- **Product permission:** " + (permission or (
                          "none beyond authentication" if op.get("security") else "none (public)"))]  # fmt: skip
            params = op.get("parameters", [])
            for where, label in (("query", "Query parameters"), ("header", "Required headers")):
                chosen = [p for p in params if p["in"] == where]
                if chosen:
                    lines.append(f"- **{label}:**")
                    for p in chosen:
                        req = "required" if p.get("required") else "optional"
                        limits = _limits(p.get("schema", {}))
                        lines.append(f"  - `{p['name']}`: {_type(p.get('schema', {}))}, {req}"
                                     + (f"; {limits}" if limits else ""))  # fmt: skip
            body = op.get("requestBody")
            body_type = _ref_name(body.get("content")) if body else None
            lines.append(f"- **Request body:** {body_type or 'none'}")
            responses = op["responses"]
            success = sorted(c for c in responses if c.startswith("2"))
            for code in success:
                kind = _ref_name(responses[code].get("content")) or "JSON"
                why = responses[code].get("description", "")
                why = "" if why in ("Successful Response", "") else f": {why.rstrip('.')}"
                lines.append(f"- **Success:** `{code}` {kind}{why}")
            errors = sorted(c for c in responses if not c.startswith("2"))
            if errors:
                lines.append(
                    "- **Errors:** "
                    + "; ".join(
                        f"`{c}` {responses[c].get('description', '').rstrip('.')}" for c in errors
                    )
                )
            lines.append("")
    lines += ["## Appendix A. Schemas (generated)", "",
              "Every request and response model of the Product API, from the Pydantic source. "
              "**Strict** marks a model that rejects unknown fields.", ""]  # fmt: skip
    for name, schema in sorted(spec["components"]["schemas"].items()):
        lines += [f'<a id="schema-{name.lower()}"></a>', f"#### `{name}`", ""]
        strict = schema.get("additionalProperties") is False
        if schema.get("enum"):
            lines += ["Enum: " + ", ".join(f"`{v}`" for v in schema["enum"]), ""]
            continue
        props = schema.get("properties", {})
        if not props:
            lines += [f"Type: {_type(schema)}.", ""]
            continue
        if strict:
            lines += ["Strict: unknown fields are rejected.", ""]
        required = set(schema.get("required", []))
        lines += ["| Field | Type | Required | Notes |", "| --- | --- | --- | --- |"]
        for field, sub in props.items():
            notes = "; ".join(
                x
                for x in (
                    _limits(sub),
                    (sub.get("description") or "").replace("\n", " ").replace("|", "\\|"),
                )
                if x
            )
            lines.append(
                f"| `{field}` | {_type(sub)} | {'yes' if field in required else 'no'} | {notes} |"
            )
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def merged_reference(current: str, generated: str) -> str:
    if current.count(BEGIN) != 1 or current.count(END) != 1:
        raise SystemExit(f"{REFERENCE_PATH} must contain the generated-block markers once")
    head, rest = current.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return head + generated + tail


def main(argv: list[str]) -> int:
    check = argv == ["--check"]
    if argv and not check:
        print("usage: export_product_openapi.py [--check]", file=sys.stderr)
        return 2
    spec = build_openapi()
    outputs = {
        OPENAPI_PATH: render_json(spec),
        REFERENCE_PATH: merged_reference(REFERENCE_PATH.read_text(encoding="utf-8"),
                                         render_markdown(spec)),
    }  # fmt: skip
    stale = [p for p, text in outputs.items()
             if not p.exists() or p.read_text(encoding="utf-8") != text]  # fmt: skip
    if check:
        for path in stale:
            print(f"out of date: {path.relative_to(ROOT)} (run the generator)", file=sys.stderr)
        return 1 if stale else 0
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outputs[path], encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
