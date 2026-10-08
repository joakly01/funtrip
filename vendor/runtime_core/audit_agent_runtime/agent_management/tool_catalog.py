from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import logging
from pathlib import Path

from audit_agent_runtime.agent_packs.discovery import discover_agent_packs
from audit_agent_runtime.agent_packs.loader import load_tools_from_agent_pack
from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.tools.core.base import BaseTool
from audit_agent_runtime.tools.core.registry import ToolRegistry


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentToolCatalog:
    """Read-only catalog of model-visible main and Extension tools."""

    settings: Settings
    main_registry: ToolRegistry
    session_factory: object
    model_client: object

    def list_items(self) -> list[dict]:
        items, _ = self._collect()
        return items

    def selectable_names(self) -> set[str]:
        _, tools_by_name = self._collect()
        return set(tools_by_name)

    def resolve(self, tool_names: list[str]) -> tuple[list[BaseTool], list[str]]:
        _, tools_by_name = self._collect()
        requested = [str(name).strip() for name in tool_names if str(name).strip()]
        resolved = [tools_by_name[name] for name in requested if name in tools_by_name]
        unresolved = sorted({name for name in requested if name not in tools_by_name})
        return resolved, unresolved

    def _collect(self) -> tuple[list[dict], dict[str, BaseTool]]:
        main_tools = [
            tool
            for tool in self.main_registry.list()
            if getattr(tool, "include_in_definitions", True)
        ]
        tools_by_name = {tool.name: tool for tool in main_tools}
        items = [
            _catalog_item(
                tool,
                source_kind="main",
                source_agent_id=None,
                selectable=True,
            )
            for tool in main_tools
        ]
        for pack in discover_agent_packs(self.settings):
            try:
                extension_tools = load_tools_from_agent_pack(
                    Path(pack.agent_dir),
                    agent_id=pack.agent_id,
                    settings=self.settings,
                    session_factory=self.session_factory,
                    model_client=self.model_client,
                )
            except (ImportError, OSError, TypeError, ValueError) as exc:
                logger.warning("Unable to load tools for extension %s: %s", pack.agent_id, exc)
                continue
            for tool in extension_tools:
                if not getattr(tool, "include_in_definitions", True):
                    continue
                selectable = tool.name not in tools_by_name
                conflict_reason = (
                    None
                    if selectable
                    else f"Tool name already registered: {tool.name}"
                )
                items.append(
                    _catalog_item(
                        tool,
                        source_kind="extension",
                        source_agent_id=pack.agent_id,
                        selectable=selectable,
                        conflict_reason=conflict_reason,
                    )
                )
                if selectable:
                    tools_by_name[tool.name] = tool
        return items, tools_by_name


def _catalog_item(
    tool: BaseTool,
    *,
    source_kind: str,
    source_agent_id: str | None,
    selectable: bool,
    conflict_reason: str | None = None,
) -> dict:
    definition = tool.definition()
    return {
        "name": tool.name,
        "description": str(definition.get("description") or ""),
        "input_schema": deepcopy(definition.get("input_schema") or {}),
        "source_kind": source_kind,
        "source_agent_id": source_agent_id,
        "selectable": selectable,
        "conflict_reason": conflict_reason,
    }
