"""Independent knowledge states; these never activate a trading rule."""
from .common import DomainError

LAYERS = ("dao", "fa", "shu", "qi")
STATES = {
    "source": {"user_report", "original_checked", "attributed_retelling", "publisher_description", "unverified"},
    "adoption": {"pending", "agreed", "questioned", "rejected"},
    "evidence": {"unverified", "observing", "limited_support", "contradicted", "needs_review"},
    "rule": {"not_applicable", "draft"},
}
DEFAULT_LAYERS = {
    "profile": ["dao", "qi"], "emotion": ["dao", "qi"], "learning": ["dao", "qi"],
    "plan": ["fa", "shu", "qi"], "trade": ["fa", "shu", "qi"],
    "position": ["fa", "qi"], "research": ["shu", "qi"], "observation": ["shu", "qi"],
    "review": ["dao", "fa", "shu", "qi"], "evolution": ["dao", "fa", "shu", "qi"],
    "hypothesis": ["shu", "qi"], "note": ["dao", "qi"], "analysis": ["shu", "qi"],
    "proposal": ["fa", "qi"], "strategy": ["fa", "qi"], "source": ["qi"],
}

def validate_metadata(item):
    if "layers" in item:
        layers = item["layers"]
        if not isinstance(layers, list) or not layers or any(x not in LAYERS for x in layers) or len(set(layers)) != len(layers):
            raise DomainError("INVALID_LAYERS", "层级需为不重复的 dao/fa/shu/qi 数组")
    if "states" in item:
        states = item["states"]
        if not isinstance(states, dict) or set(states) - set(STATES):
            raise DomainError("INVALID_STATES", "来源、认同、效果与规则状态分别填写")
        for key, value in states.items():
            if not isinstance(value, str) or value not in STATES[key]:
                raise DomainError("INVALID_STATES", f"状态 {key} 无效；普通知识不能激活规则")
        if states.get("evidence") == "limited_support" and not item.get("refs"):
            raise DomainError("MISSING_EVIDENCE", "有限支持需关联实际案例或证据引用")

def describe_record(record):
    body = record["body"]
    defaults = {"source": "user_report" if body.get("source_id") else "unverified",
                "adoption": "pending", "evidence": "unverified", "rule": "not_applicable"}
    states = {**defaults, **body.get("states", {})}
    if record["kind"] == "proposal":
        states["rule"] = "draft"
    elif record["kind"] == "strategy" and body.get("confirmation_source_id"):
        states.update(source="user_report", adoption="agreed", rule="active")
    if record["status"] == "needs_recheck":
        states["evidence"] = "needs_review"
    return {"layers": body.get("layers", DEFAULT_LAYERS.get(record["kind"], ["qi"])),
            "layer_assignment": "explicit" if "layers" in body else "derived_from_kind",
            "states": states, "state_scope": "缺省状态只表示尚未提供，不代表用户同意或方法已验证"}
