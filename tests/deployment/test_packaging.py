"""Task 026: static guards for the API image and the generic deployment template.

The runtime behaviour (build, migration job, boot, non-root, read-only root, auth,
secret non-leak, shutdown, production fail-closed) is proven by the Infrastructure CI
job (.github/scripts/deployment-smoke.sh); these tests pin the reviewed design so it
cannot drift silently.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "apps" / "api" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
TEMPLATE = ROOT / "deployments" / "template"
TEMPLATE_COMPOSE = TEMPLATE / "compose.yaml"
TEMPLATE_ENV = TEMPLATE / ".env.example"
LOCAL_COMPOSE = ROOT / "docker-compose.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
SMOKE = ROOT / ".github" / "scripts" / "deployment-smoke.sh"

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
SECRET_NAMES = ("PASSWORD", "SECURITY_KEY", "API_KEY", "DATABASE_URL", "TOKEN", "SECRET")


# ----- helpers ------------------------------------------------------------------------------


def instructions() -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) with line continuations joined and comments dropped."""
    joined: list[str] = []
    buffer = ""
    for line in DOCKERFILE.read_text().splitlines():
        stripped = line.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        joined.append(buffer + stripped)
        buffer = ""
    assert not buffer
    return [(text.split(None, 1)[0].upper(), text.split(None, 1)[1]) for text in joined]


def stages() -> list[list[tuple[str, str]]]:
    result: list[list[tuple[str, str]]] = []
    for instruction in instructions():
        if instruction[0] == "FROM":
            result.append([])
        if result:
            result[-1].append(instruction)
    return result


def final_stage() -> list[tuple[str, str]]:
    return stages()[-1]


def python_image() -> str:
    [arg] = [a for i, a in instructions() if i == "ARG" and a.startswith("PYTHON_IMAGE=")]
    return arg.split("=", 1)[1]


def compose() -> dict[str, Any]:
    return yaml.safe_load(TEMPLATE_COMPOSE.read_text())


def service(name: str) -> dict[str, Any]:
    return compose()["services"][name]


def env_example() -> dict[str, str]:
    values = {}
    for line in TEMPLATE_ENV.read_text().splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


# ----- Dockerfile: base image, Python, uv, locked dependencies -----------------------------


def test_python_base_is_313_pinned_by_digest_in_every_stage() -> None:
    image = python_image()
    assert re.fullmatch(r"python:3\.13\.\d+-slim-bookworm@sha256:[0-9a-f]{64}", image), image
    assert (ROOT / ".python-version").read_text().strip() == "3.13"
    froms = [a for i, a in instructions() if i == "FROM"]
    assert froms == ["${PYTHON_IMAGE} AS builder", "${PYTHON_IMAGE} AS runtime"]
    text = DOCKERFILE.read_text()
    assert ":latest" not in text and "3.14" not in text


def test_uv_is_pinned_and_hash_verified() -> None:
    text = DOCKERFILE.read_text()
    assert "'uv==0.8.17 \\'" in text
    assert text.count("--hash=sha256:") >= 2 and "--require-hashes" in text
    ci = CI.read_text()
    assert 'version: "0.8.17"' in ci  # the same uv as CI


def test_dependencies_come_from_the_lock_without_the_dev_group() -> None:
    runs = [a for i, a in stages()[0] if i == "RUN"]
    assert "uv sync --locked --no-dev --no-install-project" in runs
    assert not any("pip install" in r and "uv==" not in r for r in runs)
    copies = [a for i, a in stages()[0] if i == "COPY"]
    assert copies == ["pyproject.toml uv.lock .python-version ./"]


def test_multi_stage_runtime_copies_an_explicit_whitelist_only() -> None:
    assert len(stages()) == 2
    copies = [a for i, a in final_stage() if i in ("COPY", "ADD")]
    assert copies == [
        "--from=builder /opt/venv /opt/venv",
        "alembic.ini ./alembic.ini",
        "apps/api/app ./apps/api/app",
        "apps/api/migrations ./apps/api/migrations",
    ]
    for _, arguments in instructions():
        assert not re.search(r"(^|\s)\.\s+\.(/|\s|$)", arguments), arguments  # no COPY . .
    assert not any(i == "ADD" for i, _ in instructions())


