from sqlalchemy import select
from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.models import SessionCompactionRecord


class SessionCompactionRepository:
    def __init__(self, db: Session):
        self.db = db

    def add_compaction(
        self,
        *,
        compaction_id: str,
        session_id: str,
        boundary_message_id: int | None,
        summary_text: str,
        preserved_from_message_id: int | None,
        preserved_to_message_id: int | None,
        reason: str,
    ) -> SessionCompactionRecord:
        record = SessionCompactionRecord(
            compaction_id=compaction_id,
            session_id=session_id,
            boundary_message_id=boundary_message_id,
            summary_text=summary_text,
            preserved_from_message_id=preserved_from_message_id,
            preserved_to_message_id=preserved_to_message_id,
            reason=reason,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_latest_compaction(self, session_id: str) -> SessionCompactionRecord | None:
        stmt = (
            select(SessionCompactionRecord)
            .where(SessionCompactionRecord.session_id == session_id)
            .order_by(
                SessionCompactionRecord.created_at.desc(),
                SessionCompactionRecord.compaction_id.desc(),
            )
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def list_compactions(self, session_id: str) -> list[SessionCompactionRecord]:
        stmt = (
            select(SessionCompactionRecord)
            .where(SessionCompactionRecord.session_id == session_id)
            .order_by(
                SessionCompactionRecord.created_at.asc(),
                SessionCompactionRecord.compaction_id.asc(),
            )
        )
        return list(self.db.scalars(stmt))

    def list_recent_compactions(
        self,
        session_id: str,
        *,
        limit: int = 3,
    ) -> list[SessionCompactionRecord]:
        stmt = (
            select(SessionCompactionRecord)
            .where(SessionCompactionRecord.session_id == session_id)
            .order_by(
                SessionCompactionRecord.created_at.desc(),
                SessionCompactionRecord.compaction_id.desc(),
            )
            .limit(limit)
        )
        return list(self.db.scalars(stmt))
