"""Task 027: static guards for the Operations Console (apps/web).

The runtime behaviour of the same-origin Product proxy is proven by the Frontend CI job
(apps/web/scripts/product-proxy-smoke.mjs); these tests pin the reviewed design.
"""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
PROXY = WEB / "lib" / "product-api" / "server.ts"
CLIENT = WEB / "lib" / "product-api" / "client.ts"
API_ROUTES = WEB / "app" / "api" / "product"
RUNTIME_DIRS = (WEB / "app", WEB / "lib", WEB / "components")

# BFF route file -> {exported method: the one UPSTREAM key it proxies to}.
EXPECTED_ROUTES = {
    "health/route.ts": {"GET": "health"},
    "operations/runs/route.ts": {"POST": "operationsRuns"},
    "operations/reports/daily/route.ts": {"GET": "dailyReport"},
    "operations/tickets/route.ts": {"POST": "tickets"},
    "operations/tickets/commands/route.ts": {"GET": "ticketCommands"},
    # Task 031: integration management (metadata; secrets are write-only).
    "integrations/catalog/route.ts": {"GET": "integrationsCatalog"},
    "integrations/connections/route.ts": {"GET": "integrationConnections",
                                          "POST": "integrationConnectionCreate"},
    "integrations/connection/route.ts": {"GET": "integrationConnection",
                                         "PUT": "integrationConnectionUpdate",
                                         "DELETE": "integrationConnectionDelete"},
    "integrations/connection/credentials/route.ts": {"PUT": "integrationCredentials"},
    "integrations/connection/test/route.ts": {"POST": "integrationTest"},
    "integrations/connection/enable/route.ts": {"POST": "integrationEnable"},
    "integrations/connection/disable/route.ts": {"POST": "integrationDisable"},
    # Task 032: Product Agent management (Product API only, never AgentOS /agents).
    "agent-management/catalog/route.ts": {"GET": "agentsCatalog"},
    "agent-management/agents/route.ts": {"GET": "agentsList"},
    "agent-management/agent/route.ts": {"GET": "agentDetail"},
    "agent-management/agent/enable/route.ts": {"POST": "agentEnable"},
    "agent-management/agent/disable/route.ts": {"POST": "agentDisable"},
    "agent-management/agent/configuration/route.ts": {"DELETE": "agentReset"},
    # Task 033: read-only Product Skill / Task metadata.
    "agent-management/skills/route.ts": {"GET": "skillsCatalog"},
    "agent-management/skill/route.ts": {"GET": "skillDetail"},
    "agent-management/tasks/route.ts": {"GET": "tasksCatalog"},
    "agent-management/task/route.ts": {"GET": "taskDetail"},
    # Task 034: read-only Workflow inspection (there is no run endpoint to proxy).
    "workflows/catalog/route.ts": {"GET": "workflowsCatalog"},
    "workflows/workflow/route.ts": {"GET": "workflowDetail"},
    "workflows/runs/route.ts": {"GET": "workflowRuns"},
    "workflows/run/route.ts": {"GET": "workflowRun"},
    # Task 035: Knowledge (operating-model PUBLISH is API-first and is not proxied).
    "knowledge/operating-model/route.ts": {"GET": "knowledgeOperatingModel"},
    "knowledge/operating-model/versions/route.ts": {"GET": "knowledgeOperatingModelVersions"},
    "knowledge/operating-model/version/route.ts": {"GET": "knowledgeOperatingModelVersion"},
    "knowledge/documents/route.ts": {"GET": "knowledgeDocuments"},
    "knowledge/document/route.ts": {"GET": "knowledgeDocument"},
    "knowledge/document/version/route.ts": {"GET": "knowledgeDocumentVersion",
                                            "POST": "knowledgeDocumentPublishVersion"},
    "knowledge/document/create/route.ts": {"POST": "knowledgeDocumentCreate"},
    "knowledge/document/archive/route.ts": {"POST": "knowledgeDocumentArchive"},
    "knowledge/query/route.ts": {"POST": "knowledgeQuery"},
    # Task 036: human approvals (there is NO create route).
    "approvals/route.ts": {"GET": "approvals"},
    "approvals/approval/route.ts": {"GET": "approval"},
    "approvals/approval/approve/route.ts": {"POST": "approvalApprove"},
    "approvals/approval/reject/route.ts": {"POST": "approvalReject"},
    "approvals/approval/cancel/route.ts": {"POST": "approvalCancel"},
    "approvals/approval/resume-workflow/route.ts": {"POST": "approvalResumeWorkflow"},
    # Task 037: read-only conversations (no ingest, webhook, send or reply route).
    "conversations/route.ts": {"GET": "conversations"},
    "conversations/conversation/route.ts": {"GET": "conversation"},
    "conversations/messages/route.ts": {"GET": "conversationMessages"},
    # Task 039: public minimal readiness (Web container health) and System Status.
    "health/ready/route.ts": {"GET": "healthReady"},
    "system/status/route.ts": {"GET": "systemStatus"},
}  # fmt: skip
# Server-only BFF routes the browser client never calls (Web container health check).
SERVER_ONLY_ROUTES = {"health/ready/route.ts"}
INTEGRATION_CONNECTION = "/api/v1/integrations/connection"
UPSTREAM_PATHS = {
    "health": ("GET", "/health"),
    "operationsRuns": ("POST", "/api/v1/operations/runs"),
    "dailyReport": ("GET", "/api/v1/operations/reports/daily"),
    "tickets": ("POST", "/api/v1/operations/tickets"),
    "ticketCommands": ("GET", "/api/v1/operations/tickets/commands"),
    "integrationsCatalog": ("GET", "/api/v1/integrations/catalog"),
    "integrationConnections": ("GET", "/api/v1/integrations/connections"),
    "integrationConnectionCreate": ("POST", "/api/v1/integrations/connections"),
    "integrationConnection": ("GET", INTEGRATION_CONNECTION),
    "integrationConnectionUpdate": ("PUT", INTEGRATION_CONNECTION),
    "integrationConnectionDelete": ("DELETE", INTEGRATION_CONNECTION),
    "integrationCredentials": ("PUT", f"{INTEGRATION_CONNECTION}/credentials"),
    "integrationTest": ("POST", f"{INTEGRATION_CONNECTION}/test"),
    "integrationEnable": ("POST", f"{INTEGRATION_CONNECTION}/enable"),
    "integrationDisable": ("POST", f"{INTEGRATION_CONNECTION}/disable"),
    "agentsCatalog": ("GET", "/api/v1/agents/catalog"),
    "agentsList": ("GET", "/api/v1/agents"),
    "agentDetail": ("GET", "/api/v1/agents/agent"),
    "agentEnable": ("POST", "/api/v1/agents/agent/enable"),
    "agentDisable": ("POST", "/api/v1/agents/agent/disable"),
    "agentReset": ("DELETE", "/api/v1/agents/agent/configuration"),
    "skillsCatalog": ("GET", "/api/v1/skills/catalog"),
    "skillDetail": ("GET", "/api/v1/skills/skill"),
    "tasksCatalog": ("GET", "/api/v1/tasks/catalog"),
    "taskDetail": ("GET", "/api/v1/tasks/task"),
    "workflowsCatalog": ("GET", "/api/v1/workflows/catalog"),
    "workflowDetail": ("GET", "/api/v1/workflows/workflow"),
    "workflowRuns": ("GET", "/api/v1/workflows/runs"),
    "workflowRun": ("GET", "/api/v1/workflows/run"),
    "knowledgeOperatingModel": ("GET", "/api/v1/knowledge/operating-model"),
    "knowledgeOperatingModelVersions": ("GET", "/api/v1/knowledge/operating-model/versions"),
    "knowledgeOperatingModelVersion": ("GET", "/api/v1/knowledge/operating-model/version"),
    "knowledgeDocuments": ("GET", "/api/v1/knowledge/documents"),
    "knowledgeDocument": ("GET", "/api/v1/knowledge/document"),
    "knowledgeDocumentVersion": ("GET", "/api/v1/knowledge/document/version"),
    "knowledgeDocumentPublishVersion": ("POST", "/api/v1/knowledge/document/version"),
    "knowledgeDocumentCreate": ("POST", "/api/v1/knowledge/document/create"),
    "knowledgeDocumentArchive": ("POST", "/api/v1/knowledge/document/archive"),
    "knowledgeQuery": ("POST", "/api/v1/knowledge/query"),
    "approvals": ("GET", "/api/v1/approvals"),
    "approval": ("GET", "/api/v1/approvals/approval"),
    "approvalApprove": ("POST", "/api/v1/approvals/approval/approve"),
    "approvalReject": ("POST", "/api/v1/approvals/approval/reject"),
    "approvalCancel": ("POST", "/api/v1/approvals/approval/cancel"),
    "approvalResumeWorkflow": ("POST", "/api/v1/approvals/approval/resume-workflow"),
    "conversations": ("GET", "/api/v1/conversations"),
    "conversation": ("GET", "/api/v1/conversations/conversation"),
    "conversationMessages": ("GET", "/api/v1/conversations/messages"),
    "healthReady": ("GET", "/health/ready"),
    "systemStatus": ("GET", "/api/v1/system/status"),
}


