from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from audit_agent_runtime.llm.base import ModelClient, ModelMessage
from audit_agent_runtime.compaction.tool_result_collapse import render_collapsed_message_text


@dataclass(slots=True)
class RecallSection:
    title: str
    content: str
    placement: str = "user"


class RelevantRecallService:
    def __init__(self, model_client: ModelClient | None = None):
        self.model_client = model_client

    def build_recall_sections(
        self,
        *,
        query: str,
        latest_memory: dict | None,
        latest_compaction: dict | None,
        recent_compactions: list[dict] | None = None,
        project_memory_markdown: str | None = None,
        active_skill_hints: dict | None = None,
        last_tool_use_summary: dict | None = None,
        last_reactive_compact_context: dict | None = None,
        include_tool_use_summary: bool = False,
    ) -> list[RecallSection]:
        sections: list[RecallSection] = []
        query_terms = _extract_terms(query)
        matched_any = False

        memory_section = _build_memory_section(latest_memory or {}, query_terms)
        if memory_section is not None:
            sections.append(memory_section)
            matched_any = True

        compaction_sections = _build_compaction_sections(
            latest_compaction=latest_compaction or {},
            recent_compactions=recent_compactions or [],
            query_terms=query_terms,
        )
        if compaction_sections:
            sections.extend(compaction_sections)
            matched_any = True
        preserved_tail_section = _build_preserved_tail_section(
            latest_compaction=latest_compaction or {},
            query_terms=query_terms,
        )
        if preserved_tail_section is not None:
            sections.append(preserved_tail_section)
            matched_any = True

        project_memory_section = _build_project_memory_section(
            project_memory_markdown=project_memory_markdown,
            query_terms=query_terms,
        )
        if project_memory_section is not None:
            sections.append(project_memory_section)
            matched_any = True

        active_skill_section = _build_active_skill_section(
            active_skill_hints=active_skill_hints or {},
            query_terms=query_terms,
        )
        if active_skill_section is not None:
            sections.append(active_skill_section)
            matched_any = True

        if include_tool_use_summary:
            tool_use_summary_section = _build_tool_use_summary_section(
                tool_use_summary=last_tool_use_summary or {},
                query_terms=query_terms,
            )
            if tool_use_summary_section is not None:
                sections.append(tool_use_summary_section)
                matched_any = True

        reactive_compact_section = _build_reactive_compact_section(
            reactive_compact_context=last_reactive_compact_context or {},
            query_terms=query_terms,
        )
        if reactive_compact_section is not None:
            sections.append(reactive_compact_section)
            matched_any = True

        if memory_section is None and not matched_any:
            memory_fallback = _build_memory_section(latest_memory or {}, set())
            if memory_fallback is not None:
                sections.insert(0, memory_fallback)
        if self.model_client is None or len(sections) <= 1:
            return sections
        selected = self._select_with_model(query=query, candidates=sections)
        return selected or sections

    def _select_with_model(
        self,
        *,
        query: str,
        candidates: list[RecallSection],
    ) -> list[RecallSection]:
        if self.model_client is None or not candidates:
            return []
        response = self.model_client.complete(
            [
                ModelMessage(
                    role="system",
                    content=(
                        "Select the most relevant recall sections for the query. "
                        "Return strict JSON only with key selected_titles, whose value is an array of section titles. "
                        "Do not use markdown fences."
                    ),
                ),
                ModelMessage(
                    role="user",
                    content=_render_recall_selection_prompt(query, candidates),
                ),
            ],
            [],
        )
        selected_titles = _parse_selected_titles(response.output_text)
        if not selected_titles:
            return []
        return [section for section in candidates if section.title in selected_titles]


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _sanitize_recall_text(value).strip()


def _sanitize_recall_text(value: str) -> str:
    sanitized = value
    sanitized = re.sub(
        r"\[tool_calls:\s*([^\]]+)\]",
        lambda match: f"(used tools: {match.group(1).strip()})",
        sanitized,
    )
    sanitized = re.sub(
        r"(?m)^tool_calls:\s*",
        "used tools: ",
        sanitized,
    )
    sanitized = re.sub(
        r"(?m)^tool\(([^)]+)\):",
        lambda match: f"tool result from {match.group(1).strip()}:",
        sanitized,
    )
    return sanitized


