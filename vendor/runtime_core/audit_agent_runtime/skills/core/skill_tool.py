from __future__ import annotations

import os
import re
import shlex
from typing import Any

from audit_agent_runtime.skills.core.definitions import SkillDefinition
from audit_agent_runtime.skills.core.registry import SkillRegistry
from audit_agent_runtime.tasks.in_process_runner import InProcessTaskRunner
from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.tools.core.base import BaseTool


class SkillTool(BaseTool):
    name = "Skill"
    description = (
        "Execute a skill within the main conversation\n\n"
        "When users ask you to perform tasks, check if any of the available skills match. "
        "Skills provide specialized capabilities and domain knowledge.\n\n"
        "When users reference a \"slash command\" or \"/<something>\" (for example, \"/commit\"), "
        "they are referring to a skill. Use this tool to invoke it.\n\n"
        "How to invoke:\n"
        "- Use this tool with the skill name and optional arguments\n"
        "- Examples:\n"
        "  - `skill: \"pdf\"` - invoke the pdf skill\n"
        "  - `skill: \"commit\", args: \"-m 'Fix bug'\"` - invoke with arguments\n"
        "  - `skill: \"review-pr\", args: \"123\"` - invoke with arguments\n\n"
        "Important:\n"
        "- Available skills are listed in system-reminder messages in the conversation\n"
        "- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task\n"
        "- NEVER mention a skill without actually calling this tool\n"
        "- Do not invoke a skill that is already running\n"
        "- Do not use this tool for built-in CLI commands (like /help, /clear, etc.)\n"
        "- If you see a <command-name> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill": {"type": "string"},
            "args": {"type": "string"},
            "skill_name": {"type": "string"},
        },
        "required": [],
    }

    def __init__(
        self,
        skill_registry: SkillRegistry,
        task_registry: TaskRegistry | None = None,
        task_runner: InProcessTaskRunner | None = None,
    ):
        self.skill_registry = skill_registry
        self.task_registry = task_registry
        self.task_runner = task_runner

    def execute(self, arguments: dict, context) -> dict:
        skill_name = arguments.get("skill") or arguments.get("skill_name")
        if not skill_name:
            raise KeyError("skill")
        if isinstance(skill_name, str) and skill_name.startswith("/"):
            skill_name = skill_name[1:]
        skill = self.skill_registry.get(skill_name)
        if not skill.user_invocable or skill.disable_model_invocation:
            raise KeyError(f"Unknown skill: {skill_name}")
        skill_content = render_skill_content(
            skill,
            session_id=getattr(context, "session_id", None),
            args=arguments.get("args"),
        )
        if skill.execution_context == "fork":
            return _fork_skill_execution(
                skill=skill,
                skill_content=skill_content,
                args=arguments.get("args"),
                context=context,
                task_registry=self.task_registry,
                task_runner=self.task_runner,
            )
        return {
            "kind": "skill_definition",
            "status": "inline",
            "skill_name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "content": skill_content,
            "allowed_tools": skill.allowed_tools,
            "argument_hint": skill.argument_hint,
            "arguments": skill.arguments,
            "when_to_use": skill.when_to_use,
            "when_not_to_use": skill.when_not_to_use,
            "model": skill.model,
            "runtime_hints": {
                "allowed_tools": skill.allowed_tools,
                "preferred_model": skill.model,
                "disable_model_invocation": skill.disable_model_invocation,
                "execution_context": skill.execution_context,
                "agent": skill.agent,
                "effort": skill.effort,
                "argument_hint": skill.argument_hint,
            },
            "disable_model_invocation": skill.disable_model_invocation,
        }


class NamedSkillTool(BaseTool):
    include_in_definitions = False

    def __init__(
        self,
        skill: SkillDefinition,
        skill_registry: SkillRegistry,
        task_registry: TaskRegistry | None = None,
        task_runner: InProcessTaskRunner | None = None,
    ):
        self.name = skill.name
        self.description = (
            f"Load the `{skill.name}` skill into the current runtime context."
        )
        self.input_schema = {
            "type": "object",
            "properties": {},
        }
        self.skill = skill
        self.skill_registry = skill_registry
        self.task_registry = task_registry
        self.task_runner = task_runner

    def execute(self, arguments: dict, context) -> dict:
        skill = self.skill_registry.get(self.skill.name)
        skill_content = render_skill_content(
            skill,
            session_id=getattr(context, "session_id", None),
            args=arguments.get("args"),
        )
        if skill.execution_context == "fork":
            return _fork_skill_execution(
                skill=skill,
                skill_content=skill_content,
                args=arguments.get("args"),
                context=context,
                task_registry=self.task_registry,
                task_runner=self.task_runner,
            )
        return {
            "kind": "skill_definition",
            "status": "inline",
            "skill_name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "content": skill_content,
            "allowed_tools": skill.allowed_tools,
            "argument_hint": skill.argument_hint,
            "arguments": skill.arguments,
            "when_to_use": skill.when_to_use,
            "when_not_to_use": skill.when_not_to_use,
            "model": skill.model,
            "runtime_hints": {
                "allowed_tools": skill.allowed_tools,
                "preferred_model": skill.model,
                "disable_model_invocation": skill.disable_model_invocation,
                "execution_context": skill.execution_context,
                "agent": skill.agent,
                "effort": skill.effort,
                "argument_hint": skill.argument_hint,
            },
            "disable_model_invocation": skill.disable_model_invocation,
        }


