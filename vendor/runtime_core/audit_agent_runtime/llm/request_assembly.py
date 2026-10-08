from __future__ import annotations

from dataclasses import dataclass, field
import json

from audit_agent_runtime.llm.base import ModelMessage
from audit_agent_runtime.llm.thinking_policy import ThinkingPolicy
from audit_agent_runtime.utils.json_utils import safe_json_dumps

SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"
ORPHAN_TOOL_RESULT_REMOVED_PLACEHOLDER = (
    "[Orphaned tool result removed due to conversation resume]"
)


@dataclass(slots=True)
class MessageNormalizationResult:
    # 诊断统计只描述 provider payload 组装前做过哪些修复，
    # 方便测试和运行时排查 tool_use/tool_result 配对问题；不改变模型可见内容。
    messages: list[ModelMessage]
    diagnostics: dict[str, int] = field(default_factory=dict)


def _empty_normalization_diagnostics() -> dict[str, int]:
    return {
        "duplicate_tool_calls_removed": 0,
        "duplicate_tool_results_removed": 0,
        "synthetic_tool_results_inserted": 0,
        "orphan_tool_results_removed": 0,
    }


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _content_blocks(content) -> list[dict]:
    if isinstance(content, list):
        normalized_blocks: list[dict] = []
        for block in content:
            if isinstance(block, dict):
                normalized_blocks.append(block)
            else:
                normalized_blocks.append(_text_block("" if block is None else str(block)))
        return normalized_blocks
    return [_text_block("" if content is None else str(content))]


