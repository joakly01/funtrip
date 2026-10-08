from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class PromptSection:
    name: str
    title: str
    content: str
    cacheable: bool = True

    def render(self) -> str:
        return f"{self.title}\n{self.content}".strip()


@dataclass(slots=True)
class PromptSectionSpec:
    name: str
    compute: Callable[[], str | None]
    cacheable: bool = True
