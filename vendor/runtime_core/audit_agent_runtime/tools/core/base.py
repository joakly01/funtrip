from __future__ import annotations

from abc import ABC, abstractmethod


class BaseTool(ABC):
    name: str
    description: str = ""
    input_schema: dict = {"type": "object", "properties": {}}
    include_in_definitions: bool = True

    @abstractmethod
    def execute(self, arguments: dict, context) -> dict:
        raise NotImplementedError

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
