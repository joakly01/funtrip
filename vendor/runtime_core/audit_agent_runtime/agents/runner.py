from __future__ import annotations

from collections.abc import Iterator

from audit_agent_runtime.context.query_models import QueryTask
from audit_agent_runtime.prompts.reminders import wrap_in_system_reminder
from audit_agent_runtime.runtime.events import RuntimeEvent
from audit_agent_runtime.runtime.loop import RuntimeLoop
from audit_agent_runtime.tasks.notifications import build_task_notification


class SubagentRunner:
    def __init__(
        self,
        runtime_loop: RuntimeLoop,
        base_system_reminders: list[str] | None = None,
    ):
        self.runtime_loop = runtime_loop
        self.base_system_reminders = list(base_system_reminders or [])

    def run(
        self,
        task: str,
        history: list[dict],
        parent_session_id: str,
        agent_name: str,
    ) -> Iterator[RuntimeEvent]:
        yield RuntimeEvent(
            type="subagent_start",
            payload={
                "agent_name": agent_name,
                "parent_session_id": parent_session_id,
                "task": task,
            },
        )
        assistant_chunks: list[str] = []
        for event in self.runtime_loop.run(
            query=task,
            history=history,
            session_id=f"{parent_session_id}:{agent_name}",
            system_reminders=self.base_system_reminders or None,
        ):
            if event.type == "assistant":
                assistant_chunks.append(_normalize_text(event.payload.get("text", "")))
                yield event
                continue
            if event.type != "done":
                yield event

        yield RuntimeEvent(
            type="subagent_end",
            payload={
                "agent_name": agent_name,
                "final_output": "".join(assistant_chunks),
            },
        )

    def run_task(
        self,
        task: QueryTask,
        history: list[dict],
    ) -> tuple[list[RuntimeEvent], dict]:
        task_query = _normalize_text(task.payload.get("task", ""))
        continuations = _normalize_continuations(task.payload.get("continuations", []))
        if continuations:
            task_query = (
                f"{task_query}\n\nContinuation:\n"
                + "\n".join(continuations)
            )
        events = list(
            self.runtime_loop.run(
                query=task_query,
                history=history,
                session_id=f"{task.session_id}:{task.agent_id}",
                agent_id=task.agent_id,
                task_id=task.task_id,
                system_reminders=_merge_system_reminders(
                    self.base_system_reminders,
                    _build_task_system_reminders(task.payload),
                ),
                can_use_tool=_build_task_can_use_tool(task.payload),
            )
        )
        events = [
            RuntimeEvent(
                type="subagent_start",
                payload={
                    "agent_name": task.agent_id,
                    "parent_session_id": task.session_id,
                    "task": task_query,
                },
            ),
            *[event for event in events if event.type != "done"],
            RuntimeEvent(
                type="subagent_end",
                payload={
                    "agent_name": task.agent_id,
                    "final_output": "".join(
                        _normalize_text(event.payload.get("text", ""))
                        for event in events
                        if event.type == "assistant"
                    ),
                },
            ),
        ]
        final_output = "".join(
            _normalize_text(event.payload.get("text", ""))
            for event in events
            if event.type == "assistant"
        )
        serialized_events = [
            {
                "type": event.type,
                "payload": event.payload,
            }
            for event in events
        ]
        notification = build_task_notification(
            task,
            summary=final_output or None,
            payload={
                "final_output": final_output,
                "subagent_events": serialized_events,
            },
        )
        return events, {
            "task_id": notification.task_id,
            "agent_id": notification.agent_id,
            "status": notification.status,
            "summary": notification.summary,
            "payload": notification.payload,
        }


def _normalize_text(value: object) -> str:
    return "" if value is None else str(value)


def _normalize_continuations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = _normalize_text(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _build_task_system_reminders(payload: object) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    skill_name = _normalize_text(payload.get("skill_name")).strip()
    execution_context = _normalize_text(payload.get("execution_context")).strip()
    allowed_tools = _normalize_allowed_tools(payload.get("allowed_tools"))
    if not skill_name and not allowed_tools and not execution_context:
        return None

    lines = [
        "This is a delegated subagent task.",
    ]
    if skill_name:
        lines.append(f"active skill: {skill_name}")
    if execution_context:
        lines.append(f"execution context: {execution_context}")
    if allowed_tools:
        lines.append(f"allowed tools: {', '.join(allowed_tools)}")
        lines.append("Use the allowed tools to complete the task before responding.")
    return [wrap_in_system_reminder("\n".join(lines))]


def _merge_system_reminders(
    base_reminders: list[str],
    task_reminders: list[str] | None,
) -> list[str] | None:
    merged = [*base_reminders, *(task_reminders or [])]
    return merged or None


def _build_task_can_use_tool(payload: object):
    if not isinstance(payload, dict):
        return None
    allowed_tools = set(_normalize_allowed_tools(payload.get("allowed_tools")))
    if not allowed_tools:
        return None

    def _can_use_tool(
        tool_name: str,
        _arguments: dict | None = None,
        _session_id: str | None = None,
    ) -> bool:
        if tool_name in allowed_tools:
            return True
        return False

    return _can_use_tool


def _normalize_allowed_tools(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = _normalize_text(item).strip()
        if text:
            normalized.append(text)
    return normalized
