from __future__ import annotations

from dataclasses import dataclass, field
import json

from audit_agent_runtime.llm.base import ModelMessage
from audit_agent_runtime.querying.tool_result_collapse import (
    DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS,
    DEFAULT_MODEL_TOOL_RESULT_PREVIEW_CHARS,
    collapse_tool_result_for_model,
)
from audit_agent_runtime.querying.tool_result_policy import (
    CLEARED_OLD_TOOL_RESULT_CONTENT,
    DEFAULT_TOOL_RESULT_POLICY_REGISTRY,
    ToolResultPolicyRegistry,
)
from audit_agent_runtime.utils.json_utils import safe_json_dumps


@dataclass(slots=True)
class ToolResultBudgetState:
    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, dict] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResultBudgetApplication:
    messages: list[ModelMessage]
    stats: dict


def serialize_tool_result_budget_state(state: ToolResultBudgetState) -> dict:
    # 持久化的是“模型已经看过什么/替换成什么”，不是完整工具结果。
    # 完整结果仍由 messages/session_events 保存，预算状态只服务于恢复后的字节稳定。
    return {
        "seen_ids": sorted(state.seen_ids),
        "replacements": {
            tool_use_id: replacement
            for tool_use_id, replacement in sorted(state.replacements.items())
            if isinstance(tool_use_id, str) and isinstance(replacement, dict)
        },
    }


def deserialize_tool_result_budget_state(payload: object) -> ToolResultBudgetState:
    if not isinstance(payload, dict):
        return ToolResultBudgetState()
    raw_seen_ids = payload.get("seen_ids", [])
    raw_replacements = payload.get("replacements", {})
    seen_ids = {
        item.strip()
        for item in raw_seen_ids
        if isinstance(item, str) and item.strip()
    } if isinstance(raw_seen_ids, list) else set()
    replacements = {
        tool_use_id: replacement
        for tool_use_id, replacement in raw_replacements.items()
        if isinstance(tool_use_id, str)
        and tool_use_id.strip()
        and isinstance(replacement, dict)
    } if isinstance(raw_replacements, dict) else {}
    seen_ids.update(replacements)
    return ToolResultBudgetState(seen_ids=seen_ids, replacements=replacements)


@dataclass(slots=True)
class _ToolResultCandidate:
    tool_use_id: str
    tool_name: str
    content: dict
    size: int


@dataclass(slots=True)
class _ToolResultClearCandidate:
    tool_use_id: str
    tool_name: str
    content: object


def apply_tool_result_message_budget(
    messages: list[ModelMessage],
    *,
    state: ToolResultBudgetState,
    max_chars: int = DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS,
    preview_chars: int = DEFAULT_MODEL_TOOL_RESULT_PREVIEW_CHARS,
    policy_registry: ToolResultPolicyRegistry = DEFAULT_TOOL_RESULT_POLICY_REGISTRY,
) -> list[ModelMessage]:
    return apply_tool_result_message_budget_with_stats(
        messages,
        state=state,
        max_chars=max_chars,
        preview_chars=preview_chars,
        policy_registry=policy_registry,
    ).messages


