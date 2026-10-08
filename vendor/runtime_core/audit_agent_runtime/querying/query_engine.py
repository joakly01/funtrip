from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import json
import logging
import re
from typing import Any
from uuid import uuid4

from audit_agent_runtime.context.dialog_state import DialogState
from audit_agent_runtime.context.query_models import QueryRequest, ToolPolicy
from audit_agent_runtime.context.runtime_context import (
    get_system_context as get_runtime_system_context,
    get_user_context as get_runtime_user_context,
)
from audit_agent_runtime.compaction.updater import SessionCompactionUpdater
from audit_agent_runtime.memory.recall_service import (
    RelevantRecallService,
    load_project_memory_markdown,
)
from audit_agent_runtime.compaction.tool_result_collapse import render_collapsed_message_text
from audit_agent_runtime.memory.updater import SessionMemoryUpdater
from audit_agent_runtime.persistence.db import build_session_factory
from audit_agent_runtime.persistence.repositories.compactions import SessionCompactionRepository
from audit_agent_runtime.persistence.repositories.memories import SessionMemoryRepository
from audit_agent_runtime.persistence.repositories.messages import MessageRepository
from audit_agent_runtime.persistence.repositories.runtime_state import RuntimeStateRepository
from audit_agent_runtime.persistence.repositories.sessions import SessionRepository
from audit_agent_runtime.persistence.repositories.tasks import TaskRepository
from audit_agent_runtime.prompts.api_assembly import append_system_context
from audit_agent_runtime.attachments.context import build_attachment_content_blocks
from audit_agent_runtime.prompts.reminders import wrap_in_system_reminder
from audit_agent_runtime.prompts.builder import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    PromptBuilder,
)
from audit_agent_runtime.prompts.system_prompt import build_effective_system_prompt
from audit_agent_runtime.runtime.engine import RuntimeEngine
from audit_agent_runtime.runtime.events import RuntimeEvent
from audit_agent_runtime.skills.core.registry import SkillRegistry
from audit_agent_runtime.skills.core.skill_tool import render_skill_content
from audit_agent_runtime.tasks.notifications import render_notification_transcript_text
from audit_agent_runtime.tools.core.pool import build_tool_pool_context
from audit_agent_runtime.tools.core.permissions import build_can_use_tool
from audit_agent_runtime.tools.core.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _sanitize_model_visible_history_payload(role: str, payload: dict[str, Any]) -> dict[str, Any]:
    if role != "tool" or not isinstance(payload, dict):
        return payload
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    files = result.get("files")
    if not isinstance(files, list):
        return payload
    return {
        **payload,
        "result": {
            "ok": bool(result.get("ok")),
            "tool_type": result.get("tool_type"),
            "artifact_type": result.get("artifact_type"),
            "file_kind": result.get("file_kind"),
            "title": result.get("title"),
            "message": result.get("message"),
            "file_count": len(files),
            **(
                {
                    "is_error": True,
                    "error": result.get("error"),
                }
                if result.get("is_error") is True
                else {}
            ),
        },
    }


@dataclass(slots=True)
class PlanCommand:
    entered: bool = False
    remaining_text: str = ""


