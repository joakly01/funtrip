from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from sqlalchemy.orm import Session

from audit_agent_runtime.memory.session_memory_service import (
    SessionMemorySnapshot,
    build_session_memory_update_candidate,
)
from audit_agent_runtime.compaction.tool_result_collapse import render_collapsed_message_text
from audit_agent_runtime.llm.base import ModelClient, ModelMessage
from audit_agent_runtime.persistence.models import SessionMemoryRecord
from audit_agent_runtime.persistence.repositories.memories import SessionMemoryRepository

SESSION_MEMORY_TEXT_KEYS = (
    "current_state",
    "user_goal",
    "runtime_state",
    "errors_and_corrections",
    "open_decisions",
    "audit_context",
    "business_domain_facts",
)

SESSION_MEMORY_LIST_KEYS = (
    "tools_used",
    "files_modules_discussed",
    "key_results",
)


@dataclass(slots=True)
class SessionMemoryUpdatePayload:
    summary_markdown: str
    summary_json: dict[str, Any]


class SessionMemoryUpdater:
    def __init__(
        self,
        model_client: ModelClient,
        *,
        min_incremental_messages: int = 8,
        force_refresh_messages: int = 14,
        min_assistant_chars: int = 240,
        min_tool_results: int = 2,
    ):
        self.model_client = model_client
        self.min_incremental_messages = max(1, int(min_incremental_messages))
        self.force_refresh_messages = max(
            self.min_incremental_messages,
            int(force_refresh_messages),
        )
        self.min_assistant_chars = max(1, int(min_assistant_chars))
        self.min_tool_results = max(1, int(min_tool_results))

    def update_session_memory(
        self,
        *,
        db: Session,
        session_id: str,
        current_turn: int,
        persisted_messages: list[dict],
    ) -> SessionMemoryRecord | None:
        memories = SessionMemoryRepository(db)
        latest_memory = memories.get_latest_memory(session_id)
        candidate = build_session_memory_update_candidate(
            session_id=session_id,
            current_turn=current_turn,
            persisted_messages=persisted_messages,
            latest_memory=(
                SessionMemorySnapshot(
                    version=latest_memory.version,
                    summarizes_until_message_id=latest_memory.summarizes_until_message_id,
                )
                if latest_memory is not None
                else None
            ),
        )
        if candidate is None:
            return None
        if not _should_refresh_incremental_memory(
            candidate.messages,
            min_incremental_messages=self.min_incremental_messages,
            force_refresh_messages=self.force_refresh_messages,
            min_assistant_chars=self.min_assistant_chars,
            min_tool_results=self.min_tool_results,
        ):
            return None

        response = self.model_client.complete(self._build_messages(candidate.messages), [])
        payload = _parse_session_memory_payload(response.output_text)
        if payload is None:
            return None

        return memories.add_memory(
            session_id=session_id,
            version=candidate.next_version,
            summary_markdown=payload.summary_markdown,
            summary_json=payload.summary_json,
            summarizes_until_message_id=max(candidate.message_ids),
            source_turn=candidate.source_turn,
        )

    def should_update_session_memory(
        self,
        *,
        db: Session,
        session_id: str,
        persisted_messages: list[dict],
    ) -> bool:
        memories = SessionMemoryRepository(db)
        latest_memory = memories.get_latest_memory(session_id)
        candidate = build_session_memory_update_candidate(
            session_id=session_id,
            current_turn=0,
            persisted_messages=persisted_messages,
            latest_memory=(
                SessionMemorySnapshot(
                    version=latest_memory.version,
                    summarizes_until_message_id=latest_memory.summarizes_until_message_id,
                )
                if latest_memory is not None
                else None
            ),
        )
        if candidate is None:
            return False
        return _should_refresh_incremental_memory(
            candidate.messages,
            min_incremental_messages=self.min_incremental_messages,
            force_refresh_messages=self.force_refresh_messages,
            min_assistant_chars=self.min_assistant_chars,
            min_tool_results=self.min_tool_results,
        )

    def _build_messages(self, persisted_messages: list[dict]) -> list[ModelMessage]:
        transcript_lines = []
        for message in persisted_messages:
            role = str(message.get("role", "unknown"))
            transcript_lines.append(f"{role}: {render_collapsed_message_text(message)}")

        return [
            ModelMessage(
                role="system",
                content=(
                    "You update structured session memory for an audit agent runtime. "
                    "Return strict JSON only with keys summary_markdown and summary_json. "
                    "summary_markdown must be concise markdown sections. "
                    "summary_json must be an object using these stable section keys only when relevant: "
                    "current_state, user_goal, runtime_state, files_modules_discussed, tools_used, "
                    "errors_and_corrections, open_decisions, key_results, audit_context, business_domain_facts. "
                    "Use strings for narrative sections and arrays of strings for tools_used. "
                    "When a tool result appears after a tool call, treat the structured tool result as authoritative; "
                    "do not summarize that tool as pending or waiting just because a later assistant message is only a progress update. "
                    "Do not use code fences."
                ),
            ),
            ModelMessage(
                role="user",
                content=(
                    "Summarize the new session transcript window into durable session memory.\n\n"
                    + "\n".join(line for line in transcript_lines if line.strip())
                ),
            ),
        ]


def _should_refresh_incremental_memory(
    persisted_messages: list[dict],
    *,
    min_incremental_messages: int,
    force_refresh_messages: int,
    min_assistant_chars: int,
    min_tool_results: int,
) -> bool:
    if len(persisted_messages) < max(1, int(min_incremental_messages)):
        return False

    tool_result_count = 0
    longest_assistant_text = 0
    for message in persisted_messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).strip().lower()
        if role == "tool":
            tool_result_count += 1
        if role != "assistant":
            continue
        text = render_collapsed_message_text(message)
        longest_assistant_text = max(longest_assistant_text, len(text.strip()))

    # session memory 应更像阶段性沉淀，而不是补参数几轮就刷新一次。
    # 这里只有在新增窗口足够大，或已经出现较强的新结果信号时才更新。
    if len(persisted_messages) >= max(force_refresh_messages, min_incremental_messages):
        return True
    if tool_result_count >= max(1, int(min_tool_results)):
        return True
    if longest_assistant_text >= max(1, int(min_assistant_chars)):
        return True
    return False

def _parse_session_memory_payload(text: str) -> SessionMemoryUpdatePayload | None:
    normalized = _strip_code_fences(text).strip()
    if not normalized:
        return None
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    summary_markdown = payload.get("summary_markdown")
    summary_json = payload.get("summary_json")
    if not isinstance(summary_markdown, str) or not summary_markdown.strip():
        return None
    if not isinstance(summary_json, dict):
        return None
    return SessionMemoryUpdatePayload(
        summary_markdown=summary_markdown.strip(),
        summary_json=_normalize_summary_json(summary_json),
    )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _normalize_summary_json(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in SESSION_MEMORY_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    for key in SESSION_MEMORY_LIST_KEYS:
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if items:
            normalized[key] = items
    return normalized
