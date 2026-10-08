from __future__ import annotations

from audit_agent_runtime.prompts.reminders import wrap_in_system_reminder

COMPACTED_HISTORY_TITLE = "# Compacted History"
COMPACTED_HISTORY_PRECEDENCE_NOTE = (
    "This session is being continued with a compacted summary of earlier "
    "conversation context. Recent transcript messages, if present, take "
    "precedence over this summary."
)
LEGACY_COMPACTION_PREFIX = "Compaction summary:\n"


def render_compacted_history_text(summary: str) -> str:
    return "\n".join(
        [
            COMPACTED_HISTORY_TITLE,
            COMPACTED_HISTORY_PRECEDENCE_NOTE,
            "",
            summary,
        ]
    )


def render_compacted_history_reminder(summary: str) -> str:
    return wrap_in_system_reminder(render_compacted_history_text(summary))


def is_compaction_summary_text(text: str) -> bool:
    return text.startswith(LEGACY_COMPACTION_PREFIX) or COMPACTED_HISTORY_TITLE in text
