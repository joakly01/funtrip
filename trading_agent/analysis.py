from __future__ import annotations

import html
import math
import statistics
from datetime import timedelta
from decimal import Decimal

from .common import DomainError, finite, stamp, now


def validate_bars(data):
    errors, warnings = [], list(data.get("warnings", []))
    bars = data.get("bars", [])
    previous = None
    fetched = stamp(data["fetched_at"])
    for i, row in enumerate(bars):
        try:
            ts = stamp(row["time"])
            vals = [finite(row[k], k, positive=True) for k in ("open", "high", "low", "close")]
            o,h,l,c = vals
            if not l <= min(o,c) <= max(o,c) <= h:
                errors.append(f"第 {i} 行 OHLC 关系不成立")
            if previous and ts <= previous:
                errors.append(f"第 {i} 行重复或时间倒序")
            if ts > fetched + timedelta(minutes=1):
                errors.append(f"第 {i} 行位于获取时间之后")
            previous = ts
            if "volume" in row and finite(row["volume"], "volume") < 0:
                errors.append(f"第 {i} 行成交量为负")
        except (KeyError, DomainError) as e:
            errors.append(f"第 {i} 行字段无效：{e}")
    if not bars:
        errors.append("无可用 K 线")
    if data.get("volume_unit") != "share":
        warnings.append("成交量单位未标准化；不计算量指标")
    return {"valid": not errors, "errors": errors, "warnings": list(dict.fromkeys(warnings)),
            "checked_rows": len(bars), "checks": ["价格有限且正值", "OHLC关系", "时间顺序及重复", "未来数据", "成交量单位"],
            "not_checked": ["交易日历完整性", "交易所实时延迟", "独立来源价格一致性"]}


def compute_features(data):
    quality = validate_bars(data)
    if not quality["valid"]:
        raise DomainError("INVALID_MARKET_DATA", "; ".join(quality["errors"]))
    bars = [b for b in data["bars"] if b.get("is_final")]
    if not bars:
        raise DomainError("INSUFFICIENT_DATA", "没有已完成的 K 线")
    closes = [b["close"] for b in bars]
    out = {"count": len(bars), "last_closed": closes[-1], "last_closed_at": bars[-1]["time"],
           "interval": data["interval"], "adjustment": data["adjustment"], "algorithm_version": "price-features-v1",
           "note": "指标为观察特征，不代表已确认的个人交易规则或买卖信号"}
    for n in (5,20,60):
        out[f"sma_{n}"] = statistics.fmean(closes[-n:]) if len(closes) >= n else None
    out["return_20_bars"] = closes[-1]/closes[-21]-1 if len(closes) >= 21 else None
    out["prior_20_high"] = max(b["high"] for b in bars[-21:-1]) if len(bars) >= 21 else None
    out["prior_20_low"] = min(b["low"] for b in bars[-21:-1]) if len(bars) >= 21 else None
    if len(bars) >= 15:
        trs = [max(bars[i]["high"]-bars[i]["low"],abs(bars[i]["high"]-bars[i-1]["close"]),abs(bars[i]["low"]-bars[i-1]["close"])) for i in range(len(bars)-14,len(bars))]
        out["atr_14_simple"] = statistics.fmean(trs)
    else:
        out["atr_14_simple"] = None
    out["volume_ratio_20"] = None
    if data.get("volume_unit") == "share" and len(bars) >= 21 and all("volume" in b for b in bars[-21:]):
        mean = statistics.fmean(b["volume"] for b in bars[-21:-1])
        out["volume_ratio_20"] = bars[-1]["volume"]/mean if mean else None
    return {"features": out, "quality": quality}


