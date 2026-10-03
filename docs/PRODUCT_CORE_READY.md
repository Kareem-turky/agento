# Product Core Ready: release decision

Status rule: **candidate** until this Task 040 commit is on `main` and push-to-main CI on
that exact commit is green; **final** thereafter. No follow-up documentation commit is
needed: the gate, not this text, decides.

| Field | Value |
|---|---|
| Decision | Agento **Product Core Ready (provider-free)** |
| Status rule | **Before merge** (Task 040 on its pull-request branch, not yet on `main`): Task 040 acceptance candidate. **After the gate**: once Task 040 is independently reviewed and merged to `main`, and push-to-main CI on that exact merge commit is green (all required gates below), the decision is final: Agento Product Core Ready (provider-free). The status follows from those objective facts; no merge commit is named here and nothing is edited after merge |
| Base requirement | Built on `main` `82be2ec1896ac7a55ab57ace7d30570bc6a03557`: Task 039 plus the pre-Core-Ready Operations safe-validation hardening (#44). Task 040 changes no production runtime code, migration (head `0008`), dependency or deployment runtime file (guarded by `tests/acceptance/test_product_core_architecture.py`) |
| Qualification | **provider-free**: proven with test-only generic adapters on Product-owned contracts. The production integration catalog and messaging registry are empty |
| Required gates | Backend: **Product Core acceptance** (`uv run pytest tests/acceptance -q`, real PostgreSQL, no external calls), the full unit and PostgreSQL suite, MVP business acceptance, lint, format. Frontend: typecheck, build, session-context tests, BFF proxy smoke. Infrastructure: deployment smoke (Task 028 and Task 039 readiness, outage and recovery), backup and restore drill (Task 039), demo smoke (Task 030). Locally: the Control Center browser suite (Task 038) |
| Remaining boundary | **Real Integrations**: a reviewed real business backend or provider (staging and production refuse to start without one), and reviewed public deployment (reverse proxy, TLS termination, Internet ingress) |

The acceptance record, matrix, limitations and the issue resolved before final acceptance are in
[`PRODUCT_CORE_ACCEPTANCE.md`](PRODUCT_CORE_ACCEPTANCE.md).