def _text_from_blocks(content, *, separator: str = "") -> str:
    parts = [
        str(block.get("text", ""))
        for block in _content_blocks(content)
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return separator.join(parts)


def _is_system_reminder_content(content) -> bool:
    blocks = _content_blocks(content)
    if not blocks:
        return False
    for block in blocks:
        if block.get("type") != "text":
            return False
        if not str(block.get("text", "")).lstrip().startswith("<system-reminder>"):
            return False
    return True


def _hoist_tool_results(blocks: list[dict]) -> list[dict]:
    tool_results = [block for block in blocks if block.get("type") == "tool_result"]
    other_blocks = [block for block in blocks if block.get("type") != "tool_result"]
    return [*tool_results, *other_blocks]


def _join_user_content_blocks(left_content, right_content) -> list[dict]:
    left = _content_blocks(left_content)
    right = _content_blocks(right_content)
    if left and right and left[-1].get("type") == "text" and right[0].get("type") == "text":
        left = [
            *left[:-1],
            {
                **left[-1],
                "text": f"{left[-1].get('text', '')}\n",
            },
        ]
    return _hoist_tool_results([*left, *right])


def _split_tool_result_blocks(message: ModelMessage) -> list[ModelMessage]:
    if message.role != "user" or not isinstance(message.content, list):
        return [message]

    tool_messages: list[ModelMessage] = []
    remaining_blocks: list[dict] = []
    for block in _content_blocks(message.content):
        if block.get("type") == "tool_result":
            tool_messages.append(
                ModelMessage(
                    role="tool",
                    content=block.get("content", ""),
                    tool_call_id=_optional_text(block.get("tool_use_id")),
                    name=_optional_text(block.get("tool_name")) or "tool_result",
                )
            )
        else:
            remaining_blocks.append(block)

    if not tool_messages:
        return [message]
    if not remaining_blocks:
        return tool_messages
    return [*tool_messages, ModelMessage(role="user", content=remaining_blocks)]


def _repair_tool_result_pairing(
    messages: list[ModelMessage],
    diagnostics: dict[str, int],
) -> list[ModelMessage]:
    repaired: list[ModelMessage] = []
    index = 0
    pending_tool_result_ids: set[str] = set()
    while index < len(messages):
        message = messages[index]

        if message.role != "assistant" or not message.tool_calls:
            if message.role == "user" and isinstance(message.content, list):
                previous = repaired[-1] if repaired else None
                has_pending_expected_tool_results = bool(pending_tool_result_ids)
                if (
                    not has_pending_expected_tool_results
                    and (previous is None or previous.role != "assistant" or not previous.tool_calls)
                ):
                    stripped_blocks = [
                        block
                        for block in message.content
                        if block.get("type") != "tool_result"
                    ]
                    diagnostics["orphan_tool_results_removed"] += (
                        len(message.content) - len(stripped_blocks)
                    )
                    if stripped_blocks:
                        repaired.append(ModelMessage(role="user", content=stripped_blocks))
                    elif not repaired:
                        repaired.append(
                            ModelMessage(
                                role="user",
                                content=[_text_block(ORPHAN_TOOL_RESULT_REMOVED_PLACEHOLDER)],
                            )
                        )
                    index += 1
                    continue
            repaired.append(message)
            index += 1
            continue

        deduped_tool_calls = []
        expected_ids: dict[str, str] = {}
        for tool_call in message.tool_calls:
            if tool_call.call_id in expected_ids:
                diagnostics["duplicate_tool_calls_removed"] += 1
                continue
            expected_ids[tool_call.call_id] = tool_call.tool_name
            deduped_tool_calls.append(tool_call)
        pending_tool_result_ids = set(expected_ids)

        repaired.append(
            ModelMessage(
                role=message.role,
                content=message.content,
                tool_calls=deduped_tool_calls,
                reasoning_content=message.reasoning_content,
            )
        )

        next_message = messages[index + 1] if index + 1 < len(messages) else None
        if next_message and next_message.role == "tool" and next_message.tool_call_id in expected_ids:
            repaired.append(next_message)
            pending_tool_result_ids.discard(next_message.tool_call_id)
            index += 2
            continue
        if next_message and next_message.role == "user" and isinstance(next_message.content, list):
            seen_result_ids: set[str] = set()
            deduped_blocks: list[dict] = []
            matched_ids: set[str] = set()
            orphan_blocks: list[dict] = []
            for block in next_message.content:
                if block.get("type") != "tool_result":
                    deduped_blocks.append(block)
                    continue
                tool_use_id = str(block.get("tool_use_id"))
                if tool_use_id not in expected_ids:
                    orphan_blocks.append(block)
                    continue
                if tool_use_id in seen_result_ids:
                    diagnostics["duplicate_tool_results_removed"] += 1
                    continue
                seen_result_ids.add(tool_use_id)
                matched_ids.add(tool_use_id)
                pending_tool_result_ids.discard(tool_use_id)
                deduped_blocks.append(block)

            missing_ids = [tool_use_id for tool_use_id in expected_ids if tool_use_id not in matched_ids]
            diagnostics["synthetic_tool_results_inserted"] += len(missing_ids)
            synthetic_blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "tool_name": expected_ids[tool_use_id],
                    "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                }
                for tool_use_id in missing_ids
            ]
            if synthetic_blocks or orphan_blocks:
                diagnostics["orphan_tool_results_removed"] += len(orphan_blocks)
                if deduped_blocks or synthetic_blocks:
                    repaired.append(
                        ModelMessage(
                            role="user",
                            content=[*synthetic_blocks, *deduped_blocks],
                        )
                    )
                else:
                    repaired.append(
                        ModelMessage(
                            role="user",
                            content=[_text_block(ORPHAN_TOOL_RESULT_REMOVED_PLACEHOLDER)],
                        )
                    )
                index += 2
                continue
            repaired.append(ModelMessage(role="user", content=deduped_blocks))
            index += 2
            continue

        for tool_call in message.tool_calls:
            diagnostics["synthetic_tool_results_inserted"] += 1
            repaired.append(
                ModelMessage(
                    role="user",
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call.call_id,
                            "tool_name": tool_call.tool_name,
                            "content": SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                        }
                    ],
                )
            )
        pending_tool_result_ids.clear()
        index += 1

    return repaired


