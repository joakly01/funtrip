"""Read-only providers. Public endpoints are best-effort, never exchange-grade SLA."""
from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
from datetime import datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .common import DomainError, TZ, now, stamp, finite


def request(url, payload=None, encoding="utf-8"):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "Mozilla/5.0 TradingPartner/0.1", "Content-Type": "application/json"}
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=12) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise DomainError("RESPONSE_TOO_LARGE", "数据响应过大")
            return raw.decode(encoding)
    except (urllib.error.URLError, TimeoutError, socket.timeout, UnicodeError) as e:
        # Do not include request body, credential or token in an error.
        raise DomainError("PROVIDER_UNAVAILABLE", f"行情来源不可用：{type(e).__name__}", True)


def security_code(value):
    match = re.fullmatch(r"(SSE|SZSE|BSE):(\d{6})", value)
    if not match:
        raise DomainError("INVALID_SECURITY", "需要经过识别的标准代码，例如 SSE:600000")
    exchange, code = match.groups()
    return {"SSE": "sh", "SZSE": "sz", "BSE": "bj"}[exchange] + code


def internal_code(value):
    match = re.fullmatch(r"(sh|sz|bj)(\d{6})", value.lower())
    if not match:
        raise DomainError("INVALID_SECURITY", "证券代码格式无效")
    return {"sh": "SSE", "sz": "SZSE", "bj": "BSE"}[match[1]] + ":" + match[2]


def is_a_share(fields):
    # Tencent marks ChiNext and STAR A shares with separate board types.
    # Field 60 also contains multiple flags (e.g. ' A A' or 'A RA').
    return (len(fields) >= 62 and 'A' in fields[60].split()
            and fields[61] in {'GP-A', 'GP-A-CYB', 'GP-A-KCB'})


def age_status(data_time, fetched_time):
    if not data_time:
        return {"state": "unknown", "age_seconds": None, "label": "来源未给出可靠行情时间"}
    seconds = (stamp(fetched_time) - stamp(data_time)).total_seconds()
    if seconds < -120:
        return {"state": "invalid_future", "age_seconds": seconds, "label": "来源时间晚于本机时间，暂停当前行情判断"}
    return {"state": "recent" if seconds <= 120 else "historical_or_delayed", "age_seconds": max(0, seconds),
            "label": "最近来源快照；非交易所延迟保证" if seconds <= 120 else "历史或延迟快照；请结合休市及证券状态判断"}


