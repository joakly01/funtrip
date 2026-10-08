from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import inspect
import json
import logging
import re
import time

from audit_agent_runtime.compaction.engine import CompactionEngine
from audit_agent_runtime.hooks.base import Hook
from audit_agent_runtime.hooks.manager import HookManager
from audit_agent_runtime.llm.base import ModelClient, ModelMessage, ToolCall
from audit_agent_runtime.compaction.context_compression import (
    is_compaction_summary_text,
    render_compacted_history_reminder,
)
from audit_agent_runtime.prompts.api_assembly import (
    build_system_prompt_blocks,
    prepend_user_context,
)
from audit_agent_runtime.prompts.reminders import wrap_in_system_reminder
from audit_agent_runtime.runtime.events import RuntimeEvent
from audit_agent_runtime.tasks.notifications import (
    notification_to_xml_payload,
    render_notification_transcript_text,
)
from audit_agent_runtime.tasks.queue_manager import QueueManager
from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.tools.core.executor import ToolExecutionContext, ToolExecutor
from audit_agent_runtime.tools.core.pool import build_tool_pool_context
from audit_agent_runtime.tools.core.registry import ToolRegistry
from audit_agent_runtime.querying.tool_result_budget import (
    ToolResultBudgetState,
    apply_tool_result_message_budget_with_stats,
    clear_old_tool_results_with_stats,
    deserialize_tool_result_budget_state,
    serialize_tool_result_budget_state,
)
from audit_agent_runtime.querying.tool_use_summary import (
    build_tool_use_summary_message as _build_tool_use_summary_message,
    build_tool_use_summary_payload as _build_tool_use_summary_payload,
    summarize_completed_tools as _summarize_completed_tools,
)
from audit_agent_runtime.querying.tool_result_collapse import (
    DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS,
    collapse_tool_result_for_model,
)
from audit_agent_runtime.utils.json_utils import safe_json_dumps


logger = logging.getLogger(__name__)

MAX_DUPLICATE_TOOL_SKIPS_PER_TURN = 3


@dataclass(slots=True)
class QueryLoopParams:
    query: object
    history: list[dict]
    session_id: str
    model_client: ModelClient
    tool_registry: ToolRegistry
    hooks: list[Hook] | None = None
    system_prompt: str | None = None
    system_prompt_blocks: list[str] | None = None
    system_reminders: list[str] | None = None
    user_context: dict[str, str] | None = None
    metadata: dict | None = None
    can_use_tool: object | None = None
    working_directory: str | None = None
    max_tool_calls_per_turn: int = 16
    model_visible_tool_names: set[str] | None = None
    queue_manager: QueueManager | None = None
    task_registry: TaskRegistry | None = None
    active_task_id: str | None = None
    active_agent_id: str | None = None
    max_output_tokens_recovery_limit: int = 3
    max_turns: int | None = None
    task_budget: int | None = None
    task_budget_remaining: int | None = None
    task_budget_source: str | None = None
    task_budget_hint_positions: list[tuple[int, int]] | list[list[int]] | None = None
    task_budget_recovered: bool = False
    compaction_engine: CompactionEngine | None = None
    tool_result_budget_state: dict | None = None
    initial_tool_result_context: dict | None = None
    tool_result_keep_recent: int | None = None
    tool_result_max_model_chars: int = DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS
    tool_result_preview_chars: int = 2_000


