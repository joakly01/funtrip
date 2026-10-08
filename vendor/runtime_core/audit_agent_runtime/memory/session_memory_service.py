from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SessionMemorySnapshot:
    version: int
    summarizes_until_message_id: int | None


@dataclass(slots=True)
class SessionMemoryUpdateCandidate:
    session_id: str
    next_version: int
    source_turn: int
    message_ids: list[int]
    messages: list[dict]


def build_session_memory_update_candidate(
    *,
    session_id: str,
    current_turn: int,
    persisted_messages: list[dict],
    latest_memory: SessionMemorySnapshot | None,
) -> SessionMemoryUpdateCandidate | None:
    latest_boundary = (
        latest_memory.summarizes_until_message_id
        if latest_memory is not None
        else None
    )
    candidate_messages: list[dict] = []
    candidate_ids: list[int] = []

    for message in persisted_messages:
        if not isinstance(message, dict):
            continue
        message_id = _normalize_message_id(message.get("id"))
        if message_id is None:
            continue
        if latest_boundary is not None and message_id <= latest_boundary:
            continue
        candidate_messages.append(message)
        candidate_ids.append(message_id)

    if not candidate_messages:
        return None

    return SessionMemoryUpdateCandidate(
        session_id=session_id,
        next_version=1 if latest_memory is None else latest_memory.version + 1,
        source_turn=max(current_turn, 0),
        message_ids=candidate_ids,
        messages=candidate_messages,
    )


def _normalize_message_id(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None
