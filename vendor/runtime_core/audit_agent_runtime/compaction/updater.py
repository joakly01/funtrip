from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

from audit_agent_runtime.compaction.state_service import build_compaction_candidate
from audit_agent_runtime.compaction.tool_result_collapse import render_collapsed_message_text
from audit_agent_runtime.llm.base import ModelClient, ModelMessage
from audit_agent_runtime.persistence.models import SessionCompactionRecord
from audit_agent_runtime.persistence.repositories.compactions import SessionCompactionRepository


@dataclass(slots=True)
class SessionCompactionUpdater:
    model_client: ModelClient
    keep_recent: int = 8
    reason: str = "history_limit"
    min_boundary_advance: int = 2

    def update_compaction_state(
        self,
        *,
        db: Session,
        session_id: str,
        persisted_messages: list[dict],
    ) -> SessionCompactionRecord | None:
        compactions = SessionCompactionRepository(db)
        latest = compactions.get_latest_compaction(session_id)
        candidate = build_compaction_candidate(
            session_id=session_id,
            persisted_messages=persisted_messages,
            keep_recent=self.keep_recent,
        )
        if candidate is None:
            return None
        if (
            latest is not None
            and latest.boundary_message_id is not None
            and candidate.boundary_message_id <= latest.boundary_message_id
        ):
            return None
        if (
            latest is not None
            and latest.boundary_message_id is not None
            and candidate.boundary_message_id - latest.boundary_message_id
            < max(1, int(self.min_boundary_advance))
        ):
            return None

        response = self.model_client.complete(
            self._build_messages(candidate.compacted_messages),
            [],
        )
        summary_text = response.output_text.strip()
        if not summary_text:
            return None

        preserved_from = (
            min(candidate.preserved_message_ids)
            if candidate.preserved_message_ids
            else None
        )
        preserved_to = (
            max(candidate.preserved_message_ids)
            if candidate.preserved_message_ids
            else None
        )
        return compactions.add_compaction(
            compaction_id=str(uuid4()),
            session_id=session_id,
            boundary_message_id=candidate.boundary_message_id,
            summary_text=summary_text,
            preserved_from_message_id=preserved_from,
            preserved_to_message_id=preserved_to,
            reason=self.reason,
        )

    def should_update_compaction_state(
        self,
        *,
        db: Session,
        session_id: str,
        persisted_messages: list[dict],
    ) -> bool:
        compactions = SessionCompactionRepository(db)
        latest = compactions.get_latest_compaction(session_id)
        candidate = build_compaction_candidate(
            session_id=session_id,
            persisted_messages=persisted_messages,
            keep_recent=self.keep_recent,
        )
        if candidate is None:
            return False
        if (
            latest is not None
            and latest.boundary_message_id is not None
            and candidate.boundary_message_id <= latest.boundary_message_id
        ):
            return False
        if (
            latest is not None
            and latest.boundary_message_id is not None
            and candidate.boundary_message_id - latest.boundary_message_id
            < max(1, int(self.min_boundary_advance))
        ):
            return False
        return True

    def _build_messages(self, persisted_messages: list[dict]) -> list[ModelMessage]:
        transcript_lines = []
        for message in persisted_messages:
            role = str(message.get("role", "unknown"))
            text = render_collapsed_message_text(message)
            transcript_lines.append(f"{role}: {text}".strip())
        transcript = "\n".join(line for line in transcript_lines if line.strip())
        return [
            ModelMessage(
                role="system",
                content=(
                    "Summarize the compacted transcript prefix into a concise compaction summary. "
                    "Return plain text only, no markdown fences."
                ),
            ),
            ModelMessage(role="user", content=transcript),
        ]