def _repair_legacy_tool_messages(
    messages: list[ModelMessage],
    diagnostics: dict[str, int],
) -> list[ModelMessage]:
    repaired: list[ModelMessage] = []
    expected_tool_ids: set[str] = set()
    matched_tool_ids: set[str] = set()
    recent_tool_result_ids: set[str] = set()

    def clear_expected_ids() -> None:
        expected_tool_ids.clear()
        matched_tool_ids.clear()

    def should_keep_leading_placeholder(start_index: int) -> bool:
        for candidate in messages[start_index + 1 :]:
            if candidate.role == "tool":
                continue
            return candidate.role == "assistant"
        return False

    for index, message in enumerate(messages):
        if message.role == "assistant":
            repaired.append(message)
            clear_expected_ids()
            recent_tool_result_ids.clear()
            if message.tool_calls:
                expected_tool_ids.update(
                    tool_call.call_id
                    for tool_call in message.tool_calls
                    if tool_call.call_id
                )
            continue

        if message.role == "tool":
            tool_call_id = message.tool_call_id
            if not tool_call_id or tool_call_id not in expected_tool_ids:
                if tool_call_id and tool_call_id in recent_tool_result_ids:
                    diagnostics["duplicate_tool_results_removed"] += 1
                    continue
                if not repaired and should_keep_leading_placeholder(index):
                    diagnostics["orphan_tool_results_removed"] += 1
                    repaired.append(
                        ModelMessage(
                            role="user",
                            content=[_text_block(ORPHAN_TOOL_RESULT_REMOVED_PLACEHOLDER)],
                        )
                    )
                    continue
                diagnostics["orphan_tool_results_removed"] += 1
                continue
            if tool_call_id in matched_tool_ids:
                diagnostics["duplicate_tool_results_removed"] += 1
                continue
            matched_tool_ids.add(tool_call_id)
            recent_tool_result_ids.add(tool_call_id)
            repaired.append(message)
            if matched_tool_ids >= expected_tool_ids:
                clear_expected_ids()
            continue

        if message.role == "user" and isinstance(message.content, list) and expected_tool_ids:
            for block in message.content:
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id"))
                if tool_use_id in expected_tool_ids:
                    matched_tool_ids.add(tool_use_id)
                    recent_tool_result_ids.add(tool_use_id)
            repaired.append(message)
            if matched_tool_ids >= expected_tool_ids:
                clear_expected_ids()
            continue

        repaired.append(message)
        if message.role != "system":
            clear_expected_ids()
            recent_tool_result_ids.clear()

    return repaired


def normalize_messages_for_api_with_diagnostics(
    messages: list[ModelMessage],
) -> MessageNormalizationResult:
    # OpenAI 与 Claude 共用同一份规范化结果，避免两条 provider wire format
    # 各自修 tool_result 边界后产生不一致的历史重放行为。
    diagnostics = _empty_normalization_diagnostics()
    normalized: list[ModelMessage] = []
    for message in messages:
        content = message.content
        if message.role in {"user", "system"} and isinstance(content, list):
            content = _content_blocks(content)
        message = ModelMessage(
            role=message.role,
            content=content,
            tool_call_id=message.tool_call_id,
            name=message.name,
            tool_calls=message.tool_calls,
            reasoning_content=message.reasoning_content,
        )
        if (
            message.role == "assistant"
            and not message.tool_calls
            and not message.tool_call_id
            and not message.name
            and (message.content is None or message.content == "")
        ):
            continue
        if (
            normalized
            and normalized[-1].role == message.role
            and message.role in {"user", "system"}
            and normalized[-1].tool_call_id is None
            and message.tool_call_id is None
            and normalized[-1].name is None
            and message.name is None
            and not normalized[-1].tool_calls
            and not message.tool_calls
        ):
            # system-reminder 是 runtime 注入的 meta 消息。它可以用 user role 发送，
            # 但不能和真实用户输入合并，否则 wire payload 会模糊消息边界。
            if message.role == "user" and (
                _is_system_reminder_content(normalized[-1].content)
                or _is_system_reminder_content(message.content)
            ):
                normalized.append(message)
                continue
            normalized[-1] = ModelMessage(
                role=message.role,
                content=(
                    _join_user_content_blocks(normalized[-1].content, message.content)
                    if message.role == "user"
                    else [
                        *_content_blocks(normalized[-1].content),
                        *_content_blocks(message.content),
                    ]
                ),
            )
            continue
        if (
            normalized
            and normalized[-1].role == message.role == "assistant"
            and normalized[-1].tool_call_id is None
            and message.tool_call_id is None
            and normalized[-1].name is None
            and message.name is None
            and not normalized[-1].tool_calls
            and not message.tool_calls
        ):
            left = normalized[-1].content
            right = message.content
            normalized[-1] = ModelMessage(
                role="assistant",
                content=f"{'' if left is None else left}{'' if right is None else right}",
                reasoning_content=(
                    f"{normalized[-1].reasoning_content or ''}{message.reasoning_content or ''}"
                    or None
                ),
            )
            continue
        normalized.append(message)
    repaired = _repair_legacy_tool_messages(
        _repair_tool_result_pairing(normalized, diagnostics),
        diagnostics,
    )
    return MessageNormalizationResult(messages=repaired, diagnostics=diagnostics)


