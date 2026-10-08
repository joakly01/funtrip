from __future__ import annotations

from audit_agent_runtime.skills.core.definitions import SkillDefinition


class SkillRegistry:
    def __init__(self, skills: list[SkillDefinition] | None = None):
        self._skills = {skill.name: skill for skill in (skills or [])}

    def register(self, skill: SkillDefinition) -> None:
        self._skills[skill.name] = skill

    def get(self, skill_name: str) -> SkillDefinition:
        try:
            return self._skills[skill_name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {skill_name}") from exc

    def list(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def list_user_invocable(self) -> list[SkillDefinition]:
        return [skill for skill in self._skills.values() if skill.user_invocable]

    def list_model_invocable(self) -> list[SkillDefinition]:
        return [
            skill
            for skill in self._skills.values()
            if skill.user_invocable and not skill.disable_model_invocation
        ]
