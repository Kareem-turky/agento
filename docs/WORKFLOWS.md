# Product Workflows (Workflow Platform v1)

> Status: **deterministic, durable, in-process Product Workflows.** One real Workflow is
> installed: `operations.daily_report`, the existing daily operations analysis. There is
> no Workflow builder, no public run endpoint and no background worker.

## The rule

- If a process is **deterministic**, it is a **Workflow**.
- If it needs **reasoning or judgment**, an **Agent** may reason around a Workflow or
  invoke it through its existing tools.

A model never decides deterministic execution order, retries, verification or recovery.
The Workflow Platform makes **zero** model calls: no LLM reasoning and no prompt
execution. An Agent may explain a Workflow's result afterwards.

## Six separate concepts

| Concept | Answers | Where |
| --- | --- | --- |
| **Agent** | WHO reasons | `app/agent_management/`, runtime Agent in `app/agents/` |
| **Skill** | WHAT capability an Agent has | `app/agent_management/skills.py` |
| **Task** | WHAT job is requested | `app/agent_management/tasks.py` |
| **Tool** | HOW one atomic read or write is invoked | `app/agents/operations_tools.py` |
| **Workflow definition** | WHICH deterministic Steps run, in which order | `app/workflow_management/` |
| **Business Workflow implementation** | the business logic of one process | `app/workflows/` (for example `DailyOperationsWorkflow`) |

The platform **orchestrates** a business implementation. It never replaces its
calculations.

## Definitions and catalog

`WorkflowDefinition` and `WorkflowStepDefinition` are immutable Product metadata.

**A Workflow definition has:**

- `workflow_id`, name, description, category;
- `version` and lifecycle;
- safe input-field metadata;
- an **ordered** tuple of Steps. v1 is strictly sequential: there is no DAG.

**A Step definition has:**

- `step_id` and `handler_id`;
- side-effect class: `read_only` or `governed_write`;
- `timeout_seconds` (1–300);
- `max_attempts` (1–5). A `governed_write` Step must be **1**;
- checkpoint policy: `none` or `state`.

A definition names no Python class, module, import path, callable, provider, credential
or expression.

`ProductWorkflowCatalog` is the explicit, immutable list of Workflows:

- static registrations only;
- duplicates are rejected;
- no dynamic import, entry point, directory scan, database row, remote registration,
  HTTP mutation or model-generated definition.

Definitions are **never stored**: there is no definitions table.

### The production catalog

The production catalog contains exactly one Workflow, `operations.daily_report` (version 1):

| Step | Handler | Side effect | Timeout | Attempts | Checkpoint |
| --- | --- | --- | --- | --- | --- |
| `compute_daily_report` | `operations.daily_report.compute` | `read_only` | 30 s | 1 | `none` |

Its single input is the optional `business_date`. The single attempt keeps the report's
existing behaviour: a failed report is answered as unavailable and never silently
re-read. No placeholder Workflow exists for finance, marketing, CX, returns, inventory,
suppliers, COD or messaging.

## Trusted handler registry

`WorkflowRuntimeRegistry` is built by trusted composition code from explicit
`WorkflowRuntimeRegistration`s. Each registration binds a `workflow_id` to:

- a frozen, strict input model;
- its Step handlers.

The registry **fails closed** at composition time when:

- a registration names an unknown Workflow;
- a Step has no handler;
- a handler is unused or duplicated;
- a handler's side effect or checkpoint production does not match its Step;
- a governed write handler overrides the coordinator path.

No handler comes from a database, an import path, user input or the network.

**Handler contract** (`app/workflow_management/handlers.py`):

- `ReadOnlyStepHandler`: implements `execute` and `verify`.
- `GovernedWriteStepHandler`: implements only `action()`, which returns the governed
  action and its raw parameters. Its `execute` and `verify` are **final**:
  - the effect always goes through the existing `ExecutionCoordinator` (permission,
    policy, risk, approval, execution, verification, audit);
  - only a `VERIFIED` `ActionRun` with a complete audit completes the Step.

Handlers receive an immutable `WorkflowStepContext`:

- run id, workflow id and version, step id, attempt and request id;
- the trusted actor, company and store;
- the trusted request and scope.

Identity and scope always come from trusted Product context, never from Workflow input.

## Execution lifecycle

```
execute(workflow_id, trusted request, trusted scope, input)
  -> definition + registration          else unavailable (nothing created)
  -> trusted actor, scope in its company else access denied (nothing created)
  -> typed input validation             else input invalid (nothing created)
  -> run "pending" + workflow_requested                      (one transaction)
  -> claim -> "running" + workflow_started
  -> for each Step, in order:
       start attempt (renews the claim) + step_started
       asyncio timeout: validate -> execute -> verify
       typed, size-bounded checkpoint
       finish attempt + events (+ terminal run state)        (one transaction)
  -> WorkflowRunResult (status, safe failure code, ephemeral Step outputs)
```

Validated input is executed exactly as a resumed run will see it: the stored JSON form.

### Run states

```
pending -> running -> succeeded | failed | requires_human | awaiting_approval
pending -> failed                 (stopped before any Step ran)
running -> running                (progress, or a recovered expired claim)
```

