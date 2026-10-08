"""Observed feedback, adopted edits and evidence feed the next task's context."""
from .growth import GrowthWorkspace, FIELDS
from trading_agent.common import DomainError


class CompanionMemory:
    def __init__(self, store):
        self.store = store

    def feedback(self, target_id, session_id, rating, note, key):
        if rating not in {"helpful", "adjust"} or len(note) > 2000 or (rating == "adjust" and not note.strip()):
            raise DomainError("INVALID_RECORD", "请说明希望调整的地方")
        refs = []
        if target_id:
            row = GrowthWorkspace(self.store).get(target_id)
            if row["kind"] not in FIELDS:
                raise DomainError("INVALID_RECORD", "请选择当前学习内容")
            refs.append(target_id)
        return self.store.save_artifact("companion_feedback", {"target_id": target_id, "session_id": session_id,
            "rating": rating, "note": note.strip()}, refs, "feedback:" + key)

    def preference(self, style, focus, key):
        if style not in {"balanced", "concise", "detailed"} or len(focus) > 2000:
            raise DomainError("INVALID_RECORD", "请选择回答方式，并简短写下关注点")
        return self.store.save_artifact("companion_preference", {"style": style, "focus": focus.strip()}, [], "preference:" + key)

    def overview(self):
        rows = self.store.snapshot(limit=None)["records"]
        titles = {r["id"]: r["body"].get("title", "") for r in rows}
        preference = next((r["body"] for r in rows if r["kind"] == "companion_preference"), {"style": "balanced", "focus": ""})
        history = []
        revisit = []
        for r in rows:
            b = r["body"]
            if r["kind"] == "companion_feedback":
                history.append({"id": r["id"], "date": r["created_at"], "target_id": b.get("target_id"),
                    "title": "你希望怎样调整" if b["rating"] == "adjust" else "这次协助对你有帮助", "text": b["note"] or "已保留这次反馈，供后续同类任务参考。"})
            elif r["kind"] == "growth_suggestion" and b.get("decision") == "accept":
                history.append({"id": r["id"], "date": b.get("decided_at", r["updated_at"]), "target_id": b["target_id"],
                    "title": "你采用了一次整理", "text": titles.get(b["target_id"]) or "原有版本仍可回查"})
            elif r["kind"] == "practice" and b.get("phase") == "reviewed":
                history.append({"id": r["id"], "date": b.get("reviewed_at", r["updated_at"]), "target_id": r["id"],
                    "title": "从实践里留下经验", "text": b.get("lesson") or b.get("review", "")})
                if b.get("evidence") == "counter" and b.get("method_id"):
                    revisit.append({"id": b["method_id"], "title": titles.get(b["method_id"]) or "相关方法", "reason": "关联复盘出现反例，值得检查适用条件。"})
        versions = {x["id"]: x["version"] for x in rows}
        for r in rows:
            if r["kind"] in {"insight", "method"}:
                # Compare body pins without a write/read transaction for every object.
                if any(versions.get(s["id"]) != s["version"] for s in r["body"].get("sources", [])):
                    revisit.append({"id": r["id"], "title": r["body"]["title"], "reason": "依据已有新版本，可以回看当前理解。"})
        history.sort(key=lambda r: r["date"], reverse=True)
        return {"preference": preference, "history": history[:30], "revisit": list({r["id"]: r for r in revisit}.values())[:8],
                "summary": "你的反馈、采用过的整理和实践结果，会带入下一次协助。"}

    def context(self, target_id=None):
        rows = self.store.snapshot(limit=None)["records"]
        page = self.overview()
        relevant = {target_id} if target_id else set()
        target = next((r for r in rows if r["id"] == target_id), None)
        if target:
            growth = [r for r in rows if r["kind"] in FIELDS]
            growth_ids = {r["id"] for r in growth}
            # Traverse actual growth links, including standalone notes -> methods -> cases.
            # A fixed point avoids creation-order dependence and safely terminates on cycles.
            while True:
                previous = set(relevant)
                for r in growth:
                    parents = {r["body"].get("topic_id"), r["body"].get("method_id"),
                               *(s["id"] for s in r["body"].get("sources", []))} & growth_ids
                    if r["id"] in relevant:
                        relevant.update(parents)
                    if parents & relevant:
                        relevant.add(r["id"])
                if relevant == previous:
                    break
        feedback = [r["body"] for r in rows if r["kind"] == "companion_feedback"
                    and (not r["body"].get("target_id") or r["body"].get("target_id") in relevant)][:6]
        cases = [{"id": r["id"], "title": r["body"]["title"], "evidence": r["body"].get("evidence"),
                  "lesson": r["body"].get("lesson"), "review": r["body"].get("review", "")[:1500]}
                 for r in rows if r["kind"] == "practice" and r["body"].get("phase") == "reviewed" and r["id"] in relevant][:6]
        adopted = [{"target_id": r["body"]["target_id"], "patch": {k: v[:1500] for k, v in list(r["body"]["patch"].items())[:6]}}
                   for r in rows if r["kind"] == "growth_suggestion" and r["body"].get("decision") == "accept"
                   and r["body"].get("target_id") in relevant][:4]
        return {"owner_preferences": page["preference"], "feedback": feedback, "reviewed_cases": cases,
                "adopted_edits": adopted, "revisit": [r for r in page["revisit"] if r["id"] in relevant]}
