from sqlalchemy import select
from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.models import MessageRecord


class MessageRepository:
    def __init__(self, db: Session):
        self.db = db

    def add_message(self, session_id: str, role: str, payload: dict) -> MessageRecord:
        record = MessageRecord(
            session_id=session_id,
            role=role,
            payload_json=_normalize_payload(payload),
        )
        self.db.add(record)
        self.db.flush()
        return record

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        stmt = (
            select(MessageRecord)
            .where(MessageRecord.session_id == session_id)
            .order_by(MessageRecord.id.asc())
        )
        return list(self.db.scalars(stmt))

    def list_message_range(
        self,
        session_id: str,
        *,
        start_id: int | None,
        end_id: int | None,
    ) -> list[MessageRecord]:
        stmt = select(MessageRecord).where(MessageRecord.session_id == session_id)
        if isinstance(start_id, int):
            stmt = stmt.where(MessageRecord.id >= start_id)
        if isinstance(end_id, int):
            stmt = stmt.where(MessageRecord.id <= end_id)
        stmt = stmt.order_by(MessageRecord.id.asc())
        return list(self.db.scalars(stmt))

    def find_tool_result(self, session_id: str, tool_call_id: str) -> MessageRecord | None:
        normalized_tool_call_id = tool_call_id.strip()
        if not normalized_tool_call_id:
            return None
        stmt = (
            select(MessageRecord)
            .where(
                MessageRecord.session_id == session_id,
                MessageRecord.role == "tool",
            )
            .order_by(MessageRecord.id.desc())
        )
        for record in self.db.scalars(stmt):
            payload = record.payload_json
            if (
                isinstance(payload, dict)
                and payload.get("tool_call_id") == normalized_tool_call_id
            ):
                return record
        return None


def _normalize_payload(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}
