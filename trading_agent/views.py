"""Four-layer views of existing records; export never changes beliefs or rules."""
import hashlib
import json
import shutil
from pathlib import Path
from .common import ROOT, DomainError, atomic_text, now, stamp
from .metadata import LAYERS, describe_record
from .catalog import ClassicCatalog

LABELS = {"dao": "道：方向、理念、规律、价值观", "fa": "法：制度、规则、战略、框架",
          "shu": "术：方法、技巧、手段、策略", "qi": "器：工具、技术、资源、平台"}

class SystemViews:
    def __init__(self, store, root=ROOT):
        self.store, self.root = store, Path(root)

    def overview(self):
        rows = self.store.snapshot(limit=None)["records"]
        ignored = {"raw", "market_snapshot", "agent_run", "agent_result", "source"}
        layers = {layer: [] for layer in LAYERS}
        for row in rows:
            if row["kind"] in ignored:
                continue
            metadata = describe_record(row)
            item = {"id": row["id"], "kind": row["kind"], "version": row["version"], "status": row["status"],
                    "recorded_at": row["created_at"], "text": row["body"].get("text", canonical_brief(row["body"])), **metadata}
            for layer in metadata["layers"]:
                layers[layer].append(item)
        return {"layers": layers, "strategy": self.store.active_snapshot(),
                "classic_count": len(ClassicCatalog(self.root).entries()), "record_count": len(rows),
                "gaps": ["未提供的资金、周期、底部和风险阈值保留未知", "候选心得与个人认同不能当成有效纪律",
                         "量单位、交易日历、完整缠论、常驻任务仍有限制"],
                "scope": "历史自述与实际生效版本；不推导实时账户，不改写旧对象"}

    def render(self):
        overview = self.overview()
        generated = now()
        contents = {}
        for layer, items in overview["layers"].items():
            lines = ["# " + LABELS[layer], "", "生成时间：" + generated, "", "默认状态不代表已认同或已验证。", ""]
            if not items:
                lines.append("本层尚未形成个人记录；不补造内容。")
            for item in items:
                lines += [f"## {item['id']} · {item['kind']} · v{item['version']}", "", item["text"], "",
                          f"记录时间：{item['recorded_at']}；对象状态：{item['status']}",
                          "独立状态：" + json.dumps(item["states"], ensure_ascii=False),
                          "层级归类：" + item["layer_assignment"], ""]
            if layer == "fa":
                lines += ["## 实际生效规则", ""] + rule_lines(overview["strategy"])
            contents[layer.upper() + ".md"] = "\n".join(lines) + "\n"
        system = ["# 我的交易体系", "", "生成时间：" + generated, "", overview["scope"], "",
                  "## 实际生效版本", ""] + rule_lines(overview["strategy"])
        system += ["", "## 四层记录", ""]
        for layer in LAYERS:
            system.append(f"- [{LABELS[layer]}]({layer.upper()}.md)：{len(overview['layers'][layer])}条关联资料")
        system += ["", "## 知识与缺口", "", f"公开经典{overview['classic_count']}条，仍与个人认同、实践证据分开。"]
        system += ["- " + gap for gap in overview["gaps"]]
        contents["SYSTEM.md"] = "\n".join(system) + "\n"
        rows = self.store.snapshot(limit=None)["records"]
        for filename, title, kind in [("PROFILE.md", "我的目标与背景", "profile"), ("EVOLUTION.md", "进化记录", "evolution")]:
            entries = [r for r in rows if r["kind"] == kind]
            contents[filename] = "# " + title + "\n\n" + "\n\n".join(
                f"{r['id']} · {r['created_at']} · {r['status']}\n\n" + r["body"].get("text", canonical_brief(r["body"])) for r in entries) + "\n"
        index = ["# 我的资料索引", "", overview["scope"], ""]
        for name in sorted(contents):
            index.append(f"- [{name}]({name})")
        index += ["", "## 对象", ""]
        index += [f"- {r['id']} · {r['kind']} · v{r['version']} · {r['status']}" for r in rows if r["kind"] != "market_snapshot"]
        contents["INDEX.md"] = "\n".join(index) + "\n"
        directory = self.store.directory / "exports"
        manifest_path = directory / ".view-manifest.json"
        old_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        for name in contents:
            path = directory / name
            if name in old_manifest and path.exists() and file_hash(path) != old_manifest[name]:
                raise DomainError("VIEW_EDIT_CONFLICT", "可读视图已被手工修改，请先整理为更正或提案：" + name)
        # Preserve the previous files before first upgraded export or a later generation.
        existing = [directory / name for name in contents if (directory / name).exists()]
        if existing:
            backup = self.store.directory / "backups" / ("views-" + stamp(generated).strftime("%Y%m%d-%H%M%S-%f"))
            backup.mkdir(parents=True)
            for path in existing:
                shutil.copy2(path, backup / path.name)
        for name, content in contents.items():
            atomic_text(directory / name, content)
        manifest = {name: file_hash(directory / name) for name in contents}
        atomic_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
        return {"views": {name: str(directory / name) for name in contents}, "path": str(directory / "INDEX.md"),
                "scope": overview["scope"], "database_modified": False}

def canonical_brief(body):
    return json.dumps(body, ensure_ascii=False, sort_keys=True)

def rule_lines(strategy):
    if not strategy.get("id"):
        return ["尚无用户确认生效的交易纪律；不会把心得或经典自动激活。"]
    return ["版本：" + strategy["id"], "生效不等于已经证明有效。", ""] + [f"- {r['id']}：{r['text']}" for r in strategy["body"]["rules"]]

def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
