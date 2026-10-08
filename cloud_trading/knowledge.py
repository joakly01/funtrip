"""Knowledge shelves are reading preferences, never evidence or trading rules."""
from collections import Counter
import json
import sqlite3

from trading_agent.common import DomainError, now
from trading_agent.metadata import describe_record

SHELVES = {"outcomes": "整理成果", "workbench": "待打磨", "references": "资料档案"}
TOPICS = {"general": "未分类", "principles": "交易认知", "risk": "风险与仓位",
          "execution": "交易执行", "review": "复盘方法", "market": "市场研究", "mindset": "心态与习惯"}
ARCHIVES = {"raw", "source", "market_snapshot", "agent_run", "agent_result", "trade", "position",
            "plan", "observation", "emotion", "profile", "strategy"}
OUTCOMES = {"analysis", "review", "evolution"}


def default_shelf(row):
    if row["kind"] in ARCHIVES:
        return "references"
    if row["kind"] in OUTCOMES or (row["kind"] == "learning" and row["body"].get("understanding")):
        return "outcomes"
    return "workbench"


class KnowledgeLibrary:
    def __init__(self, store, runtime_dir):
        self.store = store
        self.path = runtime_dir / "library.sqlite"

    def preferences(self):
        if not self.path.exists():
            return {}
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return {r["record_id"]: dict(r) for r in db.execute("SELECT * FROM library_items")}

    def describe(self, row, preferences=None):
        preferences = self.preferences() if preferences is None else preferences
        saved = preferences.get(row["id"], {})
        return {"shelf": saved.get("shelf", default_shelf(row)), "topic": saved.get("topic", "general"),
                "starred": bool(saved.get("starred", False)), "organized": bool(saved),
                "archive": row["kind"] in ARCHIVES}

    def rows(self):
        preferences = self.preferences()
        return [{**r, **describe_record(r), "collection": self.describe(r, preferences)}
                for r in self.store.snapshot(limit=None)["records"]
                if r["kind"] not in {"growth_source", "growth_suggestion", "proposal_decision", "collaboration",
                                     "companion_feedback", "companion_preference", "curation_batch"}]

    def browse(self, query="", shelf="outcomes", topic="", kind="", state="", starred=False, page=1, size=12):
        rows = self.rows()
        counts = Counter(r["collection"]["shelf"] for r in rows)
        matches = [r for r in rows if (shelf == "all" or r["collection"]["shelf"] == shelf)
                   and (not query or query.casefold() in json.dumps(r["body"], ensure_ascii=False).casefold())
                   and (not topic or r["collection"]["topic"] == topic)
                   and (not starred or r["collection"]["starred"])
                   and (state != "needs_review" or r["states"]["evidence"] == "needs_review")]
        kinds = Counter(r["kind"] for r in matches)
        matches = [r for r in matches if not kind or r["kind"] == kind]
        matches.sort(key=lambda r: (r["collection"]["starred"], r["updated_at"], r["id"]), reverse=True)
        start = (page - 1) * size
        return {"records": matches[start:start + size], "total": len(matches), "page": page, "size": size,
                "shelves": {k: counts[k] for k in SHELVES}, "kinds": dict(kinds),
                "topics": TOPICS, "starred": sum(r["collection"]["starred"] for r in rows),
                "needs_review": sum(r["states"]["evidence"] == "needs_review" for r in rows),
                "scope": "整理成果表示可阅读的输出；不表示观点已验证、个人已认同或纪律已生效。"}

    def organize(self, record_id, shelf, topic, starred):
        # Read before writing; never initialize or modify a business database here.
        row = next((r for r in self.store.snapshot(limit=None)["records"] if r["id"] == record_id), None)
        if row is None:
            raise DomainError("NOT_FOUND", "资料不存在")
        if shelf not in SHELVES or topic not in TOPICS:
            raise DomainError("INVALID_COLLECTION", "请选择已有的资料层级和主题")
        if row["kind"] in ARCHIVES and shelf != "references":
            raise DomainError("ORIGINAL_ARCHIVE", "原始依据保留在资料档案；可在对话中另行整理成果")
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS library_items(record_id TEXT PRIMARY KEY, shelf TEXT NOT NULL, "
                       "topic TEXT NOT NULL, starred INTEGER NOT NULL, updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO library_items VALUES(?,?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET "
                       "shelf=excluded.shelf,topic=excluded.topic,starred=excluded.starred,updated_at=excluded.updated_at",
                       (record_id, shelf, topic, int(starred), now()))
        return self.describe(row)
