from __future__ import annotations

import ast
from dataclasses import dataclass, field
import importlib.util
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
from typing import Any

from audit_agent_runtime.skills.core.registry import SkillRegistry
from audit_agent_runtime.tools.core.base import BaseTool
from audit_agent_runtime.utils.json_utils import normalize_json_value


DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 60


class ValidateSkillScriptTool(BaseTool):
    name = "validate_skill_script"
    description = (
        "校验已加载 skill 包内的 Python 脚本是否可执行。"
        "本工具只访问该 skill 的 skill_root，禁止绝对路径和 ../ 路径；"
        "只支持 .py 脚本，会检查语法和缺失的 Python 依赖。"
        "通过后再调用 execute_skill_script 执行。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "目标 skill 名称。"},
            "script_path": {
                "type": "string",
                "description": "skill 根目录内的相对 Python 脚本路径，例如 scripts/analyze.py。",
            },
        },
        "required": ["skill_name", "script_path"],
    }

    def __init__(self, skill_registry: SkillRegistry):
        self.skill_registry = skill_registry

    def execute(self, arguments: dict, context) -> dict:
        validation = validate_skill_script(
            self.skill_registry,
            skill_name=arguments.get("skill_name"),
            script_path=arguments.get("script_path"),
        )
        return validation.to_result()


class ExecuteSkillScriptTool(BaseTool):
    name = "execute_skill_script"
    description = (
        "执行已通过 validate_skill_script 校验的 skill 包内 Python 脚本。"
        "脚本必须从 stdin 读取 JSON，并向 stdout 输出 JSON 对象；"
        "本工具不安装依赖、不执行 shell 命令，只在受控子进程中运行 skill_root 内的 .py 文件。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "目标 skill 名称。"},
            "script_path": {
                "type": "string",
                "description": "skill 根目录内的相对 Python 脚本路径，例如 scripts/analyze.py。",
            },
            "input": {
                "type": "object",
                "description": "传给脚本 stdin 的 JSON 对象。",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "执行超时时间，默认 10 秒，最大 60 秒。",
            },
        },
        "required": ["skill_name", "script_path"],
    }

    def __init__(self, skill_registry: SkillRegistry):
        self.skill_registry = skill_registry

    def execute(self, arguments: dict, context) -> dict:
        validation = validate_skill_script(
            self.skill_registry,
            skill_name=arguments.get("skill_name"),
            script_path=arguments.get("script_path"),
        )
        if not validation.ok:
            return validation.to_result()

        payload = arguments.get("input")
        if not isinstance(payload, dict):
            payload = {}
        timeout_seconds = _coerce_timeout(arguments.get("timeout_seconds"))
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _SCRIPT_RUNNER_SOURCE,
                    str(validation.skill_root),
                    str(validation.script_file),
                ],
                cwd=str(validation.skill_root),
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "python_timeout",
                "skill_name": validation.skill_name,
                "script_path": validation.relative_path,
                "summary": f"Python 脚本执行超过 {timeout_seconds} 秒，已终止。",
            }

        if completed.returncode != 0:
            return {
                "ok": False,
                "error": "python_execution_failed",
                "skill_name": validation.skill_name,
                "script_path": validation.relative_path,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "summary": "Python 脚本执行失败。",
            }

        try:
            output = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "invalid_script_output",
                "skill_name": validation.skill_name,
                "script_path": validation.relative_path,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "summary": "Python 脚本必须向 stdout 输出 JSON 对象。",
            }
        if not isinstance(output, dict):
            output = {"result": output}

        return normalize_json_value(
            {
                "ok": True,
                "artifact_type": "skill_script_result",
                "tool_type": "skill_script_result",
                "skill_name": validation.skill_name,
                "script_path": validation.relative_path,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "result": output.get("result"),
                "tables": output.get("tables", []),
                "artifacts": output.get("artifacts", []),
                "summary": output.get("summary") or "Python skill 脚本已执行完成。",
            }
        )


@dataclass(slots=True)
class SkillScriptValidation:
    ok: bool
    skill_name: str
    relative_path: str
    skill_root: Path | None = None
    script_file: Path | None = None
    error: str | None = None
    message: str | None = None
    missing_dependencies: list[str] = field(default_factory=list)

    def to_result(self) -> dict:
        result = {
            "ok": self.ok,
            "skill_name": self.skill_name,
            "script_path": self.relative_path,
            "missing_dependencies": self.missing_dependencies,
        }
        if self.skill_root is not None:
            result["skill_root"] = str(self.skill_root)
        if self.error:
            result["error"] = self.error
        if self.message:
            result["summary"] = self.message
        return result


def build_skill_script_tools(skill_registry: SkillRegistry) -> list[BaseTool]:
    return [
        ValidateSkillScriptTool(skill_registry),
        ExecuteSkillScriptTool(skill_registry),
    ]