### Step attempt states

```
running -> succeeded | failed | timed_out | requires_human | awaiting_approval
```

Every other transition is rejected. Terminal states never change.

## Retries, timeouts and verification

- **Retries** are immediate, deterministic and bounded by `max_attempts`.
  - Every attempt is its own durable row: a failed attempt is never overwritten.
  - A read-only Step is retried after an unexpected error or a timeout.
  - A denial, a confirmed failure, a verification failure or an invalid checkpoint is
    terminal.
  - There is no backoff or scheduling infrastructure.
- **Timeouts** are real `asyncio.timeout`s around `execute` and `verify`.
- **Verification is mandatory.** A Step is `succeeded` only when `verify` returns true. A
  Step never succeeds merely because `execute` returned.

## Write safety

A `governed_write` Step:

- runs **once**;
- is never retried automatically, because no Product idempotency proof exists for it in
  v1.

| Outcome | Run status |
| --- | --- |
| Verified `ActionRun` | Step succeeded |
| Approval required | `awaiting_approval` |
| Governance denial | `failed` (`access_denied`) |
| Confirmed no effect | `failed` (`step_execution_failed`) |
| Uncertain execution, timeout, error, failed verification, invalid checkpoint | `requires_human` |
| Interrupted mid-write (found on resume) | `requires_human` (`executor_lost`) |

No downstream Step runs after `requires_human` or `awaiting_approval`. The platform is
orchestration, never authorization: every write still goes through Product governance
and the `ExecutionCoordinator`.

### Approval boundary

v1 only recognizes `awaiting_approval` and stops. It never auto-approves, fakes a
continuation or resumes such a run. Human Approval comes later (Task 036).

## Checkpoints and data ownership

A checkpoint is the **minimal** state a later Step needs. It is:

- produced by trusted Step code as an instance of the handler's declared frozen model;
- re-validated through that model;
- at most 8 KiB;
- stored only on a succeeded attempt.

It never contains credentials, provider tokens or payloads, prompts or model output.

**The run input** is stored only as the validated typed input (at most 4 KiB) plus a
SHA-256 fingerprint, so an interrupted run can resume.

**The inspection API** never returns the input, a checkpoint or a Step output.

**External systems stay the source of truth.** Workflow persistence is not permission to
mirror orders, shipments, customers, inventory or provider payloads.

**`operations.daily_report` persists only execution metadata.** The report is the Step's
**ephemeral** output, handed back to the caller in memory. It is never stored, and the
Step checkpoints nothing.

## Durable state (migration `0005`)

| Table | Holds |
| --- | --- |
| `product.workflow_runs` | One row per run: workflow id/version, request id, trusted company/actor/channel/store, status, current Step, safe failure code, validated input + fingerprint, the execution claim, timestamps |
| `product.workflow_step_runs` | One row per Step **attempt**, keyed by `(run_id, step_id, attempt)`: handler id, status, safe failure code, verification code, checkpoint, timestamps |
| `product.workflow_events` | Append-only lifecycle events, keyed by `(run_id, sequence)`. A trigger refuses UPDATE and DELETE |

- CHECK constraints freeze every vocabulary and the size limits.
- Downgrading to `0004` removes only these tables, the trigger and its function.
  `write_commands`, `audit_events`, `integration_connections` and `agent_configurations`
  are kept with their rows.

**Event vocabulary:**

- `workflow_requested`, `workflow_started`, `workflow_resumed`
- `step_started`, `step_succeeded`, `step_failed`, `step_timed_out`, `step_retrying`
- `workflow_succeeded`, `workflow_failed`, `workflow_requires_human`,
  `workflow_awaiting_approval`

Events carry safe metadata only: step id, attempt, status, failure code and time.

`workflow_events` does **not** replace `product.audit_events`. Governed writes are still
audited and verified by the `ExecutionCoordinator`, and a Workflow event is never proof
that a business side effect happened.

### Failure codes

`input_invalid`, `handler_not_registered`, `access_denied`, `step_timeout`,
`step_execution_failed`, `step_verification_failed`, `step_outcome_uncertain`,
`checkpoint_invalid`, `retry_exhausted`, `executor_lost`, `approval_required`,
`workflow_unavailable`, `lease_conflict`.

Raw exception messages are never persisted, logged or returned.

## Recovery and concurrency

**Crash and restart.** `WorkflowEngine.resume(request, run_id)` is an internal Product
service method (company-scoped). It continues an interrupted run from durable state:

- completed Steps are **never executed again**: their checkpoints are reloaded and
  re-validated;
- an attempt left `running` by a lost executor is closed first:
  - `executor_lost` for a read-only Step (another attempt may follow, within
    `max_attempts`);
  - `requires_human` for a governed write;
- a stored input or checkpoint that no longer validates fails the run closed;
- terminal runs, runs awaiting approval and runs of a changed definition version are not
  resumable.

**Execution claims.** A run is progressed only by the executor holding its claim:

- the claim is a fresh random token plus an expiry (`lease_owner`, `lease_expires_at`);
- `claim` is a compare-and-set update that succeeds only on an active run whose claim is
  free or **expired**;
