from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(slots=True)
class CompactionResult:
    summary: str | None
    retained_messages: list[dict]


class CompactionEngine:
    def __init__(self, max_message_count: int = 20, keep_recent: int = 8):
        self.max_message_count = max_message_count
        self.keep_recent = keep_recent

    def compact(self, history: list[dict]) -> CompactionResult:
        if len(history) <= self.max_message_count:
            return CompactionResult(summary=None, retained_messages=history)

        if self.keep_recent <= 0:
            summarized_messages = history
            retained_messages = []
        else:
            retained_start = _safe_retained_start(history, len(history) - self.keep_recent)
            summarized_messages = history[:retained_start]
            retained_messages = history[retained_start:]
        summary = "\n".join(
            line
            for message in summarized_messages
            if (line := _render_summary_line(message))
        )
        return CompactionResult(summary=summary, retained_messages=retained_messages)


def _render_summary_line(message: dict) -> str:
    role = str(message.get("role", "unknown"))
    payload = _normalize_payload(message.get("payload", {}))

    if _is_system_generated_notification(payload):
        return ""

    if role == "tool":
        tool_name = _optional_text(payload.get("tool_name")) or "tool"
        result = payload.get("result", payload.get("content", ""))
        if result is None:
            result = ""
        elif not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        return f"tool result from {tool_name}: {result}"

    text = _payload_text_content(payload)
    tool_calls = payload.get("tool_calls", [])
    if role == "assistant" and tool_calls:
        tool_names = ", ".join(
            tool_name
            for tool_call in tool_calls
            if isinstance(tool_call, dict)
            and (tool_name := _optional_text(tool_call.get("name")))
        )
        suffix = f" (used tools: {tool_names})" if tool_names else ""
        return f"assistant: {text}{suffix}"

    return f"{role}: {text}"


def _safe_retained_start(history: list[dict], requested_start: int) -> int:
    """避免 compaction 把 assistant tool_use 与后续 tool_result 切开。"""
    start = max(0, requested_start)
    while start > 0 and _is_tool_message(history[start]):
        assistant_index = _find_preceding_assistant_tool_call(history, start)
        if assistant_index is None:
            break
        start = assistant_index
    return start


def _find_preceding_assistant_tool_call(history: list[dict], tool_index: int) -> int | None:
    index = tool_index - 1
    while index >= 0 and _is_tool_message(history[index]):
        index -= 1
    if index < 0:
        return None
    if not _assistant_expects_tool_results(history[index], history[index + 1 : tool_index + 1]):
        return None
    return index


def _assistant_expects_tool_results(assistant_message: dict, tool_messages: list[dict]) -> bool:
    if assistant_message.get("role") != "assistant":
        return False
    payload = _normalize_payload(assistant_message.get("payload", {}))
    expected_ids = {
        tool_call_id
        for tool_call in payload.get("tool_calls", [])
        if isinstance(tool_call, dict)
        and (tool_call_id := _optional_text(tool_call.get("id")))
    }
    if not expected_ids:
        return False
    return all(
        (tool_call_id := _tool_call_id(tool_message)) and tool_call_id in expected_ids
        for tool_message in tool_messages
    )


def _is_tool_message(message: dict) -> bool:
    return message.get("role") == "tool"


def _tool_call_id(message: dict) -> str:
    payload = _normalize_payload(message.get("payload", {}))
    return _optional_text(payload.get("tool_call_id"))


def _payload_text_content(payload: dict) -> str:
    if "text" in payload:
        text = payload.get("text", "")
        return "" if text is None else str(text)
    content = payload.get("content", "")
    if isinstance(content, list):
        rendered_parts = [
            "" if block.get("text") is None else str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("text")
        ]
        return "\n".join(part for part in rendered_parts if part)
    return "" if content is None else str(content)


def _optional_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_payload(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}


def _is_system_generated_notification(payload: dict) -> bool:
    return payload.get("kind") == "notification"
