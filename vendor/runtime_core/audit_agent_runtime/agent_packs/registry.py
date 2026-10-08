from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter

from audit_agent_runtime.agent_packs.contract import ExtensionContext, ExtensionDefinition
from audit_agent_runtime.agent_packs.discovery import resolve_extensions_root
from audit_agent_runtime.config.settings import Settings


class ExtensionRegistry:
    def __init__(
        self,
        *,
        context: ExtensionContext,
        definitions: tuple[ExtensionDefinition, ...],
    ) -> None:
        self.context = context
        self._definitions = definitions
        self._services = _collect_services(definitions)

    @classmethod
    def discover(
        cls,
        settings: Settings,
        *,
        context: ExtensionContext,
    ) -> "ExtensionRegistry":
        definitions: list[ExtensionDefinition] = []
        for agent_dir in _iter_extension_dirs(settings):
            extension_path = agent_dir / "extension.py"
            if not extension_path.is_file():
                continue
            agent_id = agent_dir.name
            module = _import_extension_module(agent_id, extension_path)
            factory = getattr(module, "create_extension", None)
            if not callable(factory):
                raise ValueError(
                    f"extension {agent_id!r} must export create_extension(context): "
                    f"{extension_path}"
                )
            definition = factory(context)
            if not isinstance(definition, ExtensionDefinition):
                raise TypeError(
                    f"extension {agent_id!r} returned "
                    f"{type(definition).__name__}, expected ExtensionDefinition"
                )
            if definition.agent_id != agent_id:
                raise ValueError(
                    f"extension agent id mismatch: discovered {agent_id!r}, "
                    f"definition returned {definition.agent_id!r}"
                )
            definitions.append(definition)
        return cls(context=context, definitions=tuple(definitions))

    def definitions(self) -> tuple[ExtensionDefinition, ...]:
        return self._definitions

    def routers(self) -> tuple[APIRouter, ...]:
        return tuple(
            router
            for definition in self._definitions
            for router in definition.routers
        )

    def service(self, name: str, default: object = None) -> object:
        return self._services.get(name, default)

    def model_modules(self) -> tuple[str, ...]:
        return tuple(
            module
            for definition in self._definitions
            for module in definition.model_modules
        )

    def schema_hooks(self):
        return tuple(
            hook
            for definition in self._definitions
            for hook in definition.schema_hooks
        )

    def startup_hooks(self):
        return tuple(
            hook
            for definition in self._definitions
            for hook in definition.startup_hooks
        )

    def shutdown_hooks(self):
        return tuple(
            hook
            for definition in self._definitions
            for hook in definition.shutdown_hooks
        )


def _collect_services(
    definitions: tuple[ExtensionDefinition, ...],
) -> dict[str, object]:
    services: dict[str, object] = {}
    for definition in definitions:
        for name, service in definition.services.items():
            if name in services:
                raise ValueError(f"duplicate extension service name: {name}")
            services[name] = service
    return services


def _import_extension_module(agent_id: str, module_path: Path) -> ModuleType:
    module_name = f"_audit_runtime_extension_{agent_id}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to import extension module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def prepare_extension_schemas(settings: Settings, engine: object) -> None:
    for agent_id, module in _iter_extension_modules(settings):
        for module_name in _module_names(module, "MODEL_MODULES"):
            importlib.import_module(module_name)
        hook = getattr(module, "prepare_schema", None)
        if hook is not None:
            if not callable(hook):
                raise TypeError(f"extension {agent_id!r} prepare_schema must be callable")
            hook(engine)


def migrate_extension_schemas(settings: Settings, engine: object) -> None:
    for agent_id, module in _iter_extension_modules(settings):
        hook = getattr(module, "migrate_schema", None)
        if hook is not None:
            if not callable(hook):
                raise TypeError(f"extension {agent_id!r} migrate_schema must be callable")
            hook(engine)


def _iter_extension_modules(settings: Settings):
    for agent_dir in _iter_extension_dirs(settings):
        extension_path = agent_dir / "extension.py"
        if extension_path.is_file():
            yield agent_dir.name, _import_extension_module(agent_dir.name, extension_path)


def _iter_extension_dirs(settings: Settings) -> tuple[Path, ...]:
    package_root = (Path(__file__).resolve().parents[1] / "extensions").resolve()
    configured_root = resolve_extensions_root(settings)
    roots = [package_root]
    if configured_root != package_root:
        roots.append(configured_root)

    directories: dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for agent_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            existing = directories.get(agent_dir.name)
            if existing is not None and existing != agent_dir:
                raise ValueError(
                    f"duplicate extension id {agent_dir.name!r}: {existing} and {agent_dir}"
                )
            directories[agent_dir.name] = agent_dir
    return tuple(directories[name] for name in sorted(directories))


def _module_names(module: ModuleType, attribute: str) -> tuple[str, ...]:
    value = getattr(module, attribute, ())
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{module.__name__}.{attribute} must be a sequence of module names")
    return tuple(value)
