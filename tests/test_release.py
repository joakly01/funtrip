"""发布包避免混入密钥、私人资料和机器环境。"""
from pathlib import Path
import tarfile
import tempfile
import unittest
from scripts.deploy import bundle


class ReleaseTests(unittest.TestCase):
    def test_release_contains_runtime_assets_but_no_private_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'release.tar.gz'
            bundle(path)
            with tarfile.open(path) as archive:
                names = archive.getnames()
                self.assertIn('config/models.toml', names)
                self.assertIn('vendor/runtime_core/manifest.json', names)
                self.assertIn('cloud_trading/static/index.html', names)
                self.assertIn('skills/private-trading-partner/SKILL.md', names)
                self.assertFalse(any(n.startswith(('data/', '.venv/', 'agent-runtime/', 'runtime-ontology/')) for n in names))
                self.assertFalse(any(n.endswith('.env') or '__pycache__' in n for n in names))