def runtime_files() -> list[Path]:
    return sorted(p for d in RUNTIME_DIRS for p in d.rglob("*") if p.suffix in (".ts", ".tsx"))


def code(path: Path) -> str:
    """Source without comments, so prohibitions explained in comments do not count."""
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in text.splitlines())


# ----- the BFF route allowlist ----------------------------------------------------------------


def test_exactly_the_statically_named_product_routes() -> None:
    files = sorted(str(p.relative_to(API_ROUTES)) for p in API_ROUTES.rglob("*") if p.is_file())
    assert files == sorted(EXPECTED_ROUTES)
    for path in API_ROUTES.rglob("*"):
        assert "[" not in path.name and "(" not in path.name, path  # no dynamic/catch-all segment
    other_api = [p for p in (WEB / "app").rglob("route.*") if API_ROUTES not in p.parents]
    assert other_api == []
    for name in ("middleware.ts", "middleware.js", "proxy.ts", "proxy.js"):
        assert not (WEB / name).exists(), name  # no request rewriting layer


def test_each_route_exports_one_method_and_one_fixed_upstream() -> None:
    for relative, methods in EXPECTED_ROUTES.items():
        source = code(API_ROUTES / relative)
        exported = re.findall(r"export (?:async )?function (\w+)", source)
        assert exported == list(methods), relative
        assert re.findall(r'proxyToProduct\(request, "(\w+)"', source) == list(methods.values())
        for method, upstream in methods.items():
            assert UPSTREAM_PATHS[upstream][0] == method, (relative, method)
        assert 'export const dynamic = "force-dynamic";' in source
        assert "fetch(" not in source and "process.env" not in source


