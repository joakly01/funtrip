from __future__ import annotations


def wrap_in_system_reminder(content: str) -> str:
    return f"<system-reminder>\n{content}\n</system-reminder>"