class QueryEngine:
    def __init__(
        self,
        settings,
        runtime_engine: RuntimeEngine,
        prompt_builder: PromptBuilder,
        skill_registry: SkillRegistry,
        tool_registry: ToolRegistry,
        session_memory_updater: SessionMemoryUpdater | None = None,
        session_compaction_updater: SessionCompactionUpdater | None = None,
        relevant_recall_service: RelevantRecallService | None = None,
    ):
        self.settings = settings
        self.runtime_engine = runtime_engine
        self.prompt_builder = prompt_builder
        self.skill_registry = skill_registry
        self.tool_registry = tool_registry
        self.session_memory_updater = session_memory_updater
        self.session_compaction_updater = session_compaction_updater
        self.relevant_recall_service = relevant_recall_service
        self.session_factory = build_session_factory(settings)
        # 这组后台维护任务只负责 session memory / compaction 刷新，不应阻塞 foreground turn 的 done。
        self._post_turn_maintenance_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="query-engine-maintenance",
        )
        self._post_turn_maintenance_futures: list[Future] = []

    def submit(self, request: QueryRequest) -> Iterator[RuntimeEvent]:
        with self.session_factory() as db:
            sessions = SessionRepository(db)
            messages = MessageRepository(db)
            memories = SessionMemoryRepository(db)
            compactions = SessionCompactionRepository(db)
            runtime_state = RuntimeStateRepository(db)
            tasks = TaskRepository(db)

            # 第一阶段：恢复会话、dialog state、task budget 等主链运行上下文。
            turn_context = self._prepare_submit_context(
                sessions=sessions,
                messages=messages,
                runtime_state=runtime_state,
                tasks=tasks,
                request=request,
            )

            # 第二阶段：组装 system prompt、recall、system reminders。
            prompt_context = self._build_prompt_context(
                messages=messages,
                memories=memories,
                compactions=compactions,
                request=request,
                turn_context=turn_context,
            )

            plan_command = turn_context["plan_command"]
            effective_input_text = turn_context["effective_input_text"]
            session_id = turn_context["session_id"]
            history = turn_context["history"]
            current_turn = turn_context["current_turn"]
            effective_task_budget = turn_context["effective_task_budget"]
            effective_task_budget_source = turn_context["effective_task_budget_source"]
            effective_task_budget_hint_positions = turn_context["effective_task_budget_hint_positions"]
            effective_task_budget_remaining = turn_context["effective_task_budget_remaining"]
            tool_result_budget_state = turn_context["tool_result_budget_state"]
            last_tool_use_summary = turn_context["last_tool_use_summary"]
            last_reactive_compact_context = turn_context["last_reactive_compact_context"]
            task_budget_recovered = turn_context["task_budget_recovered"]
            dialog_state = turn_context["dialog_state"]
            previous_dialog_state = turn_context["previous_dialog_state"]
            task_id = turn_context["task_id"]
            resumed_task = turn_context["resumed_task"]
            persisted_agent_id = turn_context["persisted_agent_id"]
            active_agent_id = turn_context["active_agent_id"]
            merged_user_context = turn_context["merged_user_context"]
            model_visible_tool_names = turn_context["model_visible_tool_names"]
            approval_decision = turn_context["approval_decision"]
            approved_plan = turn_context["approved_plan"]
            rejected_plan = turn_context["rejected_plan"]
            model_metadata = turn_context["model_metadata"]

            effective_system_prompt_blocks = prompt_context["effective_system_prompt_blocks"]
            effective_system_prompt = prompt_context["effective_system_prompt"]
            system_reminders = prompt_context["system_reminders"]
            expanded_input_content = prompt_context["expanded_input_content"]
            post_compact_context = prompt_context["post_compact_context"]
            history = _filter_history_after_compaction(
                history,
                post_compact_context=post_compact_context,
            )
            turn_wiring = self._build_turn_wiring_summary(
                dialog_state=dialog_state,
                system_reminders=system_reminders,
                model_visible_tool_names=model_visible_tool_names,
            )

            # 第三阶段：处理 `/plan` 的纯模式切换快捷路径，不进入模型调用。
            if plan_command.entered and not effective_input_text.strip():
                messages.add_message(
                    session_id=session_id,
                    role="user",
                    payload={"text": "/plan"},
                )
                messages.add_message(
                    session_id=session_id,
                    role="assistant",
                    payload={
                        "text": (
                            "Entered plan mode. Focus on exploration, clarification, "
                            "and designing an implementation approach before coding."
                        )
                    },
                )
                runtime_state.upsert_state(
                    session_id=session_id,
                    payload={
                        "turn": current_turn + 1,
                        "dialog_state": dialog_state.to_payload(),
                        "last_stop_reason": "entered_plan_mode",
                    },
                )
                db.commit()
                yield RuntimeEvent(
                    type="assistant",
                    payload={
                        "text": (
                            "Entered plan mode. Focus on exploration, clarification, "
                            "and designing an implementation approach before coding."
                        )
                    },
                )
                yield RuntimeEvent(
                    type="done",
                    payload={
                        "session_id": session_id,
                        "turn": current_turn + 1,
                        "stop_reason": "entered_plan_mode",
                        "pending_user_question": None,
                    },
                )
                return

            # 第四阶段：把当前 user turn 和前台 task 落库，形成后续 runtime stream 的持久化锚点。
            turn_id = task_id
            turn_sequence = [0]
            messages.add_message(
                session_id=session_id,
                role="user",
                payload=_with_turn_replay_metadata(
                    _build_user_message_payload(
                        input_text=effective_input_text,
                        expanded_input_content=expanded_input_content,
                    input_mode=request.input_mode,
                    metadata=request.metadata,
                    ),
                    turn_id=turn_id,
                    sequence=_next_turn_sequence(turn_sequence),
                    kind="message",
                    event_type="user",
                ),
            )
            if resumed_task is None:
                task = tasks.create_task(
                    task_id=task_id,
                    session_id=session_id,
                    agent_id=persisted_agent_id,
                    status="running",
                    mode="foreground",
                    payload=self._build_task_payload(
                        base_payload=None,
                        query=effective_input_text,
                        metadata=request.metadata,
                        query_source=request.query_source,
                        task_budget=effective_task_budget,
                        task_budget_source=effective_task_budget_source,
                        task_budget_hint_positions=effective_task_budget_hint_positions,
                        task_budget_recovered=task_budget_recovered,
                        task_budget_remaining=effective_task_budget_remaining,
                        post_compact_context=post_compact_context,
                        tool_use_summary=last_tool_use_summary,
                        reactive_compact_context=last_reactive_compact_context,
                    ),
                )
            else:
                task = tasks.update_task(
                    resumed_task.task_id,
                    status="running",
                    payload=self._build_task_payload(
                        base_payload={
                            **resumed_task.payload,
                            "continuations": [
                                *resumed_task.payload.get("continuations", []),
                                effective_input_text,
                            ],
                        },
                        query=effective_input_text,
                        metadata=request.metadata,
                        query_source=request.query_source,
                        task_budget=effective_task_budget,
                        task_budget_source=effective_task_budget_source,
                        task_budget_hint_positions=effective_task_budget_hint_positions,
                        task_budget_recovered=task_budget_recovered,
                        task_budget_remaining=effective_task_budget_remaining,
                        post_compact_context=post_compact_context,
                        tool_use_summary=last_tool_use_summary,
                        reactive_compact_context=last_reactive_compact_context,
                    ),
                )
            task = tasks.update_task(
                task.task_id,
                status="running",
                payload=self._build_task_payload(
                    base_payload=task.payload,
                    query=effective_input_text,
                    metadata=request.metadata,
                    query_source=request.query_source,
                    task_budget=effective_task_budget,
                    task_budget_source=effective_task_budget_source,
                    task_budget_hint_positions=effective_task_budget_hint_positions,
                    task_budget_recovered=task_budget_recovered,
                    task_budget_remaining=effective_task_budget_remaining,
                    post_compact_context=post_compact_context,
                    tool_use_summary=last_tool_use_summary,
                    reactive_compact_context=last_reactive_compact_context,
                ),
            )
            dialog_state.active_agent_id = active_agent_id
            dialog_state.awaiting_task_id = task_id
            dialog_state.status = "running"
            dialog_state.pending_user_question = None
            dialog_state.pending_params = {}
            db.commit()

            # 第五阶段：向前端公开这一轮 turn 的起始状态，让页面知道当前 task 和恢复上下文。
            yield RuntimeEvent(
                type="request_start",
                payload=self._build_request_start_payload(
                    session_id=session_id,
                    task_id=task.task_id,
                    turn=current_turn + 1,
                    resumed=resumed_task is not None,
                    dialog_state=dialog_state,
                    task_budget=effective_task_budget,
                    task_budget_source=effective_task_budget_source,
                    task_budget_hint_positions=effective_task_budget_hint_positions,
                    task_budget_recovered=task_budget_recovered,
                    task_budget_remaining=effective_task_budget_remaining,
                    post_compact_context=post_compact_context,
                    tool_use_summary=last_tool_use_summary,
                    reactive_compact_context=last_reactive_compact_context,
                    turn_wiring=turn_wiring,
                ),
            )

            assistant_chunks: list[str] = []
            thinking_chunks: list[str] = []
            terminal_payload: dict = {"stop_reason": "end_turn"}
            active_skill_ids = set(dialog_state.active_skill_ids)
            active_skill_hints = dict(dialog_state.active_skill_hints)
            background_task_id: str | None = None
            background_agent_id: str | None = None
            background_notification_consumed = False
            can_use_tool = _build_can_use_tool(
                request.tool_policy,
                previous_dialog_state=previous_dialog_state,
                approval_decision=approval_decision,
                active_skill_hints=dialog_state.active_skill_hints,
            )
            # 第六阶段：进入 RuntimeEngine 主链，并把运行时事件同步回数据库和 dialog state。
            for event in self.runtime_engine.stream(
                **_runtime_stream_kwargs(
                    runtime_engine=self.runtime_engine,
                    query=expanded_input_content,
                    history=history,
                    session_id=session_id,
                    system_prompt=effective_system_prompt,
                    system_prompt_blocks=effective_system_prompt_blocks,
                    system_reminders=system_reminders,
                    user_context=merged_user_context,
                    metadata=model_metadata,
                    task_id=task.task_id,
                    agent_id=active_agent_id,
                    can_use_tool=can_use_tool,
                    working_directory=request.working_directory,
                    max_turns=request.max_turns,
                    model_visible_tool_names=model_visible_tool_names,
                    task_budget=effective_task_budget,
                    task_budget_source=effective_task_budget_source,
                    task_budget_hint_positions=effective_task_budget_hint_positions,
                    task_budget_remaining=effective_task_budget_remaining,
                    task_budget_recovered=task_budget_recovered,
                    tool_result_budget_state=tool_result_budget_state,
                    enable_outer_compaction=self.relevant_recall_service is None,
                )
            ):
                event_payload = event.payload if isinstance(event.payload, dict) else {}
                # assistant 增量只先缓存在内存里；直到这一轮 stop_reason 明确后再决定如何落库。
                if event.type == "assistant":
                    text = event_payload.get("text")
                    if isinstance(text, str):
                        assistant_chunks.append(text)
                if event.type == "thinking":
                    text = event_payload.get("text")
                    if isinstance(text, str) and text:
                        thinking_chunks.append(text)
                # 工具调用前的 assistant 文本需要单独落库，供下一轮历史重放时恢复 tool_call 轨迹。
                if event.type == "tool_use":
                    assistant_tool_calls = event_payload.get("assistant_tool_calls")
                    if isinstance(assistant_tool_calls, list) and assistant_tool_calls:
                        assistant_text = event_payload.get("assistant_text", "")
                        assistant_reasoning_content = _normalize_optional_text(
                            event_payload.get("assistant_reasoning_content")
                        )
                        assistant_payload = {
                            "text": "" if assistant_text is None else str(assistant_text),
                            "tool_calls": assistant_tool_calls,
                        }
                        if assistant_reasoning_content:
                            assistant_payload["reasoning_content"] = assistant_reasoning_content
                        messages.add_message(
                            session_id=session_id,
                            role="assistant",
                            payload=_with_turn_replay_metadata(
                                assistant_payload,
                                turn_id=turn_id,
                                sequence=_next_turn_sequence(turn_sequence),
                                kind="tool_use",
                                event_type="tool_use",
                            ),
                        )
                        assistant_chunks.clear()
                        db.commit()
                # tool_result / notification 都是运行时可见历史，需要立即持久化。
                if event.type == "tool_result":
                    messages.add_message(
                        session_id=session_id,
                        role="tool",
                        payload=_with_turn_replay_metadata(
                            event_payload,
                            turn_id=turn_id,
                            sequence=_next_turn_sequence(turn_sequence),
                            kind="tool_result",
                            event_type="tool_result",
                        ),
                        )
                    db.commit()
                if event.type == "tool_result":
                    # load_skill / skill_definition 会改变后续 turn 的 active skills，需要同步 runtime hints。
                    tool_name = event_payload.get("tool_name")
                    result = event_payload.get("result", {})
                    if (
                        isinstance(result, dict)
                        and result.get("kind") == "skill_definition"
                    ):
                        skill_name = _normalize_optional_text(result.get("skill_name"))
                        if skill_name is None:
                            continue
                        active_skill_ids.add(skill_name)
                        active_skill_hints[skill_name] = _extract_active_skill_hint(result)
                    elif (
                        tool_name == "load_skill"
                        and isinstance(result, dict)
                        and result.get("kind") == "skill_definition"
                    ):
                        skill_name = _normalize_optional_text(result.get("skill_name"))
                        if skill_name is None:
                            continue
                        active_skill_ids.add(skill_name)
                        active_skill_hints[skill_name] = _extract_active_skill_hint(result)
                if event.type == "notification":
                    payload = dict(event_payload)
                    if (
                        background_task_id is not None
                        and payload.get("task_id") == background_task_id
                    ):
                        background_notification_consumed = True
                    messages.add_message(
                        session_id=session_id,
                        role="user",
                        payload=_with_turn_replay_metadata(
                            {
                                "text": render_notification_transcript_text(payload),
                                "kind": "notification",
                                "payload": payload,
                            },
                            turn_id=turn_id,
                            sequence=_next_turn_sequence(turn_sequence),
                            kind="notification",
                            event_type="notification",
                        ),
                    )
                    db.commit()
                # 这几类 runtime event 会直接改变 dialog_state，是主线程下一轮行为的依据。
                if event.type == "tool_approval_required":
                    dialog_state.status = "awaiting_tool_approval"
                    dialog_state.pending_user_question = event_payload.get("message")
                    dialog_state.pending_params = {
                        "tool_name": event_payload.get("tool_name"),
                        "arguments": event_payload.get("arguments", {}),
                        "tool_call_id": event_payload.get("tool_call_id"),
                    }
                    _normalize_pending_tool_approval_state(dialog_state)
                if event.type == "tool_result":
                    result = event_payload.get("result", {})
                    tool_name = event_payload.get("tool_name")
                    if (
                        tool_name == "spawn_agent"
                        and isinstance(result, dict)
                        and result.get("mode") == "background"
                        and isinstance(result.get("task_id"), str)
                    ):
                        background_task_id = result["task_id"]
                        background_agent_id = _normalize_optional_text(
                            result.get("agent_id")
                        )
                    if isinstance(result, dict) and result.get("kind") == "plan_mode_enter":
                        dialog_state.plan_mode = True
                        dialog_state.status = "idle"
                        dialog_state.pending_user_question = None
                        dialog_state.pending_params = {}
                    if isinstance(result, dict) and result.get("kind") == "user_question":
                        dialog_state.status = "awaiting_user_question"
                        dialog_state.pending_user_question = _normalize_optional_text(
                            result.get("question")
                        )
                        dialog_state.pending_params = {
                            "question": dialog_state.pending_user_question
                        }
                    if isinstance(result, dict) and result.get("kind") == "plan_mode_exit":
                        dialog_state.status = "awaiting_plan_approval"
                        dialog_state.pending_user_question = _normalize_optional_text(
                            result.get("question")
                        ) or "Exit plan mode?"
                        dialog_state.pending_params = {
                            "plan": _normalize_optional_text(result.get("plan")),
                            "allowed_prompts": result.get("allowed_prompts", []),
                        }
                if event.type == "done":
                    terminal_payload = dict(event_payload)
                    continue
                yield RuntimeEvent(type=event.type, payload=event_payload)

            # 第七阶段：根据 stop_reason 收敛 assistant 文本、task 状态和 dialog_state。
            stop_reason = terminal_payload.get("stop_reason", "end_turn")
            final_task_budget_remaining = (
                terminal_payload.get("task_budget_remaining")
                if terminal_payload.get("task_budget_remaining") is not None
                else effective_task_budget_remaining
            )
            final_task_budget_used = (
                terminal_payload.get("task_budget_used")
                if terminal_payload.get("task_budget_used") is not None
                else (
                    max(0, effective_task_budget - effective_task_budget_remaining)
                    if isinstance(effective_task_budget, int)
                    and isinstance(effective_task_budget_remaining, int)
                    else None
                )
            )
            final_tool_result_budget_state = terminal_payload.get(
                "tool_result_budget_state",
                tool_result_budget_state,
            )
            assistant_text = _persist_assistant_chunks(
                messages=messages,
                session_id=session_id,
                assistant_chunks=assistant_chunks,
                stop_reason=stop_reason,
                turn_id=turn_id,
                turn_sequence=turn_sequence,
                thinking_chunks=thinking_chunks,
                terminal_payload=terminal_payload,
            )
            final_task_status = (
                "stop_requested"
                if stop_reason == "task_stop_requested"
                else "awaiting_tool_approval"
                if stop_reason == "awaiting_tool_approval"
                else "awaiting_user_question"
                if stop_reason == "awaiting_user_question"
                else "awaiting_plan_approval"
                if stop_reason == "awaiting_plan_approval"
                else "failed"
                if stop_reason == "model_error"
                else "completed"
            )
            tasks.update_task(
                task.task_id,
                status=final_task_status,
                payload=self._build_completed_task_payload(
                    base_payload=task.payload,
                    query=effective_input_text,
                    metadata=request.metadata,
                    query_source=request.query_source,
                    task_budget=effective_task_budget,
                    task_budget_source=effective_task_budget_source,
                    task_budget_hint_positions=effective_task_budget_hint_positions,
                    task_budget_recovered=task_budget_recovered,
                    task_budget_remaining=final_task_budget_remaining,
                    task_budget_used=final_task_budget_used,
                    assistant_text=assistant_text,
                    transition=terminal_payload.get("transition"),
                    budget_completion=terminal_payload.get("budget_completion"),
                    post_compact_context=post_compact_context,
                    reactive_compact_context=terminal_payload.get("reactive_compact_context"),
                    tool_use_summary=terminal_payload.get("tool_use_summary"),
                    tool_result_context=terminal_payload.get("tool_result_context"),
                    provider_payload_diagnostics=terminal_payload.get("provider_payload_diagnostics"),
                ),
            )
            if background_task_id is not None and not background_notification_consumed:
                dialog_state.awaiting_task_id = background_task_id
                dialog_state.active_agent_id = background_agent_id
                dialog_state.status = "waiting_background_task"
                dialog_state.pending_user_question = "后台分析进行中，等待子任务结果。"
                dialog_state.pending_params = {
                    "task_id": background_task_id,
                    "agent_id": background_agent_id,
                }
            else:
                dialog_state.awaiting_task_id = None
                if dialog_state.status == "waiting_background_task":
                    dialog_state.active_agent_id = None
            dialog_state.active_skill_ids = sorted(active_skill_ids)
            dialog_state.active_skill_hints = _normalize_active_skill_hints(active_skill_hints)
            _prune_hidden_active_skills(dialog_state)
            if (
                stop_reason not in {"awaiting_tool_approval", "awaiting_user_question", "awaiting_plan_approval"}
                and dialog_state.status != "waiting_background_task"
            ):
                dialog_state.status = "idle"
                dialog_state.pending_user_question = None
                dialog_state.pending_params = {}
            runtime_state.upsert_state(
                session_id=session_id,
                payload=self._build_runtime_state_payload(
                    turn=current_turn + 1,
                    dialog_state=dialog_state,
                    active_task_id=task.task_id,
                    stop_reason=stop_reason,
                    transition=terminal_payload.get("transition"),
                    budget_completion=terminal_payload.get("budget_completion"),
                    post_compact_context=post_compact_context,
                    reactive_compact_context=terminal_payload.get("reactive_compact_context"),
                    tool_use_summary=terminal_payload.get("tool_use_summary"),
                    tool_result_context=terminal_payload.get("tool_result_context"),
                    provider_payload_diagnostics=terminal_payload.get("provider_payload_diagnostics"),
                    # 保存模型可见工具结果裁剪状态，下一 turn 恢复后复用同一替换决定。
                    # 这不是证据数据本身，完整工具结果仍在 messages/session_events。
                    tool_result_budget_state=final_tool_result_budget_state,
                    task_budget=effective_task_budget,
                    task_budget_source=effective_task_budget_source,
                    task_budget_hint_positions=effective_task_budget_hint_positions,
                    task_budget_recovered=task_budget_recovered,
                    task_budget_remaining=final_task_budget_remaining,
                    task_budget_used=final_task_budget_used,
                ),
            )
            completed_task_id = task.task_id
            db.commit()
            # 第八阶段：只有完整结束的 foreground turn 才调度后台 memory / compaction 维护。
            # 这部分是面向后续 recall/compaction 的维护能力，不应该继续阻塞当前 turn 的完成态。
            # 调度前先做轻量阈值判断，避免每个小 turn 都排一个后台任务。
            if final_task_status == "completed" and (
                self.session_memory_updater is not None or self.session_compaction_updater is not None
            ):
                persisted_messages = [
                    {
                        "id": message.id,
                        "role": message.role,
                        "payload": message.payload_json,
                    }
                    for message in messages.list_messages(session_id)
                ]
                should_refresh_memory = (
                    self.session_memory_updater is not None
                    and self.session_memory_updater.should_update_session_memory(
                        db=db,
                        session_id=session_id,
                        persisted_messages=persisted_messages,
                    )
                )
                should_refresh_compaction = (
                    self.session_compaction_updater is not None
                    and terminal_payload.get("reactive_compact_context") is not None
                    and self.session_compaction_updater.should_update_compaction_state(
                        db=db,
                        session_id=session_id,
                        persisted_messages=persisted_messages,
                    )
                )
                maintenance_runtime_state_payload = (
                    self._build_runtime_state_payload(
                        turn=current_turn + 1,
                        dialog_state=dialog_state,
                        active_task_id=completed_task_id,
                        stop_reason=stop_reason,
                        transition=terminal_payload.get("transition"),
                        budget_completion=terminal_payload.get("budget_completion"),
                        post_compact_context=post_compact_context,
                        reactive_compact_context=terminal_payload.get("reactive_compact_context"),
                        tool_use_summary=terminal_payload.get("tool_use_summary"),
                        tool_result_context=terminal_payload.get("tool_result_context"),
                        provider_payload_diagnostics=terminal_payload.get("provider_payload_diagnostics"),
                        tool_result_budget_state=final_tool_result_budget_state,
                        task_budget=effective_task_budget,
                        task_budget_source=effective_task_budget_source,
                        task_budget_hint_positions=effective_task_budget_hint_positions,
                        task_budget_recovered=task_budget_recovered,
                        task_budget_remaining=final_task_budget_remaining,
                        task_budget_used=final_task_budget_used,
                    )
                )
                # SQLite 测试与单连接嵌入式宿主会共享同一底层连接；后台任务
                # 启动前显式释放 foreground Session，避免 close/rollback 与维护
                # 事务竞态。生产 PostgreSQL 同样在这里完成清晰的事务交接。
                db.close()
                self._schedule_post_turn_maintenance(
                    session_id=session_id,
                    task_id=completed_task_id,
                    current_turn=current_turn + 1,
                    persisted_messages=persisted_messages,
                    should_refresh_memory=should_refresh_memory,
                    should_refresh_compaction=should_refresh_compaction,
                    runtime_state_payload=maintenance_runtime_state_payload,
                )

            # 第九阶段：输出这一轮的统一 done 事件，供 API 层决定是否继续 drain notification。
            yield RuntimeEvent(
                type="done",
                payload={
                    **terminal_payload,
                    "session_id": session_id,
                    "turn": current_turn + 1,
                    "task_id": completed_task_id,
                    "stop_reason": stop_reason,
                    "plan_mode": dialog_state.plan_mode,
                    "task_budget": effective_task_budget,
                    "task_budget_source": effective_task_budget_source,
                    "task_budget_hint_positions": [
                        list(position)
                        for position in effective_task_budget_hint_positions
                    ],
                    "task_budget_recovered": task_budget_recovered,
                    "post_compact_context": post_compact_context,
                    "reactive_compact_context": terminal_payload.get("reactive_compact_context"),
                    "tool_use_summary": terminal_payload.get("tool_use_summary"),
                    "pending_user_question": dialog_state.pending_user_question,
                    "dialog_status": dialog_state.status,
                },
            )

    def _schedule_post_turn_maintenance(
        self,
        *,
        session_id: str,
        task_id: str,
        current_turn: int,
        persisted_messages: list[dict],
        should_refresh_memory: bool,
        should_refresh_compaction: bool,
        runtime_state_payload: dict[str, Any],
    ) -> None:
        if not should_refresh_memory and not should_refresh_compaction:
            return
        # 后置维护不阻塞 foreground done，但仍要保留 future 作为可观测边界：
        # 测试、关闭流程或调试时可以 drain，避免只能靠轮询数据库判断后台任务是否完成。
        future = self._post_turn_maintenance_executor.submit(
            self._run_post_turn_maintenance,
            session_id=session_id,
            task_id=task_id,
            current_turn=current_turn,
            persisted_messages=persisted_messages,
            should_refresh_memory=should_refresh_memory,
            should_refresh_compaction=should_refresh_compaction,
            runtime_state_payload=runtime_state_payload,
        )
        self._post_turn_maintenance_futures.append(future)

    def drain_post_turn_maintenance(self) -> None:
        while self._post_turn_maintenance_futures:
            pending = list(self._post_turn_maintenance_futures)
            self._post_turn_maintenance_futures.clear()
            for future in pending:
                future.result()

    def _run_post_turn_maintenance(
        self,
        *,
        session_id: str,
        task_id: str,
        current_turn: int,
        persisted_messages: list[dict],
        should_refresh_memory: bool,
        should_refresh_compaction: bool,
        runtime_state_payload: dict[str, Any],
    ) -> None:
        try:
            with self.session_factory() as db:
                messages = MessageRepository(db)
                compactions = SessionCompactionRepository(db)
                runtime_state = RuntimeStateRepository(db)
                tasks = TaskRepository(db)

                if should_refresh_memory and self.session_memory_updater is not None:
                    self.session_memory_updater.update_session_memory(
                        db=db,
                        session_id=session_id,
                        current_turn=current_turn,
                        persisted_messages=persisted_messages,
                    )
                    db.commit()

                if should_refresh_compaction and self.session_compaction_updater is not None:
                    self.session_compaction_updater.update_compaction_state(
                        db=db,
                        session_id=session_id,
                        persisted_messages=persisted_messages,
                    )
                    refreshed_post_compact_context = self._refresh_post_compact_context(
                        compactions=compactions,
                        messages=messages,
                        session_id=session_id,
                    )
                    task = tasks.get_task(task_id)
                    if task is not None:
                        tasks.update_task(
                            task.task_id,
                            payload={
                                **task.payload,
                                "post_compact_context": refreshed_post_compact_context,
                            },
                        )
                    runtime_state.upsert_state(
                        session_id=session_id,
                        payload={
                            **runtime_state_payload,
                            "last_post_compact_context": refreshed_post_compact_context,
                        },
                    )
                    db.commit()
        except Exception:
            logger.exception("post-turn maintenance failed for session %s", session_id)
            raise

    def _prepare_submit_context(
        self,
        *,
        sessions: SessionRepository,
        messages: MessageRepository,
        runtime_state: RuntimeStateRepository,
        tasks: TaskRepository,
        request: QueryRequest,
    ) -> dict[str, Any]:
        # 恢复 session + runtime_state，并把历史中仍有效的 dialog state 规范化成主链可用结构。
        session = sessions.get_session(request.session_id)
        plan_command = _extract_plan_command(request.input_text)
        effective_input_text = (
            plan_command.remaining_text if plan_command.entered else request.input_text
        )
        if session is None:
            session = sessions.create_session(
                session_id=request.session_id,
                user_id=request.user_id,
                title=(effective_input_text or "Plan mode")[:255],
            )
        session_id = session.session_id

        history = [
            {
                "role": message.role,
                "message_id": message.id,
                "payload": _sanitize_model_visible_history_payload(
                    message.role,
                    message.payload_json,
                ),
            }
            for message in messages.list_messages(session_id)
        ]
        state_payload = runtime_state.get_state(session_id) or {}
        current_turn = _normalize_turn_value(state_payload.get("turn"))
        effective_task_budget = (
            request.task_budget
            if request.task_budget is not None
            else _normalize_optional_int(state_payload.get("task_budget"))
        )
        effective_task_budget_source = (
            request.task_budget_source
            if request.task_budget_source is not None
            else "explicit"
            if request.task_budget is not None
            else None
            if effective_task_budget is None
            else _normalize_optional_text(state_payload.get("task_budget_source"))
        )
        effective_task_budget_hint_positions = (
            request.task_budget_hint_positions
            if request.task_budget_hint_positions
            else []
            if request.task_budget is not None
            else []
            if effective_task_budget is None
            else _normalize_hint_positions(state_payload.get("task_budget_hint_positions"))
        )
        effective_task_budget_remaining = (
            None
            if request.task_budget is not None or effective_task_budget is None
            else _normalize_optional_int(state_payload.get("task_budget_remaining"))
        )
        last_tool_use_summary = (
            state_payload.get("last_tool_use_summary")
            if isinstance(state_payload.get("last_tool_use_summary"), dict)
            else None
        )
        last_reactive_compact_context = (
            state_payload.get("last_reactive_compact_context")
            if isinstance(state_payload.get("last_reactive_compact_context"), dict)
            else None
        )
        task_budget_recovered = (
            request.task_budget is None and effective_task_budget is not None
        )
        dialog_state = DialogState.from_payload(state_payload.get("dialog_state"))
        previous_dialog_state = DialogState.from_payload(state_payload.get("dialog_state"))
        if plan_command.entered:
            dialog_state.plan_mode = True
        dialog_state.active_skill_ids = _normalize_active_skill_ids(dialog_state.active_skill_ids)
        previous_dialog_state.active_skill_ids = _normalize_active_skill_ids(
            previous_dialog_state.active_skill_ids
        )
        if dialog_state.active_skill_ids:
            # 已加载 skill 的 frontmatter 可能在代码更新后发生变化。每轮按当前 registry
            # 刷新轻量提示，避免旧会话继续携带过期的 allowed-tools / argument-hint。
            dialog_state.active_skill_hints = {
                **dialog_state.active_skill_hints,
                **_backfill_active_skill_hints(dialog_state.active_skill_ids, self.skill_registry),
            }
            previous_dialog_state.active_skill_hints = {
                **previous_dialog_state.active_skill_hints,
                **_backfill_active_skill_hints(previous_dialog_state.active_skill_ids, self.skill_registry),
            }
        dialog_state.active_skill_hints = _normalize_active_skill_hints(
            dialog_state.active_skill_hints
        )
        previous_dialog_state.active_skill_hints = _normalize_active_skill_hints(
            previous_dialog_state.active_skill_hints
        )
        _normalize_pending_tool_approval_state(dialog_state)
        _normalize_pending_tool_approval_state(previous_dialog_state)
        _prune_hidden_active_skills(dialog_state)
        _prune_hidden_active_skills(previous_dialog_state)
        task_id = request.resume_task_id or str(uuid4())
        resumed_task = tasks.get_task(task_id) if request.resume_task_id else None
        # 队列消费产生的 follow-up turn 必须回到主智能体上下文。
        # 这里如果继续继承 waiting_background_task 阶段留下的 active_agent_id，
        # 就会把通知消费误跑进子智能体上下文，导致主线程拿不到综合后的正式回复。
        if request.query_source in {"api_queue_drain", "notification_host"}:
            persisted_agent_id = "main"
        else:
            persisted_agent_id = (
                resumed_task.agent_id
                if resumed_task is not None
                else dialog_state.active_agent_id or "main"
            )
        active_agent_id = None if persisted_agent_id == "main" else persisted_agent_id
        auto_system_context = get_runtime_system_context(request.working_directory)
        auto_user_context = get_runtime_user_context(request.working_directory)
        # 已加载 skill 是下一轮判断的上下文，不是后续 turn 的工具可见性锁。
        # Claude Code 会把 skill 的 allowedTools 合入权限上下文，但主循环仍把常规工具池交给模型，
        # 让模型根据最新用户问题决定继续当前 skill 还是改用其他能力。
        model_visible_tool_names = None
        merged_system_context = {
            **auto_system_context,
            **_normalize_context_values(request.system_context),
        }
        merged_user_context = {
            **auto_user_context,
            **_normalize_context_values(request.user_context),
        }
        pending_tool_call_id = _normalize_optional_text(
            previous_dialog_state.pending_params.get("tool_call_id")
        )
        approval_decision = _classify_pending_tool_approval_decision(
            request.input_text,
            metadata=request.metadata,
            pending_tool_call_id=pending_tool_call_id,
        )
        approved_plan: str | None = None
        rejected_plan: str | None = None
        if previous_dialog_state.status == "awaiting_plan_approval":
            pending_plan = _normalize_optional_text(
                previous_dialog_state.pending_params.get("plan")
            )
            if approval_decision == "approved":
                approved_plan = pending_plan
                dialog_state.plan_mode = False
                dialog_state.status = "idle"
                dialog_state.pending_user_question = None
                dialog_state.pending_params = {}
            elif approval_decision == "denied":
                rejected_plan = pending_plan
                dialog_state.plan_mode = True
                dialog_state.status = "idle"
                dialog_state.pending_user_question = None
                dialog_state.pending_params = {}
        elif previous_dialog_state.status == "awaiting_user_question":
            dialog_state.status = "idle"
            dialog_state.pending_user_question = None
            dialog_state.pending_params = {}

        return {
            "plan_command": plan_command,
            "effective_input_text": effective_input_text,
            "session_id": session_id,
            "history": history,
            "current_turn": current_turn,
            "effective_task_budget": effective_task_budget,
            "effective_task_budget_source": effective_task_budget_source,
            "effective_task_budget_hint_positions": effective_task_budget_hint_positions,
            "effective_task_budget_remaining": effective_task_budget_remaining,
            "tool_result_budget_state": state_payload.get("tool_result_budget_state"),
            "last_tool_use_summary": last_tool_use_summary,
            "last_reactive_compact_context": last_reactive_compact_context,
            "task_budget_recovered": task_budget_recovered,
            "dialog_state": dialog_state,
            "previous_dialog_state": previous_dialog_state,
            "task_id": task_id,
            "resumed_task": resumed_task,
            "persisted_agent_id": persisted_agent_id,
            "active_agent_id": active_agent_id,
            "merged_system_context": merged_system_context,
            "merged_user_context": merged_user_context,
            "model_visible_tool_names": model_visible_tool_names,
            "approval_decision": approval_decision,
            "approved_plan": approved_plan,
            "rejected_plan": rejected_plan,
            "model_metadata": {
                **_metadata_without_resolved_attachments(request.metadata),
                "user_id": request.user_id,
            },
        }

    def _build_prompt_context(
        self,
        *,
        messages: MessageRepository,
        memories: SessionMemoryRepository,
        compactions: SessionCompactionRepository,
        request: QueryRequest,
        turn_context: dict[str, Any],
    ) -> dict[str, Any]:
        # 组装系统提示、回忆信息和 system reminders，让模型在一次 turn 内拿到完整主线程上下文。
        dialog_state: DialogState = turn_context["dialog_state"]
        previous_dialog_state: DialogState = turn_context["previous_dialog_state"]
        session_id = turn_context["session_id"]
        effective_input_text = turn_context["effective_input_text"]
        active_agent_id = turn_context["active_agent_id"]
        merged_system_context = turn_context["merged_system_context"]
        approval_decision = turn_context["approval_decision"]
        approved_plan = turn_context["approved_plan"]
        rejected_plan = turn_context["rejected_plan"]
        last_tool_use_summary = turn_context["last_tool_use_summary"]
        last_reactive_compact_context = turn_context["last_reactive_compact_context"]

        default_system_prompt_blocks = self.prompt_builder.build_blocks(
            skill_registry=self.skill_registry,
            tool_registry=self.tool_registry,
            additional_sections=None,
            coordinator_mode=False,
            plan_mode=dialog_state.plan_mode,
        )
        effective_system_prompt_blocks = build_effective_system_prompt(
            default_system_prompt=default_system_prompt_blocks,
            main_thread_agent_prompt=None,
            custom_system_prompt=request.custom_system_prompt,
            append_system_prompt=request.tool_policy.describe(),
            override_system_prompt=request.override_system_prompt,
            coordinator_system_prompt=self.prompt_builder.build_coordinator_system_prompt(),
            coordinator_mode=request.coordinator_mode,
            proactive_mode=False,
        )
        latest_memory = memories.get_latest_memory(session_id)
        latest_compaction = compactions.get_latest_compaction(session_id)
        recent_compactions = compactions.list_recent_compactions(session_id, limit=3)
        latest_message_id = max(
            (message.id for message in messages.list_messages(session_id)),
            default=None,
        )
        has_preserved_range = (
            latest_compaction is not None
            and (
                latest_compaction.preserved_from_message_id is not None
                or latest_compaction.preserved_to_message_id is not None
            )
        )
        preserved_messages = (
            [
                {
                    "id": message.id,
                    "role": message.role,
                    "payload": message.payload_json,
                }
                for message in messages.list_message_range(
                    session_id,
                    start_id=latest_compaction.preserved_from_message_id,
                    end_id=latest_compaction.preserved_to_message_id,
                )
            ]
            if has_preserved_range
            else []
        )
        post_compact_context = _build_post_compact_context(
            latest_compaction=latest_compaction,
            preserved_messages=preserved_messages,
        )
        if self.relevant_recall_service is not None:
            recall_sections = self.relevant_recall_service.build_recall_sections(
                query=effective_input_text,
                latest_memory=(
                    {
                        "summary_markdown": latest_memory.summary_markdown,
                        "summary_json": latest_memory.summary_json,
                        "summarizes_until_message_id": latest_memory.summarizes_until_message_id,
                        "latest_message_id": latest_message_id,
                    }
                    if latest_memory is not None
                    else None
                ),
                latest_compaction=(
                    {
                        "summary_text": latest_compaction.summary_text,
                        "boundary_message_id": latest_compaction.boundary_message_id,
                        "preserved_messages": preserved_messages,
                    }
                    if latest_compaction is not None
                    else None
                ),
                recent_compactions=[
                    {
                        "summary_text": compaction.summary_text,
                        "boundary_message_id": compaction.boundary_message_id,
                    }
                    for compaction in recent_compactions
                ],
                project_memory_markdown=load_project_memory_markdown(
                    self.settings.runtime_project_memory_path
                ),
                active_skill_hints=dialog_state.active_skill_hints,
                last_tool_use_summary=last_tool_use_summary,
                last_reactive_compact_context=last_reactive_compact_context,
            )
            effective_system_prompt_blocks.extend(
                _render_recall_section(section)
                for section in recall_sections
                if section.placement == "system"
            )
        else:
            recall_sections = []
        effective_system_prompt_blocks = append_system_context(
            effective_system_prompt_blocks,
            merged_system_context,
        )
        effective_system_prompt = "\n\n".join(
            block for block in effective_system_prompt_blocks
            if block != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
        )
        system_reminders = self.prompt_builder.build_system_reminders(
            self.skill_registry,
            query=effective_input_text,
        )
        system_reminders.extend(
            wrap_in_system_reminder(_render_recall_section(section))
            for section in recall_sections
            if section.placement != "system"
        )
        task_notification_reminder = _build_task_notification_reminder(self.tool_registry)
        if task_notification_reminder is not None:
            system_reminders.append(task_notification_reminder)
        active_skill_reminder = _build_active_skill_reminder(dialog_state)
        if active_skill_reminder is not None:
            system_reminders.append(active_skill_reminder)
        pending_tool_approval_reminder = _build_pending_tool_approval_reminder(
            previous_dialog_state,
            approval_decision=approval_decision,
        )
        if pending_tool_approval_reminder is not None:
            system_reminders.append(pending_tool_approval_reminder)
        pending_plan_approval_reminder = _build_pending_plan_approval_reminder(
            previous_dialog_state,
            approval_decision=approval_decision,
            approved_plan=approved_plan,
            rejected_plan=rejected_plan,
        )
        if pending_plan_approval_reminder is not None:
            system_reminders.append(pending_plan_approval_reminder)

        expanded_input_content = _expand_slash_skill_input(
            effective_input_text,
            skill_registry=self.skill_registry,
            session_id=session_id,
            skip_slash_commands=request.skip_slash_commands,
        )
        resolved_attachments = (
            request.metadata.get("resolved_attachments")
            if isinstance(request.metadata, dict)
            else None
        )
        if isinstance(resolved_attachments, list) and resolved_attachments:
            expanded_input_content = build_attachment_content_blocks(
                expanded_input_content,
                [item for item in resolved_attachments if isinstance(item, dict)],
            )
        return {
            "effective_system_prompt_blocks": effective_system_prompt_blocks,
            "effective_system_prompt": effective_system_prompt,
            "system_reminders": system_reminders,
            "expanded_input_content": expanded_input_content,
            "post_compact_context": post_compact_context,
        }

    def _build_request_start_payload(
        self,
        *,
        session_id: str,
        task_id: str,
        turn: int,
        resumed: bool,
        dialog_state: DialogState,
        task_budget: int | None,
        task_budget_source: str | None,
        task_budget_hint_positions: list[tuple[int, int]],
        task_budget_recovered: bool,
        task_budget_remaining: int | None,
        post_compact_context: dict | None,
        tool_use_summary: dict | None,
        reactive_compact_context: dict | None,
        turn_wiring: dict[str, Any],
    ) -> dict[str, Any]:
        task_budget_used = (
            max(0, task_budget - task_budget_remaining)
            if isinstance(task_budget, int) and isinstance(task_budget_remaining, int)
            else None
        )
        return {
            "session_id": session_id,
            "task_id": task_id,
            "turn": turn,
            "resumed": resumed,
            "plan_mode": dialog_state.plan_mode,
            "task_budget": task_budget,
            "task_budget_source": task_budget_source,
            "task_budget_hint_positions": [list(position) for position in task_budget_hint_positions],
            "task_budget_recovered": task_budget_recovered,
            "task_budget_remaining": task_budget_remaining,
            "task_budget_used": task_budget_used,
            "post_compact_context": post_compact_context,
            "tool_use_summary": tool_use_summary,
            "reactive_compact_context": reactive_compact_context,
            "turn_wiring": turn_wiring,
        }

    def _build_turn_wiring_summary(
        self,
        *,
        dialog_state: DialogState,
        system_reminders: list[str],
        model_visible_tool_names: set[str] | None,
    ) -> dict[str, Any]:
        registered_tool_names = {tool["name"] for tool in self.tool_registry.definitions()}
        tool_pool = build_tool_pool_context(
            model_visible_tool_names=model_visible_tool_names
        )
        visible_tool_names = sorted(
            tool["name"]
            for tool in tool_pool.visible_definitions(self.tool_registry.definitions())
        )
        model_invocable_skill_names = sorted(
            skill.name for skill in self.skill_registry.list_model_invocable()
        )
        active_skill_ids = [
            skill_name
            for skill_name in dialog_state.active_skill_ids
            if isinstance(skill_name, str)
        ]
        model_visible_active_skill_ids = [
            skill_name
            for skill_name in active_skill_ids
            if _is_model_visible_skill_hint(
                dialog_state.active_skill_hints.get(skill_name, {})
            )
        ]
        issues = _build_turn_wiring_issues(
            active_skill_ids=active_skill_ids,
            active_skill_hints=dialog_state.active_skill_hints,
            registered_tool_names=registered_tool_names,
            model_visible_tool_names=model_visible_tool_names,
        )
        if issues:
            logger.warning("Runtime turn wiring issues detected: %s", issues)
        return {
            "tool_pool_mode": "explicit" if model_visible_tool_names is not None else "default",
            "model_visible_tool_count": len(visible_tool_names),
            "model_visible_tool_names": visible_tool_names,
            "model_invocable_skill_count": len(model_invocable_skill_names),
            "model_invocable_skill_names": model_invocable_skill_names,
            "system_reminder_count": len(system_reminders),
            "active_skill_ids": active_skill_ids,
            "model_visible_active_skill_ids": model_visible_active_skill_ids,
            "issues": issues,
        }

    def _build_task_payload(
        self,
        *,
        base_payload: dict | None,
        query: str,
        metadata: dict | None,
        query_source: str | None,
        task_budget: int | None,
        task_budget_source: str | None,
        task_budget_hint_positions: list[tuple[int, int]],
        task_budget_recovered: bool,
        task_budget_remaining: int | None,
        post_compact_context: dict | None,
        tool_use_summary: dict | None,
        reactive_compact_context: dict | None,
    ) -> dict[str, Any]:
        task_budget_used = (
            max(0, task_budget - task_budget_remaining)
            if isinstance(task_budget, int) and isinstance(task_budget_remaining, int)
            else None
        )
        return {
            **(base_payload or {}),
            "query": query,
            "metadata": _metadata_without_resolved_attachments(metadata),
            "query_source": query_source,
            "task_budget": task_budget,
            "task_budget_source": task_budget_source,
            "task_budget_hint_positions": task_budget_hint_positions,
            "task_budget_recovered": task_budget_recovered,
            "task_budget_remaining": task_budget_remaining,
            "task_budget_used": task_budget_used,
            "post_compact_context": post_compact_context,
            "tool_use_summary": tool_use_summary,
            "reactive_compact_context": reactive_compact_context,
        }

    def _build_completed_task_payload(
        self,
        *,
        base_payload: dict,
        query: str,
        metadata: dict | None,
        query_source: str | None,
        task_budget: int | None,
        task_budget_source: str | None,
        task_budget_hint_positions: list[tuple[int, int]],
        task_budget_recovered: bool,
        task_budget_remaining: int | None,
        task_budget_used: int | None,
        assistant_text: str,
        transition: object,
        budget_completion: object,
        post_compact_context: dict | None,
        reactive_compact_context: object,
        tool_use_summary: object,
        tool_result_context: object,
        provider_payload_diagnostics: object,
    ) -> dict[str, Any]:
        return {
            **base_payload,
            "query": query,
            "metadata": _metadata_without_resolved_attachments(metadata),
            "query_source": query_source,
            "task_budget": task_budget,
            "task_budget_source": task_budget_source,
            "task_budget_hint_positions": task_budget_hint_positions,
            "task_budget_recovered": task_budget_recovered,
            "task_budget_remaining": task_budget_remaining,
            "task_budget_used": task_budget_used,
            "assistant_text": assistant_text,
            "transition": transition,
            "budget_completion": budget_completion,
            "post_compact_context": post_compact_context,
            "reactive_compact_context": reactive_compact_context,
            "tool_use_summary": tool_use_summary,
            "tool_result_context": tool_result_context,
            "provider_payload_diagnostics": provider_payload_diagnostics,
        }

    def _build_runtime_state_payload(
        self,
        *,
        turn: int,
        dialog_state: DialogState,
        active_task_id: str,
        stop_reason: str,
        transition: object,
        budget_completion: object,
        post_compact_context: dict | None,
        reactive_compact_context: object,
        tool_use_summary: object,
        tool_result_context: object,
        provider_payload_diagnostics: object,
        tool_result_budget_state: object,
        task_budget: int | None,
        task_budget_source: str | None,
        task_budget_hint_positions: list[tuple[int, int]],
        task_budget_recovered: bool,
        task_budget_remaining: int | None,
        task_budget_used: int | None,
    ) -> dict[str, Any]:
        return {
            "turn": turn,
            "dialog_state": dialog_state.to_payload(),
            "active_task_id": active_task_id,
            "last_stop_reason": stop_reason,
            "last_transition": transition,
            "last_budget_completion": budget_completion,
            "last_post_compact_context": post_compact_context,
            "last_reactive_compact_context": reactive_compact_context,
            "last_tool_use_summary": tool_use_summary,
            "last_tool_result_context": tool_result_context,
            "last_provider_payload_diagnostics": provider_payload_diagnostics,
            "tool_result_budget_state": tool_result_budget_state,
            "task_budget": task_budget,
            "task_budget_source": task_budget_source,
            "task_budget_hint_positions": [list(position) for position in task_budget_hint_positions],
            "task_budget_recovered": task_budget_recovered,
            "task_budget_remaining": task_budget_remaining,
            "task_budget_used": task_budget_used,
        }

    def _refresh_post_compact_context(
        self,
        *,
        compactions: SessionCompactionRepository,
        messages: MessageRepository,
        session_id: str,
    ) -> dict | None:
        latest_compaction = compactions.get_latest_compaction(session_id)
        preserved_messages = (
            [
                {
                    "id": message.id,
                    "role": message.role,
                    "payload": message.payload_json,
                }
                for message in messages.list_message_range(
                    session_id,
                    start_id=latest_compaction.preserved_from_message_id,
                    end_id=latest_compaction.preserved_to_message_id,
                )
            ]
            if latest_compaction is not None
            else []
        )
        return _build_post_compact_context(
            latest_compaction=latest_compaction,
            preserved_messages=preserved_messages,
        )


