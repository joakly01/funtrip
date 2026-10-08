from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CompactionCandidate:
    session_id: str
    boundary_message_id: int
    compacted_message_ids: list[int]
    compacted_messages: list[dict]
    preserved_message_ids: list[int]
    preserved_messages: list[dict]


def build_compaction_candidate(
    *,
    session_id: str,
    persisted_messages: list[dict],
    keep_recent: int,
) -> CompactionCandidate | None:
    normalized_messages: list[tuple[int, dict]] = []
    for message in persisted_messages:
        if not isinstance(message, dict):
            continue
        message_id = _normalize_message_id(message.get("id"))
        if message_id is None:
            continue
        normalized_messages.append((message_id, message))

    if keep_recent < 0:
        keep_recent = 0
    if len(normalized_messages) <= keep_recent:
        return None

    split_index = len(normalized_messages) - keep_recent
    compacted = normalized_messages[:split_index]
    preserved = normalized_messages[split_index:]
    if not compacted:
        return None

    return CompactionCandidate(
        session_id=session_id,
        boundary_message_id=compacted[-1][0],
        compacted_message_ids=[message_id for message_id, _ in compacted],
        compacted_messages=[message for _, message in compacted],
        preserved_message_ids=[message_id for message_id, _ in preserved],
        preserved_messages=[message for _, message in preserved],
    )


def _normalize_message_id(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None
