from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from audit_agent_runtime.agent_packs.discovery import (
    AgentPackInfo,
    AgentInstructionDocument,
    discover_agent_packs,
    resolve_agents_root,
    load_agent_instruction_document,
)
from audit_agent_runtime.agent_packs.loader import load_skills_from_agent_pack, load_tools_from_agent_pack
from audit_agent_runtime.agents.runner import SubagentRunner
from audit_agent_runtime.compaction.engine import CompactionEngine
from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.control_plane import AgentTool, SendMessageTool, TaskStopTool
from audit_agent_runtime.prompts.builder import PromptBuilder, SYSTEM_PROMPT_DYNAMIC_BOUNDARY
from audit_agent_runtime.prompts.reminders import wrap_in_system_reminder
from audit_agent_runtime.querying.query_engine import QueryEngine
from audit_agent_runtime.runtime.engine import RuntimeEngine
from audit_agent_runtime.runtime.loop import RuntimeLoop
from audit_agent_runtime.skills.core.registry import SkillRegistry
from audit_agent_runtime.skills.core.skill_tool import LoadSkillAliasTool, NamedSkillTool, SkillTool
from audit_agent_runtime.tasks.in_process_runner import InProcessTaskRunner
from audit_agent_runtime.tasks.queue_manager import QueueManager
from audit_agent_runtime.tasks.task_registry import TaskRegistry
from audit_agent_runtime.tools.builtin.interaction import build_interaction_tools
from audit_agent_runtime.tools.builtin.tool_results import ReadSessionMessagesTool, ReadToolResultTool
from audit_agent_runtime.tools.business.skill_runtime import (
    ReadSkillFileTool,
    build_skill_script_tools,
)
from audit_agent_runtime.tools.core.registry import ToolRegistry
from audit_agent_runtime.agent_management.service import AgentManagementService
from audit_agent_runtime.agent_management.tool_catalog import AgentToolCatalog
from audit_agent_runtime.skill_management.loader import load_database_skill_versions


_DEFAULT_AGENT_ALIASES = frozenset({"", "default", "main", "ontology"})


class AgentPackPromptBuilder(PromptBuilder):
    def __init__(
        self,
        *,
        instruction_document: AgentInstructionDocument | None,
        agent_id: str,
        project_memory: str | None = None,
    ):
        super().__init__()
        self._instruction_document = instruction_document
        self._agent_id = agent_id
        self._project_memory = (project_memory or "").strip()

    @staticmethod
    def _build_intro_section() -> str:
        return """
# Intro
You are an interactive specialist agent hosted by the Agent Runtime. Help the user within the role, instructions, skills, and tools configured for the current Agent.

IMPORTANT: Do not fabricate facts, evidence, identifiers, URLs, tool results, or workflow outcomes. Use only user-provided context and trusted tool results.
"""

    @staticmethod
    def _build_doing_tasks_section() -> str:
        return """
# Doing tasks
- Work only within the current Agent's declared role and registered capabilities.
- Inspect relevant user context or tool evidence before drawing conclusions.
- If a request falls outside this Agent, state the boundary plainly instead of pretending to have another Agent's tools.
- Diagnose failed approaches before retrying and report verification honestly.
- Do not expose internal runtime bookkeeping or local filesystem paths.
"""

    @staticmethod
    def _build_audit_ontology_principles_section() -> str:
        return ""

    @staticmethod
    def _build_actions_section() -> str:
        return """
# Executing actions with care
Consider reversibility and blast radius. Read-only inspection is normally safe; shared-system writes, external effects, destructive operations, and governed workflow changes require the configured permission and confirmation policy.
"""

    def build_blocks(
        self,
        *,
        skill_registry: SkillRegistry,
        tool_registry: ToolRegistry,
        additional_sections: list[str] | None = None,
        coordinator_mode: bool = False,
        plan_mode: bool = False,
    ) -> list[str]:
        header = (
            f"# Agent: {self._agent_id}\n"
            "你是当前配置的专用助手。遵循当前 Agent 指令与已加载 Skill；"
            "不要调用未在当前 Agent 注册的工具。"
        )
        return super().build_blocks(
            skill_registry=skill_registry,
            tool_registry=tool_registry,
            additional_sections=[header, *(additional_sections or [])],
            coordinator_mode=coordinator_mode,
            plan_mode=plan_mode,
        )

    def build_agent_instruction_reminders(self) -> list[str]:
        document = self._instruction_document
        reminders = []
        if document is not None:
            reminders.append(
                wrap_in_system_reminder(
                    f"Agent instructions from {document.filename}:\n\n{document.content}"
                )
            )
        if self._project_memory:
            reminders.append(
                wrap_in_system_reminder(
                    f"Project memory for Agent {self._agent_id}:\n\n{self._project_memory}"
                )
            )
        return reminders

    def build_system_reminders(
        self,
        skill_registry: SkillRegistry,
        *,
        query: str | None = None,
    ) -> list[str]:
        return [
            *self.build_agent_instruction_reminders(),
            *super().build_system_reminders(skill_registry, query=query),
        ]


