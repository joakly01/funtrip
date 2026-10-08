from __future__ import annotations

from audit_agent_runtime.prompts.cache import PromptSectionCache
from audit_agent_runtime.prompts.sections import PromptSection, PromptSectionSpec


class PromptSectionRegistry:
    """Claude Code 风格的 system prompt section registry。

    static section 可缓存并位于 dynamic boundary 前；dynamic section 每轮重算，
    用于 session/mode/context 相关提示，避免污染稳定前缀。
    """

    def __init__(self) -> None:
        self._static_specs: list[PromptSectionSpec] = []
        self._dynamic_specs: list[PromptSectionSpec] = []

    def register_static(self, name: str, compute) -> None:
        self._static_specs.append(PromptSectionSpec(name, compute, cacheable=True))

    def register_dynamic(self, name: str, compute) -> None:
        self._dynamic_specs.append(PromptSectionSpec(name, compute, cacheable=False))

    def extend_dynamic(self, specs: list[PromptSectionSpec]) -> None:
        for spec in specs:
            self._dynamic_specs.append(
                PromptSectionSpec(spec.name, spec.compute, cacheable=False)
            )

    def resolve_static(self, cache: PromptSectionCache) -> list[PromptSection]:
        return _resolve_sections(self._static_specs, cache)

    def resolve_dynamic(self, cache: PromptSectionCache) -> list[PromptSection]:
        return _resolve_sections(self._dynamic_specs, cache)


def _resolve_sections(
    specs: list[PromptSectionSpec],
    cache: PromptSectionCache,
) -> list[PromptSection]:
    sections: list[PromptSection] = []
    for spec in specs:
        if spec.cacheable and cache.has(spec.name):
            content = cache.get(spec.name)
        else:
            content = spec.compute()
            if spec.cacheable:
                cache.set(spec.name, content)
        if not content:
            continue
        title, body = _split_title_and_body(content)
        sections.append(
            PromptSection(
                name=spec.name,
                title=title,
                content=body,
                cacheable=spec.cacheable,
            )
        )
    return sections


def _split_title_and_body(content: str) -> tuple[str, str]:
    stripped = content.strip()
    if "\n" not in stripped:
        return "", stripped
    first_line, remainder = stripped.split("\n", 1)
    if first_line.startswith("#"):
        return first_line, remainder.strip()
    return "", stripped
