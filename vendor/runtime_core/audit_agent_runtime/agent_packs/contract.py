from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from fastapi import APIRouter

from audit_agent_runtime.config.settings import Settings


@dataclass(slots=True, frozen=True)
class ExtensionContext:
    settings: Settings
    session_factory: object
    model_client: object
    task_registry: object
    queue_manager: object
    hooks: object = None


@dataclass(slots=True, frozen=True)
class ExtensionDefinition:
    agent_id: str
    routers: tuple[APIRouter, ...] = ()
    services: Mapping[str, object] = field(default_factory=dict)
    model_modules: tuple[str, ...] = ()
    schema_hooks: tuple[Callable[[object], None], ...] = ()
    startup_hooks: tuple[Callable[[], object], ...] = ()
    shutdown_hooks: tuple[Callable[[], object], ...] = ()
