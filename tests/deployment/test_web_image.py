"""Task 028: static guards for the Product Web image (apps/web/Dockerfile).

Runtime behaviour (build, non-root, read-only root, BFF -> private API, isolation,
secret non-leak, shutdown) is proven by .github/scripts/deployment-smoke.sh in the
Infrastructure CI job; these tests pin the reviewed image design.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
DOCKERFILE = WEB / "Dockerfile"
SECRET_NAMES = ("PASSWORD", "SECURITY_KEY", "API_KEY", "DATABASE_URL", "TOKEN", "SECRET",
                "PRODUCT_API_ORIGIN")  # fmt: skip


def instructions() -> list[tuple[str, str]]:
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


def stages() -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    current = None
    for instruction, arguments in instructions():
        if instruction == "FROM":
            current = arguments.split(" AS ")[1]
            result[current] = []
        if current:
            result[current].append((instruction, arguments))
    return result


def test_node_22_is_pinned_by_exact_version_and_digest_in_every_stage() -> None:
    [arg] = [a for i, a in instructions() if i == "ARG" and a.startswith("NODE_IMAGE=")]
    image = arg.split("=", 1)[1]
    assert re.fullmatch(r"node:22\.\d+\.\d+-bookworm-slim@sha256:[0-9a-f]{64}", image), image
    froms = [a for i, a in instructions() if i == "FROM"]
    assert froms == ["${NODE_IMAGE} AS dependencies", "${NODE_IMAGE} AS builder",
                     "${NODE_IMAGE} AS runtime"]  # fmt: skip
    assert ":latest" not in DOCKERFILE.read_text()


def test_dependencies_come_from_the_lockfile_only() -> None:
    dependencies = stages()["dependencies"]
    copies = [a for i, a in dependencies if i == "COPY"]
    assert copies == ["apps/web/package.json apps/web/package-lock.json apps/web/.npmrc ./"]
    runs = [a for i, a in dependencies if i == "RUN"]
    assert runs == ["npm ci"]
    for _, arguments in instructions():
        assert "npm install" not in arguments and "npm i " not in arguments


def test_builder_copies_only_the_web_sources_it_builds() -> None:
    copies = [a for i, a in stages()["builder"] if i == "COPY"]
    assert copies == [
        "--from=dependencies /src/node_modules ./node_modules",
        "apps/web/package.json apps/web/package-lock.json apps/web/next.config.ts "
        "apps/web/tsconfig.json ./",
        "apps/web/app ./app",
        "apps/web/components ./components",
        "apps/web/lib ./lib",
    ]
    assert [a for i, a in stages()["builder"] if i == "RUN"] == ["npm run build"]


def test_runtime_is_the_standalone_server_only() -> None:
    runtime = stages()["runtime"]
    copies = [a for i, a in runtime if i in ("COPY", "ADD")]
    assert copies == [
        "--from=builder /src/.next/standalone ./",
        "--from=builder /src/.next/static ./.next/static",
    ]
    for _, arguments in instructions():
        assert not re.search(r"(^|\s)\.\s+\.(/|\s|$)", arguments), arguments  # no COPY . .
    assert not any(i == "ADD" for i, _ in instructions())


def test_runtime_identity_environment_and_command() -> None:
    runtime = stages()["runtime"]
    assert [a for i, a in runtime if i == "USER"] == ["10002:10002"]
    runs = " ".join(a for i, a in runtime if i == "RUN")
    assert "--gid 10002" in runs and "--uid 10002" in runs
    env = " ".join(a for i, a in runtime if i == "ENV")
    for required in ("NODE_ENV=production", "NEXT_TELEMETRY_DISABLED=1", "HOSTNAME=0.0.0.0",
                     "PORT=3000", "HOME=/tmp"):  # fmt: skip
        assert required in env, required
    assert json.loads([a for i, a in runtime if i == "CMD"][0]) == ["node", "server.js"]
    assert [a for i, a in runtime if i == "ENTRYPOINT"] == ["[]"]  # Node is PID 1
    text = DOCKERFILE.read_text()
    assert "chmod" not in text and "chown" not in text and "777" not in text
    assert "next dev" not in text and "npm start" not in text
    for stage in ("dependencies", "builder"):
        stage_env = " ".join(a for i, a in stages()[stage] if i == "ENV")
        assert "NEXT_TELEMETRY_DISABLED=1" in stage_env, stage


def test_healthcheck_goes_through_the_bff_with_node_builtins() -> None:
    [check] = [a for i, a in stages()["runtime"] if i == "HEALTHCHECK"]
    argv = json.loads(check.split("CMD", 1)[1])
    assert argv[:2] == ["node", "-e"]
    assert "fetch('http://127.0.0.1:3000/api/product/health'" in argv[2]
    for _, arguments in instructions():
        assert "curl" not in arguments and "wget" not in arguments


def test_no_secret_or_origin_is_an_image_arg_env_or_label() -> None:
    for instruction, arguments in instructions():
        if instruction in ("ARG", "ENV", "LABEL"):
            for name in SECRET_NAMES:
                assert name not in arguments.upper(), (instruction, arguments)
    for _, arguments in instructions():  # comments aside
        assert "api:8000" not in arguments


def test_next_config_is_standalone_and_keeps_the_hardening() -> None:
    config = (WEB / "next.config.ts").read_text()
    assert 'output: "standalone"' in config
    assert "reactStrictMode: true" in config and "poweredByHeader: false" in config


def test_build_context_ignores_local_state_and_secrets() -> None:
    patterns = {line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()
                if line.strip() and not line.startswith("#")}  # fmt: skip
    for required in ("apps/web/node_modules", "apps/web/.next", "**/.env", "**/.env.*", ".git",
                     "tests"):  # fmt: skip
        assert required in patterns, required
    for needed in ("apps/web", "apps/web/app", "apps/web/components", "apps/web/lib",
                   "apps/web/package-lock.json"):  # fmt: skip
        assert needed not in patterns, needed