def test_proxy_has_exactly_the_product_paths_and_no_agentos_path() -> None:
    source = code(PROXY)
    found = dict(
        re.findall(r'(\w+): \{ method: "(?:GET|POST|PUT|DELETE)", path: "([^"]+)" \}', source)
    )
    assert found == {key: path for key, (_, path) in UPSTREAM_PATHS.items()}
    for key, (method, path) in UPSTREAM_PATHS.items():
        assert f'{key}: {{ method: "{method}", path: "{path}" }}' in source
    for agentos in (
        "/agents",
        "/info",
        "/config",
        "/sessions",
        "/teams",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
        "/workflows",
        "/memories",
        "/knowledge",
    ):
        assert f'"{agentos}' not in source and f"'{agentos}" not in source, agentos
    assert source.count("fetch(") == 1
    assert "`${origin}${target.path}${search}`" in source  # fixed origin + fixed path


def test_proxy_security_controls_are_in_place() -> None:
    source = code(PROXY)
    assert source.lstrip().startswith('import "server-only";')
    for control in (
        'redirect: "manual"',
        'cache: "no-store"',
        '"Cache-Control": NO_STORE',
        "MAX_REQUEST_BYTES = 16 * 1024",
        "MAX_KNOWLEDGE_REQUEST_BYTES = 256 * 1024",
        "const limit = options.maxRequestBytes ?? MAX_REQUEST_BYTES;",
        "MAX_RESPONSE_BYTES = 1024 * 1024",
        "UPSTREAM_TIMEOUT_MS = 120_000",
        "AbortSignal.timeout(UPSTREAM_TIMEOUT_MS)",
        '{ detail: "Product API unavailable" }',
        "process.env.PRODUCT_API_ORIGIN",
    ):
        assert control in source, control
    # A fresh header set: only Accept, Authorization, Idempotency-Key and Content-Type.
    set_headers = set(re.findall(r'headers\.set\("([\w-]+)"', source))
    assert set_headers == {"Authorization", "Idempotency-Key", "Content-Type", "X-Request-ID"}
    assert 'new Headers({ Accept: "application/json" })' in source
    assert "request.headers.entries" not in source and "...request.headers" not in source
    assert "new Headers(request.headers)" not in source and "upstream.headers.entries" not in source
    assert "console." not in source


def test_origin_is_server_only() -> None:
    for path in runtime_files():
        source = code(path)
        if "PRODUCT_API_ORIGIN" in source:
            assert path == PROXY, path
        assert "NEXT_PUBLIC_" not in source, path
    for path in runtime_files():
        if path.read_text().lstrip().startswith('"use client"'):
            assert "product-api/server" not in path.read_text(), path


# ----- credentials never persist, log or leak --------------------------------------------------


def test_no_credential_persistence_apis_in_runtime_code() -> None:
    for path in runtime_files():
        source = code(path)
        for forbidden in ("localStorage", "sessionStorage", "indexedDB", "document.cookie",
                          "cookieStore", "cookies(", "serviceWorker", "caches.open",
                          "history.pushState", "history.replaceState", "location.hash",
                          "next/headers"):  # fmt: skip
            assert forbidden not in source, (path.name, forbidden)
    assert not list((WEB / "public").glob("*sw*.js")) if (WEB / "public").exists() else True


def test_no_logging_analytics_or_external_assets_in_runtime_code() -> None:
    for path in [*runtime_files(), *sorted((WEB / "app").glob("*.css"))]:
        source = code(path)
        assert not re.search(r"\bconsole\.\w+\(", source), path.name
        assert not re.search(r"https?://", source), path.name  # no CDN, font or analytics URL
        for forbidden in ("googleapis", "gtag", "posthog", "segment", "sentry", "@import",
                          "next/font"):  # fmt: skip
            assert forbidden not in source.lower(), (path.name, forbidden)


def test_no_agentos_key_or_agentos_endpoint_in_runtime_code() -> None:
    for path in runtime_files():
        source = code(path)
        assert "OS_SECURITY_KEY" not in source, path.name
        for public in ("NEXT_PUBLIC_PRODUCT_API_KEY", "NEXT_PUBLIC_OS_SECURITY_KEY"):
            assert public not in source, (path.name, public)
        for agentos in ('"/agents', '"/info', '"/sessions', '"/teams', '"/config', '"/docs',
                        '"/openapi.json', '"/redoc'):  # fmt: skip
            assert agentos not in source, (path.name, agentos)


def test_client_exposes_explicit_functions_only() -> None:
    source = code(CLIENT)
    exported = set(re.findall(r"export (?:async )?function (\w+)", source))
    assert exported == {"getHealth", "runOperations", "getDailyReport", "createTicket",
                        "getTicketCommand", "newIdempotencyKey", "looksLikeUuid",
                        "getIntegrationCatalog", "listIntegrationConnections",
                        "getIntegrationConnection", "createIntegrationConnection",
                        "updateIntegrationConnection", "replaceIntegrationCredentials",
                        "testIntegrationConnection", "setIntegrationConnectionEnabled",
                        "deleteIntegrationConnection", "getAgentCatalog", "getAgent",
                        "listAgents", "setAgentEnabled", "resetAgentConfiguration",
                        "getSkillCatalog", "getSkill", "getTaskCatalog", "getTask",
                        "getWorkflowCatalog", "getWorkflow", "listWorkflowRuns",
                        "getWorkflowRun", "getKnowledgeOperatingModel",
                        "listKnowledgeOperatingModelVersions",
                        "getKnowledgeOperatingModelVersion", "listKnowledgeDocuments",
                        "getKnowledgeDocument", "getKnowledgeDocumentVersion",
                        "createKnowledgeDocument", "publishKnowledgeDocumentVersion",
                        "archiveKnowledgeDocument", "queryKnowledge",
                        # Task 036: no create function (governance creates requests).
                        "listApprovals", "getApproval", "approveApproval", "rejectApproval",
                        "cancelApproval", "resumeApprovalWorkflow",
                        # Task 037: read-only (no send/reply/ingest function).
                        "listConversations", "getConversation",
                        "listConversationMessages",
                        # Task 038: the one session's in-memory auth observer.
                        "observeAuthOutcomes",
                        # Task 039: read-only System Status (system.read).
                        "getSystemStatus"}  # fmt: skip
    paths = set(re.findall(r'"(/api/product/[^"]*)"', source))
    browser_routes = [r for r in EXPECTED_ROUTES if r not in SERVER_ONLY_ROUTES]
    assert paths == {"/api/product/" + r.removesuffix("/route.ts") for r in browser_routes}
    assert 'credentials: "omit"' in source and 'cache: "no-store"' in source
    assert "crypto.randomUUID()" in source
    assert source.count('method: "DELETE"') == 2 and source.count('method: "PUT"') == 2
    # The key is only ever an Authorization header value, never part of a URL or body.
    assert "`Bearer ${apiKey}`" in source
    assert not re.search(r"(URLSearchParams|JSON\.stringify)\([^)]*apiKey", source)
    assert "idempotencyKey" not in "".join(re.findall(r"JSON\.stringify\([^)]*\)", source))


