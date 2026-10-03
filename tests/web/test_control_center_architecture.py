"""Task 038: static guards for the Agento Product Control Center (apps/web).

One shell, one in-memory ProductSessionProvider and a read-only Overview over the EXISTING
Product APIs. The runtime behaviour is proven by apps/web/scripts/control-center.browser.mjs
(Playwright against a test-only stub upstream) and the proxy smoke test; these tests pin
the reviewed design.
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
API = ROOT / "apps" / "api"
COMPONENTS = WEB / "components"
SHELL = COMPONENTS / "shell"
OVERVIEW = COMPONENTS / "overview" / "OverviewPage.tsx"
RUNTIME_DIRS = (WEB / "app", WEB / "lib", WEB / "components")

# Dependency manifests are unchanged by the Control Center (no UI, chart, icon or test
# framework was added).
# Task 039 (reviewed): opentelemetry-sdk moves to runtime and the OTLP/HTTP exporter is
# added (optional, disabled-by-default export); nothing else changes.
PYPROJECT_SHA256 = "449b133228a3c7172e3e0c4b4868401bbf6df521e03bdd1e1ae1e14917a54744"
UV_LOCK_SHA256 = "bd2ecfa15909b304cb4892c704b0110ed0c008c7c1670b9f2024d40b3348d359"
PACKAGE_JSON_SHA256 = "2d1c907aeffb7b41f29d3865fe8e0c7d580de3b7e0e312e23856630de6c198f2"
PACKAGE_LOCK_SHA256 = "e5ba136b463838ff3b82cdf88fb73c08aa4d2ee34b410fee945793617156c989"

PAGE_COMPONENTS = {
    "console/Console.tsx": "Operations",
    "approvals/ApprovalsSettings.tsx": "Approvals",
    "conversations/ConversationsPage.tsx": "Conversations",
    "workflows/WorkflowsSettings.tsx": "Workflows",
    "agents/AgentsSettings.tsx": "Agents",
    "integrations/IntegrationsSettings.tsx": "Integrations",
    "knowledge/KnowledgeSettings.tsx": "Knowledge",
}


def code(path: Path) -> str:
    """Source without // line comments and /* */ blocks (guards inspect code, not prose)."""
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return re.sub(r"(?m)(^|[^:\"'])//.*$", r"\1", text)


def runtime_files() -> list[Path]:
    return sorted(p for d in RUNTIME_DIRS for p in d.rglob("*") if p.suffix in {".ts", ".tsx"})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ----- scope: frontend only ----------------------------------------------------------------------


def test_no_migration_0009_and_head_stays_0008() -> None:
    versions = sorted(p.name for p in (API / "migrations" / "versions").glob("0*.py"))
    assert versions[-1] == "0008_create_conversations.py"
    assert not [name for name in versions if name.startswith("0009")]
    heads = [
        p
        for p in (API / "migrations" / "versions").glob("*.py")
        if "down_revision" in p.read_text()
    ]
    revisions = {
        re.search(r'^revision\b[^=\n]*=\s*"([^"]+)"', p.read_text(), re.M).group(1) for p in heads
    }
    downs = {
        m.group(1)
        for p in heads
        for m in [re.search(r'^down_revision\b[^=\n]*=\s*"([^"]+)"', p.read_text(), re.M)]
        if m
    }
    assert len(revisions - downs) == 1  # one head


def test_no_new_dependency() -> None:
    assert sha256(ROOT / "pyproject.toml") == PYPROJECT_SHA256
    assert sha256(ROOT / "uv.lock") == UV_LOCK_SHA256
    assert sha256(WEB / "package.json") == PACKAGE_JSON_SHA256
    assert sha256(WEB / "package-lock.json") == PACKAGE_LOCK_SHA256
    lock = (WEB / "package-lock.json").read_text()
    for package in (
        "tailwind",
        "shadcn",
        "@radix-ui",
        "lucide",
        "chart.js",
        "recharts",
        '"node_modules/@playwright',
        '"node_modules/playwright"',
        "@testing-library",
    ):
        assert package not in lock, package


def test_catalogs_are_unchanged_and_nothing_is_installed() -> None:
    from app.agent_management.catalog import build_default_agent_catalog
    from app.integration_management import build_default_integration_catalog
    from app.integrations.messaging import build_default_messaging_registry
    from app.workflow_management.catalog import build_default_workflow_catalog

    assert build_default_agent_catalog().agent_ids == frozenset({"operations"})
    assert build_default_workflow_catalog().workflow_ids == frozenset({"operations.daily_report"})
    catalog = build_default_integration_catalog()
    assert len(catalog) == 0 and len(build_default_messaging_registry(catalog)) == 0


