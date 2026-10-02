"""Trusted governed actions of Product Knowledge (immutable constants, Task 035).

Reads and retrieval require ``knowledge.read``; mutations require ``knowledge.manage``.
All are COMPANY-scoped READ / LOW_RISK_WRITE actions: mutations run through
``ExecutionCoordinator`` and are audited by the existing audit trail (metadata only:
never a body, excerpt, query or operating-model JSON). Knowledge never grants a
permission, and an Agent actor never gets ``knowledge.manage`` (see
``KnowledgePermissionEvaluator``).
"""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

READ_PERMISSION = "knowledge.read"
MANAGE_PERMISSION = "knowledge.manage"


def _action(name: str, description: str, *, write: bool) -> ActionDefinition:
    return ActionDefinition(
        name=name,
        description=description,
        risk=ActionRisk.LOW_RISK_WRITE if write else ActionRisk.READ,
        required_permission=MANAGE_PERMISSION if write else READ_PERMISSION,
        scope_requirement=ActionScopeRequirement.COMPANY,
    )


OPERATING_MODEL_READ = _action(
    "knowledge.operating_model.read", "Read the company operating model and its versions.",
    write=False,
)  # fmt: skip
DOCUMENTS_READ = _action(
    "knowledge.documents.read", "Read Knowledge documents and their versions.", write=False
)
QUERY = _action("knowledge.query", "Retrieve company context references.", write=False)
OPERATING_MODEL_PUBLISH = _action(
    "knowledge.operating_model.publish", "Publish a new company operating model version.",
    write=True,
)  # fmt: skip
DOCUMENT_CREATE = _action("knowledge.document.create", "Create a Knowledge document.", write=True)
DOCUMENT_PUBLISH_VERSION = _action(
    "knowledge.document.publish_version", "Publish a new Knowledge document version.",
    write=True,
)  # fmt: skip
DOCUMENT_ARCHIVE = _action("knowledge.document.archive", "Archive a Knowledge document.",
                           write=True)  # fmt: skip

KNOWLEDGE_ACTIONS: tuple[ActionDefinition, ...] = (
    OPERATING_MODEL_READ, DOCUMENTS_READ, QUERY, OPERATING_MODEL_PUBLISH, DOCUMENT_CREATE,
    DOCUMENT_PUBLISH_VERSION, DOCUMENT_ARCHIVE,
)  # fmt: skip