def queryLoop(params: QueryLoopParams) -> Iterator[RuntimeEvent]:
    """执行单次用户输入对应的模型/工具内循环。

    这个函数只负责 inner loop：组装本轮模型上下文、调用模型、执行模型请求的工具、
    把工具结果追加回消息历史，并在需要时继续下一次模型采样。外层 session 持久化、
    SSE 事件落库和历史恢复由 QueryEngine/query() 负责。
    """
    hook_manager = HookManager(params.hooks)
    tool_executor = ToolExecutor(params.tool_registry, can_use_tool=params.can_use_tool)
    visible_tool_definitions = _model_visible_tool_definitions(
        params.tool_registry.definitions(),
        params.model_visible_tool_names,
    )
    messages = _build_messages_with_prompt(
        params.history,
        params.query,
        system_prompt=params.system_prompt,
        system_prompt_blocks=params.system_prompt_blocks,
        system_reminders=params.system_reminders,
        user_context=params.user_context,
    )
    yield RuntimeEvent(
        type="trace",
        payload={"history_count": len(params.history), "session_id": params.session_id},
    )
    _log_loop_phase(
        params.session_id,
        "start",
        history_count=len(params.history),
        message_count=len(messages),
        visible_tool_count=len(visible_tool_definitions),
        active_task_id=params.active_task_id,
        active_agent_id=params.active_agent_id,
    )
    executed_tool_keys: set[str] = set()
    completed_tools: list[dict[str, object]] = []
    tool_call_count = 0
    max_output_tokens_recovery_count = 0
    last_transition: dict | None = None
    last_reactive_compact_context: dict | None = None
    last_tool_use_summary: dict | None = None
    turn_count = 1
    model_usage_summary: dict[str, object] = _empty_model_usage_summary()
    task_budget_remaining = params.task_budget_remaining
    budget_started_at_ms = int(time.monotonic() * 1000)
    budget_summary_count = 0
    post_tool_progress_recovery_count = 0
    duplicate_tool_skip_count = 0
    # 记录每个 tool_use_id 的预算裁剪决定。该状态会随 done payload 回写
    # session_runtime_state，下一轮恢复后复用同一 replacement，避免 resume 后
    # 同一工具结果被重新裁剪成不同内容，破坏 Claude Code 风格的上下文稳定性。
    tool_result_budget_state = deserialize_tool_result_budget_state(
        params.tool_result_budget_state
    )
    tool_result_context_stats: dict[str, dict] = {
        key: dict(value)
        for key, value in (params.initial_tool_result_context or {}).items()
        if isinstance(key, str) and isinstance(value, dict)
    }

    while True:
        # 第一阶段：先冻结 tool_result 上下文治理决定，再处理已有历史的 reactive compaction。
        # 如果先 compact，压进 summary 的 tool_result 就无法写入 replacement state，resume 时会失去稳定重放依据。
        messages = _apply_tool_result_context_management(
            messages=messages,
            tool_result_budget_state=tool_result_budget_state,
            tool_result_context_stats=tool_result_context_stats,
            tool_result_keep_recent=params.tool_result_keep_recent,
            tool_result_max_model_chars=params.tool_result_max_model_chars,
            tool_result_preview_chars=params.tool_result_preview_chars,
        )
        messages, task_budget_remaining, last_reactive_compact_context = yield from _apply_reactive_compaction_phase(
            messages=messages,
            compaction_engine=params.compaction_engine,
            task_budget=params.task_budget,
            task_budget_remaining=task_budget_remaining,
            last_reactive_compact_context=last_reactive_compact_context,
        )
        stop_event = _build_stop_requested_done_event(
            task_registry=params.task_registry,
            active_task_id=params.active_task_id,
            hook_manager=hook_manager,
            session_id=params.session_id,
            task_budget=params.task_budget,
            task_budget_remaining=task_budget_remaining,
            messages=messages,
            last_transition=last_transition,
            last_reactive_compact_context=last_reactive_compact_context,
        )
        if stop_event is not None:
            yield stop_event
            return

        # 第二阶段：主线程优先 drain 当前 agent 可见的 queued commands，再决定是否继续采样。
        yield from _drain_commands(
            queue_manager=params.queue_manager,
            session_id=params.session_id,
            max_priority="next",
            messages=messages,
            target_agent_id=params.active_agent_id,
        )
        messages = _apply_tool_result_context_management(
            messages=messages,
            tool_result_budget_state=tool_result_budget_state,
            tool_result_context_stats=tool_result_context_stats,
            tool_result_keep_recent=params.tool_result_keep_recent,
            tool_result_max_model_chars=params.tool_result_max_model_chars,
            tool_result_preview_chars=params.tool_result_preview_chars,
        )
        messages, task_budget_remaining, last_reactive_compact_context = yield from _apply_reactive_compaction_phase(
            messages=messages,
            compaction_engine=params.compaction_engine,
            task_budget=params.task_budget,
            task_budget_remaining=task_budget_remaining,
            last_reactive_compact_context=last_reactive_compact_context,
        )

        # 第三阶段：进入模型采样/工具执行主链。一次 turn 从这里开始，
        # 到遇到工具调用后 continue，或最终产出 done 事件时结束。
        hook_manager.before_model_call(messages=list(messages), session_id=params.session_id)
        stream_method = getattr(params.model_client, "stream", None)
        _log_loop_phase(
            params.session_id,
            "model_call_start",
            turn=turn_count,
            message_count=len(messages),
            visible_tool_count=len(visible_tool_definitions),
            stream=callable(stream_method),
        )
        if callable(stream_method):
            # 流式模型分支：先收集增量文本/工具调用，再决定本轮是继续、总结还是结束。
            streamed_tool_calls: list[ToolCall] = []
            saw_stream = False
            finish_reason: str | None = None
            streamed_assistant_parts: list[str] = []
            streamed_reasoning_parts: list[str] = []
            try:
                for event in stream_method(
                    **_model_invocation_kwargs(
                        method=stream_method,
                        messages=messages,
                        tools=visible_tool_definitions,
                        metadata=params.metadata,
                        task_budget=params.task_budget,
                        task_budget_remaining=task_budget_remaining,
                    ),
                ):
                    saw_stream = True
                    if event.type == "done":
                        finish_reason = event.stop_reason
                        _accumulate_model_usage(
                            model_usage_summary,
                            usage=event.usage,
                            turn=turn_count,
                            stream=True,
                        )
                        if event.reasoning_content and not streamed_reasoning_parts:
                            streamed_reasoning_parts.append(event.reasoning_content)
                    if event.type == "reasoning_delta" and event.text:
                        streamed_reasoning_parts.append(event.text)
                        yield RuntimeEvent(type="thinking", payload={"text": event.text})
                    elif event.type == "text_delta" and event.text:
                        streamed_assistant_parts.append(event.text)
                        yield RuntimeEvent(type="assistant", payload={"text": event.text})
                    elif event.type == "tool_call" and event.tool_call is not None:
                        if event.reasoning_content and not streamed_reasoning_parts:
                            streamed_reasoning_parts.append(event.reasoning_content)
                        streamed_tool_calls.append(event.tool_call)
            except Exception as exc:
                _log_loop_phase(
                    params.session_id,
                    "model_call_error",
                    turn=turn_count,
                    stream=True,
                    error_type=type(exc).__name__,
                )
                error_message = _model_error_message(exc)
                yield RuntimeEvent(type="assistant", payload={"text": error_message})
                yield _done_event(
                    hook_manager=hook_manager,
                    session_id=params.session_id,
                    stop_reason="model_error",
                    tool_result_budget_state=tool_result_budget_state,
                    tool_result_context=tool_result_context_stats,
                    task_budget=params.task_budget,
                    task_budget_remaining=_effective_task_budget_remaining(
                        total_budget=params.task_budget,
                        current_remaining=task_budget_remaining,
                        messages=messages,
                    ),
                    error=str(exc),
                    transition=last_transition,
                    reactive_compact_context=last_reactive_compact_context,
                    model_usage=model_usage_summary,
                )
                return
            if saw_stream:
                resolved_stop_reason = finish_reason or (
                    "tool_calls" if streamed_tool_calls else "end_turn"
                )
                _log_loop_phase(
                    params.session_id,
                    "model_call_done",
                    turn=turn_count,
                    stop_reason=resolved_stop_reason,
                    tool_call_count=len(streamed_tool_calls),
                    output_chars=len("".join(streamed_assistant_parts)),
                    stream=True,
                )
                _log_model_response_output(
                    session_id=params.session_id,
                    turn=turn_count,
                    stream=True,
                    output_text="".join(streamed_assistant_parts),
                    reasoning_content="".join(streamed_reasoning_parts),
                    tool_calls=streamed_tool_calls,
                    stop_reason=resolved_stop_reason,
                )
                hook_manager.after_sampling(
                    output_text="".join(streamed_assistant_parts),
                    tool_calls=streamed_tool_calls,
                    stop_reason=resolved_stop_reason,
                    session_id=params.session_id,
                )
                if streamed_tool_calls:
                    # 工具调用分支：把 assistant/tool 轨迹写回消息历史，并根据预算决定是继续还是总结。
                    hook_manager.after_model_response(
                        output_text="".join(streamed_assistant_parts),
                        stop_reason=resolved_stop_reason,
                        session_id=params.session_id,
                    )
                    messages.append(
                        ModelMessage(
                            role="assistant",
                            content="".join(streamed_assistant_parts),
                            tool_calls=streamed_tool_calls,
                            reasoning_content="".join(streamed_reasoning_parts) or None,
                        )
                    )
                    for runtime_event in _execute_tool_calls(
                        messages=messages,
                        session_id=params.session_id,
                        tool_calls=streamed_tool_calls,
                        assistant_text="".join(streamed_assistant_parts),
                        assistant_reasoning_content="".join(streamed_reasoning_parts),
                        hook_manager=hook_manager,
                        tool_executor=tool_executor,
                        executed_tool_keys=executed_tool_keys,
                        completed_tools=completed_tools,
                        max_tool_calls_per_turn=params.max_tool_calls_per_turn,
                        tool_call_count=tool_call_count,
                        task_budget=params.task_budget,
                        task_budget_remaining=task_budget_remaining,
                        working_directory=params.working_directory,
                        active_task_id=params.active_task_id,
                        active_agent_id=params.active_agent_id,
                        metadata=params.metadata,
                        reactive_compact_context=last_reactive_compact_context,
                    ):
                        if runtime_event.type == "tool_use":
                            tool_call_count += 1
                            duplicate_tool_skip_count = 0
                        if runtime_event.type == "transition":
                            last_transition = dict(runtime_event.payload)
                        yield runtime_event
                        if runtime_event.type == "done":
                            return
                        if runtime_event.type == "tool_skipped":
                            duplicate_tool_skip_count += 1
                            if duplicate_tool_skip_count >= MAX_DUPLICATE_TOOL_SKIPS_PER_TURN:
                                yield from _build_duplicate_tool_calls_limit_events(
                                    hook_manager=hook_manager,
                                    session_id=params.session_id,
                                    task_budget=params.task_budget,
                                    task_budget_remaining=_effective_task_budget_remaining(
                                        total_budget=params.task_budget,
                                        current_remaining=task_budget_remaining,
                                        messages=messages,
                                    ),
                                    reactive_compact_context=last_reactive_compact_context,
                                )
                                return
                    batch_tool_summary = _build_tool_use_summary_payload(completed_tools)
                    if batch_tool_summary is not None:
                        last_tool_use_summary = batch_tool_summary
                        messages.append(_build_tool_use_summary_message(batch_tool_summary))
                        yield RuntimeEvent(type="tool_use_summary", payload=batch_tool_summary)
                    task_budget_summary_message = _build_task_budget_summary_message(
                        messages=messages,
                        total_budget=params.task_budget,
                        current_remaining=task_budget_remaining,
                    )
                    if task_budget_summary_message is not None:
                        messages.append(task_budget_summary_message)
                        last_transition = _task_budget_transition_payload(
                            reason="task_budget_summary",
                            mode="summary",
                            total_budget=params.task_budget,
                            current_remaining=task_budget_remaining,
                            task_budget_source=params.task_budget_source,
                            task_budget_hint_positions=params.task_budget_hint_positions,
                            task_budget_recovered=params.task_budget_recovered,
                            messages=messages,
                            continuation_count=budget_summary_count,
                            duration_ms=max(0, int(time.monotonic() * 1000) - budget_started_at_ms),
                        )
                        budget_summary_count += 1
                        yield RuntimeEvent(type="transition", payload=last_transition)
                    next_turn_count = turn_count + 1
                    if params.max_turns is not None and next_turn_count > params.max_turns:
                        yield RuntimeEvent(
                            type="assistant",
                            payload={"text": _runtime_limit_message("max_turns", turn_count=next_turn_count)},
                        )
                        yield _done_event(
                            hook_manager=hook_manager,
                            session_id=params.session_id,
                            stop_reason="max_turns",
                            tool_result_budget_state=tool_result_budget_state,
                            tool_result_context=tool_result_context_stats,
                            task_budget=params.task_budget,
                            task_budget_remaining=_effective_task_budget_remaining(
                                total_budget=params.task_budget,
                                current_remaining=task_budget_remaining,
                                messages=messages,
                            ),
                            turn_count=next_turn_count,
                            transition=last_transition,
                            reactive_compact_context=last_reactive_compact_context,
                            tool_use_summary=last_tool_use_summary,
                            model_usage=model_usage_summary,
                        )
                        return
                    turn_count = next_turn_count
                    _log_loop_phase(
                        params.session_id,
                        "continue_after_tools",
                        next_turn=turn_count,
                        completed_tool_count=len(completed_tools),
                    )
                    continue
                # 纯文本分支：没有工具调用时，再根据 stop_reason 决定是否走 max_output_tokens 恢复。
                next_turn_count = turn_count + 1
                if params.max_turns is not None and next_turn_count > params.max_turns:
                    yield RuntimeEvent(
                        type="assistant",
                        payload={"text": _runtime_limit_message("max_turns", turn_count=next_turn_count)},
                    )
                    yield _done_event(
                        hook_manager=hook_manager,
                        session_id=params.session_id,
                        stop_reason="max_turns",
                        tool_result_budget_state=tool_result_budget_state,
                        tool_result_context=tool_result_context_stats,
                        task_budget=params.task_budget,
                        task_budget_remaining=_effective_task_budget_remaining(
                            total_budget=params.task_budget,
                            current_remaining=task_budget_remaining,
                            messages=messages,
                        ),
                        turn_count=next_turn_count,
                        transition=last_transition,
                        reactive_compact_context=last_reactive_compact_context,
                        tool_use_summary=last_tool_use_summary,
                        model_usage=model_usage_summary,
                    )
                    return
                if streamed_assistant_parts:
                    messages.append(
                        ModelMessage(
                            role="assistant",
                            content="".join(streamed_assistant_parts),
                        )
                    )
                if _should_continue_after_tool_progress_text(
                    text="".join(streamed_assistant_parts),
                    completed_tools=completed_tools,
                    recovery_count=post_tool_progress_recovery_count,
                ):
                    messages.append(_build_post_tool_progress_recovery_message())
                    post_tool_progress_recovery_count += 1
                    last_transition = {
                        "reason": "post_tool_progress_recovery",
                        "attempt": post_tool_progress_recovery_count,
                    }
                    yield RuntimeEvent(type="transition", payload=last_transition)
                    turn_count = next_turn_count
                    continue
                stream_was_truncated = finish_reason in {"length", "max_output_tokens"}
                recovery_attempts_available = max_output_tokens_recovery_count < params.max_output_tokens_recovery_limit
                should_recover_stream_output = stream_was_truncated and recovery_attempts_available
                if should_recover_stream_output:
                    # 模型因为输出长度限制提前停住时，不直接结束 turn。
                    # 这里追加一条内部恢复提示，让下一次采样继续补完答案。
                    hook_manager.after_model_response(
                        output_text="".join(streamed_assistant_parts),
                        stop_reason=finish_reason,
                        session_id=params.session_id,
                    )
                    next_turn_count = turn_count + 1
                    if params.max_turns is not None and next_turn_count > params.max_turns:
                        yield RuntimeEvent(
                            type="assistant",
                            payload={"text": _runtime_limit_message("max_turns", turn_count=next_turn_count)},
                        )
                        yield _done_event(
                            hook_manager=hook_manager,
                            session_id=params.session_id,
                            stop_reason="max_turns",
                            tool_result_budget_state=tool_result_budget_state,
                            tool_result_context=tool_result_context_stats,
                            task_budget=params.task_budget,
                            task_budget_remaining=_effective_task_budget_remaining(
                                total_budget=params.task_budget,
                                current_remaining=task_budget_remaining,
                                messages=messages,
                            ),
                            turn_count=next_turn_count,
                            transition=last_transition,
                            reactive_compact_context=last_reactive_compact_context,
                            tool_use_summary=last_tool_use_summary,
                            model_usage=model_usage_summary,
                        )
                        return
                    recovery_message = _build_max_output_tokens_recovery_message()
                    messages.append(recovery_message)
                    max_output_tokens_recovery_count += 1
                    last_transition = {
                        "reason": "max_output_tokens_recovery",
                        "attempt": max_output_tokens_recovery_count,
                    }
                    yield RuntimeEvent(
                        type="transition",
                        payload=last_transition,
                    )
                    turn_count = next_turn_count
                    continue
                # 流式纯文本的正常结束点：
                # 1. 没有 tool_calls；
                # 2. 不需要 max_output_tokens 恢复；
                # 3. 最终只 drain 普通 send_message continuation；
                # 4. yield done 后 return，本轮 queryLoop 结束。
                hook_manager.after_model_response(
                    output_text="".join(streamed_assistant_parts),
                    stop_reason=resolved_stop_reason,
                    session_id=params.session_id,
                )
                yield from _drain_commands(
                    queue_manager=params.queue_manager,
                    session_id=params.session_id,
                    max_priority="later",
                    messages=messages,
                    target_agent_id=params.active_agent_id,
                    allowed_kinds={"send_message"},
                )
                yield _done_event(
                    hook_manager=hook_manager,
                    session_id=params.session_id,
                    stop_reason=finish_reason or "end_turn",
                    tool_result_budget_state=tool_result_budget_state,
                    tool_result_context=tool_result_context_stats,
                    task_budget=params.task_budget,
                    task_budget_remaining=_effective_task_budget_remaining(
                        total_budget=params.task_budget,
                        current_remaining=task_budget_remaining,
                        messages=messages,
                    ),
                    transition=last_transition,
                    reactive_compact_context=last_reactive_compact_context,
                    tool_use_summary=last_tool_use_summary,
                    model_usage=model_usage_summary,
                )
                return

        # 非流式模型分支：语义与流式分支保持一致，只是一次拿到完整 response。
        try:
            response = params.model_client.complete(
                **_model_invocation_kwargs(
                    method=params.model_client.complete,
                    messages=messages,
                    tools=visible_tool_definitions,
                    metadata=params.metadata,
                    task_budget=params.task_budget,
                    task_budget_remaining=task_budget_remaining,
                ),
            )
        except Exception as exc:
            _log_loop_phase(
                params.session_id,
                "model_call_error",
                turn=turn_count,
                stream=False,
                error_type=type(exc).__name__,
            )
            error_message = _model_error_message(exc)
            yield RuntimeEvent(type="assistant", payload={"text": error_message})
            yield _done_event(
                hook_manager=hook_manager,
                session_id=params.session_id,
                stop_reason="model_error",
                tool_result_budget_state=tool_result_budget_state,
                tool_result_context=tool_result_context_stats,
                task_budget=params.task_budget,
                task_budget_remaining=_effective_task_budget_remaining(
                    total_budget=params.task_budget,
                    current_remaining=task_budget_remaining,
                    messages=messages,
                ),
                error=str(exc),
                transition=last_transition,
                reactive_compact_context=last_reactive_compact_context,
                tool_use_summary=last_tool_use_summary,
                model_usage=model_usage_summary,
            )
            return
        _accumulate_model_usage(
            model_usage_summary,
            usage=getattr(response, "usage", None),
            turn=turn_count,
            stream=False,
        )
        resolved_stop_reason = response.stop_reason or (
            "tool_calls" if response.tool_calls else "end_turn"
        )
        _log_loop_phase(
            params.session_id,
            "model_call_done",
            turn=turn_count,
            stop_reason=resolved_stop_reason,
            tool_call_count=len(response.tool_calls),
            output_chars=len(response.output_text),
            stream=False,
        )
        _log_model_response_output(
            session_id=params.session_id,
            turn=turn_count,
            stream=False,
            output_text=response.output_text,
            reasoning_content=response.reasoning_content or "",
            tool_calls=response.tool_calls,
            stop_reason=resolved_stop_reason,
        )
        hook_manager.after_sampling(
            output_text=response.output_text,
            tool_calls=response.tool_calls,
            stop_reason=resolved_stop_reason,
            session_id=params.session_id,
        )
        if response.tool_calls:
            # 工具调用分支：先执行工具，再根据预算/turn 限制判断是否继续下一轮。
            hook_manager.after_model_response(
                output_text=response.output_text,
                stop_reason=resolved_stop_reason,
                session_id=params.session_id,
            )
            messages.append(
                ModelMessage(
                    role="assistant",
                    content=response.output_text,
                    tool_calls=response.tool_calls,
                    reasoning_content=response.reasoning_content,
                )
            )
            for runtime_event in _execute_tool_calls(
                messages=messages,
                session_id=params.session_id,
                tool_calls=response.tool_calls,
                assistant_text=response.output_text,
                assistant_reasoning_content=response.reasoning_content or "",
                hook_manager=hook_manager,
                tool_executor=tool_executor,
                executed_tool_keys=executed_tool_keys,
                completed_tools=completed_tools,
                max_tool_calls_per_turn=params.max_tool_calls_per_turn,
                tool_call_count=tool_call_count,
                task_budget=params.task_budget,
                task_budget_remaining=task_budget_remaining,
                working_directory=params.working_directory,
                active_task_id=params.active_task_id,
                active_agent_id=params.active_agent_id,
                metadata=params.metadata,
                reactive_compact_context=last_reactive_compact_context,
            ):
                if runtime_event.type == "tool_use":
                    tool_call_count += 1
                    duplicate_tool_skip_count = 0
                if runtime_event.type == "transition":
                    last_transition = dict(runtime_event.payload)
                yield runtime_event
                if runtime_event.type == "done":
                    return
                if runtime_event.type == "tool_skipped":
                    duplicate_tool_skip_count += 1
                    if duplicate_tool_skip_count >= MAX_DUPLICATE_TOOL_SKIPS_PER_TURN:
                        yield from _build_duplicate_tool_calls_limit_events(
                            hook_manager=hook_manager,
                            session_id=params.session_id,
                            task_budget=params.task_budget,
                            task_budget_remaining=_effective_task_budget_remaining(
                                total_budget=params.task_budget,
                                current_remaining=task_budget_remaining,
                                messages=messages,
                            ),
                            reactive_compact_context=last_reactive_compact_context,
                        )
                        return
            batch_tool_summary = _build_tool_use_summary_payload(completed_tools)
            if batch_tool_summary is not None:
                last_tool_use_summary = batch_tool_summary
                messages.append(_build_tool_use_summary_message(batch_tool_summary))
                yield RuntimeEvent(type="tool_use_summary", payload=batch_tool_summary)
            task_budget_summary_message = _build_task_budget_summary_message(
                messages=messages,
                total_budget=params.task_budget,
                current_remaining=task_budget_remaining,
            )
            if task_budget_summary_message is not None:
                messages.append(task_budget_summary_message)
                last_transition = _task_budget_transition_payload(
                    reason="task_budget_summary",
                    mode="summary",
                    total_budget=params.task_budget,
                    current_remaining=task_budget_remaining,
                    task_budget_source=params.task_budget_source,
                    task_budget_hint_positions=params.task_budget_hint_positions,
                    task_budget_recovered=params.task_budget_recovered,
                    messages=messages,
                    continuation_count=budget_summary_count,
                    duration_ms=max(0, int(time.monotonic() * 1000) - budget_started_at_ms),
                )
                budget_summary_count += 1
                yield RuntimeEvent(type="transition", payload=last_transition)
            next_turn_count = turn_count + 1
            if params.max_turns is not None and next_turn_count > params.max_turns:
                yield RuntimeEvent(
                    type="assistant",
                    payload={"text": _runtime_limit_message("max_turns", turn_count=next_turn_count)},
                )
                yield _done_event(
                    hook_manager=hook_manager,
                    session_id=params.session_id,
                    stop_reason="max_turns",
                    tool_result_budget_state=tool_result_budget_state,
                    tool_result_context=tool_result_context_stats,
                    task_budget=params.task_budget,
                    task_budget_remaining=_effective_task_budget_remaining(
                        total_budget=params.task_budget,
                        current_remaining=task_budget_remaining,
                        messages=messages,
                    ),
                    turn_count=next_turn_count,
                    transition=last_transition,
                    reactive_compact_context=last_reactive_compact_context,
                    tool_use_summary=last_tool_use_summary,
                    model_usage=model_usage_summary,
                )
                return
            turn_count = next_turn_count
            _log_loop_phase(
                params.session_id,
                "continue_after_tools",
                next_turn=turn_count,
                completed_tool_count=len(completed_tools),
            )
            continue

        if response.output_text:
            yield RuntimeEvent(type="assistant", payload={"text": response.output_text})
            messages.append(ModelMessage(role="assistant", content=response.output_text))

        if _should_continue_after_tool_progress_text(
            text=response.output_text,
            completed_tools=completed_tools,
            recovery_count=post_tool_progress_recovery_count,
        ):
            messages.append(_build_post_tool_progress_recovery_message())
            post_tool_progress_recovery_count += 1
            last_transition = {
                "reason": "post_tool_progress_recovery",
                "attempt": post_tool_progress_recovery_count,
            }
            yield RuntimeEvent(type="transition", payload=last_transition)
            turn_count += 1
            continue

        # 纯文本分支：允许在 length/max_output_tokens 时追加恢复提示，避免无结论中断。
        response_was_truncated = response.stop_reason in {"length", "max_output_tokens"}
        recovery_attempts_available = max_output_tokens_recovery_count < params.max_output_tokens_recovery_limit
        should_recover_complete_output = response_was_truncated and recovery_attempts_available
        if should_recover_complete_output:
            # 非流式模型一次性返回完整 response；如果 stop_reason 表示被截断，
            # 这里插入恢复提示并 continue，让下一轮模型继续生成。
            hook_manager.after_model_response(
                output_text=response.output_text,
                stop_reason=response.stop_reason,
                session_id=params.session_id,
            )
            next_turn_count = turn_count + 1
            if params.max_turns is not None and next_turn_count > params.max_turns:
                yield RuntimeEvent(
                    type="assistant",
                    payload={"text": _runtime_limit_message("max_turns", turn_count=next_turn_count)},
                )
                yield _done_event(
                    hook_manager=hook_manager,
                    session_id=params.session_id,
                    stop_reason="max_turns",
                    tool_result_budget_state=tool_result_budget_state,
                    tool_result_context=tool_result_context_stats,
                    task_budget=params.task_budget,
                    task_budget_remaining=_effective_task_budget_remaining(
                        total_budget=params.task_budget,
                        current_remaining=task_budget_remaining,
                        messages=messages,
                    ),
                    turn_count=next_turn_count,
                    transition=last_transition,
                    reactive_compact_context=last_reactive_compact_context,
                    tool_use_summary=last_tool_use_summary,
                    model_usage=model_usage_summary,
                )
                return
            recovery_message = _build_max_output_tokens_recovery_message()
            messages.append(recovery_message)
            max_output_tokens_recovery_count += 1
            last_transition = {
                "reason": "max_output_tokens_recovery",
                "attempt": max_output_tokens_recovery_count,
            }
            yield RuntimeEvent(
                type="transition",
                payload=last_transition,
            )
            turn_count = next_turn_count
            continue

        # 非流式纯文本的正常结束点：
        # 1. 没有 tool_calls；
        # 2. 不需要 max_output_tokens 恢复；
        # 3. 最终只 drain 普通 send_message continuation；
        # 4. yield done 后 return，本轮 queryLoop 结束。
        hook_manager.after_model_response(
            output_text=response.output_text,
            stop_reason=resolved_stop_reason,
            session_id=params.session_id,
        )

        yield from _drain_commands(
            queue_manager=params.queue_manager,
            session_id=params.session_id,
            max_priority="later",
            messages=messages,
            target_agent_id=params.active_agent_id,
            allowed_kinds={"send_message"},
        )
        yield _done_event(
            hook_manager=hook_manager,
            session_id=params.session_id,
            stop_reason=response.stop_reason or "end_turn",
            tool_result_budget_state=tool_result_budget_state,
            tool_result_context=tool_result_context_stats,
            task_budget=params.task_budget,
            task_budget_remaining=_effective_task_budget_remaining(
                total_budget=params.task_budget,
                current_remaining=task_budget_remaining,
                messages=messages,
            ),
            transition=last_transition,
            reactive_compact_context=last_reactive_compact_context,
            tool_use_summary=last_tool_use_summary,
            model_usage=model_usage_summary,
        )
        return


