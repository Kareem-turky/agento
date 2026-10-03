"""Task 039: static guards for the read-only System page and its fixed BFF routes."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
PAGE = WEB / "components" / "system" / "SystemPage.tsx"
ROUTES = WEB / "app" / "api" / "product"


def code(path: Path) -> str:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return re.sub(r"(?m)(^|[^:\"'])//.*$", r"\1", text)


def test_system_page_is_read_only_and_uses_the_one_session() -> None:
    page = code(PAGE)
    assert "useProductSession()" in page and "setApiKey" not in page
    imported = re.findall(r"import \{([^}]*)\} from \"../../lib/product-api/client\"", page)
    assert [name.strip() for name in imported[0].split(",")] == ["getSystemStatus"]
    for forbidden in (
        'type="password"',
        "<form",
        "<textarea",
        "<input",
        "<select",
        "fetch(",
        "/api/",
        "setInterval",
        "setTimeout",
        "EventSource",
        "WebSocket",
        "dangerouslySetInnerHTML",
        "innerHTML",
        "localStorage",
        "sessionStorage",
        'method: "POST"',
    ):
        assert forbidden not in page, forbidden
    # No operator action is callable from the page (prose may mention them).
    assert not re.search(r"\b\w*(restart|backup|restore|migrate)\w*\(", page, re.IGNORECASE)
    # Exactly one explicit control: the manual refresh.
    assert page.count("<button") == 1 and "Refresh status" in page
    assert '<ConnectNotice area="system status" />' in page
    assert "key={sessionEpoch}" in page


def test_system_failures_are_safe_fixed_messages() -> None:
    page = code(PAGE)
    assert 'return "You don\'t have access to System status.";' in page
    assert 'return "System status is unavailable right now.";' in page
    assert 'return "Product API key not accepted.";' in page
    # Only the fixed status fields are rendered: never a URL, host, path, id or endpoint.
    rendered = set(re.findall(r"status\.(\w+(?:\.\w+)?)", page))
    assert rendered <= {
        "overall",
        "reasons",
        "reasons.length",
        "reasons.map",
        "components.application",
        "components.database",
        "components.product_schema",
        "components.agent_runtime",
        "application.version",
        "application.environment",
        "application.uptime_seconds",
        "observability.export_mode",
    }
    for word in ("endpoint", "database_url", "company", "actor", "key_id", "hostname"):
        assert word not in page.lower(), word


def test_bff_readiness_is_public_and_status_is_authenticated() -> None:
    ready = code(ROUTES / "health" / "ready" / "route.ts")
    status = code(ROUTES / "system" / "status" / "route.ts")
    assert 'proxyToProduct(request, "healthReady", { authorization: false })' in ready
    assert 'proxyToProduct(request, "systemStatus", { authorization: true })' in status
    for route in (ready, status):
        assert "export function GET" in route
        assert not re.search(r"export function (POST|PUT|PATCH|DELETE)", route)
        assert "query" not in route
    system_routes = sorted(
        str(p.relative_to(ROUTES)) for p in (ROUTES / "system").rglob("route.ts")
    )
    assert system_routes == ["system/status/route.ts"]


def test_web_container_health_goes_through_the_bff_readiness_route() -> None:
    dockerfile = (WEB / "Dockerfile").read_text()
    assert "http://127.0.0.1:3000/api/product/health/ready" in dockerfile
    navigation = code(WEB / "components" / "shell" / "ProductNavigation.tsx")
    configure = navigation.split('group: "Configure"', 1)[1]
    assert '{ href: "/system", label: "System" }' in configure
    assert '{ href: "/system"' not in navigation.split('group: "Configure"', 1)[0]
    # The Overview read set is unchanged: it only links to System.
    overview = code(WEB / "components" / "overview" / "OverviewPage.tsx")
    assert "getSystemStatus" not in overview and '["/system", "System"' in overview
