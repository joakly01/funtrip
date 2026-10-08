"""Persistent learning objects, practice snapshots and owner-reviewed changes.

Uses the existing versioned business store. Engine tools can only propose edits;
the authenticated owner routes perform edits, freeze practices and adopt rules.
"""
import json
from copy import deepcopy

from trading_agent.common import DomainError, canonical, digest, now


FIELDS = {
    "topic": {"title", "question", "summary", "open_questions", "next_step", "area", "stage", "category"},
    "insight": {"title", "topic_id", "source_text", "understanding", "boundary", "example", "next_step", "stage"},
    "method": {"title", "topic_id", "scope", "conditions", "steps", "invalidation", "risk", "unknowns", "next_step", "stage"},
    "practice": {"title", "topic_id", "method_id", "judgment", "conditions", "invalidation", "plan",
                 "actual", "result", "review", "lesson", "evidence", "next_step"},
    "handbook": {"title", "goals", "boundaries", "scope", "principles", "review_rhythm"},
}
STAGES = {"topic": {"active", "paused", "archived"}, "insight": {"draft", "understood", "revisit"},
          "method": {"draft", "trial", "adopted", "paused"}}
DEFAULTS = {"topic": {"stage": "active", "area": "trading"}, "insight": {"stage": "draft"},
            "method": {"stage": "draft"}, "practice": {"phase": "draft", "evidence": "uncertain"}, "handbook": {}}
PRIVATE_FIELDS = {"sources", "phase", "frozen_at", "method_snapshot", "strategy_snapshot", "reviewed_at", "origin", "curation_key"}
LABELS = {"title": "标题", "question": "正在解决的问题", "summary": "我目前的认识", "open_questions": "还没弄清",
          "next_step": "下一步", "source_text": "资料摘录", "understanding": "我的理解", "boundary": "适用边界",
          "example": "我的例子", "scope": "参与范围与周期", "conditions": "观察条件", "steps": "执行步骤",
          "invalidation": "失效条件", "risk": "风险处理", "unknowns": "仍需验证", "judgment": "事前判断",
          "plan": "计划怎样做", "actual": "实际做了什么", "result": "后来发生了什么", "review": "我的复盘",
          "lesson": "下次如何改进", "goals": "目标与方向", "boundaries": "生活与资金边界",
          "principles": "我选择的原则", "review_rhythm": "怎样回顾与修订"}