def test_runtime_is_a_fixed_non_root_user_without_world_writable_paths() -> None:
    users = [a for i, a in final_stage() if i == "USER"]
    assert users == ["10001:10001"]
    runs = " ".join(a for i, a in final_stage() if i == "RUN")
    assert "--gid 10001" in runs and "--uid 10001" in runs
    text = DOCKERFILE.read_text()
    assert "777" not in text and "chmod" not in text and "chown" not in text


def test_command_is_the_exec_form_deployment_factory_without_migration_or_reload() -> None:
    [cmd] = [a for i, a in final_stage() if i == "CMD"]
    argv = json.loads(cmd)
    all_interfaces = "0.0.0.0"  # noqa: S104 - inside the container; published on 127.0.0.1
    assert argv == ["python", "-m", "uvicorn", "app.bootstrap:create_deployment_app",
                    "--factory", "--app-dir", "/app/apps/api", "--host", all_interfaces,
                    "--port", "8000"]  # fmt: skip
    assert not any(i == "ENTRYPOINT" for i, _ in instructions())
    for _, arguments in final_stage():
        assert "alembic" not in arguments.replace("alembic.ini", ""), arguments
        assert "--reload" not in arguments


def test_healthcheck_uses_the_stdlib_against_health() -> None:
    [check] = [a for i, a in final_stage() if i == "HEALTHCHECK"]
    assert "CMD [" in check
    argv = json.loads(check.split("CMD", 1)[1])
    assert argv[:2] == ["python", "-c"]
    assert "urllib.request" in argv[2] and "http://127.0.0.1:8000/health" in argv[2]
    for _, arguments in instructions():  # comments aside: no curl/wget anywhere
        assert "curl" not in arguments and "wget" not in arguments


def test_no_secret_is_an_image_arg_or_env() -> None:
    for instruction, arguments in instructions():
        if instruction in ("ARG", "ENV", "LABEL"):
            for name in SECRET_NAMES:
                assert name not in arguments.upper(), (instruction, arguments)
    env = " ".join(a for i, a in final_stage() if i == "ENV")
    for required in ("PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1", "HOME=/tmp",
                     "XDG_CACHE_HOME=/tmp/.cache"):  # fmt: skip
        assert required in env


# ----- .dockerignore --------------------------------------------------------------------------


def test_dockerignore_excludes_secrets_and_local_state_but_not_runtime_inputs() -> None:
    patterns = {line.strip() for line in DOCKERIGNORE.read_text().splitlines()
                if line.strip() and not line.startswith("#")}  # fmt: skip
    for required in (".git", ".env", ".env.*", "**/.env", ".venv", "**/__pycache__",
                     ".pytest_cache", ".ruff_cache", ".mypy_cache", ".coverage", "htmlcov",
                     "apps/web/node_modules", "apps/web/.next", "tests", ".idea",
                     ".vscode"):  # fmt: skip
        assert required in patterns, required
    for needed in ("pyproject.toml", "uv.lock", ".python-version", "alembic.ini", "apps",
                   "apps/api", "apps/api/app", "apps/api/migrations"):  # fmt: skip
        assert needed not in patterns, needed
        assert f"!{needed}" not in patterns


# ----- deployment template ---------------------------------------------------------------------


def test_template_has_exactly_postgres_migrate_api_and_web() -> None:
    assert set(compose()["services"]) == {"postgres", "migrate", "api", "web"}
    assert compose()["name"] == "commerce-ai-platform-deployment"
    assert yaml.safe_load(LOCAL_COMPOSE.read_text())["name"] != compose()["name"]


def test_postgres_is_pinned_persistent_private_and_pgvector_enabled() -> None:
    postgres = service("postgres")
    assert postgres["image"].startswith("pgvector/pgvector:0.8.6-pg17@sha256:")
    assert DIGEST.search(postgres["image"])
    assert "ports" not in postgres
    assert "postgres-data:/var/lib/postgresql/data" in postgres["volumes"]
    assert "postgres-data" in compose()["volumes"]
    init = "../../infra/postgres/init:/docker-entrypoint-initdb.d:ro"
    assert init in postgres["volumes"]
    assert (TEMPLATE / "../../infra/postgres/init/01-extensions.sql").resolve().is_file()
    assert postgres["networks"] == ["database"]
    assert "${POSTGRES_PASSWORD:?" in postgres["environment"]["POSTGRES_PASSWORD"]


