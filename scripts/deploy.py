"""独立发布到Ubuntu：保留共享数据，版本目录和专属systemd服务。"""
import argparse
from datetime import datetime
import getpass
import io
import json
from pathlib import Path
import re
import shlex
import sqlite3
import sys
import tarfile
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
import paramiko

FILES = ['cloud_trading', 'trading_agent', 'vendor', 'skills', 'roles', 'knowledge',
         'config/models.toml', 'config/capabilities.toml', 'requirements.txt', 'deploy']


def bundle(destination):
    def keep(member):
        if '__pycache__' in Path(member.name).parts or member.name.endswith(('.pyc', '.DS_Store')):
            return None
        return member
    with tarfile.open(destination, 'w:gz') as archive:
        for name in FILES:
            archive.add(ROOT/name, arcname=name, filter=keep)


def _remote_file(sftp, path):
    try:
        sftp.stat(path)
        return True
    except FileNotFoundError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--user', default='ubuntu')
    parser.add_argument('--remote-root', default='/home/ubuntu/trading-partner')
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--credentials-file', type=Path, help='本地临时JSON凭据文件，仅用于自动化；不要提交')
    parser.add_argument('--wheelhouse', type=Path, help='本地Linux离线依赖目录')
    parser.add_argument('--voice-model', type=Path, help='本地离线语音模型目录，单独安装到共享data/runtime/models')
    parser.add_argument('--package-index', default='https://pypi.org/simple', help='Python包源，仅用于本应用环境')
    parser.add_argument('--include-data', action='store_true', help='首次部署导入网页资料，目标已有数据库时拒绝覆盖')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', args.user):
        parser.error('用户名无效')
    if not re.fullmatch(r'/[a-zA-Z0-9_/-]+', args.remote_root):
        parser.error('部署目录必须为不含空格的绝对路径')
    values = dotenv_values(ROOT/'config/app.env', interpolate=False)
    if not values.get('TRADING_PASSWORD'):
        parser.error('请先配置个人网页登录密码')
    password = None
    if args.credentials_file:
        credentials = json.loads(args.credentials_file.read_text())
        if credentials['host'] != args.host or credentials['user'] != args.user:
            parser.error('凭据目标与部署主机不一致')
        password = credentials['password']
    elif not args.key_file:
        password = getpass.getpass('SSH密码（不保存）：')
    ssh = paramiko.SSHClient()
    ssh.load_system_host_keys()
    # 首次使用主机需通过普通SSH确认主机身份；不自动接受未知主机。
    ssh.connect(args.host, username=args.user, password=password,
                key_filename=str(args.key_file) if args.key_file else None,
                look_for_keys=args.key_file is not None, allow_agent=args.key_file is not None,
                timeout=20, auth_timeout=20)
    def run(command, *, timeout=300):
        _, out, err = ssh.exec_command(command, timeout=timeout)
        output = out.read().decode() + err.read().decode()
        code = out.channel.recv_exit_status()
        if output:
            print(output[-5000:], end='' if output.endswith('\n') else '\n')
        if code:
            raise RuntimeError(f'部署步骤失败，状态码{code}。')
        return output
    root = args.remote_root
    q = shlex.quote
    release_id = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
    release = root+'/releases/'+release_id
    try:
        run('sudo -n true && command -v nginx && python3 --version')
        # 未使用本应用服务时，80和8097必须空闲；不停止其他应用。
        run("if ! systemctl is-active --quiet trading-partner-web.service; then "
            "if ss -H -ltn '( sport = :80 )' | grep -q .; then echo '80端口被其他服务使用，停止部署'; exit 1; fi; fi; "
            "if ! systemctl is-active --quiet trading-partner.service; then "
            "if ss -H -ltn '( sport = :8097 )' | grep -q .; then echo '8097端口被其他服务使用，停止部署'; exit 1; fi; fi")
        if args.include_data:
            run('test ! -f '+q(root+'/data/trading/knowledge.sqlite')+' && test ! -f '+q(root+'/data/runtime/runtime.sqlite'))
        run('mkdir -p '+ ' '.join(q(p) for p in [release, root+'/config', root+'/data/trading', root+'/data/runtime']))
        sftp = ssh.open_sftp()
        try:
            with tempfile.TemporaryDirectory(prefix='trading-release-') as directory:
                folder = Path(directory)
                archive = folder/'application.tar.gz'
                bundle(archive)
                sftp.put(str(archive), release+'/application.tar.gz')
                run('tar -xzf '+q(release+'/application.tar.gz')+' -C '+q(release)+' && rm '+q(release+'/application.tar.gz'))
                if args.wheelhouse:
                    wheels = list(args.wheelhouse.glob('*.whl'))
                    if not wheels:
                        raise ValueError('离线依赖目录为空。')
                    sftp.mkdir(release+'/wheels')
                    for wheel in wheels:
                        sftp.put(str(wheel), release+'/wheels/'+wheel.name)
                if args.voice_model:
                    model_files = ['model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt']
                    if not all((args.voice_model/name).is_file() for name in model_files):
                        raise ValueError('语音模型目录不完整。')
                    model_root = root+'/data/runtime/models/faster-whisper-base'
                    run('mkdir -p '+q(model_root))
                    for name in model_files:
                        sftp.put(str(args.voice_model/name), model_root+'/'+name+'.tmp')
                        sftp.posix_rename(model_root+'/'+name+'.tmp', model_root+'/'+name)
                tls_ready = all(_remote_file(sftp, root+'/config/tls/'+name) for name in ('joakly.site_bundle.crt', 'joakly.site.key'))
                for name in ('trading-partner.service', 'trading-partner-web.service', 'nginx.conf'):
                    template = 'nginx-https.conf' if name == 'nginx.conf' and tls_ready else name
                    content = (ROOT/'deploy'/template).read_text().replace('@APP_ROOT@', root).replace('@APP_USER@', args.user)
                    with sftp.open(release+'/deploy/'+name, 'w') as handle:
                        handle.write(content)
                if args.include_data:
                    for source, name in [(ROOT/'data/cloud/trading/knowledge.sqlite','trading/knowledge.sqlite'),
                                         (ROOT/'data/cloud/runtime/runtime.sqlite','runtime/runtime.sqlite')]:
                        if source.is_file():
                            target = folder/Path(name).name
                            with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(target) as dst:
                                src.backup(dst)
                                assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                            sftp.put(str(target), root+'/data/'+name)
                    for source in (ROOT/'data/cloud/trading/exports').glob('*'):
                        if source.is_file():
                            try:sftp.mkdir(root+'/data/trading/exports')
                            except OSError:pass
                            sftp.put(str(source), root+'/data/trading/exports/'+source.name)
                    selection = ROOT/'data/cloud/runtime/settings.json'
                    if selection.is_file():sftp.put(str(selection), root+'/data/runtime/settings.json')
                server_values = {key:value for key,value in values.items() if key.startswith('TRADING_') and value is not None}
                server_values.update(TRADING_DATA_DIR=root+'/data/trading', TRADING_RUNTIME_DIR=root+'/data/runtime',
                                     TRADING_MODELS_FILE=root+'/current/config/models.toml', TRADING_HOST='127.0.0.1',
                                     TRADING_PORT='8097', TRADING_SECURE_COOKIE='1' if tls_ready else '0')
                if args.voice_model:
                    server_values['TRADING_ASR_MODEL_DIR'] = root+'/data/runtime/models/faster-whisper-base'
                with sftp.open(root+'/config/app.env.tmp','w') as handle:
                    handle.write(''.join(key+'='+json.dumps(value,ensure_ascii=False)+'\n' for key,value in server_values.items()))
                sftp.chmod(root+'/config/app.env.tmp',0o600)
                run('if test -f '+q(root+'/config/app.env')+'; then cp '+q(root+'/config/app.env')+' '+q(root+'/config/app.env.backup-'+release_id)+'; fi; mv '+q(root+'/config/app.env.tmp')+' '+q(root+'/config/app.env'))
        finally:
            sftp.close()
        run('test -x '+q(root+'/.venv/bin/python')+' || python3 -m venv '+q(root+'/.venv'))
        install_source = ('--no-index --find-links '+q(release+'/wheels')) if args.wheelhouse else ('--index-url '+q(args.package_index))
        run(q(root+'/.venv/bin/pip')+' install '+install_source+' -r '+q(release+'/requirements.txt'), timeout=600)
        # 启动前验证裁剪后的依赖可导入。
        run('cd '+q(release)+' && '+q(root+'/.venv/bin/python')+' -c '+q('from cloud_trading.app import create_app; print("运行依赖导入通过")'))
        run('if test -L '+q(root+'/current')+'; then readlink '+q(root+'/current')+' > '+q(root+'/previous-release')+'; fi; '
            'ln -sfn '+q(release)+' '+q(root+'/current'))
        for name in ('trading-partner.service','trading-partner-web.service'):
            run('sudo install -m 644 '+q(release+'/deploy/'+name)+' '+q('/etc/systemd/system/'+name))
        run('sudo systemctl daemon-reload && sudo systemctl enable trading-partner.service trading-partner-web.service && '
            'sudo systemctl restart trading-partner.service trading-partner-web.service')
        run('systemctl is-active trading-partner.service trading-partner-web.service')
        run('curl --retry 8 --retry-connrefused --retry-delay 1 --fail --silent http://127.0.0.1:8097/health')
        print('\n部署成功：http://'+args.host+'/')
        print('应用目录：'+root+'；代码版本：'+release_id)
    finally:
        ssh.close()

if __name__=='__main__':
    main()