# ----- UI behaviour guards ------------------------------------------------------------------------


def component(name: str) -> str:
    return code(WEB / "components" / "console" / name)


SHELL = WEB / "components" / "shell"


def shell(name: str) -> str:
    return code(SHELL / name)


def test_api_key_input_is_masked_and_never_rendered_back() -> None:
    # Task 038: the ONE key form lives in the shell's session control.
    control = shell("SessionControl.tsx")
    assert 'type="password"' in control and 'setDraft("")' in control
    assert "Show" not in control  # no "show API key" control
    provider = shell("ProductSessionProvider.tsx")
    assert "useState<string | null>(null)" in provider  # memory only
    assert "setApiKey(null)" in provider and '{ type: "disconnected" }' in provider


def test_ticket_writes_are_explicit_idempotent_and_never_auto_retried() -> None:
    ticket = component("TicketPanel.tsx")
    assert "Create operational ticket" in ticket and "Retry same ticket request" in ticket
    assert "Reset ticket form" in ticket
    assert "newIdempotencyKey()" in ticket
    assert "pending.intent === intent ? pending.key" in ticket  # same intent reuses its key
    assert ticket.count("createTicket(") == 1
    assert "busyRef.current" in ticket  # no double submission
    for forbidden in ("setInterval", "setTimeout", "for (", "while ("):
        assert forbidden not in ticket, forbidden
    analysis = component("AnalysisPanel.tsx")
    assert "createTicket" not in analysis and "Read-only Operations analysis" in analysis
    assert "maxLength={MAX_MESSAGE_LENGTH}" in analysis and "MAX_MESSAGE_LENGTH = 8000" in analysis
    assert "MAX_TITLE_LENGTH = 160" in ticket and "MAX_DESCRIPTION_LENGTH = 4000" in ticket


def test_only_verified_is_presented_as_a_created_ticket() -> None:
    status = component("ticketStatus.tsx")
    success = re.findall(r'(\w+): \["[^"]+", "success"', status)
    assert success == ["verified"]
    for pending in ("in_progress", "awaiting_approval", "requires_human"):
        assert re.search(rf'{pending}: \["[^"]+", "(pending|attention)"', status), pending


def test_no_polling_or_background_refresh() -> None:
    for path in runtime_files():
        source = code(path)
        assert "setInterval" not in source and "EventSource" not in source, path.name
        assert "WebSocket" not in source, path.name


def test_report_is_presented_as_returned() -> None:
    report = component("ReportPanel.tsx")
    assert "report.findings.map(" in report
    for forbidden in (".sort(", ".filter(", ".reduce(", "severity ===", "count > 0"):
        if forbidden == "severity ===":
            # Colour only: critical vs anything else, never a recomputed severity.
            assert report.count(forbidden) == 1
            continue
        assert forbidden not in report, forbidden
    for shown in ("business_date", "timezone", "generated_at", "orders_created",
                  "shipments_shipped", "affected_orders", "order_status_counts",
                  "shipment_status_counts", "findings_total", "findings_truncated",
                  "inventory_reason", "recommended_action", "canonical_status"):  # fmt: skip
        assert shown in report, shown
    assert "Inventory was not part of this report." in report
    assert "it is not complete" in report
    assert "businessDate || undefined" in report  # blank date omitted


def test_command_not_found_stays_indistinguishable() -> None:
    ui = component("ui.tsx")
    assert '"Ticket command not found"' in ui
    command = component("CommandPanel.tsx")
    for leak in ("another actor", "another store", "does not exist", "not yours"):
        assert leak not in command.lower()
    assert "replayed" not in command and "persistence_complete" not in command


# ----- scope --------------------------------------------------------------------------------------


def test_web_dependencies_and_packaging_are_unchanged_in_scope() -> None:
    package = json.loads((WEB / "package.json").read_text())
    assert set(package["dependencies"]) == {"next", "react", "react-dom"}
    assert set(package["devDependencies"]) == {"@types/node", "@types/react", "@types/react-dom",
                                               "typescript"}  # fmt: skip
    assert package["scripts"]["smoke:proxy"] == "node scripts/product-proxy-smoke.mjs"
    assert package["scripts"]["test:session"] == "node --test scripts/session-epoch.test.mjs"
    compose = yaml.safe_load((ROOT / "deployments" / "template" / "compose.yaml").read_text())
    assert set(compose["services"]) == {"postgres", "migrate", "api", "web"}
    # The packaged BFF targets only the private API service.
    assert compose["services"]["web"]["environment"] == {"PRODUCT_API_ORIGIN": "http://api:8000"}


