from __future__ import annotations

from audit_agent_runtime.skills.core.registry import SkillRegistry
from audit_agent_runtime.skills.core.skill_tool import render_skill_content
from audit_agent_runtime.tasks.in_process_runner import InProcessTaskRunner
from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.tools.core.base import BaseTool


class AgentTool(BaseTool):
    name = "spawn_agent"
    description = "Register a child agent task for delegated execution"
    input_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "agent_name": {"type": "string"},
            "mode": {"type": "string", "enum": ["background", "foreground"]},
            "parent_task_id": {"type": "string"},
        },
        "required": ["task", "agent_name"],
    }

    def __init__(
        self,
        task_registry: TaskRegistry,
        skill_registry: SkillRegistry | None = None,
        task_runner: InProcessTaskRunner | None = None,
    ):
        self.task_registry = task_registry
        self.skill_registry = skill_registry
        self.task_runner = task_runner

    def execute(self, arguments: dict, context) -> dict:
        agent_name = _normalize_agent_name(arguments.get("agent_name"))
        task_text = "" if arguments.get("task") is None else str(arguments["task"])
        payload = {"task": task_text}
        resolved_skill_name = _resolve_skill_name(self.skill_registry, agent_name)
        if resolved_skill_name is not None:
            skill = self.skill_registry.get(resolved_skill_name)
            skill_content = render_skill_content(
                skill,
                session_id=getattr(context, "session_id", None),
            )
            payload = {
                "task": (
                    f"{skill_content}\n\n"
                    "任务单：\n"
                    f"{task_text}"
                ),
                "skill_name": skill.name,
                "allowed_tools": list(skill.allowed_tools),
                "execution_context": skill.execution_context,
                "preferred_model": skill.model,
                "effort": skill.effort,
            }
        task = self.task_registry.register_task(
            session_id=context.session_id,
            agent_id=agent_name,
            mode=arguments.get("mode", "background"),
            parent_task_id=arguments.get("parent_task_id"),
            payload=payload,
            status="queued",
        )
        started = False
        if self.task_runner is not None and task.mode == "background":
            started = self.task_runner.start_task(task.task_id)
        return {
            "task_id": task.task_id,
            "agent_id": task.agent_id,
            "status": task.status,
            "mode": task.mode,
            "started": started,
        }


def _normalize_agent_name(value: object) -> str:
    if not isinstance(value, str):
        return "main"
    normalized = value.strip()
    return normalized or "main"


def _resolve_skill_name(
    skill_registry: SkillRegistry | None,
    agent_name: str,
) -> str | None:
    if skill_registry is None or not agent_name:
        return None
    candidates = _agent_name_candidates(agent_name)
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            skill_registry.get(candidate)
        except KeyError:
            continue
        return candidate

    for skill in skill_registry.list():
        skill_agent = _normalize_agent_name(skill.agent)
        if not skill_agent:
            continue
        if skill_agent in candidates:
            return skill.name
    return None


def _agent_name_candidates(agent_name: str) -> set[str]:
    candidates = {agent_name}
    if "-" in agent_name:
        candidates.add(agent_name.replace("-", "_"))
    if "_" in agent_name:
        candidates.add(agent_name.replace("_", "-"))
    return {candidate.strip() for candidate in candidates if candidate.strip()}
