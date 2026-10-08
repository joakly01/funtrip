from __future__ import annotations


class PromptSectionCache:
    def __init__(self):
        self._entries: dict[str, str | None] = {}

    def get(self, name: str) -> str | None:
        return self._entries.get(name)

    def has(self, name: str) -> bool:
        return name in self._entries

    def set(self, name: str, value: str | None) -> None:
        self._entries[name] = value

    def clear(self) -> None:
        self._entries.clear()