def test_env_example_holds_only_the_server_origin() -> None:
    lines = [line for line in (WEB / ".env.example").read_text().splitlines()
             if line and not line.startswith("#")]  # fmt: skip
    assert lines == ["PRODUCT_API_ORIGIN=http://127.0.0.1:8000"]
    root = (ROOT / ".env.example").read_text()
    assert "NEXT_PUBLIC_" not in "\n".join(
        line for line in root.splitlines() if not line.startswith("#")
    )


def test_placeholder_replaced_and_metadata_neutral() -> None:
    layout = (WEB / "app" / "layout.tsx").read_text()
    assert "placeholder" not in layout.lower() and 'default: "Agento"' in layout
    assert 'description: "AI operating layer for commerce operations."' in layout
    assert 'import "./globals.css";' in layout
    page = (WEB / "app" / "page.tsx").read_text()
    assert "placeholder" not in page.lower() and "<OverviewPage />" in page
    operations = (WEB / "app" / "operations" / "page.tsx").read_text()
    assert "<Console initialTab={tabFrom(tab)} />" in operations


def test_frontend_ci_runs_the_proxy_smoke_test() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert [job["name"] for job in workflow["jobs"].values()] == [
        "Backend (Python)",
        "Frontend (Next.js)",
        "Infrastructure (Docker Compose)",
    ]
    commands = [step.get("run") for step in workflow["jobs"]["frontend"]["steps"]]
    assert commands[-5:] == ["npm ci", "npm run typecheck", "npm run build",
                             "npm run test:session", "npm run smoke:proxy"]  # fmt: skip


def test_smoke_test_is_offline_and_covers_the_contract() -> None:
    script = (WEB / "scripts" / "product-proxy-smoke.mjs").read_text()
    imports = re.findall(r'from "([^"]+)"', script)
    assert all(name.startswith("node:") for name in imports), imports
    assert '"127.0.0.1"' in script and "localhost" not in script
    for check in ("browser Cookie not forwarded", "browser X-Request-ID not forwarded",
                  "browser Host not forwarded", "upstream Set-Cookie not forwarded",
                  "Location not forwarded", "redirect: not followed", "fixed 502",
                  "oversized request: 413", "not proxied: ", '"/api/product/agents"',
                  "Product X-Request-ID preserved", "no-store", "query rejected",
                  "server logs contain no key", "rendered HTML has no key",
                  "client bundles contain no server origin"):  # fmt: skip
        assert check in script, check


# ----- session-context isolation (review fix) ---------------------------------------------------


PANELS = ("AnalysisPanel.tsx", "ReportPanel.tsx", "TicketPanel.tsx", "CommandPanel.tsx")


def test_panels_are_keyed_by_the_integer_session_epoch_never_the_key() -> None:
    console = component("Console.tsx")
    assert '<div className="page-layout__main" key={operationsEpoch}>' in console
    assert not re.search(r"key=\{[^}]*apiKey", console)
    assert "generation" not in console  # the single epoch replaced the disconnect counter
    session = shell("session.ts")
    assert "apiKey" not in session
    assert "sessionEpoch: number;" in session and "operationsEpoch: number;" in session
    for forbidden in ("localStorage", "sessionStorage", "indexedDB", "cookie", "sha", "digest"):
        assert forbidden not in session.lower(), forbidden


def test_key_store_and_disconnect_all_start_a_new_epoch() -> None:
    session = shell("session.ts")
    for action in ('case "keySet":', 'case "storeChanged":', 'case "disconnected":'):
        branch = session.split(action, 1)[1].split("case ", 1)[0]
        assert "operationsEpoch: state.operationsEpoch + 1" in branch, action
        assert "recentCommandId: null" in branch, action
    for action in ('case "keySet":', 'case "disconnected":'):
        branch = session.split(action, 1)[1].split("case ", 1)[0]
        assert "sessionEpoch: state.sessionEpoch + 1" in branch, action
    # A store change remounts Operations only; the store is never authorization.
    store = session.split('case "storeChanged":', 1)[1].split("case ", 1)[0]
    assert "sessionEpoch" not in store and "keyStatus" not in store
    provider = shell("ProductSessionProvider.tsx")
    assert 'dispatch({ type: "keySet" })' in provider
    assert 'dispatch({ type: "storeChanged", storeId })' in provider
    console = component("Console.tsx")
    assert "onChange={(event) => changeStore(event.target.value.trim())}" in console


def test_stale_completions_are_ignored_by_epoch() -> None:
    session = shell("session.ts")
    auth = session.split('case "authResult":', 1)[1].split("case ", 1)[0]
    assert "action.sessionEpoch !== state.sessionEpoch" in auth
    command = session.split('case "commandCreated":', 1)[1].split("case ", 1)[0]
    assert "action.operationsEpoch !== state.operationsEpoch" in command
    for panel in PANELS:
        source = component(panel)
        assert "epoch: number;" in source, panel
        assert "onAuthResult(epoch, response);" in source, panel
        assert "onAuthResult(response)" not in source, panel
    ticket = component("TicketPanel.tsx")
    assert "onCommand(epoch, response.data.command_id);" in ticket
    assert ticket.count("createTicket(") == 1  # a context change never resubmits


