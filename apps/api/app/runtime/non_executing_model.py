"""A model placeholder that can never execute.

AgentOS assigns Agno's default provider model (OpenAI) to any agent registered
without one, which would require a provider SDK. The smoke-test agent uses this
placeholder instead: it satisfies Agno's ``Model`` interface, needs no SDK or
credentials, and raises if anything tries to invoke it.
"""

from dataclasses import dataclass
from typing import Any, NoReturn

from agno.models.base import Model


class ModelExecutionDisabledError(RuntimeError):
    pass


@dataclass
class NonExecutingModel(Model):
    id: str = "non-executing"
    name: str = "NonExecutingModel"
    provider: str = "none"

    def _refuse(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ModelExecutionDisabledError(
            "This agent has no model provider configured; execution is disabled."
        )

    invoke = _refuse
    ainvoke = _refuse
    invoke_stream = _refuse
    ainvoke_stream = _refuse
    _parse_provider_response = _refuse
    _parse_provider_response_delta = _refuse