def test_no_new_bff_route_or_generic_proxy() -> None:
    routes = sorted(
        str(p.relative_to(WEB / "app" / "api" / "product"))
        for p in (WEB / "app" / "api" / "product").rglob("route.ts")
    )
    # Task 038 added none; Task 039 adds exactly health/ready and system/status.
    assert len(routes) == 46
    assert {"health/ready/route.ts", "system/status/route.ts"} <= set(routes)
    assert not [r for r in routes if "[" in r or "overview" in r or "aggregate" in r]
    assert not list((WEB / "app").rglob("[[]*"))  # no dynamic / catch-all segment anywhere
    assert not (WEB / "middleware.ts").exists() and not (WEB / "proxy.ts").exists()


# ----- one shell, one session ---------------------------------------------------------------


def test_root_layout_mounts_one_provider_and_one_shell() -> None:
    layout = code(WEB / "app" / "layout.tsx")
    assert layout.count("<ProductSessionProvider>") == 1 and layout.count("<AppShell>") == 1
    assert layout.index("<ProductSessionProvider>") < layout.index("<AppShell>")
    assert 'default: "Agento"' in layout
    assert 'description: "AI operating layer for commerce operations."' in layout
    for path in runtime_files():
        if path.name != "layout.tsx":
            assert "<ProductSessionProvider" not in path.read_text(), path.name


def test_shell_components_exist_and_own_branding_navigation_and_session() -> None:
    names = {p.name for p in SHELL.glob("*.ts*")}
    assert {
        "AppShell.tsx",
        "ProductNavigation.tsx",
        "ProductSessionProvider.tsx",
        "SessionControl.tsx",
        "PageHeader.tsx",
        "session.ts",
        "authObservation.ts",
    } <= names
    shell = code(SHELL / "AppShell.tsx")
    assert "Agento" in shell and "AI Operating Layer" in shell
    assert (
        "<ProductNavigation" in shell
        and "<SessionControl />" in shell
        and "<HealthBadge />" in shell
    )
    assert "aria-expanded={menuOpen}" in shell and 'aria-controls="product-sidebar"' in shell
    assert "<h1" not in shell  # every page renders its own single h1
    navigation = code(SHELL / "ProductNavigation.tsx")
    assert 'aria-current={active ? "page" : undefined}' in navigation
    assert re.findall(r'group: "(\w+)"', navigation) == ["Work", "Configure"]
    labels = re.findall(r'label: "([^"]+)"', navigation)
    assert labels == [
        "Overview",
        "Operations",
        "Approvals",
        "Conversations",
        "Workflows",
        "Agents",
        "Integrations",
        "Knowledge",
        "System",  # Task 039, under Configure
    ]


def test_internal_framework_names_never_reach_the_ui() -> None:
    for path in [*SHELL.glob("*.tsx"), OVERVIEW, *(COMPONENTS / p for p in PAGE_COMPONENTS)]:
        visible = re.sub(r"^import .*$", "", code(path), flags=re.M)
        for name in ("AgentOS", "Agno", "FastAPI", "Next.js", "NextJS"):
            assert name not in visible, (path.name, name)


def test_the_key_lives_only_in_the_provider_memory() -> None:
    provider = code(SHELL / "ProductSessionProvider.tsx")
    assert "useState<string | null>(null)" in provider
    holders = [p.name for p in runtime_files() if "setApiKey" in code(p)]
    assert holders == ["ProductSessionProvider.tsx"]
    for path in runtime_files():
        source = code(path)
        for forbidden in (
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "document.cookie",
            "cookieStore",
            "cookies(",
            "history.pushState",
            "history.replaceState",
            "location.hash",
            "navigator.sendBeacon",
            "postMessage(",
        ):
            assert forbidden not in source, (path.name, forbidden)
        # Never in a URL, a data-* / HTML attribute or a server-rendered prop.
        assert not re.search(r"(href|src|action|data-[\w-]+|value)=\{[^}]*apiKey", source), (
            path.name
        )
        assert not re.search(r"[?&]\w*key=\$\{", source, re.I), path.name
    # Server components and route pages never see the key.
    for page in (WEB / "app").rglob("page.tsx"):
        assert "apiKey" not in code(page) and '"use client"' not in page.read_text(), page


def test_auth_outcomes_follow_401_403_rules() -> None:
    session = code(SHELL / "session.ts")
    assert 'return result.status === 401 ? "rejected" : "other";' in session
    assert 'if (result.ok) return "accepted";' in session


