"""真实模型在临时目录完成一次工具循环；不会读取个人交易资料。"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from io import BytesIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from cloud_trading.app import create_app
from cloud_trading.config import Config
from trading_agent.service import Service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('model', help='config/models.toml中的模型ID')
    parser.add_argument('--multimodal', action='store_true', help='使用合成图片和文档验证附件与后续追问')
    parser.add_argument('--growth', action='store_true', help='在临时库验证带专题上下文的整理建议与用户采用')
    parser.add_argument('--collaboration', action='store_true', help='临时库验证两个专职伙伴的真实模型与工具循环')
    args = parser.parse_args()
    for key, value in dotenv_values(ROOT / 'config/app.env', interpolate=False).items():
        if key.startswith('TRADING_') and value is not None:
            os.environ.setdefault(key, value)
    with tempfile.TemporaryDirectory(prefix='trading-model-check-') as directory:
        root = Path(directory)
        config = replace(Config.from_env(), data_dir=root/'trading', runtime_dir=root/'runtime',
                         password='isolated-verification', active_model=args.model, max_turns=8)
        with TestClient(create_app(config)) as client:
            client.post('/api/login', json={'password': config.password})
            response = client.post('/api/router/ask', json={'session_id':'verification', 'message_id':'verification-1',
                'model':args.model, 'text':'这是工程验收，不是真实交易。请调用agent_status，然后用一句中文说明实际能力。不要保存交易记忆。', 'remember':False})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            tools = [e['payload']['tool_name'] for e in events if e['type']=='tool_result']
            done = [e['payload'].get('stop_reason') for e in events if e['type']=='done']
            reply = ''.join(e['payload'].get('text','') for e in events if e['type']=='assistant')
            records = Service(config.data_dir).store.search()['records']
            summary = {'model':args.model, 'http_status':response.status_code,'tools':tools,
                       'stop_reason':done,'business_records':len(records),'reply_preview':reply[:160]}
            print(json.dumps(summary, ensure_ascii=False))
            if response.status_code!=200 or 'agent_status' not in tools or (len(done)!=1 or done[0] not in {'end_turn','stop'}) or records or any(e['type']=='error' for e in events):
                raise SystemExit('真实模型验收失败。')
            if args.growth:
                from cloud_trading.growth import GrowthWorkspace
                workspace = GrowthWorkspace(Service(config.data_dir).store)
                topic = workspace.create('topic', {'title': '隔离示例：怎样记好读书笔记',
                    'question': '怎样区分摘录与自己的理解？'}, [], 'verification-topic')
                result = client.post('/api/router/ask', json={'session_id': 'growth_' + topic['id'],
                    'message_id': 'verification-growth', 'model': args.model, 'context_id': topic['id'],
                    'text': '请根据当前专题，用 growth_suggest 为 summary 和 next_step 提交一份简短整理建议，留待我审阅。不要修改交易纪律，也不需要调用其他写入工具。', 'remember': True})
                events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith('data: ')]
                called = [e['payload']['tool_name'] for e in events if e['type'] == 'tool_result']
                done = [e['payload'].get('stop_reason') for e in events if e['type'] == 'done']
                detail = workspace.detail(topic['id'])
                assert result.status_code == 200 and 'growth_suggest' in called and len(done) == 1 and done[0] in {'end_turn', 'stop'}
                assert not any(e['type'] == 'error' for e in events) and detail['version'] == 1
                assert len(detail['suggestions']) == 1 and detail['suggestions'][0]['body']['patch'].get('summary')
                review = client.post('/api/growth/suggestions/' + detail['suggestions'][0]['id'] + '/decision', json={'decision': 'accept'})
                assert review.status_code == 200 and review.json()['version'] == 2
                assert not workspace.store.snapshot(kind='trade')['records'] and not workspace.store.active_snapshot()['id']
                print(json.dumps({'model': args.model, 'contextual_suggestion': True, 'owner_acceptance': True,
                    'formal_rules_unchanged': True, 'isolated_data': True}, ensure_ascii=False))
            if args.collaboration:
                from cloud_trading.growth import GrowthWorkspace
                workspace = GrowthWorkspace(Service(config.data_dir).store)
                topic = workspace.create('topic', {'title': '隔离示例：区分观点与证据',
                    'question': '如何读懂一句经典，又避免直接当成纪律？'}, [], 'team-topic')
                result = client.post('/api/router/ask', json={'session_id': 'team-verification',
                    'message_id': 'team-verification', 'model': args.model, 'context_id': topic['id'],
                    'text': '这是临时资料验收。请使用 collaborate 邀请 learning 与 evolution 两个伙伴，委托他们分别调用 classic_search 检索“知止”，以原文和边界说明观点不等于已验证的纪律。最后汇总共同点与未知，不调用写入工具。', 'remember': False})
                events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith('data: ')]
                teams = [e['payload']['result'] for e in events if e['type'] == 'tool_result' and e['payload']['tool_name'] == 'collaborate']
                assert result.status_code == 200 and teams and not any(e['type']=='error' for e in events)
                members = teams[0]['members']
                assert {m['id'] for m in members} == {'learning','evolution'}
                assert all(m['status']=='complete' and 'classic_search' in m['tools'] and m['text'] for m in members), members
                assert not workspace.store.snapshot(kind='collaboration')['records'] and not workspace.store.active_snapshot()['id']
                print(json.dumps({'model':args.model, 'independent_specialists':len(members),
                    'shared_tools':True,'no_business_writes':True,'isolated_data':True},ensure_ascii=False))
            if args.multimodal:
                from PIL import Image, ImageDraw
                image = Image.new('RGB', (180, 90), 'white')
                drawing = ImageDraw.Draw(image)
                drawing.ellipse((15, 20, 65, 70), fill='red')
                drawing.rectangle((105, 20, 155, 70), fill='blue')
                output = BytesIO(); image.save(output, format='PNG')
                uploaded = []
                for name, content in [('image.png', output.getvalue()), ('document.txt', '合成验收资料；标签：松树；编号：4702。'.encode())]:
                    upload = client.post('/api/attachments', files={'file': (name, content)})
                    if upload.status_code != 200:
                        raise SystemExit('附件上传验收失败。')
                    uploaded.append(upload.json()['id'])
                def ask(text, identifier, attachment_ids):
                    result = client.post('/api/router/ask', json={'session_id': 'verification',
                        'message_id': identifier, 'model': args.model, 'text': text,
                        'attachment_ids': attachment_ids, 'remember': False})
                    output_events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith('data: ')]
                    if result.status_code != 200 or any(event['type'] == 'error' for event in output_events):
                        raise SystemExit('多模态模型验收失败。')
                    return ''.join(event['payload'].get('text', '') for event in output_events if event['type'] == 'assistant')
                reply = ask('这是工程验收，不记交易资料。只回答左侧形状颜色、右侧形状颜色，以及文件编号。', 'verification-image', uploaded)
                if not ('红' in reply or 'red' in reply.lower()) or not ('蓝' in reply or 'blue' in reply.lower()) or '4702' not in reply:
                    raise SystemExit('模型未正确读到图片或文档。')
                followup = ask('继续工程验收：刚才文件中的标签是什么？只回答标签。', 'verification-followup', [])
                if '松树' not in followup:
                    raise SystemExit('附件追问验收失败。')
                history = client.get('/api/sessions/verification').json()
                if sum(len(row['attachments']) for row in history) != 2 or Service(config.data_dir).store.search()['records']:
                    raise SystemExit('附件留存或隔离验收失败。')
                print(json.dumps({'model': args.model, 'image_and_document': True, 'followup': True,
                                  'history_attachments': 2, 'business_records': 0}, ensure_ascii=False))

if __name__=='__main__':
    main()