class Tencent:
    name = "tencent_public"

    def raw_quotes(self, symbols):
        if not symbols or any(not re.fullmatch(r"(sh|sz|bj)\d{6}", s) for s in symbols):
            raise DomainError("INVALID_SECURITY", "证券格式无效")
        text = request("https://qt.gtimg.cn/q=" + ",".join(symbols), encoding="gb18030")
        output = {}
        for symbol, raw in re.findall(r'v_((?:sh|sz|bj)\d{6})="([^"\n]*)"', text):
            fields = raw.split("~")
            if len(fields) > 34 and fields[2] == symbol[2:]:
                output[symbol] = fields
        return output

    def resolve(self, query):
        q = query.strip()
        if re.fullmatch(r"(SSE|SZSE|BSE):\d{6}", q):
            symbols = [security_code(q)]
        elif re.fullmatch(r"(sh|sz|bj)\d{6}", q.lower()):
            symbols = [q.lower()]
        elif re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", q.upper()):
            symbols = [q[-2:].lower() + q[:6]]
        elif re.fullmatch(r"\d{6}", q):
            # Probe identities, not a prefix-only inference.
            symbols = [p + q for p in ("sh", "sz", "bj")]
        else:
            if not q or len(q) > 40:
                raise DomainError("INVALID_QUERY", "请提供股票名称或代码，勿发送整段日记")
            raw = request("https://smartbox.gtimg.cn/s3/?" + urlencode({"q": q, "t": "gp"}), encoding="gb18030")
            try:
                candidates = json.loads(raw.split("=", 1)[1].strip().rstrip(";"))
                symbols = []
                for item in candidates.split("^"):
                    fields = item.split("~")
                    if len(fields) >= 3 and fields[0] in {"sh", "sz", "bj"} and re.fullmatch(r"\d{6}", fields[1]):
                        symbols.append(fields[0] + fields[1])
            except (ValueError, IndexError):
                raise DomainError("PROVIDER_SCHEMA_CHANGED", "证券搜索响应格式变化，请提供交易所及股票代码")
        if not symbols:
            return {"resolved": None, "candidates": [], "reason": "未找到证券"}
        raw = self.raw_quotes(symbols[:8])
        candidates = []
        for symbol, fields in raw.items():
            # The first version serves A shares, not index/fund lookalikes.
            if not is_a_share(fields):
                continue
            candidates.append({"security_id": internal_code(symbol), "name": fields[1], "code": fields[2], "source": self.name})
        exact = [c for c in candidates if c["name"] == q]
        selected = exact[0] if len(exact) == 1 else (candidates[0] if len(candidates) == 1 else None)
        return {"resolved": selected, "candidates": candidates, "fetched_at": now()}

    def quote(self, security_id):
        symbol = security_code(security_id)
        raw = self.raw_quotes([symbol])
        if symbol not in raw:
            raise DomainError("NO_DATA", "该证券没有可用报价")
        f = raw[symbol]
        if not is_a_share(f):
            raise DomainError("UNSUPPORTED_SECURITY", "首版仅处理已核实身份的 A 股股票")
        try:
            data_time = datetime.strptime(f[30], "%Y%m%d%H%M%S").replace(tzinfo=TZ).isoformat()
            data = {"security_id": security_id, "name": f[1], "last": float(f[3]), "previous_close": float(f[4]),
                    "open": float(f[5]), "high": float(f[33]), "low": float(f[34]), "data_as_of": data_time,
                    "fetched_at": now(), "source": self.name, "market_status": "unknown", "provider_delay_seconds": None,
                    "currency": "CNY", "source_url": "https://qt.gtimg.cn/q=" + symbol, "raw": f}
        except (ValueError, IndexError):
            raise DomainError("PROVIDER_SCHEMA_CHANGED", "报价字段或时间格式无法解析")
        finite(data["last"], "最新价", positive=True)
        data["freshness"] = age_status(data_time, data["fetched_at"])
        data["warnings"] = ["公开来源尽力提供；未接入证券交易日历，交易/停牌状态未知", "未验证成交量单位，因此报价不输出标准化量额"]
        return data

    def bars(self, security_id, interval="1d", count=160, adjustment="forward", closed_only=True):
        if not 20 <= count <= 640:
            raise DomainError("INVALID_COUNT", "count 应为 20 到 640")
        if interval not in {"1d", "5m", "15m", "30m", "60m"}:
            raise DomainError("UNSUPPORTED_INTERVAL", "公开来源当前支持日线及 5/15/30/60 分钟；其他周期未实现")
        if adjustment not in {"none", "forward"}:
            raise DomainError("INVALID_ADJUSTMENT", "仅支持 none/forward")
        symbol = security_code(security_id)
        if interval == "1d":
            adj = "qfq" if adjustment == "forward" else ""
            url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?" + urlencode({"param": f"{symbol},day,,,{count},{adj}"})
            key = "qfqday" if adj else "day"
        else:
            if adjustment != "none":
                raise DomainError("UNSUPPORTED_ADJUSTMENT", "公开分钟序列只采用不复权口径")
            key = "m" + interval[:-1]
            url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?" + urlencode({"param": f"{symbol},{key},,{count}"})
        text = request(url)
        try:
            payload = json.loads(text)
            node = payload["data"][symbol]
            if key not in node:
                if adjustment == "forward" and interval == "1d" and "day" in node:
                    raise DomainError("ADJUSTED_HISTORY_UNAVAILABLE", "当前来源未提供该股票的前复权历史，暂时无法分析大趋势。可先在图表与画线中查看原始走势，或稍后重试。")
                raise KeyError(key)
            rows = node[key]
            parsed, fetched = [], now()
            current = stamp(fetched)
            for row in rows:
                if interval == "1d":
                    end = datetime.strptime(row[0], "%Y-%m-%d").replace(hour=15, tzinfo=TZ)
                    # Conservative: today's daily candle stays unconfirmed until next date.
                    final = end.date() < current.date()
                else:
                    end = datetime.strptime(row[0], "%Y%m%d%H%M").replace(tzinfo=TZ)
                    # Provider convention isn't a guaranteed publication deadline.
                    final = end + timedelta(minutes=int(interval[:-1])) < current
                parsed.append({"time": end.isoformat(), "open": float(row[1]), "close": float(row[2]), "high": float(row[3]),
                               "low": float(row[4]), "volume_raw": float(row[5]), "is_final": final})
        except (ValueError, KeyError, TypeError, IndexError):
            raise DomainError("PROVIDER_SCHEMA_CHANGED", "行情序列缺失或格式变化，未自动改用其他口径")
        if not parsed:
            raise DomainError("NO_DATA", "行情序列为空")
        usable = [r for r in parsed if r["is_final"] or not closed_only]
        return {"security_id": security_id, "interval": interval, "adjustment": adjustment,
                "adjustment_anchor": fetched[:10] if adjustment == "forward" else None,
                "bars": usable, "excluded_unfinished": len(parsed)-len(usable), "source": self.name, "source_url": url,
                "data_as_of": usable[-1]["time"] if usable else None, "fetched_at": fetched,
                "volume_unit": "unverified_provider_native", "raw": rows,
                "warnings": ["量单位尚未独立核实，量额计算已禁用", "缺完整交易日历，不保证序列无缺口", "当天日线按保守规则视为未最终确认"]}


