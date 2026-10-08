"""Product-facing system and concrete, bounded tool surfaces."""
from trading_agent.analysis import check_rules

SKILLS = [
    {"id": "trading-plan-review", "title": "交易计划检查", "description": "行动前，把依据、风险和退出条件说清楚。",
     "input": "买卖或持有计划、交易周期、当前持仓", "output": "计划缺口、失败路径、条件方案",
     "boundary": "检查计划，不代替你下单。", "tools": ["memory_search", "strategy_get", "rules_check", "risk_compute"],
     "prompt": "请使用交易计划检查技能，帮我检查下面的计划。先找回相关纪律，信息不足时向我追问。\n\n我的计划："},
    {"id": "trading-experience-review", "title": "交易经验复盘", "description": "分开看当时判断、实际执行和最后结果。",
     "input": "原始计划、实际成交、当时的判断", "output": "案例复盘、候选经验、后续验证问题",
     "boundary": "一次盈亏不会自动变成规律。", "tools": ["memory_search", "memory_read", "review_save", "hypothesis_save"],
     "prompt": "请使用交易经验复盘技能，帮我还原一次交易，分别评价事前判断、计划执行和结果，并整理可回查的复盘成果。\n\n这次经历："},
    {"id": "trading-classic-learning", "title": "知识学习与吸收", "description": "从原意到自己的理解，再明确应用边界。",
     "input": "文章、语录、读书笔记或经典条目", "output": "学习总结、适用边界、实践候选",
     "boundary": "转述与已核验原文分别标明。", "tools": ["classic_search", "learning_link", "memory_search", "evidence_check"],
     "prompt": "请使用知识学习与吸收技能，把下面的内容整理成便于查阅的学习成果：原意、我的理解、适用边界、实践候选与来源。认同和验证状态分别保留。\n\n学习内容："},
    {"id": "trading-environment-review", "title": "体系与环境回顾", "description": "方法不再顺手时，查原因，再谈修改。",
     "input": "方法表现变化、反例、原有规则与新认识", "output": "竞争解释、适用边界、体系修改提案",
     "boundary": "修改提案需你确认，才可能成为纪律。", "tools": ["memory_search", "strategy_get", "evolution_record", "strategy_propose"],
     "prompt": "请使用体系与环境回顾技能，检查我的方法与当前环境是否适配，比较其他解释，整理有依据的修改提案。不要自动激活纪律。\n\n想回顾的问题："},
]
TOOLS = [
    {"id": "trend", "title": "大趋势分析", "description": "周线看大方向，日线看当前位置；明确各阶段的观察与行动条件。",
     "uses": ["market_trend"], "action": "分析趋势", "note": "多周期对照 · 判断依据与失效条件"},
    {"id": "chart", "title": "图表与画线", "description": "自动推荐观察模式，画出趋势通道、横盘区间与关键价位。",
     "uses": ["market_study"], "action": "打开图表", "note": "自动分析 · 依据可查 · 支持手动补充"},
    {"id": "activity", "title": "资金参与判断", "description": "自动判断参与活跃程度与量价方向，推荐观察模式并比较其他解释。",
     "uses": ["market_study"], "action": "分析量价", "note": "计算依据可查 · 不等同主力净流入"},
    {"id": "selection", "title": "趋势与量价检查", "description": "依次核对上涨结构、反复放量与回撤量能、指定起点的近期涨幅。",
     "uses": ["market_selection"], "action": "检查条件", "note": "三项逐一核对 · 起点由你选择 · 不自动买入"},
    {"id": "pattern", "title": "放量后的结构观察", "description": "自动定位放量上影，画出参考价区，逐步核对守位、回撤与再次突破。",
     "uses": ["market_pattern"], "action": "观察结构", "note": "自动画线 · 逐日回看 · 保留失效与到期"},
    {"id": "risk", "title": "仓位与风险计算", "description": "输入数量和价格，计算市值、账户占比与退出情景。",
     "uses": ["risk_compute"], "action": "开始计算", "note": "无需模型 · 本次输入不入库"},
    {"id": "quote", "title": "A 股行情查询", "description": "按名称或代码查公开报价，保留来源与数据时间。",
     "uses": ["security_resolve", "market_quote"], "action": "查询行情", "note": "公开行情 · 可能有延迟"},
    {"id": "journal", "title": "持仓与交易台账", "description": "查看记录过的持仓和成交，回到原话与更正版本。",
     "uses": ["positions_get", "memory_read"], "action": "打开台账", "note": "个人历史记录 · 未同步券商"},
    {"id": "rules", "title": "准则条件检查", "description": "按当前生效准则核对条件，缺项如实保留待判断。",
     "uses": ["rules_check", "strategy_get"], "action": "检查条件", "note": "确定性计算 · 需已有生效准则"},
]


def system_workspace(knowledge, skill_names, tool_names, model_ready):
    rows = knowledge.rows()
    rows.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
    principle_kinds = {"note", "learning", "hypothesis", "evolution"}
    principles = [r for r in rows if r["kind"] in principle_kinds and "dao" in r["layers"]]
    skills = [{**s, "available": s["id"] in skill_names and all(t in tool_names for t in s["tools"]),
               "model_ready": model_ready} for s in SKILLS]
    return {"strategy": knowledge.store.active_snapshot(),
            "background": [r for r in rows if r["kind"] == "profile"],
            "principles": [r for r in principles if r["states"]["adoption"] == "agreed"],
            "pending_principles": [r for r in principles if r["states"]["adoption"] == "pending"],
            "proposals": [r for r in rows if r["kind"] == "proposal"],
            "methods": [r for r in rows if r["kind"] in {"hypothesis", "learning", "evolution", "review", "note"}
                        and "shu" in r["layers"] and r["states"]["adoption"] != "rejected"],
            "skills": skills,
            "tools": [{**t, "available": all(n in tool_names for n in t["uses"])} for t in TOOLS]}


def rule_form(store):
    strategy = store.active_snapshot()
    fields = {}
    labels = {"account_equity": "账户总资产（元）", "account_weight": "账户仓位（小数，如 0.3）",
              "position_weight": "单个持仓占比（小数）", "risk_ratio": "风险比例（小数）", "price": "计划价格（元）",
              "quantity": "计划数量（股）", "amount": "计划金额（元）", "horizon": "交易周期"}
    for rule in strategy["body"].get("rules", []):
        c = rule.get("condition")
        if not isinstance(c, dict) or not c.get("field") or c.get("operator") not in {"lte", "gte", "eq", "in"} or "value" not in c:
            continue
        value = c["value"]
        options = value if c["operator"] == "in" and isinstance(value, list) else None
        kind = "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "text"
        if options:
            kind = "text"  # UI returns the selected value without string conversion.
        fields.setdefault(c["field"], {"key": c["field"], "label": c.get("label") or labels.get(c["field"], rule["text"]),
                                       "type": kind, "options": options})
    return {"strategy": strategy, "fields": list(fields.values())}


def check_current_rules(store, inputs):
    strategy = store.active_snapshot()
    return {"strategy_id": strategy["id"], **check_rules(strategy["body"].get("rules", []), inputs)}