def _execute_tool_calls(
    *,
    messages: list[ModelMessage],
    session_id: str,
    tool_calls: list[ToolCall],
    assistant_text: str,
    assistant_reasoning_content: str,
    hook_manager: HookManager,
    tool_executor: ToolExecutor,
    executed_tool_keys: set[str],
    completed_tools: list[dict[str, object]],
    max_tool_calls_per_turn: int,
    tool_call_count: int,
    task_budget: int | None,
    task_budget_remaining: int | None,
    working_directory: str | None,
    active_task_id: str | None,
    active_agent_id: str | None,
    metadata: dict | None,
    reactive_compact_context: dict | None,
) -> Iterator[RuntimeEvent]:
    """执行模型在当前采样里请求的所有工具调用。

    每个 tool_call 都会先发 tool_use 事件，再执行工具，再发 tool_result 事件，
    最后把 tool_result message 追加回 messages，供下一轮模型采样读取。
    如果工具结果要求等待用户、进入计划模式或审批工具权限，这里会直接产出 done。
    """
    for tool_index, tool_call in enumerate(tool_calls):
        # 工具预算保护：一旦这一轮可用 tool call 用尽，就直接结束，避免无限 agentic 循环。
        if tool_call_count >= max_tool_calls_per_turn:
            yield RuntimeEvent(
                type="assistant",
                payload={
                    "text": _runtime_limit_message(
                        "max_tool_calls",
                        tool_summary=_summarize_completed_tools(completed_tools),
                    )
                },
            )
            yield _done_event(
                hook_manager=hook_manager,
                session_id=session_id,
                stop_reason="max_tool_calls",
                task_budget=task_budget,
                task_budget_remaining=_effective_task_budget_remaining(
                    total_budget=task_budget,
                    current_remaining=task_budget_remaining,
                    messages=messages,
                ),
                transition=None,
                reactive_compact_context=reactive_compact_context,
            )
            return
        tool_call = _with_validated_query_dataset_handle(tool_call, completed_tools)
        tool_key = _tool_call_key(tool_call)
        if tool_key in executed_tool_keys:
            duplicate_result = {
                "ok": False,
                "kind": "duplicate_tool_call",
                "is_error": True,
                "summary": (
                    "This exact tool call was already executed in the current turn. "
                    "Use the previous tool result already in context and continue to the final answer; "
                    "do not call the same tool with the same arguments again."
                ),
            }
            messages.append(_build_tool_result_message(tool_call=tool_call, result=duplicate_result))
            _log_loop_phase(
                session_id,
                "tool_call_skipped",
                tool_name=tool_call.tool_name,
                tool_call_id=tool_call.call_id,
                reason="duplicate_tool_call",
            )
            yield RuntimeEvent(
                type="tool_skipped",
                payload={
                    "tool_name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                    "tool_call_id": tool_call.call_id,
                    "reason": "duplicate_tool_call",
                },
            )
            continue
        if _should_force_tool_budget_summary(
            tool_call_count=tool_call_count,
            max_tool_calls_per_turn=max_tool_calls_per_turn,
            remaining_tool_calls=len(tool_calls) - tool_index,
        ):
            tool_summary = _summarize_completed_tools(completed_tools)
            messages.append(
                _build_tool_budget_summary_message(
                    tool_summary=tool_summary,
                    remaining_tool_calls=len(tool_calls) - tool_index,
                    max_tool_calls_per_turn=max_tool_calls_per_turn,
                )
            )
            yield RuntimeEvent(
                type="transition",
                payload={
                    "reason": "tool_budget_summary",
                    "tool_call_count": tool_call_count,
                    "max_tool_calls": max_tool_calls_per_turn,
                    "remaining_tool_calls": len(tool_calls) - tool_index,
                },
            )
            return

        # 真正执行工具前，把可见的 tool_use 事件和 assistant 轨迹一起发给前端与持久化层。
        executed_tool_keys.add(tool_key)
        _log_loop_phase(
            session_id,
            "tool_call_start",
            tool_name=tool_call.tool_name,
            tool_call_id=tool_call.call_id,
            tool_index=tool_index + 1,
            tool_count=len(tool_calls),
        )
        hook_manager.before_tool_use(
            tool_name=tool_call.tool_name,
            arguments=tool_call.arguments,
            session_id=session_id,
        )
        yield RuntimeEvent(
            type="tool_use",
            payload={
                "tool_name": tool_call.tool_name,
                "arguments": tool_call.arguments,
                "tool_call_id": tool_call.call_id,
                **(
                    {
                        "assistant_text": assistant_text,
                        **(
                            {"assistant_reasoning_content": assistant_reasoning_content}
                            if assistant_reasoning_content
                            else {}
                        ),
                        "assistant_tool_calls": [
                            {
                                "id": current_tool_call.call_id,
                                "name": current_tool_call.tool_name,
                                "arguments": current_tool_call.arguments,
                            }
                            for current_tool_call in tool_calls
                        ],
                    }
                    if tool_index == 0
                    else {}
                ),
            },
        )
        active_tool_policy = _current_turn_runtime_tool_policy(completed_tools)
        if _is_disallowed_by_runtime_tool_policy(
            tool_name=tool_call.tool_name,
            policy=active_tool_policy,
        ):
            result = _runtime_tool_policy_error(tool_name=tool_call.tool_name, policy=active_tool_policy)
        else:
            result = tool_executor.execute(
                tool_call,
                context=ToolExecutionContext(
                    session_id=session_id,
                    working_directory=working_directory,
                    task_id=active_task_id,
                    agent_id=active_agent_id,
                    completed_tools=completed_tools,
                    metadata=metadata,
                ),
            )
        _log_loop_phase(
            session_id,
            "tool_call_done",
            tool_name=tool_call.tool_name,
            tool_call_id=tool_call.call_id,
            result_kind=result.get("kind"),
            result_error=result.get("error"),
        )
        completed_tools.append(
            {
                "tool_name": tool_call.tool_name,
                "arguments": tool_call.arguments,
                "result": result,
            }
        )
        hook_manager.after_tool_use(
            tool_name=tool_call.tool_name,
            result=result,
            session_id=session_id,
        )
        yield RuntimeEvent(
            type="tool_result",
            payload={
                "tool_name": tool_call.tool_name,
                "result": result,
                "tool_call_id": tool_call.call_id,
            },
        )
        messages.append(_build_tool_result_message(tool_call=tool_call, result=result))
        # 某些工具结果会直接把主线程切进等待/审批/计划确认状态，需要立即终止当前 loop。
        if result.get("kind") == "user_question":
            yield _done_event(
                hook_manager=hook_manager,
                session_id=session_id,
                stop_reason="awaiting_user_question",
                pending_user_question=result.get("question"),
                reactive_compact_context=reactive_compact_context,
            )
            return
        if result.get("kind") == "plan_mode_enter":
            yield _done_event(
                hook_manager=hook_manager,
                session_id=session_id,
                stop_reason="entered_plan_mode",
                pending_user_question=None,
                reactive_compact_context=reactive_compact_context,
            )
            return
        if result.get("kind") == "plan_mode_exit":
            yield _done_event(
                hook_manager=hook_manager,
                session_id=session_id,
                stop_reason="awaiting_plan_approval",
                pending_user_question=result.get("question", "Exit plan mode?"),
                pending_plan=result.get("plan"),
                allowed_prompts=result.get("allowed_prompts", []),
                reactive_compact_context=reactive_compact_context,
            )
            return
        if result.get("error") == "tool_approval_required":
            approval_message = (
                result.get("message")
                if isinstance(result.get("message"), str)
                else f"Tool '{tool_call.tool_name}' requires approval before execution."
            )
            yield RuntimeEvent(
                type="tool_approval_required",
                payload={
                    "tool_name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                    "tool_call_id": tool_call.call_id,
                    "message": approval_message,
                    **(
                        {"permission_reason": result["permission_reason"]}
                        if isinstance(result.get("permission_reason"), str)
                        else {}
                    ),
                },
            )
            yield _done_event(
                hook_manager=hook_manager,
                session_id=session_id,
                stop_reason="awaiting_tool_approval",
                task_budget=task_budget,
                task_budget_remaining=_effective_task_budget_remaining(
                    total_budget=task_budget,
                    current_remaining=task_budget_remaining,
                    messages=messages,
                ),
                pending_tool_name=tool_call.tool_name,
                transition=None,
                reactive_compact_context=reactive_compact_context,
            )
            return
        tool_call_count += 1


