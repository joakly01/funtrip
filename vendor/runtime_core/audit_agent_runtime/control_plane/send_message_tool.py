from __future__ import annotations

from audit_agent_runtime.tasks.in_process_runner import InProcessTaskRunner
from audit_agent_runtime.tasks.queue_manager import QueueManager
from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.tools.core.base import BaseTool


class SendMessageTool(BaseTool):
    name = "send_message"
    description = "Send a continuation message to an existing task or agent"
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "message": {"type": "string"},
            "priority": {"type": "string", "enum": ["now", "next", "later"]},
            "target_agent_id": {"type": "string"},
        },
        "required": ["task_id", "message"],
    }

    def __init__(
        self,
        queue_manager: QueueManager,
        task_registry: TaskRegistry | None = None,
        task_runner: InProcessTaskRunner | None = None,
    ):
        self.queue_manager = queue_manager
        self.task_registry = task_registry
        self.task_runner = task_runner

    def execute(self, arguments: dict, context) -> dict:
        priority = arguments.get("priority", "next")
        if priority not in {"now", "next", "later"}:
            priority = "next"
        target_agent_id = arguments.get("target_agent_id")
        if isinstance(target_agent_id, str) and not target_agent_id.strip():
            target_agent_id = None
        if target_agent_id is None and self.task_registry is not None:
            task = self.task_registry.get_task(arguments["task_id"])
            if task is not None:
                target_agent_id = None if task.agent_id == "main" else task.agent_id
        command = self.queue_manager.enqueue(
            session_id=context.session_id,
            kind="send_message",
            priority=priority,
            payload={
                "task_id": arguments["task_id"],
                "message": arguments["message"],
            },
            target_agent_id=target_agent_id,
        )
        resumed = False
        if self.task_runner is not None:
            resumed = self.task_runner.continue_task(
                arguments["task_id"], arguments["message"]
            )
        return {
            "queued_command_id": command.id,
            "priority": command.priority,
            "task_id": arguments["task_id"],
            "target_agent_id": target_agent_id,
            "resumed": resumed,
        }
