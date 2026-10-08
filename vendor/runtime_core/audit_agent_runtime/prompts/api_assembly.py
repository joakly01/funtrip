from __future__ import annotations

from audit_agent_runtime.llm.base import ModelMessage
from audit_agent_runtime.prompts.builder import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
from audit_agent_runtime.prompts.reminders import wrap_in_system_reminder


def split_sys_prompt_prefix(
    system_prompt: list[str],
) -> list[dict[str, str | None]]:
    boundary_index = next(
        (index for index, block in enumerate(system_prompt) if block == SYSTEM_PROMPT_DYNAMIC_BOUNDARY),
        -1,
    )
    static_blocks: list[str] = []
    dynamic_blocks: list[str] = []
    for index, block in enumerate(system_prompt):
        if not block or block == SYSTEM_PROMPT_DYNAMIC_BOUNDARY:
            continue
        if boundary_index != -1 and index > boundary_index:
            dynamic_blocks.append(block)
        else:
            static_blocks.append(block)

    result: list[dict[str, str | None]] = []
    static_text = "\n\n".join(static_blocks)
    dynamic_text = "\n\n".join(dynamic_blocks)
    if static_text:
        result.append({"text": static_text, "cache_scope": "org"})
    if dynamic_text:
        result.append({"text": dynamic_text, "cache_scope": None})
    return result


def build_system_prompt_blocks(
    system_prompt: list[str],
) -> list[dict[str, str]]:
    return [
        {"type": "text", "text": block["text"]}
        for block in split_sys_prompt_prefix(system_prompt)
        if block["text"]
    ]


def append_system_context(
    system_prompt_blocks: list[str],
    context: dict[str, str],
) -> list[str]:
    if not context:
        return system_prompt_blocks
    filtered_items = [
        (key, value)
        for key, value in context.items()
        if value is not None and not (key == "input_mode" and value == "prompt")
    ]
    if not filtered_items:
        return system_prompt_blocks
    rendered_blocks: list[str] = []
    runtime_context_lines: list[str] = []
    for key, value in filtered_items:
        if "\n" in value:
            rendered_blocks.append(value)
        else:
            runtime_context_lines.append(f"{key}: {value}")
    if runtime_context_lines:
        rendered_blocks.append("# Runtime Context\n" + "\n".join(runtime_context_lines))
    context_block = "\n\n".join(rendered_blocks)
    return [*system_prompt_blocks, context_block]


def prepend_user_context(
    messages: list[ModelMessage],
    context: dict[str, str],
) -> list[ModelMessage]:
    if not context:
        return messages
    filtered_items = [(key, value) for key, value in context.items() if value is not None]
    if not filtered_items:
        return messages
    reminder = wrap_in_system_reminder(
        "As you answer the user's questions, you can use the following context:\n"
        + "\n".join(f"# {key}\n{value}" for key, value in filtered_items)
        + "\n\nIMPORTANT: this context may or may not be relevant to your tasks. "
        "You should not respond to this context unless it is highly relevant to your task."
    )
    insertion_index = 0
    while insertion_index < len(messages) and messages[insertion_index].role == "system":
        insertion_index += 1
    return [
        *messages[:insertion_index],
        ModelMessage(role="user", content=f"{reminder}\n"),
        *messages[insertion_index:],
    ]