def apply_tool_result_message_budget_with_stats(
    messages: list[ModelMessage],
    *,
    state: ToolResultBudgetState,
    max_chars: int = DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS,
    preview_chars: int = DEFAULT_MODEL_TOOL_RESULT_PREVIEW_CHARS,
    policy_registry: ToolResultPolicyRegistry = DEFAULT_TOOL_RESULT_POLICY_REGISTRY,
) -> ToolResultBudgetApplication:
    # Claude Code 的 contentReplacementState 是按 tool_use_id 回放的稳定投影。
    # 即使当前历史里的工具结果已经被其它策略折叠，也必须先按 id 套回
    # 已持久化 replacement，避免 resume 后模型看到不同版本的工具结果。
    replacement_by_id: dict[str, dict] = _existing_replacements_for_messages(
        messages,
        state=state,
    )
    replacement_ids: list[str] = []
    seen_ids_this_call: set[str] = set()
    for group in _collect_candidate_groups(messages):
        fresh = [candidate for candidate in group if candidate.tool_use_id not in state.seen_ids]
        for candidate in group:
            replacement = state.replacements.get(candidate.tool_use_id)
            if replacement is not None:
                replacement_by_id[candidate.tool_use_id] = replacement
        if not fresh:
            continue
        total_size = sum(candidate.size for candidate in group)
        if total_size <= max_chars:
            for candidate in fresh:
                state.seen_ids.add(candidate.tool_use_id)
                seen_ids_this_call.add(candidate.tool_use_id)
            continue

        # 聚合预算只处理可安全缩略的结果。报告导出和错误结果通常已经是
        # 小型状态/诊断信息，压成 preview 反而会丢掉用户可见的关键结论。
        eligible_fresh = [
            candidate
            for candidate in fresh
            if _is_collapsible_tool_result(candidate, policy_registry=policy_registry)
        ]
        selected = _select_candidates_to_replace(eligible_fresh, total_size, max_chars)
        selected_ids = {candidate.tool_use_id for candidate in selected}
        for candidate in group:
            if candidate.tool_use_id in selected_ids:
                replacement = _build_aggregate_replacement(
                    candidate,
                    preview_chars=preview_chars,
                )
                state.replacements[candidate.tool_use_id] = replacement
                replacement_by_id[candidate.tool_use_id] = replacement
                replacement_ids.append(candidate.tool_use_id)
            state.seen_ids.add(candidate.tool_use_id)
            seen_ids_this_call.add(candidate.tool_use_id)

    stats = {
        "replaced_count": len(replacement_ids),
        "replacement_ids": replacement_ids,
        "seen_count": len(seen_ids_this_call),
    }
    if not replacement_by_id:
        return ToolResultBudgetApplication(messages=messages, stats=stats)
    return ToolResultBudgetApplication(
        messages=_replace_tool_result_contents(messages, replacement_by_id),
        stats=stats,
    )


def _existing_replacements_for_messages(
    messages: list[ModelMessage],
    *,
    state: ToolResultBudgetState,
) -> dict[str, dict]:
    visible_ids = _collect_tool_result_ids(messages)
    return {
        tool_use_id: replacement
        for tool_use_id, replacement in state.replacements.items()
        if tool_use_id in visible_ids
    }


def _collect_tool_result_ids(messages: list[ModelMessage]) -> set[str]:
    tool_result_ids: set[str] = set()
    for message in messages:
        if message.role == "tool" and isinstance(message.tool_call_id, str):
            tool_result_ids.add(message.tool_call_id)
            continue
        if message.role != "user" or not isinstance(message.content, list):
            continue
        for block in message.content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if isinstance(tool_use_id, str) and tool_use_id.strip():
                tool_result_ids.add(tool_use_id)
    return tool_result_ids


def clear_old_tool_results(
    messages: list[ModelMessage],
    *,
    keep_recent: int,
    policy_registry: ToolResultPolicyRegistry = DEFAULT_TOOL_RESULT_POLICY_REGISTRY,
) -> list[ModelMessage]:
    return clear_old_tool_results_with_stats(
        messages,
        keep_recent=keep_recent,
        policy_registry=policy_registry,
    ).messages


