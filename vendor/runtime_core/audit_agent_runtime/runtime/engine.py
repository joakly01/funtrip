from __future__ import annotations

from collections.abc import Iterable

from audit_agent_runtime.compaction.engine import CompactionEngine
from audit_agent_runtime.memory.extractor import MemoryExtractor
from audit_agent_runtime.llm.base import ModelMessage, ModelResponse
from audit_agent_runtime.querying.query import QueryParams, query as run_query
from audit_agent_runtime.querying.tool_result_collapse import DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS
from audit_agent_runtime.runtime.loop import RuntimeLoop
from audit_agent_runtime.runtime.events import RuntimeEvent
from audit_agent_runtime.tasks.queue_manager import QueueManager
from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.tools.core.registry import ToolRegistry


class EchoModelClient:
    def complete(self, messages: list[ModelMessage], tools: list[dict]) -> ModelResponse:
        latest_user_message = messages[-1].content if messages else ""
        return ModelResponse(output_text=f"Echo: {latest_user_message}")


class RuntimeEngine:
    def __init__(
        self,
        runtime_loop: RuntimeLoop | None = None,
        queue_manager: QueueManager | None = None,
        task_registry: TaskRegistry | None = None,
        compaction_engine: CompactionEngine | None = None,
        memory_extractor: MemoryExtractor | None = None,
        tool_result_keep_recent: int | None = None,
        tool_result_max_model_chars: int = DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS,
        tool_result_preview_chars: int = 2_000,
    ):
        self.runtime_loop = runtime_loop or RuntimeLoop(
            model_client=EchoModelClient(),
            tool_registry=ToolRegistry(),
        )
        self.queue_manager = queue_manager
        self.task_registry = task_registry
        self.compaction_engine = compaction_engine
        self.memory_extractor = memory_extractor
        # None 表示不启用旧工具结果清理；整数值表示模型采样前保留最近 N 个。
        # 这个策略只作用于模型可见消息，避免影响前端证据包和数据库完整审计轨迹。
        self.tool_result_keep_recent = tool_result_keep_recent
        # 聚合预算阈值同样只控制模型可见投影；replacement state 会持久化，
        # 使恢复后的同一 tool_use_id 继续看到相同裁剪结果。
        self.tool_result_max_model_chars = tool_result_max_model_chars
        self.tool_result_preview_chars = tool_result_preview_chars

    def stream(
        self,
        query: object,
        history: list[dict],
        session_id: str,
        system_prompt: str | None = None,
        system_prompt_blocks: list[str] | None = None,
        system_reminders: list[str] | None = None,
        user_context: dict[str, str] | None = None,
        metadata: dict | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
        can_use_tool=None,
        working_directory: str | None = None,
        max_turns: int | None = None,
        model_visible_tool_names: set[str] | None = None,
        task_budget: int | None = None,
        task_budget_source: str | None = None,
        task_budget_hint_positions: list[tuple[int, int]] | None = None,
        task_budget_remaining: int | None = None,
        task_budget_recovered: bool = False,
        tool_result_budget_state: dict | None = None,
        enable_outer_compaction: bool = True,
    ) -> Iterable[RuntimeEvent]:
        for event in run_query(
            QueryParams(
                query=query,
                history=history,
                session_id=session_id,
                model_client=self.runtime_loop.model_client,
                tool_registry=self.runtime_loop.tool_registry,
                hooks=self.runtime_loop.hooks,
                system_prompt=system_prompt or self.runtime_loop.system_prompt,
                system_prompt_blocks=system_prompt_blocks,
                system_reminders=system_reminders,
                user_context=user_context,
                metadata=metadata,
                can_use_tool=can_use_tool or self.runtime_loop.can_use_tool,
                working_directory=working_directory,
                max_tool_calls_per_turn=self.runtime_loop.max_tool_calls_per_turn,
                model_visible_tool_names=model_visible_tool_names,
                queue_manager=self.queue_manager,
                compaction_engine=self.compaction_engine,
                memory_extractor=self.memory_extractor,
                task_registry=self.task_registry,
                active_task_id=task_id,
                active_agent_id=agent_id,
                max_turns=max_turns,
                task_budget=task_budget,
                task_budget_source=task_budget_source,
                task_budget_hint_positions=task_budget_hint_positions or [],
                task_budget_remaining=task_budget_remaining,
                task_budget_recovered=task_budget_recovered,
                tool_result_budget_state=tool_result_budget_state,
                tool_result_keep_recent=self.tool_result_keep_recent,
                tool_result_max_model_chars=self.tool_result_max_model_chars,
                tool_result_preview_chars=self.tool_result_preview_chars,
                enable_outer_compaction=enable_outer_compaction,
            )
        ):
            if event.type == "done":
                diagnostics = _provider_payload_diagnostics(self.runtime_loop.model_client)
                if diagnostics is not None:
                    event = RuntimeEvent(
                        type=event.type,
                        payload={
                            **event.payload,
                            "provider_payload_diagnostics": diagnostics,
                        },
                    )
            yield event


def _provider_payload_diagnostics(model_client: object) -> dict | None:
    diagnostics = getattr(model_client, "last_provider_payload_diagnostics", None)
    if not isinstance(diagnostics, dict):
        return None
    schema_version = diagnostics.get("schema_version")
    normalization = diagnostics.get("normalization")
    if schema_version != 1 or not isinstance(normalization, dict) or not normalization:
        return None
    compact_normalization = {
        key: int(value)
        for key, value in normalization.items()
        if isinstance(key, str) and isinstance(value, int) and value > 0
    }
    if not compact_normalization:
        return None
    return {
        "schema_version": 1,
        "normalization": compact_normalization,
    }