class Tushare:
    name = "tushare"

    def api(self, api_name, params):
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise DomainError("CREDENTIAL_REQUIRED", "未配置 TUSHARE_TOKEN；不要把密钥发到聊天中")
        text = request("https://api.tushare.pro", {"api_name": api_name, "token": token, "params": params, "fields": ""})
        try:
            payload = json.loads(text)
            if payload.get("code") != 0:
                raise DomainError("PROVIDER_DENIED", "Tushare 拒绝请求，请核对接口权限及配额")
            fields, items = payload["data"]["fields"], payload["data"]["items"]
            return [dict(zip(fields, row)) for row in items]
        except (ValueError, KeyError, TypeError):
            raise DomainError("PROVIDER_SCHEMA_CHANGED", "Tushare 返回格式无效")

    def bars(self, security_id, interval="1d", count=160, adjustment="none", closed_only=True):
        if adjustment != "none":
            raise DomainError("UNSUPPORTED_ADJUSTMENT", "首版 Tushare 适配只使用不复权数据")
        symbol = security_code(security_id)
        code = symbol[2:] + "." + symbol[:2].upper()
        fetched = now()
        if interval == "1d":
            raw = self.api("daily", {"ts_code": code, "start_date": (stamp(fetched)-timedelta(days=count*2+30)).strftime("%Y%m%d"), "end_date": stamp(fetched).strftime("%Y%m%d")})
        elif interval in {"1m", "5m", "15m", "30m", "60m"}:
            raw = self.api("rt_min_daily", {"ts_code": code, "freq": interval[:-1] + "MIN"})
        else:
            raise DomainError("UNSUPPORTED_INTERVAL", "周期不支持")
        rows = []
        try:
            for r in raw:
                if interval == "1d":
                    end = datetime.strptime(r["trade_date"], "%Y%m%d").replace(hour=15, tzinfo=TZ)
                    final = end.date() < stamp(fetched).date()
                else:
                    end = datetime.fromisoformat(r["time"]).replace(tzinfo=TZ)
                    final = end + timedelta(minutes=int(interval[:-1])) < stamp(fetched)
                rows.append({"time": end.isoformat(), **{k: float(r[k]) for k in ("open", "high", "low", "close")},
                             "volume": float(r["vol"])*(100 if interval == "1d" else 1), "is_final": final})
        except (ValueError, KeyError, TypeError):
            raise DomainError("PROVIDER_SCHEMA_CHANGED", "Tushare 行情字段无法解析")
        rows.sort(key=lambda r: r["time"])
        excluded = sum(not r["is_final"] for r in rows) if closed_only else 0
        rows = [r for r in rows if r["is_final"] or not closed_only][-count:]
        if not rows:
            raise DomainError("NO_DATA", "没有满足条件的行情数据")
        return {"security_id": security_id, "interval": interval, "adjustment": "none", "adjustment_anchor": None,
                "bars": rows, "excluded_unfinished": excluded, "volume_unit": "share", "fetched_at": fetched,
                "data_as_of": rows[-1]["time"], "source": self.name, "raw": raw,
                "warnings": ["Tushare 权限和实时延迟需实际核实", "不复权序列可能受除权影响；不能据此自动确认跨除权趋势"]}