def clear_old_tool_results_with_stats(
    messages: list[ModelMessage],
    *,
    keep_recent: int,
    policy_registry: ToolResultPolicyRegistry = DEFAULT_TOOL_RESULT_POLICY_REGISTRY,
) -> ToolResultBudgetApplication:
    # Claude Code microcompact 的核心是不删除 tool_use/tool_result 配对，
    # 只替换较旧结果的 content；这里再叠加审计 runtime 的业务 envelope 策略，
    # 避免把表格续查句柄、报告导出状态、错误诊断一起清掉。
    keep_count = max(1, int(keep_recent))
    candidates = _collect_tool_result_clear_candidates(messages)
    old_candidates = candidates[:-keep_count]
    replacement_by_id: dict[str, dict | str] = {}
    cleared_ids: list[str] = []
    skipped_ids: list[str] = []
    for candidate in old_candidates:
        replacement = _build_old_tool_result_replacement(
            candidate,
            policy_registry=policy_registry,
        )
        if replacement is None:
            skipped_ids.append(candidate.tool_use_id)
            continue
        replacement_by_id[candidate.tool_use_id] = replacement
        cleared_ids.append(candidate.tool_use_id)
    stats = {
        "cleared_count": len(cleared_ids),
        "kept_count": max(0, len(candidates) - len(cleared_ids)),
        "cleared_ids": cleared_ids,
        "skipped_count": len(skipped_ids),
        "skipped_ids": skipped_ids,
        "keep_recent": keep_count,
    }
    if not replacement_by_id:
        return ToolResultBudgetApplication(messages=messages, stats=stats)
    return ToolResultBudgetApplication(
        messages=_replace_tool_result_contents(
            messages,
            replacement_by_id,
        ),
        stats=stats,
    )


def _collect_tool_result_clear_candidates(
    messages: list[ModelMessage],
) -> list[_ToolResultClearCandidate]:
    candidates: list[_ToolResultClearCandidate] = []
    for message in messages:
        if message.role == "tool" and isinstance(message.tool_call_id, str):
            content: object = message.content
            if isinstance(message.content, str):
                parsed = _parse_tool_content_dict(message.content)
                if parsed is not None:
                    content = parsed
            if _is_already_cleared_tool_result_content(content):
                continue
            candidates.append(
                _ToolResultClearCandidate(
                    tool_use_id=message.tool_call_id,
                    tool_name=_normalized_tool_name(message.name),
                    content=content,
                )
            )
            continue
        if message.role != "user" or not isinstance(message.content, list):
            continue
        for block in message.content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            if isinstance(tool_use_id, str) and tool_use_id.strip():
                content = block.get("content")
                if _is_already_cleared_tool_result_content(content):
                    continue
                candidates.append(
                    _ToolResultClearCandidate(
                        tool_use_id=tool_use_id,
                        tool_name=_normalized_tool_name(block.get("tool_name")),
                        content=content,
                    )
                )
    return candidates


def _build_old_tool_result_replacement(
    candidate: _ToolResultClearCandidate,
    *,
    policy_registry: ToolResultPolicyRegistry,
) -> dict | str | None:
    return policy_registry.build_old_result_replacement(
        tool_use_id=candidate.tool_use_id,
        tool_name=candidate.tool_name,
        content=candidate.content,
    )


def _is_already_cleared_tool_result_content(content: object) -> bool:
    return content == CLEARED_OLD_TOOL_RESULT_CONTENT or (
        isinstance(content, dict)
        and content.get("cleared") is True
        and content.get("clear_reason") == "old_tool_result"
    )


def _collect_candidate_groups(messages: list[ModelMessage]) -> list[list[_ToolResultCandidate]]:
    groups: list[list[_ToolResultCandidate]] = []
    current_group: list[_ToolResultCandidate] = []

    def flush() -> None:
        nonlocal current_group
        if current_group:
            groups.append(current_group)
            current_group = []

    for message in messages:
        if message.role == "assistant":
            flush()
            continue
        if message.role == "tool":
            candidate = _candidate_from_tool_message(message)
            if candidate is not None:
                current_group.append(candidate)
            continue
        if message.role != "user" or not isinstance(message.content, list):
            continue
        for block in message.content:
            candidate = _candidate_from_block(block)
            if candidate is not None:
                current_group.append(candidate)
    flush()
    return groups


