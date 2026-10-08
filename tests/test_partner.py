import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from trading_agent.service import Service

class PartnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "personal"
        self.service = Service(self.directory)

    def call(self, name, args):
        result = self.service.call(name, args)
        self.assertEqual(result["status"], "ok", result)
        return result["data"]

    def process(self, **kw):
        args = {"message_id": "m1", "text": "底部还没有确认，准备观察", "intents": ["record", "plan"], "key": "m1:1"}
        args.update(kw)
        return self.call("partner_process", args)

    def item(self, **kw):
        item = {"item_key": "plan", "kind": "plan", "text": "观察底部条件", "excerpt": "准备观察",
                "nature": "plan", "layers": ["fa", "shu", "qi"]}
        item.update(kw)
        return item

    def test_mixed_input_saved_once_and_layer_tasks_are_real(self):
        args = {"intents": ["record", "learn", "plan"], "items": [self.item()], "classic_query": "风险"}
        first = self.process(**args)
        again = self.process(**args)
        self.assertEqual(first, again)
        self.assertEqual({t["id"] for t in first["tasks"]}, {"dao", "fa", "shu", "qi"})
        self.assertTrue(all(t["instruction"] for t in first["tasks"]))
        self.assertEqual(self.service.store.status()["counts"]["raw"], 1)
        self.assertEqual(len(self.service.store.search(kind="plan")["records"]), 1)
        self.assertTrue(first["classics"]["entries"])

    def test_ephemeral_request_does_not_initialize_a_database(self):
        result = self.process(persist=False, classic_query="能力圈")
        self.assertIsNone(result["run_id"])
        self.assertFalse(self.directory.exists())
        self.assertEqual(result["classics"]["entries"][0]["id"], "K05")

    def test_development_is_not_a_trading_record(self):
        result = self.process(intents=["development"])
        self.assertFalse(result["saved"])
        self.assertFalse(self.directory.exists())

    def test_classification_failure_keeps_original_and_reports_partial(self):
        result = self.process(items=[self.item(excerpt="不存在")])
        self.assertEqual(result["phase"], "partial")
        self.assertEqual(result["record_ids"], [])
        self.assertEqual(self.service.store.read(result["source_id"])["body"]["text"], "底部还没有确认，准备观察")

    def test_partial_layer_results_never_claim_all_layers_completed(self):
        run = self.process()
        result = self.call("partner_finalize", {"run_id": run["run_id"], "results": [
            {"layer": "qi", "status": "completed", "summary": "原文已保存", "evidence_refs": [run["source_id"]]}],
            "text": "其余层尚未处理", "key": "result1"})
        self.assertEqual(result["body"]["phase"], "partial")
        self.assertEqual(set(result["body"]["missing_layers"]), {"fa", "shu"})
        self.assertIsNone(self.service.store.active_snapshot()["id"])

    def test_learning_links_actual_excerpt_and_keeps_independent_states(self):
        source = self.service.store.capture("learn1", "能力圈这句话我还有疑问")
        result = self.call("learning_link", {"source_id": source["source_id"], "classic_id": "K05",
            "excerpt": "还有疑问", "understanding": "希望进一步理解能力边界", "key": "learn1",
            "adoption": "questioned"})
        record = result["records"][0]
        self.assertEqual(record["body"]["states"]["adoption"], "questioned")
        self.assertEqual(record["body"]["states"]["evidence"], "unverified")
        self.assertIsNone(self.service.store.active_snapshot()["id"])

    def test_false_rule_activation_metadata_is_rejected(self):
        item = self.item(states={"rule": "active"})
        result = self.process(items=[item])
        self.assertEqual(result["phase"], "partial")
        self.assertEqual(result["issues"][0]["code"], "INVALID_STATES")

    def test_publisher_description_stays_a_limited_source(self):
        result = self.call("classic_search", {"query": "概率"})
        card = result["entries"][0]
        self.assertEqual(card["id"], "K12")
        self.assertEqual(card["source_status"], "publisher_description")
        self.assertEqual(card["personal_effect"], "unverified")

    def test_view_reads_and_export_do_not_change_database_bytes(self):
        self.process(items=[self.item()])
        db = self.directory / "knowledge.sqlite"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        overview = self.call("system_overview", {})
        self.assertTrue(overview["layers"]["fa"])
        output = self.call("system_render", {})
        self.assertEqual(set(output["views"]), {"SYSTEM.md", "DAO.md", "FA.md", "SHU.md", "QI.md", "PROFILE.md", "EVOLUTION.md", "INDEX.md"})
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

    def test_manual_view_edit_is_preserved(self):
        self.process()
        self.call("system_render", {})
        path = self.directory / "exports/SYSTEM.md"
        path.write_text("我手工补充的内容")
        result = self.service.call("system_render", {})
        self.assertEqual(result["error"]["code"], "VIEW_EDIT_CONFLICT")
        self.assertEqual(path.read_text(), "我手工补充的内容")

    def test_old_records_without_new_metadata_remain_readable(self):
        raw = self.service.store.capture("old", "旧学习笔记")
        self.service.store.commit(raw["source_id"], [{"item_key": "old", "kind": "learning", "nature": "user_report",
            "text": "旧学习笔记", "excerpt": "旧学习笔记"}], "old")
        rows = self.call("system_overview", {})["layers"]["dao"]
        self.assertEqual(rows[0]["layer_assignment"], "derived_from_kind")
        self.assertEqual(rows[0]["states"]["adoption"], "pending")

    def test_snapshot_historical_classification_is_not_future_kind(self):
        raw = self.service.store.capture("history", "准备观察")
        record = self.service.store.commit(raw["source_id"], [self.item()], "history")["records"][0]
        cutoff = record["created_at"]
        correction = self.service.store.capture("fix", "这是学习案例")
        self.service.store.amend(record["id"], {"kind": "learning", "nature": "user_report"}, "分类更正",
                                 correction["source_id"], 1, "fix")
        past = self.service.store.snapshot(kind="plan", known_before=cutoff)["records"]
        self.assertEqual(past[0]["id"], record["id"])
        self.assertEqual(past[0]["version"], 1)

    def test_export_does_not_truncate_after_100_objects(self):
        text = " ".join(f"案例{i}" for i in range(105))
        raw = self.service.store.capture("many", text)
        items = [{"item_key": str(i), "kind": "note", "nature": "user_report", "text": f"案例{i}", "excerpt": f"案例{i}"} for i in range(105)]
        self.service.store.commit(raw["source_id"], items, "many")
        output = self.call("system_render", {})
        self.assertEqual(len(self.call("system_overview", {})["layers"]["dao"]), 105)
        self.assertIn("案例104", Path(output["views"]["DAO.md"]).read_text())

    def test_task_depending_on_corrected_memory_requires_recheck(self):
        raw = self.service.store.capture("prior", "旧认识")
        record = self.service.store.commit(raw["source_id"], [{"item_key": "prior", "kind": "note", "nature": "user_report",
            "text": "旧认识", "excerpt": "旧认识"}], "prior")["records"][0]
        run = self.process(memory_query="旧认识")
        correction = self.service.store.capture("correction", "修正旧认识")
        self.service.store.amend(record["id"], {"text": "修正后的认识"}, "更正", correction["source_id"], 1, "correction")
        result = self.service.call("partner_finalize", {"run_id": run["run_id"], "results": [], "text": "旧任务结果", "key": "stale"})
        self.assertEqual(result["error"]["code"], "RUN_STALE")

if __name__ == "__main__":
    unittest.main()
