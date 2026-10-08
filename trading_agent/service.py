from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .common import DomainError, ROOT, now, canonical, digest, atomic_text, stamp
from .store import Store
from .market import Tencent, Tushare
from .analysis import validate_bars, compute_features, check_rules, risk, chart_svg
from .coordinator import Coordinator, INTENTS
from .views import SystemViews
from .catalog import ClassicCatalog
from .market_study import study
from .chart_auto import analyse_chart
from .trend_analysis import analyse_trend
from .volume_pattern import analyse_volume_pattern
from .selection_check import analyse_selection


def field(kind="string", **kw):
    return {"type": kind, **kw}


STR = field()
OBJ = field("object")
ARRAY = field("array", items=STR)


def validate(value, schema, path="arguments"):
    types = {"string":str,"object":dict,"array":list,"boolean":bool,"integer":int,"number":(int,float),"null":type(None)}
    kind = schema.get("type")
    expected = kind if isinstance(kind,list) else [kind]
    if kind and not any(isinstance(value,types[k]) and (k not in {"number","integer"} or not isinstance(value,bool)) for k in expected):
        raise DomainError("INVALID_ARGUMENT", f"{path} 类型无效")
    if "enum" in schema and value not in schema["enum"]:
        raise DomainError("INVALID_ARGUMENT", f"{path} 取值不在支持范围")
    if isinstance(value,dict):
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value)-set(properties):
            raise DomainError("INVALID_ARGUMENT", f"{path} 包含未知字段")
        for key in schema.get("required",[]):
            if key not in value:
                raise DomainError("INVALID_ARGUMENT", f"缺少 {path}.{key}")
        for k,v in value.items():
            if k in properties:
                validate(v,properties[k],path+"."+k)
    if isinstance(value,list) and "items" in schema:
        for i,v in enumerate(value):
            validate(v,schema["items"],f"{path}[{i}]")


