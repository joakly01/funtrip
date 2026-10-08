from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ToolPoolContext:
    """Runtime view of which registered tool schemas are visible to the model."""

    visible_tool_names: frozenset[str] | None = None
    hidden_tool_names: frozenset[str] = field(
        default_factory=lambda: frozenset({"load_skill"})
    )

    def visible_definitions(self, tools: Iterable[dict]) -> list[dict]:
        return [
            tool
            for tool in tools
            if self.is_visible(_tool_name(tool))
        ]

    def is_visible(self, tool_name: str | None) -> bool:
        if not tool_name or tool_name in self.hidden_tool_names:
            return False
        if self.visible_tool_names is None:
            return True
        return tool_name in self.visible_tool_names


def build_tool_pool_context(
    *,
    model_visible_tool_names: set[str] | None = None,
    hidden_tool_names: set[str] | None = None,
) -> ToolPoolContext:
    return ToolPoolContext(
        visible_tool_names=(
            frozenset(model_visible_tool_names)
            if model_visible_tool_names is not None
            else None
        ),
        hidden_tool_names=frozenset(hidden_tool_names or {"load_skill"}),
    )


def _tool_name(tool: dict) -> str | None:
    name = tool.get("name")
    return name if isinstance(name, str) else None