def test_auth_outcomes_are_bound_to_the_epoch_their_request_began_in() -> None:
    client = code(WEB / "lib" / "product-api" / "client.ts")
    send = client.split("async function send<T>(", 1)[1].split("\n}\n", 1)[0]
    # Phase one runs BEFORE the exchange (network I/O); phase two after, with the token.
    begin = send.index("const observation = beginAuthObservation(init.headers);")
    exchange = send.index("const result = await exchange(path, init, guard);")
    complete = send.index(
        "observation.observer.complete(observation.token, "
        "{ ok: result.ok, status: result.status });"
    )
    assert begin < exchange < complete
    assert send.count("await") == 1  # nothing awaited before the token is captured
    helper = client.split("function beginAuthObservation(", 1)[1].split("\n}\n", 1)[0]
    assert "await" not in helper and "async" not in helper  # synchronous request setup
    assert 'observer.begin(authorization.slice("Bearer ".length))' in helper
    # The token is the begin() result (an epoch); the key is never kept by the client.
    assert "begin(apiKey: string): number | null;" in client
    assert re.findall(r"^let (\w+)", client, re.M) == ["authObserver"]
    observation = code(SHELL / "authObservation.ts")
    assert (
        "return currentKey !== null && apiKey === currentKey ? sessionEpoch : null;" in observation
    )
    assert "report(sessionEpoch, result);" in observation
    assert "import " not in observation and "let " not in observation
    provider = code(SHELL / "ProductSessionProvider.tsx")
    wiring = provider.split("createAuthObservation(", 1)[1].split("),\n    [],", 1)[0]
    assert "() => ({ apiKey: keyRef.current, sessionEpoch: epochRef.current })" in wiring
    report = wiring.split("(sessionEpoch, result) =>", 1)[1]
    assert 'dispatch({ type: "authResult", sessionEpoch, outcome: authOutcome(result) })' in report
    assert "epochRef" not in report  # completion never reads the CURRENT epoch
    for path in (WEB / "lib" / "product-api" / "client.ts", SHELL / "authObservation.ts",
                 SHELL / "ProductSessionProvider.tsx"):  # fmt: skip
        source = code(path)
        for forbidden in ("localStorage", "sessionStorage", "indexedDB", "cookie", "caches."):
            assert forbidden not in source, (path.name, forbidden)
    tests = (WEB / "scripts" / "session-epoch.test.mjs").read_text()
    for case in ("same-key reconnect: a stale 401", "same-key reconnect: a stale success",
                 "stays ignored after switching to key B",
                 "accepts on success, rejects on 401 and keeps state on 403"):  # fmt: skip
        assert case in tests, case
    browser = (WEB / "scripts" / "control-center.browser.mjs").read_text()
    assert "same-key reconnect: ${label}" in browser and "/__stub/hold?path=" in browser


def test_pages_render_no_header_nav_or_key_form_of_their_own() -> None:
    for relative, title in PAGE_COMPONENTS.items():
        source = code(COMPONENTS / relative)
        assert "useProductSession()" in source, relative
        assert f'title="{title}"' in source, relative  # the one PageHeader h1
        for forbidden in (
            "<header",
            "<nav",
            "topbar",
            'type="password"',
            "product-api-key",
            'title="Session"',
            "Disconnect",
            "<main",
        ):
            assert forbidden not in source, (relative, forbidden)
    h1 = sorted(p.name for p in COMPONENTS.rglob("*.tsx") if "<h1" in code(p))
    assert h1 == ["PageHeader.tsx"]


def test_no_inner_html_anywhere() -> None:
    for path in runtime_files():
        source = path.read_text()
        assert "dangerouslySetInnerHTML" not in source and "innerHTML" not in source, path.name


# ----- the Overview is read-only -----------------------------------------------------------------


READS = {
    "listAgents",
    "listApprovals",
    "listWorkflowRuns",
    "listIntegrationConnections",
    "getIntegrationCatalog",
    "listKnowledgeDocuments",
    "listConversations",
}


def test_overview_calls_only_read_functions() -> None:
    overview = code(OVERVIEW)
    imported = set(
        re.findall(
            r"\b(\w+),?\n",
            overview.split('} from "../../lib/product-api/client";')[0].rsplit("import {", 1)[1],
        )
    )
    assert imported == READS
    assert 'listApprovals(apiKey, "requested")' in overview
    for forbidden in (
        "runOperations",
        "getDailyReport",
        "createTicket",
        "getTicketCommand",
        "approveApproval",
        "rejectApproval",
        "cancelApproval",
        "resumeApproval",
        "testIntegrationConnection",
        "setIntegrationConnectionEnabled",
        "createIntegrationConnection",
        "deleteIntegrationConnection",
        "setAgentEnabled",
        "resetAgentConfiguration",
        "createKnowledgeDocument",
        "publishKnowledge",
        "archiveKnowledge",
        "queryKnowledge",
        "fetch(",
        "/api/",
        "setInterval",
        "setTimeout",
        "EventSource",
        "WebSocket",
        'method: "POST"',
        "message.text",
        ".body}",
        "excerpt",
        "summary.description",
    ):
        assert forbidden not in overview, forbidden