class Service:
    def __init__(self, directory=None):
        self.store = Store(directory or os.environ.get("TRADING_DATA_DIR") or ROOT/"data"/"trading")
        self.public, self.paid = Tencent(), Tushare()
        self.coordinator = Coordinator(self.store, self.evidence)
        self.views = SystemViews(self.store)
        self.specs, self.handlers = {}, {}
        self._register()

    def add(self,name,description,properties,required,handler,write=False):
        self.specs[name] = {"name":name,"description":description,"inputSchema":{"type":"object","properties":properties,"required":required,"additionalProperties":False},
                            "annotations":{"readOnlyHint":not write,"destructiveHint":False,"openWorldHint": name.startswith(("market_","security_"))}}
        self.handlers[name] = handler

    def _register(self):
        add = self.add
        add("agent_status","查看真实已实现能力、资料数量与行情配置；不表示监控正在运行",{},[],self.status)
        add("inbox_capture","保存用户原文；persist=false 时不创建任何持久资料",{"message_id":STR,"text":STR,"persist":field("boolean")},["message_id","text"],self.store.capture,True)
        add("records_commit","按原文归集多个分类条目。模型负责理解，工具校验原文引用和幂等",{"source_id":STR,"items":field("array",items=OBJ),"idempotency_key":STR},["source_id","items","idempotency_key"],self.store.commit,True)
        add("memory_search","查询本地全文与元数据；known_before 按当时版本检索",{"query":STR,"kind":STR,"security_id":STR,"limit":field("integer"),"known_before":STR},[],self.store.search)
        add("memory_read","读取对象、版本、更正历史及引用",{"object_id":STR,"version":field("integer")},["object_id"],self.store.read)
        add("record_amend","追加更正并使依赖结论进入待复查状态",{"object_id":STR,"patch":OBJ,"reason":STR,"source_id":STR,"expected_version":field("integer"),"idempotency_key":STR},["object_id","patch","reason","source_id","expected_version","idempotency_key"],self.store.amend,True)
        add("security_resolve","仅用股票名称或代码识别 A 股证券；多候选时不要擅自选择",{"query":STR},["query"],self.public.resolve)
        add("market_quote","查询公开来源报价及行情时间；可选择保存快照。非交易所延迟保证",{"security_id":STR,"persist":field("boolean")},["security_id"],self.quote,True)
        add("market_bars","获取日线或分钟序列并进行质量检查；返回完整数据或快照编号",{"security_id":STR,"interval":STR,"count":field("integer"),"adjustment":STR,"closed_only":field("boolean"),"provider":field(enum=["tencent_public","tushare"]),"persist":field("boolean")},["security_id"],self.bars,True)
        add("market_trend", "读取前复权完成日线与聚合周线，分层判断上涨、盘整、下跌或方向待确认；提供依据、相反信号、阶段行动候选与重评条件，不预测涨跌、不写入记忆", {"security_id":STR}, ["security_id"], self.market_trend)
        add("market_selection", "依次检查上涨结构、反复放量及阴线量能、指定起点收盘涨幅；可按历史截止日截取。参数为工具代理口径，不识别主力、不生成买点、不写入记忆", {"security_id":STR,"lookback":field("integer"),"volume_ratio":field("number"),"start_date":STR,"as_of":STR}, ["security_id"], self.market_selection)
        add("market_pattern", "日线识别放量上影、守位回撤与再突破，返回冻结价区、阶段与逐日依据。参数为工具观察规则，非作者原话；不识别主力，不自动下单、不写入记忆", {"security_id":STR,"volume_ratio":field("number"),"max_wait":field("integer"),"as_of":STR}, ["security_id"], self.market_pattern)
        add("market_study", "读取已完成K线，推荐自动画线模式（趋势通道、横盘区间、关键价位）并给出候选线与依据；附资金参与模式、程度及量价方向依据，不输出买卖信号、主力身份或实际净流入，不写入记忆", {"security_id":STR,"interval":field(enum=["1d","5m","15m","30m","60m"]),"count":field("integer")}, ["security_id"], self.market_study)
        add("data_validate","检查已有行情快照的价格、顺序、时间、单位；不代表核实预测",{"snapshot_id":STR},["snapshot_id"],self.data_validate)
        add("features_compute","基于有效已完成 K 线计算均线、历史区间与波动；不生成买卖信号",{"snapshot_id":STR},["snapshot_id"],self.features)
        add("chart_render","基于已保存真实行情生成可查看的 SVG 图表",{"snapshot_id":STR},["snapshot_id"],self.chart,True)
        add("risk_compute","只对显式提供的持仓与价格计算；无账户分母不输出全账户比例",{"positions":field("array",items=OBJ),"account_equity":field(["number","null"])},["positions"],risk)
        add("positions_get","返回用户自述持仓快照与成交；不自动推导为实时完整账户",{"limit":field("integer")},[],self.positions)
        add("strategy_get","读取指定时点生效的规则，没有规则返回 not_defined",{"effective_at":STR},[],self.store.strategy_get)
        add("strategy_propose","创建完整规则集合变更提案；不会自动激活",{"rules":field("array",items=OBJ),"rationale":STR,"refs":ARRAY,"key":STR},["rules","rationale","refs","key"],self.store.propose,True)
        add("rules_check","对已生效版本计算可判定规则；缺项保留 unknown",{"inputs":OBJ,"strategy_id":STR},["inputs"],self.rules)
        add("source_register","归档 Codex 已读取或用户提供的来源摘录；记录获取方式，不冒充本工具独立抓取",{"url":STR,"title":STR,"excerpt":STR,"published_at":field(["string","null"]),"retrieved_at":STR,"origin":field(enum=["codex_fetched","user_supplied"]),"key":STR},["url","title","excerpt","published_at","retrieved_at","origin","key"],self.source,True)
        add("evidence_check","检查主张引用是否存在、类型和时间边界；语义仍需 Codex 核验原文",{"claims":field("array",items=OBJ),"known_before":STR},["claims"],self.evidence)
        add("analysis_save","保存分析、证据清单与核验结果；未来判断永远不能标为已证实",{"text":STR,"claims":field("array",items=OBJ),"refs":ARRAY,"key":STR,"horizon":STR,"follow_up":OBJ},["text","claims","refs","key"],self.save_analysis,True)
        add("review_save","保存复盘并关联实际原始计划、成交和规则引用",{"text":STR,"refs":ARRAY,"key":STR},["text","refs","key"],lambda **kw:self.artifact("review",**kw),True)
        add("hypothesis_save","保存待验证假设及支持反对案例引用，不修改正式规则",{"text":STR,"refs":ARRAY,"key":STR},["text","refs","key"],lambda **kw:self.artifact("hypothesis",**kw),True)
        add("evolution_record","保存方法改变与依据，不能激活交易纪律",{"text":STR,"refs":ARRAY,"key":STR},["text","refs","key"],lambda **kw:self.artifact("evolution",**kw),True)
        add("knowledge_export","生成最近资料的可读 Markdown 视图",{},[],self.store.export,True)
        add("knowledge_backup","建立本地一致性数据库备份并检查完整性",{},[],self.store.backup,True)
        add("partner_process","单入口：接收Codex语义判断后的意图，保存和关联记录，调取记忆与四层任务；不记录模式不落盘",
            {"message_id":STR,"text":STR,"intents":field("array",items=field(enum=list(INTENTS))),"key":STR,
             "items":field("array",items=OBJ),"horizon":STR,"security_ids":ARRAY,"memory_query":STR,"classic_query":STR,"persist":field("boolean")},
            ["message_id","text","intents","key"],self.coordinator.process,True)
        add("partner_finalize","汇总已实际完成的层级结果及核验；缺层或缺证返回部分完成，不激活规则",
            {"run_id":STR,"results":field("array",items=OBJ),"text":STR,"key":STR,"claims":field("array",items=OBJ)},
            ["run_id","results","text","key"],self.coordinator.finalize,True)
        add("layer_registry","读取道法术器实际角色指令及执行方式",{},[],self.coordinator.registry)
        add("classic_search","按主题词读取公开经典知识与来源性质，不当作个人信念",
            {"query":STR,"limit":field("integer")},[],ClassicCatalog().search)
        add("learning_link","把真实个人学习原话关联到经典条目；认同、效果和规则状态分别管理",
            {"source_id":STR,"classic_id":STR,"excerpt":STR,"understanding":STR,"key":STR,"adoption":STR,"evidence":STR,"refs":ARRAY},
            ["source_id","classic_id","excerpt","understanding","key"],self.coordinator.learning_link,True)
        add("system_overview","只读四层体系、已确认规则和缺口；不初始化空业务库",{},[],self.views.overview)
        add("system_render","生成八份体系视图，保留旧导出；手工编辑冲突时停止覆盖，不修改数据库",{},[],self.views.render,True)

    def call(self,name,arguments):
        rid=uuid.uuid4().hex
        try:
            if name not in self.handlers:
                raise DomainError("UNKNOWN_TOOL", "不存在该工具")
            validate(arguments,self.specs[name]["inputSchema"])
            result=self.handlers[name](**arguments)
            # Ensure serialization cannot silently emit NaN.
            canonical(result)
            return {"status":"ok","request_id":rid,"data":result,"error":None}
        except DomainError as e:
            return {"status":"error","request_id":rid,"data":None,"error":{"code":e.code,"message":e.message,"retryable":e.retryable}}
        except (ValueError,TypeError,KeyError,IndexError) as e:
            return {"status":"error","request_id":rid,"data":None,"error":{"code":"INVALID_DATA","message":f"输入或数据格式错误：{type(e).__name__}","retryable":False}}
        except Exception as e:
            return {"status":"error","request_id":rid,"data":None,"error":{"code":"INTERNAL_ERROR","message":f"执行未完成：{type(e).__name__}","retryable":False}}

    def status(self):
        return {**self.store.status(),"version":"0.2.0","tools":list(self.specs),
                "layers":["dao","fa","shu","qi"],"coordination":"当前Codex分层执行，本地程序交接和留痕",
                "language_understanding":"由当前 Codex 对话和项目技能完成；服务不是关键词聊天机器人",
                "providers":{"tencent_public":"无需密钥；实际调用可能需要网络访问权限","tushare":"configured" if os.environ.get("TUSHARE_TOKEN") else "not_configured"},
                "not_implemented":["自动下单","常驻监控与通知","全市场自动选股","完整缠论算法","自动券商持仓同步","独立聊天模型"]}

    def _snapshot(self,data,persist):
        if not persist:
            return {"snapshot_id":None,"persisted":False,"data":data}
        obj=self.store.save_artifact("market_snapshot",data,[],uuid.uuid4().hex)
        compact={k:v for k,v in data.items() if k not in {"raw","bars"}}
        if "bars" in data:
            compact["row_count"]=len(data["bars"])
            compact["last_3_bars"]=data["bars"][-3:]
        return {"snapshot_id":obj["id"],"persisted":True,"data":compact}

    def quote(self,security_id,persist=True):
        return self._snapshot(self.public.quote(security_id),persist)

    def bars(self,security_id,interval="1d",count=160,adjustment="forward",closed_only=True,provider="tencent_public",persist=True):
        data=(self.public if provider=="tencent_public" else self.paid).bars(security_id,interval,count,adjustment,closed_only)
        data["quality"]=validate_bars(data)
        # Keep an invalid response as evidence, but never compute it as valid input.
        return self._snapshot(data,persist)

    def _bars_read(self,snapshot_id):
        obj=self.store.read(snapshot_id)
        if obj["kind"]!="market_snapshot" or "bars" not in obj["body"]:
            raise DomainError("WRONG_OBJECT", "需要行情序列快照编号")
        return obj["body"]

    def market_trend(self, security_id):
        data = self.bars(security_id, "1d", 640, adjustment="forward", closed_only=True, persist=False)["data"]
        return analyse_trend(data)

    def market_selection(self, security_id, lookback=40, volume_ratio=1.5, start_date=None, as_of=None):
        data = self.bars(security_id, "1d", 240, adjustment="none", closed_only=True, persist=False)["data"]
        return {"analysis": analyse_selection(data, lookback, volume_ratio, start_date, as_of),
                "basis": {k:data.get(k) for k in ("security_id", "source", "source_url", "fetched_at", "adjustment", "warnings")}}

    def market_pattern(self, security_id, volume_ratio=2.0, max_wait=20, as_of=None):
        data = self.bars(security_id, "1d", 240, adjustment="none", closed_only=True, persist=False)["data"]
        result = analyse_volume_pattern(data, volume_ratio, max_wait, as_of)
        return {"analysis": result, "data": {**{k:v for k,v in data.items() if k not in {"raw", "bars"}},
                "bars": [b for b in data['bars'] if b.get('is_final') and (as_of is None or stamp(b['time']) <= stamp(as_of))]}}

    def market_study(self, security_id, interval="1d", count=240):
        data = self.bars(security_id, interval, count, adjustment="none", closed_only=True, persist=False)["data"]
        activity = study(data)
        return {"data": {k:v for k,v in data.items() if k != "raw"}, "activity": activity,
                "chart_analysis": {str(n): analyse_chart(data, n) for n in (40, 80, 160)}}

    def data_validate(self,snapshot_id):
        return validate_bars(self._bars_read(snapshot_id))

    def features(self,snapshot_id):
        return {"snapshot_id":snapshot_id,**compute_features(self._bars_read(snapshot_id))}

    def chart(self,snapshot_id):
        data=self._bars_read(snapshot_id)
        path=self.store.directory/"exports"/(snapshot_id+".svg")
        atomic_text(path,chart_svg(data))
        return {"path":str(path),"snapshot_id":snapshot_id,"data_as_of":data["data_as_of"]}

    def positions(self,limit=20):
        return {"snapshots":self.store.search(kind="position",limit=limit)["records"],
                "reported_trades":self.store.search(kind="trade",limit=limit)["records"],
                "scope":"用户输入的历史快照与成交自述，未自动完成对账，不代表当前完整账户"}

    def rules(self,inputs,strategy_id=None):
        obj=self.store.read(strategy_id) if strategy_id else self.store.strategy_get()
        if obj.get("kind") not in {None,"strategy"}:
            raise DomainError("WRONG_OBJECT","只能检查已激活的规则版本")
        return {"strategy_id":obj["id"],**check_rules(obj["body"]["rules"],inputs)}

    def source(self,url,title,excerpt,published_at,retrieved_at,origin,key):
        from urllib.parse import urlparse
        if urlparse(url).scheme not in {"https","http"}:
            raise DomainError("INVALID_URL","来源需要 HTTP(S) URL")
        stamp(retrieved_at)
        if published_at: stamp(published_at)
        if not excerpt.strip(): raise DomainError("EMPTY_SOURCE","来源摘录不能为空")
        body={"url":url,"title":title,"text":excerpt,"published_at":published_at,"retrieved_at":retrieved_at,"origin":origin,
              "content_hash":digest(excerpt),"verification":"由调用方提供的实际读取摘录；工具未再次抓取网页"}
        return self.store.save_artifact("source",body,[],key)

    def evidence(self,claims,known_before=None):
        cutoff=stamp(known_before) if known_before else None
        results=[]
        for claim in claims:
            if claim.get("type") not in {"user_report","external_fact","calculation","interpretation","prediction"} or not claim.get("text"):
                raise DomainError("INVALID_CLAIM","主张需要 text 和合法 type")
            refs=claim.get("evidence_refs",[])
            issues=[]
            for ref in refs:
                try:
                    obj=self.store.read(ref)
                    if obj["status"]=="needs_recheck": issues.append("依赖资料已更正，需要复查")
                    if cutoff:
                        relevant=obj["body"].get("data_as_of") or obj["body"].get("published_at") or obj["created_at"]
                        if stamp(relevant)>cutoff: issues.append("证据晚于指定判断时点")
                    if obj["kind"]=="market_snapshot":
                        if obj["body"].get("freshness",{}).get("state") in {"unknown","invalid_future","historical_or_delayed"}:
                            issues.append("行情时效有限，不能作为已核实实时现状")
                        if "bars" in obj["body"] and not validate_bars(obj["body"])["valid"]:
                            issues.append("行情序列未通过数据检查")
                    if claim["type"]=="external_fact" and obj["kind"] not in {"source","market_snapshot"}:
                        issues.append("个人记录或模型分析不能作为外部事实的独立来源")
                except DomainError:
                    issues.append("引用不存在："+str(ref))
            if not refs: issues.append("缺少证据引用")
            state="unresolved" if issues else ("conditional" if claim["type"] in {"interpretation","prediction"} else "references_checked")
            results.append({"text":claim["text"],"type":claim["type"],"state":state,"issues":issues,"evidence_refs":refs})
        return {"claims":results,"semantic_review_required":True,"note":"工具只检查引用、类型及时间条件；Codex 仍须核对主张是否被原文及计算支持，禁止将此回执称为语义正确保证"}

    def save_analysis(self,text,claims,refs,key,horizon="unknown",follow_up=None):
        if horizon not in {"short","medium_long","unknown"}: raise DomainError("INVALID_HORIZON","周期无效")
        checked=self.evidence(claims)
        dependencies=list(dict.fromkeys(refs+[r for c in claims for r in c.get("evidence_refs",[])]))
        body={"text":text,"claims":claims,"verification":checked,"horizon":horizon,"follow_up":follow_up or {},"created_by":"codex","tool_version":"0.2.0"}
        return self.store.save_artifact("analysis",body,dependencies,key)

    def artifact(self,kind,text,refs,key):
        return self.store.save_artifact(kind,{"text":text,"refs":refs,"nature":"interpretation"},refs,key)