def _current_turn_runtime_tool_policy(completed_tools: list[dict[str, object]]) -> dict | None:
    for item in reversed(completed_tools):
        result = item.get("result")
        if not isinstance(result, dict) or result.get("ok") is not True:
            continue
        policy = result.get("runtime_tool_policy")
        if isinstance(policy, dict):
            return policy
    return None


def _is_disallowed_by_runtime_tool_policy(*, tool_name: str, policy: dict | None) -> bool:
    if not policy:
        return False
    allowed_tool_names = policy.get("allowed_tool_names")
    if not isinstance(allowed_tool_names, list):
        return False
    allowed = {str(name) for name in allowed_tool_names if str(name).strip()}
    return bool(allowed) and tool_name not in allowed


def _runtime_tool_policy_error(*, tool_name: str, policy: dict | None) -> dict:
    # 工具可通过结果声明当前 turn 的后续工具边界；query loop 只执行通用策略，不关心具体业务域。
    message = (
        str(policy.get("message")).strip()
        if isinstance(policy, dict) and policy.get("message")
        else "当前上下文限制了后续可调用工具。"
    )
    return {
        "ok": False,
        "is_error": True,
        "error_type": "tool_not_allowed_by_runtime_policy",
        "tool_name": tool_name,
        "message": message,
        "allowed_tool_names": policy.get("allowed_tool_names") if isinstance(policy, dict) else [],
        "repair_hints": policy.get("repair_hints") if isinstance(policy, dict) else [],
    }