def _persist_assistant_chunks(
    *,
    messages: MessageRepository,
    session_id: str,
    assistant_chunks: list[str],
    stop_reason: str,
    turn_id: str,
    turn_sequence: list[int],
    thinking_chunks: list[str],
    terminal_payload: dict[str, Any],
) -> str:
    if not assistant_chunks:
        return ""

    if (
        stop_reason == "model_error"
        and len(assistant_chunks) > 1
        and assistant_chunks[-1].startswith("Model error: ")
    ):
        partial_text = "".join(assistant_chunks[:-1])
        error_text = assistant_chunks[-1]
        if partial_text:
            messages.add_message(
                session_id=session_id,
                role="assistant",
                payload=_with_turn_replay_metadata(
                    {
                        "text": partial_text,
                        **_build_assistant_replay_extras(
                            thinking_chunks=thinking_chunks,
                            stop_reason=None,
                            terminal_payload={},
                        ),
                    },
                    turn_id=turn_id,
                    sequence=_next_turn_sequence(turn_sequence),
                    kind="message",
                    event_type="assistant",
                ),
            )
        messages.add_message(
            session_id=session_id,
            role="assistant",
            payload=_with_turn_replay_metadata(
                {
                    "text": error_text,
                    **_build_assistant_replay_extras(
                        thinking_chunks=[],
                        stop_reason=stop_reason,
                        terminal_payload=terminal_payload,
                    ),
                },
                turn_id=turn_id,
                sequence=_next_turn_sequence(turn_sequence),
                kind="message",
                event_type="assistant",
            ),
        )
        return f"{partial_text}{error_text}"

    assistant_text = "".join(assistant_chunks)
    if assistant_text:
        messages.add_message(
            session_id=session_id,
            role="assistant",
            payload=_with_turn_replay_metadata(
                {
                    "text": assistant_text,
                    **_build_assistant_replay_extras(
                        thinking_chunks=thinking_chunks,
                        stop_reason=stop_reason,
                        terminal_payload=terminal_payload,
                    ),
                },
                turn_id=turn_id,
                sequence=_next_turn_sequence(turn_sequence),
                kind="message",
                event_type="assistant",
            ),
        )
    return assistant_text


