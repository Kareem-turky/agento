"""The Product Conversation read permission and its governed action (Task 037).

``conversations.read`` is the only Conversation permission in this build: there is no
send, reply, manage or ingest permission because none of those operations is exposed.
No Agent manifest is changed: the Operations Agent is NOT granted Conversation access.
"""

from app.governance import ActionDefinition, ActionRisk, ActionScopeRequirement

READ_PERMISSION = "conversations.read"

CONVERSATIONS_READ = ActionDefinition(
    name="conversations.read",
    description="Read the company's conversations and their transcripts.",
    risk=ActionRisk.READ,
    required_permission=READ_PERMISSION,
    scope_requirement=ActionScopeRequirement.COMPANY,
)

CONVERSATION_ACTIONS: tuple[ActionDefinition, ...] = (CONVERSATIONS_READ,)
