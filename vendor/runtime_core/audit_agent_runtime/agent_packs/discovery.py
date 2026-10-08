from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from audit_agent_runtime.config.settings import Settings


_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


@dataclass(slots=True, frozen=True)
class AgentPackInfo:
    agent_id: str
    display_name: str
    description: str
    agent_dir: str
    has_claude_md: bool
    has_agent_md: bool
    has_skill: bool
    has_tools: bool
    builtin_skills: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class AgentInstructionDocument:
    filename: str
    content: str


def resolve_extensions_root(settings: Settings, *, repo_root: Path | None = None) -> Path:
    configured = str(settings.runtime_agents_dir or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            root = repo_root or Path(__file__).resolve().parents[3]
            path = root / path
        return path.resolve()
    package_root = Path(__file__).resolve().parents[1]
    return (package_root / "extensions").resolve()


def resolve_agents_root(settings: Settings, *, repo_root: Path | None = None) -> Path:
    return resolve_extensions_root(settings, repo_root=repo_root)


def discover_agent_packs(settings: Settings, *, repo_root: Path | None = None) -> list[AgentPackInfo]:
    extensions_root = resolve_extensions_root(settings, repo_root=repo_root)
    if not extensions_root.exists():
        return []

    packs: list[AgentPackInfo] = []
    for entry in sorted(extensions_root.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "agent.json"
        if not manifest_path.exists():
            continue
        agent_id = entry.name
        if not _AGENT_ID_PATTERN.match(agent_id):
            continue
        manifest = _read_agent_manifest(entry)
        display_name = str(manifest.get("display_name") or agent_id).strip() or agent_id
        description = str(manifest.get("description") or "").strip()
        skill_name = str(manifest.get("skill_name") or "").strip()
        builtin_skills = _builtin_skills_from_manifest(manifest)
        if skill_name and skill_name not in builtin_skills:
            builtin_skills = [skill_name, *builtin_skills]
        packs.append(
            AgentPackInfo(
                agent_id=agent_id,
                display_name=display_name,
                description=description,
                agent_dir=str(entry.resolve()),
                has_claude_md=_has_claude_md(entry),
                has_agent_md=_has_agent_md(entry),
                has_skill=_agent_pack_has_skill(entry, agent_id)
                or bool(builtin_skills),
                has_tools=(entry / "tools.py").exists(),
                builtin_skills=tuple(builtin_skills),
                aliases=tuple(_aliases_from_manifest(manifest)),
            )
        )
    return packs


def load_agent_instruction_document(agent_dir: Path) -> AgentInstructionDocument | None:
    """加载唯一 Agent 指令文件；AGENT.md 优先，CLAUDE.md 仅作兼容回退。"""
    return _load_instruction_document(
        agent_dir,
        filenames=("AGENT.md", "agent.md", "CLAUDE.md", "claude.md"),
    )


def _load_instruction_document(
    agent_dir: Path,
    *,
    filenames: tuple[str, ...],
) -> AgentInstructionDocument | None:
    for filename in filenames:
        path = agent_dir / filename
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if content:
            return AgentInstructionDocument(filename=filename, content=content)
    return None


def _has_claude_md(agent_dir: Path) -> bool:
    return (agent_dir / "CLAUDE.md").exists() or (agent_dir / "claude.md").exists()


def _has_agent_md(agent_dir: Path) -> bool:
    return (agent_dir / "AGENT.md").exists() or (agent_dir / "agent.md").exists()


def _agent_pack_has_skill(agent_dir: Path, agent_id: str) -> bool:
    if any((agent_dir / "skills").rglob("SKILL.md")):
        return True
    if (agent_dir / "SKILL.md").exists():
        return True
    if (agent_dir / agent_id / "SKILL.md").exists():
        return True
    if (agent_dir / "skill" / "SKILL.md").exists():
        return True
    return False


def _read_agent_manifest(agent_dir: Path) -> dict:
    manifest_path = agent_dir / "agent.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _builtin_skills_from_manifest(manifest: dict) -> list[str]:
    raw = manifest.get("builtin_skills")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name:
            names.append(name)
    return names


def _aliases_from_manifest(manifest: dict) -> list[str]:
    raw = manifest.get("aliases")
    if not isinstance(raw, list):
        return []
    aliases: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        alias = item.strip()
        if alias and _AGENT_ID_PATTERN.match(alias):
            aliases.append(alias)
    return aliases
