from copy import deepcopy
from pathlib import Path

from audit_agent_runtime.tools.core.base import BaseTool
from trading_agent.service import Service
from cloud_trading.config import ROOT
from cloud_trading.growth import GrowthWorkspace
from trading_agent.common import DomainError

EXCLUDED = {"inbox_capture", "records_commit", "source_register", "knowledge_backup", "knowledge_export", "system_render"}
TEMPORARY_TOOLS = {"partner_process", "market_quote", "market_bars"}


class TradingTool(BaseTool):
    def __init__(self, service, name):
        self.service = service
        spec = service.specs[name]
        self.name = name
        self.description = spec["description"].replace("Codex", "云端智能体")
        if name == "evidence_check":
            self.description += "。claims 每项用 text、type 和 evidence_refs；经典 K01/S01 是公开目录编号，不是个人数据库对象 ID，不要交给本工具核验。"
        self.input_schema = deepcopy(spec["inputSchema"])

    def execute(self, arguments, context):
        arguments = dict(arguments)
        metadata = context.metadata or {}
        # 归集必须绑定真实网页消息；模型不能代造用户原文或更换重试身份。
        if self.name == "partner_process":
            arguments.update(text=metadata["cloud_input"], message_id=metadata["cloud_message_id"],
                             key="cloud:" + metadata["cloud_message_id"])
            if arguments.get("persist") is False:
                metadata["cloud_remember"] = False
        remember = metadata.get("cloud_remember", True)
        if not remember:
            if self.name in TEMPORARY_TOOLS:
                arguments["persist"] = False
            elif self.service.specs[self.name]["annotations"]["readOnlyHint"] is False:
                return {"is_error": True, "error": "memory_disabled", "message": "本轮不写入交易资料。"}
        result = self.service.call(self.name, arguments)
        if result["status"] == "error":
            return {**result, "is_error": True}
        current = metadata.get("cloud_context") or {}
        if self.name == "partner_process" and remember and current.get("id") and result["data"].get("source_id"):
            GrowthWorkspace(self.service.store).attach_conversation_source(current["id"], result["data"]["source_id"])
        if self.name == "chart_render":
            chart = result["data"]
            chart["url"] = "/api/charts/" + Path(chart.pop("path")).name
        # 只更正宿主执行方式说明，用户原话与历史正文不改写。
        if self.name == "agent_status":
            result["data"].update(version="0.6.0", tools=[name for name in self.service.specs if name not in EXCLUDED],
                                  coordination="主伙伴按需调用 collaborate，专职伙伴通过原核心循环独立执行技能与共享工具，再汇总意见",
                                  language_understanding="由已配置的真实模型与交易技能完成")
            result["data"]["not_implemented"] = [x for x in result["data"]["not_implemented"] if x != "独立聊天模型"]
        if self.name == "layer_registry":
            result["data"]["execution"] = "由云端agent-runtime执行分层判断，交易程序提供工具与任务回执。"
        return result


class SkillReferenceTool(BaseTool):
    name = "read_skill_reference"
    description = "读取交易技能已有的 Markdown 参考；path 相对于技能目录。技能中的 ../../knowledge/classics/cards.md 与 sources.md 可直接读取；也可用 knowledge/classics/cards.md。classic_search 已返回完整条目时无需重复读取。"
    input_schema = {"type": "object", "properties": {"skill": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["skill", "path"], "additionalProperties": False}

    def __init__(self):
        self.input_schema = deepcopy(self.input_schema)
        self.input_schema["properties"]["skill"]["enum"] = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file())

    def execute(self, arguments, context):
        root = (ROOT / "skills" / arguments["skill"]).resolve()
        reference = arguments["path"]
        path = (ROOT / reference if reference.startswith(("knowledge/", "roles/")) else root / reference).resolve()
        public_roots = [(ROOT / folder).resolve() for folder in ("knowledge", "roles")]
        if root.parent != (ROOT / "skills").resolve() or not root.is_dir() or path.suffix != ".md" or not (path.is_relative_to(root) or any(path.is_relative_to(p) for p in public_roots)):
            return {"is_error": True, "error": "invalid_reference", "message": "skill 使用技能的完整名称，不是伙伴 ID。只能读取技能或公开知识目录中的 Markdown。",
                    "skills": self.input_schema["properties"]["skill"]["enum"]}
        if not path.is_file():
            return {"is_error": True, "error": "not_found"}
        return {"content": path.read_text(encoding="utf-8")[:24000]}


class GrowthSuggestionTool(BaseTool):
    name = "growth_suggest"
    description = "为当前页面的专题、知识、方法或实践提出正文修改草稿。只创建待审阅建议，不覆盖正文、不生效纪律。"
    input_schema = {"type": "object", "properties": {
        "target_id": {"type": "string"}, "expected_version": {"type": "integer"},
        "patch": {"type": "object", "additionalProperties": {"type": "string"}},
        "reason": {"type": "string"}}, "required": ["target_id", "expected_version", "patch", "reason"], "additionalProperties": False}

    def __init__(self, store):
        self.workspace = GrowthWorkspace(store)

    def execute(self, arguments, context):
        metadata = context.metadata or {}
        current = metadata.get("cloud_context") or {}
        if not metadata.get("cloud_remember", True):
            return {"is_error": True, "error": "memory_disabled", "message": "本轮不保存整理建议。"}
        if arguments.get("target_id") != current.get("id") or not current.get("id"):
            return {"is_error": True, "message": "只能为当前打开的对象提出建议。"}
        try:
            row = self.workspace.suggest(arguments["target_id"], arguments["expected_version"], arguments["patch"],
                                         arguments["reason"], "cloud:" + metadata["cloud_message_id"])
            return {"saved": True, "suggestion_id": row["id"], "state": "等待用户在整理建议中审阅，正文未改变"}
        except DomainError as error:
            return {"is_error": True, "message": error.message}


def build_agent_tools(*, settings, session_factory, model_client, agent_id, agent_dir):
    service = Service(directory=settings.trading_data_dir)
    return [TradingTool(service, name) for name in service.specs if name not in EXCLUDED] + [SkillReferenceTool(), GrowthSuggestionTool(service.store)]
