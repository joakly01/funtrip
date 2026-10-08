from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from trading_agent.common import DomainError
from trading_agent.catalog import ClassicCatalog
from .companion import CompanionMemory
from .curation import KnowledgeCurator, bullets


class Feedback(BaseModel):
    target_id: str | None = None
    session_id: str = Field(max_length=100)
    rating: str
    note: str = Field(default="", max_length=2000)
    key: str = Field(min_length=1, max_length=150)


class Preferences(BaseModel):
    style: str
    focus: str = Field(default="", max_length=2000)
    key: str = Field(min_length=1, max_length=150)


def install_companion_routes(app, owner, store, capabilities):
    memory = CompanionMemory(store)
    router = APIRouter(prefix="/api/companion", dependencies=[Depends(owner)])

    def perform(fn, *args):
        try:
            return fn(*args)
        except DomainError as e:
            raise HTTPException(404 if e.code == "NOT_FOUND" else 400, e.message) from None

    @router.get("")
    def overview():
        catalog = ClassicCatalog()
        entries = catalog.entries()
        # Public catalog excerpts only; the source type remains visible.
        item = catalog.get(entries[date.today().toordinal() % len(entries)]["id"]) if entries else None
        if item:
            parts = bullets(item["content"])
            item = {k: item[k] for k in ("id", "title", "source_status") } | {
                "excerpt": parts.get("原文") or parts.get("原文短摘") or parts.get("作者观点归纳") or parts.get("本项目推导") or parts.get("语境") or parts.get("内容性质", ""),
                "is_quote": "原文" in parts or "原文短摘" in parts,
                "boundary": parts.get("边界", "结合原文语境理解。")}
        return {**memory.overview(), "capabilities": capabilities.public(), "featured": item,
                "curated": sum(r["kind"] in {"topic", "insight", "method", "handbook"} and bool(r["body"].get("origin")) for r in store.snapshot(limit=None)["records"])}

    @router.post("/curate")
    def curate():
        return perform(KnowledgeCurator(store).run)

    @router.post("/feedback")
    def feedback(body: Feedback):
        return perform(memory.feedback, body.target_id, body.session_id, body.rating, body.note, body.key)

    @router.post("/preferences")
    def preferences(body: Preferences):
        return perform(memory.preference, body.style, body.focus, body.key)

    @router.get("/collaborations")
    def collaborations(session_id: str = Query(max_length=100)):
        return {"runs": [r for r in store.snapshot(kind="collaboration", limit=None)["records"]
                         if r["body"].get("session_id") == session_id][:20]}

    app.include_router(router)
    return memory
