from __future__ import annotations

from audit_agent_runtime.tools.core.base import BaseTool


class AskUserQuestionTool(BaseTool):
    name = "AskUserQuestion"
    description = (
        "Ask the user a targeted clarifying question and pause until they reply. "
        "Use this to resolve ambiguity before continuing."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
        },
        "required": ["question"],
    }

    def execute(self, arguments: dict, context) -> dict:
        question = str(arguments.get("question", "")).strip()
        if not question:
            return {
                "is_error": True,
                "error": "missing_question",
                "message": "AskUserQuestion requires a non-empty question.",
            }
        return {
            "kind": "user_question",
            "status": "awaiting_response",
            "question": question,
        }


class EnterPlanModeTool(BaseTool):
    name = "EnterPlanMode"
    description = (
        "Enter session-level plan mode for exploration and design before implementation."
    )
    input_schema = {"type": "object", "properties": {}}

    def execute(self, arguments: dict, context) -> dict:
        return {
            "kind": "plan_mode_enter",
            "status": "entered",
            "message": (
                "Entered plan mode. Focus on exploration, clarification, and "
                "designing an implementation approach before coding."
            ),
        }


class ExitPlanModeTool(BaseTool):
    name = "ExitPlanMode"
    description = (
        "Present the current implementation plan for approval and request exit "
        "from plan mode before coding."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plan": {"type": "string"},
            "allowed_prompts": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["plan"],
    }

    def execute(self, arguments: dict, context) -> dict:
        plan = str(arguments.get("plan", "")).strip()
        if not plan:
            return {
                "is_error": True,
                "error": "missing_plan",
                "message": "ExitPlanMode requires a non-empty plan.",
            }
        allowed_prompts = arguments.get("allowed_prompts", [])
        if not isinstance(allowed_prompts, list):
            allowed_prompts = []
        normalized_allowed_prompts = [
            prompt.strip()
            for prompt in allowed_prompts
            if isinstance(prompt, str) and prompt.strip()
        ]
        return {
            "kind": "plan_mode_exit",
            "status": "awaiting_approval",
            "question": "Exit plan mode?",
            "plan": plan,
            "allowed_prompts": normalized_allowed_prompts,
        }


def build_interaction_tools() -> list[BaseTool]:
    return [AskUserQuestionTool(), EnterPlanModeTool(), ExitPlanModeTool()]
