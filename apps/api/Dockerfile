# Product API deployment image (runtime + Product migrations).
#
#   docker build -f apps/api/Dockerfile -t commerce-ai-platform-api:0.1.0 .
#
# The build context is the REPOSITORY ROOT. The same image runs the API (default
# command) and the explicit migration job (`alembic -c /app/alembic.ini upgrade head`);
# the API never migrates at startup. No secret is an ARG or ENV here: database URL,
# AgentOS key, Product API key hashes, model keys and backend inputs are supplied at
# container runtime only.

# Python 3.13 (matches .python-version / requires-python), pinned by manifest digest.
ARG PYTHON_IMAGE=python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6

# ----- builder: locked runtime dependencies only ---------------------------------------
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.13 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# uv, pinned exactly (same version as CI) and verified by hash (x86_64 / aarch64 wheels).
RUN printf '%s\n' \
      'uv==0.8.17 \' \
      '  --hash=sha256:b6d30d02fb65193309fc12a20f9e1a9fab67f469d3e487a254ca1145fd06788f \' \
      '  --hash=sha256:84d56ae50ca71aec032577adf9737974554a82a94e52cee57722745656c1d383 \' \
      '  --hash=sha256:3941cecd9a6a46d3d4505753912c9cf3e8ae5eea30b9d0813f3656210f8c5d01' \
      > /tmp/uv-requirements.txt \
 && python -m pip install --require-hashes --only-binary=:all: -r /tmp/uv-requirements.txt \
 && rm /tmp/uv-requirements.txt

WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
# Exactly the locked versions (fails if uv.lock is stale); the dev group is excluded.
RUN uv sync --locked --no-dev --no-install-project

# ----- runtime ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    VIRTUAL_ENV=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

# Fixed, unprivileged runtime identity (documented in deployments/template/README.md).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin app

WORKDIR /app
# Explicit whitelist: never the repository root. Files stay root-owned and read-only
# for the runtime user; nothing here needs to be writable.
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./alembic.ini
COPY apps/api/app ./apps/api/app
COPY apps/api/migrations ./apps/api/migrations
RUN python -m compileall -q /app/apps/api

USER 10001:10001
EXPOSE 8000

# Product READINESS (Task 039), stdlib only (no curl): unauthenticated /health/ready is 200
# only when the lifespan started, the Agent runtime is attached, PostgreSQL is reachable and
# the Product schema is at the expected revision (a 503 raises: unhealthy). /health/live
# stays 200 while the process is alive.
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
  CMD ["python", "-c", "import sys, urllib.request as r; sys.exit(0 if r.build_opener(r.ProxyHandler({})).open('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"]

# Exec form: uvicorn is PID 1 and receives SIGTERM directly. No --reload, no migration.
CMD ["python", "-m", "uvicorn", "app.bootstrap:create_deployment_app", "--factory", \
     "--app-dir", "/app/apps/api", "--host", "0.0.0.0", "--port", "8000"]
