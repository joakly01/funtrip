from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass(slots=True)
class DialogState:
    status: str = "idle"
    plan_mode: bool = False
    pending_user_question: str | None = None
    missing_params: list[str] = field(default_factory=list)
    pending_params: JsonDict = field(default_factory=dict)
    awaiting_task_id: str | None = None
    active_agent_id: str | None = None
    active_skill_ids: list[str] = field(default_factory=list)
    active_skill_hints: JsonDict = field(default_factory=dict)

    def to_payload(self) -> JsonDict:
        return {
            "status": self.status,
            "plan_mode": self.plan_mode,
            "pending_user_question": self.pending_user_question,
            "missing_params": list(self.missing_params),
            "pending_params": dict(self.pending_params),
            "awaiting_task_id": self.awaiting_task_id,
            "active_agent_id": self.active_agent_id,
            "active_skill_ids": list(self.active_skill_ids),
            "active_skill_hints": dict(self.active_skill_hints),
        }

    @classmethod
    def from_payload(cls, payload: JsonDict | None) -> "DialogState":
        source = payload or {}
        return cls(
            status=_normalize_status(source.get("status", "idle")),
            plan_mode=_normalize_bool(source.get("plan_mode", False)),
            pending_user_question=_normalize_optional_text(
                source.get("pending_user_question")
            ),
            missing_params=_normalize_string_list(source.get("missing_params", [])),
            pending_params=_normalize_dict(source.get("pending_params", {})),
            awaiting_task_id=_normalize_optional_text(source.get("awaiting_task_id")),
            active_agent_id=_normalize_optional_text(source.get("active_agent_id")),
            active_skill_ids=_normalize_string_list(source.get("active_skill_ids", [])),
            active_skill_hints=_normalize_dict(source.get("active_skill_hints", {})),
        )


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_status(value: object) -> str:
    normalized = _normalize_optional_text(value)
    return normalized or "idle"


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return False


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def _normalize_dict(value: object) -> JsonDict:
    return dict(value) if isinstance(value, dict) else {}
