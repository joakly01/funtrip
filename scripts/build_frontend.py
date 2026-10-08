"""从唯一前端源码生成 H5 与微信包，并将 H5 放入 Python 发布目录。"""
from pathlib import Path
import argparse
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-only', action='store_true', help='仅检查并构建网页；小程序暂停调试时使用')
    args = parser.parse_args()
    frontend = ROOT / 'frontend'
    version = json.loads((frontend / 'package.json').read_text())['version']
    manifest_version = json.loads((frontend / 'src/manifest.json').read_text())['versionName']
    source_version = re.search(r'VERSION\s*=\s*[\"\']([^\"\']+)', (frontend / 'src/domain/types.ts').read_text()).group(1)
    if len({version, manifest_version, source_version}) != 1:
        raise ValueError('网页和微信产品版本不一致，请同步版本后构建')
    commands = ['type-check', 'test', 'build:h5']
    if not args.web_only:
        commands.append('build:mp-weixin')
    for command in commands:
        subprocess.run(['npm', 'run', command], cwd=frontend, check=True)
    destination = ROOT / 'cloud_trading/static/unified'
    temporary = destination.with_name('unified-building')
    shutil.rmtree(temporary, ignore_errors=True)
    shutil.copytree(frontend / 'dist/build/h5', temporary)
    shutil.rmtree(destination, ignore_errors=True)
    temporary.rename(destination)
    print('网页发布资源已生成。' if args.web_only else '网页发布资源和微信小程序包已由同一源码生成。')

if __name__ == '__main__':
    main()
