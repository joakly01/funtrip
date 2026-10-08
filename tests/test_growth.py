import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from cloud_trading.growth import GrowthWorkspace
from trading_agent.store import Store
from trading_agent.common import DomainError
from audit_agent_runtime.llm.base import ModelResponse, ToolCall


class GrowthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = Config(root / "trading", root / "runtime", "test-password")
        self.store = Store(self.config.data_dir)
        self.growth = GrowthWorkspace(self.store)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def key(self):
        self.n += 1
        return str(self.n)

    def item(self, kind, **body):
        return self.growth.create(kind, {"title": "隔离测试 " + kind, **body}, [], self.key())

    def method(self, topic_id=""):
        return self.item("method", topic_id=topic_id, scope="只用于隔离观察", conditions="条件明确后再观察",
                         steps="记下当时看见什么", invalidation="依据缺失", risk="只观察不交易", stage="trial")

    def practice(self, method):
        return self.item("practice", topic_id=method["body"].get("topic_id", ""), method_id=method["id"],
                         judgment="还未明确", conditions="保存事前材料", invalidation="定义不清", plan="只观察")

    def test_empty_read_is_nonmutating_and_idempotent_edits_keep_versions(self):
        self.assertEqual(self.growth.overview()["items"], [])
        self.assertFalse((self.config.data_dir / "knowledge.sqlite").exists())
        row = self.growth.create("topic", {"title": "同一个问题"}, [], "same")
        repeat = self.growth.create("topic", {"title": "同一个问题"}, [], "same")
        self.assertEqual(row["id"], repeat["id"])
        a = self.growth.update(row["id"], 1, {"summary": "我的认识"}, None, "edit")
        b = self.growth.update(row["id"], 1, {"summary": "我的认识"}, None, "edit")
        self.assertEqual(a["version"], b["version"])
        self.assertEqual(a["version"], 2)
        with self.assertRaises(DomainError):
            self.growth.update(row["id"], 1, {"summary": "覆盖新版本"}, None, self.key())
        self.assertEqual(len(self.growth.detail(row["id"])["history"]), 2)

    def test_sources_are_pinned_and_original_records_preserved(self):
        topic = self.item("topic")
        insight = self.item("insight", topic_id=topic["id"], understanding="旧理解")
        method = self.growth.create("method", {"title": "一个方法", "topic_id": topic["id"]}, [insight["id"]], self.key())
        self.growth.update(insight["id"], 1, {"understanding": "新的理解"}, None, self.key())
        ctx = self.growth.context(method["id"])
        self.assertEqual(ctx["sources"][0]["body"]["understanding"], "旧理解")
        self.assertTrue(self.growth.detail(method["id"])["sources"][0]["changed"])
        self.assertEqual(self.growth.get(insight["id"])["body"]["understanding"], "新的理解")
        self.assertEqual(ctx["topic"]["id"], topic["id"])

    def test_owner_can_refresh_or_unlink_sources_without_rewriting_originals(self):
        source = self.item("insight", understanding="原来的理解")
        target = self.growth.create("method", {"title": "有出处的方法"}, [source["id"]], self.key())
        self.growth.update(source["id"], 1, {"understanding": "后来修改的理解"}, None, self.key())
        self.assertTrue(self.growth.detail(target["id"])["sources"][0]["managed"])
        key = self.key()
        refreshed = self.growth.update(target["id"], 1, {}, [source["id"]], key, [source["id"]])
        self.assertEqual(self.growth.update(target["id"], 1, {}, [source["id"]], key, [source["id"]])["version"], 2)
        self.assertEqual(refreshed["body"]["sources"][0]["version"], 2)
        self.assertFalse(self.growth.detail(target["id"])["sources"][0]["changed"])
        self.assertEqual(self.store.read(target["id"], 1)["body"]["sources"][0]["version"], 1)
        unlinked = self.growth.update(target["id"], 2, {}, [], self.key())
        self.assertEqual(unlinked["body"]["sources"], [])
        self.assertEqual(self.growth.get(source["id"])["version"], 2)
        with self.assertRaises(DomainError):
            self.growth.update(target["id"], 3, {}, [], self.key(), [source["id"]])

    def test_reclassification_keeps_practice_and_method_consistent(self):
        first, second = self.item("topic"), self.item("topic")
        insight = self.item("insight", topic_id=first["id"])
        moved = self.growth.update(insight["id"], 1, {"topic_id": second["id"]}, None, self.key())
        self.assertEqual(moved["body"]["topic_id"], second["id"])
        method = self.method(first["id"])
        practice = self.practice(method)
        with self.assertRaisesRegex(DomainError, "已有实践"):
            self.growth.update(method["id"], 1, {"topic_id": second["id"]}, None, self.key())
        frozen = self.growth.practice_action(practice["id"], 1, "freeze", self.key())
        with self.assertRaises(DomainError):
            self.growth.update(practice["id"], frozen["version"], {"method_id": "", "topic_id": second["id"]}, None, self.key())
        self.assertEqual(self.growth.get(method["id"])["body"]["topic_id"], first["id"])

    def test_continuation_reports_missing_fields_and_only_actionable_attention(self):
        draft = self.item("practice", judgment="仅观察")
        progress = self.growth.detail(draft["id"])["progress"]
        self.assertEqual(progress["action"], "edit")
        self.assertEqual({m["field"] for m in progress["missing"]}, {"conditions", "invalidation", "plan"})
        method = self.method()
        practice = self.practice(method)
        self.assertEqual(self.growth.detail(practice["id"])["progress"]["action"], "freeze")
        frozen = self.growth.practice_action(practice["id"], 1, "freeze", self.key())
        self.growth.suggest(method["id"], 1, {"unknowns": "待核查"}, "隔离建议", self.key())
        attention = self.growth.overview()["attention"]
        self.assertEqual({(a["id"], a["type"]) for a in attention}, {(practice["id"], "practice"), (method["id"], "suggestions")})
        self.growth.update(practice["id"], frozen["version"], {"actual": "未交易", "result": "观察结束", "review": "证据不足"}, None, self.key())
        self.assertEqual(self.growth.detail(practice["id"])["progress"]["action"], "review")
        topic = self.item("topic")
        self.growth.update(method["id"], 1, {"stage": "paused"}, None, self.key())
        self.assertFalse(any(a["id"] == method["id"] for a in self.growth.overview()["attention"]))
        self.assertEqual(self.growth.detail(topic["id"])["progress"]["action"], "insight")

    def test_source_refresh_route_requires_owner_and_keeps_conversation_sources(self):
        source = self.item("insight")
        target = self.growth.create("topic", {"title": "隔离专题"}, [source["id"]], self.key())
        raw = self.store.capture("isolation-source", "隔离原话")["source_id"]
        self.growth.attach_conversation_source(target["id"], raw)
        self.growth.update(source["id"], 1, {"understanding": "新版本"}, None, self.key())
        with TestClient(create_app(self.config)) as c:
            payload = {"version": 1, "patch": {}, "refs": [source["id"]], "refresh_refs": [source["id"]], "key": self.key()}
            self.assertEqual(c.put('/api/growth/items/' + target["id"], json=payload).status_code, 401)
            c.post('/api/login', json={"password": self.config.password})
            self.assertEqual(c.put('/api/growth/items/' + target["id"], json=payload).status_code, 200)
            detail = c.get('/api/growth/items/' + target["id"]).json()
            self.assertEqual({s["id"]: s["managed"] for s in detail["sources"]}, {source["id"]: True, raw: False})

    def test_practice_freezes_before_context_and_collects_counterexample(self):
        topic = self.item("topic")
        method = self.method(topic["id"])
        practice = self.practice(method)
        frozen = self.growth.practice_action(practice["id"], 1, "freeze", self.key())
        self.growth.update(method["id"], 1, {"conditions": "之后才修改的条件"}, None, self.key())
        with self.assertRaises(DomainError):
            self.growth.update(practice["id"], 2, {"judgment": "事后改写"}, None, self.key())
        with self.assertRaises(DomainError):
            self.growth.update(practice["id"], 2, {"phase": "reviewed"}, None, self.key())
        actual = self.growth.update(practice["id"], frozen["version"], {"actual": "只观察，没有成交", "result": "与预期不同",
                    "review": "这次不足以支持原判断", "evidence": "counter", "lesson": "继续检验适用条件"}, None, self.key())
        review = self.growth.practice_action(practice["id"], actual["version"], "review", self.key())
        self.assertEqual(review["body"]["method_snapshot"]["version"], 1)
        self.assertEqual(review["body"]["phase"], "reviewed")
        self.assertEqual(review["body"]["evidence"], "counter")
        self.assertEqual(self.store.snapshot(kind="trade")["records"], [])
        self.assertEqual(self.growth.detail(method["id"])["related"][0]["id"], practice["id"])

    def test_conversation_sources_preserve_draft_version_and_pinned_read(self):
        topic = self.item("topic", summary="最初的理解")
        source = self.store.capture("isolated-message", "这是一段隔离原话")["source_id"]
        first = self.growth.attach_conversation_source(topic["id"], source)
        again = self.growth.attach_conversation_source(topic["id"], source)
        self.assertEqual(first["id"], again["id"])
        detail = self.growth.detail(topic["id"])
        self.assertEqual(detail["version"], 1)
        self.assertEqual(detail["sources"][0]["id"], source)
        self.growth.update(topic["id"], 1, {"summary": "后来改过的理解"}, None, self.key())
        with TestClient(create_app(self.config)) as client:
            client.post("/api/login", json={"password": self.config.password})
            pinned = client.get("/api/records/" + topic["id"] + "?version=1")
            self.assertEqual(pinned.status_code, 200)
            self.assertEqual(pinned.json()["body"]["summary"], "最初的理解")
            self.assertNotIn("growth_source", client.get("/api/knowledge?shelf=all").json()["kinds"])

    def test_invalid_rule_condition_has_domain_error(self):
        for rule in [{"id": [], "text": "不合法标识"},
                     {"id": "x", "text": "不合法数值", "condition": {"field": "weight", "operator": "lte", "value": "abc"}}]:
            with self.assertRaises(DomainError):
                self.growth.propose_rules([rule], "隔离校验", [], None, self.key())

    def test_proposal_source_is_pinned_before_later_edit(self):
        insight = self.item("insight", understanding="提出修改时的依据")
        proposal = self.growth.propose_rules([{"id": "r1", "text": "隔离条款"}], "依据说明", [insight["id"]], None, self.key())
        self.growth.update(insight["id"], 1, {"understanding": "之后才改变的认识"}, None, self.key())
        self.growth.attach_conversation_source(proposal["id"], insight["id"])
        source = self.growth.proposal_detail(proposal["id"])["sources"][0]
        self.assertEqual(source["version"], 1)
        self.assertEqual(self.store.read(source["id"], source["version"])["body"]["understanding"], "提出修改时的依据")

    def test_invalid_transitions_and_cross_topic_associations_are_rejected(self):
        with self.assertRaises(DomainError):
            self.item("method", stage="adopted")
        m = self.method(self.item("topic")["id"])
        with self.assertRaises(DomainError):
            self.item("practice", topic_id=self.item("topic")["id"], method_id=m["id"])
        with self.assertRaises(DomainError):
            self.item("practice", result="在事前草稿填写结果")
        p = self.item("practice")
        with self.assertRaises(DomainError):
            self.growth.practice_action(p["id"], 1, "freeze", self.key())
        self.growth.update(m["id"], 1, {"stage": "paused"}, None, self.key())
        with self.assertRaises(DomainError):
            self.practice(m)

    def test_suggestions_never_overwrite_and_stale_suggestions_cannot_apply(self):
        row = self.item("insight", understanding="用户自己的话")
        draft = self.growth.suggest(row["id"], 1, {"understanding": "模型建议"}, "整理表达", self.key())
        self.assertEqual(self.growth.get(row["id"])["body"]["understanding"], "用户自己的话")
        result = self.growth.decide_suggestion(draft["id"], "accept")
        self.assertEqual(result["body"]["understanding"], "模型建议")
        self.assertEqual(result["version"], 2)
        self.assertEqual(self.growth.decide_suggestion(draft["id"], "accept")["version"], 2)
        stale = self.growth.suggest(row["id"], 2, {"understanding": "旧建议"}, "理由", self.key())
        self.growth.update(row["id"], 2, {"understanding": "用户后来编辑"}, None, self.key())
        with self.assertRaises(DomainError):
            self.growth.decide_suggestion(stale["id"], "accept")
        with self.assertRaises(DomainError):
            self.growth.suggest(row["id"], 3, {"stage": "understood"}, "代替用户决定", self.key())
        self.assertEqual(self.growth.get(row["id"])["body"]["understanding"], "用户后来编辑")

    def test_proposal_owner_confirmation_keeps_historical_strategy(self):
        first = self.growth.propose_rules([{"id": "r1", "text": "隔离测试纪律"}], "测试理由", [], None, self.key())
        second = self.growth.propose_rules([], "另一个旧版本提案", [], None, self.key())
        self.assertIsNone(self.store.active_snapshot()["id"])
        active = self.growth.decide_proposal(first["id"], "adopt", first["body"]["digest"])
        self.assertEqual(active["id"], self.growth.decide_proposal(first["id"], "adopt", first["body"]["digest"])["id"])
        with self.assertRaises(DomainError):
            self.growth.decide_proposal(second["id"], "adopt", second["body"]["digest"])
        practice = self.practice(self.method())
        frozen = self.growth.practice_action(practice["id"], 1, "freeze", self.key())
        change = self.growth.propose_rules([], "移除测试纪律", [practice["id"]], active["id"], self.key())
        self.growth.decide_proposal(change["id"], "adopt", change["body"]["digest"])
        self.assertEqual(frozen["body"]["strategy_snapshot"]["id"], active["id"])
        self.assertEqual(self.store.active_snapshot()["body"]["rules"], [])
        self.assertEqual(len(self.store.snapshot(kind="strategy")["records"]), 2)

    def test_paused_topics_are_not_recommended_and_handbook_is_single(self):
        topic = self.item("topic", next_step="继续思考")
        insight = self.item("insight", topic_id=topic["id"], next_step="解释概念")
        self.assertEqual(len(self.growth.overview()["focus"]), 2)
        self.growth.update(topic["id"], 1, {"stage": "paused"}, None, self.key())
        self.assertEqual(self.growth.overview()["focus"], [])
        self.item("handbook")
        with self.assertRaises(DomainError):
            self.item("handbook")

    def test_authenticated_http_flow_and_context_model_suggests_without_overwriting(self):
        workspace = self.growth
        class Model:
            calls = 0
            def complete(self, messages, tools):
                self.calls += 1
                context = next(m.content for m in messages if m.role == "system" and isinstance(m.content, str) and "当前页面的资料" in m.content)
                self.last_context = context
                if self.calls == 1:
                    target = workspace.overview()["items"][0]
                    return ModelResponse(tool_calls=[ToolCall("suggest-test", "growth_suggest", {"target_id": target["id"],
                         "expected_version": target["version"], "patch": {"summary": "仅为隔离建议"}, "reason": "围绕已有问题"})])
                return ModelResponse(output_text="整理建议已保存，等待审阅。")
        model = Model()
        with TestClient(create_app(self.config, model)) as c:
            self.assertEqual(c.get('/api/growth').status_code, 401)
            c.post('/api/login', json={"password": "test-password"})
            row = c.post('/api/growth/items', json={"kind": "topic", "fields": {"title": "隔离上下文问题"}, "key": "first"}).json()
            reply = c.post('/api/router/ask', json={"session_id": "growth_" + row["id"], "message_id": "context-test", "text": "请帮我整理", "context_id": row["id"]})
            self.assertIn('整理建议已保存', reply.text)
            self.assertIn('隔离上下文问题', model.last_context)
            detail = c.get('/api/growth/items/' + row["id"]).json()
            self.assertNotIn("summary", detail["body"])
            self.assertEqual(len(detail['suggestions']), 1)
            suggestion = detail['suggestions'][0]
            result = c.post('/api/growth/suggestions/' + suggestion['id'] + '/decision', json={"decision": "accept"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['body']['summary'], '仅为隔离建议')
            self.assertEqual(c.post('/api/growth/items/'+row['id'],json={}).status_code,405)
            self.assertEqual(c.put('/api/growth/items/'+row['id'],json={"version":1,"patch":{"summary":"旧版覆盖"},"key":"stale"}).status_code,409)
