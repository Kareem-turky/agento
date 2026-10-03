"""TEST-ONLY Product Core acceptance harness (Task 040). Never used by production code.

One REAL Product installation per ``CoreInstallation.app()``: ``create_deployment_app`` in
the ``test`` environment with the ``mock`` business backend on the migrated PostgreSQL,
Product API-key authentication and the deployment observability runtime (the real Product
logger, written to an in-memory stream instead of stdout).

The ONLY substitutions are TEST-ONLY, through seams that already exist:

* the integration catalog: ``build_integration_management(..., catalog=)`` and
  ``build_conversations(..., catalog=)`` receive a generic test catalog (``example-chat``,
  ``example-commerce``, ``example-messaging``). The production default stays EMPTY; it is
  never mutated;
* the Agent model: the ``model=`` test seam with a deterministic ``ScriptedToolModel``;
* the Product log stream: ``build_deployment_observability(..., stream=)``.

Nothing here adds a route, a flag or a runtime switch to the Product. The governed
TEST-ONLY approval action and Workflow (``GovernedTestProcess``) run through the REAL
ApprovalBroker, ExecutionCoordinator, GovernanceGate, WorkflowEngine and ApprovalService
on the REAL PostgreSQL repositories, exactly like a future business composition would.
"""

import io
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.pool import NullPool

from app import bootstrap
from app.composition import local_mock
from app.config import Settings
from app.context.models import ActorContext, RequestContext
from app.integration_management import InstalledIntegration, IntegrationCatalog
from tests.support.canonical_mock import COMPANY, NORTH, SOUTH
from tests.support.conversation_fakes import CHAT
from tests.support.integration_fakes import COMMERCE, MESSAGING, FakeDriver
from tests.support.product_auth import deployment_settings, principal

# ----- credentials (TEST-ONLY; configuration holds only their SHA-256) ----------------------

OPERATOR_KEY = "test-core-operator-key-" + "o" * 24  # noqa: S105 - test-only
DECIDER_KEY = "test-core-decider-key-" + "d" * 24  # noqa: S105 - test-only
REQUESTER_KEY = "test-core-requester-key-" + "r" * 24  # noqa: S105 - test-only
RESTRICTED_KEY = "test-core-restricted-key-" + "x" * 24  # noqa: S105 - test-only
INTEGRATION_SECRET = "test-only-core-credential-" + "s" * 24  # noqa: S105 - test-only

OPERATOR_ID, DECIDER_ID = "core-operator", "core-decider"
REQUESTER_ID, RESTRICTED_ID = "core-requester", "core-north-operator"

OPERATOR_PERMISSIONS = frozenset({
    "stores.read", "orders.read", "shipments.read", "tickets.create",
    "agents.read", "agents.manage", "workflows.read", "integrations.read",
    "integrations.manage", "knowledge.read", "knowledge.manage", "conversations.read",
    "approvals.read", "system.read",
})  # fmt: skip
DECIDER_PERMISSIONS = frozenset({"approvals.read", "approvals.decide", "approvals.cancel"})
# The requester may also decide: proves the two-person rule is not a missing permission.
REQUESTER_PERMISSIONS = frozenset({"approvals.read", "approvals.decide", "approvals.cancel",
                                   "workflows.read"})  # fmt: skip
# Store NORTH only, no management, no approvals, no workflows, no system.read.
RESTRICTED_PERMISSIONS = frozenset({"stores.read", "orders.read", "shipments.read",
                                    "tickets.create", "conversations.read"})  # fmt: skip


class Credentials(dict[str, str]):
    """Request headers whose repr never shows a key (pytest prints call arguments)."""

    def __repr__(self) -> str:
        return "<redacted credentials>"

    def __or__(self, other: dict[str, str]) -> "Credentials":  # type: ignore[override]
        return Credentials({**self, **other})


def bearer(key: str) -> Credentials:
    return Credentials({"Authorization": f"Bearer {key}"})


