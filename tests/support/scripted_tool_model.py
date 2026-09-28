"""TEST-ONLY scripted Agno model that emits native tool calls. Never used by
production code.

It follows a fixed script: each model call consumes one step. ``CallTool`` returns a
native ``ModelResponse`` tool call, so the REAL Agno tool loop executes the tool and
calls the model again with the result. ``Reply`` returns the final assistant text,
optionally computed from the tool results the model has received. No network, no
randomness. Every request is recorded (messages as the model saw them, and the tool
schemas it was offered) for assertions.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agno.models.base import Model
from agno.models.response import ModelResponse


@dataclass(frozen=True)
class CallTool:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Reply:
    """Final answer: fixed text, or computed from {tool name: [parsed results]}."""

    text: str | Callable[[dict[str, list[dict[str, Any]]]], str]


@dataclass(frozen=True)
class SeenMessage:
    role: str
    content: str
    tool_name: str | None


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[SeenMessage, ...]
    tools: Any


Step = CallTool | Reply


@dataclass
class ScriptedToolModel(Model):
    id: str = "scripted-tool-model"
    name: str = "ScriptedToolModel"
    provider: str = "test"
    script: list[Step] = field(default_factory=list)
    requests: list[ModelRequest] = field(default_factory=list)

    def _respond(self, kwargs: dict[str, Any]) -> ModelResponse:
        messages = tuple(
            SeenMessage(
                role=str(m.role),
                content="" if m.content is None else str(m.content),
                tool_name=getattr(m, "tool_name", None),
            )
            for m in kwargs.get("messages") or []
        )
        self.requests.append(ModelRequest(messages=messages, tools=kwargs.get("tools")))
        index = len(self.requests) - 1
        step = self.script[index] if index < len(self.script) else Reply("script exhausted")
        if isinstance(step, CallTool):
            call = {
                "id": f"call_{index}",
                "type": "function",
                "function": {"name": step.name, "arguments": json.dumps(step.arguments)},
            }
            return ModelResponse(role="assistant", tool_calls=[call])
        text = step.text(self.tool_results()) if callable(step.text) else step.text
        return ModelResponse(role="assistant", content=text)

    def tool_results(self) -> dict[str, list[dict[str, Any]]]:
        """Parsed tool results from the latest request, grouped by tool name."""
        results: dict[str, list[dict[str, Any]]] = {}
        if not self.requests:
            return results
        for message in self.requests[-1].messages:
            if message.role == "tool" and message.tool_name:
                try:
                    parsed = json.loads(message.content)
                except ValueError:
                    parsed = {"raw": message.content}
                results.setdefault(message.tool_name, []).append(parsed)
        return results

    def visible_text(self) -> str:
        """Everything the model was shown (all messages of all requests)."""
        return "\n".join(m.content for r in self.requests for m in r.messages)

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._respond(kwargs)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._respond(kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any):
        yield self._respond(kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):
        yield self._respond(kwargs)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response