def normalize_messages_for_api(messages: list[ModelMessage]) -> list[ModelMessage]:
    return normalize_messages_for_api_with_diagnostics(messages).messages


def serialize_message(message: ModelMessage) -> dict:
    content = message.content
    if message.role in {"user", "system"}:
        content = _content_blocks(content)
    elif message.role == "tool" and not isinstance(content, str):
        content = safe_json_dumps(content, ensure_ascii=False)
    payload = {"role": message.role, "content": content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.call_id,
                "type": "function",
                "function": {
                    "name": tool_call.tool_name,
                    "arguments": safe_json_dumps(
                        tool_call.arguments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for tool_call in message.tool_calls
        ]
    if message.tool_call_id:
        payload["tool_call_id"] = message.tool_call_id
    if message.name:
        payload["name"] = message.name
    if message.reasoning_content and message.role == "assistant":
        payload["reasoning_content"] = message.reasoning_content
    return payload


def serialize_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


_CLAUDE_TOOL_OVERLAY_FIELDS = (
    "strict",
    "eager_input_streaming",
    "defer_loading",
    "cache_control",
)


def serialize_claude_tool(tool: dict) -> dict:
    payload = {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": tool.get(
            "input_schema",
            {"type": "object", "properties": {}},
        ),
    }
    for field in _CLAUDE_TOOL_OVERLAY_FIELDS:
        if field in tool:
            payload[field] = tool[field]
    return payload


def _normalized_claude_messages(
    messages: list[ModelMessage],
) -> tuple[list[ModelMessage], list[dict], list[dict]]:
    normalized_messages = normalize_messages_for_api_with_diagnostics(messages).messages
    system_blocks: list[dict] = []
    serialized_messages: list[dict] = []
    for message in normalized_messages:
        if message.role == "system":
            system_blocks.extend(_content_blocks(message.content))
            continue
        serialized = _serialize_claude_message(message)
        if serialized is not None:
            serialized_messages.append(serialized)
    return normalized_messages, system_blocks, serialized_messages


def _serialize_claude_tool_result_block(block: dict) -> dict:
    payload = {
        "type": "tool_result",
        "tool_use_id": _optional_text(block.get("tool_use_id")) or "",
        "content": block.get("content", ""),
        "is_error": bool(block.get("is_error", False)),
    }
    return payload


def _serialize_claude_message(message: ModelMessage) -> dict | None:
    if message.role == "system":
        return None
    if message.role == "assistant":
        content_blocks: list[dict] = []
        if isinstance(message.content, list):
            content_blocks.extend(_content_blocks(message.content))
        elif message.content not in {None, ""}:
            content_blocks.append(_text_block(str(message.content)))
        for tool_call in message.tool_calls or []:
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call.call_id,
                    "name": tool_call.tool_name,
                    "input": tool_call.arguments,
                }
            )
        if not content_blocks:
            return None
        return {
            "role": "assistant",
            "content": content_blocks,
        }
    if message.role == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": message.content,
                    "is_error": False,
                }
            ],
        }
    if message.role == "user":
        content_blocks: list[dict] = []
        for block in _content_blocks(message.content):
            if block.get("type") == "tool_result":
                content_blocks.append(_serialize_claude_tool_result_block(block))
            else:
                content_blocks.append(block)
        return {
            "role": "user",
            "content": content_blocks,
        }
    return {
        "role": message.role,
        "content": _content_blocks(message.content),
    }


def build_claude_messages_payload(
    *,
    model_name: str,
    messages: list[ModelMessage],
    tools: list[dict],
    metadata: dict | None = None,
    max_tokens: int = 32000,
    task_budget: object = None,
    enable_thinking: bool = False,
    thinking_budget: int | None = None,
    stream: bool = False,
) -> dict:
    _, system_blocks, serialized_messages = _normalized_claude_messages(messages)
    payload = {
        "model": model_name,
        "system": system_blocks,
        "messages": serialized_messages,
        "tools": [serialize_claude_tool(tool) for tool in tools],
        "metadata": metadata or {},
        "max_tokens": max_tokens,
        "stream": stream,
    }
    serialized_task_budget = _serialize_claude_task_budget(task_budget)
    if serialized_task_budget is not None:
        payload["output_config"] = {"task_budget": serialized_task_budget}
    serialized_thinking = _serialize_claude_thinking(enable_thinking, thinking_budget)
    if serialized_thinking is not None:
        payload["thinking"] = serialized_thinking
    return payload