def _with_validated_query_dataset_handle(
    tool_call: ToolCall,
    completed_tools: list[dict[str, object]],
) -> ToolCall:
    if tool_call.tool_name != "query_dataset":
        return tool_call
    arguments = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
    if isinstance(arguments.get("query_handle"), dict):
        return tool_call
    sql = str(arguments.get("sql") or "").strip()
    if not sql:
        return tool_call
    query_handle = _latest_matching_validated_query_handle(sql, completed_tools)
    if query_handle is None:
        return tool_call
    # query_dataset 本身仍不信任模型传入的 allowed_table_names；这里仅复用本轮
    # validate_enterprise_sql 产出的可信句柄，解决模型漏传 handle 导致的误拒绝。
    return ToolCall(
        call_id=tool_call.call_id,
        tool_name=tool_call.tool_name,
        arguments={**arguments, "query_handle": query_handle},
    )


def _latest_matching_validated_query_handle(
    sql: str,
    completed_tools: list[dict[str, object]],
) -> dict | None:
    normalized_sql = _normalize_sql_for_compare(sql)
    for item in reversed(completed_tools):
        if item.get("tool_name") != "validate_enterprise_sql":
            continue
        result = item.get("result")
        if not isinstance(result, dict) or result.get("ok") is not True:
            continue
        validated_sql = result.get("validated_sql")
        if not isinstance(validated_sql, str):
            continue
        if _normalize_sql_for_compare(validated_sql) != normalized_sql:
            continue
        query_handle = result.get("query_handle")
        if isinstance(query_handle, dict):
            return dict(query_handle)
    return None


def _normalize_sql_for_compare(sql: str) -> str:
    return " ".join(str(sql or "").strip().rstrip(";").split()).lower()


