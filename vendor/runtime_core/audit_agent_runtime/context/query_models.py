from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


JsonDict = dict[str, Any]


@dataclass(slots=True)
class ToolPolicy:
    mode: str = "allow_all"
    tools: list[str] = field(default_factory=list)

    def allows(self, tool_name: str) -> bool:
        return self.decision(tool_name) == "allow"

    def decision(self, tool_name: str) -> str:
        if self.mode == "deny_list":
            return "deny" if tool_name in self.tools else "allow"
        if self.mode == "allow_list":
            return "allow" if tool_name in self.tools else "deny"
        if self.mode == "ask_all":
            return "ask"
        if self.mode == "ask_list":
            return "ask" if tool_name in self.tools else "allow"
        return "allow"

    def describe(self) -> str | None:
        if self.mode == "deny_list" and self.tools:
            return f"Denied tools: {', '.join(self.tools)}"
        if self.mode == "allow_list" and self.tools:
            return f"Only allowed tools: {', '.join(self.tools)}"
        if self.mode == "ask_all":
            return "All tool uses require approval before execution."
        if self.mode == "ask_list" and self.tools:
            return f"These tools require approval before execution: {', '.join(self.tools)}"
        return None


@dataclass(slots=True)
class QueryRequest:
    session_id: str
    user_id: str
    input_text: str
    metadata: JsonDict = field(default_factory=dict)
    input_mode: str = "prompt"
    system_context: JsonDict = field(default_factory=dict)
    user_context: JsonDict = field(default_factory=dict)
    working_directory: str | None = None
    query_source: str = "api"
    resume_task_id: str | None = None
    override_system_prompt: str | None = None
    custom_system_prompt: str | None = None
    coordinator_mode: bool = False
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    max_turns: int | None = None
    task_budget: int | None = None
    task_budget_source: str | None = None
    task_budget_hint_positions: list[tuple[int, int]] = field(default_factory=list)
    skip_slash_commands: bool = False


@dataclass(slots=True)
class QueryContext:
    system_prompt: str
    user_context: JsonDict = field(default_factory=dict)
    system_context: JsonDict = field(default_factory=dict)
    model_client: Any = None
    can_use_tool: Any = None
    fallback_model: str | None = None
    max_turns: int | None = None
    task_budget: int | None = None


@dataclass(slots=True)
class QueryState:
    mutable_messages: list[JsonDict]
    dialog_state: JsonDict = field(default_factory=dict)
    tool_use_context: JsonDict = field(default_factory=dict)
    turn_count: int = 1
    transition: str | None = None
    pending_tool_use_summary: JsonDict | None = None
    auto_compact_tracking: JsonDict | None = None
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    active_agent_id: str | None = None
    stop_hook_active: bool | None = None


@dataclass(slots=True)
class QueryTask:
    task_id: str
    session_id: str
    agent_id: str
    status: str
    mode: str
    payload: JsonDict = field(default_factory=dict)
    parent_task_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class QueuedCommand:
    id: int
    session_id: str
    kind: str
    priority: str
    payload: JsonDict = field(default_factory=dict)
    target_agent_id: str | None = None
    created_at: datetime | None = None
    claimed_at: datetime | None = None
    consumed_at: datetime | None = None


@dataclass(slots=True)
class TaskNotification:
    task_id: str
    agent_id: str
    status: str
    summary: str | None = None
    payload: JsonDict = field(default_factory=dict)
    created_at: datetime | None = None
