from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ModelMessage:
    role: str
    content: Any
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list["ToolCall"] | None = None
    reasoning_content: str | None = None


@dataclass(slots=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: dict = field(default_factory=dict)


@dataclass(slots=True)
class ModelResponse:
    output_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    reasoning_content: str | None = None
    usage: dict[str, Any] | None = None


@dataclass(slots=True)
class ModelStreamEvent:
    type: str
    text: str = ""
    tool_call: ToolCall | None = None
    stop_reason: str | None = None
    reasoning_content: str | None = None
    usage: dict[str, Any] | None = None


class ModelClient(Protocol):
    def complete(self, messages: list[ModelMessage], tools: list[dict]) -> ModelResponse: ...

    def stream(self, messages: list[ModelMessage], tools: list[dict]): ...
