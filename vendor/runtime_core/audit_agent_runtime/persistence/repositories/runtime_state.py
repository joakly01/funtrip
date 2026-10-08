from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.models import RuntimeStateRecord


class RuntimeStateRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_state(self, session_id: str, payload: dict) -> RuntimeStateRecord:
        record = self.db.get(RuntimeStateRecord, session_id)
        if record is None:
            record = RuntimeStateRecord(
                session_id=session_id,
                payload_json=_normalize_payload(payload),
            )
            self.db.add(record)
        else:
            record.payload_json = _normalize_payload(payload)
        self.db.flush()
        return record

    def get_state(self, session_id: str) -> dict | None:
        record = self.db.get(RuntimeStateRecord, session_id)
        return None if record is None else _normalize_payload(record.payload_json)


def _normalize_payload(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}
