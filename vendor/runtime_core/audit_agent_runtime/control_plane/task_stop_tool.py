from __future__ import annotations

from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.tools.core.base import BaseTool


class TaskStopTool(BaseTool):
    name = "stop_task"
    description = "Request stop for an existing task or agent"
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
        },
        "required": ["task_id"],
    }

    def __init__(self, task_registry: TaskRegistry):
        self.task_registry = task_registry

    def execute(self, arguments: dict, context) -> dict:
        task = self.task_registry.request_stop(arguments["task_id"])
        return {
            "task_id": arguments["task_id"],
            "status": None if task is None else task.status,
        }
