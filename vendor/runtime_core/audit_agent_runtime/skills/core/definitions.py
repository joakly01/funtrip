from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    content: str
    display_name: str | None = None
    skill_root: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str | None = None
    arguments: list[str] = field(default_factory=list)
    when_to_use: str | None = None
    when_not_to_use: str | None = None
    version: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    execution_context: str | None = None
    agent: str | None = None
    effort: str | int | None = None
    model: str | None = None
