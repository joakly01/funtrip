from __future__ import annotations

from uuid import uuid4

from audit_agent_runtime.context.query_models import QueryTask
from audit_agent_runtime.persistence.db import build_session_factory
from audit_agent_runtime.persistence.repositories.tasks import TaskRepository
from audit_agent_runtime.tasks.notifications import build_task_notification
from audit_agent_runtime.utils.json_utils import normalize_json_value


class TaskRegistry:
    def __init__(self, settings):
        self.session_factory = build_session_factory(settings)

    def register_task(
        self,
        *,
        session_id: str,
        agent_id: str,
        mode: str,
        payload: dict | None = None,
        parent_task_id: str | None = None,
        status: str = "queued",
    ) -> QueryTask:
        task_id = str(uuid4())
        normalized_agent_id = _normalize_agent_id(agent_id)
        normalized_payload = _normalize_payload(payload)
        with self.session_factory() as db:
            tasks = TaskRepository(db)
            task = tasks.create_task(
                task_id=task_id,
                session_id=session_id,
                parent_task_id=parent_task_id,
                agent_id=normalized_agent_id,
                status=status,
                mode=mode,
                payload=normalized_payload,
            )
            db.commit()
            return task

    def get_task(self, task_id: str) -> QueryTask | None:
        with self.session_factory() as db:
            return TaskRepository(db).get_task(task_id)

    def ensure_task(
        self,
        *,
        task_id: str,
        session_id: str,
        agent_id: str,
        mode: str,
        payload: dict | None = None,
        parent_task_id: str | None = None,
        status: str = "queued",
    ) -> QueryTask:
        existing = self.get_task(task_id)
        if existing is not None:
            return existing
        normalized_agent_id = _normalize_agent_id(agent_id)
        normalized_payload = _normalize_payload(payload)
        with self.session_factory() as db:
            tasks = TaskRepository(db)
            task = tasks.create_task(
                task_id=task_id,
                session_id=session_id,
                parent_task_id=parent_task_id,
                agent_id=normalized_agent_id,
                status=status,
                mode=mode,
                payload=normalized_payload,
            )
            db.commit()
            return task

    def list_session_tasks(self, session_id: str) -> list[QueryTask]:
        with self.session_factory() as db:
            return TaskRepository(db).list_session_tasks(session_id)

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        payload: dict | None = None,
    ) -> QueryTask | None:
        with self.session_factory() as db:
            task = TaskRepository(db).update_task(
                task_id,
                status=status,
                payload=_normalize_payload(payload) if payload is not None else None,
            )
            db.commit()
            return task

    def complete_task(
        self,
        task_id: str,
        *,
        payload: dict | None = None,
        summary: str | None = None,
    ):
        normalized_payload = _normalize_payload(payload) if payload is not None else None
        task = self.update_task(task_id, status="completed", payload=normalized_payload)
        if task is None:
            return None
        return build_task_notification(task, summary=summary, payload=normalized_payload)

    def request_stop(self, task_id: str) -> QueryTask | None:
        return self.update_task(task_id, status="stop_requested")

    def is_stop_requested(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        return task is not None and task.status == "stop_requested"


def _normalize_agent_id(agent_id: object) -> str:
    if not isinstance(agent_id, str):
        return "main"
    normalized = agent_id.strip()
    return normalized or "main"


def _normalize_payload(payload: object) -> dict:
    return normalize_json_value(payload) if isinstance(payload, dict) else {}