def _tool_call_key(tool_call: ToolCall) -> str:
    serialized_arguments = json.dumps(
        tool_call.arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{tool_call.tool_name}:{serialized_arguments}"


def _build_messages_with_prompt(
    history: list[dict],
    query: object,
    system_prompt: str | None,
    system_prompt_blocks: list[str] | None = None,
    system_reminders: list[str] | None = None,
    user_context: dict[str, str] | None = None,
) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    if system_prompt_blocks:
        messages.append(
            ModelMessage(
                role="system",
                content=build_system_prompt_blocks(system_prompt_blocks),
            )
        )
    elif system_prompt:
        messages.append(ModelMessage(role="system", content=system_prompt))
    for reminder in system_reminders or []:
        messages.append(ModelMessage(role="user", content=reminder))
    messages.extend(rehydrate_history_for_model(history))
    messages.append(ModelMessage(role="user", content=query))
    return prepend_user_context(messages, user_context or {})


def _model_invocation_kwargs(
    *,
    method,
    messages: list[ModelMessage],
    tools: list[dict],
    metadata: dict | None,
    task_budget: int | None,
    task_budget_remaining: int | None,
) -> dict:
    kwargs = {
        "messages": messages,
        "tools": tools,
    }
    if metadata or task_budget is not None:
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if metadata and "metadata" in parameters:
            kwargs["metadata"] = metadata
        if task_budget is not None and "task_budget" in parameters:
            kwargs["task_budget"] = {
                "total": task_budget,
                **(
                    {"remaining": task_budget_remaining}
                    if task_budget_remaining is not None
                    else {}
                ),
            }
    return kwargs


def _next_task_budget_remaining(
    *,
    total_budget: int | None,
    current_remaining: int | None,
    messages: list[ModelMessage],
) -> int | None:
    if total_budget is None:
        return None
    baseline = total_budget if current_remaining is None else current_remaining
    estimated_spend = _estimate_message_token_cost(messages)
    return max(0, baseline - estimated_spend)


def _estimate_message_token_cost(messages: list[ModelMessage]) -> int:
    total_chars = 0
    for message in messages:
        content = message.content
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
        elif content is not None:
            total_chars += len(str(content))
        for tool_call in message.tool_calls or []:
            total_chars += len(tool_call.tool_name)
            total_chars += len(json.dumps(tool_call.arguments, ensure_ascii=False))
    return max(1, total_chars // 4) if total_chars else 0


def _build_task_budget_summary_message(
    *,
    messages: list[ModelMessage],
    total_budget: int | None,
    current_remaining: int | None,
) -> ModelMessage | None:
    effective_remaining = _effective_task_budget_remaining(
        total_budget=total_budget,
        current_remaining=current_remaining,
        messages=messages,
    )
    if not _should_force_task_budget_summary(
        total_budget=total_budget,
        remaining_budget=effective_remaining,
    ):
        return None
    used_budget = (total_budget or 0) - effective_remaining
    return ModelMessage(
        role="user",
        content=(
            "The task budget is nearly exhausted. "
            f"You have used approximately {used_budget} of {total_budget} budgeted tokens. "
            "Do not call more tools unless absolutely necessary. "
            "Summarize the current findings concisely, note any important gaps, and finish."
        ),
    )


def _effective_task_budget_remaining(
    *,
    total_budget: int | None,
    current_remaining: int | None,
    messages: list[ModelMessage],
) -> int:
    if total_budget is None:
        return 0
    if current_remaining is not None:
        return current_remaining
    return max(0, total_budget - _estimate_message_token_cost(messages))


def _should_force_task_budget_summary(
    *,
    total_budget: int | None,
    remaining_budget: int,
) -> bool:
    if total_budget is None or total_budget <= 0:
        return False
    return remaining_budget <= max(total_budget // 10, 64)


def _task_budget_transition_payload(
    *,
    reason: str,
    mode: str,
    total_budget: int | None,
    current_remaining: int | None,
    task_budget_source: str | None,
    task_budget_hint_positions: list[tuple[int, int]] | list[list[int]] | None,
    task_budget_recovered: bool,
    messages: list[ModelMessage],
    continuation_count: int,
    duration_ms: int,
) -> dict:
    effective_remaining = _effective_task_budget_remaining(
        total_budget=total_budget,
        current_remaining=current_remaining,
        messages=messages,
    )
    safe_total = total_budget or 0
    used_budget = max(0, safe_total - effective_remaining)
    pct_used = 0 if safe_total <= 0 else round((used_budget / safe_total) * 100)
    diminishing_returns = continuation_count > 0
    return {
        "reason": reason,
        "mode": mode,
        "completion_reason": (
            "diminishing_returns" if diminishing_returns else "threshold"
        ),
        "total_budget": total_budget,
        "task_budget_source": task_budget_source,
        "task_budget_hint_positions": _normalize_hint_positions(
            task_budget_hint_positions
        ),
        "task_budget_recovered": task_budget_recovered,
        "remaining_budget": effective_remaining,
        "used_budget": used_budget,
        "pct_used": pct_used,
        "continuation_count": continuation_count,
        "diminishing_returns": diminishing_returns,
        "duration_ms": duration_ms,
    }


def _budget_completion_from_transition(transition: object) -> dict | None:
    if not isinstance(transition, dict):
        return None
    if transition.get("reason") != "task_budget_summary":
        return None
    keys = (
        "mode",
        "completion_reason",
        "total_budget",
        "task_budget_source",
        "task_budget_hint_positions",
        "task_budget_recovered",
        "remaining_budget",
        "used_budget",
        "pct_used",
        "continuation_count",
        "diminishing_returns",
        "duration_ms",
    )
    return {
        key: transition.get(key)
        for key in keys
        if key in transition
    }


def _normalize_hint_positions(
    positions: list[tuple[int, int]] | list[list[int]] | None,
) -> list[list[int]]:
    normalized: list[list[int]] = []
    for position in positions or []:
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            continue
        try:
            start = int(position[0])
            end = int(position[1])
        except (TypeError, ValueError):
            continue
        normalized.append([start, end])
    return normalized


def _model_visible_tool_definitions(
    tools: list[dict],
    model_visible_tool_names: set[str] | None,
) -> list[dict]:
    return build_tool_pool_context(
        model_visible_tool_names=model_visible_tool_names,
    ).visible_definitions(tools)


def rehydrate_history_for_model(history: list[dict]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for message in history:
        if isinstance(message, dict):
            messages.extend(_rehydrate_history_messages(message))
    return messages


def _rehydrate_history_messages(message: dict) -> list[ModelMessage]:
    skill_messages = _rehydrate_skill_definition_history_message(message)
    if skill_messages is not None:
        return skill_messages
    rehydrated = _rehydrate_history_message(message)
    return [rehydrated] if rehydrated is not None else []


def _rehydrate_skill_definition_history_message(message: dict) -> list[ModelMessage] | None:
    if message.get("role") != "tool":
        return None
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        return None
    result = payload.get("result", payload.get("content", ""))
    if not isinstance(result, dict) or result.get("kind") != "skill_definition":
        return None
    content = result.get("content")
    if not content:
        return None
    skill_name = _normalize_optional_string(result.get("skill_name")) or _normalize_optional_string(
        payload.get("tool_name")
    ) or "skill"
    tool_name = _normalize_optional_string(payload.get("tool_name")) or "Skill"
    tool_call_id = _normalize_optional_string(payload.get("tool_call_id"))
    return [
        ModelMessage(
            role="tool",
            content=f"Launching skill: {skill_name}",
            tool_call_id=tool_call_id,
            name=tool_name,
        ),
        ModelMessage(role="user", content=wrap_in_system_reminder(_stringify_skill_content(content))),
    ]


def _rehydrate_history_message(message: dict) -> ModelMessage | None:
    role = _normalize_optional_string(message.get("role"))
    if role is None:
        return None
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    if role == "assistant":
        tool_calls_payload = payload.get("tool_calls", [])
        tool_calls = [
            ToolCall(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            for tool_call in tool_calls_payload
            if isinstance(tool_call, dict)
            if (call_id := _normalize_optional_string(tool_call.get("id"))) is not None
            and (tool_name := _normalize_optional_string(tool_call.get("name"))) is not None
            and isinstance((arguments := tool_call.get("arguments", {})), dict)
        ]
        return ModelMessage(
            role="assistant",
            content=_payload_text_content(payload),
            tool_calls=tool_calls or None,
            reasoning_content=_normalize_optional_string(payload.get("reasoning_content")),
        )
    if role == "tool":
        result = payload.get("result", payload.get("content", ""))
        tool_name = _normalize_optional_string(payload.get("tool_name"))
        if isinstance(result, dict):
            result = _sanitize_tool_result_for_model(
                tool_name=tool_name or "tool",
                result=result,
                tool_call_id=_normalize_optional_string(payload.get("tool_call_id")),
            )
        if not isinstance(result, str):
            result = safe_json_dumps(result, ensure_ascii=False)
        return ModelMessage(
            role="tool",
            content=result,
            tool_call_id=_normalize_optional_string(payload.get("tool_call_id")),
            name=tool_name,
        )
    return ModelMessage(
        role=role,
        content=_payload_text_content(payload),
    )


def _payload_text_content(payload: dict):
    if "text" in payload:
        text = payload.get("text", "")
        return "" if text is None else str(text)
    content = payload.get("content", "")
    if isinstance(content, list):
        return content
    return "" if content is None else str(content)


def _normalize_optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _build_max_output_tokens_recovery_message() -> ModelMessage:
    return ModelMessage(
        role="user",
        content=(
            "Output token limit hit. Resume directly - no apology, no recap of what you were doing. "
            "Pick up mid-thought if needed. Break remaining work into smaller pieces."
        ),
    )


def _build_skill_injection_message(result: dict) -> ModelMessage | None:
    if result.get("kind") != "skill_definition" or not result.get("content"):
        return None
    content = result["content"]
    if isinstance(content, (dict, list)):
        content = safe_json_dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        content = "" if content is None else str(content)
    return ModelMessage(role="user", content=wrap_in_system_reminder(content))


def _build_tool_result_message(*, tool_call: ToolCall, result: dict) -> ModelMessage:
    if result.get("kind") == "skill_definition" and result.get("content"):
        return ModelMessage(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.call_id,
                    "content": f"Launching skill: {result.get('skill_name', tool_call.tool_name)}",
                    "tool_name": tool_call.tool_name,
                },
                {
                    "type": "text",
                    "text": wrap_in_system_reminder(
                        _stringify_skill_content(result.get("content"))
                    ),
                },
            ],
        )
    if result.get("kind") == "skill_fork":
        skill_name = result.get("skill_name", tool_call.tool_name)
        task_id = result.get("task_id", "")
        agent_id = result.get("agent_id", "")
        return ModelMessage(
            role="user",
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.call_id,
                    "content": f"Launching forked skill: {skill_name}",
                    "tool_name": tool_call.tool_name,
                },
                {
                    "type": "text",
                    "text": (
                        "Background task started.\n"
                        f"task_id: {task_id}\n"
                        f"agent_id: {agent_id}\n"
                        "Results will return through task notifications."
                    ),
                },
            ],
        )
    model_visible_result = _sanitize_tool_result_for_model(
        tool_name=tool_call.tool_name,
        result=result,
        tool_call_id=tool_call.call_id,
    )
    return ModelMessage(
        role="user",
        content=[
            {
                "type": "tool_result",
                "tool_use_id": tool_call.call_id,
                "content": model_visible_result,
                "tool_name": tool_call.tool_name,
            }
            | ({"is_error": True} if model_visible_result.get("is_error") is True else {})
        ],
    )


def _should_continue_after_tool_progress_text(
    *,
    text: str,
    completed_tools: list[dict[str, object]],
    recovery_count: int,
) -> bool:
    if recovery_count > 0 or not completed_tools:
        return False
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    progress_prefixes = (
        "正在",
        "我将",
        "我会",
        "让我",
        "现在",
        "接下来",
        "首先",
        "需要",
        "准备",
        "开始",
    )
    if not normalized.startswith(progress_prefixes):
        return False
    completion_markers = (
        "结论",
        "如下",
        "结果",
        "分析",
        "发现",
        "建议",
        "风险",
        "证据",
        "明细",
        "汇总",
    )
    # 只拦截典型“进度播报后就 stop”的短文本。正常的短结论仍然允许结束。
    if any(marker in normalized for marker in completion_markers) and not normalized.endswith(("...", "…")):
        return False
    return normalized.endswith(("...", "…", "：", ":")) or len(normalized) <= 32


def _build_post_tool_progress_recovery_message() -> ModelMessage:
    return ModelMessage(
        role="user",
        content=(
            "上一条工具结果已经返回。不要只输出进度说明，也不要重复描述将要调用工具。"
            "请直接基于最近一次工具结果给出面向用户的最终结论；如果仍需更多数据，请发起真实工具调用。"
        ),
    )


def _stringify_skill_content(content: object) -> str:
    if isinstance(content, (dict, list)):
        return safe_json_dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return "" if content is None else str(content)


def _sanitize_tool_result_for_model(
    *,
    tool_name: str,
    result: dict,
    tool_call_id: str | None = None,
) -> dict:
    if tool_name == "read_tool_result":
        return result
    if tool_name != "export_report_document":
        return collapse_tool_result_for_model(
            tool_name=tool_name,
            result=result,
            tool_call_id=tool_call_id,
        )
    sanitized = {
        "ok": bool(result.get("ok")),
        "tool_type": result.get("tool_type"),
        "artifact_type": result.get("artifact_type"),
        "report_title": result.get("report_title"),
        "message": result.get("message"),
    }
    files = result.get("files")
    sanitized["file_count"] = len(files) if isinstance(files, list) else 0
    if result.get("is_error") is True:
        sanitized["is_error"] = True
        sanitized["error"] = result.get("error")
    return collapse_tool_result_for_model(
        tool_name=tool_name,
        result=sanitized,
        tool_call_id=tool_call_id,
    )


def _reactive_compact_messages(
    messages: list[ModelMessage],
    compaction_engine: CompactionEngine | None,
) -> tuple[list[ModelMessage], str | None, dict | None]:
    if compaction_engine is None:
        return messages, None, None

    prefix: list[ModelMessage] = []
    compactable: list[ModelMessage] = []
    in_prefix = True
    for message in messages:
        if in_prefix and _is_non_compactable_prefix_message(message):
            prefix.append(message)
            continue
        in_prefix = False
        compactable.append(message)

    while prefix and _is_compaction_summary_message(prefix[-1]):
        prefix.pop()

    compacted = compaction_engine.compact(
        [_model_message_to_history(message) for message in compactable]
    )
    if compacted.summary is None:
        return messages, None, None

    rebuilt_messages = [
        *prefix,
        ModelMessage(
            role="user",
            content=render_compacted_history_reminder(compacted.summary),
        ),
        *(
            rehydrated
            for message in compacted.retained_messages
            for rehydrated in _rehydrate_history_messages(message)
        ),
    ]
    return (
        rebuilt_messages,
        compacted.summary,
        _build_reactive_compact_context(
            summary=compacted.summary,
            compactable_messages=compactable,
            retained_messages=compacted.retained_messages,
        ),
    )


def _build_reactive_compact_context(
    *,
    summary: str,
    compactable_messages: list[ModelMessage],
    retained_messages: list[dict],
) -> dict:
    compacted_message_count = max(0, len(compactable_messages) - len(retained_messages))
    preserved_tail = [
        entry
        for message in retained_messages
        if (entry := _render_reactive_compact_tail_entry(message)) is not None
    ]
    return {
        "summary_text": summary,
        "compacted_message_count": compacted_message_count,
        "retained_message_count": len(retained_messages),
        "preserved_tail": preserved_tail,
    }


def _render_reactive_compact_tail_entry(message: dict) -> dict | None:
    role = _normalize_optional_string(message.get("role"))
    if role is None:
        return None
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    if role == "tool":
        tool_name = _normalize_optional_string(payload.get("tool_name")) or "tool"
        result = payload.get("result", payload.get("content", ""))
        if isinstance(result, dict):
            if isinstance(result.get("file_path"), str) and isinstance(result.get("content"), str):
                text = f"tool[{tool_name}] path={result['file_path']} content_length={len(result['content'])}"
            elif isinstance(result.get("pattern"), str) and isinstance(result.get("matches"), list):
                first_match = next((match for match in result["matches"] if isinstance(match, dict)), {})
                first_file = first_match.get("file_path") if isinstance(first_match, dict) else None
                text = (
                    f"tool[{tool_name}] pattern={result['pattern']} "
                    f"match_count={len(result['matches'])}"
                    + (f" first_file={first_file}" if isinstance(first_file, str) else "")
                )
            else:
                text = safe_json_dumps(result, ensure_ascii=False, sort_keys=True)
        elif isinstance(result, list):
            text = safe_json_dumps(result, ensure_ascii=False, sort_keys=True)
        else:
            text = "" if result is None else str(result)
        return {"role": role, "text": text}
    if role == "assistant" and isinstance(payload.get("tool_calls"), list):
        tool_names = [
            tool_name
            for tool_call in payload["tool_calls"]
            if isinstance(tool_call, dict)
            if (tool_name := _normalize_optional_string(tool_call.get("name"))) is not None
        ]
        if tool_names:
            return {
                "role": role,
                # 这里会进入 reactive compaction 的保留尾部，必须避免继续向模型暴露
                # transcript 风格的 tool_calls 标记，否则模型容易把它当成面向用户的输出模板。
                "text": f"used tools: {', '.join(tool_names)}",
            }
    text = _payload_text_content(payload)
    if isinstance(text, list):
        text = safe_json_dumps(text, ensure_ascii=False)
    return {
        "role": role,
        "text": "" if text is None else str(text),
    }


def _build_tool_budget_summary_message(
    *,
    tool_summary: str | None,
    remaining_tool_calls: int,
    max_tool_calls_per_turn: int,
) -> ModelMessage:
    parts = [
        (
            "Tool call budget is nearly exhausted. "
            "Do not call more tools unless the current answer would be misleading without them. "
            "Use the completed tool work and give the best possible summary now."
        ),
        (
            f"Completed {max_tool_calls_per_turn - remaining_tool_calls} tool calls so far; "
            f"skipped the remaining {remaining_tool_calls} planned tool call(s) in this batch."
        ),
    ]
    if tool_summary:
        parts.append(tool_summary)
    return ModelMessage(role="user", content="\n\n".join(parts))


def _should_force_tool_budget_summary(
    *,
    tool_call_count: int,
    max_tool_calls_per_turn: int,
    remaining_tool_calls: int,
) -> bool:
    if remaining_tool_calls <= 0:
        return False
    if tool_call_count <= 0:
        return False
    return tool_call_count >= max_tool_calls_per_turn - 1


def _is_non_compactable_prefix_message(message: ModelMessage) -> bool:
    if message.role == "system":
        return True
    return (
        message.role == "user"
        and isinstance(message.content, str)
        and "<system-reminder>" in message.content
    )


def _is_compaction_summary_message(message: ModelMessage) -> bool:
    return (
        message.role in {"system", "user"}
        and isinstance(message.content, str)
        and is_compaction_summary_text(message.content)
    )


def _model_message_to_history(message: ModelMessage) -> dict:
    if message.role == "assistant":
        assistant_content = (
            message.content
            if isinstance(message.content, list)
            else {"text": "" if message.content is None else str(message.content)}
        )
        payload: dict = (
            {"content": assistant_content}
            if isinstance(assistant_content, list)
            else assistant_content
        )
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tool_call.call_id,
                    "name": tool_call.tool_name,
                    "arguments": tool_call.arguments,
                }
                for tool_call in message.tool_calls
            ]
        if message.reasoning_content:
            payload["reasoning_content"] = message.reasoning_content
        return {"role": "assistant", "payload": payload}

    if message.role == "tool":
        return {
            "role": "tool",
            "payload": {
                "tool_name": message.name,
                "tool_call_id": message.tool_call_id,
                "result": message.content,
            },
        }

    if message.role == "user" and isinstance(message.content, list):
        tool_result_block = next(
            (
                block
                for block in message.content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ),
            None,
        )
        if tool_result_block is not None:
            return {
                "role": "tool",
                "payload": {
                    "tool_name": tool_result_block.get("tool_name"),
                    "tool_call_id": tool_result_block.get("tool_use_id"),
                    "result": tool_result_block.get("content", ""),
                },
            }

    payload_content = (
        message.content
        if isinstance(message.content, list)
        else {"text": "" if message.content is None else str(message.content)}
    )
    payload = (
        {"content": payload_content}
        if isinstance(payload_content, list)
        else payload_content
    )
    return {"role": message.role, "payload": payload}


