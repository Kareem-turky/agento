# apps/api

FastAPI application. Import package: `app` (run with `--app-dir apps/api`).

- `app/config.py` — structured settings (`APP_*` environment variables, optional `.env`).
- `app/main.py` — application factory and `GET /health`.
- `app/runtime/` — registration of the Agno agent runtime into the application lifecycle.

No business endpoints live here yet.
