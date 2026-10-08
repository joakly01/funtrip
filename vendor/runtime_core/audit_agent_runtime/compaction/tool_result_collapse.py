from __future__ import annotations

import json


def render_collapsed_message_text(message: dict) -> str:
    role = str(message.get("role", "unknown"))
    payload = message.get("payload")
    if role != "tool" or not isinstance(payload, dict):
        return _extract_non_tool_text(payload)
    return _collapse_tool_payload(payload)


def _extract_non_tool_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        text_value = text.strip()
    else:
        text_value = ""
    tool_calls_text = _render_tool_calls(payload.get("tool_calls"))
    if text_value and tool_calls_text:
        return f"{text_value} ({tool_calls_text})"
    if tool_calls_text:
        return tool_calls_text
    if text_value:
        return text_value
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(block.get("text", "")).strip()
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n".join(part for part in parts if part)
    return ""


def _collapse_tool_payload(payload: dict) -> str:
    tool_name = str(payload.get("tool_name") or "tool").strip() or "tool"
    result = payload.get("result")
    handle_parts = _tool_result_handle_parts(payload)
    if not isinstance(result, dict):
        if isinstance(result, str):
            return " ".join([f"tool[{tool_name}]:", *handle_parts, f"result_length={len(result)}"])
        return " ".join([f"tool[{tool_name}]", *handle_parts])

    if tool_name == "Read":
        file_path = _string_field(result, "file_path", "path", "target")
        content = result.get("content")
        content_length = len(content) if isinstance(content, str) else None
        parts = [f"tool[{tool_name}]", *handle_parts]
        if file_path:
            parts.append(f"path={file_path}")
        if content_length is not None:
            parts.append(f"content_length={content_length}")
        return " ".join(parts)

    if tool_name == "Grep":
        pattern = _string_field(result, "pattern", "query")
        matches = result.get("matches")
        match_count = len(matches) if isinstance(matches, list) else None
        first_match = matches[0] if isinstance(matches, list) and matches else None
        first_file = (
            str(first_match.get("file")).strip()
            if isinstance(first_match, dict) and isinstance(first_match.get("file"), str)
            else None
        )
        parts = [f"tool[{tool_name}]", *handle_parts]
        if pattern:
            parts.append(f"pattern={pattern}")
        if match_count is not None:
            parts.append(f"match_count={match_count}")
        if first_file:
            parts.append(f"first_file={first_file}")
        return " ".join(parts)

    if tool_name == "Glob":
        pattern = _string_field(result, "pattern", "glob")
        files = result.get("files")
        file_count = len(files) if isinstance(files, list) else None
        first_file = files[0] if isinstance(files, list) and files else None
        parts = [f"tool[{tool_name}]", *handle_parts]
        if pattern:
            parts.append(f"pattern={pattern}")
        if file_count is not None:
            parts.append(f"file_count={file_count}")
        if isinstance(first_file, str) and first_file.strip():
            parts.append(f"first_file={first_file.strip()}")
        return " ".join(parts)

    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if len(rendered) > 240:
        rendered = rendered[:240].rstrip() + "..."
    prefix = " ".join([f"tool[{tool_name}]", *handle_parts])
    return f"{prefix} {rendered}"


def _tool_result_handle_parts(payload: dict) -> list[str]:
    tool_call_id = _string_field(payload, "tool_call_id")
    if not tool_call_id:
        return []
    return [
        f"tool_call_id={tool_call_id}",
        "read_tool=read_tool_result",
    ]


def _string_field(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _render_tool_calls(tool_calls: object) -> str:
    if not isinstance(tool_calls, list):
        return ""
    rendered_calls: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        name = tool_call.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        normalized_name = name.strip()
        arguments = tool_call.get("arguments")
        rendered_args = _render_tool_call_arguments(arguments)
        if rendered_args:
            rendered_calls.append(f"{normalized_name}({rendered_args})")
        else:
            rendered_calls.append(normalized_name)
    if not rendered_calls:
        return ""
    return "used tools: " + "; ".join(rendered_calls)


def _render_tool_call_arguments(arguments: object) -> str:
    if not isinstance(arguments, dict):
        return ""
    interesting_keys = (
        "file_path",
        "path",
        "pattern",
        "query",
        "glob",
        "request",
        "text",
        "task_id",
        "agent_id",
    )
    rendered: list[str] = []
    for key in interesting_keys:
        value = arguments.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized:
            rendered.append(f"{key}={normalized}")
    if rendered:
        return ", ".join(rendered)
    if arguments:
        return ", ".join(sorted(str(key) for key in arguments.keys())[:3])
    return ""
