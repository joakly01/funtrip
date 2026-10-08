from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import re

from audit_agent_runtime.context.query_models import QueryTask, TaskNotification
from audit_agent_runtime.utils.json_utils import safe_json_dumps


def build_task_notification(
    task: QueryTask,
    *,
    summary: str | None = None,
    payload: dict | None = None,
) -> TaskNotification:
    normalized_payload = _normalize_payload(task.payload if payload is None else payload)
    return TaskNotification(
        task_id=task.task_id,
        agent_id=task.agent_id,
        status=task.status,
        summary=summary,
        payload=normalized_payload,
        created_at=datetime.now(timezone.utc),
    )


def notification_to_payload(notification: TaskNotification) -> dict:
    return {
        "task_id": notification.task_id,
        "agent_id": notification.agent_id,
        "status": notification.status,
        "summary": notification.summary,
        "payload": notification.payload,
        "created_at": None
        if notification.created_at is None
        else notification.created_at.isoformat(),
    }


def compact_notification_payload_for_main_thread(payload: dict) -> dict:
    normalized = payload if isinstance(payload, dict) else {}
    status = _optional_text(normalized.get("status"))
    normalized_result = _normalize_payload(normalized.get("payload"))
    final_output = _normalize_final_output(normalized_result.get("final_output"))

    return {
        "task_id": normalized.get("task_id"),
        "agent_id": normalized.get("agent_id"),
        "status": normalized.get("status"),
        "summary": _normalize_notification_summary(
            normalized.get("summary"),
            final_output=final_output,
        ),
        # 主线程只需要能继续对话所必需的结果，避免把完整 subagent transcript 再塞回主链。
        "payload": _compact_main_thread_result(
            normalized_result=normalized_result,
            status=status,
            final_output=final_output,
        ),
        "created_at": normalized.get("created_at"),
    }


def notification_to_xml_payload(payload: dict) -> str:
    parts = [
        "<task-notification>",
        f"<task-id>{escape(_optional_text(payload.get('task_id')), quote=True)}</task-id>",
        f"<agent-id>{escape(_optional_text(payload.get('agent_id')), quote=True)}</agent-id>",
        f"<status>{escape(_optional_text(payload.get('status')), quote=True)}</status>",
    ]
    summary = payload.get("summary")
    if summary:
        parts.append(
            f"<summary>{escape(_render_structured_text(summary), quote=True)}</summary>"
        )
    has_result = "payload" in payload
    result = payload.get("payload")
    if has_result:
        serialized_result = (
            ""
            if result is None
            else
            safe_json_dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if isinstance(result, (dict, list))
            else str(result)
        )
        parts.append(f"<result>{escape(serialized_result, quote=True)}</result>")
    parts.append("</task-notification>")
    return "\n".join(parts)


def render_send_message_payload(message: object) -> str:
    return _render_structured_text(message)


def frontend_tool_result_to_xml_payload(payload: dict) -> str:
    normalized = payload if isinstance(payload, dict) else {}
    result = normalized.get("result")
    serialized_result = (
        safe_json_dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if isinstance(result, (dict, list))
        else _render_structured_text(result)
    )
    parts = [
        "<frontend-tool-result>",
        f"<tool-name>{escape(_optional_text(normalized.get('tool_name')), quote=True)}</tool-name>",
        f"<block-id>{escape(_optional_text(normalized.get('block_id')), quote=True)}</block-id>",
        f"<title>{escape(_optional_text(normalized.get('title')), quote=True)}</title>",
    ]
    instruction = _optional_text(normalized.get("instruction")).strip()
    if instruction:
        parts.append(f"<instruction>{escape(instruction, quote=True)}</instruction>")
    parts.append(f"<result>{escape(serialized_result, quote=True)}</result>")
    parts.append("</frontend-tool-result>")
    return "\n".join(parts)