class LoadSkillAliasTool(SkillTool):
    name = "load_skill"
    description = (
        "Compatibility alias for Skill. Load a skill definition into the current runtime context.\n\n"
        "Available skills are listed in system-reminder messages in the conversation. "
        "When a listed skill matches the user's request, this is a BLOCKING REQUIREMENT: "
        "load the relevant skill before answering or calling that skill's business tools. "
        "After loading a skill, follow the returned SKILL.md instructions and runtime hints. "
        "Do not repeatedly load a skill that is already active, and do not mention a relevant "
        "skill without actually loading it."
    )


def render_skill_content(
    skill: SkillDefinition,
    session_id: str | None = None,
    args: str | None = None,
) -> str:
    content = skill.content
    if skill.skill_root:
        normalized_root = skill.skill_root.replace("\\", "/")
        content = f"Base directory for this skill: {normalized_root}\n\n{content}"
        content = content.replace("${CLAUDE_SKILL_DIR}", normalized_root)
    content = _substitute_arguments(content, args, skill.arguments)
    content = content.replace(
        "${CLAUDE_SESSION_ID}",
        session_id or os.getenv("AUDIT_AGENT_RUNTIME_SESSION_ID", "session"),
    )
    return content


def _fork_skill_execution(
    *,
    skill: SkillDefinition,
    skill_content: str,
    args: str | None,
    context: Any,
    task_registry: TaskRegistry | None,
    task_runner: InProcessTaskRunner | None,
) -> dict:
    if task_registry is None:
        return {
            "kind": "skill_definition",
            "status": "inline",
            "skill_name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "content": skill_content,
            "allowed_tools": skill.allowed_tools,
            "argument_hint": skill.argument_hint,
            "arguments": skill.arguments,
            "when_to_use": skill.when_to_use,
            "when_not_to_use": skill.when_not_to_use,
            "model": skill.model,
            "runtime_hints": {
                "allowed_tools": skill.allowed_tools,
                "preferred_model": skill.model,
                "disable_model_invocation": skill.disable_model_invocation,
                "execution_context": skill.execution_context,
                "agent": skill.agent,
                "effort": skill.effort,
                "argument_hint": skill.argument_hint,
            },
            "disable_model_invocation": skill.disable_model_invocation,
        }

    agent_id = (skill.agent or skill.name).strip() or skill.name
    task = task_registry.register_task(
        session_id=getattr(context, "session_id", ""),
        agent_id=agent_id,
        mode="background",
        parent_task_id=getattr(context, "task_id", None),
        payload={
            "task": skill_content,
            "skill_name": skill.name,
            "skill_args": args,
            "execution_context": "fork",
            "allowed_tools": list(skill.allowed_tools),
            "preferred_model": skill.model,
            "effort": skill.effort,
        },
        status="queued",
    )
    started = False
    if task_runner is not None:
        started = task_runner.start_task(task.task_id)
    return {
        "kind": "skill_fork",
        "status": "forked",
        "skill_name": skill.name,
        "display_name": skill.display_name,
        "task_id": task.task_id,
        "agent_id": task.agent_id,
        "started": started,
        "execution_context": "fork",
    }


def _parse_args(args: str) -> list[str]:
    if not args.strip():
        return []
    try:
        return shlex.split(args, posix=True)
    except ValueError:
        return [part for part in args.split() if part]


def _substitute_arguments(
    content: str,
    args: str | None,
    argument_names: list[str] | None = None,
) -> str:
    if args is None:
        return content

    parsed_args = _parse_args(args)
    original_content = content

    for index, name in enumerate(argument_names or []):
        escaped_name = re.escape(name)
        content = re.sub(
            rf"\${escaped_name}(?![\[\w])",
            parsed_args[index] if index < len(parsed_args) else "",
            content,
        )

    content = re.sub(
        r"\$ARGUMENTS\[(\d+)\]",
        lambda match: parsed_args[int(match.group(1))]
        if int(match.group(1)) < len(parsed_args)
        else "",
        content,
    )
    content = re.sub(
        r"\$(\d+)(?!\w)",
        lambda match: parsed_args[int(match.group(1))]
        if int(match.group(1)) < len(parsed_args)
        else "",
        content,
    )
    content = content.replace("$ARGUMENTS", args)

    if content == original_content and args:
        content = f"{content}\n\nARGUMENTS: {args}"
    return content