def test_session_reducer_tests_exist_and_run_offline() -> None:
    script = (WEB / "scripts" / "session-epoch.test.mjs").read_text()
    imports = re.findall(r'from "([^"]+)"', script)
    allowed = {"../components/shell/session.ts", "../components/shell/authObservation.ts"}
    assert all(i.startswith("node:") or i in allowed for i in imports)
    for case in (
        "replacing the Product API key",
        "changing the Store UUID",
        "disconnect clears",
        "stale auth result",
        "stale ticket completion",
        "401 rejects, 403",
        "never holds the key",
    ):
        assert case in script, case


# ----- Task 031: the generic integrations settings page ------------------------------------------


INTEGRATIONS_UI = WEB / "components" / "integrations"


def test_integrations_page_is_generic_and_renders_no_provider_html() -> None:
    page = code(WEB / "app" / "settings" / "integrations" / "page.tsx")
    assert "<IntegrationsSettings />" in page
    sources = {p.name: code(p) for p in INTEGRATIONS_UI.glob("*.tsx")}
    assert set(sources) == {"IntegrationsSettings.tsx", "ConnectionForm.tsx"}
    joined = "\n".join(sources.values()).lower()
    for provider in ("shopify", "woocommerce", "whatsapp", "meta ads", "google ads", "bosta",
                     "shipblu", "f" + "ulfly"):  # fmt: skip
        assert provider not in joined, provider  # no hard-coded provider cards
    for path in [
        *INTEGRATIONS_UI.glob("*.tsx"),
        WEB / "app" / "settings" / "integrations" / "page.tsx",
    ]:
        assert "dangerouslySetInnerHTML" not in path.read_text(), path.name
        assert "innerHTML" not in path.read_text(), path.name
    settings = sources["IntegrationsSettings.tsx"]
    assert "No integrations are installed in this build." in settings  # empty catalog state
    assert "definition.connectable ?" in settings  # Connect only for connectable definitions
    assert "Last known test" in settings and "configured_secret_fields" in settings
    assert "useProductSession()" in settings and "setApiKey" not in settings  # the one session
    assert "busyRef.current" in settings  # one mutation at a time
    for forbidden in ("setInterval", "setTimeout", "for (", "while ("):
        assert forbidden not in settings, forbidden  # never auto-retried


def test_secret_inputs_are_write_only_and_never_prefilled() -> None:
    form = code(INTEGRATIONS_UI / "ConnectionForm.tsx")
    assert form.count('type="password"') == 1 and 'autoComplete="new-password"' in form
    assert 'if (field.kind === "secret") continue;' in form  # never pre-filled from metadata
    assert 'value={secrets[field.name] ?? ""}' in form
    assert "setSecrets({});" in form  # cleared after every submission
    assert "useState<Record<string, string>>({})" in form
    # Editing settings never sends credentials; replacing them never sends settings.
    assert 'const showSecrets = mode !== "edit";' in form
    assert 'const showConfig = mode !== "credentials";' in form
    settings = code(INTEGRATIONS_UI / "IntegrationsSettings.tsx")
    assert (
        "connection.config[" not in settings
        or "secret" not in settings.split("connection.config[")[1][:40]
    )
    assert "get_secret_value" not in settings


def test_product_navigation_links_every_area_once() -> None:
    # Task 038: pages no longer carry their own navigation; the shell links every area.
    navigation = shell("ProductNavigation.tsx")
    hrefs = re.findall(r'href: "([^"]+)"', navigation)
    assert hrefs == ["/", "/operations", "/approvals", "/conversations", "/workflows",
                     "/settings/agents", "/settings/integrations",
                     "/settings/knowledge", "/system"]  # fmt: skip
    for page in (component("Console.tsx"), code(INTEGRATIONS_UI / "IntegrationsSettings.tsx")):
        assert 'className="topbar__nav"' not in page and "<nav" not in page


# ----- Task 032: the Product Agents settings page ------------------------------------------


AGENTS_UI = WEB / "components" / "agents"


def test_agents_page_is_lifecycle_management_only() -> None:
    page = code(WEB / "app" / "settings" / "agents" / "page.tsx")
    assert "<AgentsSettings />" in page
    assert {p.name for p in AGENTS_UI.glob("*.tsx")} == {"AgentsSettings.tsx"}
    settings = code(AGENTS_UI / "AgentsSettings.tsx")
    # Product API only: the explicit Agent-management client functions, never AgentOS.
    calls = set(re.findall(r"\b(listAgents|setAgentEnabled|resetAgentConfiguration|"
                           r"getAgentCatalog|getAgent|getSkillCatalog|getSkill|getTaskCatalog|"
                           r"getTask)\(", settings))  # fmt: skip
    assert calls == {"listAgents", "setAgentEnabled", "resetAgentConfiguration",
                     "getSkillCatalog", "getTaskCatalog"}  # fmt: skip
    assert "fetch(" not in settings and "/api/" not in settings
    for agentos in ('"/agents', '"/info', '"/sessions', "os_security", "AgentOS("):
        assert agentos not in settings, agentos
    # No editor of any kind: no text areas, no prompt/model/tool/permission inputs.
    assert "<textarea" not in settings and "contentEditable" not in settings
    assert "<input" not in settings  # the key form lives in the shell only
    for word in ("prompt", "instruction", "model_id", "api key field", "tool selection",
                 "dangerouslySetInnerHTML", "innerHTML", "JSON.parse"):  # fmt: skip
        assert word.lower() not in settings.lower(), word
    assert "useProductSession()" in settings and "busyRef.current" in settings
    for forbidden in ("setInterval", "setTimeout", "for (", "while ("):
        assert forbidden not in settings, forbidden
    joined = settings.lower()
    for provider in ("shopify", "woocommerce", "whatsapp", "f" + "ulfly"):
        assert provider not in joined, provider


