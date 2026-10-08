from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.repositories.messages import MessageRepository
from audit_agent_runtime.tools.core.base import BaseTool
from audit_agent_runtime.utils.json_utils import safe_json_dumps


AUTO_FULL_THRESHOLD_CHARS = 30_000
DEFAULT_SEGMENT_CHARS = 50_000
MAX_SEGMENT_CHARS = 50_000
MAX_TOTAL_READ_CHARS = 200_000
MAX_TURN_READ_CALLS = 30
DEFAULT_ITEM_LIMIT = 20
MAX_ITEM_LIMIT = 200
COMPLETE_WARNING = "This is the complete content. Do not call read_tool_result again for the same tool_call_id/json_path."
LIMIT_WARNING = "Read limit reached for this tool_call_id/json_path. Use the content already returned instead of reading more."
PARTIAL_CONTENT_WARNING = "Content is a raw character slice and may not be valid JSON. Do not parse it as complete JSON; continue with next_read."
TURN_LIMIT_WARNING = "Current turn read_tool_result call limit reached. Use already returned content and avoid reading more tool results."


@dataclass(frozen=True)
class _ReadState:
    next_offset: int | None
    completed: bool
    total_chars_read: int = 0


class ReadToolResultTool(BaseTool):
    name = "read_tool_result"
    description = (
        "Read a previously returned large tool_result from this session by tool_call_id. "
        "Use json_path for a specific JSON fragment. Small fragments are returned fully by default; "
        "large fragments return next_read and follow-up reads must use that next_offset. "
        "For JSON arrays, prefer item_offset/item_limit over character offsets; array pages return structured items, not content. "
        "Do not call read_tool_result on a read_tool_result tool_call_id; use the returned content directly. "
        "IMPORTANT: If has_more=false or read_status is COMPLETE/ALREADY_COMPLETE, you MUST use the returned "
        "content/value/items directly and NEVER call read_tool_result again for the same tool_call_id/json_path. "
        "Repeated calls waste context tokens and may return no content."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "tool_call_id": {
                "type": "string",
                "description": "The tool_call_id from the collapsed tool_result.",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset into the serialized result. Defaults to 0.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum characters to return. Defaults to full content under 30000 chars, otherwise 50000; maximum 50000.",
            },
            "json_path": {
                "type": "string",
                "description": "Optional dotted JSON path, e.g. rows.0 or metadata.title.",
            },
            "item_offset": {
                "type": "integer",
                "description": "For array json_path results, first item index to return. Prefer this over character offset; use returned items directly.",
            },
            "item_limit": {
                "type": "integer",
                "description": "For array json_path results, maximum item count to return. Defaults to 20, maximum 200; if has_more=false, do not read again.",
            },
        },
        "required": ["tool_call_id"],
    }

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self._read_states: dict[tuple[str, str, str | None], _ReadState] = {}
        self._turn_read_counts: dict[str, int] = {}

    def execute(self, arguments: dict, context) -> dict:
        session_id = getattr(context, "session_id", None)
        tool_call_id = _normalize_string(arguments.get("tool_call_id"))
        if not session_id:
            return _error("missing_session_id", "read_tool_result requires a session context.")
        if tool_call_id is None:
            return _error("missing_tool_call_id", "tool_call_id is required.")

        with self._session_factory() as db:
            record = MessageRepository(db).find_tool_result(session_id, tool_call_id)
            if record is None:
                return _error(
                    "tool_result_not_found",
                    f"No tool_result found for tool_call_id: {tool_call_id}",
                    tool_call_id=tool_call_id,
                )
            payload = record.payload_json

        result = payload.get("result") if isinstance(payload, dict) else None
        if result is None:
            return _error(
                "tool_result_empty",
                f"Tool result payload has no result for tool_call_id: {tool_call_id}",
                tool_call_id=tool_call_id,
            )
        if isinstance(payload, dict) and payload.get("tool_name") == self.name:
            return _readback_recursion_error(result, tool_call_id=tool_call_id)
        requested_json_path = _normalize_string(arguments.get("json_path"))
        json_path = requested_json_path
        normalized_json_path: dict[str, str] | None = None
        if json_path is not None:
            normalized = _normalize_readback_wrapper_path(result, json_path)
            if normalized is not None:
                json_path, normalized_json_path = normalized
            resolved = _resolve_json_path(result, json_path)
            if isinstance(resolved, _JsonPathError):
                return _error(
                    "json_path_not_found",
                    resolved.message,
                    tool_call_id=tool_call_id,
                    json_path=requested_json_path,
                    **resolved.fields,
                )
            result = resolved

        should_read_items = isinstance(result, list) and (
            arguments.get("item_offset") is not None
            or arguments.get("item_limit") is not None
            or arguments.get("offset") is None
        )
        if should_read_items:
            if arguments.get("offset") is not None:
                return _error(
                    "mixed_paging_arguments",
                    "Array item paging cannot be mixed with character offset. Use item_offset/item_limit only.",
                    tool_call_id=tool_call_id,
                    **({"json_path": json_path} if json_path is not None else {}),
                    recommended_arguments={
                        "tool_call_id": tool_call_id,
                        **({"json_path": json_path} if json_path is not None else {}),
                        "item_offset": _bounded_int(
                            arguments.get("item_offset"),
                            default=0,
                            minimum=0,
                            maximum=len(result),
                        ),
                        "item_limit": _bounded_int(
                            arguments.get("item_limit"),
                            default=DEFAULT_ITEM_LIMIT,
                            minimum=1,
                            maximum=MAX_ITEM_LIMIT,
                        ),
                    },
                )
            item_arguments = {**arguments}
            item_arguments.setdefault("item_limit", MAX_ITEM_LIMIT)
            read_scope_id = _read_scope_id(context, session_id=session_id)
            turn_limit_error = self._turn_read_limit_error(read_scope_id=read_scope_id)
            if turn_limit_error is not None:
                return turn_limit_error
            self._remember_turn_read(read_scope_id)
            item_result = self._read_items(
                result,
                payload=payload,
                tool_call_id=tool_call_id,
                json_path=json_path,
                arguments=item_arguments,
            )
            if normalized_json_path is not None:
                item_result["normalized_json_path"] = normalized_json_path
            return item_result

        serialized = _serialize_result(result)
        offset_was_provided = arguments.get("offset") is not None
        offset = _bounded_int(arguments.get("offset"), default=0, minimum=0, maximum=len(serialized))
        default_limit = _default_text_limit(len(serialized), offset_was_provided=offset_was_provided)
        limit = _bounded_int(
            arguments.get("limit"),
            default=default_limit,
            minimum=1,
            maximum=MAX_SEGMENT_CHARS,
        )
        read_state_error = self._read_state_error(
            session_id=session_id,
            tool_call_id=tool_call_id,
            json_path=json_path,
            offset=offset,
            limit=limit,
            offset_was_provided=offset_was_provided,
        )
        if read_state_error is not None:
            return read_state_error
        read_scope_id = _read_scope_id(context, session_id=session_id)
        turn_limit_error = self._turn_read_limit_error(read_scope_id=read_scope_id)
        if turn_limit_error is not None:
            return turn_limit_error
        content = serialized[offset : offset + limit]
        next_offset = offset + len(content)
        has_more = next_offset < len(serialized)
        self._remember_turn_read(read_scope_id)
        self._remember_next_offset(
            session_id=session_id,
            tool_call_id=tool_call_id,
            json_path=json_path,
            next_offset=next_offset,
            has_more=has_more,
            chars_read=len(content),
        )
        payload_result = {
            "ok": True,
            "read_status": "PARTIAL" if has_more else "COMPLETE",
            "tool_name": payload.get("tool_name") if isinstance(payload, dict) else None,
            "tool_call_id": tool_call_id,
            **({"json_path": json_path} if json_path is not None else {}),
            **({"normalized_json_path": normalized_json_path} if normalized_json_path is not None else {}),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            **(
                {
                    "next_read": {
                        "tool_call_id": tool_call_id,
                        **({"json_path": json_path} if json_path is not None else {}),
                        "offset": next_offset,
                        "limit": limit,
                    }
                }
                if has_more
                else {}
            ),
            "original_char_count": len(serialized),
            "content_is_complete": not has_more,
            **(
                {
                    "partial_content_warning": PARTIAL_CONTENT_WARNING,
                    "content_is_parseable_json": False,
                }
                if has_more
                else {}
            ),
            "do_not_call_again": not has_more,
            **({"completion_warning": COMPLETE_WARNING} if not has_more else {}),
            "usage_guidance": {
                "do_not_repeat_same_call": not has_more,
                "do_not_call_again": not has_more,
                "has_more_false_means_done": not has_more,
                "never_repeat_after_complete": not has_more,
                "do_not_read_read_tool_result_again": payload.get("tool_name") == self.name
                if isinstance(payload, dict)
                else False,
                "read_strategy": "sequential" if has_more else "full",
                "must_use_next_offset": has_more,
                "continue_with_offset": next_offset if has_more else None,
                "do_not_use_overlapping_offsets": True,
                "prefer_item_paging": isinstance(result, list),
                "do_not_parse_partial_content_as_json": has_more,
                "do_not_read_content_json_path": not has_more and not isinstance(result, str),
                "use_returned_content_directly": not has_more and isinstance(result, str),
                "use_returned_value_directly": not has_more and not isinstance(result, str),
            },
        }
        if has_more or isinstance(result, str):
            payload_result["content"] = content
        else:
            payload_result["value"] = result
            payload_result["value_type"] = _value_type(result)
        return payload_result

    def _read_items(
        self,
        items: list,
        *,
        payload: dict,
        tool_call_id: str,
        json_path: str | None,
        arguments: dict,
    ) -> dict:
        item_count = len(items)
        item_offset = _bounded_int(arguments.get("item_offset"), default=0, minimum=0, maximum=item_count)
        item_limit = _bounded_int(arguments.get("item_limit"), default=DEFAULT_ITEM_LIMIT, minimum=1, maximum=MAX_ITEM_LIMIT)
        page_items = items[item_offset : item_offset + item_limit]
        next_item_offset = item_offset + len(page_items)
        has_more = next_item_offset < item_count
        return {
            "ok": True,
            "read_status": "PARTIAL" if has_more else "COMPLETE",
            "mode": "items",
            "tool_name": payload.get("tool_name") if isinstance(payload, dict) else None,
            "tool_call_id": tool_call_id,
            **({"json_path": json_path} if json_path is not None else {}),
            "item_count": item_count,
            "item_offset": item_offset,
            "item_limit": item_limit,
            "has_more": has_more,
            "next_item_offset": next_item_offset if has_more else None,
            **(
                {
                    "next_read": {
                        "tool_call_id": tool_call_id,
                        **({"json_path": json_path} if json_path is not None else {}),
                        "item_offset": next_item_offset,
                        "item_limit": item_limit,
                    }
                }
                if has_more
                else {}
            ),
            "items": page_items,
            "items_is_complete": not has_more,
            "do_not_call_again": not has_more,
            **({"completion_warning": COMPLETE_WARNING} if not has_more else {}),
            "value_type": "array_page",
            "usage_guidance": {
                "do_not_repeat_same_call": not has_more,
                "do_not_call_again": not has_more,
                "has_more_false_means_done": not has_more,
                "never_repeat_after_complete": not has_more,
                "do_not_read_read_tool_result_again": payload.get("tool_name") == self.name
                if isinstance(payload, dict)
                else False,
                "read_strategy": "item_paging",
                "prefer_item_paging": True,
                "must_use_next_item_offset": has_more,
                "continue_with_item_offset": next_item_offset if has_more else None,
                "do_not_read_content_json_path": True,
                "use_items_directly": True,
            },
        }

    def _read_state_error(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        json_path: str | None,
        offset: int,
        limit: int,
        offset_was_provided: bool,
    ) -> dict | None:
        state = self._read_states.get((session_id, tool_call_id, json_path))
        if state is None:
            return None
        if state.completed:
            return _terminal_read_status(
                read_status="ALREADY_COMPLETE",
                warning=COMPLETE_WARNING,
                tool_call_id=tool_call_id,
                json_path=json_path,
                requested_offset=offset,
                limit=limit,
                total_chars_read=state.total_chars_read,
            )
        if state.total_chars_read >= MAX_TOTAL_READ_CHARS or state.total_chars_read + limit > MAX_TOTAL_READ_CHARS:
            return _terminal_read_status(
                read_status="LIMIT_REACHED",
                warning=LIMIT_WARNING,
                tool_call_id=tool_call_id,
                json_path=json_path,
                requested_offset=offset,
                limit=limit,
                total_chars_read=state.total_chars_read,
            )
        if not offset_was_provided:
            return None
        expected = state.next_offset
        if expected is None or offset == expected:
            return None
        error = "overlapping_read" if offset < expected else "non_sequential_read"
        message = (
            "This read overlaps with a previous read. Continue with next_offset instead of guessing offsets."
            if offset < expected
            else "This read skips unread content. Continue with next_offset instead of guessing offsets."
        )
        return _error(
            error,
            message,
            tool_call_id=tool_call_id,
            **({"json_path": json_path} if json_path is not None else {}),
            previous_next_offset=expected,
            requested_offset=offset,
            recommended_arguments={
                "tool_call_id": tool_call_id,
                **({"json_path": json_path} if json_path is not None else {}),
                "offset": expected,
                "limit": limit,
            },
        )

    def _turn_read_limit_error(self, *, read_scope_id: str) -> dict | None:
        count = self._turn_read_counts.get(read_scope_id, 0)
        if count < MAX_TURN_READ_CALLS:
            return None
        return _terminal_read_status(
            read_status="TURN_READ_LIMIT_REACHED",
            warning=TURN_LIMIT_WARNING,
            tool_call_id="",
            json_path=None,
            requested_offset=0,
            limit=0,
            total_chars_read=0,
            turn_read_count=count,
        )

    def _remember_turn_read(self, read_scope_id: str) -> None:
        self._turn_read_counts[read_scope_id] = self._turn_read_counts.get(read_scope_id, 0) + 1

    def _remember_next_offset(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        json_path: str | None,
        next_offset: int,
        has_more: bool,
        chars_read: int,
    ) -> None:
        key = (session_id, tool_call_id, json_path)
        previous = self._read_states.get(key)
        total_chars_read = (previous.total_chars_read if previous is not None else 0) + chars_read
        self._read_states[key] = _ReadState(
            next_offset=next_offset if has_more else None,
            completed=not has_more,
            total_chars_read=total_chars_read,
        )


