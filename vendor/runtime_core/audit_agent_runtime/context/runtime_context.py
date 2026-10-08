from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import os
from pathlib import Path
import platform
import subprocess


MAX_STATUS_CHARS = 2000


def get_system_context(working_directory: str | None) -> dict[str, str]:
    environment = get_environment_info(working_directory)
    git_status = get_git_status(working_directory)
    context: dict[str, str] = {"environment": environment}
    if git_status:
        context["gitStatus"] = git_status
    return context


def get_user_context(working_directory: str | None) -> dict[str, str]:
    context = {
        "currentDate": f"Today's date is {datetime.now().date().isoformat()}.",
        "responseLanguage": "Use Chinese for natural-language responses unless the user explicitly asks for another language.",
    }
    claude_md = get_claude_md_content(working_directory)
    if claude_md:
        context["claudeMd"] = claude_md
    return context


@lru_cache(maxsize=32)
def get_claude_md_content(working_directory: str | None) -> str | None:
    root = _resolve_working_directory(working_directory)
    if root is None:
        return None
    for directory in [root, *root.parents]:
        candidate = directory / "CLAUDE.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or None
    return None


@lru_cache(maxsize=32)
def get_git_status(working_directory: str | None) -> str | None:
    root = _resolve_working_directory(working_directory)
    if root is None:
        return None
    try:
        is_git = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if is_git.returncode != 0 or is_git.stdout.strip() != "true":
            return None
        branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
        main_branch = _git(["symbolic-ref", "refs/remotes/origin/HEAD"], root)
        status = _git(["--no-optional-locks", "status", "--short"], root)
        recent_commits = _git(["--no-optional-locks", "log", "--oneline", "-n", "5"], root)
    except OSError:
        return None

    if len(status) > MAX_STATUS_CHARS:
        status = (
            status[:MAX_STATUS_CHARS]
            + '\n... (truncated because it exceeds 2k characters. If you need more information, run "git status" using BashTool)'
        )
    branch_line = branch or "(unknown)"
    main_branch_line = main_branch.rsplit("/", 1)[-1] if main_branch else "(unknown)"
    return "\n\n".join(
        [
            "This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.",
            f"Current branch: {branch_line}",
            f"Main branch (you will usually use this for PRs): {main_branch_line}",
            f"Status:\n{status or '(clean)'}",
            f"Recent commits:\n{recent_commits}",
        ]
    )


@lru_cache(maxsize=32)
def get_environment_info(working_directory: str | None) -> str:
    root = _resolve_working_directory(working_directory)
    resolved_working_directory = str(root) if root is not None else "(not provided)"
    is_git_repo = "True" if get_git_status(working_directory) else "False"
    env_items = [
        f"Working directory: {resolved_working_directory}",
        f"Is a git repository: {is_git_repo}",
        f"Platform: {platform.system()} ({os.name})",
        f"Shell: {_detect_shell()}",
        f"OS Version: {platform.platform()}",
    ]
    return "\n".join(
        [
            "# Environment",
            "You have been invoked in the following environment:",
            *(f"- {item}" for item in env_items),
        ]
    )


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _resolve_working_directory(working_directory: str | None) -> Path | None:
    if not working_directory:
        return None
    path = Path(working_directory).expanduser()
    if not path.exists():
        return None
    return path if path.is_dir() else path.parent


def _detect_shell() -> str:
    shell = os.environ.get("SHELL")
    if shell:
        return shell
    if os.name == "nt":
        if os.environ.get("PSModulePath"):
            return "powershell"
        return os.environ.get("COMSPEC", "cmd.exe")
    return "unknown"
