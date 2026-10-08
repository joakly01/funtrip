from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages: Mapped[list["MessageRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageRecord.id",
    )
    runtime_state: Mapped["RuntimeStateRecord | None"] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        uselist=False,
    )
    query_tasks: Mapped[list["QueryTaskRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="QueryTaskRecord.created_at",
    )
    queued_commands: Mapped[list["QueuedCommandRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="QueuedCommandRecord.id",
    )
    session_events: Mapped[list["SessionEventRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionEventRecord.id",
    )
    session_memories: Mapped[list["SessionMemoryRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionMemoryRecord.version",
    )
    session_compactions: Mapped[list["SessionCompactionRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionCompactionRecord.created_at",
    )


class MessageRecord(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[SessionRecord] = relationship(back_populates="messages")


class RuntimeStateRecord(Base):
    __tablename__ = "session_runtime_state"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), primary_key=True
    )
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    session: Mapped[SessionRecord] = relationship(back_populates="runtime_state")


class QueryTaskRecord(Base):
    __tablename__ = "query_tasks"

    task_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    parent_task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    agent_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    session: Mapped[SessionRecord] = relationship(back_populates="query_tasks")


class QueuedCommandRecord(Base):
    __tablename__ = "queued_commands"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    target_agent_id: Mapped[str | None] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    priority: Mapped[str] = mapped_column(String(16), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[SessionRecord] = relationship(back_populates="queued_commands")


class SessionEventRecord(Base):
    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[str] = mapped_column(String(255), index=True)
    sequence: Mapped[int] = mapped_column(index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[SessionRecord] = relationship(back_populates="session_events")


class SessionMemoryRecord(Base):
    __tablename__ = "session_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(index=True)
    summary_markdown: Mapped[str] = mapped_column(String)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    summarizes_until_message_id: Mapped[int | None] = mapped_column(index=True)
    source_turn: Mapped[int | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    session: Mapped[SessionRecord] = relationship(back_populates="session_memories")


class SessionCompactionRecord(Base):
    __tablename__ = "session_compactions"

    compaction_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), index=True
    )
    boundary_message_id: Mapped[int | None] = mapped_column(index=True)
    summary_text: Mapped[str] = mapped_column(String)
    preserved_from_message_id: Mapped[int | None] = mapped_column(index=True)
    preserved_to_message_id: Mapped[int | None] = mapped_column(index=True)
    reason: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[SessionRecord] = relationship(back_populates="session_compactions")


class RuntimeSkillRecord(Base):
    __tablename__ = "runtime_skills"

    skill_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String, nullable=False)
    skill_markdown: Mapped[str] = mapped_column(String, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default="system")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    files: Mapped[list["RuntimeSkillFileRecord"]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        order_by=lambda: (RuntimeSkillFileRecord.version, RuntimeSkillFileRecord.relative_path),
    )
    versions: Mapped[list["RuntimeSkillVersionRecord"]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        order_by="RuntimeSkillVersionRecord.version",
    )


class RuntimeSkillVersionRecord(Base):
    __tablename__ = "runtime_skill_versions"
    __table_args__ = (UniqueConstraint("skill_name", "version", name="uq_runtime_skill_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    skill_name: Mapped[str] = mapped_column(
        ForeignKey("runtime_skills.skill_name", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String, nullable=False)
    skill_markdown: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default="system")
    change_note: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    skill: Mapped[RuntimeSkillRecord] = relationship(back_populates="versions")


class RuntimeSkillFileRecord(Base):
    __tablename__ = "runtime_skill_files"
    __table_args__ = (
        UniqueConstraint(
            "skill_name",
            "version",
            "relative_path",
            name="uq_runtime_skill_file_version_path",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    skill_name: Mapped[str] = mapped_column(
        ForeignKey("runtime_skills.skill_name", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    skill: Mapped[RuntimeSkillRecord] = relationship(back_populates="files")


class ManagedAgentRecord(Base):
    __tablename__ = "managed_agents"

    agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    agent_markdown: Mapped[str] = mapped_column(String, nullable=False, default="")
    project_memory: Mapped[str] = mapped_column(String, nullable=False, default="")
    tool_names_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="draft")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ManagedAgentVersionRecord(Base):
    __tablename__ = "managed_agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_managed_agent_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default="system")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentSkillBindingRecord(Base):
    __tablename__ = "agent_skill_bindings"
    __table_args__ = (
        UniqueConstraint("agent_id", "skill_name", name="uq_agent_skill_binding"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    skill_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    skill_version: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
