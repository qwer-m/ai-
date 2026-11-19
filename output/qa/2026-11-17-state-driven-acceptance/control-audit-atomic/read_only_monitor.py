"""新Run只读控制链审计；生产代码、数据库、人工答复和模型请求均不写入。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import time
import sys

RUN_ID = int(sys.argv[1])

ROOT = Path(__file__).resolve().parents[4]
BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('readonly_capture', ROOT / 'backend/scripts/qa/verify/capture_generation_run_real_data.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
manifest = BASE / 'changes.json'
entries = json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else []


def atomic(target, value):
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf8')
    temporary.replace(target)


def save(snapshot):
    signature = {'status': snapshot['run']['status'], 'usage': snapshot['run']['usage'],
        'nodes': [{key: value for key, value in node.items() if key not in {'started_at', 'finished_at'}}
            for node in snapshot['nodes']], 'approvals': snapshot['approvals'],
        'latest_event': max((event['sequence'] for event in snapshot['events']), default=0)}
    digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    if entries and entries[-1]['state_signature'] == digest:
        return
    name = f'control-{len(entries) + 1:04d}.json'
    target = BASE / name
    atomic(target, snapshot)
    nodes = [{'id': node['id'], 'projection': node['checkpoint_projection'],
        'reported_usage': node['reported_usage'], 'charged': node['charged'],
        'pending_requests': node['pending_requests']} for node in snapshot['nodes']]
    row = {'snapshot': name, 'captured_at': snapshot['capture']['captured_at'], 'state_signature': digest,
        'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'status': snapshot['run']['status'],
        'reported_usage': snapshot['run']['usage'], 'nodes': nodes, 'approvals': snapshot['approvals']}
    entries.append(row)
    atomic(manifest, entries)
    print(json.dumps({'snapshot': name, 'at': row['captured_at'], 'status': row['status'],
        'reported': row['reported_usage'], 'nodes': [{'id': node['id'],
        'progress': (node['projection'] or {}).get('progress'),
        'current_action': (node['projection'] or {}).get('current_action'),
        'pending_requests': node['pending_requests'], 'charged': node['charged']} for node in nodes],
        'approvals': row['approvals']}, ensure_ascii=False), flush=True)


for count in range(180):
    snapshot = capture.capture(RUN_ID, summary=True)
    save(snapshot)
    if snapshot['run']['status'] in {'success', 'failed', 'cancelled'}:
        terminal = capture.capture(RUN_ID, summary=False)
        atomic(BASE / 'terminal-snapshot.json', terminal)
        print(json.dumps({'terminal_snapshot': str(BASE / 'terminal-snapshot.json'),
            'status': snapshot['run']['status'], 'model_requests_sent': 0, 'database_writes': False}), flush=True)
        break
    if snapshot['run']['status'] not in {'running', 'pending'}:
        print(json.dumps({'attention_status': snapshot['run']['status'], 'approvals': snapshot['approvals']}), flush=True)
        break
    time.sleep(8)