def test_disabled_operations_agent_message_in_the_console() -> None:
    ui = component("ui.tsx")
    assert (
        'if (context === "analysis") return "Operations Agent is disabled (Settings → Agents)";'
        in ui
    )


def test_skills_and_tasks_are_read_only_in_the_ui() -> None:
    settings = code(AGENTS_UI / "AgentsSettings.tsx")
    section = settings.split("function AgentCapabilities", 1)[1]
    # Display only: no button, input, form or editor inside the Skills/Tasks section.
    for control in ("<button", "<input", "<form", "<textarea", "<select", "onClick",
                    "onChange", "contentEditable"):  # fmt: skip
        assert control not in section, control
    assert "Task limits are contract metadata" in section
    assert "acceptance_criteria" in section and "allowed_write_actions" in section


# ----- Task 034: the read-only Workflows settings page -----------------------------------------


WORKFLOWS_UI = WEB / "components" / "workflows"


def test_workflows_page_is_read_only_inspection() -> None:
    page = code(WEB / "app" / "workflows" / "page.tsx")
    assert "<WorkflowsSettings />" in page
    assert {p.name for p in WORKFLOWS_UI.glob("*.tsx")} == {"WorkflowsSettings.tsx"}
    settings = code(WORKFLOWS_UI / "WorkflowsSettings.tsx")
    calls = set(re.findall(r"\b(getWorkflowCatalog|getWorkflow|listWorkflowRuns|getWorkflowRun"
                           r")\(", settings))  # fmt: skip
    assert calls == {"getWorkflowCatalog", "listWorkflowRuns", "getWorkflowRun"}
    assert "fetch(" not in settings and "/api/" not in settings
    # No Run / Retry / Resume control, no input form, no JSON or code editor.
    assert "<input" not in settings  # the key form lives in the shell only
    assert "<textarea" not in settings and "contentEditable" not in settings
    assert "<select" not in settings
    assert settings.count("<button") == 2  # Refresh, Details
    labels = sorted(t.strip() for t in re.findall(r">\s*([^<>{}]+?)\s*</button>", settings))
    assert labels == ["Details", "Refresh"]
    for word in ("resume", "retry(", "execute", "runWorkflow", "dangerouslySetInnerHTML",
                 "innerHTML", "JSON.parse", "checkpoint\"]", "input_state"):  # fmt: skip
        assert word.lower() not in settings.lower(), word
    for forbidden in ("setInterval", "setTimeout", "while ("):
        assert forbidden not in settings, forbidden
    assert "useProductSession()" in settings and "setApiKey" not in settings


# ----- Task 035: the Knowledge settings page -----------------------------------------------------


KNOWLEDGE_UI = WEB / "components" / "knowledge"


def test_only_knowledge_document_writes_get_the_larger_request_cap() -> None:
    larger = sorted(str(p.relative_to(API_ROUTES)) for p in API_ROUTES.rglob("route.ts")
                    if "maxRequestBytes" in code(p))  # fmt: skip
    assert larger == ["knowledge/document/create/route.ts", "knowledge/document/version/route.ts"]
    version = code(API_ROUTES / "knowledge" / "document" / "version" / "route.ts")
    assert "maxRequestBytes" not in version.split("export function POST", 1)[0]
    # Operating-model publishing is API-first: no BFF route reaches it.
    assert "operating-model/publish" not in code(PROXY)
    assert not (API_ROUTES / "knowledge" / "operating-model" / "publish").exists()


def test_knowledge_page_renders_untrusted_text_inertly() -> None:
    page = code(WEB / "app" / "settings" / "knowledge" / "page.tsx")
    assert "<KnowledgeSettings />" in page
    assert {p.name for p in KNOWLEDGE_UI.glob("*.tsx")} == {"KnowledgeSettings.tsx"}
    settings = code(KNOWLEDGE_UI / "KnowledgeSettings.tsx")
    assert "fetch(" not in settings and "/api/" not in settings
    for forbidden in ("dangerouslySetInnerHTML", "innerHTML", "eval(", "new Function",
                      "JSON.parse", "marked", "remark", "markdown-it", "<iframe",
                      'type="file"', "FileReader", "localStorage", "sessionStorage"):  # fmt: skip
        assert forbidden not in settings, forbidden
    # Bodies and excerpts are plain text inside <pre>, labelled as untrusted references.
    assert '<pre className="knowledge-text">{text}</pre>' in settings
    assert "<InertText text={detail.current.body} />" in settings
    assert "<InertText text={reference.excerpt} />" in settings
    assert "Untrusted reference" in settings
    # Operating model: display only (no publish call, no JSON editor).
    calls = set(re.findall(r"\b(\w+Knowledge\w*|queryKnowledge)\(", settings))
    assert calls == {"getKnowledgeOperatingModel", "listKnowledgeOperatingModelVersions",
                     "getKnowledgeOperatingModelVersion", "listKnowledgeDocuments",
                     "getKnowledgeDocument", "getKnowledgeDocumentVersion",
                     "createKnowledgeDocument", "publishKnowledgeDocumentVersion",
                     "archiveKnowledgeDocument", "queryKnowledge"}  # fmt: skip
    assert settings.count("<textarea") == 1  # the document text only
    assert "publishKnowledgeOperatingModel" not in settings
    assert "deleteKnowledge" not in settings and '"DELETE"' not in settings
    assert "useProductSession()" in settings and "busyRef.current" in settings
    for forbidden in ("setInterval", "setTimeout", "while ("):
        assert forbidden not in settings, forbidden
    joined = settings.lower()
    for provider in ("shopify", "woocommerce", "whatsapp", "f" + "ulfly"):
        assert provider not in joined, provider


