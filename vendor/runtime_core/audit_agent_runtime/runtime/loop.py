from __future__ import annotations

from collections.abc import Iterator

from audit_agent_runtime.hooks.base import Hook
from audit_agent_runtime.llm.base import ModelClient, ModelMessage
from audit_agent_runtime.querying.query_loop import (
    QueryLoopParams,
    _build_messages_with_prompt,
    queryLoop,
)
from audit_agent_runtime.runtime.events import RuntimeEvent
from audit_agent_runtime.tools.core.registry import ToolRegistry


class RuntimeLoop:
    def __init__(
        self,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        hooks: list[Hook] | None = None,
        system_prompt: str | None = None,
        can_use_tool=None,
        working_directory: str | None = None,
        max_tool_calls_per_turn: int = 16,
    ):
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.hooks = hooks
        self.system_prompt = system_prompt
        self.can_use_tool = can_use_tool
        self.working_directory = working_directory
        self.max_tool_calls_per_turn = max_tool_calls_per_turn

    def run(
        self,
        query: object,
        history: list[dict],
        session_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        working_directory: str | None = None,
        max_turns: int | None = None,
        system_prompt_blocks: list[str] | None = None,
        system_reminders: list[str] | None = None,
        can_use_tool=None,
    ) -> Iterator[RuntimeEvent]:
        yield from queryLoop(
            QueryLoopParams(
                query=query,
                history=history,
                session_id=session_id,
                model_client=self.model_client,
                tool_registry=self.tool_registry,
                hooks=self.hooks,
                system_prompt=self.system_prompt,
                system_prompt_blocks=system_prompt_blocks,
                system_reminders=system_reminders,
                can_use_tool=can_use_tool or self.can_use_tool,
                working_directory=working_directory or self.working_directory,
                max_tool_calls_per_turn=self.max_tool_calls_per_turn,
                active_task_id=task_id,
                active_agent_id=agent_id,
                max_turns=max_turns,
            )
        )

    @staticmethod
    def _build_messages(history: list[dict], query: str) -> list[ModelMessage]:
        return _build_messages_with_prompt(history, query, system_prompt=None)

    @staticmethod
    def _build_messages_with_prompt(
        history: list[dict],
        query: str,
        system_prompt: str | None,
    ) -> list[ModelMessage]:
        return _build_messages_with_prompt(history, query, system_prompt)
