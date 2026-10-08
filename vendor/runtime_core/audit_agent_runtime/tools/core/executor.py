from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from audit_agent_runtime.llm.base import ToolCall
from audit_agent_runtime.tools.core.permissions import ToolPermissionDecision
from audit_agent_runtime.tools.core.registry import ToolRegistry


@dataclass(slots=True)
class ToolExecutionContext:
    session_id: str
    working_directory: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    completed_tools: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None


class ToolExecutor:
    def __init__(self, tool_registry: ToolRegistry, can_use_tool=None):
        self.tool_registry = tool_registry
        self.can_use_tool = can_use_tool

    def execute(self, tool_call: ToolCall, context: ToolExecutionContext) -> dict:
        if not tool_call.tool_name:
            return {
                "is_error": True,
                "error": "unknown_tool",
                "tool_name": tool_call.tool_name,
                "message": "Tool call did not include a valid tool name.",
            }
        if self.can_use_tool is not None:
            decision = _normalize_permission_decision(
                self.can_use_tool(
                    tool_call.tool_name,
                    tool_call.arguments,
                    context.session_id,
                )
            )
            if decision.behavior == "deny" and decision.reason == "flow_blocked":
                return {
                    "is_error": True,
                    "error": "tool_flow_blocked",
                    "tool_name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                    "message": decision.message or "Tool use was blocked.",
                }
            if decision.behavior == "deny":
                return {
                    "is_error": True,
                    "error": "tool_permission_denied",
                    "tool_name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                    "message": (
                        decision.message
                        or f"Tool '{tool_call.tool_name}' was denied by the current permission policy."
                    ),
                    **(
                        {"permission_reason": decision.reason}
                        if decision.reason
                        else {}
                    ),
                }
            if decision.behavior == "ask":
                return {
                    "is_error": True,
                    "error": "tool_approval_required",
                    "tool_name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                    "message": (
                        decision.message
                        or f"Tool '{tool_call.tool_name}' requires approval before execution."
                    ),
                    **(
                        {"permission_reason": decision.reason}
                        if decision.reason
                        else {}
                    ),
                }
        try:
            tool = self.tool_registry.get(tool_call.tool_name)
        except KeyError:
            return {
                "is_error": True,
                "error": "unknown_tool",
                "tool_name": tool_call.tool_name,
                "message": f"Unknown tool: {tool_call.tool_name}",
            }
        try:
            return tool.execute(tool_call.arguments, context)
        except Exception as exc:
            # Tool failures should surface as tool results so the runtime can
            # continue streaming instead of terminating the entire turn.
            return {
                "is_error": True,
                "error": "tool_execution_failed",
                "tool_name": tool_call.tool_name,
                "message": str(exc),
            }


def _normalize_permission_decision(value: Any) -> ToolPermissionDecision:
    if isinstance(value, ToolPermissionDecision):
        return value
    if isinstance(value, dict):
        behavior = value.get("behavior")
        if behavior in {"allow", "deny", "ask"}:
            return ToolPermissionDecision(
                behavior=behavior,
                message=(
                    value.get("message")
                    if isinstance(value.get("message"), str)
                    else None
                ),
                reason=(
                    value.get("reason")
                    if isinstance(value.get("reason"), str)
                    else None
                ),
            )
    if isinstance(value, str) and value.startswith("deny:"):
        return ToolPermissionDecision(
            behavior="deny",
            message=value.split(":", 1)[1].strip(),
            reason="flow_blocked",
        )
    if value in (False, "deny"):
        return ToolPermissionDecision(behavior="deny")
    if value == "ask":
        return ToolPermissionDecision(behavior="ask")
    return ToolPermissionDecision(behavior="allow")
