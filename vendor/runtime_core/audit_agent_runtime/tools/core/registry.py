from __future__ import annotations

from copy import deepcopy

from audit_agent_runtime.tools.core.base import BaseTool


class ToolRegistry:
    def __init__(self, tools: list[BaseTool] | None = None):
        self._tools = {tool.name: tool for tool in (tools or [])}
        self._definition_cache: list[dict] | None = None

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        self._definition_cache = None

    def get(self, tool_name: str) -> BaseTool:
        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {tool_name}") from exc

    def list(self) -> list[BaseTool]:
        return list(self._tools.values())

    def definitions(self) -> list[dict]:
        if self._definition_cache is None:
            # 工具 schema 位于模型 payload 的大块前缀中；同一 registry 生命周期内缓存
            # 首次渲染结果，避免动态描述或中途 feature gate 漂移造成 payload bytes 抖动。
            self._definition_cache = [
                deepcopy(tool.definition())
                for tool in self._tools.values()
                if getattr(tool, "include_in_definitions", True)
            ]
        return deepcopy(self._definition_cache)
