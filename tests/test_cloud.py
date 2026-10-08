import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from cloud_trading.app import create_app
from cloud_trading.config import Config
from audit_agent_runtime.llm.base import ModelResponse, ToolCall
from trading_agent.service import Service


class ScriptedModel:
    """Synthetic model only for isolated execution-chain tests."""
    def __init__(self, tool="partner_process", fail=False):
        self.calls = 0
        self.tool = tool
        self.fail = fail

    def complete(self, messages, tools):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider-private-error")
        if self.calls == 1:
            if self.tool == "partner_process":
                args = {"message_id": "model-invented", "text": "模型代造的原话", "key": "random-model-key",
                        "intents": ["plan"], "items": [{"item_key": "plan", "kind": "plan", "nature": "plan",
                        "text": "准备观察，尚未成交", "excerpt": "打算买，但没有下单", "fields": {}}]}
            else:
                args = {"text": "不应落库的分析", "claims": [], "refs": [], "key": "forbidden-write"}
            return ModelResponse(tool_calls=[ToolCall("test-call", self.tool, args)])
        return ModelResponse(output_text="这是隔离测试回复，计划与成交已分别处理。")


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.config = Config(base / "trading", base / "runtime", "test-password")

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, client):
        self.assertEqual(client.post("/api/login", json={"password": "test-password"}).status_code, 200)

    def body(self, **changes):
        return {"session_id": "test-session", "message_id": "test-message", "text": "打算买，但没有下单", **changes}

    def events(self, response):
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_real_core_tool_loop_binds_original_and_replays_without_duplicate(self):
        model = ScriptedModel()
        with TestClient(create_app(self.config, model)) as client:
            self.login(client)
            response = client.post("/api/router/ask", json=self.body())
            self.assertEqual(response.status_code, 200)
            events = self.events(response)
            self.assertTrue(any(e["type"] == "assistant" for e in events), events)
            tool_results = [e for e in events if e["type"] == "tool_result"]
            self.assertTrue(tool_results, events)
            self.assertNotIn('"is_error": true', json.dumps(tool_results))
            self.assertEqual(client.post("/api/router/ask", json=self.body()).text, response.text)
            self.assertEqual(model.calls, 2)
            service = Service(self.config.data_dir)
            plans = service.store.search(kind="plan")["records"]
            self.assertEqual(len(plans), 1)
            self.assertEqual(service.store.search(kind="trade")["records"], [])
            raw = service.store.search(kind="raw")["records"]
            self.assertEqual(len(raw), 1)
            self.assertEqual(raw[0]["body"]["text"], self.body()["text"])
            history = client.get("/api/sessions/test-session").json()
            self.assertEqual(sum(m["role"] == "user" for m in history), 1)
            conflict = client.post("/api/router/ask", json=self.body(text="不同内容"))
            self.assertEqual(conflict.status_code, 409)

    def test_anthropic_tool_json_is_encoded_without_mutating_history(self):
        from cloud_trading.runtime import SafeModel
        from audit_agent_runtime.llm.claude_compatible import ClaudeCompatibleModelClient
        from audit_agent_runtime.llm.base import ModelMessage
        import httpx
        with httpx.Client() as http:
            model = ClaudeCompatibleModelClient("https://example.invalid", "test", "test", http_client=http)
            messages = [ModelMessage("tool", {"status": "ok", "data": {"text": "中文"}}, tool_call_id="test-call")]
            _, args = SafeModel(model)._arguments((), {"messages": messages, "tools": []})
            self.assertEqual(json.loads(args["messages"][0].content), messages[0].content)
            self.assertIsInstance(messages[0].content, dict)
            blocks = [ModelMessage("user", [{"type": "tool_result", "tool_use_id": "test-call", "content": {"ok": True}}])]
            _, args = SafeModel(model)._arguments((), {"messages": blocks})
            self.assertEqual(json.loads(args["messages"][0].content[0]["content"]), {"ok": True})
            self.assertIsInstance(blocks[0].content[0]["content"], dict)

    def test_memory_switch_blocks_tool_writes(self):
        with TestClient(create_app(self.config, ScriptedModel(tool="analysis_save"))) as client:
            self.login(client)
            response = client.post("/api/router/ask", json=self.body(remember=False))
            self.assertIn("memory_disabled", response.text)
            records = Service(self.config.data_dir).store.search()["records"]
            self.assertEqual(records, [])

    def test_no_model_is_explicit_and_no_demo_output(self):
        with TestClient(create_app(self.config)) as client:
            self.assertEqual(client.get("/api/sessions").status_code, 401)
            self.assertEqual(client.post("/api/login", json={"password": "wrong"}).status_code, 401)
            self.login(client)
            self.assertFalse(client.get("/api/status").json()["model_configured"])
            response = client.post("/api/router/ask", json=self.body())
            self.assertEqual(response.status_code, 503)
            self.assertEqual(client.get("/api/sessions").json(), [])

    def test_failure_is_cached_and_original_chat_survives(self):
        model = ScriptedModel(fail=True)
        with TestClient(create_app(self.config, model)) as client:
            self.login(client)
            response = client.post("/api/router/ask", json=self.body())
            self.assertTrue(any(e["type"] == "error" for e in self.events(response)))
            self.assertNotIn("provider-private-error", response.text)
            client.post("/api/router/ask", json=self.body())
            self.assertEqual(model.calls, 1)
            self.assertEqual(client.get("/api/sessions/test-session").json()[0]["text"], self.body()["text"])

    def test_restart_preserves_history_and_only_core_tables(self):
        app = create_app(self.config, ScriptedModel())
        with TestClient(app) as client:
            self.login(client)
            client.post("/api/router/ask", json=self.body())
            tables = inspect(app.state.services.engine).get_table_names()
            self.assertEqual(len(tables), 9)
            self.assertFalse(any("ontology" in table for table in tables))
        with TestClient(create_app(self.config)) as client:
            self.login(client)
            self.assertEqual(len(client.get("/api/sessions").json()), 1)
            self.assertEqual(client.get("/api/records?kind=plan").json()["records"][0]["kind"], "plan")

    def test_web_assets_and_reference_boundary(self):
        with TestClient(create_app(self.config)) as client:
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/assets/app.js").status_code, 200)
            self.assertEqual(client.get("/assets/secrets.env").status_code, 404)
            self.login(client)
            self.assertEqual(client.get("/api/charts/secrets.svg").status_code, 404)
            tool = client.app.state.services.tools.get("read_skill_reference")
            result = tool.execute({"skill": "../../", "path": "README.md"}, None)
            self.assertTrue(result["is_error"])


if __name__ == "__main__":
    unittest.main()
