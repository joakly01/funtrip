from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from audit_agent_runtime.compaction.engine import CompactionEngine
from audit_agent_runtime.compaction.context_compression import (
    is_compaction_summary_text,
    render_compacted_history_reminder,
)
from audit_agent_runtime.memory.extractor import MemoryExtractor
from audit_agent_runtime.llm.base import ModelMessage
from audit_agent_runtime.querying.query_loop import (
    QueryLoopParams,
    queryLoop,
    rehydrate_history_for_model,
)
from audit_agent_runtime.querying.tool_result_budget import (
    apply_tool_result_message_budget_with_stats,
    clear_old_tool_results_with_stats,
    deserialize_tool_result_budget_state,
    serialize_tool_result_budget_state,
)
from audit_agent_runtime.querying.tool_result_collapse import DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS
from audit_agent_runtime.runtime.events import RuntimeEvent
from audit_agent_runtime.tasks.queue_manager import QueueManager
from audit_agent_runtime.tasks.task_registry import TaskRegistry


@dataclass(slots=True)
class QueryParams:
    query: object
    history: list[dict]
    session_id: str
    model_client: object
    tool_registry: object
    hooks: list | None = None
    system_prompt: str | None = None
    system_prompt_blocks: list[str] | None = None
    system_reminders: list[str] | None = None
    user_context: dict[str, str] | None = None
    metadata: dict | None = None
    can_use_tool: object | None = None
    working_directory: str | None = None
    max_tool_calls_per_turn: int = 8
    model_visible_tool_names: set[str] | None = None
    queue_manager: QueueManager | None = None
    compaction_engine: CompactionEngine | None = None
    memory_extractor: MemoryExtractor | None = None
    task_registry: TaskRegistry | None = None
    active_task_id: str | None = None
    active_agent_id: str | None = None
    max_turns: int | None = None
    task_budget: int | None = None
    task_budget_remaining: int | None = None
    task_budget_source: str | None = None
    task_budget_hint_positions: list[tuple[int, int]] = field(default_factory=list)
    task_budget_recovered: bool = False
    tool_result_budget_state: dict | None = None
    tool_result_keep_recent: int | None = None
    tool_result_max_model_chars: int = DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS
    tool_result_preview_chars: int = 2_000
    enable_outer_compaction: bool = True


def query(params: QueryParams) -> Iterator[RuntimeEvent]:
    history = _normalize_outer_history(params.history)
    memory_source_history = list(history)
    effective_system_prompt = params.system_prompt
    effective_system_prompt_blocks = list(params.system_prompt_blocks or [])

    if params.memory_extractor is not None:
        memory = params.memory_extractor.extract(memory_source_history)
        if memory.summary:
            memory_section = f"Relevant memory:\n{memory.summary}"
            if effective_system_prompt_blocks:
                effective_system_prompt_blocks.append(memory_section)
            else:
                effective_system_prompt = (
                    memory_section
                    if not effective_system_prompt
                    else f"{effective_system_prompt}\n\n{memory_section}"
                )

    task_budget_remaining = params.task_budget_remaining
    tool_result_budget_state = params.tool_result_budget_state
    initial_tool_result_context: dict[str, dict] | None = None
    if params.enable_outer_compaction and params.compaction_engine is not None:
        # 外层 compact 发生在 queryLoop 之前；这里先冻结 tool_result budget 决定，
        # 避免被 compact 成摘要的历史结果在 resume 时丢失稳定 replacement state。
        history, tool_result_budget_state, initial_tool_result_context = (
            _apply_pre_compaction_tool_result_context(
                history=history,
                tool_result_budget_state=tool_result_budget_state,
                tool_result_keep_recent=params.tool_result_keep_recent,
                tool_result_max_model_chars=params.tool_result_max_model_chars,
                tool_result_preview_chars=params.tool_result_preview_chars,
            )
        )
        pre_compact_history = list(history)
        compacted = params.compaction_engine.compact(history)
        if compacted.summary:
            yield RuntimeEvent(
                type="compaction",
                payload={"summary": compacted.summary},
            )
            task_budget_remaining = _next_task_budget_remaining(
                total_budget=params.task_budget,
                current_remaining=task_budget_remaining,
                history=pre_compact_history,
            )
            history = compacted.retained_messages
            history = [
                {
                    "role": "user",
                    "payload": {
                        "text": _build_outer_compaction_summary_text(compacted.summary)
                    },
                }
            ] + history

    yield from queryLoop(
        QueryLoopParams(
            query=params.query,
            history=history,
            session_id=params.session_id,
            model_client=params.model_client,
            tool_registry=params.tool_registry,
            hooks=params.hooks,
            system_prompt=effective_system_prompt,
            system_prompt_blocks=effective_system_prompt_blocks or None,
            system_reminders=params.system_reminders,
            user_context=params.user_context,
            metadata=params.metadata,
            can_use_tool=params.can_use_tool,
            working_directory=params.working_directory,
            max_tool_calls_per_turn=params.max_tool_calls_per_turn,
            model_visible_tool_names=params.model_visible_tool_names,
            queue_manager=params.queue_manager,
            task_registry=params.task_registry,
            active_task_id=params.active_task_id,
            active_agent_id=params.active_agent_id,
            max_turns=params.max_turns,
            task_budget=params.task_budget,
            task_budget_remaining=task_budget_remaining,
            task_budget_source=params.task_budget_source,
            task_budget_hint_positions=params.task_budget_hint_positions,
            task_budget_recovered=params.task_budget_recovered,
            tool_result_budget_state=tool_result_budget_state,
            initial_tool_result_context=initial_tool_result_context,
            tool_result_keep_recent=params.tool_result_keep_recent,
            tool_result_max_model_chars=params.tool_result_max_model_chars,
            tool_result_preview_chars=params.tool_result_preview_chars,
            compaction_engine=params.compaction_engine,
        )
    )


