import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from trading_agent.common import DomainError
from trading_agent.market_study import study
from trading_agent.service import Service


def fixture():
    start = datetime(2026, 8, 1, 15, tzinfo=timezone(timedelta(hours=8)))
    rows = [{'time':(start+timedelta(days=i)).isoformat(), 'open':10+i, 'high':12+i, 'low':9+i,
             'close':11+i, 'volume_raw':100.0, 'is_final':True} for i in range(25)]
    return {'security_id':'SSE:600000','bars':rows, 'fetched_at':'2026-10-06T12:00:00+08:00',
            'data_as_of':rows[-1]['time'], 'interval':'1d','adjustment':'none',
            'excluded_unfinished':0,'source':'isolated_fixture','source_url':'https://example.invalid',
            'volume_unit':'unverified_provider_native','warnings':[]}


class MarketStudyTests(unittest.TestCase):
    def test_independent_expected_values_and_unit_scale_invariance(self):
        data=fixture()
        data['bars'][-1]['volume_raw']=200
        result=study(data)
        self.assertEqual(result['volume_ratio_20'],2)
        self.assertAlmostEqual(result['cmf_20'],1/3)
        self.assertEqual(result['up_volume_share_20'],1)
        scaled=copy.deepcopy(data)
        for b in scaled['bars']:b['volume_raw']*=100
        other=study(scaled)
        for key in ['volume_ratio_20','cmf_20','up_volume_share_20']:
            self.assertAlmostEqual(result[key],other[key])
        self.assertNotIn('net_inflow',result)

    def test_invalid_or_missing_volume_never_becomes_inflow(self):
        for volume in [None,-1,float('nan'),float('inf')]:
            data=fixture();data['bars'][7]['volume_raw']=volume
            result=study(data)
            self.assertFalse(result['volume_available'])
            self.assertIsNone(result['cmf_20'])
        data=fixture()
        for b in data['bars']:b['volume_raw']=0
        self.assertFalse(study(data)['volume_available'])

    def test_flat_bars_unfinished_and_invalid_price(self):
        data=fixture()
        for b in data['bars']:b.update(open=10,high=10,low=10,close=10)
        self.assertEqual(study(data)['cmf_20'],0)
        self.assertEqual(study(data)['up_volume_share_20'],0)
        data['bars'][-1]['is_final']=False
        self.assertEqual(study(data)['count'],24)
        data['bars'][0]['high']=9
        with self.assertRaises(DomainError):study(data)
        short=fixture();short['bars']=short['bars'][:10]
        self.assertIsNone(study(short)['cmf_20'])

    def test_service_is_read_only_and_api_drawings_restore_conflict_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=Config(root/'trading',root/'runtime','test-password')
            config.runtime_dir.mkdir()
            service=Service(config.data_dir)
            with patch('trading_agent.market.Tencent.bars',return_value=fixture()):
                self.assertEqual(service.call('market_study',{'security_id':'SSE:600000'})['status'],'ok')
            self.assertFalse((config.data_dir/'knowledge.sqlite').exists())
            with TestClient(create_app(config)) as c:
                self.assertEqual(c.get('/api/tools/study?security_id=SSE:600000').status_code,401)
                self.assertEqual(c.get('/api/tools/drawings?security_id=SSE:600000').status_code,401)
                c.post('/api/login',json={'password':'test-password'})
                with patch('trading_agent.market.Tencent.bars',return_value=fixture()):
                    result=c.get('/api/tools/study?security_id=SSE:600000')
                    self.assertEqual(result.status_code,200)
                    self.assertEqual(result.json()['activity']['volume_ratio_20'],1)
                    self.assertEqual(set(result.json()['chart_analysis']), {'40','80','160'})
                    self.assertEqual(result.json()['chart_analysis']['80']['recommended'], 'trend')
                self.assertEqual(c.get('/api/tools/study?security_id=wrong').status_code,422)
                url='/api/tools/drawings?security_id=SSE:600000&interval=1d'
                self.assertEqual(c.get(url).json()['version'],0)
                self.assertFalse((config.runtime_dir/'chart-annotations.sqlite').exists())
                body={'security_id':'SSE:600000','interval':'1d','expected_version':0,'drawings':[{'id':'line1','kind':'horizontal','label':'测试标注','points':[{'time':'2026-08-15T15:00:00+08:00','price':10.5}]}]}
                body['drawings'][0]['origin'] = {'algorithm_version':'chart-pattern-v1','mode':'levels','lookback':80,'as_of':'2026-08-25T15:00:00+08:00'}
                self.assertEqual(c.put('/api/tools/drawings',json=body).status_code,200)
                self.assertEqual(c.get(url).json()['drawings'],body['drawings'])
                self.assertEqual(c.put('/api/tools/drawings',json=body).status_code,409)
                self.assertEqual(c.get('/api/tools/drawings?security_id=SZSE:000001').json()['drawings'],[])
                self.assertEqual(c.get('/api/tools/drawings?security_id=SSE:600000&interval=30m').json()['drawings'],[])
                body['expected_version']=1;body['drawings'][0]['kind']='trend'
                self.assertEqual(c.put('/api/tools/drawings',json=body).status_code,400)
                self.assertEqual(c.get(url).json()['version'],1)
                body['drawings']=[]
                self.assertEqual(c.put('/api/tools/drawings',json=body).status_code,200)
                self.assertEqual(c.get(url).json()['drawings'],[])
