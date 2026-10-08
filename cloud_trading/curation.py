"""Idempotent editorial shelves from existing records and the verified seed catalog.

Writes new, clearly attributed drafts; never changes historical facts or rules.
"""
import re
from .growth import GrowthWorkspace
from trading_agent.catalog import ClassicCatalog
from trading_agent.common import digest


def bullets(text):
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^- \*\*([^*]+)\*\*[:：]?\s*(.*)$", text, re.M)}


def excerpt(text, limit=500):
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M).strip()
    return text[:limit] + ("\n\n完整内容见来源。" if len(text) > limit else "")


class KnowledgeCurator:
    def __init__(self, store, catalog=None):
        self.growth = GrowthWorkspace(store)
        self.store, self.catalog = store, catalog or ClassicCatalog()
        self.created = []
        self.backed_up = False

    def before_write(self):
        if not self.backed_up:
            if (self.store.directory / "knowledge.sqlite").is_file():
                self.store.backup()
            self.backed_up = True

    def ensure(self, kind, key, fields, refs=(), origin_kind="history"):
        existing = next((r for r in self.growth.rows() if r["kind"] == kind and r["body"].get("curation_key") == key), None)
        if existing:
            return existing
        self.before_write()
        row = self.growth.create(kind, fields, list(dict.fromkeys(refs))[:40], key,
            {"kind": origin_kind, "label": "经典导读" if origin_kind == "classic" else "历史整理", "status": "pending"})
        self.created.append(row["id"])
        return row

    def run(self):
        self.created = []
        rows = self.growth.rows()
        profiles = [r for r in rows if r["kind"] == "profile"]
        hypotheses = [r for r in rows if r["kind"] == "hypothesis"]
        learning = [r for r in rows if r["kind"] in {"learning", "note"}]
        research = [r for r in rows if r["kind"] in {"analysis", "review", "evolution"}]
        if profiles and not any(r["kind"] == "handbook" for r in rows):
            self.ensure("handbook", "curation:handbook:1", {"title": "我的交易理念", "goals": "\n\n".join(r["body"].get("text", "") for r in profiles)[:8000],
                "boundaries": "具体投入时间、资金用途与风险承受范围，还需要由我补充。",
                "review_rhythm": "先从已有资料校对自己的方向，再逐步补齐方法与复盘安排。"}, [r["id"] for r in profiles])
        if hypotheses:
            topic = self.ensure("topic", "curation:methods-topic:1", {"title": "把交易想法，写成可检验的方法", "category": "方法与判断",
                "question": "我已有的判断依据，能否说成清楚的条件？", "summary": "已有候选认识保留在这里。先检查定义与证据，再通过观察补齐适用边界。",
                "open_questions": "哪些条件能直接观察，哪些还只是解释？"}, [r["id"] for r in hypotheses])
            # One combined draft avoids turning overlapping historical hypotheses into five rules.
            self.ensure("method", "curation:method-outline:1", {"title": "我的判断框架 · 待梳理", "topic_id": topic["id"],
                "scope": "根据历史候选认识整理；具体市场、周期与适用环境仍需补充。",
                "conditions": "\n\n".join(r["body"].get("text", "") for r in hypotheses)[:8500],
                "steps": "先校对这些认识，再为其中一条定义可观察的条件，留下事前记录。",
                "unknowns": "判定条件、失败路径、风险处理与反例尚未完整，不代表方法已验证。"}, [r["id"] for r in hypotheses])
        if learning:
            topic = self.ensure("topic", "curation:learning-topic:1", {"title": "让读过的内容，成为自己的理解", "category": "学习与认知",
                "question": "哪些内容值得吸收，哪些需要保留疑问？", "summary": "以前的学习笔记先汇在这里，原文、整理解释与实践证据分别查阅。"}, [r["id"] for r in learning])
            for r in learning:
                b = r["body"]
                title = b.get("title") or re.sub(r"^用户提供的.*?《(.*?)》.*", r"\1", b.get("text", "").split("：")[0])
                self.ensure("insight", "curation:record:" + r["id"], {"title": (title or "一条学习笔记")[:80],
                    "topic_id": topic["id"], "understanding": excerpt(b.get("understanding") or b.get("text", ""), 1800),
                    "boundary": "这是对历史笔记的整理，先校对原意与我的理解；其中的效果判断仍需实践证据。"}, [r["id"]])
        if research:
            topic = self.ensure("topic", "curation:research-topic:1", {"title": "把过去的研究，留作判断的参照", "category": "研究与复盘",
                "question": "当时的依据和未知，后来怎样变化了？", "summary": "按原来的时间和来源回看研究与复盘。历史价格、结论和候选方案保留原语境。"}, [r["id"] for r in research])
            for r in research:
                b = r["body"]
                text = b.get("text", "")
                first = next((line for line in text.splitlines() if line.strip()), "")
                title = b.get("title") or re.sub(r"^[#\s]+", "", first)[:60]
                title = title or "一份历史研究"
                self.ensure("insight", "curation:record:" + r["id"], {"title": title[:100], "topic_id": topic["id"],
                    "understanding": excerpt(text, 2200), "boundary": "历史研究，保留当时的信息时点；使用前需重新核对。", "stage": "revisit"}, [r["id"]])
        entries = self.catalog.entries()
        if entries:
            groups = {
                "direction": ("先想清交易的边界", "财富、生活与判断独立性，怎样取得平衡？"),
                "execution": ("让判断与执行更清楚", "如何准备失败路径，保持耐心，又允许自己更正？"),
                "method": ("方法如何接受检验", "环境、证据与反例，怎样帮助我修订方法？"),
                "shortterm": ("读懂短线心法的适用条件", "前辈的具体经验，哪些能理解，哪些仍需观察？")}
            topics = {key: self.ensure("topic", "curation:classics-topic:" + key, {"title": title, "category": "经典导读",
                "question": question, "summary": "先读原意，再看导读、适用边界与值得继续的问题。出处性质单独保留，个人认同与效果仍需分别判断。"}, origin_kind="classic") for key, (title, question) in groups.items()}
            for item in entries:
                entry = self.catalog.get(item["id"])
                number = int(entry["id"][1:]) if re.fullmatch(r"K\d+", entry["id"]) else 0
                group = "shortterm" if number >= 15 else "direction" if number in {1, 5, 6, 7} else "method" if number in {4, 10, 13, 14} else "execution"
                topic = topics.get(entry.get("learning_group"), topics[group])
                parts = bullets(entry["content"])
                existing = next((r for r in self.growth.rows() if r["kind"] == "source" and r["body"].get("classic_id") == entry["id"]), None)
                if not existing:
                    self.before_write()
                source = existing or self.store.save_artifact("source", {"title": entry["title"], "classic_id": entry["id"],
                    "text": entry["content"], "origin": "已有经典资料", "source_status": entry["source_status"],
                    "catalog_snapshot": "本工程已有经典资料", "source_note": parts.get("来源", "出处见原始条目")}, [], "curation:classic-source:" + entry["id"])
                understanding = parts.get("本项目理解") or parts.get("本项目应用") or parts.get("本项目推导") or parts.get("助手推导") or parts.get("作者观点归纳") or parts.get("语境") or "结合原文与语境，形成自己的理解。"
                source_text = parts.get("原文") or parts.get("原文短摘") or parts.get("作者观点归纳") or parts.get("来源", "")
                if "作者观点归纳" in parts and "原文" not in parts and "原文短摘" not in parts:
                    source_text = "观点归纳（非逐字引文）：" + source_text
                self.ensure("insight", "curation:classic:" + entry["id"], {"title": entry["title"], "topic_id": topic["id"],
                    "source_text": source_text, "understanding": understanding,
                    "boundary": parts.get("边界", "结合原语境理解，不能据此证明交易效果。"),
                    "example": parts.get("个人吸收问题") or parts.get("个人应用候选", "")}, [source["id"]], "classic")
        if self.created:
            self.store.save_artifact("curation_batch", {"created_ids": self.created, "scope": "新增历史整理与经典导读，待用户校对；原始记录保留。"},
                self.created, "curation-batch:" + digest(self.created))
        return {"created": len(self.created), "ids": self.created, "message": "已整理为可查阅的专题与笔记，原始来源仍可回查。" if self.created else "已有资料已经整理过；你编辑的内容会继续保留。"}
