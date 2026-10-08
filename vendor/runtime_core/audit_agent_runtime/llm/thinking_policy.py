from __future__ import annotations

from dataclasses import dataclass

from audit_agent_runtime.llm.base import ModelMessage


@dataclass(frozen=True, slots=True)
class ThinkingPolicy:
    provider: str
    model_name: str
    requested_enabled: bool = False

    @property
    def is_kimi(self) -> bool:
        return self.provider == "openai" and self.model_name.lower().startswith("kimi-")

    def effective_mode(self, messages: list[ModelMessage]) -> str:
        if not self.requested_enabled:
            return "disabled"
        if self._has_invalid_reasoning_trajectory(messages):
            # Claude Code 的做法是不伪造 thinking：轨迹缺失真实 reasoning 时，
            # 本次请求降级为无 thinking 并清掉历史 reasoning，避免 provider 400。
            return "disabled"
        return "enabled"

    def request_payload(self, mode: str) -> dict | None:
        if self.is_kimi:
            return {"type": mode}
        if mode == "disabled":
            return None
        return None

    def should_serialize_reasoning(self, mode: str) -> bool:
        return mode == "enabled"

    def normalize_messages(
        self,
        messages: list[ModelMessage],
        *,
        mode: str,
    ) -> list[ModelMessage]:
        if self.should_serialize_reasoning(mode):
            return messages
        return [_without_reasoning(message) for message in messages]

    def _has_invalid_reasoning_trajectory(self, messages: list[ModelMessage]) -> bool:
        if not self.is_kimi:
            return False
        for message in messages:
            if (
                message.role == "assistant"
                and message.tool_calls
                and not message.reasoning_content
            ):
                return True
        return False


def _without_reasoning(message: ModelMessage) -> ModelMessage:
    if not message.reasoning_content:
        return message
    return ModelMessage(
        role=message.role,
        content=message.content,
        tool_call_id=message.tool_call_id,
        name=message.name,
        tool_calls=message.tool_calls,
        reasoning_content=None,
    )