class ReadSessionMessagesTool(BaseTool):
    name = "read_session_messages"
    description = (
        "Read original session transcript messages by message id range. "
        "Use this when compacted history or session memory points to a source message range."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "start_message_id": {
                "type": "integer",
                "description": "First message id to read. Defaults to 1.",
            },
            "end_message_id": {
                "type": "integer",
                "description": "Last message id to read, inclusive.",
            },
            "offset": {
                "type": "integer",
                "description": "Character offset into the rendered transcript. Defaults to 0.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum characters to return. Defaults to 12000, maximum 50000.",
            },
        },
    }

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def execute(self, arguments: dict, context) -> dict:
        session_id = getattr(context, "session_id", None)
        if not session_id:
            return _error("missing_session_id", "read_session_messages requires a session context.")
        start_id = _optional_bounded_int(arguments.get("start_message_id"), minimum=1)
        end_id = _optional_bounded_int(arguments.get("end_message_id"), minimum=1)
        with self._session_factory() as db:
            records = MessageRepository(db).list_message_range(
                session_id,
                start_id=start_id,
                end_id=end_id,
            )
        rendered_messages = [_render_message_record(record) for record in records]
        serialized = "\n".join(item for item in rendered_messages if item)
        offset = _bounded_int(arguments.get("offset"), default=0, minimum=0, maximum=len(serialized))
        limit = _bounded_int(arguments.get("limit"), default=12_000, minimum=1, maximum=50_000)
        content = serialized[offset : offset + limit]
        next_offset = offset + len(content)
        has_more = next_offset < len(serialized)
        return {
            "ok": True,
            "start_message_id": start_id,
            "end_message_id": end_id,
            "message_count": len(records),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            "original_char_count": len(serialized),
            "content": content,
        }