def test_overview_cards_degrade_independently() -> None:
    overview = code(OVERVIEW)
    assert "return `You don't have access to ${area}.`;" in overview
    assert "return `${area} is unavailable right now.`;" in overview
    assert "No integrations are installed in this build." in overview
    for card in (
        "OperationsAgentCard",
        "ApprovalsCard",
        "WorkflowsCard",
        "ConversationsCard",
        "IntegrationsCard",
        "KnowledgeCard",
    ):
        assert f"<{card} " in overview, card
    # Each read is its own hook call: one failure never blanks another card.
    assert overview.count("= useRead<") == len(READS)
    assert "Promise.all" not in overview


def test_quick_actions_only_navigate_to_operations_tabs() -> None:
    overview = code(OVERVIEW)
    actions = re.findall(r'href: "(/operations\?tab=\w+)", label: "([^"]+)"', overview)
    assert actions == [
        ("/operations?tab=analysis", "Analyze operations"),
        ("/operations?tab=report", "Daily report"),
        ("/operations?tab=ticket", "Create ticket"),
    ]
    tabs = code(COMPONENTS / "console" / "tabs.ts")
    assert '?.id ?? "analysis"' in tabs  # unknown tab falls back to analysis


def test_operations_never_runs_on_its_own() -> None:
    console = code(COMPONENTS / "console" / "Console.tsx")
    for forbidden in ("runOperations", "getDailyReport", "createTicket", "getTicketCommand"):
        assert forbidden not in console, forbidden
    for panel, call in (
        ("AnalysisPanel.tsx", "runOperations("),
        ("ReportPanel.tsx", "getDailyReport("),
        ("TicketPanel.tsx", "createTicket("),
    ):
        source = code(COMPONENTS / "console" / panel)
        assert source.count(call) == 1, panel
        submit = source.split("async function submit", 1)[1]
        assert call in submit.split("\n  }\n", 1)[0], panel  # only inside the explicit submit
        assert (
            "useEffect" not in source
            or call not in source.split("useEffect", 1)[1].split("});", 1)[0]
        )


# ----- routes ------------------------------------------------------------------------------------


def test_routes_and_legacy_redirects() -> None:
    app = WEB / "app"
    assert "<OverviewPage />" in code(app / "page.tsx")
    assert "<Console initialTab={tabFrom(tab)} />" in code(app / "operations" / "page.tsx")
    assert "<ApprovalsSettings />" in code(app / "approvals" / "page.tsx")
    assert "<WorkflowsSettings />" in code(app / "workflows" / "page.tsx")
    assert "<ConversationsPage />" in code(app / "conversations" / "page.tsx")
    for name in ("agents", "integrations", "knowledge"):
        assert "redirect(" not in code(app / "settings" / name / "page.tsx"), name
    for name in ("approvals", "workflows"):
        legacy = code(app / "settings" / name / "page.tsx")
        assert (
            f'redirect("/{name}");' in legacy and "Settings" not in legacy.split("redirect", 1)[1]
        )
        assert "redirect(" not in code(app / name / "page.tsx")  # no loop back
    # One implementation per area: the legacy pages import no page component.
    for name in ("approvals", "workflows"):
        assert "components/" not in code(app / "settings" / name / "page.tsx")


def test_browser_tests_use_a_test_only_local_stub() -> None:
    script = (WEB / "scripts" / "control-center.browser.mjs").read_text()
    stub = (WEB / "scripts" / "control-center-stub.mjs").read_text()
    imports = re.findall(r'from "([^"]+)"', script)
    assert all(i.startswith("node:") or i == "./control-center-stub.mjs" for i in imports), imports
    assert 'require("playwright")' in script  # present on the machine; never a dependency
    assert all(i.startswith("node:") for i in re.findall(r'from "([^"]+)"', stub))
    assert '"127.0.0.1"' in stub and "localhost" not in stub and "TEST-ONLY" in stub
    # The stub records whether a key was sent, never the key itself.
    assert "authenticated: key !== null" in stub and "requests.push({ method" in stub
    for case in (
        "no authenticated call",
        "only parallel read calls, no model call",
        "persists across client navigation",
        "hard reload forgets the key",
        "disconnect clears",
        "401 marks the key rejected",
        "403 and 503 degrade",
        "deep links",
        "redirect",
        "no horizontal overflow",
        "Operations regression",
    ):
        assert case in script, case
    # The scripts are test tooling: nothing in the app imports them.
    for path in runtime_files():
        assert "control-center" not in path.read_text(), path.name
