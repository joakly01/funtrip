"""模型切换、重启和协议边界的行为验收。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from cloud_trading.models import SafeModel, load_profiles
from audit_agent_runtime.llm.base import ModelResponse


class ReplyModel:
    def __init__(self, label):
        self.label = label
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        return ModelResponse(output_text=self.label)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.profiles = root / 'models.toml'
        self.profiles.write_text('''
[[models]]
id="kimi"
label="Kimi"
protocol="claude"
base_url="https://example.invalid"
model="kimi-test"
key_env="TRADING_TEST_KEY"
[[models]]
id="deepseek"
label="DeepSeek"
protocol="openai"
base_url="https://example.invalid"
model="deepseek-test"
key_env="TRADING_TEST_KEY"
[[models]]
id="unconfigured"
protocol="openai"
base_url=""
model=""
key_env="TRADING_MISSING_KEY"
''')
        self.config = Config(root / 'trading', root / 'runtime', 'test-password',
                             models_path=self.profiles, active_model='kimi')
        self.env = patch.dict(os.environ, {'TRADING_TEST_KEY': 'private-test-secret'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_switch_routes_each_message_persists_and_does_not_replay(self):
        app = create_app(self.config)
        router = app.state.services.model_client
        router.close()
        kimi, deepseek = ReplyModel('Kimi reply'), ReplyModel('DeepSeek reply')
        router.clients = {'kimi': SafeModel(kimi), 'deepseek': SafeModel(deepseek)}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/models').status_code, 401)
            client.post('/api/login', json={'password': 'test-password'})
            listing = client.get('/api/models')
            self.assertNotIn('private-test-secret', listing.text)
            self.assertNotIn('key_env', listing.text)
            self.assertEqual(listing.json()['active'], 'kimi')
            body = {'session_id': 'same-chat', 'message_id': 'kimi-message', 'text': '第一条', 'model': 'kimi', 'remember': False}
            first = client.post('/api/router/ask', json=body)
            self.assertIn('Kimi reply', first.text)
            self.assertEqual(client.post('/api/models/select', json={'model': 'deepseek'}).status_code, 200)
            self.assertEqual(client.post('/api/router/ask', json=body).text, first.text)
            self.assertEqual(kimi.calls, 1)
            second = client.post('/api/router/ask', json={'session_id': 'same-chat', 'message_id': 'deepseek-message', 'text': '第二条', 'remember': False})
            self.assertIn('DeepSeek reply', second.text)
            self.assertEqual(deepseek.calls, 1)
            history = client.get('/api/sessions/same-chat').json()
            self.assertEqual([r['text'] for r in history if r['role']=='assistant'], ['Kimi reply', 'DeepSeek reply'])
            self.assertEqual(client.post('/api/models/select', json={'model': 'unconfigured'}).status_code, 503)
            self.assertEqual(client.post('/api/models/select', json={'model': 'missing'}).status_code, 404)
            conflict = client.post('/api/router/ask', json={**body, 'model': 'deepseek'})
            self.assertEqual(conflict.status_code, 409)
        with TestClient(create_app(self.config)) as client:
            client.post('/api/login', json={'password': 'test-password'})
            self.assertEqual(client.get('/api/models').json()['active'], 'deepseek')
            self.assertEqual(len(client.get('/api/sessions/same-chat').json()), 4)

    def test_ambiguous_configuration_is_rejected(self):
        self.profiles.write_text(self.profiles.read_text().replace('id="deepseek"', 'id="kimi"'))
        with self.assertRaisesRegex(ValueError, '重复'):
            load_profiles(self.config)
