from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RuntimeEvent:
    type: str
    payload: dict = field(default_factory=dict)