def _drain_commands(
    *,
    queue_manager: QueueManager | None,
    session_id: str,
    max_priority: str,
    messages: list[ModelMessage],
    target_agent_id: str | None = None,
    allowed_kinds: set[str] | None = None,
) -> Iterator[RuntimeEvent]:
    if queue_manager is None:
        return
    commands = queue_manager.list_ready(
        session_id=session_id,
        max_priority=max_priority,
        target_agent_id=target_agent_id,
    )
    for command in commands:
        if allowed_kinds is not None and command.kind not in allowed_kinds:
            continue
        payload = command.payload
        if command.kind == "task_notification":
            yield RuntimeEvent(type="notification", payload=payload)
            messages.append(
                ModelMessage(
                    role="user",
                    content=notification_to_xml_payload(payload),
                )
            )
        elif command.kind == "send_message":
            yield RuntimeEvent(type="notification", payload=payload)
            messages.append(
                ModelMessage(
                    role="user",
                    content=render_notification_transcript_text(payload),
                )
            )
        queue_manager.mark_consumed(command.id)


def _apply_reactive_compaction_phase(
    *,
    messages: list[ModelMessage],
    compaction_engine: CompactionEngine | None,
    task_budget: int | None,
    task_budget_remaining: int | None,
    last_reactive_compact_context: dict | None,
) -> Iterator[RuntimeEvent]:
    pre_compact_messages = list(messages)
    messages, compaction_summary, reactive_compact_context = _reactive_compact_messages(
        messages,
        compaction_engine,
    )
    if compaction_summary is not None:
        last_reactive_compact_context = reactive_compact_context
        task_budget_remaining = _next_task_budget_remaining(
            total_budget=task_budget,
            current_remaining=task_budget_remaining,
            messages=pre_compact_messages,
        )
        yield RuntimeEvent(
            type="compaction",
            payload={
                "summary": compaction_summary,
                "reactive_compact_context": reactive_compact_context,
            },
        )
    return messages, task_budget_remaining, last_reactive_compact_context


