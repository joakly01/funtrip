"""Task handoff for the host AI: semantic classification remains with Codex."""
from pathlib import Path
from .common import DomainError, ROOT
from .catalog import ClassicCatalog
from .metadata import LAYERS

INTENTS = ("record", "recall", "learn", "research", "plan", "review", "evolve", "correct", "development")
ROUTES = {
    "record": ("qi",), "recall": ("qi",), "learn": ("dao", "qi"),
    "research": ("shu", "qi"), "plan": ("fa", "shu", "qi"),
    "review": ("dao", "fa", "shu", "qi"), "evolve": LAYERS,
    "correct": ("qi",), "development": ("qi",),
}
SKILLS = {"learn": "trading-classic-learning", "plan": "trading-plan-review",
          "review": "trading-experience-review", "evolve": "trading-environment-review"}
NAMES = {"dao": "道", "fa": "法", "shu": "术", "qi": "器"}

class Coordinator:
    def __init__(self, store, evidence_check, root=ROOT):
        self.store, self.evidence_check, self.root = store, evidence_check, Path(root)
        self.catalog = ClassicCatalog(root)

    def registry(self):
        roles = []
        for layer in LAYERS:
            path = self.root / "roles" / (layer + ".md")
            if not path.is_file():
                raise DomainError("ROLE_MISSING", "层级角色资料缺失：" + layer)
            roles.append({"id": layer, "name": NAMES[layer], "instruction": path.read_text(encoding="utf-8"), "path": str(path)})
        return {"layers": roles, "coordinator": str(self.root / "roles/coordinator.md"),
                "execution": "由当前Codex执行分层判断；本地程序组织交接，不伪装为独立模型或常驻服务"}

    def process(self, message_id, text, intents, key, items=None, horizon="unknown", security_ids=None,
                memory_query="", classic_query="", persist=True):
        if not intents or len(set(intents)) != len(intents) or any(x not in INTENTS for x in intents):
            raise DomainError("INVALID_INTENTS", "使用语义判断后的合法意图，不按关键词猜测")
        if horizon not in {"short", "medium_long", "unknown"}:
            raise DomainError("INVALID_HORIZON", "周期无效")
        if "development" in intents and len(intents) != 1:
            raise DomainError("MIXED_SCOPE", "开发讨论与业务输入由主智能体分开处理")
        if not text.strip():
            raise DomainError("EMPTY_INPUT", "原文不能为空")
        persist = persist and "development" not in intents
        items, security_ids = items or [], security_ids or []
        request = {"message_id": message_id, "text": text, "intents": intents, "key": key,
                   "items": items, "horizon": horizon, "security_ids": security_ids,
                   "memory_query": memory_query, "classic_query": classic_query, "persist": persist}
        if persist:
            cached = self.store.cached_run(key, request)
            if cached:
                if cached["status"] == "needs_recheck":
                    raise DomainError("RUN_STALE", "旧任务依赖已更正，请使用新任务键重新调取资料")
                return {"run_id": cached["id"], **cached["body"]}
        role_map = {r["id"]: r for r in self.registry()["layers"]}
        wanted = {x for intent in intents for x in ROUTES[intent]}
        # Structured record metadata may route a simple note to another relevant role.
        wanted.update(x for item in items for x in item.get("layers", []) if x in LAYERS)
        source_id, records, issues = None, [], []
        if persist:
            capture = self.store.capture(message_id, text)
            source_id = capture["source_id"]
            if items:
                try:
                    records = self.store.commit(source_id, items, "partner:" + key)["records"]
                except DomainError as error:
                    issues.append({"step": "organize", "code": error.code, "message": error.message})
        memory = [] if "development" in intents else self.store.snapshot(query=memory_query, limit=None)["records"]
        if security_ids:
            memory = [r for r in memory if any(s in r["body"].get("security_ids", []) for s in security_ids)
                      or r["kind"] in {"profile", "strategy", "proposal"}]
        memory = [r for r in memory if r["kind"] not in {"raw", "market_snapshot", "agent_run", "agent_result"}][:8]
        strategy = self.store.active_snapshot() if "development" not in intents else {"id": None, "status": "not_applicable"}
        classics = self.catalog.search(classic_query, 4) if classic_query else {"entries": [], "scope": "本次未请求经典检索"}
        body = {"source_id": source_id, "saved": persist, "record_ids": [r["id"] for r in records],
                "phase": "partial" if issues else ("organized" if records else "prepared"),
                "issues": issues, "intents": intents, "horizon": horizon, "security_ids": security_ids,
                "memory": memory, "strategy": strategy, "classics": classics,
                "tasks": [role_map[x] for x in LAYERS if x in wanted],
                "skills": list(dict.fromkeys(SKILLS[i] for i in intents if i in SKILLS)),
                "request": {"text": text, "horizon": horizon, "security_ids": security_ids},
                "execution": "角色任务已准备；判断、取数和复核仍需由当前智能体实际执行", "persist": persist}
        if not persist:
            return {"run_id": None, **body}
        refs = list(dict.fromkeys([source_id, *body["record_ids"], *[r["id"] for r in memory],
                                 *([strategy["id"]] if strategy.get("id") else [])]))
        run = self.store.save_run(key, request, body, refs)
        return {"run_id": run["id"], **run["body"]}

    def finalize(self, run_id, results, text, key, claims=None):
        run = self.store.read(run_id)
        if run["kind"] != "agent_run":
            raise DomainError("WRONG_OBJECT", "需要真实分层任务编号")
        if run["status"] == "needs_recheck":
            raise DomainError("RUN_STALE", "任务依赖已更正，请重新准备判断")
        expected = {t["id"] for t in run["body"]["tasks"]}
        seen, refs = set(), [run_id]
        for result in results:
            if set(result) - {"layer", "status", "summary", "evidence_refs", "unknowns", "counter_evidence", "scope"}:
                raise DomainError("INVALID_LAYER_RESULT", "角色结果含未约定字段")
            layer = result.get("layer")
            if layer not in expected or layer in seen:
                raise DomainError("INVALID_LAYER_RESULT", "角色结果不能重复或属于未安排层级")
            if result.get("status") not in {"completed", "partial", "blocked"} or not result.get("summary"):
                raise DomainError("INVALID_LAYER_RESULT", "结果需说明完成状态及具体结论")
            evidence = result.get("evidence_refs", [])
            if not isinstance(evidence, list) or any(not isinstance(r, str) for r in evidence):
                raise DomainError("INVALID_LAYER_RESULT", "证据引用需为真实对象编号数组")
            seen.add(layer); refs.extend(evidence)
        verification = self.evidence_check(claims or [])
        missing = sorted(expected - seen)
        partial = bool(missing or run["body"]["issues"] or any(r["status"] != "completed" for r in results)
                       or any(c["state"] == "unresolved" for c in verification["claims"]))
        body = {"text": text, "layer_results": results, "missing_layers": missing, "verification": verification,
                "phase": "partial" if partial else "completed", "rule_changes": "本流程不激活纪律"}
        refs.extend(r for c in (claims or []) for r in c.get("evidence_refs", []))
        return self.store.save_artifact("agent_result", body, list(dict.fromkeys(refs)), key)

    def learning_link(self, source_id, classic_id, excerpt, understanding, key, adoption="pending", evidence="unverified", refs=None):
        classic = self.catalog.get(classic_id)
        item = {"item_key": key, "kind": "learning", "nature": "user_report", "text": understanding,
                "excerpt": excerpt, "layers": classic["layers"], "tags": [classic_id, *classic["tags"]],
                "states": {"source": "user_report", "adoption": adoption, "evidence": evidence, "rule": "not_applicable"},
                "fields": {"classic_id": classic_id, "source_ref": classic["source_id"],
                           "classic_source_status": classic["source_status"], "classic_path": "knowledge/classics/cards.md"},
                "refs": refs or []}
        return self.store.commit(source_id, [item], "learning:" + key)