def check_rules(rules, inputs):
    results = []
    operations = {"lte": lambda a,b:a<=b, "gte":lambda a,b:a>=b, "eq":lambda a,b:a==b, "in":lambda a,b:a in b}
    for rule in rules:
        condition = rule.get("condition")
        result = {"rule_id": rule["id"], "text": rule["text"], "result": "unknown"}
        if not isinstance(condition, dict):
            result["reason"] = "描述性规则，需人工结合证据判断"
        elif condition.get("field") not in inputs or inputs[condition["field"]] is None:
            result["reason"] = "缺少输入：" + str(condition.get("field"))
        elif condition.get("operator") not in operations or "value" not in condition:
            result["reason"] = "未实现的规则条件"
        else:
            try:
                a,b = inputs[condition["field"]], condition["value"]
                if condition["operator"] in {"gte","lte"}:
                    finite(a, "规则输入"); finite(b,"规则阈值")
                passed = operations[condition["operator"]](a,b)
                result.update(result="pass" if passed else "conflict", reason="依据已生效条件计算", input=a)
            except (TypeError, DomainError):
                result["reason"] = "输入类型不符合规则"
        results.append(result)
    overall = "not_defined" if not results else ("conflict" if any(r["result"]=="conflict" for r in results) else ("unknown" if any(r["result"]=="unknown" for r in results) else "pass"))
    return {"overall": overall, "rules": results}


def risk(positions, account_equity=None):
    values = []
    for p in positions:
        q = finite(p["quantity"], "quantity", positive=True)
        price = finite(p["price"], "price", positive=True)
        value = Decimal(str(q))*Decimal(str(price))
        item = {"security_id": p["security_id"], "market_value": float(value)}
        if p.get("scenario_price") is not None:
            exit_price = finite(p["scenario_price"], "scenario_price", positive=True)
            item["scenario_pnl"] = float(Decimal(str(q))*(Decimal(str(exit_price))-Decimal(str(price))))
        values.append(item)
    total = sum(Decimal(str(v["market_value"])) for v in values)
    if account_equity is not None:
        finite(account_equity, "account_equity", positive=True)
    return {"positions": values, "known_market_value": float(total),
            "account_weight": float(total/Decimal(str(account_equity))) if account_equity is not None else None,
            "scope": "仅针对明确输入的持仓与价格；不是自动同步的完整账户", "missing": [] if account_equity is not None else ["account_equity"],
            "warning": "情景价格不是成交保证；未计交易费用和滑点"}


def chart_svg(data):
    quality = validate_bars(data)
    if not quality["valid"]:
        raise DomainError("INVALID_MARKET_DATA", "; ".join(quality["errors"]))
    bars = data["bars"][-100:]
    width,height,left,top = 1000,500,70,90
    lo=min(b["low"] for b in bars); hi=max(b["high"] for b in bars)
    span=hi-lo or max(hi*.01, .01)
    lo-=span*.08; hi+=span*.08
    step=880/max(len(bars),1)
    y=lambda p: top+(hi-p)/(hi-lo)*310
    esc=lambda s:html.escape(str(s))
    title=f"{data['security_id']} · {data['interval']} · {data['adjustment']}"
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
         '<rect width="1000" height="500" fill="#101b2d"/>',
         '<g font-family="sans-serif" fill="#e6edf7">',f'<text x="70" y="35" font-size="21">{esc(title)}</text>',
         f'<text x="70" y="62" font-size="12">来源 {esc(data["source"])} · 截至 {esc(data["data_as_of"])}</text>']
    for i in range(5):
        p=lo+(hi-lo)*i/4
        out.extend([f'<path d="M70 {y(p):.2f} H950" stroke="#293750"/>', f'<text x="10" y="{y(p)+4:.2f}" font-size="12">{p:.2f}</text>'])
    for i,b in enumerate(bars):
        x=left+step*(i+.5); color="#f16d74" if b["close"]>=b["open"] else "#5ed1a7"
        opacity="1" if b.get("is_final") else ".45"
        out.append(f'<g stroke="{color}" fill="{color}" opacity="{opacity}"><path d="M{x:.2f} {y(b["high"]):.2f} V{y(b["low"]):.2f}"/><rect x="{x-step*.3:.2f}" y="{min(y(b["open"]),y(b["close"])):.2f}" width="{max(step*.6,1):.2f}" height="{max(abs(y(b["open"])-y(b["close"])),1):.2f}"/></g>')
    out.extend([f'<text x="70" y="423" font-size="12">{esc(bars[0]["time"])}</text>',f'<text x="635" y="423" font-size="12">{esc(bars[-1]["time"])}</text>',
                '<text x="70" y="460" font-size="12">真实来源数据；未完成 K 线以半透明显示。图形本身不构成买卖信号。</text>','</g></svg>'])
    return "\n".join(out)