class GrowthWorkspace:
    def __init__(self, store):
        self.store = store

    def rows(self):
        return self.store.snapshot(limit=None)["records"]

    def get(self, identifier):
        row = next((r for r in self.rows() if r["id"] == identifier), None)
        if row is None:
            raise DomainError("NOT_FOUND", "内容不存在")
        return row

    def detail(self, identifier):
        row = self.get(identifier)
        if row["kind"] not in FIELDS:
            raise DomainError("INVALID_RECORD", "请选择专题、知识、方法或实践")
        rows = self.rows()
        related = [r for r in rows if r["kind"] in FIELDS and r["id"] != identifier and (
            r["body"].get("topic_id") == identifier or r["body"].get("method_id") == identifier or
            any(s["id"] == identifier for s in r["body"].get("sources", [])))]
        suggestions = [r for r in rows if r["kind"] == "growth_suggestion"
                       and r["body"].get("target_id") == identifier and r["body"].get("decision") == "pending"]
        row = self.store.read(identifier)
        current = {r["id"]: r for r in rows}
        linked = [r["body"]["source"] for r in rows if r["kind"] == "growth_source"
                  and r["body"].get("target_id") == identifier]
        unique = {s["id"]: s for s in [*linked, *row["body"].get("sources", [])]}
        managed = {s["id"] for s in row["body"].get("sources", [])}
        sources = [{**s, "managed": s["id"] in managed,
                    "changed": current.get(s["id"], {}).get("version") != s["version"]}
                   for s in unique.values()]
        return {**row, "sources": sources, "related": related, "suggestions": suggestions,
                "progress": self.progress(row), "session_id": "growth_" + identifier}

    @staticmethod
    def progress(row):
        kind, body = row["kind"], row["body"]
        required, action, label, hint = [], "edit", "继续完善", "每次只想清一点，随时可以保存。"
        if kind == "method":
            required = ["scope", "conditions", "steps", "invalidation", "risk"]
            hint = "写清适用范围、行动条件和退出路径，再决定是否开始试用。"
            if body.get("stage") == "paused":
                label, hint = "重新评估方法", "这个方法已暂停。恢复前先检查条件是否仍然适用。"
            elif all(body.get(k, "").strip() for k in required):
                action, label = "practice", "准备一次观察"
                hint = "条件已写清。用事前记录和真实回看继续检验，采用状态由你选择。"
        elif kind == "practice":
            if body.get("phase") == "draft":
                required = ["judgment", "conditions", "invalidation", "plan"]
                label, hint = "补齐事前记录", "先留下当时的判断，再锁定。只观察、不交易也可以。"
                if all(body.get(k, "").strip() for k in required):
                    action, label = "freeze", "锁定事前记录"
            elif body.get("phase") == "observing":
                required = ["actual", "result", "review"]
                label, hint = "补充实际情况与复盘", "分别回看当时的判断、实际执行和后来结果。"
                if all(body.get(k, "").strip() for k in required):
                    action, label = "review", "完成这次复盘"
            else:
                action, label, hint = "insight", "留下这次的认识", "把可复用的经验写回知识笔记，再检查原方法。"
        elif kind == "insight":
            required = ["understanding", "boundary"]
            label, hint = "写下自己的理解", "先说清自己怎样理解、什么时候适用，再考虑下次怎样用。"
            if all(body.get(k, "").strip() for k in required):
                action, label = "method", "形成一个候选方法"
        elif kind == "topic":
            action, label, hint = "insight", "留下第一张知识笔记", "围绕这个问题，分开记录资料原意和自己的理解。"
        elif kind == "handbook":
            required = ["goals", "boundaries", "scope", "principles", "review_rhythm"]
            label, hint = "继续梳理交易理念", "从目标和生活边界开始，逐步明确做法与回顾节奏。"
        return {"action": action, "label": label, "hint": hint,
                "missing": [{"field": k, "label": LABELS[k]} for k in required if not body.get(k, "").strip()]}

    def overview(self):
        rows = self.rows()
        items = sorted([r for r in rows if r["kind"] in FIELDS], key=lambda r: r["updated_at"], reverse=True)
        decisions = {r["body"]["proposal_id"]: r["body"]["decision"] for r in reversed(rows)
                     if r["kind"] == "proposal_decision"}
        adopted = {r["body"].get("proposal_id") for r in rows if r["kind"] == "strategy"}
        proposals = [{**r, "decision": "adopted" if r["id"] in adopted else decisions.get(r["id"], "pending")}
                     for r in rows if r["kind"] == "proposal"]
        active_topics = [r for r in items if r["kind"] == "topic" and r["body"].get("stage") == "active"]
        focus = [r for r in items if r["kind"] in {"topic", "insight", "method", "practice"}
                 and r["body"].get("next_step", "").strip()
                 and r["body"].get("stage") not in {"paused", "archived"}
                 and (not r["body"].get("topic_id") or any(t["id"] == r["body"]["topic_id"] for t in active_topics))]
        by_id = {r["id"]: r for r in rows}
        attention = []
        for item in items:
            body, identifier = item["body"], item["id"]
            if body.get("stage") in {"paused", "archived"} or by_id.get(body.get("topic_id"), {}).get("body", {}).get("stage") in {"paused", "archived"}:
                continue
            pending = [s for s in rows if s["kind"] == "growth_suggestion" and
                       s["body"].get("target_id") == identifier and s["body"].get("decision") == "pending"]
            if pending:
                stale = any(s["body"]["expected_version"] != item["version"] for s in pending)
                attention.append({"id": identifier, "title": body["title"], "type": "suggestions",
                                  "label": "整理建议待审阅", "detail": f"{len(pending)} 份建议" + (" · 部分依据旧正文，需对照" if stale else " · 正文由你确认后更新")})
            changed = [s for s in body.get("sources", []) if by_id.get(s["id"], {}).get("version") != s["version"]]
            if changed:
                attention.append({"id": identifier, "title": body["title"], "type": "sources",
                                  "label": "引用资料有更新", "detail": f"{len(changed)} 份来源已有新版本 · 可以对照原意"})
            if item["kind"] == "practice" and body.get("phase") == "observing":
                progress = self.progress(item)
                attention.append({"id": identifier, "title": body["title"], "type": "practice",
                                  "label": "观察记录待回顾", "detail": progress["label"]})
        return {"items": items, "focus": focus[:3], "attention": attention, "proposals": proposals,
                "strategy": self.store.active_snapshot(), "labels": LABELS,
                "existing_count": sum(r["kind"] not in {*FIELDS, "growth_source", "growth_suggestion", "proposal_decision", "raw", "agent_run", "agent_result"} for r in rows)}

    def attach_conversation_source(self, identifier, source_id):
        """Link already-persisted real input without changing the editable draft version."""
        if self.get(identifier)["kind"] not in {*FIELDS, "proposal"}:
            raise DomainError("INVALID_RECORD", "当前内容不能关联来源")
        existing = next((r for r in self.rows() if r["kind"] == "growth_source" and
                         r["body"].get("target_id") == identifier and r["body"]["source"]["id"] == source_id), None)
        if existing:
            return existing
        source = self._sources([source_id])[0]
        return self.store.save_artifact("growth_source", {"target_id": identifier, "source": source},
                                        [identifier, source_id], "growth-source:" + identifier + ":" + source_id)

    def _validate(self, kind, body, old=None):
        if kind not in FIELDS or not isinstance(body, dict) or set(body) - FIELDS[kind] - PRIVATE_FIELDS:
            raise DomainError("INVALID_RECORD", "内容字段无效")
        for k, v in body.items():
            if k in PRIVATE_FIELDS:
                continue
            if not isinstance(v, str) or len(v) > (160 if k == "title" else 12000):
                raise DomainError("INVALID_RECORD", "文字过长或格式不正确")
        if not body.get("title", "").strip():
            raise DomainError("INVALID_RECORD", "请写一个标题")
        if kind in STAGES and body.get("stage") not in STAGES[kind]:
            raise DomainError("INVALID_RECORD", "请选择已有的状态")
        if kind == "topic" and body.get("area") not in {"trading", "general"}:
            raise DomainError("INVALID_RECORD", "请选择交易学习或其他学习")
        if kind == "practice" and body.get("evidence") not in {"support", "counter", "uncertain"}:
            raise DomainError("INVALID_RECORD", "请选择支持、反例或暂难判断")
        for field, expected_kind in [("topic_id", "topic"), ("method_id", "method")]:
            if body.get(field):
                parent = self.get(body[field])
                if parent["kind"] != expected_kind:
                    raise DomainError("INVALID_RECORD", "关联内容的类型不匹配")
                if field == "method_id" and parent["body"].get("topic_id", "") != body.get("topic_id", ""):
                    raise DomainError("INVALID_RECORD", "实践与方法必须属于同一专题")
                if field == "method_id" and not old and parent["body"].get("stage") == "paused":
                    raise DomainError("INVALID_RECORD", "请先恢复方法，再创建新的实践")
        if old and kind == "practice" and old.get("phase") != "draft":
            for key in ["method_id", "topic_id", "judgment", "conditions", "invalidation", "plan"]:
                if body.get(key, "") != old.get(key, ""):
                    raise DomainError("FROZEN_PRACTICE", "事前记录已锁定；请在实际情况或复盘中补充说明")
        if kind == "practice" and body.get("phase") == "draft" and any(body.get(k, "").strip() for k in ["actual", "result", "review", "lesson"]):
            raise DomainError("INVALID_RECORD", "请先锁定事前记录，再补充实际情况和复盘")
        if kind == "method" and body.get("stage") in {"trial", "adopted"}:
            if any(not body.get(k, "").strip() for k in ["scope", "conditions", "steps", "invalidation", "risk"]):
                raise DomainError("METHOD_INCOMPLETE", "试用前请补齐范围、条件、步骤、失效和风险处理；可以先保存为构思")

    def _sources(self, refs, previous=(), refresh=()):
        if not isinstance(refs, list) or len(refs) > 40 or any(not isinstance(i, str) for i in refs):
            raise DomainError("INVALID_RECORD", "关联资料最多 40 份")
        if not isinstance(refresh, (list, tuple)) or len(refresh) > 40 or any(i not in refs for i in refresh):
            raise DomainError("INVALID_RECORD", "只能更新当前保留的引用资料")
        old = {s["id"]: s for s in previous if s["id"] not in refresh}
        sources = []
        for identifier in dict.fromkeys(refs):
            row = self.get(identifier)
            sources.append(old.get(identifier) or {"id": identifier, "version": row["version"], "kind": row["kind"],
                           "title": str(row["body"].get("title") or row["body"].get("text") or row["kind"]).split("\n")[0][:100]})
        return sources

    def create(self, kind, fields, refs, key, origin=None):
        if kind not in FIELDS or set(fields) - FIELDS[kind]:
            raise DomainError("INVALID_RECORD", "内容字段无效")
        body = {**DEFAULTS[kind], **fields}
        if origin:
            body.update(origin=origin, curation_key=key)
        self._validate(kind, body)
        body["sources"] = self._sources(refs)
        parents = [body[k] for k in ["topic_id", "method_id"] if body.get(k)]
        with self.store.db() as db:
            def put():
                if kind == "handbook" and db.execute("SELECT 1 FROM objects WHERE kind='handbook'").fetchone():
                    raise DomainError("VERSION_CONFLICT", "交易理念已存在，请继续完善已有内容")
                return self.store._put(db, kind, body, list(dict.fromkeys([*refs, *parents])))
            return self.store._once(db, "growth-create:" + key, [kind, fields, refs], put)

    def _revise(self, db, row, body, reason):
        ts, version = now(), row["version"] + 1
        db.execute("UPDATE objects SET version=?,body=?,updated_at=?,status='current' WHERE id=?",
                   (version, canonical(body), ts, row["id"]))
        db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (row["id"], version, canonical(body), ts, reason))
        references = [s["id"] for s in body.get("sources", [])]
        references += [body[k] for k in ["topic_id", "method_id"] if body.get(k)]
        db.executemany("INSERT OR IGNORE INTO links VALUES(?,?)", [(r, row["id"]) for r in references if r != row["id"]])
        return self.store._read(db, row["id"])

    def update(self, identifier, version, patch, refs, key, refresh_refs=None):
        row = self.get(identifier)
        if row["kind"] not in FIELDS or set(patch) - FIELDS[row["kind"]]:
            raise DomainError("INVALID_RECORD", "无法直接修改这些字段")
        if identifier in (refs or []):
            raise DomainError("INVALID_RECORD", "不能把内容关联到自己")
        if refresh_refs and (refs is None or any(i not in {s["id"] for s in row["body"].get("sources", [])} for i in refresh_refs)):
            raise DomainError("INVALID_RECORD", "请先关联资料，再对照并更新引用")
        with self.store.db() as db:
            def action():
                current = self.store._read(db, identifier)
                if current["version"] != version:
                    raise DomainError("VERSION_CONFLICT", "内容已有更新。你的草稿已保留，请重新载入并比较后保存")
                body = {**current["body"], **patch}
                if current["kind"] == "method" and body.get("topic_id", "") != current["body"].get("topic_id", ""):
                    if any(r["kind"] == "practice" and r["body"].get("method_id") == identifier for r in self.rows()):
                        raise DomainError("INVALID_RECORD", "这个方法已有实践记录，请保留所属专题；可以在新专题另建方法并关联原方法")
                if patch and body.get("origin"):
                    body["origin"] = {**body["origin"], "status": "reviewed"}
                if refs is not None:
                    body["sources"] = self._sources(refs, current["body"].get("sources", []), refresh_refs or [])
                self._validate(current["kind"], body, current["body"])
                return self._revise(db, current, body, "用户编辑")
            arguments = [identifier, version, patch, refs]
            if refresh_refs:
                arguments.append(refresh_refs)
            return self.store._once(db, "growth-update:" + key, arguments, action)

    def practice_action(self, identifier, version, action, key):
        with self.store.db() as db:
            def apply():
                row = self.store._read(db, identifier)
                if row["kind"] != "practice" or row["version"] != version:
                    raise DomainError("VERSION_CONFLICT", "请重新载入当前实践")
                body = deepcopy(row["body"])
                if action == "freeze" and body["phase"] == "draft":
                    if any(not body.get(k, "").strip() for k in ["judgment", "conditions", "invalidation", "plan"]):
                        raise DomainError("INVALID_RECORD", "请先记录判断、条件、失效和计划；不确定时可以明确写出缺口")
                    method = self.store._read(db, body["method_id"]) if body.get("method_id") else None
                    if method and method["body"].get("stage") == "paused":
                        raise DomainError("INVALID_RECORD", "关联方法已暂停，请重新评估后恢复方法")
                    snapshot = {k: method[k] for k in ["id", "kind", "version", "body"]} if method else None
                    body.update(phase="observing", frozen_at=now(), method_snapshot=snapshot,
                                strategy_snapshot=self.store.active_snapshot())
                elif action == "review" and body["phase"] in {"observing", "reviewed"}:
                    if any(not body.get(k, "").strip() for k in ["actual", "result", "review"]):
                        raise DomainError("INVALID_RECORD", "请补充实际行动、结果和复盘")
                    body.update(phase="reviewed", reviewed_at=now())
                else:
                    raise DomainError("INVALID_RECORD", "当前阶段不能执行这项操作")
                return self._revise(db, row, body, "锁定事前记录" if action == "freeze" else "完成复盘")
            return self.store._once(db, "practice:" + key, [identifier, version, action], apply)

    def suggest(self, identifier, version, patch, reason, key):
        row = self.get(identifier)
        if row["kind"] not in FIELDS or row["kind"] == "handbook":
            raise DomainError("INVALID_RECORD", "当前内容不能由模型提交修改")
        allowed = FIELDS[row["kind"]] - {"topic_id", "method_id", "stage", "area", "evidence", "category"}
        if not patch or set(patch) - allowed:
            raise DomainError("INVALID_RECORD", "建议只能修改正文，不能改变个人选择或实践状态")
        if row["version"] != version:
            raise DomainError("VERSION_CONFLICT", "当前正文已经更新，请重新读取后提出建议")
        self._validate(row["kind"], {**row["body"], **patch}, row["body"])
        return self.store.save_artifact("growth_suggestion", {"target_id": identifier, "expected_version": version,
                "patch": patch, "reason": str(reason)[:2000], "decision": "pending"}, [identifier], key)

    def decide_suggestion(self, identifier, decision):
        with self.store.db() as db:
            row = self.store._read(db, identifier)
            if row["kind"] != "growth_suggestion" or decision not in {"accept", "dismiss"}:
                raise DomainError("INVALID_RECORD", "建议操作无效")
            if row["body"]["decision"] != "pending":
                return self.store._read(db, row["body"]["target_id"])
            body = row["body"]
            target = self.store._read(db, body["target_id"])
            if decision == "accept":
                if target["version"] != body["expected_version"]:
                    raise DomainError("VERSION_CONFLICT", "建议针对的是旧版本，请保留当前正文，重新讨论或手动编辑")
                merged = {**target["body"], **body["patch"]}
                if merged.get("origin"):
                    merged["origin"] = {**merged["origin"], "status": "reviewed"}
                self._validate(target["kind"], merged, target["body"])
                target = self._revise(db, target, merged, "用户采用 AI 整理建议")
            self._revise(db, row, {**body, "decision": decision, "decided_at": now()}, "用户审阅建议")
            return target

    def context(self, identifier):
        row = self.detail(identifier)
        references = []
        for s in row["sources"]:
            source = self.store.read(s["id"], version=s["version"])
            references.append({"id": s["id"], "version": s["version"], "kind": source["kind"], "body": source["body"]})
        topic = self.get(row["body"]["topic_id"]) if row["body"].get("topic_id") else None
        return {"id": row["id"], "kind": row["kind"], "version": row["version"], "body": row["body"],
                "topic": topic, "sources": references[:8], "related": row["related"][:12],
                "source_coverage": f"当前已关联 {len(references)} 份来源，随上下文提供前 8 份；其余可按 ID 读取。"}

    def propose_rules(self, rules, rationale, refs, base_id, key):
        if base_id != self.store.active_snapshot()["id"]:
            raise DomainError("VERSION_CONFLICT", "生效纪律已变化，请重新载入后再提出修改")
        if not rationale.strip() or len(rules) > 50:
            raise DomainError("INVALID_RULE", "请说明修改理由；纪律最多 50 条")
        for rule in rules:
            if not isinstance(rule.get("id"), str) or not rule["id"].strip() or len(rule["id"]) > 160:
                raise DomainError("INVALID_RULE", "纪律标识无效")
            if set(rule) - {"id", "text", "horizon", "condition"} or not isinstance(rule.get("text"), str) or not rule["text"].strip():
                raise DomainError("INVALID_RULE", "每条纪律需明确正文")
            if rule.get("horizon", "unknown") not in {"short", "medium_long", "unknown"}:
                raise DomainError("INVALID_RULE", "交易周期无效")
            condition = rule.get("condition")
            if condition is not None:
                if not isinstance(condition, dict) or not isinstance(condition.get("field"), str) or not condition["field"] or condition.get("operator") not in {"lte", "gte", "eq", "in"} or "value" not in condition:
                    raise DomainError("INVALID_RULE", "纪律条件格式无效")
                value, op = condition["value"], condition["operator"]
                if (op in {"lte", "gte"} and (not isinstance(value, (int, float)) or isinstance(value, bool))) or (op == "in" and not isinstance(value, list)):
                    raise DomainError("INVALID_RULE", "数值纪律需要数字，范围纪律需要选项列表")
        proposal = self.store.propose(rules, rationale.strip(), refs, "growth:" + key)
        for source_id in refs:
            self.attach_conversation_source(proposal["id"], source_id)
        return proposal

    def proposal_detail(self, identifier):
        row = self.get(identifier)
        if row["kind"] != "proposal" or not row["body"].get("digest"):
            raise DomainError("INVALID_RECORD", "这份历史提案缺少完整指纹，请重新整理后提出")
        base = self.get(row["body"]["base_id"]) if row["body"].get("base_id") else {"body": {"rules": []}, "id": None}
        adopted = next((r for r in self.rows() if r["kind"] == "strategy" and r["body"].get("proposal_id") == identifier), None)
        decisions = [r for r in self.rows() if r["kind"] == "proposal_decision" and r["body"].get("proposal_id") == identifier]
        sources = [r["body"]["source"] for r in self.rows() if r["kind"] == "growth_source" and r["body"].get("target_id") == identifier]
        return {**row, "base": base, "current": self.store.active_snapshot(), "adopted": adopted, "sources": sources,
                "decision": "adopted" if adopted else decisions[0]["body"]["decision"] if decisions else "pending"}

    def decide_proposal(self, identifier, decision, fingerprint):
        row = self.proposal_detail(identifier)
        if fingerprint != row["body"]["digest"]:
            raise DomainError("PROPOSAL_CHANGED", "提案内容已变化，请重新审阅")
        if row["adopted"]:
            return row["adopted"]
        if decision not in {"adopt", "later", "reject"}:
            raise DomainError("INVALID_RECORD", "请选择采用、暂缓或不采用")
        if decision == "adopt":
            if row["current"]["id"] != row["body"].get("base_id"):
                raise DomainError("VERSION_CONFLICT", "当前体系已变化，请重新比较提案")
            confirmation = self.store.capture("owner-approval:" + identifier, "确认 " + identifier + " " + fingerprint)
            return self.store.activate(identifier, fingerprint, confirmation["source_id"])
        return self.store.save_artifact("proposal_decision", {"proposal_id": identifier, "decision": decision},
                                       [identifier], identifier + ":" + decision)
