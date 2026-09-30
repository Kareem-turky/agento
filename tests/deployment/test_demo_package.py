"""Task 030: static guards for the runnable LOCAL Product demo.

The runtime behaviour (launcher, Web BFF business flow, restart durability, credential
boundary, staging/production refusal on the real image) is proven by
.github/scripts/demo-smoke.sh in the Infrastructure CI job; these tests pin the design.
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "deployments" / "demo"
OVERRIDE = DEMO / "compose.override.yaml"
TEMPLATE_COMPOSE = ROOT / "deployments" / "template" / "compose.yaml"
TEMPLATE_ENV = ROOT / "deployments" / "template" / ".env.example"
LAUNCHER = ROOT / "scripts" / "demo.sh"
SMOKE = ROOT / ".github" / "scripts" / "demo-smoke.sh"
CI = ROOT / ".github" / "workflows" / "ci.yml"


class _ComposeLoader(yaml.SafeLoader):
    """Compose merge tags (``!override``/``!reset``) as plain values."""


def _sequence(loader: yaml.SafeLoader, node: yaml.Node) -> list[Any]:
    assert isinstance(node, yaml.SequenceNode)
    return loader.construct_sequence(node)


_ComposeLoader.add_constructor("!override", _sequence)
_ComposeLoader.add_constructor("!reset", lambda loader, node: None)


def override() -> dict[str, Any]:
    return yaml.load(OVERRIDE.read_text(), Loader=_ComposeLoader)  # noqa: S506 - SafeLoader subclass


def test_override_layers_the_demo_policy_on_the_template_and_nothing_else() -> None:
    data = override()
    assert set(data) == {"name", "services"}
    assert data["name"] == "commerce-ai-platform-demo"
    assert set(data["services"]) == {"api"}  # postgres, migrate and web come from the template
    api = data["services"]["api"]
    assert set(api) == {"environment", "networks"}
    assert api["environment"] == {
        "APP_ENVIRONMENT": "local",
        "APP_BUSINESS_BACKEND": "mock",
        "APP_DEFAULT_MODEL_PROVIDER": "demo",
        "APP_DEFAULT_MODEL_ID": "",
        "APP_PRODUCT_AUTH_MODE": "api_key",
        "APP_BACKEND_CONFIG_DIR": "",
        "APP_BACKEND_SECRETS_DIR": "",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
    }
    # No outbound network for the demo API: only the template's private networks.
    assert api["networks"] == ["database", "product"]
    assert "networks: !override" in OVERRIDE.read_text()
    assert "image" not in api and "build" not in api  # the SAME images as the template


def test_the_generic_template_stays_production_oriented() -> None:
    compose = TEMPLATE_COMPOSE.read_text()
    env = TEMPLATE_ENV.read_text()
    assert "demo" not in compose.lower()
    assert "APP_DEFAULT_MODEL_PROVIDER: ${APP_DEFAULT_MODEL_PROVIDER:-disabled}" in compose
    assert "APP_ENVIRONMENT=production" in env and "APP_BUSINESS_BACKEND=disabled" in env
    assert "APP_DEFAULT_MODEL_PROVIDER=disabled" in env
    assert not (ROOT / "deployments" / "demo" / "Dockerfile").exists()
    assert sorted(p.name for p in (ROOT / "apps").rglob("Dockerfile*")
                  if "node_modules" not in p.parts) == ["Dockerfile", "Dockerfile"]  # fmt: skip


def test_launcher_contract() -> None:
    script = LAUNCHER.read_text()
    assert LAUNCHER.stat().st_mode & 0o111
    assert script.startswith("#!/usr/bin/env bash\n") and "set -euo pipefail" in script
    for command in ("up)", "down)", "reset)", "status)", "credentials)"):
        assert command in script, command
    for expected in (
        '"$ROOT/apps/api/Dockerfile"', '"$ROOT/apps/web/Dockerfile"',
        '-f "$TEMPLATE_COMPOSE" -f "$DEMO_COMPOSE"', "compose config --quiet",
        "up -d --wait", "migrate", "down -v", "chmod 600", "chmod 700", "umask 077",
        "/dev/urandom", "sha256sum", "shasum -a 256", 'DEMO_DATE="2026-03-03"',
        "canonical_id(EntityType.STORE, \"shop_south\")", "--network none",
        "Commerce AI Product Demo is ready", "Operations Console:", "Product API Key:",
        "Store UUID:", "Demo business date:", "Suggested analysis:",
        "APP_PRODUCT_AUTH_MODE=api_key", "APP_DEFAULT_MODEL_PROVIDER=demo",
        "APP_BUSINESS_BACKEND=mock", "APP_ENVIRONMENT=local",
    ):  # fmt: skip
        assert expected in script, expected
    # Only the key's SHA-256 is written to the Compose env; the raw key goes to the
    # separate credentials file, never to a container.
    runtime_env = script.split('cat > "$RUNTIME_ENV" <<EOF', 1)[1].split("\nEOF", 1)[0]
    assert "APP_PRODUCT_API_KEYS=$principals" in runtime_env
    assert "$product_key" not in runtime_env
    assert '\\"key_sha256\\":\\"$key_sha\\"' in script  # principals hold the hash only
    assert "$product_key" not in script.split("principals=", 1)[1].split("\n", 1)[0]
    assert "PRODUCT_API_KEY=$product_key" in script
    # Secrets are generated, never literal; no host Python/Node/npm is required.
    assert not re.search(r"(?i)(password|security_key|api_key)=[0-9a-z]{8,}", script)
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    for tool in ("uv ", "npm ", "pip ", "python3 ", "node "):
        assert tool not in code, tool


def test_runtime_state_is_git_ignored_and_untracked() -> None:
    assert "deployments/demo/.runtime/" in (ROOT / ".gitignore").read_text()
    git = shutil.which("git")
    assert git is not None
    for name in ("runtime.env", "credentials"):
        path = f"deployments/demo/.runtime/{name}"
        ignored = subprocess.run([git, "check-ignore", "-q", path], cwd=ROOT)  # noqa: S603
        assert ignored.returncode == 0, name
    tracked = subprocess.run([git, "ls-files", "deployments/demo"], cwd=ROOT,  # noqa: S603
                             capture_output=True, text=True, check=True).stdout.split()  # fmt: skip
    assert all(".runtime" not in path for path in tracked)
    assert "deployments" in (ROOT / ".dockerignore").read_text().split()  # never in a build


def test_ci_runs_the_demo_smoke_after_the_deployment_smoke() -> None:
    workflow = yaml.safe_load(CI.read_text())
    assert [job["name"] for job in workflow["jobs"].values()] == [
        "Backend (Python)", "Frontend (Next.js)", "Infrastructure (Docker Compose)",
    ]  # fmt: skip
    runs = [step.get("run", "") for step in workflow["jobs"]["infrastructure"]["steps"]]
    deployment = next(i for i, r in enumerate(runs) if "deployment-smoke.sh" in r)
    demo = next(i for i, r in enumerate(runs) if "demo-smoke.sh" in r)
    assert deployment < demo
    assert "commerce-ai-platform-api:ci commerce-ai-platform-web:ci" in runs[demo]
    assert any("cap-demo-smoke down -v" in r for r in runs)
    assert SMOKE.stat().st_mode & 0o111


def test_demo_smoke_covers_the_demo_contract() -> None:
    script = SMOKE.read_text()
    for check in (
        '"$DEMO" up', '"$DEMO" down', '"$DEMO" reset', "Operations Console",
        "/api/product/health", "/api/product/operations/reports/daily",
        "/api/product/operations/runs", "/api/product/operations/tickets",
        "/api/product/operations/tickets/commands", "Idempotency-Key", "persistence_complete",
        "docker history --no-trunc", "docker logs", "git check-ignore", "Internal",
        "demo model provider is not allowed in staging/production", "staging production",
        "APP_DEFAULT_MODEL_PROVIDER=demo", "raw Product key in the API environment",
        "trap cleanup EXIT", "set -euo pipefail",
    ):  # fmt: skip
        assert check in script, check
    assert "OPENAI_API_KEY=sk" not in script and "ANTHROPIC_API_KEY=sk" not in script


def test_demo_documentation_is_explicitly_non_production() -> None:
    def flat(path: Path) -> str:
        return " ".join(path.read_text().replace("*", "").split())

    demo = flat(DEMO / "README.md")
    for phrase in ("local Product demo", "not a production installation and not a real integration",
                   "deterministic mock", "LOCAL-DEMO-ONLY native Agno model", "No external calls",
                   "refused in staging and production", "fail-closed", "./scripts/demo.sh reset",
                   "Real Product authentication"):  # fmt: skip
        assert phrase in demo, phrase
    readme = flat(ROOT / "README.md")
    assert readme.index("## Run the Product demo") < readme.index("## 1. What this is")
    for phrase in ("./scripts/demo.sh up", "http://127.0.0.1:3000",
                   "Analyze operations for 2026-03-03.", "Investigate failed shipment",
                   "Review the failed shipment found in the demo operations report.",
                   "./scripts/demo.sh down", "./scripts/demo.sh reset"):  # fmt: skip
        assert phrase in readme, phrase
    acceptance = flat(ROOT / "docs" / "MVP_RELEASE_ACCEPTANCE.md")
    assert "PRODUCTION BUSINESS USE IS STILL BLOCKED." in acceptance
    assert "Production business use is blocked because there is no real backend." in acceptance
    available = "local runnable demo is fully available using deterministic internal demo data"
    assert available in acceptance
    for text in (demo, readme, acceptance):
        assert "production-ready" not in text.replace("not production-ready", "")
