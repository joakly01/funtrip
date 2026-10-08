from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from sqlalchemy.exc import SQLAlchemyError

from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.persistence.db import build_session_factory
from audit_agent_runtime.persistence.repositories.runtime_skills import RuntimeSkillRepository
from audit_agent_runtime.skills.core.definitions import SkillDefinition
from audit_agent_runtime.skills.core.loader import SkillLoader


def load_database_skill_versions(
    settings: Settings,
    bindings: list[dict],
    *,
    namespace: str,
) -> list[SkillDefinition]:
    """Materialize only explicitly bound immutable Skill versions for one Agent."""
    safe_namespace = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in namespace)
    cache_root = _runtime_skill_cache_root(settings) / "agents" / (safe_namespace or "default")
    try:
        with build_session_factory(settings)() as db:
            repository = RuntimeSkillRepository(db)
            _clear_cache_root(cache_root)
            for binding in bindings:
                if not binding.get("enabled", True):
                    continue
                skill_name = str(binding.get("skill_name") or "").strip()
                version = int(binding.get("version") or 0)
                version_record = repository.get_version(skill_name, version)
                if version_record is None:
                    continue
                skill_dir = cache_root / skill_name
                skill_dir.mkdir(parents=True, exist_ok=True)
                files = repository.list_files(skill_name, version)
                if files:
                    for file_record in files:
                        target = skill_dir / _safe_relative_path(file_record.relative_path)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(file_record.content_bytes)
                else:
                    (skill_dir / "SKILL.md").write_text(
                        version_record.skill_markdown,
                        encoding="utf-8",
                    )
    except SQLAlchemyError:
        return []
    return SkillLoader().load_from_skills_dir(cache_root)


def _runtime_skill_cache_root(settings: Settings) -> Path:
    database_fingerprint = "".join(
        ch if ch.isalnum() else "_"
        for ch in str(settings.database_url or "default")
    )[-80:]
    return Path(tempfile.gettempdir()) / "audit-agent-runtime" / "runtime-skills" / database_fingerprint


def _clear_cache_root(cache_root: Path) -> None:
    if cache_root.exists():
        shutil.rmtree(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)


def _safe_relative_path(relative_path: str) -> Path:
    path = Path(str(relative_path).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe runtime skill file path: {relative_path}")
    return path
