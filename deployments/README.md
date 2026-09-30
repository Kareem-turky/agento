# deployments/

Product deployment packaging. **One installation = one company**, with its own
physically isolated database: nothing here is shared between companies, and there is no
central control plane or shared SaaS deployment.

| Path | What it is |
|---|---|
| `../docker-compose.yml` (repository root) | LOCAL DEVELOPMENT infrastructure only (PostgreSQL/Redis on localhost). Never a deployment. |
| `../apps/api/Dockerfile` | The immutable Product API image: API runtime **and** Product migrations. |
| `../apps/web/Dockerfile` | The immutable Product Web image: the Operations Console and its same-origin BFF. |
| `template/` | The generic deployment template (Docker Compose): PostgreSQL → explicit migration job → private Product API → Product Web, the only host-facing service (127.0.0.1). See [`template/README.md`](template/README.md). |

Topology: Browser → localhost Web (Operations Console + BFF) → private Product network
→ Product API (and AgentOS inside it, never host-published) → private PostgreSQL.

There are deliberately no per-company or per-customer directories here: an installation
is configured through its own `.env` (outside git), not through repository content.

Current status (be explicit about it):

- **No real business backend exists yet.** Staging/production refuse to compose the
  Product (fail closed). `mock` is local/test only and is refused in deployments.
- The Product Web is published on localhost only; the API and AgentOS are private.
  Remote/public ingress still requires a later, reviewed TLS / reverse-proxy design.
- The Product API key remains browser-memory-only (no login or server session).
- Reverse proxy / TLS termination, backup/restore automation, image registry
  publication, orchestrators (Kubernetes/Helm/Terraform) and real provider installation
  are **not implemented yet**.