def _with_turn_replay_metadata(
    payload: dict[str, Any],
    *,
    turn_id: str,
    sequence: int,
    kind: str,
    event_type: str,
) -> dict[str, Any]:
    return {
        **payload,
        "turn_id": turn_id,
        "sequence": sequence,
        "kind": kind,
        "event_type": event_type,
    }


def _next_turn_sequence(counter: list[int]) -> int:
    counter[0] += 1
    return counter[0]


def _build_assistant_replay_extras(
    *,
    thinking_chunks: list[str],
    stop_reason: str | None,
    terminal_payload: dict[str, Any],
) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    if thinking_chunks:
        extras["thinking"] = list(thinking_chunks)
    if stop_reason:
        extras["stop_reason"] = stop_reason
    notice = _normalize_optional_text(_build_turn_stop_notice(terminal_payload))
    if notice:
        extras["notice"] = notice
    return extras


def _build_turn_stop_notice(payload: dict[str, Any]) -> str | None:
    stop_reason = _normalize_optional_text(payload.get("stop_reason"))
    if stop_reason == "max_turns":
        turn_count = payload.get("turn_count")
        if isinstance(turn_count, int):
            return f"已达到最大轮次（{turn_count}）"
        return "已达到最大轮次"
    if stop_reason == "max_tool_calls":
        return "已达到工具调用次数上限"
    if stop_reason == "duplicate_tool_calls":
        return "模型重复请求相同工具调用，已停止本轮"
    return None


