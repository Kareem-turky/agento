"""Product persistence infrastructure (SQLAlchemy 2 async, PostgreSQL).

Implements product-owned store contracts: ``app.commands.WriteCommandStore`` /
``WriteCommandReader``, ``app.execution.AuditSink`` (``PostgresAuditSink``) and
``app.integration_management.IntegrationConnectionRepository`` (connection METADATA,
Task 031) and ``app.agent_management.AgentConfigurationRepository`` (Agent enable/disable
overrides, Task 032) and ``app.workflow_management.contracts.WorkflowRunRepository``
(Workflow execution CONTROL state, Task 034) and the ``app.knowledge.contracts``
repositories (``PostgresKnowledgeRepository``: versioned operating model and Knowledge
documents, Task 035) and ``app.approval_management.contracts.ApprovalRepository``
(``PostgresApprovalRepository``: human-approval requests and their append-only events,
Task 036) and ``app.conversations.contracts.ConversationRepository``
(``PostgresConversationRepository``: the canonical conversation transcript, Task 037).
Depends on SQLAlchemy and those contracts
only: no agents, Agno, routes, business integrations or business handlers, and no
business-data tables. The schema is owned by Alembic migrations, never created here.
"""

from app.persistence.agent_configurations import (
    PostgresAgentConfigurationRepository,
    agent_configurations,
)
from app.persistence.approvals import (
    PostgresApprovalRepository,
    approval_events,
    approval_requests,
)
from app.persistence.audit import AuditPersistenceError, PostgresAuditSink, audit_events
from app.persistence.conversations import (
    PostgresConversationRepository,
    conversation_messages,
    conversations,
    message_delivery_events,
)
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
from app.persistence.knowledge import (
    PostgresKnowledgeRepository,
    company_operating_model_current,
    company_operating_model_versions,
    knowledge_chunks,
    knowledge_document_versions,
    knowledge_documents,
)
from app.persistence.workflow_runs import (
    PostgresWorkflowRunRepository,
    workflow_events,
    workflow_runs,
    workflow_step_runs,
)
from app.persistence.write_commands import PostgresWriteCommandStore, write_commands

__all__ = [
    "PostgresApprovalRepository",
    "PostgresConversationRepository",
    "conversation_messages",
    "conversations",
    "message_delivery_events",
    "approval_events",
    "approval_requests",
    "PRODUCT_SCHEMA",
    "PostgresAgentConfigurationRepository",
    "agent_configurations",
    "AuditPersistenceError",
    "PostgresAuditSink",
    "PostgresIntegrationConnectionRepository",
    "PostgresKnowledgeRepository",
    "PostgresWorkflowRunRepository",
    "audit_events",
    "company_operating_model_current",
    "company_operating_model_versions",
    "PostgresWriteCommandStore",
    "create_product_engine",
    "integration_connections",
    "knowledge_chunks",
    "knowledge_document_versions",
    "knowledge_documents",
    "create_session_factory",
    "product_metadata",
    "workflow_events",
    "workflow_runs",
    "workflow_step_runs",
    "write_commands",
]