def _build_memory_section(latest_memory: dict, query_terms: set[str]) -> RecallSection | None:
    stale_note = _build_prior_memory_note(latest_memory)
    summary_json = latest_memory.get("summary_json")
    if isinstance(summary_json, dict):
        selected_lines: list[str] = []
        for key, value in summary_json.items():
            text = _render_summary_json_value(value)
            if not text:
                continue
            if _score_text(f"{_memory_key_matching_text(key)} {text}", query_terms) <= 0:
                continue
            selected_lines.append(f"- {_format_key(key)}: {text}")
        if selected_lines:
            return RecallSection(
                title="Session Memory",
                content=_prepend_prior_memory_note("\n".join(selected_lines), stale_note),
            )

    summary_markdown = _normalized_text(latest_memory.get("summary_markdown"))
    if summary_markdown and (_score_text(summary_markdown, query_terms) > 0 or not query_terms):
        return RecallSection(
            title="Session Memory",
            content=_prepend_prior_memory_note(summary_markdown, stale_note),
        )
    return None


def _build_prior_memory_note(latest_memory: dict) -> str | None:
    summarizes_until = latest_memory.get("summarizes_until_message_id")
    latest_message_id = latest_memory.get("latest_message_id")
    if not isinstance(summarizes_until, int) or not isinstance(latest_message_id, int):
        return None
    if latest_message_id <= summarizes_until:
        return None
    return (
        f"Prior summary: this session memory summarizes the conversation through message {summarizes_until}; "
        f"later transcript messages exist through message {latest_message_id}; "
        "later transcript messages take precedence."
    )


def _prepend_prior_memory_note(content: str, note: str | None) -> str:
    if not note:
        return content
    return f"{note}\n\n{content}"


def _format_key(key: object) -> str:
    if not isinstance(key, str):
        return "section"
    normalized = key.replace("_", " ").strip()
    return normalized.title() if normalized else "section"


def _memory_key_matching_text(key: object) -> str:
    if not isinstance(key, str):
        return ""
    aliases = {
        "current_state": "current state 当前状态 现状",
        "user_goal": "user goal 目标 用户目标",
        "runtime_state": "runtime state 运行时状态",
        "files_modules_discussed": "files modules discussed 文件 模块 讨论文件 讨论模块",
        "tools_used": "tools used 工具 使用工具",
        "errors_and_corrections": "errors corrections 错误 修复 更正",
        "open_decisions": "open decisions 待定决策 未决事项",
        "key_results": "key results 关键结果 结果 结论",
        "audit_context": "audit context 审计上下文",
        "business_domain_facts": "business domain facts 业务领域事实 业务事实",
    }
    return aliases.get(key, key.replace("_", " "))


def _render_summary_json_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        rendered_items = [_render_summary_json_list_item(item) for item in value]
        rendered_items = [item for item in rendered_items if item]
        if not rendered_items:
            return ""
        separator = "; " if any(len(item) > 30 or " " in item for item in rendered_items) else ", "
        return separator.join(rendered_items)
    if isinstance(value, dict):
        rendered_items: list[str] = []
        for key, nested in value.items():
            nested_text = _render_summary_json_value(nested)
            if not nested_text:
                continue
            rendered_items.append(f"{_format_key(key)}={nested_text}")
        return "; ".join(rendered_items)
    return ""


def _render_summary_json_list_item(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        pairs: list[str] = []
        for key, nested in value.items():
            nested_text = _render_summary_json_value(nested)
            if not nested_text:
                continue
            pairs.append(f"{_format_key(key)}={nested_text}")
        return ", ".join(pairs)
    return ""


def _score_text(text: str, query_terms: set[str]) -> int:
    if not text:
        return 0
    if not query_terms:
        return 1
    haystack = _extract_terms(text)
    return sum(1 for term in query_terms if term in haystack)


def _extract_terms(text: str) -> set[str]:
    normalized = (text or "").lower()
    tokens = set(re.findall(r"[a-z0-9_./+-]+|[\u4e00-\u9fff]{2,}", normalized))
    expanded: set[str] = set(tokens)
    for token in tokens:
        for delimiter in ("_", "-", "/"):
            if delimiter in token:
                expanded.update(part for part in token.split(delimiter) if part)
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token):
            max_size = min(len(token), 6)
            for size in range(2, max_size + 1):
                for index in range(0, len(token) - size + 1):
                    expanded.add(token[index:index + size])
    return {token for token in expanded if token}


