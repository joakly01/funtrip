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
from trading_agent.selection_check import analyse_selection


def fixture():
    start=datetime(2026,5,1,15,tzinfo=timezone(timedelta(hours=8)))
    bars=[]
    for i in range(100):
        close=10+i*.02
        bars.append(dict(time=(start+timedelta(days=i)).isoformat(),open=close-.01,close=close,high=close+.1,low=close-.1,volume_raw=100.,is_final=True))
    for i in [65,66,80,81]:bars[i]['volume_raw']=200.
    for i in [72,76]:bars[i]['open']=bars[i]['close']+.01;bars[i]['volume_raw']=70.
    return dict(security_id='SSE:600000',bars=bars,interval='1d',adjustment='none',source='isolated_fixture',source_url='https://example.invalid',fetched_at='2026-10-07T15:00:00+08:00',volume_unit='unverified_provider_native',warnings=[],data_as_of=bars[-1]['time'])


class SelectionCheckTests(unittest.TestCase):
    def test_groups_not_single_spike_and_unknown_start(self):
        d=fixture();a=analyse_selection(d)
        self.assertTrue(a['available']);self.assertEqual(a['checks'][0]['state'],'pass')
        self.assertEqual(a['checks'][1]['groups'],2);self.assertEqual(a['checks'][1]['state'],'pass')
        self.assertEqual(a['checks'][2]['state'],'unknown');self.assertIsNone(a['checks'][2]['gain'])
        self.assertEqual(a['state'],'unknown')
        for b in d['bars'][70:]:b['volume_raw']=100.
        self.assertEqual(analyse_selection(d)['checks'][1]['groups'],1)
        self.assertEqual(analyse_selection(d)['checks'][1]['state'],'watch')

    def test_gain_manual_anchor_not_profit_share_and_twenty_boundary(self):
        d=fixture();start=d['bars'][60]['time'][:10]
        a=analyse_selection(d,start_date=start)
        self.assertAlmostEqual(a['checks'][2]['gain'],d['bars'][-1]['close']/d['bars'][60]['close']-1)
        self.assertEqual(a['state'],'pass')
        d['bars'][-1].update(open=12,high=12.1,low=11.9,close=12)
        self.assertEqual(analyse_selection(d,start_date=d['bars'][0]['time'][:10])['checks'][2]['state'],'watch')
        with self.assertRaises(DomainError):analyse_selection(d,start_date='2026-01-01')
        with self.assertRaises(DomainError):analyse_selection(d,start_date=d['bars'][-1]['time'][:10])

    def test_cutoff_future_independence_and_unfinished_exclusion(self):
        d=fixture();cut=d['bars'][85]['time'][:10];a=analyse_selection(d,as_of=cut)
        for b in d['bars'][86:]:b.update(high=-1,close=-2,volume_raw=None)
        self.assertEqual(a,analyse_selection(d,as_of=cut))
        d=fixture();d['bars'][-1]['is_final']=False
        self.assertEqual(analyse_selection(d)['as_of'],d['bars'][-2]['time'])
        self.assertTrue(all(p['known_at'][:10] <= cut for p in a['checks'][0]['highs']+a['checks'][0]['lows']))

    def test_confirmed_pivots_are_delayed_and_break_invalidates(self):
        import math
        d=fixture()
        for i,b in enumerate(d['bars']):
            close=10+i*.02+math.sin(i*.7)*.3
            b.update(open=close-.02,close=close,high=close+.1,low=close-.1)
        a=analyse_selection(d,as_of=d['bars'][85]['time'][:10]);c=a['checks'][0]
        self.assertEqual(len(c['highs']),2);self.assertEqual(len(c['lows']),2)
        for p in c['highs']+c['lows']:
            index=next(i for i,b in enumerate(d['bars']) if b['time']==p['time'])
            self.assertEqual(p['known_at'],d['bars'][index+2]['time'])
        latest=analyse_selection(d)['checks'][0]['lows'][-1]['price']-.01
        d['bars'][-1].update(open=latest,close=latest,high=latest+.1,low=latest-.1)
        self.assertEqual(analyse_selection(d)['checks'][0]['state'],'watch')

    def test_volume_units_missing_bearish_vs_daily_return(self):
        d=fixture();a=analyse_selection(d)
        obs=a['checks'][1]['observations']
        self.assertTrue(any(o['bearish'] and o['rising'] for o in obs))
        d['bars'][72]['volume_raw']=250.
        self.assertEqual(analyse_selection(d)['checks'][1]['state'],'watch')
        for v in [None,0,-1,True,float('nan')]:
            d=fixture();d['bars'][65]['volume_raw']=v
            self.assertFalse(analyse_selection(d)['available'])
        d=fixture();d['bars'][65]['volume']=d['bars'][65].pop('volume_raw')
        self.assertFalse(analyse_selection(d)['available'])
        d=fixture();a=analyse_selection(d)
        for b in d['bars']:b['volume_raw']*=1000
        self.assertEqual(a,analyse_selection(d))

    def test_parameters_and_gap(self):
        for args in [dict(lookback=True),dict(lookback=40.0),dict(lookback=30),dict(volume_ratio=True),dict(volume_ratio=7),dict(as_of='yesterday')]:
            with self.assertRaises(DomainError):analyse_selection(fixture(),**args)
        d=fixture()
        for b in d['bars'][90:]:
            for key in ['open','high','low','close']:b[key]*=2
        self.assertFalse(analyse_selection(d)['available'])
        self.assertFalse(analyse_selection({**fixture(),'bars':fixture()['bars'][:45]})['available'])

    def test_shared_readonly_api_and_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=Config(root/'data',root/'runtime','test-password');cfg.runtime_dir.mkdir()
            s=Service(cfg.data_dir)
            with patch('trading_agent.market.Tencent.bars',return_value=fixture()):
                r=s.call('market_selection',{'security_id':'SSE:600000'})
                self.assertEqual(r['status'],'ok');self.assertTrue(r['data']['analysis']['available'])
            self.assertFalse((cfg.data_dir/'knowledge.sqlite').exists())
            with TestClient(create_app(cfg)) as c:
                self.assertEqual(c.get('/api/tools/selection?security_id=SSE:600000').status_code,401)
                c.post('/api/login',json={'password':'test-password'})
                with patch('trading_agent.market.Tencent.bars',return_value=fixture()):
                    r=c.get('/api/tools/selection',params={'security_id':'SSE:600000','as_of':fixture()['bars'][85]['time'][:10], 'lookback':40, 'volume_ratio':1.5})
                    self.assertEqual(r.status_code,200);self.assertEqual(len(r.json()['analysis']['dates']),86)
                self.assertEqual(c.get('/api/tools/selection?security_id=SSE:600000&lookback=30').status_code,422)