def _candidate_from_tool_message(message: ModelMessage) -> _ToolResultCandidate | None:
    if not isinstance(message.content, str):
        return None
    content = _parse_tool_content_dict(message.content)
    if content is None or content.get("collapsed") is True:
        return None
    tool_use_id = message.tool_call_id
    if not isinstance(tool_use_id, str) or not tool_use_id.strip():
        return None
    return _ToolResultCandidate(
        tool_use_id=tool_use_id,
        tool_name=_normalized_tool_name(message.name),
        content=content,
        size=len(_stable_json(content)),
    )


def _candidate_from_block(block: object) -> _ToolResultCandidate | None:
    if not isinstance(block, dict) or block.get("type") != "tool_result":
        return None
    content = block.get("content")
    if not isinstance(content, dict) or content.get("collapsed") is True:
        return None
    tool_use_id = block.get("tool_use_id")
    if not isinstance(tool_use_id, str) or not tool_use_id.strip():
        return None
    tool_name = block.get("tool_name")
    serialized = _stable_json(content)
    return _ToolResultCandidate(
        tool_use_id=tool_use_id,
        tool_name=_normalized_tool_name(tool_name),
        content=content,
        size=len(serialized),
    )


def _normalized_tool_name(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else "tool_result"


def _select_candidates_to_replace(
    candidates: list[_ToolResultCandidate],
    total_size: int,
    max_chars: int,
) -> list[_ToolResultCandidate]:
    selected: list[_ToolResultCandidate] = []
    remaining = total_size
    for candidate in sorted(candidates, key=lambda item: item.size, reverse=True):
        if remaining <= max_chars:
            break
        selected.append(candidate)
        remaining -= candidate.size
    return selected


def _is_collapsible_tool_result(
    candidate: _ToolResultCandidate,
    *,
    policy_registry: ToolResultPolicyRegistry,
) -> bool:
    return policy_registry.should_collapse_for_budget(
        tool_name=candidate.tool_name,
        content=candidate.content,
    )


def _build_aggregate_replacement(
    candidate: _ToolResultCandidate,
    *,
    preview_chars: int,
) -> dict:
    collapsed = collapse_tool_result_for_model(
        tool_name=candidate.tool_name,
        result=candidate.content,
        tool_call_id=candidate.tool_use_id,
        max_chars=0,
        preview_chars=preview_chars,
    )
    collapsed["collapse_reason"] = "aggregate_tool_result_budget"
    collapsed["tool_use_id"] = candidate.tool_use_id
    return collapsed


def _replace_tool_result_contents(
    messages: list[ModelMessage],
    replacement_by_id: dict[str, dict | str],
) -> list[ModelMessage]:
    replaced: list[ModelMessage] = []
    for message in messages:
        if message.role != "user" or not isinstance(message.content, list):
            replacement = replacement_by_id.get(message.tool_call_id or "")
            if message.role == "tool" and replacement is not None:
                replaced.append(
                    ModelMessage(
                        role=message.role,
                        content=(
                            replacement
                            if isinstance(replacement, str)
                            else safe_json_dumps(replacement, ensure_ascii=False)
                        ),
                        tool_call_id=message.tool_call_id,
                        name=message.name,
                        tool_calls=message.tool_calls,
                    )
                )
                continue
            replaced.append(message)
            continue
        touched = False
        content = []
        for block in message.content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                content.append(block)
                continue
            replacement = replacement_by_id.get(str(block.get("tool_use_id", "")))
            if replacement is None:
                content.append(block)
                continue
            touched = True
            content.append({**block, "content": replacement})
        if not touched:
            replaced.append(message)
            continue
        replaced.append(
            ModelMessage(
                role=message.role,
                content=content,
                tool_call_id=message.tool_call_id,
                name=message.name,
                tool_calls=message.tool_calls,
            )
        )
    return replaced


def _parse_tool_content_dict(content: str) -> dict | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _stable_json(value: dict) -> str:
    return safe_json_dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