def _build_project_memory_section(
    *,
    project_memory_markdown: str | None,
    query_terms: set[str],
) -> RecallSection | None:
    markdown = _normalized_text(project_memory_markdown)
    if not markdown:
        return None
    raw_lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    matching_lines: list[str] = []
    for index, line in enumerate(raw_lines):
        if _score_text(line, query_terms) <= 0:
            continue
        if line not in matching_lines:
            matching_lines.append(line)
        if index + 1 < len(raw_lines):
            next_line = raw_lines[index + 1]
            if next_line.startswith("- ") or next_line.startswith("base url:") or "http" in next_line.lower():
                if next_line not in matching_lines:
                    matching_lines.append(next_line)
    if not matching_lines:
        return None
    return RecallSection(
        title="Project Memory",
        content="\n".join(matching_lines),
        placement="system",
    )


def _build_compaction_sections(
    *,
    latest_compaction: dict,
    recent_compactions: list[dict],
    query_terms: set[str],
) -> list[RecallSection]:
    candidates: list[dict] = []
    seen_boundaries: set[int] = set()
    if isinstance(latest_compaction, dict) and latest_compaction:
        candidates.append(latest_compaction)
        boundary = latest_compaction.get("boundary_message_id")
        if isinstance(boundary, int):
            seen_boundaries.add(boundary)
    for compaction in recent_compactions:
        if not isinstance(compaction, dict):
            continue
        boundary = compaction.get("boundary_message_id")
        if isinstance(boundary, int) and boundary in seen_boundaries:
            continue
        if isinstance(boundary, int):
            seen_boundaries.add(boundary)
        candidates.append(compaction)

    sections: list[RecallSection] = []
    for index, compaction in enumerate(candidates):
        compaction_text = _normalized_text(compaction.get("summary_text"))
        boundary_message_id = compaction.get("boundary_message_id")
        content_parts: list[str] = []
        scored = _score_text(compaction_text, query_terms)
        if query_terms and scored <= 0:
            continue
        if compaction_text and scored > 0:
            content_parts.append(compaction_text)
        if isinstance(boundary_message_id, int) and boundary_message_id > 0:
            content_parts.append(f"Compact boundary: message {boundary_message_id}")
            content_parts.append(
                "Source transcript can be read with read_session_messages "
                f"(start_message_id=1, end_message_id={boundary_message_id})."
            )
        if not content_parts:
            continue
        title = "Compacted History" if index == 0 else f"Compacted History (message {boundary_message_id})"
        sections.append(RecallSection(title=title, content="\n".join(content_parts)))
    return sections


def _build_preserved_tail_section(
    *,
    latest_compaction: dict,
    query_terms: set[str],
) -> RecallSection | None:
    rendered_preserved_tail = _render_preserved_tail(
        latest_compaction.get("preserved_messages"),
        query_terms,
    )
    if not rendered_preserved_tail:
        return None
    return RecallSection(
        title="Preserved Tail Metadata",
        content="\n".join(rendered_preserved_tail),
    )


def _render_preserved_tail(
    preserved_messages: object,
    query_terms: set[str],
) -> list[str]:
    if not isinstance(preserved_messages, list):
        return []
    lines: list[str] = []
    for message in preserved_messages:
        if not isinstance(message, dict):
            continue
        role = _normalized_text(message.get("role")) or "unknown"
        text = _normalized_text(render_collapsed_message_text(message))
        if not text:
            continue
        if _score_text(f"{role} {text}", query_terms) <= 0:
            continue
        lines.append(f"- {role}: {text}")
    return lines


