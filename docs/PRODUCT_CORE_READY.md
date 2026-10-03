# Product Core Ready: release decision

Status: candidate

| Field | Value |
|---|---|
| Decision | Agento **Product Core Ready (provider-free)** |
| Status | **candidate**: "Task 040 acceptance candidate". The decision becomes final only after Task 040 is independently reviewed and merged, and push-to-main CI on the exact merge commit is green, with the final invariants re-verified on `main` |
| Base requirement | Built on the Task 039 `main`, `c913d73c32d2c8c942d5e5ec33f06620b4a42a49`. Task 040 changes no production runtime code, migration (head `0008`), dependency or deployment runtime file (guarded by `tests/acceptance/test_product_core_architecture.py`) |
| Qualification | **provider-free**: proven with test-only generic adapters on Product-owned contracts. The production integration catalog and messaging registry are empty |
| Required gates | Backend: **Product Core acceptance** (`uv run pytest tests/acceptance -q`, real PostgreSQL, no external calls), the full unit and PostgreSQL suite, MVP business acceptance, lint, format. Frontend: typecheck, build, session-context tests, BFF proxy smoke. Infrastructure: deployment smoke (Task 028 and Task 039 readiness, outage and recovery), backup and restore drill (Task 039), demo smoke (Task 030). Locally: the Control Center browser suite (Task 038) |
| Remaining boundary | **Real Integrations**: a reviewed real business backend or provider (staging and production refuse to start without one), and reviewed public deployment (reverse proxy, TLS termination, Internet ingress) |

The acceptance record, matrix, limitations and findings are in
[`PRODUCT_CORE_ACCEPTANCE.md`](PRODUCT_CORE_ACCEPTANCE.md).
