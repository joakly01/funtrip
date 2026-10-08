from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from audit_agent_runtime.context.query_models import QueuedCommand
from audit_agent_runtime.persistence.models import QueuedCommandRecord


_PRIORITY_ORDER = {"now": 0, "next": 1, "later": 2}


def _to_queued_command(record: QueuedCommandRecord) -> QueuedCommand:
    return QueuedCommand(
        id=record.id,
        session_id=record.session_id,
        target_agent_id=_normalize_target_agent_id(record.target_agent_id),
        kind=record.kind,
        priority=record.priority,
        payload=_normalize_payload(record.payload_json),
        created_at=record.created_at,
        claimed_at=record.claimed_at,
        consumed_at=record.consumed_at,
    )


class QueuedCommandRepository:
    def __init__(self, db: Session):
        self.db = db

    def enqueue(
        self,
        session_id: str,
        kind: str,
        priority: str,
        payload: dict | None = None,
        target_agent_id: str | None = None,
    ) -> QueuedCommand:
        record = QueuedCommandRecord(
            session_id=session_id,
            target_agent_id=_normalize_target_agent_id(target_agent_id),
            kind=kind,
            priority=priority,
            payload_json=_normalize_payload(payload),
        )
        self.db.add(record)
        self.db.flush()
        return _to_queued_command(record)

    def get(self, command_id: int) -> QueuedCommand | None:
        record = self.db.get(QueuedCommandRecord, command_id)
        return None if record is None else _to_queued_command(record)

    def list_ready(
        self,
        session_id: str,
        max_priority: str,
        target_agent_id: str | None = None,
    ) -> list[QueuedCommand]:
        max_rank = _PRIORITY_ORDER[max_priority]
        priority_rank = case(
            *_priority_case_args(),
            else_=99,
        )
        stmt = (
            select(QueuedCommandRecord)
            .where(QueuedCommandRecord.session_id == session_id)
            .where(QueuedCommandRecord.consumed_at.is_(None))
            .where(QueuedCommandRecord.claimed_at.is_(None))
            .where(priority_rank <= max_rank)
            .order_by(priority_rank.asc(), QueuedCommandRecord.id.asc())
        )
        if target_agent_id is not None:
            stmt = stmt.where(
                QueuedCommandRecord.target_agent_id == target_agent_id
            )
        else:
            stmt = stmt.where(QueuedCommandRecord.target_agent_id.is_(None))
        return [_to_queued_command(record) for record in self.db.scalars(stmt)]

    def list_ready_global(
        self,
        *,
        max_priority: str,
        target_agent_id: str | None = None,
        allowed_kinds: set[str] | None = None,
        limit: int = 50,
    ) -> list[QueuedCommand]:
        max_rank = _PRIORITY_ORDER[max_priority]
        priority_rank = case(
            *_priority_case_args(),
            else_=99,
        )
        stmt = (
            select(QueuedCommandRecord)
            .where(QueuedCommandRecord.consumed_at.is_(None))
            .where(QueuedCommandRecord.claimed_at.is_(None))
            .where(priority_rank <= max_rank)
            # 全局 notification host 跨 session 消费队列，也必须遵守和单 session
            # drain 一致的优先级顺序；id 只作为同优先级内的稳定 tie-breaker。
            .order_by(priority_rank.asc(), QueuedCommandRecord.id.asc())
            .limit(limit)
        )
        if target_agent_id is not None:
            stmt = stmt.where(QueuedCommandRecord.target_agent_id == target_agent_id)
        else:
            stmt = stmt.where(QueuedCommandRecord.target_agent_id.is_(None))
        if allowed_kinds is not None:
            normalized_kinds = {
                kind for kind in allowed_kinds if isinstance(kind, str) and kind
            }
            if not normalized_kinds:
                return []
            stmt = stmt.where(QueuedCommandRecord.kind.in_(normalized_kinds))
        return [_to_queued_command(record) for record in self.db.scalars(stmt)]

    def claim(self, command_id: int) -> QueuedCommand | None:
        record = self.db.get(QueuedCommandRecord, command_id)
        if record is None:
            return None
        if record.consumed_at is not None or record.claimed_at is not None:
            return None
        record.claimed_at = datetime.now(timezone.utc)
        self.db.flush()
        return _to_queued_command(record)

    def release_claim(self, command_id: int) -> QueuedCommand | None:
        record = self.db.get(QueuedCommandRecord, command_id)
        if record is None or record.consumed_at is not None:
            return None
        if record.claimed_at is None:
            return _to_queued_command(record)
        record.claimed_at = None
        self.db.flush()
        return _to_queued_command(record)

    def mark_consumed(self, command_id: int) -> QueuedCommand | None:
        record = self.db.get(QueuedCommandRecord, command_id)
        if record is None:
            return None
        if record.consumed_at is not None:
            return None
        record.consumed_at = datetime.now(timezone.utc)
        self.db.flush()
        return _to_queued_command(record)


def _priority_case_args() -> list[tuple[object, int]]:
    return [
        (QueuedCommandRecord.priority == priority, rank)
        for priority, rank in _PRIORITY_ORDER.items()
    ]


def _normalize_payload(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}


def _normalize_target_agent_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