class _JsonPathError:
    def __init__(self, message: str, **fields: object):
        self.message = message
        self.fields = fields


def _resolve_json_path(value: Any, path: str) -> Any | _JsonPathError:
    current = value
    traversed: list[str] = []
    for segment in [part for part in path.split(".") if part != ""]:
        if isinstance(current, dict):
            if segment not in current:
                return _JsonPathError(
                    f"JSON path segment not found: {segment}",
                    missing_segment=segment,
                    path_base=".".join(traversed) if traversed else "$",
                    available_keys=sorted(str(key) for key in current.keys()),
                    usage_guidance={
                        "do_not_guess_json_path": True,
                        "json_path_base": "original_tool_result_root",
                        "do_not_prefix_with_readback_wrapper_fields": ["value", "content", "items"],
                    },
                )
            current = current[segment]
            traversed.append(segment)
            continue
        if isinstance(current, list):
            try:
                index = int(segment)
            except ValueError:
                return _JsonPathError(
                    f"JSON path segment is not a list index: {segment}",
                    missing_segment=segment,
                    path_base=".".join(traversed) if traversed else "$",
                    usage_guidance={"use_numeric_list_index_or_item_paging": True},
                )
            if index < 0 or index >= len(current):
                return _JsonPathError(
                    f"JSON path list index out of range: {segment}",
                    missing_segment=segment,
                    path_base=".".join(traversed) if traversed else "$",
                    item_count=len(current),
                    usage_guidance={"use_existing_list_index_or_item_paging": True},
                )
            current = current[index]
            traversed.append(segment)
            continue
        return _JsonPathError(
            f"JSON path cannot descend into scalar at: {segment}",
            missing_segment=segment,
            path_base=".".join(traversed) if traversed else "$",
            value_type=_value_type(current),
            usage_guidance={"do_not_descend_into_scalar": True},
        )
    return current


