# deployments/

Product deployment packaging. **One installation = one company**, with its own
physically isolated database: nothing here is shared between companies, and there is no
central control plane or shared SaaS deployment.

| Path | What it is |
|---|---|
| `../docker-compose.yml` (repository root) | LOCAL DEVELOPMENT infrastructure only (PostgreSQL/Redis on localhost). Never a deployment. |
| `../apps/api/Dockerfile` | The immutable Product API image: API runtime **and** Product migrations. |
| `template/` | The generic deployment template (Docker Compose): PostgreSQL → explicit migration job → Product API. See [`template/README.md`](template/README.md). |

There are deliberately no per-company or per-customer directories here: an installation
is configured through its own `.env` (outside git), not through repository content.

Current status (be explicit about it):

- **No real business backend exists yet.** Staging/production refuse to compose the
  Product (fail closed). `mock` is local/test only and is refused in deployments.
- Reverse proxy / TLS termination, backup/restore automation, image registry
  publication, orchestrators (Kubernetes/Helm/Terraform) and real provider installation
  are **not implemented yet**.
