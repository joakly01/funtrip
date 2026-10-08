from __future__ import annotations

from audit_agent_runtime.llm.base import ToolCall


class Hook:
    def before_model_call(self, messages: list, session_id: str) -> None:
        return None

    def after_model_response(
        self,
        output_text: str,
        stop_reason: str | None,
        session_id: str,
    ) -> None:
        return None

    def after_sampling(
        self,
        output_text: str,
        tool_calls: list[ToolCall],
        stop_reason: str | None,
        session_id: str,
    ) -> None:
        return None

    def before_tool_use(self, tool_name: str, arguments: dict, session_id: str) -> None:
        return None

    def after_tool_use(self, tool_name: str, result: dict, session_id: str) -> None:
        return None

    def after_stop(self, stop_reason: str, session_id: str) -> None:
        return None
