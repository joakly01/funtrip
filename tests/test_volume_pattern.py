import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from trading_agent.service import Service
from trading_agent.common import DomainError
from trading_agent.volume_pattern import analyse_volume_pattern


def fixture():
    start=datetime(2026,8,1,15,tzinfo=timezone(timedelta(hours=8)))
    bars=[dict(time=(start+timedelta(days=i)).isoformat(),open=10,high=10.4,low=9.8,close=10.2,volume_raw=100.,is_final=True) for i in range(30)]
    bars[20].update(open=10.2,high=11,low=10,close=10.5,volume_raw=300.)
    for i in range(21,30):bars[i].update(open=10.5,high=10.8,low=10.2,close=10.6,volume_raw=60.)
    bars[24].update(open=10.7,high=11.5,low=10.6,close=11.3,volume_raw=150.)
    for i in range(25,30):bars[i].update(open=11.3,high=11.6,low=11.1,close=11.4,volume_raw=100.)
    return dict(security_id='SSE:600000',bars=bars,interval='1d',adjustment='none',source='isolated_fixture',source_url='https://example.invalid',fetched_at='2026-10-07T15:00:00+08:00',volume_unit='unverified_provider_native',warnings=[],data_as_of=bars[-1]['time'])


class VolumePatternTests(unittest.TestCase):
    def test_time_sequence_and_known_anchors(self):
        d=fixture(); f=lambda i: analyse_volume_pattern(d,as_of=d['bars'][i]['time'])['events'][0]
        self.assertEqual(f(20)['state'],'event')
        self.assertEqual(f(23)['state'],'holding')
        self.assertEqual(f(24)['state'],'breakout_pending')
        self.assertEqual(f(25)['state'],'confirmed')
        self.assertEqual(f(29)['levels'],f(20)['levels'])
        self.assertEqual(f(20)['levels']['support'],10)
        self.assertEqual(f(20)['levels']['pressure'],11)
        self.assertAlmostEqual(f(20)['volume_ratio'],3)
        self.assertTrue(all(t['time'] <= d['bars'][24]['time'] for t in f(24)['trace']))

    def test_future_does_not_change_past_even_if_invalid(self):
        d=fixture();cut=d['bars'][23]['time'];before=analyse_volume_pattern(d,as_of=cut)
        for b in d['bars'][24:]:b.update(close=-3,high=-1,volume_raw=None)
        self.assertEqual(before,analyse_volume_pattern(d,as_of=cut))

    def test_failures_expiry_and_failed_breakout_kept(self):
        d=fixture();d['bars'][23].update(open=10.4,high=10.5,low=9.5,close=9.6)
        r=analyse_volume_pattern(d)['events'][0]
        self.assertEqual(r['state'],'failed');self.assertEqual(len(r['trace']),4)
        d=fixture()
        for b in d['bars'][24:]:b.update(open=10.5,high=10.8,low=10.2,close=10.6,volume_raw=60.)
        self.assertEqual(analyse_volume_pattern(d,max_wait=5)['events'][0]['state'],'expired')
        d=fixture();d['bars'][25].update(open=11.2,high=11.3,low=10.5,close=10.6)
        self.assertEqual(analyse_volume_pattern(d)['events'][0]['state'],'failed')

    def test_completion_units_bad_volume_gaps_and_parameters(self):
        d=fixture();d['bars'][20]['is_final']=False
        self.assertFalse(any(e['time']==d['bars'][20]['time'] for e in analyse_volume_pattern(d)['events']))
        for v in [None,0,-1,float('nan'),float('inf'),True]:
            d=fixture();d['bars'][3]['volume_raw']=v
            self.assertFalse(analyse_volume_pattern(d)['available'])
        d=fixture();d['bars'][2]['volume']=d['bars'][2].pop('volume_raw')
        self.assertFalse(analyse_volume_pattern(d)['available'])
        d=fixture()
        for b in d['bars'][5:]:
            for key in ['open','high','low','close']:b[key]*=2
        self.assertFalse(analyse_volume_pattern(d)['available'])
        d=fixture();d['interval']='5m';self.assertFalse(analyse_volume_pattern(d)['available'])
        for args in [dict(volume_ratio=True),dict(volume_ratio=6),dict(max_wait=3),dict(max_wait=20.5)]:
            with self.assertRaises(DomainError):analyse_volume_pattern(fixture(),**args)
        d=fixture();before=analyse_volume_pattern(d)
        for b in d['bars']:b['volume_raw']*=1000
        self.assertEqual(before,analyse_volume_pattern(d))

    def test_direct_rally_is_not_consolidation_and_bearish_bars_are_distinct(self):
        d=fixture()
        for b in d['bars'][21:]:b.update(open=11.2,high=11.7,low=11.1,close=11.5,volume_raw=150.)
        self.assertNotEqual(analyse_volume_pattern(d)['events'][0]['state'],'confirmed')
        d=fixture();d['bars'][21].update(open=10.8,high=10.8,low=10.1,close=10.5)
        d['bars'][22].update(open=10.6,high=10.8,low=10.0,close=10.3,volume_raw=150.)
        trace=analyse_volume_pattern(d,as_of=d['bars'][22]['time'])['events'][0]['trace']
        self.assertEqual(trace[-1]['bearish_streak'],2)
        self.assertEqual(trace[-1]['down_streak'],1)
        self.assertTrue(trace[-1]['support_test'])
        self.assertIn('放量回撤',trace[-1]['observation'])
        self.assertFalse(trace[0]['support_test'])

    def test_readonly_shared_tool_api_and_point_in_time_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=Config(root/'data',root/'runtime','test-password');cfg.runtime_dir.mkdir()
            s=Service(cfg.data_dir)
            with patch('trading_agent.market.Tencent.bars',return_value=fixture()):
                result=s.call('market_pattern',dict(security_id='SSE:600000',as_of=fixture()['bars'][23]['time']))
                self.assertEqual(result['status'],'ok');self.assertEqual(len(result['data']['data']['bars']),24)
            self.assertFalse((cfg.data_dir/'knowledge.sqlite').exists())
            with TestClient(create_app(cfg)) as c:
                self.assertEqual(c.get('/api/tools/pattern?security_id=SSE:600000').status_code,401)
                c.post('/api/login',json={'password':'test-password'})
                with patch('trading_agent.market.Tencent.bars',return_value=fixture()):
                    r=c.get('/api/tools/pattern',params={'security_id':'SSE:600000','as_of':fixture()['bars'][24]['time']})
                    self.assertEqual(r.status_code,200);self.assertEqual(r.json()['analysis']['events'][0]['state'],'breakout_pending')
                    self.assertEqual(len(r.json()['data']['bars']),25)
                self.assertEqual(c.get('/api/tools/pattern?security_id=SSE:600000&max_wait=2').status_code,422)
