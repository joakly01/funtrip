import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from cloud_trading.knowledge import KnowledgeLibrary
from cloud_trading.workspace import system_workspace, rule_form, check_current_rules, SKILLS, TOOLS
from trading_agent.common import DomainError
from trading_agent.store import Store
from trading_agent.service import Service


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config(self.root / 'trading', self.root / 'runtime', 'test-password')
        self.config.runtime_dir.mkdir()
        self.store = Store(self.config.data_dir)
        self.knowledge = KnowledgeLibrary(self.store, self.config.runtime_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        with self.store.db() as db:
            raw = self.store._put(db, 'raw', {'text': '隔离测试原话'})
            summary = self.store._put(db, 'analysis', {'text': '# 风险成果\n这是整理结果，不是预测保证。'}, [raw['id']])
            candidate = self.store._put(db, 'hypothesis', {'text': '候选方法', 'layers': ['dao', 'shu']}, [raw['id']])
            review = self.store._put(db, 'review', {'text': '案例复盘'}, [raw['id']], status='needs_recheck')
        return raw, summary, candidate, review

    def test_results_default_and_curation_never_modify_business_records(self):
        raw, summary, candidate, review = self.seed()
        before = (self.config.data_dir / 'knowledge.sqlite').read_bytes()
        first = self.knowledge.browse()
        self.assertEqual(first['total'], 2)
        self.assertEqual(first['shelves'], {'outcomes': 2, 'workbench': 1, 'references': 1})
        self.assertFalse(self.knowledge.path.exists())
        self.knowledge.organize(candidate['id'], 'outcomes', 'risk', True)
        fresh = KnowledgeLibrary(self.store, self.config.runtime_dir)
        picked = fresh.browse(topic='risk', starred=True)['records']
        self.assertEqual([r['id'] for r in picked], [candidate['id']])
        self.assertEqual(picked[0]['states']['adoption'], 'pending')
        self.assertEqual(picked[0]['states']['evidence'], 'unverified')
        self.assertEqual(picked[0]['states']['rule'], 'not_applicable')
        self.assertEqual(self.store.active_snapshot()['id'], None)
        self.assertEqual((self.config.data_dir / 'knowledge.sqlite').read_bytes(), before)
        self.assertEqual(len(self.store.read(candidate['id'])['history']), 1)

    def test_archives_cannot_become_results_and_filter_pagination_no_loss(self):
        raw, summary, candidate, review = self.seed()
        with self.assertRaises(DomainError):
            self.knowledge.organize(raw['id'], 'outcomes', 'general', False)
        with self.assertRaises(DomainError):
            self.knowledge.organize(summary['id'], 'outcomes', 'invented', False)
        self.assertFalse(self.knowledge.path.exists())
        a = self.knowledge.browse(shelf='all', size=2)
        b = self.knowledge.browse(shelf='all', size=2, page=2)
        self.assertEqual(len({r['id'] for r in a['records'] + b['records']}), 4)
        self.assertEqual(self.knowledge.browse(state='needs_review')['records'][0]['id'], review['id'])
        self.assertEqual(self.knowledge.browse(shelf='all', kind='raw', query='原话')['total'], 1)
        self.assertEqual(self.knowledge.browse(shelf='workbench')['records'][0]['id'], candidate['id'])

    def test_system_uses_real_capabilities_not_qi_tagged_notes(self):
        raw, summary, candidate, review = self.seed()
        with self.store.db() as db:
            agreed = self.store._put(db, 'learning', {'text': '隔离测试认同', 'layers': ['dao', 'qi'], 'states': {'adoption': 'agreed'}})
            rejected = self.store._put(db, 'note', {'text': '不采纳', 'layers': ['dao', 'shu'], 'states': {'adoption': 'rejected'}})
            self.store._put(db, 'proposal', {'rules': [{'id': 'test', 'text': '候选纪律'}]})
        names = set(Service(self.config.data_dir).specs)
        data = system_workspace(self.knowledge, {s['id'] for s in SKILLS}, names, False)
        self.assertEqual([r['id'] for r in data['principles']], [agreed['id']])
        self.assertEqual([r['id'] for r in data['pending_principles']], [candidate['id']])
        self.assertFalse(any(r['id'] == rejected['id'] for r in data['methods']))
        self.assertIsNone(data['strategy']['id'])
        self.assertEqual(len(data['proposals']), 1)
        self.assertEqual(len(data['tools']), 9)
        self.assertTrue(all(t['available'] for t in data['tools']))
        self.assertTrue(all(s['available'] and not s['model_ready'] for s in data['skills']))
        missing = system_workspace(self.knowledge, set(), names - {'risk_compute'}, True)
        self.assertFalse(any(s['available'] for s in missing['skills']))
        self.assertFalse(next(t['available'] for t in missing['tools'] if t['id'] == 'risk'))

    def test_empty_read_surfaces_do_not_create_databases(self):
        self.assertEqual(self.knowledge.browse()['total'], 0)
        self.assertEqual(rule_form(self.store)['fields'], [])
        self.assertEqual(check_current_rules(self.store, {})['overall'], 'not_defined')
        self.assertFalse((self.config.data_dir / 'knowledge.sqlite').exists())
        self.assertFalse(self.knowledge.path.exists())

    def test_http_surfaces_auth_errors_risk_and_named_source_links(self):
        raw, summary, candidate, review = self.seed()
        with TestClient(create_app(self.config)) as client:
            for url in ['/api/knowledge', '/api/system', '/api/tools/journal', '/api/tools/rules', '/api/tools/quote?query=600000']:
                self.assertEqual(client.get(url).status_code, 401)
            self.assertEqual(client.post('/api/tools/risk', json={'positions': []}).status_code, 401)
            self.assertEqual(client.put('/api/knowledge/' + summary['id'], json={'shelf':'outcomes','topic':'risk','starred':True}).status_code, 401)
            client.post('/api/login', json={'password': 'test-password'})
            self.assertEqual(client.get('/api/knowledge?shelf=bad').status_code, 400)
            self.assertEqual(client.get('/api/knowledge?page=0').status_code, 422)
            self.assertEqual(client.get('/api/knowledge?topic=bad').status_code, 400)
            self.assertEqual(client.put('/api/knowledge/missing', json={'shelf':'outcomes','topic':'risk','starred':False}).status_code, 404)
            self.assertEqual(client.get('/api/records/' + summary['id']).json()['links'][raw['id']]['title'], '隔离测试原话')
            before = self.store.snapshot(limit=None)['records']
            body = {'positions':[{'security_id':'隔离计算','quantity':100,'price':10,'scenario_price':8}]}
            result = client.post('/api/tools/risk', json=body).json()
            self.assertEqual(result['known_market_value'], 1000)
            self.assertIsNone(result['account_weight'])
            self.assertEqual(result['positions'][0]['scenario_pnl'], -200)
            self.assertAlmostEqual(client.post('/api/tools/risk', json={**body,'account_equity':5000}).json()['account_weight'], .2)
            self.assertEqual(client.post('/api/tools/risk', json={'positions':[{'security_id':'x','quantity':-1,'price':10}]}).status_code, 422)
            self.assertEqual(client.post('/api/tools/risk', json={**body,'account_equity':0}).status_code, 422)
            self.assertEqual(client.post('/api/tools/rules', json={'inputs':{}}).json()['overall'], 'not_defined')
            self.assertEqual(self.store.snapshot(limit=None)['records'], before)
            self.assertEqual(len(client.get('/api/system').json()['skills']), 4)

    def test_quote_does_not_choose_ambiguous_security_or_persist_snapshot(self):
        candidates = [{'security_id':'SSE:600000','name':'测试证券一','code':'600000'}, {'security_id':'SZSE:000001','name':'测试证券二','code':'000001'}]
        with patch('trading_agent.market.Tencent.resolve', return_value={'resolved':None,'candidates':candidates}), TestClient(create_app(self.config)) as client:
            client.post('/api/login', json={'password':'test-password'})
            with patch('trading_agent.market.Tencent.quote') as quote:
                result = client.get('/api/tools/quote?query=测试').json()
                self.assertIsNone(result['quote'])
                self.assertEqual(result['candidates'], candidates)
                quote.assert_not_called()
            with patch('trading_agent.market.Tencent.quote', return_value={'name':'测试证券','last':10,'source':'fixture'}) as quote:
                response = client.get('/api/tools/quote?security_id=SSE:600000')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['quote']['last'], 10)
                quote.assert_called_once_with('SSE:600000')
            self.assertFalse((self.config.data_dir / 'knowledge.sqlite').exists())
            self.assertEqual(client.get('/api/tools/quote').status_code, 400)
            self.assertEqual(client.get('/api/tools/quote?security_id=bad').status_code, 422)

    def test_rules_use_active_snapshot_keep_missing_and_descriptive_unknown(self):
        self.seed()
        with self.store.db() as db:
            strategy = self.store._put(db, 'strategy', {'effective_at':'2020-01-01T00:00:00+00:00', 'confirmation_source_id':'fixture', 'rules':[
                {'id':'weight','text':'仓位不超过三成','condition':{'field':'account_weight','operator':'lte','value':.3}},
                {'id':'discipline','text':'不冲动交易'},
                {'id':'check','text':'计划已复核','condition':{'field':'checked','operator':'eq','value':True}},
            ]})
        before = (self.config.data_dir / 'knowledge.sqlite').read_bytes()
        form = rule_form(self.store)
        self.assertEqual(form['fields'][0]['type'], 'number')
        self.assertEqual(form['fields'][1]['type'], 'boolean')
        missing = check_current_rules(self.store, {})
        self.assertTrue(all(r['result'] == 'unknown' for r in missing['rules']))
        conflict = check_current_rules(self.store, {'account_weight':.5,'checked':True})
        self.assertEqual(conflict['strategy_id'], strategy['id'])
        self.assertEqual(conflict['overall'], 'conflict')
        incomplete = check_current_rules(self.store, {'account_weight':.2,'checked':True})
        self.assertEqual(incomplete['overall'], 'unknown')
        self.assertEqual((self.config.data_dir / 'knowledge.sqlite').read_bytes(), before)

    def test_backup_includes_knowledge_organization(self):
        from cloud_trading.__main__ import main
        _, summary, _, _ = self.seed()
        self.knowledge.organize(summary['id'], 'outcomes', 'risk', True)
        env = {'TRADING_DATA_DIR': str(self.config.data_dir), 'TRADING_RUNTIME_DIR': str(self.config.runtime_dir)}
        with patch.dict(os.environ, env), patch.object(sys, 'argv', ['cloud_trading','backup','--env-file',str(self.root/'missing.env')]), patch('builtins.print'):
            main()
        folder = next((self.root / 'backups').iterdir())
        with sqlite3.connect(folder / 'library.sqlite') as db:
            self.assertEqual(db.execute('SELECT shelf,topic,starred FROM library_items').fetchone(), ('outcomes','risk',1))


if __name__ == '__main__':
    unittest.main()
