import copy
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from trading_agent.common import DomainError, TZ, now
from trading_agent.store import Store
from trading_agent.service import Service
from trading_agent.analysis import validate_bars, compute_features, check_rules, risk, chart_svg
from trading_agent.market import Tencent, Tushare, age_status
from trading_agent.mcp import run


def series(n=65):
    start=datetime(2025,1,1,15,tzinfo=TZ)
    rows=[]
    for i in range(n):
        c=100+i
        rows.append({"time":(start+timedelta(days=i)).isoformat(),"open":c-.5,"high":c+1,"low":c-1,"close":c,
                     "volume":1000+i,"is_final":True})
    return {"security_id":"SSE:600000","source":"test_fixture","interval":"1d","adjustment":"forward",
            "bars":rows,"data_as_of":rows[-1]["time"],"fetched_at":now(),"volume_unit":"share","warnings":[]}


def record(key="trade",kind="trade"):
    return {"item_key":key,"kind":kind,"text":"用户陈述已买入，价格未知","excerpt":"买了 100 股",
            "nature":"user_report","tags":["追高"],"security_ids":["SSE:600000"],"horizon":"short",
            "fields":{"execution_status":"reported_executed","side":"buy","quantity":100,"price":None}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.s=Store(Path(self.tmp.name)/"data")
        self.source=self.s.capture("message1","买了 100 股，有些后悔")['source_id']

    def saved(self):
        return self.s.commit(self.source,[record()],"one")["records"][0]

    def test_capture_idempotency(self):
        self.assertEqual(self.source,self.s.capture("message1","买了 100 股，有些后悔")["source_id"])
        with self.assertRaises(DomainError): self.s.capture("message1","不同内容")

    def test_no_record_creates_no_directory(self):
        path=Path(self.tmp.name)/"private"
        result=Store(path).capture("private","不记录",False)
        self.assertFalse(result["saved"]);self.assertFalse(path.exists())

    def test_retry_items_does_not_duplicate_trade(self):
        obj=self.saved()
        again=self.s.commit(self.source,[record()],"another-request")
        self.assertEqual(obj["id"],again["records"][0]["id"])
        self.assertEqual(len(self.s.search(kind="trade")["records"]),1)

    def test_changed_retry_conflicts(self):
        self.saved();item=record();item["fields"]["quantity"]=200
        with self.assertRaises(DomainError): self.s.commit(self.source,[item],"new")

    def test_plan_cannot_be_reported_trade(self):
        item=record();item["fields"]["execution_status"]="planned"
        with self.assertRaises(DomainError): self.s.commit(self.source,[item],"plan")

    def test_mixed_intents_share_original(self):
        emotion={"item_key":"emotion","kind":"emotion","nature":"user_report","text":"后悔","excerpt":"后悔"}
        result=self.s.commit(self.source,[record(),emotion],"mixed")
        self.assertEqual(len(result["records"]),2)
        self.assertEqual(self.s.status()["counts"]["raw"],1)

    def test_excerpt_must_exist_and_batch_atomic(self):
        bad=record("bad");bad["excerpt"]="并不存在的原文"
        with self.assertRaises(DomainError): self.s.commit(self.source,[record(),bad],"batch")
        self.assertEqual(self.s.search(kind="trade")["records"],[])

    def test_amend_preserves_version_and_marks_dependents(self):
        obj=self.saved()
        a=self.s.save_artifact("analysis",{"text":"依赖成交数量的分析"},[obj["id"]],"a")
        b=self.s.save_artifact("review",{"text":"依赖分析的复盘"},[a["id"]],"b")
        correction=self.s.capture("correction","刚才是 200 股")["source_id"]
        out=self.s.amend(obj["id"],{"fields":{"quantity":200}},"用户更正",correction,1,"fix")
        self.assertEqual(out["record"]["body"]["fields"]["quantity"],200)
        self.assertEqual(self.s.read(obj["id"],1)["body"]["fields"]["quantity"],100)
        self.assertIn(correction,self.s.read(obj["id"])["reference_ids"])
        self.assertIn(b["id"],out["needs_recheck"])
        self.assertEqual(self.s.read(a["id"])["status"],"needs_recheck")
        self.assertEqual(len(self.s.search(kind="trade")["records"]),1)

    def test_stale_update_rejected(self):
        obj=self.saved();src=self.s.capture("c","更正")["source_id"]
        self.s.amend(obj["id"],{"text":"更正"},"更正",src,1,"fix")
        with self.assertRaises(DomainError):self.s.amend(obj["id"],{"text":"覆盖"},"更正",src,1,"fix2")

    def test_original_is_immutable(self):
        with self.assertRaises(DomainError):self.s.amend(self.source,{"text":"改写"},"x",self.source,1,"x")

    def test_search_uses_past_revision(self):
        obj=self.saved(); cutoff=now(); src=self.s.capture("c","更正")["source_id"]
        self.s.amend(obj["id"],{"text":"只在后来知道的信息"},"later",src,1,"later")
        past=self.s.search(kind="trade",known_before=cutoff)["records"][0]
        self.assertEqual(past["version"],1)
        self.assertNotIn("后来",past["body"]["text"])

    def test_rule_confirmation_and_stale_proposal(self):
        self.assertEqual(self.s.strategy_get()["status"],"not_defined")
        p=self.s.propose([{"id":"rule","text":"用户自己的规则"}],"测试",[],"p")
        other=self.s.propose([],"另一项",[],"p2")
        with self.assertRaises(DomainError): self.s.activate(p["id"],p["body"]["digest"],self.source)
        source=self.s.capture("confirmation",f"确认 {p['id']} {p['body']['digest']}")["source_id"]
        active=self.s.activate(p["id"],p["body"]["digest"],source)
        self.assertEqual(self.s.strategy_get()["id"],active["id"])
        s2=self.s.capture("confirmation2",f"确认 {other['id']} {other['body']['digest']}")["source_id"]
        with self.assertRaises(DomainError): self.s.activate(other["id"],other["body"]["digest"],s2)

    def test_export_backup_can_restore(self):
        obj=self.saved();self.assertTrue(Path(self.s.export()["path"]).exists())
        backup=self.s.backup()
        with sqlite3.connect(backup["path"]) as con:
            row=con.execute("SELECT body FROM objects WHERE id=?",(obj["id"],)).fetchone()
        self.assertEqual(json.loads(row[0])["fields"]["quantity"],100)

    def test_negated_confirmation_cannot_activate(self):
        p=self.s.propose([{"id":"r","text":"测试规则"}],"test",[],"p")
        c=self.s.capture("deny",f"不要确认 {p['id']} {p['body']['digest']}")["source_id"]
        with self.assertRaises(DomainError):self.s.activate(p["id"],p["body"]["digest"],c)


class CalculationTests(unittest.TestCase):
    def test_golden_features(self):
        out=compute_features(series())["features"]
        self.assertEqual(out["sma_5"],162)
        self.assertEqual(out["sma_20"],154.5)
        self.assertEqual(out["prior_20_high"],164)
        self.assertEqual(out["atr_14_simple"],2)
        self.assertAlmostEqual(out["return_20_bars"],164/144-1)

    def test_unfinished_not_in_indicators(self):
        data=series(); data["bars"][-1]["is_final"]=False
        self.assertEqual(compute_features(data)["features"]["last_closed"],163)

    def test_invalid_ohlc_blocked(self):
        data=series();data["bars"][0]["high"]=1
        with self.assertRaises(DomainError):compute_features(data)

    def test_duplicate_and_future_bars_blocked(self):
        for issue in ("duplicate","future"):
            data=series()
            data["bars"][-1]["time"]=data["bars"][-2]["time"] if issue=="duplicate" else "2099-01-01T15:00:00+08:00"
            self.assertFalse(validate_bars(data)["valid"])

    def test_unknown_units_no_volume_claim(self):
        data=series();data["volume_unit"]="unknown"
        self.assertIsNone(compute_features(data)["features"]["volume_ratio_20"])

    def test_nan_cannot_pass(self):
        data=series();data["bars"][0]["close"]=float("nan")
        self.assertFalse(validate_bars(data)["valid"])

    def test_rules_missing_is_unknown(self):
        rules=[{"id":"r","text":"限制","condition":{"field":"weight","operator":"lte","value":.3}}]
        self.assertEqual(check_rules(rules,{})["overall"],"unknown")
        self.assertEqual(check_rules(rules,{"weight":.4})["overall"],"conflict")
        self.assertEqual(check_rules([], {})["overall"],"not_defined")

    def test_risk_unknown_denominator(self):
        result=risk([{"security_id":"SSE:600000","quantity":100,"price":10,"scenario_price":9}])
        self.assertEqual(result["known_market_value"],1000)
        self.assertIsNone(result["account_weight"])
        self.assertEqual(result["positions"][0]["scenario_pnl"],-100)

    def test_svg_uses_data_escapes_text(self):
        import xml.etree.ElementTree as ET
        data=series();data["source"]="<test&source>"
        svg=chart_svg(data);ET.fromstring(svg)
        self.assertIn("&lt;test&amp;source&gt;",svg)


class ProviderTests(unittest.TestCase):
    def quote_fields(self,name="浦发银行",code="600000"):
        f=[""]*90
        for i,v in {1:name,2:code,3:"9.2",4:"9.16",5:"9.15",30:"20260929133042",33:"9.25",34:"9.09",60:"   A",61:"GP-A"}.items():f[i]=v
        return f

    def test_identity_verified_not_prefix_guessed(self):
        p=Tencent()
        with patch.object(p,"raw_quotes",return_value={"sh600000":self.quote_fields()}):
            self.assertEqual(p.resolve("600000")["resolved"]["security_id"],"SSE:600000")

    def test_ambiguous_identity_not_selected(self):
        p=Tencent()
        with patch.object(p,"raw_quotes",return_value={"sh600000":self.quote_fields(),"sz600000":self.quote_fields("其他") }):
            self.assertIsNone(p.resolve("600000")["resolved"])

    def test_index_not_resolved_as_stock(self):
        p=Tencent(); f=self.quote_fields();f[61]="ZS"
        with patch.object(p,"raw_quotes",return_value={"sh600000":f}):
            self.assertIsNone(p.resolve("600000")["resolved"])

    def test_chinext_and_star_identity_and_quote(self):
        p = Tencent()
        for symbol, code, name, flags, kind, identity in [
            ('sz300357', '300357', '我武生物', ' A A', 'GP-A-CYB', 'SZSE:300357'),
            ('sh688981', '688981', '中芯国际', 'A RA', 'GP-A-KCB', 'SSE:688981'),
        ]:
            fields = self.quote_fields(name, code)
            fields[60], fields[61] = flags, kind
            with self.subTest(board=kind), patch.object(p, 'raw_quotes', return_value={symbol: fields}):
                self.assertEqual(p.resolve(code)['resolved']['security_id'], identity)
                self.assertEqual(p.quote(identity)['name'], name)
                with patch('trading_agent.market.request', return_value=f'v="{symbol[:2]}~{code}~{name}~{code}";'):
                    self.assertEqual(p.resolve(name)['resolved']['security_id'], identity)
        for kind in ['ZS', 'JJ', 'GP-B', 'GP-A-UNKNOWN']:
            fields = self.quote_fields()
            fields[61] = kind
            with self.subTest(kind=kind), patch.object(p, 'raw_quotes', return_value={'sh600000': fields}):
                self.assertIsNone(p.resolve('600000')['resolved'])
                with self.assertRaises(DomainError): p.quote('SSE:600000')

    def test_stale_and_future_timestamps(self):
        self.assertEqual(age_status("2020-01-01T09:30:00+08:00",now())["state"],"historical_or_delayed")
        self.assertEqual(age_status("2099-01-01T09:30:00+08:00",now())["state"],"invalid_future")
        self.assertEqual(age_status(None,now())["state"],"unknown")

    def test_no_silent_adjustment_fallback(self):
        payload={"data":{"sh600000":{"day":[["2025-01-01","1","1","1","1","1"]]}}}
        with patch("trading_agent.market.request",return_value=json.dumps(payload)):
            with self.assertRaises(DomainError):Tencent().bars("SSE:600000")

    def test_missing_credentials_not_faked(self):
        with patch.dict("os.environ",{},clear=True):
            with self.assertRaises(DomainError):Tushare().api("daily",{})


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=Service(Path(self.tmp.name)/"data")

    def test_schema_rejects_unknown_argument(self):
        out=self.s.call("inbox_capture",{"message_id":"m","text":"x","approved":True})
        self.assertEqual(out["status"],"error")

    def test_rule_activation_not_exposed(self):
        self.assertNotIn("strategy_activate",self.s.specs)
        self.assertEqual(self.s.call("strategy_activate",{})["status"],"error")

    def test_market_no_save_is_ephemeral(self):
        with patch.object(self.s.public,"bars",return_value=series()):
            out=self.s.call("market_bars",{"security_id":"SSE:600000","persist":False})
        self.assertEqual(out["status"],"ok")
        self.assertFalse(self.s.store.directory.exists())

    def test_market_failure_keeps_diary(self):
        self.s.call("inbox_capture",{"message_id":"m","text":"今天的心得"})
        with patch.object(self.s.public,"quote",side_effect=DomainError("TIMEOUT","超时",True)):
            result=self.s.call("market_quote",{"security_id":"SSE:600000"})
        self.assertEqual(result["status"],"error")
        self.assertEqual(self.s.store.status()["counts"]["raw"],1)

    def test_missing_evidence_unresolved_prediction_conditional(self):
        src=self.s.store.capture("m","用户观点")["source_id"]
        out=self.s.evidence([{"text":"以后上涨","type":"prediction","evidence_refs":[src]},
                             {"text":"已经公告","type":"external_fact","evidence_refs":[src]}])
        self.assertEqual(out["claims"][0]["state"],"conditional")
        self.assertEqual(out["claims"][1]["state"],"unresolved")
        self.assertTrue(out["semantic_review_required"])

    def test_snapshot_to_chart_features_analysis(self):
        with patch.object(self.s.public,"bars",return_value=series()):
            snap=self.s.bars("SSE:600000")["snapshot_id"]
        self.assertEqual(self.s.features(snap)["features"]["sma_5"],162)
        self.assertTrue(Path(self.s.chart(snap)["path"]).exists())
        obj=self.s.save_analysis("测试分析",[{"text":"存在上升观察","type":"interpretation","evidence_refs":[snap]}],[snap],"a")
        self.assertEqual(obj["kind"],"analysis")

    def test_mcp_handshake_list_call(self):
        requests=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}},
                  {"jsonrpc":"2.0","method":"notifications/initialized"},
                  {"jsonrpc":"2.0","id":2,"method":"tools/list"},
                  {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"inbox_capture","arguments":{"message_id":"x","text":"私密","persist":False}}}]
        output=io.StringIO();run(self.s,io.StringIO("\n".join(json.dumps(x) for x in requests)),output)
        result=[json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(len(result),3)
        self.assertIn("tools",result[1]["result"])
        self.assertFalse(result[2]["result"]["isError"])
        self.assertFalse(self.s.store.directory.exists())


if __name__=="__main__":unittest.main()