def _normalize_outer_history(history: list[dict]) -> list[dict]:
    normalized = [message for message in history if isinstance(message, dict)]
    while normalized and _is_compaction_summary_history_message(normalized[0]):
        normalized.pop(0)
    return normalized


def _apply_pre_compaction_tool_result_context(
    *,
    history: list[dict],
    tool_result_budget_state: dict | None,
    tool_result_keep_recent: int | None,
    tool_result_max_model_chars: int,
    tool_result_preview_chars: int,
) -> tuple[list[dict], dict, dict[str, dict]]:
    state = deserialize_tool_result_budget_state(tool_result_budget_state)
    messages = _model_messages_from_history(history)
    context: dict[str, dict] = {}

    budgeted = apply_tool_result_message_budget_with_stats(
        messages,
        state=state,
        max_chars=tool_result_max_model_chars,
        preview_chars=tool_result_preview_chars,
    )
    managed_messages = budgeted.messages
    context["budget"] = budgeted.stats

    if tool_result_keep_recent is not None:
        cleared = clear_old_tool_results_with_stats(
            managed_messages,
            keep_recent=tool_result_keep_recent,
        )
        managed_messages = cleared.messages
        context["clearing"] = cleared.stats

    return (
        _history_from_model_messages(managed_messages),
        serialize_tool_result_budget_state(state),
        context,
    )


def _model_messages_from_history(history: list[dict]) -> list[ModelMessage]:
    return rehydrate_history_for_model(history)


def _history_from_model_messages(messages: list[ModelMessage]) -> list[dict]:
    return [
        {"role": message.role, "payload": _payload_from_model_message(message)}
        for message in messages
    ]


def _payload_from_model_message(message: ModelMessage) -> dict:
    payload: dict = {}
    if isinstance(message.content, list):
        payload["content"] = message.content
    else:
        payload["text"] = "" if message.content is None else str(message.content)
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.call_id,
                "name": tool_call.tool_name,
                "arguments": tool_call.arguments,
            }
            for tool_call in message.tool_calls
        ]
    if message.role == "tool":
        payload = {
            "tool_name": message.name,
            "tool_call_id": message.tool_call_id,
            "result": message.content,
        }
    return payload


def _is_compaction_summary_history_message(message: dict) -> bool:
    if message.get("role") not in {"system", "user"}:
        return False
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        return False
    text = payload.get("text")
    return isinstance(text, str) and is_compaction_summary_text(text)


def _build_outer_compaction_summary_text(summary: str) -> str:
    return render_compacted_history_reminder(summary)


def _next_task_budget_remaining(
    *,
    total_budget: int | None,
    current_remaining: int | None,
    history: list[dict],
) -> int | None:
    if total_budget is None:
        return None
    baseline = total_budget if current_remaining is None else current_remaining
    estimated_spend = _estimate_history_token_cost(history)
    return max(0, baseline - estimated_spend)


def _estimate_history_token_cost(history: list[dict]) -> int:
    total_chars = 0
    for message in history:
        payload = message.get("payload", {}) if isinstance(message, dict) else {}
        if not isinstance(payload, dict):
            continue
        text = payload.get("text")
        if isinstance(text, str):
            total_chars += len(text)
        content = payload.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    total_chars += len("" if block is None else str(block))
                    continue
                if isinstance(block.get("text"), str):
                    total_chars += len(block["text"])
                if isinstance(block.get("content"), str):
                    total_chars += len(block["content"])
        elif isinstance(content, str):
            total_chars += len(content)
    return max(1, total_chars // 4) if total_chars else 0