def test_migration_job_uses_the_same_image_and_runs_only_alembic() -> None:
    migrate, api = service("migrate"), service("api")
    assert migrate["image"] == api["image"] == "${API_IMAGE:?API_IMAGE must be set (see README.md)}"
    assert migrate["command"] == ["alembic", "-c", "/app/alembic.ini", "upgrade", "head"]
    assert migrate["restart"] == "no"
    assert migrate["depends_on"] == {"postgres": {"condition": "service_healthy"}}
    assert set(migrate["environment"]) == {"APP_DATABASE_URL"}  # no model/AgentOS/backend
    assert "ports" not in migrate and migrate["networks"] == ["database"]
    assert migrate["healthcheck"] == {"disable": True}


def test_api_starts_only_after_a_successful_migration() -> None:
    api = service("api")
    assert api["depends_on"] == {
        "postgres": {"condition": "service_healthy"},
        "migrate": {"condition": "service_completed_successfully"},
    }
    assert "command" not in api and "entrypoint" not in api  # the image's own command


@pytest.mark.parametrize("name", ["migrate", "api", "web"])
def test_runtime_containers_are_read_only_and_unprivileged(name) -> None:
    runtime = service(name)
    assert runtime["read_only"] is True
    assert runtime["tmpfs"] == ["/tmp:size=64m,mode=1777"]  # noqa: S108 - the tmpfs spec
    assert runtime["cap_drop"] == ["ALL"]
    assert runtime["security_opt"] == ["no-new-privileges:true"]
    assert "user" not in runtime and "privileged" not in runtime
    assert "volumes" not in runtime  # no writable application mounts


def test_only_web_is_published_on_localhost_and_nothing_uses_host_networking() -> None:
    assert service("web")["ports"] == ["127.0.0.1:${PRODUCT_WEB_PORT:-3000}:3000"]
    published = {name for name, d in compose()["services"].items() if "ports" in d}
    assert published == {"web"}  # the API (and AgentOS inside it) has no host port
    for name, definition in compose()["services"].items():
        assert "network_mode" not in definition, name
        assert "expose" not in definition, name
        for port in definition.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (name, port)
    assert compose()["networks"]["database"] == {"internal": True}
    assert "8000:8000" not in TEMPLATE_COMPOSE.read_text()
    assert "network_mode" not in TEMPLATE_COMPOSE.read_text()
    assert "5432:5432" not in TEMPLATE_COMPOSE.read_text()


def test_api_receives_runtime_configuration_and_required_secrets() -> None:
    environment = service("api")["environment"]
    assert environment["OS_SECURITY_KEY"] == "${OS_SECURITY_KEY:?OS_SECURITY_KEY must be set}"
    assert environment["APP_BUSINESS_BACKEND"].startswith("${APP_BUSINESS_BACKEND:?")
    assert environment["APP_ENVIRONMENT"].startswith("${APP_ENVIRONMENT:?")
    assert environment["AGNO_TELEMETRY"] == "false"
    assert "mock" not in json.dumps(environment)
    assert environment["APP_DATABASE_URL"].endswith("@postgres:5432/${POSTGRES_DB:-platform}")


