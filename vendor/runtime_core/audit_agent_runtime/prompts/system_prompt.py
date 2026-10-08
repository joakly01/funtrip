from __future__ import annotations


def build_effective_system_prompt(
    *,
    default_system_prompt: list[str],
    main_thread_agent_prompt: str | None = None,
    custom_system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    override_system_prompt: str | None = None,
    coordinator_system_prompt: str | None = None,
    coordinator_mode: bool = False,
    proactive_mode: bool = False,
) -> list[str]:
    if override_system_prompt:
        return [override_system_prompt]

    if coordinator_mode and coordinator_system_prompt and not main_thread_agent_prompt:
        return [
            coordinator_system_prompt,
            *([append_system_prompt] if append_system_prompt else []),
        ]

    if main_thread_agent_prompt and proactive_mode:
        return [
            *default_system_prompt,
            f"\n# Custom Agent Instructions\n{main_thread_agent_prompt}",
            *([append_system_prompt] if append_system_prompt else []),
        ]

    return [
        *(
            [main_thread_agent_prompt]
            if main_thread_agent_prompt
            else [custom_system_prompt]
            if custom_system_prompt
            else default_system_prompt
        ),
        *([append_system_prompt] if append_system_prompt else []),
    ]