- every later change is a compare-and-set on the token **and** the expected status, in
  one short transaction that locks the run row;
- a stale executor whose claim was taken over writes nothing (`lease_conflict`);
- every Step attempt renews the claim to `timeout + margin`;
- no transaction is ever held while Step code runs;
- time is injectable for tests.

**Durability is never optional.** If a run cannot be created, an attempt or checkpoint
stored, a required event appended or a terminal state written, execution stops
(`WorkflowUnavailableError`). The run stays resumable.

## No background worker

Execution is synchronous and in-process: the caller awaits the run.

- There is no Celery, Temporal, Kafka, Redis queue, scheduler, cron or worker fleet.
- No detached task outlives the request.

Durable state provides recovery semantics.

## Operations Daily integration

```
GET /api/v1/operations/reports/daily           (route: unchanged contract)
Operations Agent tool get_daily_operations_report (Agent files: unchanged)
  -> DailyOperationsReportService               (unchanged contract)
  -> WorkflowBackedDailyOperationsReportService (app/workflows/operations_daily_platform.py)
  -> WorkflowEngine.execute("operations.daily_report", trusted request, trusted scope)
  -> DailyReportStepHandler                     (read-only; verifies store and date)
  -> DailyOperationsWorkflow                    (unchanged: the only report logic)
```

The report, the denial (`DailyOperationsForbiddenError`, mapped to 403) and "unavailable"
(503) are externally unchanged. Business-day handling, timezones, governance preflight,
reads, cross-store protection, metrics and findings all remain in
`DailyOperationsWorkflow`.

The mock deployment composes this in `app/composition/local_mock.py`. One daily report
request is one durable `operations.daily_report` run, so the report now needs the
Product database, which the mock composition already requires.

**Task relationship.** The Task `operations.analyze_daily` names the Workflow
`operations.daily_report` (`TaskDefinition.workflow_id`). `operations.inspect_order` and
`operations.escalate_issue` are not Workflow-backed. The capability graph validates the
reference at startup and fails closed when it dangles.

## Read-only inspection API (`workflows.read`)

| Method | Path |
| --- | --- |
| GET | `/api/v1/workflows/catalog` |
| GET | `/api/v1/workflows/workflow?workflow_id=` |
| GET | `/api/v1/workflows/runs?limit=` (1–100, default 25) |
| GET | `/api/v1/workflows/run?run_id=` |

- These are Product-authenticated, fixed paths. The AgentOS key is refused.
- **Run queries are company-scoped in the query itself.** Another company's run answers
  `404`, exactly like a missing one.
- **Responses** contain ids, versions, statuses, Step ids, attempt metadata, safe failure
  codes, events and timestamps only.
- **There is no run, resume, retry or mutation endpoint.** Workflows execute only inside
  trusted Product services, under those services' own business permissions. There is
  deliberately no `workflows.run` permission.

**Workflows** (`/workflows`; the legacy `/settings/workflows` redirects there) shows the
definitions, recent runs, Step attempts and events. It is read-only: no Run, Retry or
Resume button, no input form and no JSON or code editor. The Control Center Overview shows
recent runs and their statuses ([`CONTROL_CENTER.md`](CONTROL_CENTER.md)).

## Observability and logs

A deployed application has exactly **one** Product observability. `app.bootstrap` chooses
it once (the Product default, or an explicitly injected one) and hands the same instance
to the business composition, which gives it to the `WorkflowEngine`, and to
`create_app`. The engine never builds an observability of its own: without one, Workflow
runs are simply not observed.

Through that observability, the platform records two operations:

- `workflow.run`: one per execute or resume;
- `workflow.step_attempt`: one per attempt.

Their attributes are low-cardinality labels only: `workflow.id`, `workflow.step_id`,
`workflow.status`, `workflow.failure_code` and `workflow.retry`. Together they cover run
counts, success and failure, duration, attempts, retries, timeouts and requires-human
outcomes. No run, company, actor or store id ever becomes a metric attribute, and no
exporter is added.

Structured JSON logs (`app.product.workflows`) carry `workflow_id`, `run_id`, `step_id`,
`attempt`, `status` and `failure_code` only. They never contain input, checkpoints,
reports, provider payloads, model content or secrets.

## Independence

- **Runtime-independent:** the Workflow domain imports no `agno.*`. Agno is the
  replaceable, pinned, self-hostable OSS Agent runtime
  (`agno[os,postgres,openai,anthropic]==3.0.11`, unchanged). Agno workflow objects are
  not the Product model. No Agno Cloud, Control Plane, hosted workflow service, paid
  feature, API key, quota or hosted telemetry is used or required.
- **Provider-independent:** Workflows reference Product domain concepts only. The daily
  report reaches the Product-owned `CommerceIntegration` through
  `DailyOperationsWorkflow`.
- **External data is untrusted:** it is never interpreted as instructions, and no LLM
  takes part in Workflow execution.
- **No new dependency:** the platform uses the standard library, Pydantic, SQLAlchemy
  with PostgreSQL, FastAPI and the existing OpenTelemetry API.
