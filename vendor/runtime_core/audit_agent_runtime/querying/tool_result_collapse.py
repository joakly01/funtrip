from __future__ import annotations

from audit_agent_runtime.utils.json_utils import safe_json_dumps

DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS = 30_000
DEFAULT_MODEL_TOOL_RESULT_PREVIEW_CHARS = 2_000
DEFAULT_STRUCTURED_PREVIEW_MAX_DEPTH = 6
DEFAULT_STRUCTURED_PREVIEW_ARRAY_ITEMS = 3
DEFAULT_STRUCTURED_PREVIEW_STRING_CHARS = 300


def collapse_tool_result_for_model(
    *,
    tool_name: str,
    result: dict,
    tool_call_id: str | None = None,
    max_chars: int = DEFAULT_MAX_MODEL_TOOL_RESULT_CHARS,
    preview_chars: int = DEFAULT_MODEL_TOOL_RESULT_PREVIEW_CHARS,
) -> dict:
    serialized = safe_json_dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(serialized) <= max_chars:
        return result

    collapsed = {
        "collapsed": True,
        "collapse_reason": "large_tool_result",
        "tool_name": tool_name,
        "original_char_count": len(serialized),
        "preview_char_count": min(len(serialized), preview_chars),
        "preview": serialized[:preview_chars],
        "preview_note": "Truncated serialized JSON for diagnostics only; do not treat preview as complete tool data.",
        "preview_summary": _build_preview_summary(result, serialized_char_count=len(serialized)),
        "preview_structured": _build_structured_preview(result),
        "top_level_keys": sorted(str(key) for key in result.keys()),
        "usage_guidance": {
            "prefer_json_path": True,
            "prefer_item_paging": True,
            "do_not_repeat_after_complete": True,
            "has_more_false_means_done": True,
            "do_not_read_read_tool_result_tool_call_id": True,
            "read_strategy": (
                "Use read_tool_result once for the smallest needed json_path. "
                "When read_tool_result returns has_more=false, answer from its content/value/items directly."
            ),
        },
    }
    source_usage_guidance = result.get("usage_guidance")
    if isinstance(source_usage_guidance, dict) and source_usage_guidance.get("raw_graph"):
        collapsed["usage_guidance"]["raw_graph"] = source_usage_guidance["raw_graph"]
    if tool_call_id:
        collapsed["tool_call_id"] = tool_call_id
        collapsed["read_tool"] = "read_tool_result"
        collapsed["read_tool_arguments"] = {"tool_call_id": tool_call_id}
    if result.get("is_error") is True:
        collapsed["is_error"] = True
        collapsed["error"] = result.get("error")
    for key in (
        "ok",
        "type",
        "artifact_type",
        "tool_type",
        "dataset_title",
        "summary",
        "query_handle",
        "page",
        "page_size",
        "has_more",
        "row_count",
        "next_action",
        "next_tool",
    ):
        if key in result:
            collapsed[key] = result[key]
    return collapsed

def _build_structured_preview(value: object, *, depth: int = 0) -> object:
    if depth >= DEFAULT_STRUCTURED_PREVIEW_MAX_DEPTH:
        if isinstance(value, dict | list):
            return {"preview_type": _preview_type(value), "truncated": True, "reason": "max_depth"}
        if isinstance(value, str) and len(value) > DEFAULT_STRUCTURED_PREVIEW_STRING_CHARS:
            return _truncated_string_preview(value)
        return value
    if isinstance(value, dict):
        return {
            str(key): _build_structured_preview(child, depth=depth + 1)
            for key, child in value.items()
        }
    if isinstance(value, list):
        items = [
            _build_structured_preview(item, depth=depth + 1)
            for item in value[:DEFAULT_STRUCTURED_PREVIEW_ARRAY_ITEMS]
        ]
        return {
            "preview_type": "array",
            "total_count": len(value),
            "sampled_count": len(items),
            "items": items,
        }
    if isinstance(value, str) and len(value) > DEFAULT_STRUCTURED_PREVIEW_STRING_CHARS:
        return _truncated_string_preview(value)
    return value


def _truncated_string_preview(value: str) -> dict:
    return {
        "preview_type": "string",
        "truncated": True,
        "original_char_count": len(value),
        "sample": value[:DEFAULT_STRUCTURED_PREVIEW_STRING_CHARS],
    }


def _build_preview_summary(value: object, *, serialized_char_count: int) -> dict:
    summary = {
        "type": _preview_type(value),
        "size_hint": f"{serialized_char_count} chars JSON",
    }
    if isinstance(value, dict):
        summary["sample_keys"] = sorted(str(key) for key in value.keys())[:10]
        array_fields: dict[str, dict] = {}
        object_fields: dict[str, dict] = {}
        for key, child in value.items():
            field_name = str(key)
            if isinstance(child, list):
                array_fields[field_name] = _summarize_array(child)
            elif isinstance(child, dict):
                object_fields[field_name] = {
                    "type": "object",
                    "sample_keys": sorted(str(child_key) for child_key in child.keys())[:10],
                }
        if array_fields:
            summary["array_fields"] = array_fields
        if object_fields:
            summary["object_fields"] = object_fields
    elif isinstance(value, list):
        summary.update(_summarize_array(value))
    return summary


def _summarize_array(items: list) -> dict:
    summary = {
        "type": "array",
        "estimated_item_count": len(items),
    }
    if not items:
        return summary
    first = items[0]
    summary["sample_item_type"] = _preview_type(first)
    if isinstance(first, dict):
        summary["sample_keys"] = sorted(str(key) for key in first.keys())[:10]
    return summary


def _preview_type(value: object) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "scalar"
