from __future__ import annotations

from typing import Any


_WORKFLOW_STATE_CACHE: dict[str, dict[str, Any]] = {}
_VALIDATOR_RESULT_CACHE: dict[str, dict[str, dict[str, Any]]] = {}


def session_cache_key(context) -> str | None:
    session_id = getattr(context, "session_id", None)
    if not isinstance(session_id, str):
        return None
    normalized = session_id.strip()
    return normalized or None


def load_workflow_state(context) -> dict[str, Any]:
    session_key = session_cache_key(context)
    if session_key is None:
        return {}
    cached = _WORKFLOW_STATE_CACHE.get(session_key)
    return dict(cached) if isinstance(cached, dict) else {}


def save_workflow_state(
    context,
    *,
    risk_tag: str,
    model_name: str,
    confirmed_params: dict[str, Any],
    validation_results: dict[str, Any],
) -> None:
    session_key = session_cache_key(context)
    if session_key is None:
        return
    _WORKFLOW_STATE_CACHE[session_key] = {
        "risk_tag": risk_tag,
        "model_name": model_name,
        "confirmed_params": dict(confirmed_params),
        "validation_results": dict(validation_results),
    }


def cache_validator_result(context, tool_name: str, candidate_value: str, result: dict[str, Any]) -> None:
    session_key = session_cache_key(context)
    if session_key is None or not candidate_value:
        return
    tool_cache = _VALIDATOR_RESULT_CACHE.setdefault(session_key, {})
    tool_cache[f"{tool_name}:{candidate_value}"] = dict(result)


def load_cached_validator_result(context, tool_name: str, candidate_value: str) -> dict[str, Any] | None:
    session_key = session_cache_key(context)
    if session_key is None or not candidate_value:
        return None
    tool_cache = _VALIDATOR_RESULT_CACHE.get(session_key) or {}
    cached = tool_cache.get(f"{tool_name}:{candidate_value}")
    return dict(cached) if isinstance(cached, dict) else None
