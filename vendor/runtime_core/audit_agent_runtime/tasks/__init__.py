"""Task package exports loaded lazily to avoid import-time cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["TaskRegistry", "QueueManager", "build_task_notification"]


def __getattr__(name: str) -> Any:
    if name == "TaskRegistry":
        return import_module("audit_agent_runtime.tasks.task_registry").TaskRegistry
    if name == "QueueManager":
        return import_module("audit_agent_runtime.tasks.queue_manager").QueueManager
    if name == "build_task_notification":
        return import_module(
            "audit_agent_runtime.tasks.notifications"
        ).build_task_notification
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
