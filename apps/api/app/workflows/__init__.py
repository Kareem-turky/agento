"""Deterministic Product workflows: backend orchestration without any model.

If a task is deterministic it is a Workflow, not an Agent. Workflows depend on
context, governance, the canonical commerce domain, integration contracts and Product
service contracts only: never on FastAPI, Agno, agents, commands, execution,
persistence or concrete (mock) integrations. The deployment composition root wires
them to a concrete integration.
"""

from app.workflows.operations_daily import (
    REQUIRED_READ_ACTIONS,
    DailyOperationsWorkflow,
    business_day_window,
)

__all__ = ["REQUIRED_READ_ACTIONS", "DailyOperationsWorkflow", "business_day_window"]