OPERATOR, DECIDER = bearer(OPERATOR_KEY), bearer(DECIDER_KEY)
REQUESTER, RESTRICTED = bearer(REQUESTER_KEY), bearer(RESTRICTED_KEY)
NO_CREDENTIALS = Credentials()


def core_settings(
    settings: Settings,
    secrets_dir: Path | None = None,
    *,
    company_id: str = COMPANY,
    business_backend: str = "mock",
) -> Settings:
    keys = (
        principal(OPERATOR_KEY, key_id="core-operator", actor_id=OPERATOR_ID,
                  permissions=OPERATOR_PERMISSIONS, store_ids=frozenset({SOUTH})),
        # A decision is governed in the request's store scope: the decider holds SOUTH.
        principal(DECIDER_KEY, key_id="core-decider", actor_id=DECIDER_ID,
                  permissions=DECIDER_PERMISSIONS, store_ids=frozenset({SOUTH})),
        principal(REQUESTER_KEY, key_id="core-requester", actor_id=REQUESTER_ID,
                  permissions=REQUESTER_PERMISSIONS, store_ids=frozenset({SOUTH})),
        principal(RESTRICTED_KEY, key_id="core-restricted", actor_id=RESTRICTED_ID,
                  permissions=RESTRICTED_PERMISSIONS, store_ids=frozenset({NORTH})),
    )  # fmt: skip
    return deployment_settings(settings, "test", company_id=company_id,
                               business_backend=business_backend, product_api_keys=keys,
                               integration_secrets_dir=secrets_dir,
                               log_level="INFO")  # fmt: skip


# ----- the installation ----------------------------------------------------------------------


def core_catalog() -> tuple[IntegrationCatalog, dict[str, FakeDriver]]:
    """A generic TEST-ONLY catalog: chat (messaging), commerce (credentials), messaging."""
    drivers = {d.integration_id: FakeDriver(d.integration_id) for d in (CHAT, COMMERCE, MESSAGING)}
    catalog = IntegrationCatalog(InstalledIntegration(d, drivers[d.integration_id])
                                 for d in (CHAT, COMMERCE, MESSAGING))  # fmt: skip
    return catalog, drivers


