# Product Web image: the Operations Console and its same-origin Product BFF.
#
#   docker build -f apps/web/Dockerfile -t commerce-ai-platform-web:0.1.0 .
#
# The build context is the REPOSITORY ROOT. The runtime is Next's standalone server
# (server.js + the traced runtime dependencies + static assets) running as a fixed
# non-root user. Nothing sensitive is an ARG or ENV here: PRODUCT_API_ORIGIN is a
# deployment runtime value (the template fixes it to the private http://api:8000), and
# the Product API key only ever exists in a browser tab's memory.

# Node 22 (exact patch), pinned by manifest digest; the same image for every stage.
ARG NODE_IMAGE=node:22.22.2-bookworm-slim@sha256:9f6d5975c7dca860947d3915877f85607946403fc55349f39b4bc3688448bb6e

# ----- dependencies: exactly package-lock.json ------------------------------------------
FROM ${NODE_IMAGE} AS dependencies
ENV NEXT_TELEMETRY_DISABLED=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false
WORKDIR /src
COPY apps/web/package.json apps/web/package-lock.json apps/web/.npmrc ./
RUN npm ci

# ----- builder: the production build (standalone output) ---------------------------------
FROM ${NODE_IMAGE} AS builder
ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
WORKDIR /src
COPY --from=dependencies /src/node_modules ./node_modules
COPY apps/web/package.json apps/web/package-lock.json apps/web/next.config.ts apps/web/tsconfig.json ./
COPY apps/web/app ./app
COPY apps/web/components ./components
COPY apps/web/lib ./lib
RUN npm run build

# ----- runtime ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    HOME=/tmp

# Fixed, unprivileged runtime identity (the API image uses 10001).
RUN groupadd --system --gid 10002 web \
 && useradd --system --uid 10002 --gid web --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin web

WORKDIR /app
# Explicit whitelist: the standalone server and its static assets only. Files stay
# root-owned and read-only for the runtime user.
COPY --from=builder /src/.next/standalone ./
COPY --from=builder /src/.next/static ./.next/static

USER 10002:10002
EXPOSE 3000

# Web -> same-origin BFF -> private Product API /health, with Node built-ins only.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/api/product/health',{signal:AbortSignal.timeout(4000)}).then(r=>process.exit(r.status===200?0:1),()=>process.exit(1))"]

# Node is PID 1 and receives SIGTERM directly (no entrypoint script, no shell).
ENTRYPOINT []
CMD ["node", "server.js"]