def render_notification_transcript_text(payload: dict) -> str:
    # send_message 是直接面向主线程的自然语言补充，不需要再包成 task-notification XML。
    if (
        "message" in payload
        and "status" not in payload
        and "agent_id" not in payload
        and "summary" not in payload
        and "payload" not in payload
    ):
        return render_send_message_payload(payload.get("message"))
    return notification_to_xml_payload(payload)


def _optional_text(value: object) -> str:
    return "" if value is None else str(value)


def _render_structured_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return safe_json_dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return str(value)


def _normalize_payload(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _normalize_notification_summary(value: object, *, final_output: str) -> str | None:
    rendered = _render_structured_text(value).strip()
    if rendered:
        rendered = _normalize_final_output(rendered)
    return rendered or final_output or None


def _normalize_final_output(value: object) -> str:
    text = _render_structured_text(value).strip()
    if not text:
        return ""

    cleaned_lines: list[str] = []
    saw_header = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if line == "已按任务单完成查询，结果如下：":
            if saw_header:
                continue
            saw_header = True
            cleaned_lines.append(line)
            continue
        if line.startswith(("正在执行", "让我", "我将开始分析", "我将调用")):
            continue
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    non_header_lines = [
        line for line in cleaned_lines
        if line and line != "已按任务单完成查询，结果如下："
    ]
    if not non_header_lines:
        return _redact_internal_identifiers(text)
    return _redact_internal_identifiers("\n".join(cleaned_lines))


_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_TASK_ID_INLINE_PATTERN = re.compile(
    r"(?:任务\s*ID(?:为)?|task[_\s-]*id)\s*[:：=]?\s*`*[0-9a-zA-Z_-]{0,}`*(?:[，,。；;]?\s*)?",
    re.IGNORECASE,
)
_AGENT_ID_INLINE_PATTERN = re.compile(
    r"(?:agent[_\s-]*id)\s*[:：=]?\s*`*[0-9a-zA-Z_-]{0,}`*(?:[，,。；;]?\s*)?",
    re.IGNORECASE,
)


def _redact_internal_identifiers(text: str) -> str:
    if not text:
        return ""

    redacted_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if redacted_lines and redacted_lines[-1] != "":
                redacted_lines.append("")
            continue
        if _line_is_internal_identifier(line):
            continue
        line = _TASK_ID_INLINE_PATTERN.sub("", line)
        line = _AGENT_ID_INLINE_PATTERN.sub("", line)
        line = _UUID_PATTERN.sub("", line)
        line = re.sub(r"^[，,。；;:\-]+\s*", "", line)
        line = re.sub(r"\s+[，,。；;:\-]+\s*", " ", line)
        line = re.sub(r"\s{2,}", " ", line).strip()
        if not line:
            continue
        redacted_lines.append(line)

    while redacted_lines and redacted_lines[-1] == "":
        redacted_lines.pop()
    return "\n".join(redacted_lines).strip()


def _line_is_internal_identifier(line: str) -> bool:
    normalized = line.strip()
    lowered = normalized.lower()
    if re.fullmatch(
        r"(?:agent[_\s-]*id|task[_\s-]*id|任务\s*id(?:为)?|任务id(?:为)?)\s*[:：=]?\s*[0-9a-zA-Z_-]{3,}",
        lowered,
    ):
        return True
    if _UUID_PATTERN.fullmatch(normalized):
        return True
    return False


def _compact_main_thread_result(
    *,
    normalized_result: dict,
    status: str,
    final_output: str,
) -> dict[str, object]:
    compact_result: dict[str, object] = {}
    if final_output:
        compact_result["final_output"] = final_output

    for key in ("error", "skill_name", "execution_context"):
        value = normalized_result.get(key)
        if value is not None:
            compact_result[key] = value

    allowed_tools = normalized_result.get("allowed_tools")
    if isinstance(allowed_tools, list):
        compact_result["allowed_tools"] = allowed_tools

    if status == "failed":
        task_text = normalized_result.get("task")
        if task_text is not None:
            compact_result["task"] = task_text
    return compact_result
