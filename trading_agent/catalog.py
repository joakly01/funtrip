"""Read the public seed catalog without importing it as personal beliefs."""
import json
import re
from pathlib import Path
from .common import DomainError, ROOT

class ClassicCatalog:
    def __init__(self, root=ROOT):
        self.root = Path(root)

    def entries(self):
        path = self.root / "knowledge" / "classics" / "catalog.json"
        if not path.is_file():
            raise DomainError("CATALOG_MISSING", "经典索引缺失，不能声称已经读取")
        return json.loads(path.read_text(encoding="utf-8"))["entries"]

    def get(self, classic_id):
        entry = next((e for e in self.entries() if e["id"] == classic_id), None)
        if entry is None:
            raise DomainError("CLASSIC_NOT_FOUND", "经典知识编号不存在")
        path = self.root / "knowledge" / "classics" / "cards.md"
        if not path.is_file():
            raise DomainError("CATALOG_MISSING", "经典正文缺失")
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^## " + re.escape(classic_id) + r"[｜|].*?(?=^## |\Z)", text, re.M | re.S)
        if not match:
            raise DomainError("CATALOG_MISMATCH", "索引与正文不一致")
        return {**entry, "content": match.group(0).strip(), "path": str(path),
                "personal_adoption": "pending", "personal_effect": "unverified"}

    def search(self, query="", limit=5):
        if not 1 <= limit <= 14:
            raise DomainError("INVALID_LIMIT", "经典检索limit需为1到14")
        words = query.casefold().split()
        scored = []
        for entry in self.entries():
            haystack = " ".join([entry["id"], entry["title"], *entry["tags"]]).casefold()
            score = sum(word in haystack for word in words)
            if not words or score:
                scored.append((score, entry["id"]))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return {"entries": [self.get(cid) for _, cid in scored[:limit]], "catalog_size": len(self.entries()),
                "scope": "按明确主题词检索公开种子知识；出处核对不等于个人认同或效果验证"}
