from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from audit_agent_runtime.skills.core.definitions import SkillDefinition
from audit_agent_runtime.skills.core.loader import SkillLoader
from audit_agent_runtime.tools.core.base import BaseTool


def load_builtin_skills(skill_names: list[str] | tuple[str, ...]) -> list[SkillDefinition]:
    wanted = {name.strip() for name in skill_names if isinstance(name, str) and name.strip()}
    if not wanted:
        return []
    builtin_dir = Path(__file__).resolve().parents[1] / "skills" / "builtin"
    return [
        skill
        for skill in SkillLoader().load_from_skills_dir(builtin_dir)
        if skill.name in wanted
    ]


def load_skills_from_agent_pack(
    agent_dir: Path,
    *,
    agent_id: str,
    builtin_skill_names: list[str] | tuple[str, ...] = (),
) -> list[SkillDefinition]:
    loaded = load_builtin_skills(builtin_skill_names)
    seen = {skill.name for skill in loaded}
    loader = SkillLoader()

    standard_skills = agent_dir / "skills"
    if standard_skills.exists():
        for skill in loader.load_from_tree(standard_skills):
            if skill.name in seen:
                raise ValueError(
                    f"duplicate skill name {skill.name!r} in agent pack {agent_id!r}"
                )
            loaded.append(skill)
            seen.add(skill.name)
        return loaded

    skill_subdirs = [
        child
        for child in sorted(agent_dir.iterdir())
        if child.is_dir() and (child / "SKILL.md").exists()
    ]
    if skill_subdirs:
        for skill in loader.load_from_skills_dir(agent_dir):
            if skill.name in seen:
                continue
            loaded.append(skill)
            seen.add(skill.name)
        return loaded

    nested_skill = agent_dir / agent_id / "SKILL.md"
    if nested_skill.exists():
        for skill in loader.load_from_skills_dir(agent_dir):
            if skill.name in seen:
                continue
            loaded.append(skill)
            seen.add(skill.name)
        return loaded

    skill_subdir = agent_dir / "skill" / "SKILL.md"
    if skill_subdir.exists():
        skill = loader.load_from_skill_file(skill_subdir, skill_name=agent_id)
        if skill.name not in seen:
            loaded.append(skill)
        return loaded

    flat_skill = agent_dir / "SKILL.md"
    if flat_skill.exists():
        skill = loader.load_from_skill_file(flat_skill, skill_name=agent_id)
        if skill.name not in seen:
            loaded.append(skill)
    return loaded


def load_tools_from_agent_pack(
    agent_dir: Path,
    *,
    agent_id: str,
    settings,
    session_factory,
    model_client,
) -> list[BaseTool]:
    tools_path = agent_dir / "tools.py"
    if not tools_path.exists():
        return []
    module = _import_module_from_path(
        module_name=f"_audit_agent_pack_tools_{agent_id}",
        module_path=tools_path,
    )
    builder = getattr(module, "build_agent_tools", None)
    if not callable(builder):
        return []
    tools = builder(
        settings=settings,
        session_factory=session_factory,
        model_client=model_client,
        agent_id=agent_id,
        agent_dir=agent_dir,
    )
    if not isinstance(tools, list):
        return []
    return [tool for tool in tools if isinstance(tool, BaseTool)]


def _import_module_from_path(*, module_name: str, module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to import agent tools module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
