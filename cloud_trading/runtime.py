from . import core  # noqa: F401
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from audit_agent_runtime.agent_packs.loader import load_tools_from_agent_pack
from audit_agent_runtime.compaction.engine import CompactionEngine
from audit_agent_runtime.config.settings import Settings
from audit_agent_runtime.persistence.db import build_engine, build_session_factory
from audit_agent_runtime.persistence.models import Base
from audit_agent_runtime.prompts.builder import PromptBuilder
from audit_agent_runtime.querying.query_engine import QueryEngine
from audit_agent_runtime.runtime.engine import RuntimeEngine
from audit_agent_runtime.runtime.loop import RuntimeLoop
from audit_agent_runtime.skills.core.loader import SkillLoader
from audit_agent_runtime.skills.core.registry import SkillRegistry
from audit_agent_runtime.skills.core.skill_tool import SkillTool, LoadSkillAliasTool
from audit_agent_runtime.tools.core.registry import ToolRegistry
from cloud_trading.config import ROOT
from cloud_trading.models import ModelRouter, SafeModel
from cloud_trading.capabilities import CapabilityCatalog, CollaborateTool
from trading_agent.store import Store

PACK = Path(__file__).parent / "extensions/trading_partner"
CORE_TABLES = {"sessions", "messages", "session_runtime_state", "query_tasks", "queued_commands",
               "session_events", "session_memories", "session_compactions"}


class TradingPrompt(PromptBuilder):
    def __init__(self, capabilities):
        self.capabilities = capabilities

    def build_blocks(self, *, skill_registry, tool_registry, **kwargs):
        instructions = (PACK / "AGENT.md").read_text(encoding="utf-8")
        skills = "\n".join(f"- {s.name}: {s.description}" for s in skill_registry.list())
        agents = "\n".join(f"- {a['id']}: {a['name']}，{a['description']}" for a in self.capabilities.agents)
        return [instructions, "可用技能，使用Skill按名称加载：\n" + skills,
                "可实际邀请的专职伙伴，使用 collaborate：\n" + agents]

    def build_system_reminders(self, skill_registry, *, query=None):
        return []

    def build_coordinator_system_prompt(self):
        return ""


def assemble(config, model_client=None):
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    # 不调用原应用的全平台建表和装配，也不读取其.env配置。
    settings = Settings(_env_file=None, database_url="sqlite+pysqlite:///" + str(config.runtime_dir / "runtime.sqlite"),
                        model_base_url="", model_api_key="", model_name=config.model_name or "unconfigured")
    engine = build_engine(settings)
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name in CORE_TABLES])
    client = ModelRouter(config, model_client)
    skill_list = SkillLoader().load_from_skills_dir(ROOT / "skills")
    # 保留原技能方法，覆盖宿主执行方式；云端不调用shell或MCP。
    adaptation = "\n云端执行补充：业务工具直接调用。参考文件用read_skill_reference。无需shell、MCP或本机Codex。原角色约定用layer_registry读取。"
    skills = SkillRegistry([replace(s, content=s.content + adaptation) for s in skill_list])
    tools = load_tools_from_agent_pack(PACK, agent_id="trading_partner",
        settings=SimpleNamespace(trading_data_dir=config.data_dir),
        session_factory=build_session_factory(settings), model_client=client)
    tools.extend([SkillTool(skills), LoadSkillAliasTool(skills)])
    registry = ToolRegistry(tools)
    capabilities = CapabilityCatalog(skills, registry)
    registry.register(CollaborateTool(capabilities, client, Store(config.data_dir)))
    loop = RuntimeLoop(client, registry, max_tool_calls_per_turn=32)
    runtime = RuntimeEngine(runtime_loop=loop, compaction_engine=CompactionEngine(60, 24))
    query = QueryEngine(settings, runtime, TradingPrompt(capabilities), skills, registry)
    return SimpleNamespace(query=query, settings=settings, session_factory=build_session_factory(settings),
                           tools=registry, capabilities=capabilities, model_client=client, engine=engine,
                           skill_names=[skill.name for skill in skills.list()],
                           model_ready=client.ready)
