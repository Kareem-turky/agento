"""TEST-ONLY deterministic Agno model. Never used by production code.

It implements Agno's ``Model`` interface and returns a fixed response without any
network access, so a real Agno ``Agent`` can be executed in tests and CI.
"""

from dataclasses import dataclass, field
from typing import Any

from agno.models.base import Model
from agno.models.response import ModelResponse

DETERMINISTIC_RESPONSE = "deterministic-test-response"


@dataclass
class DeterministicModel(Model):
    id: str = "deterministic-test-model"
    name: str = "DeterministicModel"
    provider: str = "test"
    calls: list[str] = field(default_factory=list)
    # The text of every message the model was given, per call (system prompt included).
    received_messages: list[list[str]] = field(default_factory=list)

    def _respond(self, method: str, kwargs: dict[str, Any]) -> ModelResponse:
        self.calls.append(method)
        self.received_messages.append(
            [str(message.content) for message in kwargs.get("messages") or []]
        )
        return ModelResponse(role="assistant", content=DETERMINISTIC_RESPONSE)

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._respond("invoke", kwargs)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._respond("ainvoke", kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any):
        yield self._respond("invoke_stream", kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):
        yield self._respond("ainvoke_stream", kwargs)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response
