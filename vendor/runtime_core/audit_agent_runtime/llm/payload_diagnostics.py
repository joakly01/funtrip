from __future__ import annotations

import json
import logging

_NORMALIZATION_DIAGNOSTIC_KEYS = (
    "duplicate_tool_calls_removed",
    "duplicate_tool_results_removed",
    "synthetic_tool_results_inserted",
    "orphan_tool_results_removed",
)


def compact_provider_payload_diagnostics(
    normalization_diagnostics: dict[str, object],
) -> dict | None:
    # 只持久化计数，不保存 prompt、message content 或 tool result 内容。
    # 这些诊断用于定位 provider payload 修复是否发生，不能变成新的上下文泄漏面。
    normalization = {
        key: int(value)
        for key in _NORMALIZATION_DIAGNOSTIC_KEYS
        if isinstance((value := normalization_diagnostics.get(key)), int) and value > 0
    }
    if not normalization:
        return None
    return {
        "schema_version": 1,
        "normalization": normalization,
    }


def dump_log_payload(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def log_model_request_payload(
    logger: logging.Logger,
    *,
    label: str,
    payload: dict,
) -> None:
    logger.info(
        "%s summary: %s",
        label,
        dump_log_payload(summarize_model_request_payload(payload)),
    )
    logger.info("%s payload: %s", label, dump_log_payload(payload))


def summarize_model_request_payload(payload: dict) -> dict:
    messages = payload.get("messages")
    tools = payload.get("tools")
    system = payload.get("system")
    return {
        "model": payload.get("model"),
        "stream": bool(payload.get("stream", False)),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "system": _summarize_system(system),
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "tool_names": _tool_names(tools),
        "thinking_enabled": bool(payload.get("enable_thinking"))
        or bool(payload.get("thinking")),
        "has_task_budget": "output_config" in payload,
    }


def _summarize_system(system: object) -> dict:
    if isinstance(system, str):
        return {"kind": "string", "block_count": 1, "char_count": len(system)}
    if isinstance(system, list):
        return {
            "kind": "blocks",
            "block_count": len(system),
            "char_count": sum(_block_text_length(block) for block in system),
            "cache_control_count": sum(
                1 for block in system if isinstance(block, dict) and "cache_control" in block
            ),
        }
    return {"kind": "none", "block_count": 0, "char_count": 0}


def _block_text_length(block: object) -> int:
    if not isinstance(block, dict):
        return 0
    text = block.get("text")
    return len(text) if isinstance(text, str) else 0


def _tool_names(tools: object) -> list[str]:
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if isinstance(name, str):
            names.append(name)
            continue
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.append(function["name"])
    return names