def _build_active_skill_section(
    *,
    active_skill_hints: dict,
    query_terms: set[str],
) -> RecallSection | None:
    lines: list[str] = []
    for skill_name, hint in active_skill_hints.items():
        if not isinstance(skill_name, str) or not isinstance(hint, dict):
            continue
        candidate_text_parts = [skill_name]
        when_to_use = _normalized_text(hint.get("when_to_use"))
        if when_to_use:
            candidate_text_parts.append(when_to_use)
        when_not_to_use = _normalized_text(hint.get("when_not_to_use"))
        if when_not_to_use:
            candidate_text_parts.append(when_not_to_use)
        allowed_tools = hint.get("allowed_tools")
        if isinstance(allowed_tools, list):
            candidate_text_parts.extend(str(tool) for tool in allowed_tools if str(tool).strip())
        preferred_model = _normalized_text(hint.get("preferred_model"))
        if preferred_model:
            candidate_text_parts.append(preferred_model)
        candidate_text = " ".join(candidate_text_parts)
        if _score_text(candidate_text, query_terms) <= 0:
            continue
        line = f"- {skill_name}"
        if when_to_use:
            line += f": {when_to_use}"
        rendered_details: list[str] = []
        if when_not_to_use:
            rendered_details.append(f"when not to use: {when_not_to_use}")
        if isinstance(allowed_tools, list) and allowed_tools:
            rendered_details.append(
                "allowed tools: " + ", ".join(str(tool) for tool in allowed_tools)
            )
        if preferred_model:
            rendered_details.append(f"preferred model: {preferred_model}")
        if rendered_details:
            line += f" ({'; '.join(rendered_details)})"
        lines.append(line)
    if not lines:
        return None
    return RecallSection(title="Active Skill Hints", content="\n".join(lines))


def _build_tool_use_summary_section(
    *,
    tool_use_summary: dict,
    query_terms: set[str],
) -> RecallSection | None:
    if not isinstance(tool_use_summary, dict):
        return None
    summary = _normalized_text(tool_use_summary.get("summary"))
    if not summary:
        return None
    if _score_text(summary, query_terms) <= 0 and query_terms:
        return None
    return RecallSection(title="Recent Tool Use Summary", content=summary)


def _build_reactive_compact_section(
    *,
    reactive_compact_context: dict,
    query_terms: set[str],
) -> RecallSection | None:
    if not isinstance(reactive_compact_context, dict):
        return None
    summary = _normalized_text(reactive_compact_context.get("summary_text"))
    preserved_tail = reactive_compact_context.get("preserved_tail")
    rendered_tail: list[str] = []
    if isinstance(preserved_tail, list):
        for item in preserved_tail:
            if not isinstance(item, dict):
                continue
            role = _normalized_text(item.get("role")) or "unknown"
            text = _normalized_text(item.get("text"))
            if not text:
                continue
            if query_terms and _score_text(f"{role} {text}", query_terms) <= 0:
                continue
            rendered_tail.append(f"- {role}: {text}")
    scored = _score_text(summary, query_terms)
    if query_terms and scored <= 0 and not rendered_tail:
        return None
    parts: list[str] = []
    if summary and (scored > 0 or not query_terms):
        parts.append(summary)
    if rendered_tail:
        parts.append("Preserved tail:")
        parts.extend(rendered_tail[:4])
    if not parts:
        return None
    return RecallSection(title="Recent Reactive Compaction", content="\n".join(parts))


def load_project_memory_markdown(
    project_memory_path: str | Path | None = None,
    *,
    repo_root: Path | None = None,
) -> str | None:
    if project_memory_path is None:
        return None
    raw_path = str(project_memory_path).strip()
    if not raw_path:
        return None
    root = repo_root or Path(__file__).resolve().parents[3]
    memory_file = Path(raw_path)
    if not memory_file.is_absolute():
        memory_file = root / memory_file
    if not memory_file.exists():
        return None
    try:
        content = memory_file.read_text(encoding="utf-8")
    except OSError:
        return None
    return content.strip() or None


def _render_recall_selection_prompt(query: str, candidates: list[RecallSection]) -> str:
    rendered_candidates = "\n\n".join(
        f"Title: {section.title}\nContent:\n{section.content}"
        for section in candidates
    )
    return f"Query:\n{query}\n\nCandidates:\n{rendered_candidates}"


def _parse_selected_titles(text: str) -> set[str]:
    normalized = _strip_code_fences(text).strip()
    if not normalized:
        return set()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    selected_titles = payload.get("selected_titles")
    if not isinstance(selected_titles, list):
        return set()
    return {
        title.strip()
        for title in selected_titles
        if isinstance(title, str) and title.strip()
    }


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)