def _serialize_claude_task_budget(task_budget: object) -> dict | None:
    if isinstance(task_budget, int):
        if task_budget < 0:
            return None
        return {"type": "tokens", "total": task_budget}
    if not isinstance(task_budget, dict):
        return None
    total = task_budget.get("total")
    remaining = task_budget.get("remaining")
    if not isinstance(total, int) or total < 0:
        return None
    payload = {"type": "tokens", "total": total}
    if isinstance(remaining, int) and remaining >= 0:
        payload["remaining"] = remaining
    return payload


def build_openai_chat_payload(
    *,
    model_name: str,
    messages: list[ModelMessage],
    tools: list[dict],
    enable_thinking: bool = False,
    thinking_budget: int | None = None,
    stream: bool = False,
    thinking_policy: ThinkingPolicy | None = None,
) -> dict:
    normalized_messages, system_blocks, _ = _normalized_claude_messages(messages)
    effective_thinking_mode = "enabled" if enable_thinking else "disabled"
    if thinking_policy is not None:
        effective_thinking_mode = thinking_policy.effective_mode(normalized_messages)
        normalized_messages = thinking_policy.normalize_messages(
            normalized_messages,
            mode=effective_thinking_mode,
        )
    openai_messages: list[dict] = []
    if system_blocks:
        openai_messages.append(
            {"role": "system", "content": _text_from_blocks(system_blocks, separator="\n\n")}
        )
    for normalized_message in normalized_messages:
        if normalized_message.role == "system":
            continue
        if normalized_message.role == "assistant":
            assistant_message = {
                "role": "assistant",
                "content": "",
            }
            if isinstance(normalized_message.content, list):
                text_parts = [
                    block.get("text", "")
                    for block in normalized_message.content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                if text_parts:
                    assistant_message["content"] = "".join(str(part) for part in text_parts)
            elif normalized_message.content not in {None, ""}:
                assistant_message["content"] = str(normalized_message.content)
            if normalized_message.tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tool_call.call_id,
                        "type": "function",
                        "function": {
                            "name": tool_call.tool_name,
                            "arguments": safe_json_dumps(
                                tool_call.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for tool_call in normalized_message.tool_calls
                ]
            if effective_thinking_mode == "enabled" and normalized_message.reasoning_content:
                assistant_message["reasoning_content"] = normalized_message.reasoning_content
            openai_messages.append(assistant_message)
            continue
        if normalized_message.role == "user":
            content_blocks = _content_blocks(normalized_message.content)
            current_text_blocks: list[dict] = []
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    if current_text_blocks:
                        openai_messages.append(
                            {"role": "user", "content": current_text_blocks}
                        )
                        current_text_blocks = []
                    openai_messages.append(
                        {
                            "role": "tool",
                            "content": (
                                block.get("content")
                                if isinstance(block.get("content"), str)
                                else safe_json_dumps(
                                    block.get("content", ""),
                                    ensure_ascii=False,
                                )
                            ),
                            "tool_call_id": block.get("tool_use_id", ""),
                            "name": _optional_text(block.get("tool_name")) or "tool_result",
                        }
                    )
                    continue
                current_text_blocks.append(block)
            if current_text_blocks:
                openai_messages.append({"role": "user", "content": current_text_blocks})
            continue
        openai_messages.append(serialize_message(normalized_message))

    payload = {
        "model": model_name,
        "messages": openai_messages,
    }
    if tools:
        payload["tools"] = [serialize_tool(tool) for tool in tools]
    thinking_payload = (
        thinking_policy.request_payload(effective_thinking_mode)
        if thinking_policy is not None
        else None
    )
    if thinking_payload is not None:
        payload["thinking"] = thinking_payload
    elif effective_thinking_mode == "enabled":
        payload["enable_thinking"] = True
        if isinstance(thinking_budget, int) and thinking_budget >= 0:
            payload["thinking_budget"] = thinking_budget
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    return payload


def _serialize_claude_thinking(
    enable_thinking: bool,
    thinking_budget: int | None,
) -> dict | None:
    if not enable_thinking:
        return None
    payload: dict[str, object] = {"type": "enabled"}
    if isinstance(thinking_budget, int) and thinking_budget >= 0:
        payload["budget_tokens"] = thinking_budget
    return payload