def _normalize_readback_wrapper_path(value: Any, path: str) -> tuple[str, dict[str, str]] | None:
    parts = [part for part in path.split(".") if part != ""]
    if len(parts) < 2 or parts[0] not in {"value", "content", "items"}:
        return None
    if isinstance(value, dict) and parts[0] in value:
        return None
    candidate = ".".join(parts[1:])
    if isinstance(_resolve_json_path(value, candidate), _JsonPathError):
        return None
    return (
        candidate,
        {
            "from": path,
            "to": candidate,
            "reason": "removed_readback_wrapper_prefix",
        },
    )


def _readback_recursion_error(result: Any, *, tool_call_id: str) -> dict:
    fields: dict[str, object] = {"tool_call_id": tool_call_id}
    if isinstance(result, dict):
        next_read = result.get("next_read")
        if isinstance(next_read, dict):
            fields["recommended_arguments"] = next_read
            fields["original_tool_call_id"] = next_read.get("tool_call_id")
        elif isinstance(result.get("tool_call_id"), str):
            recommended = {"tool_call_id": result["tool_call_id"]}
            if isinstance(result.get("json_path"), str):
                recommended["json_path"] = result["json_path"]
            fields["recommended_arguments"] = recommended
            fields["original_tool_call_id"] = result["tool_call_id"]
    return _error(
        "read_tool_result_recursion",
        "Do not call read_tool_result on a read_tool_result tool_call_id. Use the previous read_tool_result content directly, or continue with recommended_arguments when present.",
        **fields,
    )


