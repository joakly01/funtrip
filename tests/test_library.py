import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from cloud_trading.app import create_app
from cloud_trading.config import Config
from cloud_trading.library import library
from trading_agent.store import Store


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.store = Store(self.path / "trading")

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        with self.store.db() as db:
            source = self.store._put(db, "raw", {"text": "真实原话"})
            knowledge = self.store._put(db, "hypothesis", {"text": "风险候选", "source_id": source["id"]}, [source["id"]])
            self.store._put(db, "learning", {"text": "学习风险", "states": {"adoption": "agreed"}}, status="needs_recheck")
            self.store._put(db, "plan", {"text": "风险计划"})
            self.store._put(db, "source", {"title": "外部来源", "text": "风险背景"})
            self.store._put(db, "agent_run", {"text": "运行记录"})
        return knowledge

    def test_collection_pagination_states_and_originals_are_read_only(self):
        row = self.seed()
        before = (self.store.directory / "knowledge.sqlite").read_bytes()
        first = library(self.store, size=2)
        second = library(self.store, page=2, size=2)
        self.assertEqual(first["total"], 3)
        self.assertFalse({r["id"] for r in first["records"]} & {r["id"] for r in second["records"]})
        self.assertEqual(first["groups"]["originals"], 3)
        candidate = next(r for r in first["records"] + second["records"] if r["id"] == row["id"])
        self.assertEqual(candidate["states"]["adoption"], "pending")
        self.assertEqual(candidate["states"]["evidence"], "unverified")
        self.assertEqual(candidate["states"]["rule"], "not_applicable")
        reviewed = library(self.store, state="needs_review")
        self.assertEqual(reviewed["total"], 1)
        self.assertEqual(reviewed["records"][0]["states"]["adoption"], "agreed")
        self.assertEqual(library(self.store, group="knowledge", query="候选")["total"], 1)
        self.assertEqual(library(self.store, group="originals")["total"], 3)
        self.assertEqual(library(self.store, kind="plan")["total"], 1)
        self.assertEqual((self.store.directory / "knowledge.sqlite").read_bytes(), before)

    def test_library_and_all_classics_require_login_and_validate_filters(self):
        self.seed()
        config = Config(self.path / "trading", self.path / "runtime", "test-password")
        with TestClient(create_app(config)) as client:
            self.assertEqual(client.get("/api/library").status_code, 401)
            self.assertEqual(client.get("/api/classics").status_code, 401)
            client.post("/api/login", json={"password": "test-password"})
            self.assertEqual(client.get("/api/library?group=wrong").status_code, 400)
            self.assertEqual(client.get("/api/library?page=0").status_code, 422)
            self.assertEqual(client.get("/api/library?size=100").status_code, 422)
            self.assertEqual(client.get("/api/library").json()["total"], 3)
            classics = client.get("/api/classics").json()
            self.assertEqual(len(classics["entries"]), classics["catalog_size"])
            self.assertGreater(len(classics["entries"]), 14)
            self.assertTrue(all("path" not in entry for entry in classics["entries"]))
            searched = client.get("/api/classics?query=K24").json()
            self.assertEqual([e["id"] for e in searched["entries"]], ["K24"])

    def test_empty_library_does_not_create_a_business_database(self):
        self.assertEqual(library(self.store)["total"], 0)
        self.assertFalse((self.store.directory / "knowledge.sqlite").exists())