def _expand_slash_skill_input(
    input_text: str,
    *,
    skill_registry: SkillRegistry,
    session_id: str,
    skip_slash_commands: bool,
):
    if skip_slash_commands or not isinstance(input_text, str):
        return input_text
    normalized = input_text.strip()
    if not normalized.startswith("/"):
        return input_text
    command_line = normalized[1:]
    if not command_line:
        return input_text
    skill_name, _, raw_args = command_line.partition(" ")
    try:
        skill = skill_registry.get(skill_name)
    except KeyError:
        return input_text
    if not skill.user_invocable:
        return input_text
    rendered_content = render_skill_content(
        skill,
        session_id=session_id,
        args=raw_args or None,
    )
    return [
        {
            "type": "text",
            "text": (
                f"<command-message>{skill_name}</command-message>\n"
                f"<command-name>/{skill_name}</command-name>\n"
                f"<command-args>{raw_args}</command-args>"
            ),
        },
        {
            "type": "text",
            "text": rendered_content,
        },
    ]

def _extract_plan_command(input_text: str) -> PlanCommand:
    normalized = input_text.strip()
    if not normalized.startswith("/plan"):
        return PlanCommand(entered=False, remaining_text=input_text)
    remainder = normalized[len("/plan") :].strip()
    return PlanCommand(entered=True, remaining_text=remainder)


