import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from cloud_trading.growth import GrowthWorkspace
from cloud_trading.companion import CompanionMemory
from cloud_trading.curation import KnowledgeCurator
from trading_agent.store import Store
from audit_agent_runtime.llm.base import ModelResponse, ToolCall


class CollaboratingModel:
    def __init__(self, forbidden=False):
        self.calls = []
        self.forbidden = forbidden

    def complete(self, messages, tools):
        self.calls.append((messages, tools))
        n = len(self.calls)
        if n == 1:
            return ModelResponse(tool_calls=[ToolCall("delegate", "collaborate", {
                "agents": ["learning", "evolution"], "task": "分别检查同一个隔离学习问题，核对已有资料。"})])
        if n in {2, 4}:
            return ModelResponse(tool_calls=[ToolCall("check" + str(n), "analysis_save" if self.forbidden else "memory_search",
                {"text": "不允许的写入", "key": "forbidden"} if self.forbidden else {"query": "隔离依据"})])
        return ModelResponse(output_text="已对照现有资料，未验证的判断继续保留。")


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = Config(root / "trading", root / "runtime", "test-password")
        self.store = Store(self.config.data_dir)
        self.growth = GrowthWorkspace(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, client):
        client.post("/api/login", json={"password": self.config.password})

    def test_curation_is_attributed_idempotent_and_preserves_owner_edits(self):
        profile = self.store.save_artifact("profile", {"text": "工作之余学习，具体投入范围未确定。"}, [], "profile")
        hypothesis = self.store.save_artifact("hypothesis", {"text": "底部尚需定义。"}, [], "hypothesis")
        source = self.store.save_artifact("analysis", {"text": "\n# 当时的研究\n价格仅代表历史时点。"}, [], "research")
        originals = self.store.snapshot(limit=None)["records"]
        curator = KnowledgeCurator(self.store)
        first = curator.run()
        self.assertGreater(first["created"], 24)
        self.assertEqual(curator.run()["created"], 0)
        for row in originals:
            self.assertEqual(self.store.read(row["id"])["body"], row["body"])
            self.assertEqual(self.store.read(row["id"])["version"], row["version"])
        items = self.growth.overview()["items"]
        self.assertTrue(all(r["body"]["origin"]["status"] == "pending" for r in items))
        self.assertFalse(any(r["kind"] == "practice" for r in items))
        self.assertIsNone(self.store.active_snapshot()["id"])
        method = next(r for r in items if r["kind"] == "method")
        self.assertEqual(method["body"]["stage"], "draft")
        self.assertEqual(method["body"]["sources"][0]["id"], hypothesis["id"])
        classic = next(r for r in items if r["kind"] == "insight" and r["body"]["origin"]["kind"] == "classic")
        self.growth.update(classic["id"], 1, {"understanding": "自己重新写过的理解"}, None, "owner-edit")
        curator.run()
        self.assertEqual(self.growth.get(classic["id"])["body"]["understanding"], "自己重新写过的理解")
        self.assertEqual(self.growth.get(classic["id"])["body"]["stage"], "draft")
        historical = next(r for r in items if r["kind"] == "insight" and r["body"]["sources"][0]["id"] == source["id"])
        self.assertEqual(historical["body"]["title"], "当时的研究")
        self.assertTrue(any(r["kind"] == "handbook" and r["body"]["sources"][0]["id"] == profile["id"] for r in items))

    def test_feedback_scopes_and_latest_preference_feed_the_next_model_request(self):
        a = self.growth.create("topic", {"title": "当前学习"}, [], "a")
        b = self.growth.create("topic", {"title": "另一个专题"}, [], "b")
        memory = CompanionMemory(self.store)
        memory.feedback(b["id"], "session", "adjust", "只属于另一个专题的反馈", "other")
        self.assertEqual(memory.context(a["id"])["feedback"], [])
        class InspectModel:
            seen = None
            def complete(inner, messages, tools):
                inner.seen = messages
                return ModelResponse(output_text="收到偏好。")
        model = InspectModel()
        with TestClient(create_app(self.config, model)) as client:
            self.login(client)
            self.assertEqual(client.post("/api/companion/preferences", json={"style": "concise", "focus": "先谈依据，一次一个问题", "key": "prefs"}).status_code, 200)
            self.assertEqual(client.post("/api/companion/feedback", json={"target_id": a["id"], "session_id": "session", "rating": "adjust", "note": "请减少术语", "key": "feedback"}).status_code, 200)
            client.post("/api/router/ask", json={"session_id": "next", "message_id": "next", "context_id": a["id"], "text": "继续", "remember": False})
            text = "\n".join(str(m.content) for m in model.seen)
            self.assertIn("请减少术语", text)
            self.assertIn("先谈依据，一次一个问题", text)
            self.assertNotIn("只属于另一个专题的反馈", text)
            self.assertNotIn("companion_feedback", client.get("/api/knowledge?shelf=all").json()["kinds"])
            self.assertTrue(client.get("/api/companion").json()["history"])

    def test_real_core_runs_independent_specialists_with_shared_read_tools(self):
        source = self.store.save_artifact("note", {"text": "隔离依据：保留未知。"}, [], "source")
        model = CollaboratingModel()
        with TestClient(create_app(self.config, model)) as client:
            self.assertEqual(client.get("/api/companion").status_code, 401)
            self.login(client)
            response = client.post("/api/router/ask", json={"session_id": "team", "message_id": "team", "text": "共同检查", "remember": True})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            result = next(e["payload"]["result"] for e in events if e["type"] == "tool_result" and e["payload"]["tool_name"] == "collaborate")
            self.assertEqual([m["status"] for m in result["members"]], ["complete", "complete"])
            self.assertTrue(all(m["tools"] == ["memory_search"] for m in result["members"]))
            self.assertNotEqual(str([m.content for m in model.calls[1][0]]), str([m.content for m in model.calls[3][0]]))
            self.assertNotIn("collaborate", [t["name"] for t in model.calls[1][1]])
            self.assertIn(source["id"], str([m.content for m in model.calls[2][0]]))
            self.assertEqual(len(client.get("/api/companion/collaborations?session_id=team").json()["runs"]), 1)
            self.assertEqual(self.store.read(source["id"])["version"], 1)

    def test_specialist_writes_fail_and_no_memory_collaboration_is_not_saved(self):
        model = CollaboratingModel(forbidden=True)
        with TestClient(create_app(self.config, model)) as client:
            self.login(client)
            response = client.post("/api/router/ask", json={"session_id": "team", "message_id": "team", "text": "共同检查", "remember": False})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            result = next(e["payload"]["result"] for e in events if e["type"] == "tool_result" and e["payload"]["tool_name"] == "collaborate")
            self.assertTrue(all(m["status"] == "partial" for m in result["members"]))
            self.assertEqual(self.store.snapshot(limit=None)["records"], [])

    def test_skill_reference_supports_declared_public_links_and_excludes_private_files(self):
        from cloud_trading.extensions.trading_partner.tools import SkillReferenceTool
        from types import SimpleNamespace
        tool = SkillReferenceTool()
        context = SimpleNamespace(metadata={})
        for path in ["../../knowledge/classics/sources.md", "knowledge/classics/cards.md"]:
            result = tool.execute({"skill": "trading-classic-learning", "path": path}, context)
            self.assertIn("content", result)
        self.assertTrue(tool.execute({"skill": "trading-classic-learning", "path": "../../config/app.env"}, context)["is_error"])

    def test_new_agent_can_share_existing_skill_and_tool_without_page_changes(self):
        from cloud_trading.runtime import assemble
        from cloud_trading.capabilities import CapabilityCatalog
        services = assemble(self.config, CollaboratingModel())
        try:
            path = Path(self.tmp.name) / "capabilities.toml"
            path.write_text('[[agents]]\nid="new-ability"\nname="新能力"\ndescription="新增检查职责"\nskill="trading-classic-learning"\ntools=["memory_search"]\n')
            catalog = CapabilityCatalog(services.capabilities.skills, services.tools, path)
            self.assertEqual(catalog.public()["agents"][0]["id"], "new-ability")
            self.assertEqual(catalog.public()["agents"][0]["tools"], ["memory_search"])
        finally:
            services.query._post_turn_maintenance_executor.shutdown(wait=True)
            services.model_client.close()
            services.engine.dispose()

    def test_standalone_note_finds_its_method_and_learns_from_the_review(self):
        note = self.growth.create("insight", {"title": "单独保存的认识", "understanding": "先明确条件"}, [], "note")
        method = self.growth.create("method", {"title": "由认识形成的方法"}, [note["id"]], "method")
        case = self.growth.create("practice", {"title": "一次只观察的实践", "method_id": method["id"],
            "judgment": "未成交", "conditions": "先观察", "invalidation": "定义不清", "plan": "记录变化"}, [], "case")
        frozen = self.growth.practice_action(case["id"], 1, "freeze", "freeze")
        actual = self.growth.update(case["id"], frozen["version"], {"actual": "只观察", "result": "资料不足",
            "review": "不能证明原判断", "lesson": "先补齐定义", "evidence": "counter"}, None, "actual")
        self.growth.practice_action(case["id"], actual["version"], "review", "review")
        self.assertIn(method["id"], [r["id"] for r in self.growth.detail(note["id"])["related"]])
        memory = CompanionMemory(self.store)
        memory.feedback(note["id"], "note-session", "adjust", "先明确观察口径", "note-feedback")
        self.assertEqual(memory.context(method["id"])["feedback"][0]["note"], "先明确观察口径")
        self.assertEqual(memory.context(note["id"])["reviewed_cases"][0]["lesson"], "先补齐定义")
        self.assertEqual(memory.context(note["id"])["revisit"][0]["id"], method["id"])
        other = self.growth.create("topic", {"title": "不相关的专题"}, [], "other")
        self.assertEqual(memory.context(other["id"])["reviewed_cases"], [])
