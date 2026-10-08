from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.models import (
    AgentSkillBindingRecord,
    ManagedAgentRecord,
    ManagedAgentVersionRecord,
)


class ManagedAgentRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_agents(self) -> list[ManagedAgentRecord]:
        return list(
            self.session.execute(
                select(ManagedAgentRecord).order_by(ManagedAgentRecord.created_at)
            ).scalars()
        )

    def get_agent(self, agent_id: str) -> ManagedAgentRecord | None:
        return self.session.get(ManagedAgentRecord, agent_id)

    def add_agent(self, record: ManagedAgentRecord) -> ManagedAgentRecord:
        self.session.add(record)
        self.session.flush()
        return record

    def list_bindings(self, agent_id: str) -> list[AgentSkillBindingRecord]:
        return list(
            self.session.execute(
                select(AgentSkillBindingRecord)
                .where(AgentSkillBindingRecord.agent_id == agent_id)
                .order_by(AgentSkillBindingRecord.skill_name)
            ).scalars()
        )

    def replace_bindings(self, agent_id: str, bindings: list[dict]) -> None:
        self.session.execute(
            delete(AgentSkillBindingRecord).where(AgentSkillBindingRecord.agent_id == agent_id)
        )
        for binding in bindings:
            self.session.add(
                AgentSkillBindingRecord(
                    agent_id=agent_id,
                    skill_name=binding["skill_name"],
                    skill_version=int(binding["version"]),
                    enabled=bool(binding.get("enabled", True)),
                )
            )
        self.session.flush()

    def add_version(
        self,
        *,
        agent_id: str,
        version: int,
        snapshot: dict,
        created_by: str,
    ) -> ManagedAgentVersionRecord:
        record = ManagedAgentVersionRecord(
            agent_id=agent_id,
            version=version,
            snapshot_json=snapshot,
            created_by=created_by,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_versions(self, agent_id: str) -> list[ManagedAgentVersionRecord]:
        return list(
            self.session.execute(
                select(ManagedAgentVersionRecord)
                .where(ManagedAgentVersionRecord.agent_id == agent_id)
                .order_by(ManagedAgentVersionRecord.version.desc())
            ).scalars()
        )

    def get_version(self, agent_id: str, version: int) -> ManagedAgentVersionRecord | None:
        return self.session.execute(
            select(ManagedAgentVersionRecord).where(
                ManagedAgentVersionRecord.agent_id == agent_id,
                ManagedAgentVersionRecord.version == version,
            )
        ).scalar_one_or_none()

    def delete_agent(self, agent_id: str) -> None:
        self.session.execute(
            delete(AgentSkillBindingRecord).where(
                AgentSkillBindingRecord.agent_id == agent_id
            )
        )
        self.session.execute(
            delete(ManagedAgentVersionRecord).where(
                ManagedAgentVersionRecord.agent_id == agent_id
            )
        )
        self.session.execute(
            delete(ManagedAgentRecord).where(ManagedAgentRecord.agent_id == agent_id)
        )
        self.session.flush()