def _normalize_turn_value(value: object) -> int:
    try:
        turn = int(value)
    except (TypeError, ValueError):
        return 0
    return max(turn, 0)


def _build_post_compact_context(
    *,
    latest_compaction,
    preserved_messages: list[dict],
) -> dict | None:
    if latest_compaction is None:
        return None

    preserved_tail: list[dict[str, object]] = []
    for message in preserved_messages:
        if not isinstance(message, dict):
            continue
        role = _normalize_optional_text(message.get("role")) or "unknown"
        message_id = _normalize_optional_int(message.get("id"))
        text = _normalize_optional_text(render_collapsed_message_text(message)) or ""
        if not text:
            continue
        preserved_tail.append(
            {
                "id": message_id,
                "role": role,
                "text": text,
            }
        )

    return {
        "compaction_id": latest_compaction.compaction_id,
        "boundary_message_id": latest_compaction.boundary_message_id,
        "summary_text": latest_compaction.summary_text,
        "preserved_from_message_id": latest_compaction.preserved_from_message_id,
        "preserved_to_message_id": latest_compaction.preserved_to_message_id,
        "reason": latest_compaction.reason,
        "preserved_tail": preserved_tail,
    }


def _filter_history_after_compaction(
    history: list[dict],
    *,
    post_compact_context: dict | None,
) -> list[dict]:
    if not isinstance(post_compact_context, dict):
        return history
    boundary_message_id = post_compact_context.get("boundary_message_id")
    if not isinstance(boundary_message_id, int) or boundary_message_id <= 0:
        return history
    filtered: list[dict] = []
    for message in history:
        message_id = message.get("message_id") if isinstance(message, dict) else None
        if isinstance(message_id, int) and message_id <= boundary_message_id:
            # 已被 compaction 覆盖的历史不再直接重放给模型；需要精确原文时，
            # 模型应通过 read_session_messages 按边界读取数据库中的原始消息。
            continue
        filtered.append(message)
    return filtered


