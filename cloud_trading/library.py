"""Read-only presentation of personal records and their independent knowledge states."""
from collections import Counter
import json

from trading_agent.metadata import describe_record


GROUPS = {
    "practice": {"plan", "trade", "position", "review", "emotion", "observation"},
    "knowledge": {"note", "learning", "hypothesis", "evolution", "profile", "proposal", "strategy"},
    "research": {"research", "analysis"},
    "originals": {"raw", "source", "market_snapshot", "agent_run", "agent_result"},
}


def library(store, query="", group="all", kind="", state="", page=1, size=12):
    rows = store.snapshot(limit=None)["records"]
    counts = Counter(row["kind"] for row in rows)
    collections = {key: sum(counts[kind] for kind in kinds) for key, kinds in GROUPS.items()}
    collections["all"] = sum(collections[key] for key in ("practice", "knowledge", "research"))
    allowed = set().union(*(GROUPS[key] for key in ("practice", "knowledge", "research"))) if group == "all" else GROUPS[group]
    selected = []
    for row in rows:
        metadata = describe_record(row)
        if row["kind"] not in allowed or (kind and row["kind"] != kind):
            continue
        if query and query.casefold() not in json.dumps(row["body"], ensure_ascii=False).casefold():
            continue
        if state == "needs_review" and metadata["states"]["evidence"] != "needs_review":
            continue
        selected.append({**row, **metadata})
    selected.sort(key=lambda row: (row["updated_at"], row["id"]), reverse=True)
    start = (page - 1) * size
    return {"records": selected[start:start + size], "total": len(selected), "page": page, "size": size,
            "groups": collections, "kinds": dict(counts),
            "needs_review": sum(describe_record(row)["states"]["evidence"] == "needs_review" for row in rows),
            "scope": "分类阅读现有资料；待验证认识与已确认纪律分别呈现。"}
