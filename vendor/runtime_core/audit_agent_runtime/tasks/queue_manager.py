from __future__ import annotations

from audit_agent_runtime.context.query_models import QueuedCommand
from audit_agent_runtime.persistence.db import build_session_factory
from audit_agent_runtime.persistence.repositories.queued_commands import (
    QueuedCommandRepository,
)

_QUEUE_PRIORITIES = {"now", "next", "later"}


def _normalize_priority(value: object, *, default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _QUEUE_PRIORITIES:
            return normalized
    return default


class QueueManager:
    def __init__(self, settings):
        self.session_factory = build_session_factory(settings)

    def enqueue(
        self,
        *,
        session_id: str,
        kind: str,
        priority: str,
        payload: dict | None = None,
        target_agent_id: str | None = None,
        ) -> QueuedCommand:
        with self.session_factory() as db:
            queue = QueuedCommandRepository(db)
            command = queue.enqueue(
                session_id=session_id,
                kind=kind,
                priority=_normalize_priority(priority, default="next"),
                payload=payload,
                target_agent_id=target_agent_id,
            )
            db.commit()
            return command

    def get(self, command_id: int) -> QueuedCommand | None:
        with self.session_factory() as db:
            queue = QueuedCommandRepository(db)
            return queue.get(command_id)

    def list_ready(
        self,
        *,
        session_id: str,
        max_priority: str,
        target_agent_id: str | None = None,
    ) -> list[QueuedCommand]:
        with self.session_factory() as db:
            queue = QueuedCommandRepository(db)
            return queue.list_ready(
                session_id=session_id,
                max_priority=_normalize_priority(max_priority, default="later"),
                target_agent_id=target_agent_id,
            )

    def mark_consumed(self, command_id: int) -> QueuedCommand | None:
        with self.session_factory() as db:
            queue = QueuedCommandRepository(db)
            command = queue.mark_consumed(command_id)
            db.commit()
            return command

    def claim(self, command_id: int) -> QueuedCommand | None:
        with self.session_factory() as db:
            queue = QueuedCommandRepository(db)
            command = queue.claim(command_id)
            db.commit()
            return command

    def release_claim(self, command_id: int) -> QueuedCommand | None:
        with self.session_factory() as db:
            queue = QueuedCommandRepository(db)
            command = queue.release_claim(command_id)
            db.commit()
            return command

    def list_ready_global(
        self,
        *,
        max_priority: str,
        target_agent_id: str | None = None,
        allowed_kinds: set[str] | None = None,
        limit: int = 50,
    ) -> list[QueuedCommand]:
        with self.session_factory() as db:
            queue = QueuedCommandRepository(db)
            return queue.list_ready_global(
                max_priority=_normalize_priority(max_priority, default="later"),
                target_agent_id=target_agent_id,
                allowed_kinds=allowed_kinds,
                limit=limit,
            )