def _runtime_stream_kwargs(*, runtime_engine: RuntimeEngine, **kwargs) -> dict:
    try:
        parameters = inspect.signature(runtime_engine.stream).parameters
    except (TypeError, ValueError):
        return kwargs
    if "metadata" not in parameters:
        kwargs.pop("metadata", None)
    if "model_visible_tool_names" not in parameters:
        kwargs.pop("model_visible_tool_names", None)
    if "task_budget" not in parameters:
        kwargs.pop("task_budget", None)
    if "task_budget_source" not in parameters:
        kwargs.pop("task_budget_source", None)
    if "task_budget_hint_positions" not in parameters:
        kwargs.pop("task_budget_hint_positions", None)
    if "task_budget_remaining" not in parameters:
        kwargs.pop("task_budget_remaining", None)
    if "task_budget_recovered" not in parameters:
        kwargs.pop("task_budget_recovered", None)
    if "tool_result_budget_state" not in parameters:
        kwargs.pop("tool_result_budget_state", None)
    if "enable_outer_compaction" not in parameters:
        kwargs.pop("enable_outer_compaction", None)
    return kwargs


def _extract_active_skill_hint(result: dict) -> dict:
    runtime_hints = result.get("runtime_hints", {}) if isinstance(result, dict) else {}
    if not isinstance(runtime_hints, dict):
        runtime_hints = {}

    hint = {
        "display_name": _normalize_optional_text(result.get("display_name")),
        "when_to_use": _normalize_optional_text(result.get("when_to_use")),
        "allowed_tools": _normalize_skill_allowed_tools(result.get("allowed_tools", [])),
        "preferred_model": _normalize_optional_text(
            runtime_hints.get("preferred_model", result.get("model"))
        ),
        "disable_model_invocation": _normalize_bool_hint(
            runtime_hints.get(
                "disable_model_invocation",
                result.get("disable_model_invocation", False),
            ),
            default=False,
        ),
        "execution_context": _normalize_optional_text(runtime_hints.get("execution_context")),
        "agent": _normalize_optional_text(runtime_hints.get("agent")),
        "effort": runtime_hints.get("effort"),
        "argument_hint": _normalize_optional_text(
            runtime_hints.get("argument_hint", result.get("argument_hint"))
        ),
        "user_invocable": _normalize_bool_hint(result.get("user_invocable", True), default=True),
    }
    when_not_to_use = _normalize_optional_text(result.get("when_not_to_use"))
    if when_not_to_use:
        hint["when_not_to_use"] = when_not_to_use
    return hint