def test_env_example_holds_placeholders_only_and_stays_fail_closed() -> None:
    values = env_example()
    for secret in ("POSTGRES_PASSWORD", "OS_SECURITY_KEY", "OPENAI_API_KEY",
                   "ANTHROPIC_API_KEY", "APP_COMPANY_ID"):  # fmt: skip
        assert values[secret] == "", secret
    assert values["APP_PRODUCT_API_KEYS"] == "[]"
    assert values["APP_ENVIRONMENT"] == "production"
    assert values["APP_BUSINESS_BACKEND"] == "disabled"
    assert values["APP_PRODUCT_AUTH_MODE"] == "api_key"
    assert values["APP_DEFAULT_MODEL_PROVIDER"] == "disabled"
    assert values["API_IMAGE"] == "commerce-ai-platform-api:0.1.0"
    assert "APP_DATABASE_URL" not in values  # built from the PostgreSQL values
    referenced = set(re.findall(r"\$\{([A-Z_]+)", TEMPLATE_COMPOSE.read_text()))
    assert set(values) <= referenced | {"APP_LOG_LEVEL"}
    assert "=mock" not in TEMPLATE_ENV.read_text()


def test_every_template_variable_is_documented_in_the_env_example() -> None:
    referenced = set(re.findall(r"\$\{([A-Z_]+)", TEMPLATE_COMPOSE.read_text()))
    assert referenced - set(env_example()) <= {"APP_AGNO_DB_SCHEMA"}


# ----- repository scope -------------------------------------------------------------------------


def test_deployments_hold_only_the_generic_template() -> None:
    # An operator's own (git-ignored) .env is not repository content.
    tracked = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "deployments").rglob("*")
                     if p.is_file() and p.name != ".env")  # fmt: skip
    assert tracked == [
        "deployments/README.md",
        "deployments/template/.env.example",
        "deployments/template/README.md",
        "deployments/template/compose.yaml",
    ]


def test_no_proxy_backup_or_orchestrator_was_added() -> None:
    names = {p.name.lower() for p in ROOT.rglob("*") if ".git" not in p.parts
             and "node_modules" not in p.parts and ".venv" not in p.parts}  # fmt: skip
    for forbidden in ("nginx.conf", "traefik.yml", "traefik.yaml", "caddyfile", "haproxy.cfg",
                      "chart.yaml", "kustomization.yaml", "main.tf", "playbook.yml"):  # fmt: skip
        assert forbidden not in names, forbidden
    text = TEMPLATE_COMPOSE.read_text().lower()
    for image in ("nginx", "traefik", "caddy", "haproxy", "redis", "backup"):
        assert image not in text, image


def test_local_development_compose_is_unchanged_in_role() -> None:
    local = yaml.safe_load(LOCAL_COMPOSE.read_text())
    assert LOCAL_COMPOSE.read_text().startswith("# Local development infrastructure only.")
    assert local["services"]["postgres"]["image"] == "pgvector/pgvector:0.8.6-pg17"
    assert set(local["services"]) == {"postgres", "redis"}


def test_ci_keeps_three_jobs_and_runs_the_deployment_smoke_test() -> None:
    workflow = yaml.safe_load(CI.read_text())
    names = [job["name"] for job in workflow["jobs"].values()]
    assert names == ["Backend (Python)", "Frontend (Next.js)", "Infrastructure (Docker Compose)"]
    steps = workflow["jobs"]["infrastructure"]["steps"]
    commands = [step.get("run", "") for step in steps]
    assert "docker compose config --quiet" in commands  # local development compose
    assert any("docker build -f apps/api/Dockerfile" in c for c in commands)
    assert any("docker build -f apps/web/Dockerfile" in c for c in commands)
    assert any(".github/scripts/deployment-smoke.sh commerce-ai-platform-api:ci "
               "commerce-ai-platform-web:ci" in c for c in commands)  # fmt: skip
    teardown = [s for s in steps if "cap-deploy-smoke down -v" in s.get("run", "")]
    assert teardown and teardown[0]["if"] == "always()"
    assert SMOKE.stat().st_mode & 0o111