# ----- Task 036: the Approvals settings page -----------------------------------------------------


APPROVALS_UI = WEB / "components" / "approvals"


def test_approval_routes_are_fixed_and_there_is_no_create_route() -> None:
    routes = sorted(
        str(p.relative_to(API_ROUTES)) for p in (API_ROUTES / "approvals").rglob("route.ts")
    )
    assert routes == sorted(r for r in EXPECTED_ROUTES if r.startswith("approvals/"))
    for path in (API_ROUTES / "approvals").rglob("route.ts"):
        source = code(path)
        assert "maxRequestBytes" not in source and "idempotencyKey" not in source, path
        assert "create" not in str(path.relative_to(API_ROUTES)), path
    proxy = code(PROXY)
    assert "/api/v1/approvals/create" not in proxy and '"/api/v1/approvals/request"' not in proxy


def test_approvals_page_renders_untrusted_text_inertly() -> None:
    page = code(WEB / "app" / "approvals" / "page.tsx")
    assert "<ApprovalsSettings />" in page
    assert {p.name for p in APPROVALS_UI.glob("*.tsx")} == {"ApprovalsSettings.tsx"}
    settings = code(APPROVALS_UI / "ApprovalsSettings.tsx")
    assert "fetch(" not in settings and "/api/" not in settings
    for forbidden in ("dangerouslySetInnerHTML", "innerHTML", "eval(", "new Function",
                      "JSON.parse", "marked", "remark", "markdown-it", "<iframe",
                      'type="file"', "FileReader", "localStorage", "sessionStorage",
                      "setInterval", "setTimeout", "while (", "createApproval",
                      "requestApproval", "sample", "placeholderApprovals", "mock"):  # fmt: skip
        assert forbidden not in settings, forbidden
    calls = set(re.findall(r"\b(\w+Approvals?\w*)\(", settings)) - {"setApprovals"}
    assert calls == {"listApprovals", "getApproval", "approveApproval", "rejectApproval",
                     "cancelApproval", "resumeApprovalWorkflow"}  # fmt: skip
    # Notes and summaries are plain JSX text; a decision needs an explicit confirmation and
    # reject / cancel need a bounded reason.
    assert '<p className="knowledge-text">{approval.decision_note}</p>' in settings
    assert "{approval.summary.description}" in settings
    assert "maxLength={MAX_NOTE}" in settings and "const MAX_NOTE = 1000;" in settings
    assert settings.count("<textarea") == 1
    assert "Confirm approval" in settings and "Confirm rejection" in settings
    assert "needsReason && !reason" in settings
    assert "useProductSession()" in settings and "busyRef.current" in settings
    # A clean empty state, never fake requests.
    assert (
        "No approval requests." in settings and "No requests are awaiting a decision." in settings
    )
    joined = settings.lower()
    for provider in ("shopify", "woocommerce", "whatsapp", "f" + "ulfly"):
        assert provider not in joined, provider


# ----- Task 037: the Conversations page ----------------------------------------------------------


CONVERSATIONS_UI = WEB / "components" / "conversations"


def test_conversation_routes_are_read_only_get_routes() -> None:
    routes = sorted(str(p.relative_to(API_ROUTES))
                    for p in (API_ROUTES / "conversations").rglob("route.ts"))  # fmt: skip
    assert routes == sorted(r for r in EXPECTED_ROUTES if r.startswith("conversations/"))
    for path in (API_ROUTES / "conversations").rglob("route.ts"):
        source = code(path)
        assert "export function GET" in source and "export function POST" not in source
        assert "body: true" not in source and "maxRequestBytes" not in source
    proxy = code(PROXY)
    for word in ("webhook", "/inbound", "conversations/send", "/reply"):
        assert word not in proxy, word


def test_conversations_page_is_read_only_and_renders_text_inertly() -> None:
    page = code(WEB / "app" / "conversations" / "page.tsx")
    assert "<ConversationsPage />" in page
    assert {p.name for p in CONVERSATIONS_UI.glob("*.tsx")} == {"ConversationsPage.tsx"}
    ui = code(CONVERSATIONS_UI / "ConversationsPage.tsx")
    assert "fetch(" not in ui and "/api/" not in ui
    for forbidden in ("dangerouslySetInnerHTML", "innerHTML", "eval(", "new Function",
                      "JSON.parse", "marked", "remark", "markdown-it", "<iframe",
                      'type="file"', "FileReader", "localStorage", "sessionStorage",
                      "setInterval", "setTimeout", "<textarea", "Send", "Reply",
                      "sendMessage", "method: \"POST\"", "mock", "sample"):  # fmt: skip
        assert forbidden not in ui, forbidden
    calls = set(re.findall(r"\b(list\w*Conversation\w*|getConversation)\(", ui))
    assert calls == {"listConversations", "listConversationMessages"}
    # Message text is plain JSX text in a layout-safe container.
    assert '<p className="transcript__text">{message.text}</p>' in ui
    css = (WEB / "app" / "globals.css").read_text()
    assert "overflow-wrap: anywhere" in css.split(".transcript__text")[1].split("}")[0]
    assert "No conversations yet." in ui
    assert "useProductSession()" in ui and "busyRef.current" in ui
    joined = ui.lower()
    for provider in ("whatsapp", "twilio", "telegram", "messenger", "instagram", "f" + "ulfly"):
        assert provider not in joined, provider
