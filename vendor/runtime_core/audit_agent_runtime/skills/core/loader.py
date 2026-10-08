from __future__ import annotations

import re
from pathlib import Path

from audit_agent_runtime.skills.core.definitions import SkillDefinition
from audit_agent_runtime.skills.core.frontmatter import parse_frontmatter


_EFFORT_LEVELS = {"low", "medium", "high", "max"}
_TOOL_NAME_PATTERN = re.compile(r"[A-Za-z_]")


class SkillLoader:
    def load_from_skill_dirs(self, skill_dirs: list[Path]) -> list[SkillDefinition]:
        loaded: list[SkillDefinition] = []
        seen_names: set[str] = set()

        # 多来源 skill 按目录顺序加载。前面的目录优先级更高，因此 builtin
        # 可以保护内置能力不被页面写入的同名 user skill 静默覆盖。
        for skills_dir in skill_dirs:
            for skill in self.load_from_skills_dir(skills_dir):
                if skill.name in seen_names:
                    continue
                loaded.append(skill)
                seen_names.add(skill.name)
        return loaded

    def load_from_skills_dir(self, skills_dir: Path) -> list[SkillDefinition]:
        loaded: list[SkillDefinition] = []
        if not skills_dir.exists():
            return loaded

        for skill_path in sorted(skills_dir.iterdir()):
            skill_markdown = skill_path / "SKILL.md"
            if not skill_path.is_dir() or not skill_markdown.exists():
                continue

            loaded.append(self.load_from_skill_file(skill_markdown))

        return loaded

    def load_from_tree(self, root: Path) -> list[SkillDefinition]:
        if not root.exists():
            return []

        loaded: list[SkillDefinition] = []
        origins: dict[str, Path] = {}
        for skill_markdown in sorted(root.rglob("SKILL.md")):
            skill = self.load_from_skill_file(skill_markdown)
            previous = origins.get(skill.name)
            if previous is not None:
                raise ValueError(
                    f"duplicate skill name {skill.name!r}: "
                    f"{previous} and {skill_markdown}"
                )
            origins[skill.name] = skill_markdown
            loaded.append(skill)

        return loaded

    def load_from_skill_file(
        self,
        skill_markdown: Path,
        *,
        skill_name: str | None = None,
    ) -> SkillDefinition:
        skill_path = skill_markdown.parent
        resolved_name = (skill_name or skill_path.name).strip()
        if not resolved_name:
            raise ValueError(f"skill name is empty: {skill_markdown}")

        metadata, content = parse_frontmatter(skill_markdown.read_text(encoding="utf-8"))
        stripped_content = content.strip()
        return SkillDefinition(
            name=resolved_name,
            display_name=_optional_scalar_string(metadata.get("name")),
            description=_description_value(
                metadata.get("description"),
                fallback=_description_from_content(stripped_content),
            ),
            content=stripped_content,
            skill_root=str(skill_path),
            allowed_tools=_allowed_tools(metadata.get("allowed-tools")),
            argument_hint=_optional_scalar_string(metadata.get("argument-hint")),
            arguments=_argument_names(metadata.get("arguments")),
            when_to_use=_optional_scalar_string(metadata.get("when_to_use")),
            when_not_to_use=_optional_scalar_string(metadata.get("when_not_to_use")),
            version=_optional_scalar_string(metadata.get("version")),
            user_invocable=_bool_value(metadata.get("user-invocable"), default=True),
            disable_model_invocation=_bool_value(
                metadata.get("disable-model-invocation"),
                default=False,
            ),
            execution_context="fork" if metadata.get("context") == "fork" else None,
            agent=_optional_agent_string(metadata.get("agent")),
            effort=_parse_effort(metadata.get("effort")),
            model=(
                None
                if metadata.get("model") in {None, "inherit"}
                else _optional_model_string(metadata.get("model"))
            ),
        )


def _allowed_tools(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in value.split() if _looks_like_tool_name(item)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and _looks_like_tool_name(item)]
    return []


def _bool_value(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    return value is True or value == "true"


def _argument_names(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and _valid_argument_name(item)]
    return [item for item in str(value).split() if _valid_argument_name(item)]


def _description_value(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        text = value.strip()
        return text or fallback
    if isinstance(value, (int, float, bool)):
        return str(value)
    return fallback


def _optional_scalar_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _optional_model_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.isdigit():
        return None
    return text


def _optional_agent_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.isdigit():
        return None
    return text


def _looks_like_tool_name(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped == "*":
        return True
    return bool(_TOOL_NAME_PATTERN.search(stripped))


def _valid_argument_name(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and not stripped.isdigit()


def _parse_effort(value: object) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _EFFORT_LEVELS:
        return text
    try:
        numeric = int(text)
    except ValueError:
        return None
    return numeric


def _description_from_content(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