def _serialize_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    return safe_json_dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _default_text_limit(original_char_count: int, *, offset_was_provided: bool) -> int:
    if not offset_was_provided and original_char_count <= AUTO_FULL_THRESHOLD_CHARS:
        return max(1, original_char_count)
    return DEFAULT_SEGMENT_CHARS


def _value_type(value: object) -> str:
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
    return "scalar"


def _normalize_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _read_scope_id(context, *, session_id: str) -> str:
    task_id = _normalize_string(getattr(context, "task_id", None))
    if task_id is not None:
        return f"task:{task_id}"
    return f"session:{session_id}"


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _optional_bounded_int(value: object, *, minimum: int) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, parsed)


def _render_message_record(record) -> str:
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    if record.role == "tool":
        tool_name = payload.get("tool_name") or "tool"
        tool_call_id = payload.get("tool_call_id")
        result = payload.get("result", payload.get("content", ""))
        rendered_result = _serialize_result(result)
        handle = f" tool_call_id={tool_call_id} read_tool=read_tool_result" if tool_call_id else ""
        return f"[message_id={record.id} role=tool tool_name={tool_name}{handle}]\n{rendered_result}"
    text = payload.get("text", payload.get("content", ""))
    rendered_text = _serialize_result(text) if not isinstance(text, str) else text
    return f"[message_id={record.id} role={record.role}]\n{rendered_text}"


def _terminal_read_status(
    *,
    read_status: str,
    warning: str,
    tool_call_id: str,
    json_path: str | None,
    requested_offset: int,
    limit: int,
    total_chars_read: int,
    turn_read_count: int | None = None,
) -> dict:
    recommended_arguments: dict[str, object] = {}
    if tool_call_id:
        recommended_arguments["tool_call_id"] = tool_call_id
    if json_path is not None:
        recommended_arguments["json_path"] = json_path
    return {
        "ok": True,
        "read_status": read_status,
        **({"tool_call_id": tool_call_id} if tool_call_id else {}),
        **({"json_path": json_path} if json_path is not None else {}),
        "has_more": False,
        "next_offset": None,
        "content_is_complete": read_status == "ALREADY_COMPLETE",
        "read_limit_reached": read_status == "LIMIT_REACHED",
        "turn_read_limit_reached": read_status == "TURN_READ_LIMIT_REACHED",
        "do_not_call_again": True,
        "completion_warning": warning,
        "requested_offset": requested_offset,
        "limit": limit,
        "total_chars_read": total_chars_read,
        **({"turn_read_count": turn_read_count} if turn_read_count is not None else {}),
        **({"recommended_arguments": recommended_arguments} if recommended_arguments else {}),
        "usage_guidance": {
            "do_not_call_again": True,
            "do_not_repeat_same_call": True,
            "has_more_false_means_done": True,
            "never_repeat_after_complete": True,
            "use_previous_content_directly": True,
        },
    }


def _error(error: str, message: str, **fields: object) -> dict:
    return {
        "ok": False,
        "is_error": True,
        "error": error,
        "message": message,
        **fields,
    }