def _normalize_bool_hint(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return default


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_optional_int(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return max(normalized, 0)


def _normalize_hint_positions(value: object) -> list[tuple[int, int]]:
    if not isinstance(value, list):
        return []
    normalized: list[tuple[int, int]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            start = int(item[0])
            end = int(item[1])
        except (TypeError, ValueError):
            continue
        normalized.append((start, end))
    return normalized


def _normalize_context_values(values: object) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            continue
        if value is None:
            continue
        normalized_key = key.strip()
        if not normalized_key:
            continue
        if isinstance(value, (dict, list)):
            normalized[normalized_key] = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            continue
        normalized[normalized_key] = str(value)
    return normalized


def _normalize_skill_allowed_tools(value: object) -> list[str]:
    if isinstance(value, str):
        return [item for item in value.split() if item]
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        tool_name = item.strip()
        if tool_name:
            normalized.append(tool_name)
    return normalized


def _normalize_active_skill_ids(active_skill_ids: list[object]) -> list[str]:
    normalized: list[str] = []
    for skill_name in active_skill_ids:
        if not isinstance(skill_name, str):
            continue
        stripped = skill_name.strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def _normalize_active_skill_hints(active_skill_hints: dict[str, dict]) -> dict[str, dict]:
    normalized: dict[str, dict] = {}
    for skill_name, hint in active_skill_hints.items():
        if not isinstance(skill_name, str):
            continue
        normalized_skill_name = skill_name.strip()
        if not normalized_skill_name:
            continue
        if not isinstance(hint, dict):
            continue
        normalized_hint = {
            "display_name": _normalize_optional_text(hint.get("display_name")),
            "when_to_use": _normalize_optional_text(hint.get("when_to_use")),
            "allowed_tools": _normalize_skill_allowed_tools(hint.get("allowed_tools", [])),
            "preferred_model": _normalize_optional_text(hint.get("preferred_model")),
            "disable_model_invocation": _normalize_bool_hint(
                hint.get("disable_model_invocation", False),
                default=False,
            ),
            "execution_context": _normalize_optional_text(hint.get("execution_context")),
            "agent": _normalize_optional_text(hint.get("agent")),
            "effort": hint.get("effort"),
            "argument_hint": _normalize_optional_text(hint.get("argument_hint")),
            "user_invocable": _normalize_bool_hint(hint.get("user_invocable", True), default=True),
        }
        when_not_to_use = _normalize_optional_text(hint.get("when_not_to_use"))
        if when_not_to_use:
            normalized_hint["when_not_to_use"] = when_not_to_use
        normalized[normalized_skill_name] = normalized_hint
    return normalized


def _extract_skill_match_keywords(when_to_use: str | None) -> list[str]:
    text = _normalize_optional_text(when_to_use)
    if text is None:
        return []
    normalized = text
    for marker in (
        "当用户提到",
        "当用户输入",
        "当用户",
        "Use when users mention",
        "Use when user mentions",
        "Use when",
    ):
        normalized = normalized.replace(marker, "")
    for marker in ("时使用", "时", "时触发", "时调用"):
        normalized = normalized.replace(marker, "")
    keywords = re.split(r"[、，,；;或|/\s]+", normalized)
    return [
        item.strip("：:()（）[]【】\"' ")
        for item in keywords
        if item.strip("：:()（）[]【】\"' ")
    ]

def _normalize_pending_tool_approval_state(dialog_state: DialogState) -> None:
    dialog_state.pending_user_question = _normalize_optional_text(
        dialog_state.pending_user_question
    )
    pending_params = dialog_state.pending_params if isinstance(dialog_state.pending_params, dict) else {}
    tool_name = _normalize_optional_text(pending_params.get("tool_name"))
    tool_call_id = _normalize_optional_text(pending_params.get("tool_call_id"))
    arguments = pending_params.get("arguments")
    normalized_arguments = arguments if isinstance(arguments, dict) else None

    if dialog_state.status != "awaiting_tool_approval" or tool_name is None:
        if dialog_state.status == "awaiting_tool_approval":
            dialog_state.status = "idle"
        dialog_state.pending_params = {}
        if dialog_state.status == "idle":
            dialog_state.pending_user_question = None
        return

    normalized_params: dict[str, Any] = {"tool_name": tool_name}
    if normalized_arguments:
        normalized_params["arguments"] = normalized_arguments
    if tool_call_id is not None:
        normalized_params["tool_call_id"] = tool_call_id
    dialog_state.pending_params = normalized_params


def _build_user_message_payload(
    *,
    input_text: str,
    expanded_input_content: object,
    input_mode: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if input_mode == "task-notification":
        payload = (
            metadata.get("queued_command_payload")
            if isinstance(metadata, dict) and isinstance(metadata.get("queued_command_payload"), dict)
            else None
        )
        return {
            "text": input_text,
            "kind": "notification",
            **({"payload": payload} if isinstance(payload, dict) else {}),
        }
    if input_mode == "frontend-tool-result":
        payload = (
            metadata.get("queued_command_payload")
            if isinstance(metadata, dict) and isinstance(metadata.get("queued_command_payload"), dict)
            else None
        )
        return {
            "text": input_text,
            "kind": "frontend_tool_result",
            **({"payload": payload} if isinstance(payload, dict) else {}),
        }
    if isinstance(expanded_input_content, list):
        return {"text": input_text, "content": expanded_input_content}
    return {"text": input_text}


def _metadata_without_resolved_attachments(metadata: object) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if key != "resolved_attachments"
    }


def _build_active_skill_reminder(dialog_state: DialogState) -> str | None:
    if not dialog_state.active_skill_hints:
        return None

    lines = [
        "Previously loaded skills are available as context for this session.",
        "Use a loaded skill only when the latest user request continues or relates to that workflow; if the latest request is unrelated, choose the appropriate skill or tool for the new request.",
    ]
    for skill_name in dialog_state.active_skill_ids:
        hint = dialog_state.active_skill_hints.get(skill_name)
        if not isinstance(hint, dict):
            lines.append(f"- {skill_name}")
            continue
        if not _is_model_visible_skill_hint(hint):
            continue
        label = (
            f"{skill_name} ({hint['display_name']})"
            if hint.get("display_name")
            else skill_name
        )
        lines.append(f"- {label}")
        if hint.get("when_to_use"):
            lines.append(f"  when to use: {hint['when_to_use']}")
        if hint.get("when_not_to_use"):
            lines.append(f"  when not to use: {hint['when_not_to_use']}")
        if hint.get("allowed_tools"):
            lines.append(f"  allowed tools: {', '.join(str(item) for item in hint['allowed_tools'])}")
        if hint.get("preferred_model"):
            lines.append(f"  preferred model: {hint['preferred_model']}")
        if hint.get("disable_model_invocation"):
            lines.append("  disable model invocation: true")
        if hint.get("execution_context"):
            lines.append(f"  execution context: {hint['execution_context']}")
        if hint.get("agent"):
            lines.append(f"  agent: {hint['agent']}")
        if hint.get("effort"):
            lines.append(f"  effort: {hint['effort']}")
        if hint.get("argument_hint"):
            lines.append(f"  argument hint: {hint['argument_hint']}")
    return wrap_in_system_reminder("\n".join(lines))


def _build_task_notification_reminder(tool_registry: ToolRegistry) -> str | None:
    tool_names = {tool["name"] for tool in tool_registry.definitions()}
    required_tools = {"spawn_agent", "send_message", "stop_task"}
    if not required_tools.issubset(tool_names):
        return None

    return wrap_in_system_reminder(
        "\n".join(
            [
                "Background task and worker results may appear as <task-notification> XML blocks in user-role messages.",
                "Treat <task-notification> content as system-generated coordination state, not as a direct user request.",
                "Distinguish these messages by the <task-notification> opening tag and synthesize any useful result back into your response to the user.",
                "If <result> includes payload.final_output, treat that final_output as the authoritative worker result.",
                "Do not narrate task dispatch mechanics, waiting state, or worker coordination when a completed result is already present.",
                "Frontend-executed tool results may appear as <frontend-tool-result> XML blocks in user-role messages.",
                "For <frontend-tool-result>, synthesize the result into a direct answer to the user's previous question; do not regenerate the frontend protocol block unless the result explicitly asks for repair.",
            ]
        )
    )


def _build_turn_wiring_issues(
    *,
    active_skill_ids: list[str],
    active_skill_hints: dict[str, dict],
    registered_tool_names: set[str],
    model_visible_tool_names: set[str] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if model_visible_tool_names is not None:
        missing_visible_tools = sorted(model_visible_tool_names - registered_tool_names)
        if missing_visible_tools:
            issues.append(
                {
                    "kind": "model_visible_tool_missing",
                    "tool_names": missing_visible_tools,
                }
            )
    for skill_name in active_skill_ids:
        hint = active_skill_hints.get(skill_name)
        if not isinstance(hint, dict):
            continue
        allowed_tools = hint.get("allowed_tools")
        if not isinstance(allowed_tools, list):
            continue
        missing_allowed_tools = sorted(
            tool_name
            for tool_name in allowed_tools
            if isinstance(tool_name, str) and tool_name not in registered_tool_names
        )
        if missing_allowed_tools:
            issues.append(
                {
                    "kind": "active_skill_allowed_tool_missing",
                    "skill": skill_name,
                    "tool_names": missing_allowed_tools,
                }
            )
    return issues


def _backfill_active_skill_hints(active_skill_ids: list[str], skill_registry: SkillRegistry) -> dict:
    hints: dict[str, dict] = {}
    for skill_name in active_skill_ids:
        try:
            skill = skill_registry.get(skill_name)
        except KeyError:
            continue
        hint = {
            "display_name": skill.display_name,
            "when_to_use": skill.when_to_use,
            "allowed_tools": list(skill.allowed_tools),
            "preferred_model": skill.model,
            "disable_model_invocation": skill.disable_model_invocation,
            "execution_context": skill.execution_context,
            "agent": skill.agent,
            "effort": skill.effort,
            "argument_hint": skill.argument_hint,
            "user_invocable": skill.user_invocable,
        }
        if skill.when_not_to_use:
            hint["when_not_to_use"] = skill.when_not_to_use
        hints[skill_name] = hint
    return hints


def _build_pending_tool_approval_reminder(
    dialog_state: DialogState,
    *,
    approval_decision: str | None = None,
) -> str | None:
    if dialog_state.status != "awaiting_tool_approval":
        return None

    tool_name = dialog_state.pending_params.get("tool_name")
    if not tool_name:
        return None

    lines = [
        "A tool approval decision is pending from the previous turn.",
        f"pending tool: {tool_name}",
    ]
    tool_call_id = dialog_state.pending_params.get("tool_call_id")
    if tool_call_id:
        lines.append(f"call id: {tool_call_id}")
    arguments = dialog_state.pending_params.get("arguments")
    if arguments:
        lines.append(f"arguments: {json.dumps(arguments, ensure_ascii=False, sort_keys=True)}")
    if dialog_state.pending_user_question:
        lines.append(f"message: {dialog_state.pending_user_question}")
    if approval_decision == "approved":
        lines.append("latest user decision: approved")
        lines.append("The user approved the pending tool call.")
    elif approval_decision == "denied":
        lines.append("latest user decision: denied")
        lines.append("The user denied the pending tool call.")
    return wrap_in_system_reminder("\n".join(lines))


def _build_pending_plan_approval_reminder(
    dialog_state: DialogState,
    *,
    approval_decision: str | None = None,
    approved_plan: str | None = None,
    rejected_plan: str | None = None,
) -> str | None:
    if approval_decision == "approved" and approved_plan:
        return wrap_in_system_reminder(
            "\n".join(
                [
                    "The user approved the pending plan and plan mode has now ended.",
                    "Approved plan:",
                    approved_plan,
                    "You may now proceed with implementation based on this plan.",
                ]
            )
        )
    if approval_decision == "denied" and rejected_plan:
        return wrap_in_system_reminder(
            "\n".join(
                [
                    "The user rejected the pending plan. Stay in plan mode and revise it.",
                    "Rejected plan:",
                    rejected_plan,
                ]
            )
        )
    if dialog_state.status != "awaiting_plan_approval":
        return None

    pending_plan = _normalize_optional_text(dialog_state.pending_params.get("plan"))
    lines = [
        "A plan approval decision is pending from the previous turn.",
        "pending action: exit plan mode",
    ]
    if pending_plan:
        lines.append("plan:")
        lines.append(pending_plan)
    if dialog_state.pending_user_question:
        lines.append(f"message: {dialog_state.pending_user_question}")
    return wrap_in_system_reminder("\n".join(lines))


def _render_recall_section(section: object) -> str:
    title = _normalize_optional_text(getattr(section, "title", None)) or "Recall"
    content = _normalize_optional_text(getattr(section, "content", None)) or ""
    if title.startswith("Compacted History"):
        return "\n".join(
            [
                f"# {title}",
                "This session is being continued with a compacted summary of earlier conversation context. Recent transcript messages, if present, take precedence over this summary.",
                "",
                content,
            ]
        ).strip()
    if title == "Session Memory":
        return "\n".join(
            [
                "# Session Memory",
                "This is durable session memory recovered from the database. Use it as background context; the latest user message and preserved transcript take precedence.",
                "",
                content,
            ]
        ).strip()
    return f"# {title}\n{content}".strip()


def _classify_pending_tool_approval_decision(
    input_text: str,
    *,
    metadata: dict[str, Any] | None = None,
    pending_tool_call_id: str | None = None,
) -> str | None:
    # 前端确认框应通过结构化 metadata 回传决策，避免依赖“批准/拒绝”文本猜测。
    # 文本识别仍作为 CLI/手工输入兜底；如果 metadata 带 call id，则必须匹配当前 pending 调用。
    structured_decision = _classify_structured_tool_approval_decision(
        metadata,
        pending_tool_call_id=pending_tool_call_id,
    )
    if structured_decision is not None:
        return structured_decision

    normalized = input_text.strip().lower()
    if not normalized:
        return None

    denied_markers = [
        "拒绝",
        "不批准",
        "不要执行",
        "别执行",
        "不要批准",
        "不要同意",
        "不同意",
        "deny",
        "denied",
        "do not run",
        "don't run",
    ]
    approved_markers = [
        "批准",
        "同意",
        "可以执行",
        "继续执行",
        "go ahead",
        "approve",
        "approved",
    ]

    if any(marker in input_text for marker in denied_markers) or any(
        marker in normalized for marker in denied_markers
    ):
        return "denied"
    if any(marker in input_text for marker in approved_markers) or any(
        marker in normalized for marker in approved_markers
    ):
        return "approved"
    return None


def _classify_structured_tool_approval_decision(
    metadata: dict[str, Any] | None,
    *,
    pending_tool_call_id: str | None,
) -> str | None:
    if not isinstance(metadata, dict):
        return None
    approval = metadata.get("tool_approval")
    if not isinstance(approval, dict):
        return None
    approval_call_id = _normalize_optional_text(approval.get("tool_call_id"))
    if approval_call_id is not None and approval_call_id != pending_tool_call_id:
        return None
    decision = _normalize_optional_text(approval.get("decision"))
    if decision is None:
        return None
    normalized = decision.lower()
    if normalized in {"approved", "approve", "allow", "allowed"}:
        return "approved"
    if normalized in {"denied", "deny", "reject", "rejected"}:
        return "denied"
    return None


def _build_can_use_tool(
    tool_policy,
    *,
    previous_dialog_state: DialogState,
    approval_decision: str | None,
    active_skill_hints: dict[str, dict] | None = None,
):
    return build_can_use_tool(
        tool_policy,
        previous_dialog_state=previous_dialog_state,
        approval_decision=approval_decision,
        active_skill_hints=active_skill_hints,
    )


def _prune_hidden_active_skills(dialog_state: DialogState) -> None:
    if not dialog_state.active_skill_ids and not dialog_state.active_skill_hints:
        return

    had_explicit_active_ids = bool(dialog_state.active_skill_ids)
    visible_ids: list[str] = []
    visible_hints: dict[str, dict] = {}

    for skill_name in dialog_state.active_skill_ids:
        hint = dialog_state.active_skill_hints.get(skill_name)
        if isinstance(hint, dict) and not _is_model_visible_skill_hint(hint):
            continue
        visible_ids.append(skill_name)
        if isinstance(hint, dict):
            visible_hints[skill_name] = hint

    for skill_name, hint in dialog_state.active_skill_hints.items():
        if skill_name in visible_hints:
            continue
        if had_explicit_active_ids:
            continue
        if isinstance(hint, dict) and not _is_model_visible_skill_hint(hint):
            continue
        if skill_name not in visible_ids:
            visible_ids.append(skill_name)
        if isinstance(hint, dict):
            visible_hints[skill_name] = hint

    dialog_state.active_skill_ids = visible_ids
    dialog_state.active_skill_hints = visible_hints


def _is_model_visible_skill_hint(hint: dict) -> bool:
    return bool(hint.get("user_invocable", True)) and not bool(
        hint.get("disable_model_invocation", False)
    )