class CoreInstallation:
    """Builds REAL deployment applications; records what the composition built."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, settings: Settings,
                 runtime_settings: Any, secrets_dir: Path) -> None:  # fmt: skip
        self.base_settings = settings
        self.settings = core_settings(settings, secrets_dir)
        self.runtime_settings = runtime_settings
        self.secrets_dir = secrets_dir
        self.log = io.StringIO()
        self.catalog, self.drivers = core_catalog()
        self.conversation_compositions: list[Any] = []
        self.desks: list[Any] = []
        self.daily_workflows: list[Any] = []

        integrations = bootstrap.build_integration_management
        conversations = bootstrap.build_conversations
        observability = bootstrap.build_deployment_observability
        desk = local_mock.MockTicketDesk
        daily = local_mock.DailyOperationsWorkflow

        def build_integrations(configured: Settings) -> Any:
            return integrations(configured, catalog=self.catalog)

        def build_conversations(configured: Settings, **options: Any) -> Any:
            built = conversations(configured, catalog=self.catalog, **options)
            self.conversation_compositions.append(built)
            return built

        def build_observability(configured: Settings) -> Any:
            return observability(configured, stream=self.log)

        def build_desk(*args: Any, **kwargs: Any) -> Any:
            self.desks.append(desk(*args, **kwargs))
            return self.desks[-1]

        def build_daily(*args: Any, **kwargs: Any) -> Any:
            self.daily_workflows.append(daily(*args, **kwargs))
            return self.daily_workflows[-1]

        monkeypatch.setattr(bootstrap, "build_integration_management", build_integrations)
        monkeypatch.setattr(bootstrap, "build_conversations", build_conversations)
        monkeypatch.setattr(bootstrap, "build_deployment_observability", build_observability)
        monkeypatch.setattr(local_mock, "MockTicketDesk", build_desk)
        monkeypatch.setattr(local_mock, "DailyOperationsWorkflow", build_daily)

    def app(self, model: Any = None) -> FastAPI:
        from tests.support.scripted_tool_model import ScriptedToolModel

        return bootstrap.create_deployment_app(
            self.settings, self.runtime_settings,
            model=model if model is not None else ScriptedToolModel(),
        )  # fmt: skip

    def other_company_app(self, company_id: str) -> FastAPI:
        """ANOTHER company's installation on the SAME database (same keys, same code, no
        business backend): the repository/security isolation invariant, not a tenant
        feature (an installation is physically one company)."""
        configured = core_settings(self.base_settings, self.secrets_dir,
                                   company_id=company_id, business_backend="disabled")  # fmt: skip
        return bootstrap.create_deployment_app(configured, self.runtime_settings)

    @property
    def ingress(self) -> Any:
        """The ConversationIngress the LATEST application composed (the provider seam)."""
        return self.conversation_compositions[-1].ingress

    @property
    def messaging(self) -> Any:
        return self.conversation_compositions[-1].messaging

    def log_records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.getvalue().splitlines() if line.strip()]


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "core-integration-secrets"
    path.mkdir(mode=0o700)
    yield path
    shutil.rmtree(path, ignore_errors=True)  # TEST-ONLY secrets never outlive the test


@pytest.fixture
def core(monkeypatch: pytest.MonkeyPatch, settings: Settings, runtime_settings: Any,
         secrets_dir: Path, migrated: str) -> CoreInstallation:  # fmt: skip
    return CoreInstallation(monkeypatch, settings, runtime_settings, secrets_dir)


# ----- durable evidence straight from PostgreSQL ---------------------------------------------


def rows(engine: sa.Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(r) for r in connection.execute(sa.text(sql), params).mappings()]


def count(engine: sa.Engine, table: str, **where: Any) -> int:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "TRUE"
    sql = f"SELECT count(*) AS n FROM product.{table} WHERE {clauses}"  # noqa: S608 - fixed
    return rows(engine, sql, **where)[0]["n"]


SIDE_EFFECT_TABLES = ("write_commands", "audit_events", "approval_requests", "workflow_runs",
                      "conversation_messages", "knowledge_documents",
                      "company_operating_model_versions", "agent_configurations",
                      "integration_connections")  # fmt: skip


def company_state(engine: sa.Engine, company_id: str = COMPANY) -> dict[str, int]:
    """Row counts of every Product table that records a side effect, for one company."""
    return {table: count(engine, table, company_id=company_id) for table in SIDE_EFFECT_TABLES}


def product_dump(engine: sa.Engine) -> str:
    """Every row of every Product table, as text."""
    with engine.connect() as connection:
        tables = connection.execute(sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'product'"
        )).scalars().all()  # fmt: skip
        return "\n".join(
            str(connection.execute(sa.text(f'SELECT * FROM product."{t}"')).all())  # noqa: S608
            for t in tables
        )


def wipe_agent_configuration(engine: sa.Engine, company_id: str = COMPANY) -> None:
    """The mock company is shared with other suites: never leave an Agent override."""
    with engine.begin() as connection:
        connection.execute(sa.text(
            "DELETE FROM product.agent_configurations WHERE company_id = :c"),
            {"c": company_id})  # fmt: skip


# ----- a TEST-ONLY governed process (approval action + approval Workflow) ---------------------


class RealClock:
    """Wall-clock time (the deployed Product decides with it), movable forward."""

    def __init__(self) -> None:
        self.offset = timedelta()

    def __call__(self) -> datetime:
        return datetime.now(UTC) + self.offset

    def advance(self, **delta: float) -> None:
        self.offset += timedelta(**delta)


def requester_context(actor_type: str = "api_client", actor_id: str = REQUESTER_ID,
                      company_id: str = COMPANY) -> RequestContext:  # fmt: skip
    """The requester principal EXACTLY as Product API-key auth resolves it (api_client),
    plus the TEST-ONLY permission of the TEST-ONLY governed action."""
    from tests.support.approval_fakes import REQUESTER as TEST_ACTION_PERMISSIONS

    return RequestContext(actor=ActorContext(
        actor_id=actor_id, actor_type=actor_type, company_id=company_id,
        permissions=TEST_ACTION_PERMISSIONS | REQUESTER_PERMISSIONS,
        store_ids=frozenset({SOUTH})), channel="api")  # fmt: skip


class GovernedTestProcess:
    """A separate 'process' with its OWN engine (NullPool: safe across event loops) holding
    the TEST-ONLY MEDIUM_RISK action ``test.budget.update`` and the TEST-ONLY Workflow
    ``testing.approval_budget`` on the REAL Product approval / audit / Workflow
    repositories. Its in-memory effect counters do NOT survive a restart (by design)."""

    def __init__(self, database_url: str, clock: RealClock) -> None:
        from app.approval_management.service import ApprovalService
        from app.persistence import (
            PostgresApprovalRepository,
            PostgresAuditSink,
            PostgresWorkflowRunRepository,
            create_product_engine,
            create_session_factory,
        )
        from app.workflow_management.engine import WorkflowEngine
        from app.workflow_management.handlers import (
            WorkflowRuntimeRegistration,
            WorkflowRuntimeRegistry,
        )
        from tests.approval_management.test_workflow_approval import (
            APPROVAL_WORKFLOW,
            CATALOG,
            BudgetRunInput,
            BudgetWrite,
            FetchCampaign,
            Report,
        )
        from tests.support.approval_fakes import ApprovalWorld

        self.engine = create_product_engine(database_url, poolclass=NullPool)
        sessions = create_session_factory(self.engine)
        self.world = ApprovalWorld(
            repository=PostgresApprovalRepository(sessions), audit=PostgresAuditSink(sessions),
            decision_audit=PostgresAuditSink(sessions), clock=clock,  # type: ignore[arg-type]
        )  # fmt: skip
        self.fetch, self.report = FetchCampaign(), Report()
        bindings = WorkflowRuntimeRegistry(CATALOG, [WorkflowRuntimeRegistration(
            APPROVAL_WORKFLOW.workflow_id, BudgetRunInput,
            (self.fetch, BudgetWrite(self.world.coordinator), self.report))])  # fmt: skip
        self.workflows = WorkflowEngine(CATALOG, bindings,
                                        PostgresWorkflowRunRepository(sessions),
                                        clock=clock)  # fmt: skip
        self.workflow_id = APPROVAL_WORKFLOW.workflow_id
        w = self.world
        # The REAL ApprovalService whose explicit continuation is this Workflow engine.
        self.approvals = ApprovalService(w.repository, w.service._gate, w.service._coordinator,
                                         clock=clock, workflows=self.workflows)  # fmt: skip

    @property
    def effects(self) -> list[Any]:
        return list(self.world.budget.effects)

    async def request_budget(self, context: RequestContext | None = None, *,
                             approval_id: Any = None, campaign: str = "spring",
                             amount: int = 150) -> Any:  # fmt: skip
        from app.governance import ActionIntent, ActionScope
        from tests.support.approval_fakes import BUDGET_UPDATE

        context = context or requester_context()
        assert context.actor is not None
        return await self.world.coordinator.run(
            context, ActionIntent(name=BUDGET_UPDATE.name),
            ActionScope(company_id=context.actor.company_id, store_id=SOUTH),
            {"campaign": campaign, "amount": amount, "reason": "Spring sale"},
            approval_id=approval_id,
        )  # fmt: skip

    async def start_workflow(self, campaign: str = "summer", amount: int = 175) -> Any:
        from app.governance import ActionScope

        return await self.workflows.execute(
            self.workflow_id, requester_context(),
            ActionScope(company_id=COMPANY, store_id=SOUTH),
            {"campaign": campaign, "amount": amount})  # fmt: skip

    async def close(self) -> None:
        await self.engine.dispose()
