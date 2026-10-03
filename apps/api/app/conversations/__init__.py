"""Product-owned Channel & Conversation foundation (Task 037).

A ``Conversation`` is ONE external messaging thread on ONE ``IntegrationConnection``;
its ``ConversationMessage`` rows are Product-owned canonical transcript data. Inbound
text is UNTRUSTED EXTERNAL DATA: it is stored and shown as text only and never reaches
a permission, policy, approval, tool, Workflow, Agent, model or Knowledge. No real
messaging provider, public webhook or outbound send exists in this build.
"""