@dataclass(slots=True)
class AgentRuntimeManager:
    settings: Settings
    default_query_engine: QueryEngine
    model_client: object
    session_factory: object
    task_registry: TaskRegistry
    queue_manager: QueueManager
    hooks: object = None
    _engines: dict[str, QueryEngine] = field(default_factory=dict)
    _pack_index: dict[str, AgentPackInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.refresh_packs()

    def refresh_packs(self) -> list[AgentPackInfo]:
        packs = discover_agent_packs(self.settings)
        self._engines.clear()
        self._pack_index = {}
        for pack in packs:
            self._pack_index[pack.agent_id] = pack
            for alias in pack.aliases:
                self._pack_index[alias] = pack
        return packs

    def invalidate_agent(self, agent_id: str) -> None:
        """Drop only the selected Agent engine; active requests retain their current object."""
        normalized = (agent_id or "").strip()
        if not normalized or normalized.lower() in _DEFAULT_AGENT_ALIASES:
            return
        self._engines.pop(normalized, None)
        pack = self._pack_index.get(normalized)
        if pack is not None:
            self._engines.pop(pack.agent_id, None)
            for alias in pack.aliases:
                self._engines.pop(alias, None)

    def tool_catalog(self) -> AgentToolCatalog:
        return AgentToolCatalog(
            settings=self.settings,
            main_registry=self.default_query_engine.tool_registry,
            session_factory=self.session_factory,
            model_client=self.model_client,
        )

    def list_agent_options(self) -> list[dict]:
        default_option = {
            "agent_id": "default",
            "display_name": "采购本体审计本体问数",
            "description": "审计本体主链：审计作业 / 采购 ontology 工具与内置 skill。",
            "is_default": True,
            "has_claude_md": False,
            "has_agent_md": False,
            "has_skill": True,
            "has_tools": True,
            "skill_count": len(self.default_query_engine.skill_registry.list()),
            "tool_count": len(self.default_query_engine.tool_registry.definitions()),
            "instruction_files": [],
            "configuration_status": "ready",
            "configuration_error": None,
        }
        pack_options = [
            {
                "agent_id": pack.agent_id,
                "display_name": pack.display_name,
                "description": pack.description,
                "is_default": False,
                "has_claude_md": pack.has_claude_md,
                "has_agent_md": pack.has_agent_md,
                "has_skill": pack.has_skill,
                "has_tools": pack.has_tools,
                **self._pack_capability_counts(pack),
                "instruction_files": _instruction_filenames(Path(pack.agent_dir)),
            }
            for pack in discover_agent_packs(self.settings)
        ]
        managed_options = [
            {
                "agent_id": item["agent_id"],
                "display_name": item["display_name"],
                "description": item["description"],
                "is_default": False,
                "has_claude_md": False,
                "has_agent_md": True,
                "has_skill": bool(item["skill_bindings"]),
                "has_tools": bool(item["tool_names"]),
                "skill_count": len(item["skill_bindings"]),
                "tool_count": len(item["tool_names"]),
                "instruction_files": ["AGENT.md"],
                "configuration_status": "ready",
                "configuration_error": None,
                "source_kind": "managed",
            }
            for item in AgentManagementService(self.settings).list_agents()
            if item["status"] == "published"
            and item["agent_id"] not in self._pack_index
        ]
        return [default_option, *pack_options, *managed_options]

    def describe_agent(self, agent_id: str) -> dict:
        normalized = (agent_id or "").strip()
        is_default = not normalized or normalized.lower() in _DEFAULT_AGENT_ALIASES
        if is_default:
            canonical_id = "default"
            engine = self.default_query_engine
            option = next(item for item in self.list_agent_options() if item["is_default"])
            instruction_document = None
        else:
            pack = self._pack_index.get(normalized)
            if pack is not None:
                canonical_id = pack.agent_id
                engine = self.engine_for_agent(canonical_id)
                option = next(
                    item for item in self.list_agent_options() if item["agent_id"] == canonical_id
                )
                instruction_document = load_agent_instruction_document(Path(pack.agent_dir))
                project_memory = None
            else:
                snapshot = AgentManagementService(self.settings).get_published_snapshot(normalized)
                if snapshot is None:
                    raise KeyError(f"Unknown agent: {normalized}")
                canonical_id = normalized
                engine = self.engine_for_agent(canonical_id)
                option = next(
                    item for item in self.list_agent_options() if item["agent_id"] == canonical_id
                )
                instruction_document = AgentInstructionDocument(
                    filename="AGENT.md",
                    content=str(snapshot.get("agent_markdown") or ""),
                )
                project_memory = str(snapshot.get("project_memory") or "") or None
        if is_default:
            project_memory = None
        prompt_blocks = engine.prompt_builder.build_blocks(
            skill_registry=engine.skill_registry,
            tool_registry=engine.tool_registry,
        )
        rendered_blocks = [block for block in prompt_blocks if block != SYSTEM_PROMPT_DYNAMIC_BOUNDARY]
        reminder_blocks = engine.prompt_builder.build_system_reminders(
            engine.skill_registry,
            query=None,
        )
        return {
            **option,
            "agent_id": canonical_id,
            "instruction_document": _serialize_instruction_document(instruction_document),
            "project_memory": project_memory,
            "skills": [_serialize_skill(skill) for skill in engine.skill_registry.list()],
            "tools": engine.tool_registry.definitions(),
            "system_prompt_blocks": [
                _serialize_prompt_block(index, block)
                for index, block in enumerate(rendered_blocks, start=1)
            ],
            "system_reminder_blocks": [
                _serialize_reminder_block(
                    index,
                    block,
                    instruction_filename=(
                        instruction_document.filename
                        if instruction_document is not None
                        else None
                    ),
                )
                for index, block in enumerate(reminder_blocks, start=1)
            ],
            "effective_system_prompt": "\n\n".join(rendered_blocks),
        }

    def _pack_capability_counts(self, pack: AgentPackInfo) -> dict[str, object]:
        try:
            engine = self.engine_for_agent(pack.agent_id)
        except Exception as exc:
            return {
                "skill_count": 0,
                "tool_count": 0,
                "configuration_status": "invalid",
                "configuration_error": str(exc),
            }
        return {
            "skill_count": len(engine.skill_registry.list()),
            "tool_count": len(engine.tool_registry.definitions()),
            "configuration_status": "ready",
            "configuration_error": None,
        }

    def resolve_query_engine(self, agent_id: str | None) -> QueryEngine:
        normalized = (agent_id or "").strip()
        if not normalized or normalized.lower() in _DEFAULT_AGENT_ALIASES:
            return self.default_query_engine
        return self.engine_for_agent(normalized)

    def engine_for_agent(self, agent_id: str) -> QueryEngine:
        normalized = agent_id.strip()
        if not normalized:
            return self.default_query_engine
        if normalized.lower() in _DEFAULT_AGENT_ALIASES:
            return self.default_query_engine

        cached = self._engines.get(normalized)
        if cached is not None:
            return cached

        pack = self._pack_index.get(normalized)
        if pack is None:
            snapshot = AgentManagementService(self.settings).get_published_snapshot(normalized)
            if snapshot is None:
                raise KeyError(f"Unknown agent: {normalized}")
            selected_tools, _ = self.tool_catalog().resolve(
                list(snapshot.get("tool_names") or [])
            )
            engine = _build_managed_agent_query_engine(
                settings=self.settings,
                model_client=self.model_client,
                session_factory=self.session_factory,
                task_registry=self.task_registry,
                queue_manager=self.queue_manager,
                hooks=self.hooks,
                snapshot=snapshot,
                selected_tools=selected_tools,
            )
            self._engines[normalized] = engine
            return engine

        canonical_id = pack.agent_id
        cached = self._engines.get(canonical_id)
        if cached is not None:
            if normalized != canonical_id:
                self._engines[normalized] = cached
            return cached

        engine = _build_agent_pack_query_engine(
            settings=self.settings,
            model_client=self.model_client,
            session_factory=self.session_factory,
            task_registry=self.task_registry,
            queue_manager=self.queue_manager,
            hooks=self.hooks,
            agent_id=canonical_id,
            agent_dir=Path(pack.agent_dir),
            builtin_skill_names=pack.builtin_skills,
        )
        self._engines[canonical_id] = engine
        if normalized != canonical_id:
            self._engines[normalized] = engine
        return engine


def resolve_agent_id_from_metadata(metadata: object) -> str | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("agent_id")
    if not isinstance(raw, str):
        return None
    normalized = raw.strip()
    if not normalized or normalized.lower() in _DEFAULT_AGENT_ALIASES:
        return None
    return normalized


def _build_agent_pack_query_engine(
    *,
    settings: Settings,
    model_client,
    session_factory,
    task_registry: TaskRegistry,
    queue_manager: QueueManager,
    hooks,
    agent_id: str,
    agent_dir: Path,
    builtin_skill_names: tuple[str, ...] = (),
) -> QueryEngine:
    instruction_document = load_agent_instruction_document(agent_dir)
    private_skills = load_skills_from_agent_pack(
        agent_dir,
        agent_id=agent_id,
        builtin_skill_names=builtin_skill_names,
    )
    linked_skills = load_database_skill_versions(
        settings,
        AgentManagementService(settings).list_agent_bindings(agent_id),
        namespace=agent_id,
    )
    private_names = {skill.name for skill in private_skills}
    loaded_skills = [*private_skills, *(skill for skill in linked_skills if skill.name not in private_names)]
    custom_tools = load_tools_from_agent_pack(
        agent_dir,
        agent_id=agent_id,
        settings=settings,
        session_factory=session_factory,
        model_client=model_client,
    )
    return _build_isolated_agent_query_engine(
        settings=settings,
        model_client=model_client,
        session_factory=session_factory,
        task_registry=task_registry,
        queue_manager=queue_manager,
        hooks=hooks,
        agent_id=agent_id,
        instruction_document=instruction_document,
        project_memory=None,
        loaded_skills=loaded_skills,
        additional_tools=custom_tools,
    )


def _build_managed_agent_query_engine(
    *,
    settings: Settings,
    model_client,
    session_factory,
    task_registry: TaskRegistry,
    queue_manager: QueueManager,
    hooks,
    snapshot: dict,
    selected_tools: list,
) -> QueryEngine:
    agent_id = str(snapshot.get("agent_id") or "").strip()
    loaded_skills = load_database_skill_versions(
        settings,
        list(snapshot.get("skill_bindings") or []),
        namespace=agent_id,
    )
    return _build_isolated_agent_query_engine(
        settings=settings,
        model_client=model_client,
        session_factory=session_factory,
        task_registry=task_registry,
        queue_manager=queue_manager,
        hooks=hooks,
        agent_id=agent_id,
        instruction_document=AgentInstructionDocument(
            filename="AGENT.md",
            content=str(snapshot.get("agent_markdown") or ""),
        ),
        project_memory=str(snapshot.get("project_memory") or "") or None,
        loaded_skills=loaded_skills,
        additional_tools=selected_tools,
    )


def _build_isolated_agent_query_engine(
    *,
    settings: Settings,
    model_client,
    session_factory,
    task_registry: TaskRegistry,
    queue_manager: QueueManager,
    hooks,
    agent_id: str,
    instruction_document: AgentInstructionDocument | None,
    project_memory: str | None,
    loaded_skills: list,
    additional_tools: list,
) -> QueryEngine:
    skill_registry = SkillRegistry(loaded_skills)
    prompt_builder = AgentPackPromptBuilder(
        instruction_document=instruction_document,
        agent_id=agent_id,
        project_memory=project_memory,
    )
    bootstrap_loop = RuntimeLoop(
        model_client=model_client,
        tool_registry=ToolRegistry(),
        hooks=hooks,
        system_prompt=None,
    )
    subagent_runner = SubagentRunner(
        runtime_loop=bootstrap_loop,
        base_system_reminders=prompt_builder.build_agent_instruction_reminders(),
    )
    task_runner = InProcessTaskRunner(
        settings=settings,
        task_registry=task_registry,
        queue_manager=queue_manager,
        subagent_runner=subagent_runner,
    )

    runtime_tools = [
        *build_interaction_tools(),
        AgentTool(
            task_registry=task_registry,
            skill_registry=skill_registry,
            task_runner=task_runner,
        ),
        SendMessageTool(
            queue_manager=queue_manager,
            task_registry=task_registry,
            task_runner=task_runner,
        ),
        TaskStopTool(task_registry),
        SkillTool(
            skill_registry=skill_registry,
            task_registry=task_registry,
            task_runner=task_runner,
        ),
        LoadSkillAliasTool(
            skill_registry=skill_registry,
            task_registry=task_registry,
            task_runner=task_runner,
        ),
        *[
            NamedSkillTool(
                skill=skill,
                skill_registry=skill_registry,
                task_registry=task_registry,
                task_runner=task_runner,
            )
            for skill in skill_registry.list_model_invocable()
        ],
        ReadToolResultTool(session_factory),
        ReadSessionMessagesTool(session_factory),
        ReadSkillFileTool(skill_registry),
        *build_skill_script_tools(skill_registry),
    ]
    runtime_tool_names = {tool.name for tool in runtime_tools}
    explicit_tools = [
        tool for tool in additional_tools if tool.name not in runtime_tool_names
    ]
    tool_registry = ToolRegistry([*runtime_tools, *explicit_tools])
    _validate_allowed_tools(skill_registry, tool_registry, agent_id=agent_id)
    system_prompt = prompt_builder.build(
        skill_registry=skill_registry,
        tool_registry=tool_registry,
    )
    runtime_loop = RuntimeLoop(
        model_client=model_client,
        tool_registry=tool_registry,
        hooks=hooks,
        system_prompt=system_prompt,
    )
    # Extension 子任务复用主链控制面和持久化，但执行时绑定当前
    # Extension 的 Prompt/Skill/Tool，避免回落到默认 ontology 工具池。
    subagent_runner.runtime_loop = runtime_loop
    runtime_engine = RuntimeEngine(
        runtime_loop=runtime_loop,
        queue_manager=queue_manager,
        task_registry=task_registry,
        compaction_engine=CompactionEngine(
            max_message_count=settings.compaction_max_message_count,
            keep_recent=settings.compaction_keep_recent,
        ),
        memory_extractor=None,
        tool_result_keep_recent=(
            settings.tool_result_keep_recent
            if settings.tool_result_keep_recent > 0
            else None
        ),
        tool_result_max_model_chars=settings.tool_result_max_model_chars,
        tool_result_preview_chars=settings.tool_result_preview_chars,
    )
    return QueryEngine(
        settings=settings,
        runtime_engine=runtime_engine,
        prompt_builder=prompt_builder,
        skill_registry=skill_registry,
        tool_registry=tool_registry,
        session_memory_updater=None,
        session_compaction_updater=None,
        relevant_recall_service=None,
    )


def _validate_allowed_tools(
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
    *,
    agent_id: str,
) -> None:
    available = {tool.name for tool in tool_registry.list()}
    for skill in skill_registry.list():
        missing = sorted(
            name
            for name in skill.allowed_tools
            if name != "*" and name not in available
        )
        if missing:
            raise ValueError(
                f"extension {agent_id!r} skill {skill.name!r} declares unknown "
                f"allowed-tools: {missing}"
            )


def _serialize_skill(skill) -> dict:
    return {
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description,
        "content": skill.content,
        "allowed_tools": list(skill.allowed_tools),
        "when_to_use": skill.when_to_use,
        "when_not_to_use": skill.when_not_to_use,
        "execution_context": skill.execution_context,
        "agent": skill.agent,
        "user_invocable": skill.user_invocable,
        "disable_model_invocation": skill.disable_model_invocation,
        "files": _serialize_skill_files(skill.skill_root),
    }


_MAX_SKILL_FILE_BYTES = 256 * 1024
_MAX_SKILL_FILES = 200
_AGENT_LEVEL_FILENAMES = {
    "AGENT.md",
    "CLAUDE.md",
    "agent.json",
    "extension.py",
    "tools.py",
}


def _serialize_skill_files(skill_root: str | None) -> list[dict]:
    """Expose a bounded relative view of a Skill package without leaking host paths."""
    if not skill_root:
        return []

    root = Path(skill_root).resolve()
    if not root.is_dir():
        return []

    candidates = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.parts
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]
    candidates.sort(
        key=lambda path: (
            path.relative_to(root).as_posix() != "SKILL.md",
            path.relative_to(root).as_posix().lower(),
        )
    )

    files: list[dict] = []
    for path in candidates[:_MAX_SKILL_FILES]:
        relative_path = path.relative_to(root).as_posix()
        if "/" not in relative_path and relative_path in _AGENT_LEVEL_FILENAMES:
            continue
        try:
            size_bytes = path.stat().st_size
            raw = path.read_bytes()[:_MAX_SKILL_FILE_BYTES]
        except OSError:
            # Skill 资产可能在加载后被替换；配置页跳过瞬时不可读文件即可。
            continue
        try:
            content = raw.decode("utf-8")
            kind = "text"
        except UnicodeDecodeError:
            content = None
            kind = "binary"
        files.append(
            {
                "path": relative_path,
                "name": path.name,
                "kind": kind,
                "size_bytes": size_bytes,
                "content": content,
                "truncated": size_bytes > _MAX_SKILL_FILE_BYTES,
            }
        )
    return files


def _serialize_prompt_block(index: int, content: str) -> dict:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    return {
        "index": index,
        "title": first_line.lstrip("# ") or f"Section {index}",
        "source": "runtime",
        "content": content,
    }


def _serialize_instruction_document(
    document: AgentInstructionDocument | None,
) -> dict | None:
    if document is None:
        return None
    return {"filename": document.filename, "content": document.content}


def _instruction_filenames(agent_dir: Path) -> list[str]:
    document = load_agent_instruction_document(agent_dir)
    return [document.filename] if document is not None else []


def _serialize_reminder_block(
    index: int,
    content: str,
    *,
    instruction_filename: str | None,
) -> dict:
    is_agent_instruction = bool(
        instruction_filename
        and f"Agent instructions from {instruction_filename}:" in content
    )
    return {
        "index": index,
        "title": "Agent Instructions" if is_agent_instruction else "Available Skills",
        "source": instruction_filename if is_agent_instruction else "runtime",
        "content": content,
    }