def _apply_tool_result_context_management(
    *,
    messages: list[ModelMessage],
    tool_result_budget_state: ToolResultBudgetState,
    tool_result_context_stats: dict[str, dict],
    tool_result_keep_recent: int | None,
    tool_result_max_model_chars: int,
    tool_result_preview_chars: int,
) -> list[ModelMessage]:
    # 对齐 Claude Code 的 per-message tool result budget：provider 侧会合并连续
    # user/tool_result 消息，因此采样或 compact 前都按合并后的消息组做一次稳定裁剪。
    budgeted = apply_tool_result_message_budget_with_stats(
        messages,
        state=tool_result_budget_state,
        max_chars=tool_result_max_model_chars,
        preview_chars=tool_result_preview_chars,
    )
    managed_messages = budgeted.messages
    _merge_tool_result_context_stats(
        tool_result_context_stats,
        section="budget",
        stats=budgeted.stats,
    )
    if tool_result_keep_recent is None:
        return managed_messages

    # 可选 microcompact：只清理模型上下文中较旧的工具结果，保留最近 N 个
    # 作为当前工作上下文；完整结果仍在持久化消息和前端事件里。
    cleared = clear_old_tool_results_with_stats(
        managed_messages,
        keep_recent=tool_result_keep_recent,
    )
    _merge_tool_result_context_stats(
        tool_result_context_stats,
        section="clearing",
        stats=cleared.stats,
    )
    return cleared.messages


def _build_stop_requested_done_event(
    *,
    task_registry: TaskRegistry | None,
    active_task_id: str | None,
    hook_manager: HookManager,
    session_id: str,
    task_budget: int | None,
    task_budget_remaining: int | None,
    messages: list[ModelMessage],
    last_transition: dict | None,
    last_reactive_compact_context: dict | None,
) -> RuntimeEvent | None:
    if (
        task_registry is None
        or active_task_id is None
        or not task_registry.is_stop_requested(active_task_id)
    ):
        return None
    return _done_event(
        hook_manager=hook_manager,
        session_id=session_id,
        stop_reason="task_stop_requested",
        task_budget=task_budget,
        task_budget_remaining=_effective_task_budget_remaining(
            total_budget=task_budget,
            current_remaining=task_budget_remaining,
            messages=messages,
        ),
        task_id=active_task_id,
        transition=last_transition,
        reactive_compact_context=last_reactive_compact_context,
    )


def _build_duplicate_tool_calls_limit_events(
    *,
    hook_manager: HookManager,
    session_id: str,
    task_budget: int | None,
    task_budget_remaining: int | None,
    reactive_compact_context: dict | None,
) -> Iterator[RuntimeEvent]:
    yield RuntimeEvent(
        type="assistant",
        payload={"text": _runtime_limit_message("duplicate_tool_calls")},
    )
    yield _done_event(
        hook_manager=hook_manager,
        session_id=session_id,
        stop_reason="duplicate_tool_calls",
        task_budget=task_budget,
        task_budget_remaining=task_budget_remaining,
        transition=None,
        reactive_compact_context=reactive_compact_context,
    )


def _log_loop_phase(session_id: str, phase: str, **fields: object) -> None:
    """记录 queryLoop 主链阶段，只写结构化计数/状态，不写业务 payload。"""
    if not logger.isEnabledFor(logging.INFO):
        return
    rendered_fields = " ".join(
        f"{key}={_format_log_value(value)}"
        for key, value in fields.items()
        if value is not None
    )
    suffix = f" {rendered_fields}" if rendered_fields else ""
    logger.info("query_loop.%s session_id=%s%s", phase, session_id, suffix)


def _log_model_response_output(
    *,
    session_id: str,
    turn: int,
    stream: bool,
    output_text: str,
    reasoning_content: str,
    tool_calls: list[ToolCall],
    stop_reason: str,
) -> None:
    """在主链层记录模型完整响应。

    底层流式客户端只负责协议事件；这里拿到的是本轮采样组装后的完整输出，
    便于控制台排查模型到底说了什么、请求了哪些工具。
    """
    if not logger.isEnabledFor(logging.INFO):
        return
    tool_names = [tool.tool_name for tool in tool_calls]
    logger.info(
        "query_loop.model_output session_id=%s turn=%s stream=%s stop_reason=%s tool_calls=%s output=%s",
        session_id,
        turn,
        str(stream).lower(),
        stop_reason,
        tool_names,
        output_text,
    )
    if reasoning_content:
        logger.info(
            "query_loop.model_reasoning session_id=%s turn=%s stream=%s reasoning=%s",
            session_id,
            turn,
            str(stream).lower(),
            reasoning_content,
        )


def _format_log_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple, set)):
        return "[" + ",".join(str(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{...}"
    return str(value)


def _done_event(
    *,
    hook_manager: HookManager,
    session_id: str,
    stop_reason: str,
    **payload: object,
) -> RuntimeEvent:
    """构造 queryLoop 的唯一 done 事件出口。

    queryLoop 里所有正常结束、错误结束、等待用户/审批、预算限制等路径最终都走这里，
    这样 hook、预算状态和 done payload 的形态不会散落在各个分支里。
    """
    hook_manager.after_stop(stop_reason=stop_reason, session_id=session_id)
    payload = dict(payload)
    tool_result_budget_state = payload.pop("tool_result_budget_state", None)
    transition = payload.get("transition")
    budget_completion = _budget_completion_from_transition(transition)
    task_budget = payload.get("task_budget")
    task_budget_remaining = payload.get("task_budget_remaining")
    task_budget_used = None
    if isinstance(task_budget, int):
        effective_remaining = (
            task_budget_remaining
            if isinstance(task_budget_remaining, int)
            else None
        )
        if effective_remaining is not None:
            task_budget_used = max(0, task_budget - effective_remaining)
    done_payload = {
        "session_id": session_id,
        "stop_reason": stop_reason,
        **_serialized_tool_result_budget_payload(tool_result_budget_state),
        **({"budget_completion": budget_completion} if budget_completion is not None else {}),
        **({"task_budget_used": task_budget_used} if task_budget_used is not None else {}),
        **payload,
    }
    _log_loop_phase(
        session_id,
        "done",
        stop_reason=stop_reason,
        turn=done_payload.get("turn_count"),
        task_id=done_payload.get("task_id"),
    )
    return RuntimeEvent(type="done", payload=done_payload)


def _empty_model_usage_summary() -> dict[str, object]:
    return {
        "model_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "items": [],
    }


def _accumulate_model_usage(
    target: dict[str, object],
    *,
    usage: object,
    turn: int,
    stream: bool,
) -> None:
    if not isinstance(usage, dict):
        return
    prompt_tokens = _int_token_value(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _int_token_value(
        usage.get("completion_tokens") or usage.get("output_tokens")
    )
    total_tokens = _int_token_value(usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    target["model_calls"] = int(target.get("model_calls") or 0) + 1
    target["prompt_tokens"] = int(target.get("prompt_tokens") or 0) + prompt_tokens
    target["completion_tokens"] = int(target.get("completion_tokens") or 0) + completion_tokens
    target["total_tokens"] = int(target.get("total_tokens") or 0) + total_tokens
    items = target.setdefault("items", [])
    if isinstance(items, list):
        items.append(
            {
                "turn": turn,
                "stream": stream,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "raw_usage": usage,
            }
        )


def _int_token_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _serialized_tool_result_budget_payload(value: object) -> dict:
    if isinstance(value, ToolResultBudgetState):
        return {
            "tool_result_budget_state": serialize_tool_result_budget_state(value)
        }
    if isinstance(value, dict):
        return {"tool_result_budget_state": value}
    return {}


def _merge_tool_result_context_stats(
    target: dict[str, dict],
    *,
    section: str,
    stats: dict,
) -> None:
    if not stats:
        return
    current = target.setdefault(section, {})
    for key, value in stats.items():
        if isinstance(value, int):
            current[key] = (
                int(current.get(key, 0)) + value
                if key.endswith("_count")
                else value
            )
            continue
        if isinstance(value, list):
            existing = current.setdefault(key, [])
            if isinstance(existing, list):
                existing.extend(value)
                current[key] = _unique_strings(existing)
            else:
                current[key] = _unique_strings(value)
            continue
        current[key] = value
    _sync_tool_result_context_id_counts(current)


def _unique_strings(values: list) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _sync_tool_result_context_id_counts(stats: dict) -> None:
    id_count_keys = {
        "replacement_ids": "replaced_count",
        "cleared_ids": "cleared_count",
        "skipped_ids": "skipped_count",
    }
    for ids_key, count_key in id_count_keys.items():
        ids = stats.get(ids_key)
        if isinstance(ids, list):
            stats[count_key] = len(ids)


def _model_error_message(exc: Exception) -> str:
    return f"Model error: {exc}"


def _runtime_limit_message(
    stop_reason: str,
    *,
    turn_count: int | None = None,
    tool_summary: str | None = None,
) -> str:
    if stop_reason == "max_turns":
        detail = (
            f" ({turn_count})"
            if isinstance(turn_count, int)
            else ""
        )
        return (
            "Stopped before a final answer because the runtime reached the "
            f"maximum number of turns{detail}. Narrow the scope or continue with a more specific follow-up."
        )
    if stop_reason == "max_tool_calls":
        base = (
            "Stopped before a final answer because the runtime reached the "
            "maximum number of tool calls for this turn. Narrow the scope or continue with a more specific follow-up."
        )
        if tool_summary:
            return f"{base}\n\n{tool_summary}"
        return base
    if stop_reason == "duplicate_tool_calls":
        return (
            "Stopped before a final answer because the model repeatedly requested the same tool call "
            "with the same arguments. Use the previous tool result already in context, or continue with a more specific follow-up."
        )
    return "Stopped before a final answer because the runtime reached a limit."
