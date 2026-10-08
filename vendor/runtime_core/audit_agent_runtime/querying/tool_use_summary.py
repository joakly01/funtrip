from __future__ import annotations

from audit_agent_runtime.llm.base import ModelMessage
from audit_agent_runtime.prompts.reminders import wrap_in_system_reminder


def build_tool_use_summary_payload(
    completed_tools: list[dict[str, object]],
) -> dict | None:
    if len(completed_tools) < 2:
        return None
    details = summarize_completed_tool_details(completed_tools)
    if details is None:
        return None
    return {
        "schema_version": 1,
        "summary": details["summary"],
        "tool_count": len(completed_tools),
        "tools": details["tools"],
        "targets": details["targets"],
    }


def build_tool_use_summary_message(payload: dict) -> ModelMessage:
    return ModelMessage(
        role="user",
        content=wrap_in_system_reminder(
            f"Tool use summary:\n{payload.get('summary', '')}"
        ),
    )


def summarize_completed_tools(completed_tools: list[dict[str, object]]) -> str | None:
    details = summarize_completed_tool_details(completed_tools)
    return None if details is None else str(details["summary"])


def summarize_completed_tool_details(
    completed_tools: list[dict[str, object]],
) -> dict[str, object] | None:
    if not completed_tools:
        return None

    tool_counts: dict[str, int] = {}
    targets: list[str] = []
    for item in completed_tools:
        tool_name = str(item.get("tool_name") or "tool")
        tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
        target = summarize_tool_target(
            tool_name,
            item.get("arguments"),
            item.get("result"),
        )
        if target and target not in targets:
            targets.append(target)

    count_summary = ", ".join(
        f"{tool_name} x{count}"
        for tool_name, count in tool_counts.items()
    )
    parts = [f"Recent tool activity: {count_summary}."]
    if targets:
        parts.append(f"Last targets: {'; '.join(targets[:4])}.")
    return {
        "summary": " ".join(parts),
        "tools": [
            {"tool_name": tool_name, "count": count}
            for tool_name, count in tool_counts.items()
        ],
        "targets": targets[:4],
    }


def summarize_tool_target(
    tool_name: str,
    arguments: object,
    result: object,
) -> str | None:
    args = arguments if isinstance(arguments, dict) else {}
    payload = result if isinstance(result, dict) else {}

    if tool_name in {"Read", "Glob", "Grep"}:
        path = args.get("path") or payload.get("path") or payload.get("root")
        if isinstance(path, str) and path:
            return path
        pattern = args.get("pattern") or payload.get("pattern")
        if isinstance(pattern, str) and pattern:
            return pattern
    if tool_name in {"Skill", "load_skill"}:
        skill_name = payload.get("skill_name")
        if isinstance(skill_name, str) and skill_name:
            return skill_name
    return tool_name
