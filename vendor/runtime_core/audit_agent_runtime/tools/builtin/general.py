from __future__ import annotations

from pathlib import Path
import fnmatch
import os
import re
import subprocess

from audit_agent_runtime.tools.core.base import BaseTool


def _resolve_root(path: str | None, context=None) -> Path:
    working_directory = getattr(context, "working_directory", None) if context is not None else None
    if path:
        target = Path(path)
        if target.is_absolute():
            return target.resolve()
        if working_directory:
            return (Path(working_directory) / target).resolve()
        return target.resolve()
    if working_directory:
        return Path(working_directory).resolve()
    return Path.cwd().resolve()


def _resolve_target_file(path: str, context=None) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target.resolve()
    working_directory = getattr(context, "working_directory", None) if context is not None else None
    if working_directory:
        return (Path(working_directory) / target).resolve()
    return target.resolve()


def _truncate_text(text: str, max_chars: int = 12000) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _expand_brace_patterns(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]
    options = [option.strip() for option in match.group(1).split(",") if option.strip()]
    if not options:
        return [pattern]
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    expanded: list[str] = []
    for option in options:
        expanded.extend(_expand_brace_patterns(f"{prefix}{option}{suffix}"))
    return expanded


def _match_any_pattern(relative_path: str, file_name: str, patterns: list[str]) -> bool:
    for candidate in patterns:
        expanded_candidates = [candidate]
        if candidate.startswith("**/"):
            expanded_candidates.append(candidate[3:])
        for expanded in expanded_candidates:
            if fnmatch.fnmatch(relative_path, expanded) or fnmatch.fnmatch(file_name, expanded):
                return True
    return False


class ReadTool(BaseTool):
    name = "Read"
    description = "Read a text file, optionally limited to a line range"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "required": ["path"],
    }

    def execute(self, arguments: dict, context) -> dict:
        target = _resolve_target_file(arguments["path"], context)
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start_line = max(int(arguments.get("start_line", 1)), 1)
        end_line = int(arguments.get("end_line", len(lines))) if lines else 0
        selected = lines[start_line - 1 : end_line]
        content = "\n".join(selected)
        content, truncated = _truncate_text(content)
        return {
            "path": str(target),
            "start_line": start_line,
            "end_line": end_line,
            "content": content,
            "truncated": truncated,
        }


class GlobTool(BaseTool):
    name = "Glob"
    description = "Find files under a directory using a glob-style pattern"
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["pattern"],
    }

    def execute(self, arguments: dict, context) -> dict:
        root = _resolve_root(arguments.get("path"), context)
        pattern = arguments["pattern"]
        patterns = _expand_brace_patterns(pattern)
        limit = max(int(arguments.get("limit", 100)), 1)

        matches: list[str] = []
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(root).as_posix()
            if _match_any_pattern(relative, file_path.name, patterns):
                matches.append(str(file_path))
                if len(matches) >= limit:
                    break

        return {
            "root": str(root),
            "pattern": pattern,
            "paths": matches,
            "num_files": len(matches),
            "truncated": len(matches) >= limit,
        }


class GrepTool(BaseTool):
    name = "Grep"
    description = "Search file contents with a regex pattern"
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "case_insensitive": {"type": "boolean"},
            "head_limit": {"type": "integer"},
        },
        "required": ["pattern"],
    }

    def execute(self, arguments: dict, context) -> dict:
        root = _resolve_root(arguments.get("path"), context)
        pattern = arguments["pattern"]
        glob_pattern = arguments.get("glob", "*")
        head_limit = max(int(arguments.get("head_limit", 100)), 1)
        flags = re.IGNORECASE if arguments.get("case_insensitive") else 0
        regex = re.compile(pattern, flags)

        matches: list[dict] = []
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(root).as_posix()
            if not (fnmatch.fnmatch(relative, glob_pattern) or fnmatch.fnmatch(file_path.name, glob_pattern)):
                continue
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "path": str(file_path),
                            "line_number": index,
                            "line": line,
                        }
                    )
                    if len(matches) >= head_limit:
                        return {
                            "root": str(root),
                            "pattern": pattern,
                            "matches": matches,
                            "num_matches": len(matches),
                            "truncated": True,
                        }
        return {
            "root": str(root),
            "pattern": pattern,
            "matches": matches,
            "num_matches": len(matches),
            "truncated": False,
        }


class BashTool(BaseTool):
    name = "Bash"
    description = "Run a shell command in the current environment"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "cwd": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["command"],
    }

    def execute(self, arguments: dict, context) -> dict:
        command = arguments["command"]
        cwd = str(_resolve_root(arguments.get("cwd"), context))
        timeout = max(int(arguments.get("timeout", 20)), 1)

        if os.name == "nt":
            shell_command = [
                "powershell",
                "-NoProfile",
                "-Command",
                command,
            ]
        else:
            shell_command = ["bash", "-lc", command]

        completed = subprocess.run(
            shell_command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout, stdout_truncated = _truncate_text(completed.stdout)
        stderr, stderr_truncated = _truncate_text(completed.stderr)
        return {
            "command": command,
            "cwd": cwd,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }


def build_general_tools() -> list[BaseTool]:
    return [ReadTool(), GrepTool(), GlobTool(), BashTool()]
