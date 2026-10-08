"""Skill-driven specialists using the supplied core loop and one shared tool pool."""
from copy import deepcopy
import hashlib
import tomllib

from audit_agent_runtime.querying.query_loop import QueryLoopParams, queryLoop
from audit_agent_runtime.tools.core.base import BaseTool
from .config import ROOT
from trading_agent.common import DomainError


class CapabilityCatalog:
    def __init__(self, skills, tools, path=None):
        self.skills, self.tools = skills, tools
        path = path or ROOT / "config/capabilities.toml"
        self.agents = tomllib.loads(path.read_text(encoding="utf-8")).get("agents", [])
        identifiers = set()
        for agent in self.agents:
            if agent["id"] in identifiers:
                raise ValueError("协作伙伴标识重复")
            identifiers.add(agent["id"])
            self.skills.get(agent["skill"])
            for name in agent["tools"]:
                self.tools.get(name)

    def agent(self, identifier):
        row = next((a for a in self.agents if a["id"] == identifier), None)
        if row is None:
            raise DomainError("NOT_FOUND", "没有这个协作伙伴")
        return row

    def public(self):
        return {"agents": [{**a, "skill_version": hashlib.sha256(self.skills.get(a["skill"]).content.encode()).hexdigest()[:12]}
                            for a in self.agents],
                "skills": [{"id": s.name, "description": s.description} for s in self.skills.list()],
                "tools": [{"id": t.name, "description": t.description} for t in self.tools.list()],
                "execution": "按任务邀请专职伙伴分别检查，由主伙伴汇总；共享技能、工具与个人记忆。"}


class CollaborateTool(BaseTool):
    name = "collaborate"
    description = "邀请 1 至 3 个专职智能体，使用各自技能和共享只读工具实际检查同一个任务。返回独立结果与不足，由主伙伴汇总。普通记录无需协作。"
    input_schema = {"type": "object", "properties": {
        "agents": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
        "task": {"type": "string"}}, "required": ["agents", "task"], "additionalProperties": False}

    def __init__(self, catalog, client, store):
        self.catalog, self.client, self.store = catalog, client, store

    def execute(self, arguments, context):
        ids, task = arguments.get("agents"), arguments.get("task")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not isinstance(task, str) or not task.strip() or len(task) > 8000:
            return {"is_error": True, "message": "请选择 1 至 3 个不同伙伴，并说明具体任务。"}
        try:
            agents = [self.catalog.agent(i) for i in ids]
        except DomainError as e:
            return {"is_error": True, "message": e.message}
        parent = context.metadata or {}
        if parent.get("specialist"):
            return {"is_error": True, "message": "专职伙伴不重复委派任务。"}
        task_cache = parent.setdefault("collaboration_cache", {})
        cache_key = "|".join(sorted(ids)) + ":" + task
        if cache_key in task_cache:
            return task_cache[cache_key]
        if sum(len(v["members"]) for v in task_cache.values()) + len(ids) > 4:
            return {"is_error": True, "message": "本轮已完成足够的独立检查，请先汇总现有结果。"}
        members = []
        for agent in agents:
            skill = self.catalog.skills.get(agent["skill"])
            metadata = {k: deepcopy(v) for k, v in parent.items() if k != "collaboration_cache"}
            metadata.update(specialist=agent["id"], cloud_remember=False)
            prompt = ("你是" + agent["name"] + "。只完成本次受委托的检查，使用中文。\n"
                      "以下技能已由服务端加载，按流程使用共享工具读取真实资料。不得写入资料、修改正文、代确认或委派。\n"
                      "按任务选择技能中必要的步骤，不通读无关资料、不重复检索已经返回的原文。不要扮演其他伙伴，只报告自己的检查。\n"
                      "工具 schema 是实际契约；工具报错后不反复猜参数。classic_search 已返回公开条目的原文、来源与边界；K/S 编号不是个人数据库引用，不能用 evidence_check 核验。\n"
                      "先执行有用的核对，再用约300字报告：结论、依据、分歧或未知、下一步。缺少资料如实保留。\n" + skill.content)
            output, used, errors, stop_reason = [], [], [], None
            try:
                assignment = (f"主伙伴已经邀请 {', '.join(a['name'] for a in agents)}。你是其中的{agent['name']}，技能名称 {agent['skill']}。"
                    "其他伙伴会独立完成自己的检查，无需你再邀请或代办。只报告你这部分检查，交回主伙伴汇总。\n"
                    "以下原委托中涉及邀请多人、汇总多人结果的部分，已由主伙伴负责：\n" + task)
                for event in queryLoop(QueryLoopParams(query=assignment, history=[], session_id=context.session_id,
                        active_agent_id=agent["id"], model_client=self.client, tool_registry=self.catalog.tools,
                        system_prompt=prompt, metadata=metadata, max_turns=5, max_tool_calls_per_turn=8,
                        model_visible_tool_names=set(agent["tools"]),
                        can_use_tool=lambda name, *_args, allowed=set(agent["tools"]): name in allowed)):
                    if event.type == "assistant":
                        output.append(event.payload.get("text", ""))
                    elif event.type == "tool_result":
                        used.append(event.payload.get("tool_name"))
                        result = event.payload.get("result") or {}
                        if result.get("is_error"):
                            errors.append(event.payload.get("tool_name"))
                    elif event.type == "done":
                        stop_reason = event.payload.get("stop_reason")
                status = "complete" if stop_reason in {"end_turn", "stop"} and output and not errors else "partial"
            except Exception:
                status = "failed"
                output = ["这次检查未完成，请保留缺口，不据此形成结论。"]
            members.append({"id": agent["id"], "name": agent["name"], "skill": agent["skill"],
                "skill_version": hashlib.sha256(skill.content.encode()).hexdigest()[:12], "status": status,
                "text": "".join(output)[:10000], "tools": list(dict.fromkeys(used)), "failed_tools": errors})
        result = {"members": members, "session_id": context.session_id,
                  "target_id": (parent.get("cloud_context") or {}).get("id"),
                  "message_id": parent.get("cloud_message_id"), "task": task,
                  "note": "协作结果是检查意见，不自动成为正式纪律。主伙伴需综合分歧再答复。"}
        if parent.get("cloud_remember", True):
            row = self.store.save_artifact("collaboration", result, [], "collaboration:" + str(parent.get("cloud_message_id")) + ":" + hashlib.sha256(cache_key.encode()).hexdigest()[:16])
            result["id"] = row["id"]
        task_cache[cache_key] = result
        return result
