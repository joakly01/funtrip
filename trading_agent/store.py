from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .common import DomainError, canonical, digest, now, stamp, finite, atomic_text
from .metadata import validate_metadata

KINDS = {"note", "trade", "plan", "emotion", "learning", "observation", "hypothesis", "review", "research", "position", "profile", "evolution"}
NATURES = {"user_report", "external_fact", "opinion", "hypothesis", "plan", "interpretation"}
HORIZONS = {"short", "medium_long", "unknown"}


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    @contextmanager
    def db(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.directory / "knowledge.sqlite", timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS objects(
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, version INTEGER NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'current');
        CREATE TABLE IF NOT EXISTS revisions(
          object_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
          recorded_at TEXT NOT NULL, reason TEXT, PRIMARY KEY(object_id,version));
        CREATE TABLE IF NOT EXISTS links(
          parent TEXT NOT NULL REFERENCES objects(id), child TEXT NOT NULL REFERENCES objects(id),
          PRIMARY KEY(parent,child));
        CREATE TABLE IF NOT EXISTS operations(
          key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS kinds ON objects(kind,updated_at);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _read(self, con, object_id):
        row = con.execute("SELECT * FROM objects WHERE id=?", (object_id,)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", f"记录不存在：{object_id}")
        return {**dict(row), "body": json.loads(row["body"])}

    def read(self, object_id, version=None):
        with self.db() as con:
            item = self._read(con, object_id)
            item["reference_ids"] = [r[0] for r in con.execute("SELECT parent FROM links WHERE child=? ORDER BY parent", (object_id,))]
            item["dependent_ids"] = [r[0] for r in con.execute("SELECT child FROM links WHERE parent=? ORDER BY child", (object_id,))]
            item["reference_scope"] = "关联边为当前可追溯关系；历史版本的正文见 history，ID 前缀不代表当前分类"
            item["history"] = [dict(r) | {"body": json.loads(r["body"])} for r in con.execute(
                "SELECT * FROM revisions WHERE object_id=? ORDER BY version", (object_id,))]
            if version is not None:
                rev = next((r for r in item["history"] if r["version"] == version), None)
                if rev is None:
                    raise DomainError("NOT_FOUND", "该版本不存在")
                item["body"], item["version"] = rev["body"], version
                item["status"] = "historical"
            return item

    def _put(self, con, kind, body, refs=(), status="current"):
        for ref in refs:
            self._read(con, ref)
        oid = kind + "_" + uuid.uuid4().hex[:16]
        ts = now()
        con.execute("INSERT INTO objects VALUES(?,?,1,?,?,?,?)", (oid, kind, ts, ts, canonical(body), status))
        con.execute("INSERT INTO revisions VALUES(?,1,?,?,?)", (oid, canonical(body), ts, "created"))
        con.executemany("INSERT OR IGNORE INTO links VALUES(?,?)", [(r, oid) for r in refs])
        return self._read(con, oid)

    def _once(self, con, key, payload, action):
        if not isinstance(key, str) or not key or len(key) > 250:
            raise DomainError("INVALID_KEY", "需要稳定且非空的幂等键")
        fp = digest(payload)
        row = con.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
        if row:
            if row["fingerprint"] != fp:
                raise DomainError("IDEMPOTENCY_CONFLICT", "相同幂等键对应了不同内容；请使用更正操作")
            return json.loads(row["result"])
        result = action()
        con.execute("INSERT INTO operations VALUES(?,?,?)", (key, fp, canonical(result)))
        return result

    def capture(self, message_id, text, persist=True):
        if not isinstance(text, str) or not text.strip():
            raise DomainError("EMPTY_INPUT", "原文不能为空")
        if not persist:
            return {"saved": False, "reason": "本次不进入长期知识库", "source_id": None}
        with self.db() as con:
            def action():
                raw = self._put(con, "raw", {"message_id": message_id, "text": text})
                return {"saved": True, "source_id": raw["id"], "recorded_at": raw["created_at"]}
            return self._once(con, "capture:" + message_id, {"text": text}, action)

    def _validate_record(self, item):
        allowed = {"item_key", "kind", "text", "excerpt", "nature", "tags", "security_ids", "horizon", "event_at", "fields", "refs", "layers", "states"}
        if set(item) - allowed:
            raise DomainError("INVALID_RECORD", "记录含未定义字段")
        if item.get("kind") not in KINDS or item.get("nature") not in NATURES:
            raise DomainError("INVALID_RECORD", "需要合法的记录类型和信息性质")
        if not item.get("text") or not isinstance(item.get("text"), str):
            raise DomainError("INVALID_RECORD", "记录正文不能为空")
        if item.get("horizon", "unknown") not in HORIZONS:
            raise DomainError("INVALID_RECORD", "交易周期无效")
        for key in ("tags", "security_ids", "refs"):
            if key in item and (not isinstance(item[key], list) or any(not isinstance(x, str) for x in item[key])):
                raise DomainError("INVALID_RECORD", f"{key} 必须是字符串数组")
        if item.get("event_at") is not None:
            stamp(item["event_at"])
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            raise DomainError("INVALID_RECORD", "fields 必须是对象")
        validate_metadata(item)
        if item["kind"] == "trade":
            if item["nature"] != "user_report" or fields.get("execution_status") != "reported_executed":
                raise DomainError("NOT_EXECUTED", "真实交易记录必须明确为用户陈述的已成交；计划请使用 plan")
            if fields.get("side") not in {"buy", "sell", "unknown"}:
                raise DomainError("INVALID_RECORD", "成交方向必须为 buy/sell/unknown")
            for key in ("price", "quantity"):
                if fields.get(key) is not None:
                    finite(fields[key], key, positive=True)

    def commit(self, source_id, items, idempotency_key):
        if not items or not isinstance(items, list):
            raise DomainError("INVALID_RECORD", "需要至少一个分类条目")
        with self.db() as con:
            source = self._read(con, source_id)
            if source["kind"] != "raw":
                raise DomainError("INVALID_SOURCE", "分类归集需要原始用户输入")
            for item in items:
                self._validate_record(item)
                if not item.get("excerpt") or item["excerpt"] not in source["body"]["text"]:
                    raise DomainError("SOURCE_MISMATCH", "每个条目需要原文中真实存在的 excerpt")
                if not item.get("item_key"):
                    raise DomainError("INVALID_KEY", "每个分类条目需稳定的 item_key")
            if len({x["item_key"] for x in items}) != len(items):
                raise DomainError("DUPLICATE_ITEM", "同一批次 item_key 重复")
            def action():
                results = []
                for item in items:
                    key = f"item:{source_id}:{item['item_key']}"
                    def create(item=item):
                        body = {"source_id": source_id, "horizon": "unknown", "tags": [], "security_ids": [], **item}
                        return self._put(con, item["kind"], body, [source_id] + item.get("refs", []))
                    results.append(self._once(con, key, item, create))
                return {"records": results, "saved": True}
            return self._once(con, "commit:" + idempotency_key, {"source": source_id, "items": items}, action)

    def search(self, query="", kind=None, security_id=None, limit=20, known_before=None):
        if not 1 <= limit <= 100:
            raise DomainError("INVALID_LIMIT", "limit 应为 1 到 100")
        cutoff = stamp(known_before) if known_before else None
        # Search versioned originals, not a future revision of an old row.
        with self.db() as con:
            rows = con.execute("SELECT * FROM objects ORDER BY updated_at DESC").fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if kind and r["kind"] != kind:
                    continue
                if cutoff and stamp(r["created_at"]) > cutoff:
                    continue
                if cutoff:
                    revs = con.execute("SELECT * FROM revisions WHERE object_id=? ORDER BY version DESC", (r["id"],)).fetchall()
                    rev = next((v for v in revs if stamp(v["recorded_at"]) <= cutoff), None)
                    if not rev:
                        continue
                    r.update(body=rev["body"], version=rev["version"], status="historical")
                body = json.loads(r["body"])
                if query and query.casefold() not in canonical(body).casefold():
                    continue
                if security_id and security_id not in body.get("security_ids", []):
                    continue
                r["body"] = body
                results.append(r)
                if len(results) == limit:
                    break
            return {"records": results, "search": "本地逐条全文及元数据匹配；未检索到不代表从未发生", "limit": limit}

    def amend(self, object_id, patch, reason, source_id, expected_version, idempotency_key):
        with self.db() as con:
            source = self._read(con, source_id)
            if source["kind"] != "raw":
                raise DomainError("INVALID_SOURCE", "更正须关联实际用户原文")
            def action():
                old = self._read(con, object_id)
                if old["kind"] not in KINDS:
                    raise DomainError("IMMUTABLE", "原文、行情快照、规则及分析不能通过记录更正直接覆盖")
                if old["version"] != expected_version:
                    raise DomainError("VERSION_CONFLICT", "记录已变化，请读取当前版本")
                allowed = {"text", "kind", "nature", "tags", "security_ids", "horizon", "event_at", "fields", "layers", "states"}
                if not patch or set(patch) - allowed:
                    raise DomainError("INVALID_PATCH", "更正字段无效")
                body = {**old["body"], **patch}
                if "fields" in patch:
                    body["fields"] = {**old["body"].get("fields", {}), **patch["fields"]}
                body["kind"] = patch.get("kind", old["kind"])
                self._validate_record({k: v for k, v in body.items() if k != "source_id"})
                version, ts = expected_version + 1, now()
                con.execute("UPDATE objects SET kind=?,version=?,updated_at=?,body=?,status='current' WHERE id=?",
                            (body["kind"], version, ts, canonical(body), object_id))
                con.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (object_id, version, canonical(body), ts, reason))
                con.execute("INSERT OR IGNORE INTO links VALUES(?,?)", (source_id, object_id))
                descendants = [r[0] for r in con.execute("""WITH RECURSIVE depend(id) AS (
                  SELECT child FROM links WHERE parent=? UNION SELECT links.child FROM links JOIN depend ON links.parent=depend.id)
                  SELECT id FROM depend WHERE id != ?""", (object_id, object_id))]
                for oid in descendants:
                    con.execute("UPDATE objects SET status='needs_recheck' WHERE id=?", (oid,))
                return {"record": self._read(con, object_id), "needs_recheck": descendants}
            return self._once(con, "amend:" + idempotency_key, [object_id, patch, reason, source_id, expected_version], action)

    def save_artifact(self, kind, body, refs, key):
        with self.db() as con:
            return self._once(con, kind + ":" + key, [body, refs], lambda: self._put(con, kind, body, refs))

    def snapshot(self, query="", kind=None, limit=100, known_before=None):
        """Read existing records without initialization, DDL or a write transaction."""
        path = self.directory / "knowledge.sqlite"
        if not path.exists():
            return {"records": [], "scope": "尚无持久资料库", "truncated": False}
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise DomainError("INVALID_LIMIT", "读取数量无效")
        cutoff = stamp(known_before) if known_before else None
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            records = []
            for row in con.execute("SELECT * FROM objects ORDER BY created_at DESC,id DESC"):
                item = dict(row)
                if cutoff:
                    if stamp(item["created_at"]) > cutoff:
                        continue
                    rev = next((r for r in con.execute("SELECT * FROM revisions WHERE object_id=? ORDER BY version DESC", (item["id"],))
                                if stamp(r["recorded_at"]) <= cutoff), None)
                    if rev is None:
                        continue
                    item.update(body=rev["body"], version=rev["version"], status="historical")
                item["body"] = json.loads(item["body"])
                item["kind"] = item["body"].get("kind", item["kind"])
                if kind and item["kind"] != kind:
                    continue
                if query and query.casefold() not in canonical(item["body"]).casefold():
                    continue
                records.append(item)
            truncated = limit is not None and len(records) > limit
            return {"records": records if limit is None else records[:limit],
                    "scope": "只读现有对象与版本；缺库不创建", "truncated": truncated}

    def active_snapshot(self):
        for row in self.snapshot(kind="strategy", limit=None)["records"]:
            if stamp(row["body"]["effective_at"]) <= stamp(now()):
                return row
        return {"id": None, "body": {"rules": []}, "status": "not_defined"}

    def cached_run(self, key, request):
        path = self.directory / "knowledge.sqlite"
        if not path.exists():
            return None
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM operations WHERE key=?", ("run:" + key,)).fetchone()
            if row is None:
                return None
            if row["fingerprint"] != digest(request):
                raise DomainError("IDEMPOTENCY_CONFLICT", "相同任务键对应了不同请求")
            result = json.loads(row["result"])
            return self._read(con, result["id"])

    def save_run(self, key, request, body, refs):
        with self.db() as con:
            return self._once(con, "run:" + key, request, lambda: self._put(con, "agent_run", body, refs))

    def strategy_get(self, effective_at=None):
        when = stamp(effective_at) if effective_at else stamp(now())
        with self.db() as con:
            rows = con.execute("SELECT * FROM objects WHERE kind='strategy' ORDER BY created_at DESC,id DESC").fetchall()
            for row in rows:
                body = json.loads(row["body"])
                if stamp(body["effective_at"]) <= when:
                    return self._read(con, row["id"])
            return {"id": None, "version": 0, "body": {"rules": [], "effective_at": None}, "status": "not_defined"}

    def propose(self, rules, rationale, refs, key):
        if not isinstance(rules, list) or any(not isinstance(r, dict) or not r.get("id") or not r.get("text") for r in rules):
            raise DomainError("INVALID_RULE", "每条规则需 id 和 text")
        if len({r["id"] for r in rules}) != len(rules):
            raise DomainError("INVALID_RULE", "规则 id 不得重复")
        current = self.strategy_get()
        payload = {"base_id": current["id"], "rules": rules, "rationale": rationale}
        payload["digest"] = digest(payload)
        return self.save_artifact("proposal", payload, refs, key)

    def activate(self, proposal_id, proposal_digest, confirmation_source_id):
        # Trusted owner confirmation only (CLI or authenticated web route), never a model tool.
        with self.db() as con:
            proposal = self._read(con, proposal_id)
            confirmation = self._read(con, confirmation_source_id)
            if proposal["kind"] != "proposal" or proposal["body"]["digest"] != proposal_digest:
                raise DomainError("PROPOSAL_CHANGED", "确认内容与提案指纹不一致")
            expected_confirmation = f"确认 {proposal_id} {proposal_digest}"
            if confirmation["kind"] != "raw" or confirmation["body"]["text"].strip() != expected_confirmation:
                raise DomainError("CONFIRMATION_REQUIRED", "需要严格匹配的用户确认原文：确认 <提案编号> <完整指纹>；否定、引文或附条件表达不能激活")
            row = con.execute("SELECT value FROM settings WHERE key='active_strategy'").fetchone()
            if (row[0] if row else None) != proposal["body"]["base_id"]:
                raise DomainError("VERSION_CONFLICT", "当前体系已变化，需重新比较提案")
            body = {"rules": proposal["body"]["rules"], "proposal_id": proposal_id, "confirmation_source_id": confirmation_source_id,
                    "effective_at": now(), "previous_id": proposal["body"]["base_id"]}
            obj = self._put(con, "strategy", body, [proposal_id, confirmation_source_id])
            con.execute("INSERT OR REPLACE INTO settings VALUES('active_strategy',?)", (obj["id"],))
            return obj

    def status(self):
        with self.db() as con:
            counts = {r[0]: r[1] for r in con.execute("SELECT kind,count(*) FROM objects GROUP BY kind")}
        return {"database": str(self.directory / "knowledge.sqlite"), "counts": counts, "monitoring": "未启用常驻监控", "active_strategy": self.strategy_get()["id"]}

    def export(self):
        from .views import SystemViews
        return SystemViews(self).render()

    def backup(self):
        import datetime
        target = self.directory / "backups" / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite")
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.directory / "knowledge.sqlite") as src, sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DomainError("BACKUP_FAILED", "备份完整性检查失败")
        return {"path": str(target), "integrity": "ok", "scope": "所有原文、对象与快照均在数据库中；导出可重建"}
