from __future__ import annotations

import re

from sqlalchemy.exc import SQLAlchemyError

from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.persistence.db import build_session_factory
from audit_agent_runtime.persistence.models import ManagedAgentRecord
from audit_agent_runtime.persistence.repositories.runtime_skills import RuntimeSkillRepository

from .repositories import ManagedAgentRepository


_AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


class AgentManagementError(ValueError):
    pass


class AgentNotFoundError(AgentManagementError):
    pass


class AgentConflictError(AgentManagementError):
    pass


class AgentValidationError(AgentManagementError):
    def __init__(self, validation: dict):
        self.validation = validation
        message = "; ".join(validation.get("errors", [])) or "Agent validation failed"
        super().__init__(message)


class AgentManagementService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session_factory = build_session_factory(settings)

    def create_agent(
        self,
        *,
        agent_id: str,
        display_name: str,
        description: str,
        created_by: str = "system",
    ) -> dict:
        normalized_id = _normalize_agent_id(agent_id)
        if not display_name.strip():
            raise AgentValidationError({"errors": ["Agent name is required."]})
        with self.session_factory() as db:
            repository = ManagedAgentRepository(db)
            if repository.get_agent(normalized_id) is not None:
                raise AgentConflictError(f"Agent already exists: {normalized_id}")
            record = repository.add_agent(
                ManagedAgentRecord(
                    agent_id=normalized_id,
                    display_name=display_name.strip(),
                    description=description.strip(),
                    created_by=created_by.strip() or "system",
                )
            )
            db.commit()
            db.refresh(record)
            return self._agent_item(db, record)

    def list_agents(self) -> list[dict]:
        try:
            with self.session_factory() as db:
                repository = ManagedAgentRepository(db)
                return [self._agent_item(db, record) for record in repository.list_agents()]
        except SQLAlchemyError:
            return []

    def get_agent(self, agent_id: str) -> dict:
        with self.session_factory() as db:
            record = self._require_agent(db, agent_id)
            return self._agent_item(db, record)

    def list_agent_bindings(self, agent_id: str) -> list[dict]:
        normalized_id = str(agent_id or "").strip().lower()
        try:
            with self.session_factory() as db:
                return [
                    _binding_item(record)
                    for record in ManagedAgentRepository(db).list_bindings(normalized_id)
                    if record.enabled
                ]
        except SQLAlchemyError:
            return []

    def replace_agent_bindings(self, agent_id: str, *, skill_bindings: list[dict]) -> list[dict]:
        normalized_id = str(agent_id or "").strip().lower()
        if not normalized_id:
            raise AgentValidationError({"errors": ["Agent ID is required."]})
        normalized = _normalize_bindings(skill_bindings)
        with self.session_factory() as db:
            repository = ManagedAgentRepository(db)
            repository.replace_bindings(normalized_id, normalized)
            db.commit()
            return [_binding_item(record) for record in repository.list_bindings(normalized_id)]

    def get_published_snapshot(self, agent_id: str) -> dict | None:
        with self.session_factory() as db:
            record = ManagedAgentRepository(db).get_agent(str(agent_id or "").strip().lower())
            if record is None or record.status != "published" or not record.current_version:
                return None
            version = ManagedAgentRepository(db).get_version(record.agent_id, record.current_version)
            return dict(version.snapshot_json or {}) if version is not None else None

    def update_agent(
        self,
        agent_id: str,
        *,
        agent_markdown: str,
        project_memory: str,
        tool_names: list[str],
        skill_bindings: list[dict],
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict:
        normalized_bindings = _normalize_bindings(skill_bindings)
        normalized_tools = sorted({name.strip() for name in tool_names if name.strip()})
        with self.session_factory() as db:
            record = self._require_agent(db, agent_id)
            if display_name is not None and display_name.strip():
                record.display_name = display_name.strip()
            if description is not None:
                record.description = description.strip()
            record.agent_markdown = agent_markdown.strip()
            record.project_memory = project_memory.strip()
            record.tool_names_json = normalized_tools
            record.status = "draft"
            repository = ManagedAgentRepository(db)
            repository.replace_bindings(record.agent_id, normalized_bindings)
            db.commit()
            db.refresh(record)
            return self._agent_item(db, record)

    def validate_agent(self, agent_id: str, *, available_tool_names: set[str]) -> dict:
        with self.session_factory() as db:
            record = self._require_agent(db, agent_id)
            bindings = ManagedAgentRepository(db).list_bindings(record.agent_id)
            errors: list[str] = []
            selected_tools = set(record.tool_names_json or [])
            unavailable_tools = sorted(selected_tools - available_tool_names)
            if unavailable_tools:
                errors.append(f"Unknown tools: {', '.join(unavailable_tools)}")
            if not record.agent_markdown.strip():
                errors.append("AGENT.md is required.")

            required_tools: set[str] = set()
            skill_repository = RuntimeSkillRepository(db)
            for binding in bindings:
                version = skill_repository.get_version(binding.skill_name, binding.skill_version)
                if version is None:
                    errors.append(
                        f"Skill version not found: {binding.skill_name}@v{binding.skill_version}"
                    )
                    continue
                allowed = (version.metadata_json or {}).get("allowed-tools") or []
                if isinstance(allowed, str):
                    allowed = allowed.split()
                required_tools.update(
                    item for item in allowed if isinstance(item, str) and item != "*"
                )
            missing_tools = sorted(required_tools - selected_tools)
            if missing_tools:
                errors.append(f"Missing tools: {', '.join(missing_tools)}")
            return {
                "ok": not errors,
                "errors": errors,
                "missing_tools": missing_tools,
                "unavailable_tools": unavailable_tools,
                "skill_count": len(bindings),
                "tool_count": len(selected_tools),
            }

    def publish_agent(
        self,
        agent_id: str,
        *,
        available_tool_names: set[str],
        created_by: str = "system",
    ) -> dict:
        validation = self.validate_agent(agent_id, available_tool_names=available_tool_names)
        if not validation["ok"]:
            raise AgentValidationError(validation)
        with self.session_factory() as db:
            record = self._require_agent(db, agent_id)
            repository = ManagedAgentRepository(db)
            bindings = repository.list_bindings(record.agent_id)
            version = int(record.current_version or 0) + 1
            snapshot = {
                "agent_id": record.agent_id,
                "display_name": record.display_name,
                "description": record.description,
                "agent_markdown": record.agent_markdown,
                "project_memory": record.project_memory,
                "tool_names": list(record.tool_names_json or []),
                "skill_bindings": [_binding_item(item) for item in bindings],
            }
            repository.add_version(
                agent_id=record.agent_id,
                version=version,
                snapshot=snapshot,
                created_by=created_by.strip() or "system",
            )
            record.current_version = version
            record.status = "published"
            db.commit()
            db.refresh(record)
            return self._agent_item(db, record)

    def list_versions(self, agent_id: str) -> list[dict]:
        with self.session_factory() as db:
            self._require_agent(db, agent_id)
            return [
                {
                    "version": record.version,
                    "snapshot": record.snapshot_json or {},
                    "created_by": record.created_by,
                    "published_at": record.published_at.isoformat() if record.published_at else None,
                }
                for record in ManagedAgentRepository(db).list_versions(agent_id)
            ]

    def delete_agent(self, agent_id: str) -> dict:
        with self.session_factory() as db:
            record = self._require_agent(db, agent_id)
            normalized_id = record.agent_id
            ManagedAgentRepository(db).delete_agent(normalized_id)
            db.commit()
            return {"agent_id": normalized_id, "deleted": True}

    def _require_agent(self, db, agent_id: str) -> ManagedAgentRecord:
        normalized_id = _normalize_agent_id(agent_id)
        record = ManagedAgentRepository(db).get_agent(normalized_id)
        if record is None:
            raise AgentNotFoundError(f"Agent not found: {normalized_id}")
        return record

    def _agent_item(self, db, record: ManagedAgentRecord) -> dict:
        bindings = ManagedAgentRepository(db).list_bindings(record.agent_id)
        return {
            "agent_id": record.agent_id,
            "display_name": record.display_name,
            "description": record.description,
            "source_kind": "managed",
            "editable": True,
            "status": record.status,
            "current_version": int(record.current_version or 0),
            "agent_markdown": record.agent_markdown,
            "project_memory": record.project_memory,
            "tool_names": list(record.tool_names_json or []),
            "skill_bindings": [_binding_item(item) for item in bindings],
            "created_by": record.created_by,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }


def _normalize_agent_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _AGENT_ID_PATTERN.fullmatch(normalized):
        raise AgentValidationError(
            {"errors": ["Agent ID must use lowercase letters, numbers, underscores, or hyphens."]}
        )
    return normalized


def _normalize_bindings(bindings: list[dict]) -> list[dict]:
    normalized: dict[str, dict] = {}
    for binding in bindings:
        name = str(binding.get("skill_name") or "").strip()
        version = int(binding.get("version") or 0)
        if not name or version <= 0:
            raise AgentValidationError({"errors": ["Skill bindings require a name and version."]})
        normalized[name] = {
            "skill_name": name,
            "version": version,
            "enabled": bool(binding.get("enabled", True)),
        }
    return [normalized[name] for name in sorted(normalized)]


def _binding_item(record) -> dict:
    return {
        "skill_name": record.skill_name,
        "version": int(record.skill_version),
        "enabled": bool(record.enabled),
    }
