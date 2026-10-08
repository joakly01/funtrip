from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from cloud_trading.app import create_app, STATIC
from cloud_trading.config import Config

@unittest.skipUnless((STATIC/'unified/index.html').is_file(), '先生成统一前端')
class UnifiedFrontendTests(unittest.TestCase):
    def test_entry_assets_and_legacy_do_not_require_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with TestClient(create_app(Config(root/'business',root/'runtime','test-only'))) as client:
                response = client.get('/', follow_redirects=False)
                self.assertEqual(response.headers['location'], '/next/')
                self.assertEqual(client.get('/next/').status_code,200)
                self.assertEqual(client.get('/legacy').status_code,200)
                self.assertEqual(client.get('/api/library').status_code,401)
                for asset in (STATIC/'unified/assets').iterdir():
                    self.assertEqual(client.get('/next/assets/'+asset.name).status_code,200)
                self.assertEqual(client.get('/next/../../config/app.env').status_code,404)
