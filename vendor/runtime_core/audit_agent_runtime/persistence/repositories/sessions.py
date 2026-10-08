from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.models import SessionRecord


def _normalize_text(value: object) -> str:
    return value if isinstance(value, str) else ""


class SessionRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_session(self, session_id: str, user_id: str, title: str) -> SessionRecord:
        record = SessionRecord(
            session_id=_normalize_text(session_id),
            user_id=_normalize_text(user_id),
            title=_normalize_text(title),
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_session(self, session_id: str) -> SessionRecord | None:
        record = self.db.get(SessionRecord, session_id)
        if record is None:
            return None
        record.user_id = _normalize_text(record.user_id)
        record.title = _normalize_text(record.title)
        return record

    def list_sessions(self, user_id: str) -> list[SessionRecord]:
        stmt = (
            select(SessionRecord)
            .where(SessionRecord.user_id == user_id)
            .order_by(SessionRecord.updated_at.desc(), SessionRecord.created_at.desc())
        )
        records = list(self.db.scalars(stmt))
        for record in records:
            record.user_id = _normalize_text(record.user_id)
            record.title = _normalize_text(record.title)
        return records

    def list_recent_sessions(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int = 0,
        session_prefixes: list[str] | None = None,
    ) -> list[SessionRecord]:
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        stmt = select(SessionRecord).where(SessionRecord.user_id == user_id)
        prefixes = [prefix for prefix in (session_prefixes or []) if prefix]
        if prefixes:
            stmt = stmt.where(
                or_(*[SessionRecord.session_id.like(f"{prefix}%") for prefix in prefixes])
            )
        stmt = (
            stmt.order_by(SessionRecord.updated_at.desc(), SessionRecord.created_at.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        records = list(self.db.scalars(stmt))
        for record in records:
            record.user_id = _normalize_text(record.user_id)
            record.title = _normalize_text(record.title)
        return records

    def delete_session(self, session_id: str) -> bool:
        record = self.get_session(session_id)
        if record is None:
            return False
        self.db.delete(record)
        self.db.flush()
        return True
