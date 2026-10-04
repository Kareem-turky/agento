# Product Core release baseline (historical record)

**THIS IS HISTORICAL RELEASE EVIDENCE.** It records the exact objective facts of the
Agento Product Core (provider-free) release. It is **not** a constraint that future `main`
stays byte-identical to the release. Later reviewed tasks may add production files,
migrations, Agents, Skills, Tasks, Workflows, integrations and backends. What `main` must
keep satisfying is the continuously-run Product regression acceptance gate: see
[`PRODUCT_REGRESSION_ACCEPTANCE.md`](PRODUCT_REGRESSION_ACCEPTANCE.md).

The release decision and the acceptance record are
[`PRODUCT_CORE_READY.md`](PRODUCT_CORE_READY.md) and
[`PRODUCT_CORE_ACCEPTANCE.md`](PRODUCT_CORE_ACCEPTANCE.md). Both describe the Product Core
release at the release commit below and are kept unchanged. Their limitations, such as
"provider-free" and "the next phase is Real Integrations", describe the release and are
not current limitations.

```text
release_commit:        a69fb36ffe5b50957425e7a40df89aeaa63c8166
release_tree:          ea84aa981beedb24f5c6f5e96b603bd9cdaaf270
task_040_base:         82be2ec1896ac7a55ab57ace7d30570bc6a03557
push_main_ci_run:      37158103014
production_digest:     be4fc7038373c6dd873812fe95a52c6895f2d2e2bcd793ad7d91d7a85df49c18
production_file_count: 335
migrations:            0001-0008
migration_head:        0008
agents:                {operations}
skills:                {operations.order_inspection, operations.daily_analysis, operations.ticket_escalation}
tasks:                 {operations.inspect_order, operations.analyze_daily, operations.escalate_issue}
workflows:             {operations.daily_report}
integration_catalog:   empty
messaging_registry:    empty
backend_registry:      {mock}
ticket_create_risk:    LOW_RISK_WRITE
agno_pin:              agno[os,postgres,openai,anthropic]==3.0.11
```

## What the release values mean

- **Production digest and file count.** SHA-256 over `"<relative path>\0<sha256 of the
  file>\n"` for every pinned production file, sorted by path. The pinned files are:
  - the trees `apps/api/app`, `apps/api/migrations`, `apps/web/app`,
    `apps/web/components`, `apps/web/lib` and `deployments/template`;
  - the files `alembic.ini`, `pyproject.toml`, `uv.lock`, `scripts/demo.sh`,
    `apps/api/Dockerfile`, `apps/api/scripts/hash_product_api_key.py`,
    `apps/web/Dockerfile`, `apps/web/package.json`, `apps/web/package-lock.json`,
    `apps/web/next.config.ts` and `apps/web/tsconfig.json`.

  The digest was computed on a clean worktree. Release, base and tree produce the same
  digest, because Task 040 changed no production file.
- **Catalogs and registries.** These are the production defaults at release
  (`build_default_*`). The integration catalog and messaging registry were empty, and the
  only business backend was the deterministic `mock`.

## Re-verifying the release digest

Run this on a clean checkout of the release commit. It uses the algorithm kept in
`tests/acceptance/test_product_core_architecture.py`:

```bash
git worktree add /tmp/agento-release a69fb36ffe5b50957425e7a40df89aeaa63c8166
cd /tmp/agento-release
uv run python -c "import runpy; ns = runpy.run_path('tests/acceptance/test_product_core_architecture.py'); print(len(ns['production_files']()), ns['production_digest']())"
# expected: 335 be4fc7038373c6dd873812fe95a52c6895f2d2e2bcd793ad7d91d7a85df49c18
```