def validate_skill_script(
    skill_registry: SkillRegistry,
    *,
    skill_name: object,
    script_path: object,
) -> SkillScriptValidation:
    normalized_skill_name = str(skill_name or "").strip()
    relative_path = _normalize_script_path(script_path)
    if not normalized_skill_name:
        return _validation_error("", relative_path, "missing_skill_name", "缺少 skill_name。")
    if relative_path is None:
        return _validation_error(
            normalized_skill_name,
            "",
            "unsafe_script_path",
            "script_path 必须是 skill 根目录内的安全相对路径。",
        )
    if not relative_path.endswith(".py"):
        return _validation_error(
            normalized_skill_name,
            relative_path,
            "unsupported_script_type",
            "当前仅支持执行 .py Python 脚本。",
        )

    try:
        skill = skill_registry.get(normalized_skill_name)
    except KeyError:
        return _validation_error(
            normalized_skill_name,
            relative_path,
            "unknown_skill",
            f"未知 skill：{normalized_skill_name}。",
        )
    if not skill.skill_root:
        return _validation_error(
            normalized_skill_name,
            relative_path,
            "missing_skill_root",
            f"skill {normalized_skill_name} 没有可用的 skill_root。",
        )

    skill_root = Path(skill.skill_root).resolve()
    script_file = (skill_root / Path(relative_path)).resolve()
    if not script_file.is_relative_to(skill_root):
        return _validation_error(
            normalized_skill_name,
            relative_path,
            "unsafe_script_path",
            "script_path 不能逃逸 skill 根目录。",
            skill_root=skill_root,
        )
    if not script_file.exists() or not script_file.is_file():
        return _validation_error(
            normalized_skill_name,
            relative_path,
            "script_not_found",
            f"脚本文件不存在：{relative_path}。",
            skill_root=skill_root,
        )

    try:
        source = script_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(script_file), mode="exec")
    except SyntaxError as exc:
        return _validation_error(
            normalized_skill_name,
            relative_path,
            "python_syntax_error",
            f"Python 语法错误：{exc.msg}。",
            skill_root=skill_root,
            script_file=script_file,
        )
    except UnicodeDecodeError:
        return _validation_error(
            normalized_skill_name,
            relative_path,
            "invalid_encoding",
            "Python 脚本必须使用 UTF-8 编码。",
            skill_root=skill_root,
            script_file=script_file,
        )

    missing_dependencies = _missing_imports(tree, skill_root=skill_root, script_dir=script_file.parent)
    if missing_dependencies:
        return SkillScriptValidation(
            ok=False,
            skill_name=normalized_skill_name,
            relative_path=relative_path,
            skill_root=skill_root,
            script_file=script_file,
            error="missing_dependencies",
            message=(
                "Python 脚本依赖未安装："
                f"{', '.join(missing_dependencies)}。请先把依赖加入 runtime 环境后再执行。"
            ),
            missing_dependencies=missing_dependencies,
        )

    return SkillScriptValidation(
        ok=True,
        skill_name=normalized_skill_name,
        relative_path=relative_path,
        skill_root=skill_root,
        script_file=script_file,
        missing_dependencies=[],
    )


def _validation_error(
    skill_name: str,
    relative_path: str,
    error: str,
    message: str,
    *,
    skill_root: Path | None = None,
    script_file: Path | None = None,
) -> SkillScriptValidation:
    return SkillScriptValidation(
        ok=False,
        skill_name=skill_name,
        relative_path=relative_path,
        skill_root=skill_root,
        script_file=script_file,
        error=error,
        message=message,
    )


def _normalize_script_path(value: object) -> str | None:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return None
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or any(not part for part in path.parts):
        return None
    return path.as_posix()


def _missing_imports(tree: ast.AST, *, skill_root: Path, script_dir: Path) -> list[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return sorted(name for name in names if not _module_available(name, skill_root, script_dir))


def _module_available(name: str, skill_root: Path, script_dir: Path) -> bool:
    if name in sys.builtin_module_names:
        return True
    if (script_dir / f"{name}.py").exists() or (script_dir / name / "__init__.py").exists():
        return True
    if (skill_root / f"{name}.py").exists() or (skill_root / name / "__init__.py").exists():
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _coerce_timeout(value: object) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(timeout, MAX_TIMEOUT_SECONDS))


_SCRIPT_RUNNER_SOURCE = """
import runpy
import sys
from pathlib import Path

skill_root = Path(sys.argv[1]).resolve()
script_file = Path(sys.argv[2]).resolve()
script_dir = script_file.parent
sys.path.insert(0, str(skill_root))
sys.path.insert(0, str(script_dir))
runpy.run_path(str(script_file), run_name="__main__")
""".strip()
