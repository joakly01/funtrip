from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from audit_agent_runtime.context.dialog_state import DialogState
from audit_agent_runtime.context.query_models import ToolPolicy


ToolPermissionBehavior = Literal["allow", "deny", "ask"]


@dataclass(frozen=True, slots=True)
class ToolPermissionDecision:
    """Structured decision returned by the runtime permission layer."""

    behavior: ToolPermissionBehavior
    message: str | None = None
    reason: str | None = None


def build_can_use_tool(
    tool_policy: ToolPolicy,
    *,
    previous_dialog_state: DialogState,
    approval_decision: str | None,
    active_skill_hints: dict[str, dict] | None = None,
):
    pending_tool_name = previous_dialog_state.pending_params.get("tool_name")
    pending_arguments = previous_dialog_state.pending_params.get("arguments", {})
    has_pending_approval = previous_dialog_state.status == "awaiting_tool_approval"
    # 已加载 skill 只作为模型上下文，不参与硬权限白名单。真正的执行权限由
    # ToolPolicy / 用户审批状态决定，和模型可见工具池保持分层。
    _ = active_skill_hints
    approval_decision_consumed = False

    def can_use_tool(
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolPermissionDecision:
        nonlocal approval_decision_consumed
        if (
            has_pending_approval
            and approval_decision in {"approved", "denied"}
            and not approval_decision_consumed
            and tool_name == pending_tool_name
            and arguments == pending_arguments
        ):
            # 一次用户确认只覆盖等待中的那一次工具意图。模型恢复时可能生成
            # 新 call_id，所以这里仍按工具名+参数匹配，但匹配成功后必须消费，
            # 避免同一轮重复同参工具调用绕过 ask_list/deny 策略。
            approval_decision_consumed = True
            return ToolPermissionDecision(
                behavior="allow" if approval_decision == "approved" else "deny",
                reason="pending_tool_approval",
            )
        return ToolPermissionDecision(
            behavior=tool_policy.decision(tool_name),
            reason=f"tool_policy:{tool_policy.mode}",
        )

    return can_use_tool
