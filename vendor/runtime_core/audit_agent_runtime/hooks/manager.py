from __future__ import annotations

import logging

from audit_agent_runtime.hooks.base import Hook
from audit_agent_runtime.llm.base import ToolCall


logger = logging.getLogger(__name__)


class HookManager:
    def __init__(self, hooks: list[Hook] | None = None):
        self.hooks = hooks or []

    def before_model_call(self, messages: list, session_id: str) -> None:
        for hook in self.hooks:
            try:
                hook.before_model_call(messages=messages, session_id=session_id)
            except Exception:
                logger.exception("Hook before_model_call failed")

    def after_model_response(
        self,
        output_text: str,
        stop_reason: str | None,
        session_id: str,
    ) -> None:
        for hook in self.hooks:
            try:
                hook.after_model_response(
                    output_text=output_text,
                    stop_reason=stop_reason,
                    session_id=session_id,
                )
            except Exception:
                logger.exception("Hook after_model_response failed")

    def after_sampling(
        self,
        output_text: str,
        tool_calls: list[ToolCall],
        stop_reason: str | None,
        session_id: str,
    ) -> None:
        for hook in self.hooks:
            try:
                hook.after_sampling(
                    output_text=output_text,
                    tool_calls=tool_calls,
                    stop_reason=stop_reason,
                    session_id=session_id,
                )
            except Exception:
                logger.exception("Hook after_sampling failed")

    def before_tool_use(self, tool_name: str, arguments: dict, session_id: str) -> None:
        for hook in self.hooks:
            try:
                hook.before_tool_use(
                    tool_name=tool_name,
                    arguments=arguments,
                    session_id=session_id,
                )
            except Exception:
                logger.exception("Hook before_tool_use failed")

    def after_tool_use(self, tool_name: str, result: dict, session_id: str) -> None:
        for hook in self.hooks:
            try:
                hook.after_tool_use(tool_name=tool_name, result=result, session_id=session_id)
            except Exception:
                logger.exception("Hook after_tool_use failed")

    def after_stop(self, stop_reason: str, session_id: str) -> None:
        for hook in self.hooks:
            try:
                hook.after_stop(stop_reason=stop_reason, session_id=session_id)
            except Exception:
                logger.exception("Hook after_stop failed")
