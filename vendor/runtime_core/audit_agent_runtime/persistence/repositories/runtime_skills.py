from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from audit_agent_runtime.persistence.models import (
    RuntimeSkillFileRecord,
    RuntimeSkillRecord,
    RuntimeSkillVersionRecord,
)


class RuntimeSkillRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_skill(self, skill_name: str) -> RuntimeSkillRecord | None:
        return self.session.get(RuntimeSkillRecord, skill_name)

    def list_active_skills(self) -> list[RuntimeSkillRecord]:
        return list(
            self.session.execute(
                select(RuntimeSkillRecord)
                .where(RuntimeSkillRecord.status == "active")
                .order_by(RuntimeSkillRecord.skill_name)
            ).scalars()
        )

    def list_versions(self, skill_name: str) -> list[RuntimeSkillVersionRecord]:
        self.ensure_current_version_record(skill_name)
        return self._list_versions_raw(skill_name)

    def _list_versions_raw(self, skill_name: str) -> list[RuntimeSkillVersionRecord]:
        return list(
            self.session.execute(
                select(RuntimeSkillVersionRecord)
                .where(RuntimeSkillVersionRecord.skill_name == skill_name)
                .order_by(RuntimeSkillVersionRecord.version.desc())
            ).scalars()
        )

    def get_version(self, skill_name: str, version: int) -> RuntimeSkillVersionRecord | None:
        self.ensure_current_version_record(skill_name)
        return self.session.execute(
            select(RuntimeSkillVersionRecord).where(
                RuntimeSkillVersionRecord.skill_name == skill_name,
                RuntimeSkillVersionRecord.version == version,
            )
        ).scalar_one_or_none()

    def ensure_current_version_record(self, skill_name: str) -> None:
        record = self.get_skill(skill_name)
        if record is None:
            return
        exists = self.session.execute(
            select(RuntimeSkillVersionRecord.id).where(
                RuntimeSkillVersionRecord.skill_name == skill_name,
                RuntimeSkillVersionRecord.version == int(record.current_version),
            )
        ).scalar_one_or_none()
        if exists is not None:
            return
        self.session.add(
            RuntimeSkillVersionRecord(
                skill_name=skill_name,
                version=int(record.current_version),
                display_name=record.display_name,
                description=record.description,
                skill_markdown=record.skill_markdown,
                metadata_json=record.metadata_json or {},
                created_by=record.created_by or "system",
                change_note="Backfilled current version",
            )
        )
        self.session.flush()

    def list_files(self, skill_name: str, version: int | None = None) -> list[RuntimeSkillFileRecord]:
        target_version = version
        if target_version is None:
            record = self.get_skill(skill_name)
            if record is None:
                return []
            target_version = int(record.current_version)
        return list(
            self.session.execute(
                select(RuntimeSkillFileRecord)
                .where(
                    RuntimeSkillFileRecord.skill_name == skill_name,
                    RuntimeSkillFileRecord.version == target_version,
                )
                .order_by(RuntimeSkillFileRecord.relative_path)
            ).scalars()
        )

    def get_file(
        self,
        skill_name: str,
        relative_path: str,
        version: int | None = None,
    ) -> RuntimeSkillFileRecord | None:
        target_version = version
        if target_version is None:
            record = self.get_skill(skill_name)
            if record is None:
                return None
            target_version = int(record.current_version)
        return self.session.execute(
            select(RuntimeSkillFileRecord).where(
                RuntimeSkillFileRecord.skill_name == skill_name,
                RuntimeSkillFileRecord.version == target_version,
                RuntimeSkillFileRecord.relative_path == relative_path,
            )
        ).scalar_one_or_none()

    def upsert_skill(
        self,
        *,
        skill_name: str,
        display_name: str | None,
        description: str,
        skill_markdown: str,
        metadata: dict,
        files: dict[str, bytes],
        created_by: str,
        preserve_existing_files: bool = False,
        change_note: str | None = None,
    ) -> RuntimeSkillRecord:
        existing = self.get_skill(skill_name)
        if existing is None:
            version = 1
            record = RuntimeSkillRecord(
                skill_name=skill_name,
                display_name=display_name,
                description=description,
                skill_markdown=skill_markdown,
                current_version=version,
                status="active",
                created_by=created_by,
                metadata_json=metadata,
            )
            self.session.add(record)
        else:
            previous_version = int(existing.current_version or 0)
            version = previous_version + 1
            existing.display_name = display_name
            existing.description = description
            existing.skill_markdown = skill_markdown
            existing.current_version = version
            existing.status = "active"
            existing.metadata_json = metadata
            if not existing.created_by:
                existing.created_by = created_by
            record = existing
        version_files = dict(files)
        if existing is not None and preserve_existing_files:
            for file_record in self.list_files(skill_name, previous_version):
                version_files.setdefault(file_record.relative_path, file_record.content_bytes)
        self.session.flush()
        self.session.add(
            RuntimeSkillVersionRecord(
                skill_name=skill_name,
                version=version,
                display_name=display_name,
                description=description,
                skill_markdown=skill_markdown,
                metadata_json=metadata,
                created_by=created_by,
                change_note=change_note,
            )
        )
        for relative_path, content in sorted(version_files.items()):
            self.session.add(
                RuntimeSkillFileRecord(
                    skill_name=skill_name,
                    version=version,
                    relative_path=relative_path,
                    content_bytes=content,
                )
            )
        self.session.flush()
        return record

    def restore_version(
        self,
        *,
        skill_name: str,
        version: int,
        created_by: str,
    ) -> RuntimeSkillRecord | None:
        target = self.get_version(skill_name, version)
        if target is None:
            return None
        files = {
            file_record.relative_path: file_record.content_bytes
            for file_record in self.list_files(skill_name, version)
        }
        return self.upsert_skill(
            skill_name=skill_name,
            display_name=target.display_name,
            description=target.description,
            skill_markdown=target.skill_markdown,
            metadata=target.metadata_json or {},
            files=files or {"SKILL.md": target.skill_markdown.encode("utf-8")},
            created_by=created_by,
            preserve_existing_files=False,
            change_note=f"Restore version {version}",
        )

    def delete_version(self, skill_name: str, version: int) -> RuntimeSkillRecord | None:
        version_record = self.get_version(skill_name, version)
        if version_record is None:
            return None
        for file_record in self.list_files(skill_name, version):
            self.session.delete(file_record)
        self.session.delete(version_record)
        self.session.flush()

        remaining = self._list_versions_raw(skill_name)
        if not remaining:
            skill_record = self.get_skill(skill_name)
            if skill_record is not None:
                self.session.delete(skill_record)
            self.session.flush()
            return None

        promoted = remaining[0]
        skill_record = self.get_skill(skill_name)
        if skill_record is None:
            return None
        skill_record.display_name = promoted.display_name
        skill_record.description = promoted.description
        skill_record.skill_markdown = promoted.skill_markdown
        skill_record.current_version = promoted.version
        skill_record.metadata_json = promoted.metadata_json or {}
        skill_record.status = "active"
        self.session.flush()
        return skill_record

    def delete_skill(self, skill_name: str) -> bool:
        record = self.get_skill(skill_name)
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True
