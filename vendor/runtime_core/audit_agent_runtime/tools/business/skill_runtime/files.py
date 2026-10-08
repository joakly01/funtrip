from __future__ import annotations

from pathlib import Path, PurePosixPath

from audit_agent_runtime.skills.core.registry import SkillRegistry
from audit_agent_runtime.tools.core.base import BaseTool


MAX_SKILL_FILE_CHARS = 100_000


class ReadSkillFileTool(BaseTool):
    name = "read_skill_file"
    description = (
        "读取已加载 skill 根目录内的 UTF-8 文本附件，例如 references/*.md、"
        "assets/*.txt 或 scripts/*.py。只接受相对路径，禁止访问 skill_root 外文件。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "已加载的 skill 名称。"},
            "path": {
                "type": "string",
                "description": "skill_root 内的相对文件路径。",
            },
        },
        "required": ["skill_name", "path"],
    }

    def __init__(self, skill_registry: SkillRegistry):
        self.skill_registry = skill_registry

    def execute(self, arguments: dict, context) -> dict:
        skill_name = str(arguments.get("skill_name") or "").strip()
        skill = self.skill_registry.get(skill_name)
        if skill is None:
            return _error(skill_name, "", "unknown_skill", f"未知 skill：{skill_name}")
        if not skill.skill_root:
            return _error(
                skill_name,
                "",
                "missing_skill_root",
                f"skill {skill_name} 没有可用的 skill_root。",
            )

        relative_path = _normalize_relative_path(arguments.get("path"))
        if relative_path is None:
            return _error(
                skill_name,
                str(arguments.get("path") or ""),
                "invalid_skill_path",
                "path 必须是 skill_root 内不含 .. 的相对路径。",
            )

        skill_root = Path(skill.skill_root).resolve()
        target = (skill_root / Path(relative_path)).resolve()
        if not target.is_relative_to(skill_root):
            return _error(
                skill_name,
                relative_path,
                "invalid_skill_path",
                "path 不能逃逸 skill_root。",
            )
        if not target.is_file():
            return _error(
                skill_name,
                relative_path,
                "skill_file_not_found",
                "目标 skill 文件不存在。",
            )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return _error(
                skill_name,
                relative_path,
                "skill_file_not_text",
                "read_skill_file 只支持 UTF-8 文本文件。",
            )
        except OSError as exc:
            return _error(
                skill_name,
                relative_path,
                "skill_file_read_failed",
                f"读取 skill 文件失败：{exc}",
            )

        if len(content) > MAX_SKILL_FILE_CHARS:
            return _error(
                skill_name,
                relative_path,
                "skill_file_too_large",
                f"skill 文件超过 {MAX_SKILL_FILE_CHARS} 字符限制。",
            )
        return {
            "ok": True,
            "skill_name": skill_name,
            "path": relative_path,
            "content": content,
        }


def _normalize_relative_path(value: object) -> str | None:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or any(not part for part in path.parts):
        return None
    return path.as_posix()


def _error(skill_name: str, path: str, error: str, summary: str) -> dict:
    return {
        "ok": False,
        "skill_name": skill_name,
        "path": path,
        "error": error,
        "summary": summary,
    }
