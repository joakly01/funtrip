from __future__ import annotations

from threading import Lock, Thread

from audit_agent_runtime.agents.runner import SubagentRunner
from audit_agent_runtime.persistence.db import build_session_factory
from audit_agent_runtime.persistence.repositories.messages import MessageRepository
from audit_agent_runtime.persistence.repositories.queued_commands import (
    QueuedCommandRepository,
)
from audit_agent_runtime.persistence.repositories.tasks import TaskRepository
from audit_agent_runtime.tasks.notifications import (
    build_task_notification,
    compact_notification_payload_for_main_thread,
    notification_to_payload,
    render_send_message_payload,
)
from audit_agent_runtime.tasks.queue_manager import QueueManager
from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.utils.json_utils import normalize_json_value


class InProcessTaskRunner:
    def __init__(
        self,
        settings,
        task_registry: TaskRegistry,
        queue_manager: QueueManager,
        subagent_runner: SubagentRunner,
    ):
        self.task_registry = task_registry
        self.queue_manager = queue_manager
        self.subagent_runner = subagent_runner
        self.session_factory = build_session_factory(settings)
        self._disable_background_threads = settings.database_url.startswith(
            "sqlite+pysqlite:///:memory:"
        )
        self._active_task_ids: set[str] = set()
        self._lock = Lock()

    def start_task(self, task_id: str) -> bool:
        if self._disable_background_threads:
            return False
        with self._lock:
            if task_id in self._active_task_ids:
                return False
            self._active_task_ids.add(task_id)
        thread = Thread(target=self.run_task_now, args=(task_id,), daemon=True)
        thread.start()
        return True

    def continue_task(self, task_id: str, message: str) -> bool:
        task = self.task_registry.get_task(task_id)
        if task is None:
            return False
        updated_payload = dict(task.payload)
        continuations = _normalize_continuations(updated_payload.get("continuations", []))
        continuations.append(message)
        updated_payload["continuations"] = continuations
        if task.status == "failed":
            updated_payload.pop("error", None)
        self.task_registry.update_task(task_id, payload=updated_payload)
        if task.status in {"completed", "stop_requested", "failed"}:
            self.task_registry.update_task(task_id, status="queued", payload=updated_payload)
            started = self.start_task(task_id)
            return started or self._disable_background_threads
        return task.status in {"queued", "running"}

    def run_task_now(self, task_id: str) -> None:
        try:
            task = self.task_registry.get_task(task_id)
            if task is None:
                return
            if task.status in {"completed", "stop_requested", "failed"}:
                # 后台线程可能晚于 stop_task / failure / completion 到达。
                # 已终止任务不能被 runner 重新置为 running，否则会复活已停止的子任务。
                return
            self.task_registry.update_task(task_id, status="running")
            history = self._load_history(task.session_id)
            _, notification_payload = self.subagent_runner.run_task(task, history)
            self._complete_task_with_subagent_result(
                task=task,
                task_id=task_id,
                notification_payload=notification_payload,
            )
        except Exception as exc:
            self._mark_task_failed(task_id=task_id, error=exc)
        finally:
            with self._lock:
                self._active_task_ids.discard(task_id)

    def _load_history(self, session_id: str) -> list[dict]:
        with self.session_factory() as db:
            messages = MessageRepository(db).list_messages(session_id)
        return [
            {"role": message.role, "payload": message.payload_json}
            for message in messages
        ]

    def _complete_task_with_subagent_result(
        self,
        *,
        task,
        task_id: str,
        notification_payload: object,
    ) -> None:
        normalized_notification_payload = (
            notification_payload if isinstance(notification_payload, dict) else {}
        )
        task_payload = _normalize_task_result_payload(
            normalized_notification_payload.get("payload")
        )
        final_output = task_payload.get("final_output", "")
        subagent_events = task_payload.get("subagent_events", [])
        updated_payload = {
            **task.payload,
            "final_output": final_output,
            "continuations": _normalize_continuations(task.payload.get("continuations", [])),
            "subagent_events": subagent_events,
        }
        summary = render_send_message_payload(
            normalized_notification_payload.get("summary") or final_output or ""
        )
        self._persist_task_notification(
            session_id=task.session_id,
            task_id=task_id,
            status="completed",
            payload=updated_payload,
            summary=summary,
        )

    def _mark_task_failed(self, *, task_id: str, error: Exception) -> None:
        current_task = self.task_registry.get_task(task_id)
        current_payload = dict(current_task.payload) if current_task is not None else {}
        if current_task is None:
            return
        failed_payload = {
            **current_payload,
            "error": str(error),
        }
        self._persist_task_notification(
            session_id=current_task.session_id,
            task_id=task_id,
            status="failed",
            payload=failed_payload,
            summary=str(error),
        )

    def _persist_task_notification(
        self,
        *,
        session_id: str,
        task_id: str,
        status: str,
        payload: dict,
        summary: str | None,
    ) -> None:
        normalized_payload = normalize_json_value(payload)
        with self.session_factory() as db:
            tasks = TaskRepository(db)
            queue = QueuedCommandRepository(db)
            current_task = tasks.get_task(task_id)
            if current_task is None or current_task.status in {
                "completed",
                "stop_requested",
                "failed",
            }:
                # 子任务执行结果可能晚于用户 stop_task 到达。这里在最终落库前重读状态，
                # 保证迟到 completion/failure 不能复活已终止任务，也不会向主线程投递过期通知。
                db.rollback()
                return
            updated_task = tasks.update_task(task_id, status=status, payload=normalized_payload)
            if updated_task is None:
                db.rollback()
                return
            notification = build_task_notification(
                updated_task,
                summary=summary,
                payload=normalized_payload,
            )
            # 任务完成态与主线程通知入队必须在同一提交边界内可见，
            # 否则主线程可能只看到 completed，却还看不到 queued command。
            queue.enqueue(
                session_id=session_id,
                kind="task_notification",
                priority="later",
                payload=compact_notification_payload_for_main_thread(
                    notification_to_payload(notification)
                ),
                target_agent_id=None,
            )
            db.commit()


def _normalize_continuations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_task_result_payload(value: object) -> dict:
    if not isinstance(value, dict):
        return {"final_output": "", "subagent_events": []}
    final_output = value.get("final_output", "")
    subagent_events = value.get("subagent_events", [])
    if not isinstance(subagent_events, list):
        subagent_events = []
    return {
        "final_output": final_output,
        "subagent_events": subagent_events,
    }
