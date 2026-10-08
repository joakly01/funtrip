from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from audit_agent_runtime.context.query_models import QueryTask
from audit_agent_runtime.persistence.models import QueryTaskRecord


def _to_query_task(record: QueryTaskRecord) -> QueryTask:
    return QueryTask(
        task_id=record.task_id,
        session_id=record.session_id,
        parent_task_id=record.parent_task_id,
        agent_id=record.agent_id,
        status=record.status,
        mode=record.mode,
        payload=_normalize_payload(record.payload_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class TaskRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_task(
        self,
        task_id: str,
        session_id: str,
        agent_id: str,
        status: str,
        mode: str,
        payload: dict | None = None,
        parent_task_id: str | None = None,
    ) -> QueryTask:
        record = QueryTaskRecord(
            task_id=task_id,
            session_id=session_id,
            parent_task_id=parent_task_id,
            agent_id=agent_id,
            status=status,
            mode=mode,
            payload_json=_normalize_payload(payload),
        )
        self.db.add(record)
        self.db.flush()
        return _to_query_task(record)

    def get_task(self, task_id: str) -> QueryTask | None:
        record = self.db.get(QueryTaskRecord, task_id)
        return None if record is None else _to_query_task(record)

    def list_session_tasks(self, session_id: str) -> list[QueryTask]:
        stmt = (
            select(QueryTaskRecord)
            .where(QueryTaskRecord.session_id == session_id)
            .order_by(QueryTaskRecord.created_at.asc(), QueryTaskRecord.task_id.asc())
        )
        return [_to_query_task(record) for record in self.db.scalars(stmt)]

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        mode: str | None = None,
        agent_id: str | None = None,
        payload: dict | None = None,
    ) -> QueryTask | None:
        record = self.db.get(QueryTaskRecord, task_id)
        if record is None:
            return None
        if status is not None:
            record.status = status
        if mode is not None:
            record.mode = mode
        if agent_id is not None:
            record.agent_id = agent_id
        if payload is not None:
            record.payload_json = _normalize_payload(payload)
        self.db.flush()
        return _to_query_task(record)


def _normalize_payload(payload: object) -> dict:
    return payload if isinstance(payload, dict) else {}