def test_smoke_script_covers_the_packaging_contract() -> None:
    script = SMOKE.read_text()
    for check in ("id -u", "/rootfs-write-probe", "/tmp/tmpfs-write-probe",  # noqa: S108
                  "raise SystemExit(3)",
                  "write_commands", "audit_events", '"ready"', "/agents", "503",
                  "docker history --no-trunc", "docker image inspect", "docker export",
                  "docker stop", "Finished server process", "APP_ENVIRONMENT=production",
                  "no business backend is available for staging/production deployments",
                  '"pytest"', '"ruff"', "trap cleanup EXIT", "down -v", "10001:10001",
                  "10002:10002", "/api/product/health", "compose port api 8000",
                  "never_started", "http://api:8000", "api_internal", "os-key",
                  "/api/product/agents", "/api/v1/operations/runs", "OPS_MESSAGE",
                  "enable_ip_masquerade", "Operations Console"):  # fmt: skip
        assert check in script, check
    # CI-only throwaway values: generated per run, never literal secrets in the repository.
    assert "APP_DEFAULT_MODEL_PROVIDER=disabled" in script
    assert "OPENAI_API_KEY=\n" in script and "ANTHROPIC_API_KEY=\n" in script
    assert "set -euo pipefail" in script


def test_documentation_states_the_current_limitations() -> None:
    def flat(path: Path) -> str:
        return " ".join(path.read_text().replace("*", "").split())

    template = flat(TEMPLATE / "README.md")
    overview = flat(ROOT / "deployments" / "README.md")
    readme = flat(ROOT / "README.md")
    for phrase in ("No real business backend exists yet", "must never be directly internet-exposed",
                   "forbidden for a real deployment", "backup and restore automation",
                   "reverse proxy", "UID 10001 / GID 10001", "The API never migrates itself",
                   "127.0.0.1", "never commit it", "docker build -f apps/api/Dockerfile",
                   "docker build -f apps/web/Dockerfile", "UID 10002 / GID 10002",
                   "The API is not published on the host", "http://api:8000",
                   "a failed migration keeps the API and the Web down"):  # fmt: skip
        assert phrase in template, phrase
    assert "LOCAL DEVELOPMENT infrastructure only" in overview
    assert "one company" in overview.lower()
    assert "not production-ready end to end" in readme
    assert "docker build -f apps/api/Dockerfile -t commerce-ai-platform-api:0.1.0 ." in readme
    assert "docker build -f apps/web/Dockerfile -t commerce-ai-platform-web:0.1.0 ." in readme


def test_no_secret_values_in_packaging_files() -> None:
    for path in (DOCKERFILE, DOCKERIGNORE, TEMPLATE_COMPOSE, TEMPLATE_ENV, SMOKE):
        text = path.read_text()
        assert not re.search(
            r"(?i)(password|security_key|api_key)=[^\s$\"'{]",
            text.replace("APP_PRODUCT_API_KEYS=[]", ""),
        ), path.name
        assert not re.search(r"\b(sk-[A-Za-z0-9]{10,}|ghp_\w{20,}|AKIA[0-9A-Z]{16})", text)


# ----- Task 028: the Web service and the private Product network --------------------------------


def test_web_starts_after_a_healthy_api_with_only_its_private_origin() -> None:
    web = service("web")
    assert web["image"] == "${WEB_IMAGE:?WEB_IMAGE must be set (see README.md)}"
    assert web["depends_on"] == {"api": {"condition": "service_healthy"}}
    # Product-owned wiring, not an operator variable; no other setting or secret.
    assert web["environment"] == {"PRODUCT_API_ORIGIN": "http://api:8000"}
    assert "command" not in web and "entrypoint" not in web
    assert web["restart"] == "unless-stopped"


def test_private_network_topology() -> None:
    networks = compose()["networks"]
    assert networks["product"] == {"internal": True}
    assert networks["web-publish"] == {
        "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"}
    }
    assert service("postgres")["networks"] == ["database"]
    assert service("migrate")["networks"] == ["database"]
    assert service("api")["networks"] == ["database", "product", "egress"]
    assert service("web")["networks"] == ["product", "web-publish"]  # never database/egress


def test_env_example_exposes_the_web_port_and_images_but_not_the_origin() -> None:
    values = env_example()
    assert values["WEB_IMAGE"] == "commerce-ai-platform-web:0.1.0"
    assert values["PRODUCT_WEB_PORT"] == "3000"
    for removed in ("PRODUCT_API_PORT", "PRODUCT_API_ORIGIN"):
        assert removed not in values, removed
        assert removed not in TEMPLATE_COMPOSE.read_text().replace(
            "PRODUCT_API_ORIGIN: http://api:8000", ""
        ), removed
