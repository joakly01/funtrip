import copy
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from trading_agent.common import DomainError
from trading_agent.service import Service
from trading_agent.market import Tencent
from trading_agent.trend_analysis import analyse_trend, weekly_bars, classify


def fixture(kind='up', count=520):
    day = datetime(2024, 1, 1, 15, tzinfo=timezone(timedelta(hours=8)))
    rows = []
    while len(rows) < count:
        if day.weekday() < 5:
            i = len(rows)
            close = 100+i*.15 if kind == 'up' else 200-i*.15 if kind == 'down' else 100+round(3*math.sin(i*math.pi/10), 2)
            rows.append({'time': day.isoformat(), 'open': close, 'close': close, 'high': close+1,
                         'low': close-1, 'is_final': True})
        day += timedelta(days=1)
    return {'security_id': 'SSE:600000', 'interval': '1d', 'adjustment': 'forward',
            'adjustment_anchor': '2026-10-07', 'fetched_at': '2026-10-07T12:00:00+08:00',
            'data_as_of': rows[-1]['time'], 'source': 'isolated_fixture', 'bars': rows, 'warnings': []}


class TrendTests(unittest.TestCase):
    def test_distinct_stages_and_flat_is_not_automatically_range(self):
        for kind in ('up', 'down', 'range'):
            result = analyse_trend(fixture(kind))
            self.assertEqual(result['state'], kind)
            self.assertEqual(result['daily']['state'], kind)
            self.assertTrue(result['weekly']['evidence'])
            self.assertEqual(result['basis']['daily_count'], 520)
        flat = fixture()
        for b in flat['bars']: b.update(open=100, close=100, high=101, low=99)
        self.assertEqual(analyse_trend(flat)['state'], 'unclear')

    def test_short_history_never_borrows_daily_direction_as_big_trend(self):
        result = analyse_trend(fixture(count=180))
        self.assertEqual(result['daily']['state'], 'up')
        self.assertEqual(result['state'], 'unclear')
        self.assertLess(result['weekly']['count'], 44)
        self.assertIn('至少需要', result['weekly']['evidence'][0])

    def test_weekly_aggregation_excludes_partial_start_current_week_and_unfinished(self):
        data = fixture(count=520)
        day = datetime(2026, 10, 5, 15, tzinfo=timezone(timedelta(hours=8)))
        data['bars'] = []
        for i in range(12):
            at = day-timedelta(days=7)+timedelta(days=i)
            if at.weekday()<5:
                data['bars'].append({'time': at.isoformat(), 'open':10+i, 'high':12+i,
                    'low':9+i, 'close':11+i, 'is_final':True})
        weeks = weekly_bars(data['bars'], '2026-10-14T12:00:00+08:00')
        self.assertEqual(len(weeks), 1)
        self.assertEqual(weeks[0]['open'], 17)
        self.assertEqual(weeks[0]['close'], 22)
        self.assertEqual(weekly_bars(data['bars'], '2026-10-07T12:00:00+08:00'), [])
        original = fixture()
        extended = copy.deepcopy(original)
        extended['bars'].append({'time':'2026-10-07T12:00:00+08:00', 'open':1,'high':1,'low':1,'close':1,'is_final':False})
        self.assertEqual(analyse_trend(original)['state'], analyse_trend(extended)['state'])
        self.assertEqual(analyse_trend(extended)['basis']['daily_count'],520)

    def test_invalid_price_future_data_and_wrong_adjustment_rejected(self):
        for change in ({'close':float('nan')}, {'high':1}, {'time':'2027-01-01T15:00:00+08:00'}):
            data=fixture();data['bars'][-1].update(change)
            with self.assertRaises(DomainError): analyse_trend(data)
        data=fixture();data['adjustment']='none'
        with self.assertRaises(DomainError): analyse_trend(data)

    def test_provider_missing_adjusted_field_never_silently_substitutes_raw(self):
        import json
        payload={'data':{'sh688981':{'day':[['2026-09-30','100','101','102','99','1000']]}}}
        with patch('trading_agent.market.request',return_value=json.dumps(payload)):
            with self.assertRaises(DomainError) as failure:
                Tencent().bars('SSE:688981', '1d', 640, 'forward', True)
        self.assertEqual(failure.exception.code, 'ADJUSTED_HISTORY_UNAVAILABLE')

    def test_conflicting_structure_is_reported_as_counterevidence(self):
        result=analyse_trend(fixture('up'))
        self.assertTrue(result['weekly']['counter_evidence'])
        self.assertIn('结构',result['weekly']['counter_evidence'][0])

    def test_abnormal_gap_pauses_overall_label(self):
        data=fixture();data['bars'][-1].update(open=350, high=352, low=349, close=351)
        result=analyse_trend(data)
        self.assertEqual(result['state'],'unclear')
        self.assertTrue(any('35%' in s for s in result['weekly']['counter_evidence']))

    def test_confirmed_pivots_cannot_use_newest_two_bars(self):
        data=fixture('range')
        result=classify(data['bars'],'1d')
        newest=data['bars'][-2]['time']
        self.assertTrue(result['levels'])
        self.assertTrue(all(level['time']<newest for level in result['levels']))
        self.assertTrue(all(level['label'].startswith('前一确认') for level in result['levels']))

    def test_api_shared_tool_readonly_resolution_and_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=Config(root/'trading',root/'runtime','test-password');config.runtime_dir.mkdir()
            service=Service(config.data_dir)
            self.assertTrue(service.specs['market_trend']['annotations']['readOnlyHint'])
            with patch('trading_agent.market.Tencent.bars', return_value=fixture()) as provider:
                r=service.call('market_trend',{'security_id':'SSE:600000'})
                self.assertEqual(r['data']['state'],'up')
                self.assertEqual(provider.call_args.args, ('SSE:600000','1d',640,'forward',True))
            self.assertFalse((config.data_dir/'knowledge.sqlite').exists())
            with patch('trading_agent.market.Tencent.resolve', return_value={'resolved':None,'candidates':[{'security_id':'SSE:600000','name':'候选股票'}]}), TestClient(create_app(config)) as client:
                self.assertEqual(client.get('/api/tools/trend?security_id=SSE:600000').status_code,401)
                client.post('/api/login',json={'password':'test-password'})
                self.assertEqual(client.get('/api/tools/trend').status_code,400)
                self.assertEqual(client.get('/api/tools/trend?security_id=bad').status_code,422)
                with patch('trading_agent.market.Tencent.bars', return_value=fixture()):
                    r=client.get('/api/tools/trend?security_id=SSE:600000')
                    self.assertEqual(r.status_code,200)
                    self.assertEqual(r.json()['analysis']['state'],'up')
                with patch('trading_agent.market.Tencent.resolve', return_value={'resolved':None,'candidates':[{'security_id':'SSE:600000','name':'候选股票'}]}):
                    r=client.get('/api/tools/trend?query=候选')
                    self.assertIsNone(r.json()['analysis'])
                    self.assertEqual(len(r.json()['candidates']),1)
                with patch('trading_agent.market.Tencent.bars', side_effect=DomainError('PROVIDER_ERROR','行情暂时不可用')):
                    r=client.get('/api/tools/trend?security_id=SSE:600000')
                    self.assertEqual(r.status_code,400)
                    self.assertEqual(r.json()['detail'],'行情暂时不可用')
