from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(slots=True)
class MemoryExtraction:
    summary: str | None


class MemoryExtractor:
    def __init__(self, max_items: int = 3):
        self.max_items = max_items

    def extract(self, history: list[dict]) -> MemoryExtraction:
        if self.max_items <= 0:
            return MemoryExtraction(summary=None)
        candidates: list[str] = []
        for message in reversed(history):
            candidate = _render_memory_candidate(message)
            if not candidate:
                continue
            candidates.append(candidate)
            if len(candidates) >= self.max_items:
                break
        if not candidates:
            return MemoryExtraction(summary=None)
        candidates.reverse()
        return MemoryExtraction(summary="\n".join(f"- {item}" for item in candidates))


def _render_memory_candidate(message: dict) -> str | None:
    role = message.get("role")
    payload = _normalize_payload(message.get("payload", {}))

    if _is_system_generated_notification(payload):
        return None

    if role == "user":
        text = _payload_text_content(payload)
        return f"User focus: {text}" if text else None

    if role == "assistant":
        text = _payload_text_content(payload)
        tool_calls = payload.get("tool_calls", [])
        tool_names = ", ".join(
            tool_name
            for tool_call in tool_calls
            if isinstance(tool_call, dict)
            and (tool_name := _optional_text(tool_call.get("name")))
        )
        if text and tool_names:
            return f"Prior answer: {text} (used tools: {tool_names})"
        if text:
            return f"Prior answer: {text}"
        if tool_names:
            return f"Prior answer used tools: {tool_names}"
        return None

    if role == "tool":
        tool_name = _optional_text(payload.get("tool_name")) or "tool"
        result = payload.get("result", payload.get("content", ""))
        if result is None:
            result = ""
        elif not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        result = result.strip()
        return f"Tool result ({tool_name}): {result}" if result else None

    return None


def _payload_text_content(payload: dict) -> str:
    if "text" in payload:
        text = payload.get("text", "")
        return ("" if text is None else str(text)).strip()
    content = payload.get("content", "")
    if isinstance(content, list):
        rendered_parts = [
            ("" if block.get("text") is None else str(block.get("text", ""))).strip()
            for block in content
            if isinstance(block, dict) and block.get("text")
        ]
        return "\n".join(part for part in rendered_parts if part).strip()
    return ("" if content is None else str(content)).strip()


def _optional_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_payload(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}


def _is_system_generated_notification(payload: dict) -> bool:
    return payload.get("kind") == "notification"
