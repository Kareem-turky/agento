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
}  # fmt: skip
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
                        "listAgents", "setAgentEnabled", "resetAgentConfiguration"}  # fmt: skip
    paths = set(re.findall(r'"(/api/product/[^"]*)"', source))
    assert paths == {"/api/product/" + relative.removesuffix("/route.ts")
                     for relative in EXPECTED_ROUTES}  # fmt: skip
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


def test_api_key_input_is_masked_and_never_rendered_back() -> None:
    session = component("SessionPanel.tsx")
    assert 'type="password"' in session and 'setDraft("")' in session
    assert "Show" not in session  # no "show API key" control
    console = component("Console.tsx")
    assert "useState<string | null>(null)" in console  # memory only
    assert "setApiKey(null)" in console and '{ type: "disconnected" }' in console


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
    assert "placeholder" not in layout.lower() and 'title: "Operations Console"' in layout
    assert 'import "./globals.css";' in layout
    page = (WEB / "app" / "page.tsx").read_text()
    assert "placeholder" not in page.lower() and "<Console />" in page


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
    assert '<main className="layout__main" key={epoch}>' in console
    assert not re.search(r"key=\{[^}]*apiKey", console)
    assert "generation" not in console  # the single epoch replaced the disconnect counter
    session = code(WEB / "components" / "console" / "session.ts")
    assert "apiKey" not in session and "epoch: number;" in session
    assert "epoch: state.epoch + 1" in session
    for forbidden in ("localStorage", "sessionStorage", "indexedDB", "cookie", "sha", "digest"):
        assert forbidden not in session.lower(), forbidden


def test_key_store_and_disconnect_all_start_a_new_epoch() -> None:
    session = code(WEB / "components" / "console" / "session.ts")
    for action in ('case "keySet":', 'case "storeChanged":', 'case "disconnected":'):
        branch = session.split(action, 1)[1].split("case ", 1)[0]
        assert "epoch: state.epoch + 1" in branch, action
        assert "recentCommandId: null" in branch, action
    console = component("Console.tsx")
    assert 'dispatch({ type: "keySet" })' in console
    assert 'dispatch({ type: "storeChanged", storeId: value })' in console
    assert "onStoreChange={changeStore}" in console


def test_stale_completions_are_ignored_by_epoch() -> None:
    session = code(WEB / "components" / "console" / "session.ts")
    for action in ('case "authResult":', 'case "commandCreated":'):
        branch = session.split(action, 1)[1].split("case ", 1)[0]
        assert "action.epoch !== state.epoch" in branch, action
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
    assert all(i.startswith("node:") or i == "../components/console/session.ts" for i in imports)
    for case in (
        "replacing the Product API key",
        "changing the Store UUID",
        "disconnect clears",
        "stale auth result",
        "stale ticket completion",
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
    assert "useState<string | null>(null)" in settings  # key in memory only
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


def test_console_links_to_integrations_settings() -> None:
    console = component("Console.tsx")
    assert '<Link href="/settings/integrations">Integrations</Link>' in console
    assert '<Link href="/settings/agents">Agents</Link>' in console
    settings = code(INTEGRATIONS_UI / "IntegrationsSettings.tsx")
    assert '<Link href="/">Operations Console</Link>' in settings


# ----- Task 032: the Product Agents settings page ------------------------------------------


AGENTS_UI = WEB / "components" / "agents"


def test_agents_page_is_lifecycle_management_only() -> None:
    page = code(WEB / "app" / "settings" / "agents" / "page.tsx")
    assert "<AgentsSettings />" in page
    assert {p.name for p in AGENTS_UI.glob("*.tsx")} == {"AgentsSettings.tsx"}
    settings = code(AGENTS_UI / "AgentsSettings.tsx")
    # Product API only: the explicit Agent-management client functions, never AgentOS.
    calls = set(re.findall(r"\b(listAgents|setAgentEnabled|resetAgentConfiguration|"
                           r"getAgentCatalog|getAgent)\(", settings))  # fmt: skip
    assert calls == {"listAgents", "setAgentEnabled", "resetAgentConfiguration"}
    assert "fetch(" not in settings and "/api/" not in settings
    for agentos in ('"/agents', '"/info', '"/sessions', "os_security", "AgentOS("):
        assert agentos not in settings, agentos
    # No editor of any kind: no text areas, no prompt/model/tool/permission inputs.
    assert "<textarea" not in settings and "contentEditable" not in settings
    inputs = re.findall(r"<input[^>]*>", settings, flags=re.S)
    assert len(inputs) == 1 and 'name="product-api-key"' in inputs[0]
    for word in ("prompt", "instruction", "model_id", "api key field", "tool selection",
                 "dangerouslySetInnerHTML", "innerHTML", "JSON.parse"):  # fmt: skip
        assert word.lower() not in settings.lower(), word
    assert "useState<string | null>(null)" in settings and "busyRef.current" in settings
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
