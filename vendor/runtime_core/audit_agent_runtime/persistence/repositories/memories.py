from sqlalchemy import select
from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.models import SessionMemoryRecord


class SessionMemoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def add_memory(
        self,
        *,
        session_id: str,
        version: int,
        summary_markdown: str,
        summary_json: dict,
        summarizes_until_message_id: int | None,
        source_turn: int | None,
    ) -> SessionMemoryRecord:
        record = SessionMemoryRecord(
            session_id=session_id,
            version=version,
            summary_markdown=summary_markdown,
            summary_json=_normalize_payload(summary_json),
            summarizes_until_message_id=summarizes_until_message_id,
            source_turn=source_turn,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_latest_memory(self, session_id: str) -> SessionMemoryRecord | None:
        stmt = (
            select(SessionMemoryRecord)
            .where(SessionMemoryRecord.session_id == session_id)
            .order_by(SessionMemoryRecord.version.desc(), SessionMemoryRecord.id.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def list_memories(self, session_id: str) -> list[SessionMemoryRecord]:
        stmt = (
            select(SessionMemoryRecord)
            .where(SessionMemoryRecord.session_id == session_id)
            .order_by(SessionMemoryRecord.version.asc(), SessionMemoryRecord.id.asc())
        )
        return list(self.db.scalars(stmt))


def _normalize_payload(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}
