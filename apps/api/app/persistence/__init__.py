"""Product persistence infrastructure (SQLAlchemy 2 async, PostgreSQL).

Implements product-owned store contracts: ``app.commands.WriteCommandStore`` /
``WriteCommandReader``, ``app.execution.AuditSink`` (``PostgresAuditSink``) and
``app.integration_management.IntegrationConnectionRepository`` (connection METADATA,
Task 031) and ``app.agent_management.AgentConfigurationRepository`` (Agent enable/disable
overrides, Task 032) and ``app.workflow_management.contracts.WorkflowRunRepository``
(Workflow execution CONTROL state, Task 034). Depends on SQLAlchemy and those contracts
only: no agents, Agno, routes, business integrations or business handlers, and no
business-data tables. The schema is owned by Alembic migrations, never created here.
"""

from app.persistence.agent_configurations import (
    PostgresAgentConfigurationRepository,
    agent_configurations,
)
from app.persistence.audit import AuditPersistenceError, PostgresAuditSink, audit_events
from app.persistence.database import (
    PRODUCT_SCHEMA,
    create_product_engine,
    create_session_factory,
    product_metadata,
)
from app.persistence.integration_connections import (
    PostgresIntegrationConnectionRepository,
    integration_connections,
)
from app.persistence.workflow_runs import (
    PostgresWorkflowRunRepository,
    workflow_events,
    workflow_runs,
    workflow_step_runs,
)
from app.persistence.write_commands import PostgresWriteCommandStore, write_commands

__all__ = [
    "PRODUCT_SCHEMA",
    "PostgresAgentConfigurationRepository",
    "agent_configurations",
    "AuditPersistenceError",
    "PostgresAuditSink",
    "PostgresIntegrationConnectionRepository",
    "PostgresWorkflowRunRepository",
    "audit_events",
    "PostgresWriteCommandStore",
    "create_product_engine",
    "integration_connections",
    "create_session_factory",
    "product_metadata",
    "workflow_events",
    "workflow_runs",
    "workflow_step_runs",
    "write_commands",
]
