"""只读提取 Run82 首次确认前后状态，不修改历史运行。"""
import sys, json, gzip
from pathlib import Path
from copy import deepcopy
sys.path.insert(0, 'backend')
from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS

base = Path('output/qa/2026-11-17-state-driven-acceptance')
snapshot = json.loads((base / 'control-audit-state/paused-ledger-snapshot.json').read_text(encoding='utf-8'))
node = next(n for n in snapshot['nodes'] if n['id'] == 447)
first, second = [r for r in node['tool_calls'] if r['tool_key'] == 'request_confirmation']
record = node['actions_without_argument_bodies'][first['tool_call_id']]
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    run = db.get(AgentRun, 82)
    store = ArtifactStore(db, user_id=run.user_id, project_id=run.project_id)
    states = {}
    for label, field in [('before_state', 'pre_state_hash'), ('after_state', 'post_state_hash')]:
        digest = record[field]
        states[label] = store.get({'id': store._id(digest), 'sha256': digest})
        assert action_state_hash(states[label]) == digest
    metadata = {k: deepcopy(getattr(run, k)) for k in ['id', 'user_id', 'project_id', 'workflow_definition_id', 'input_payload', 'run_context']}
    db.rollback()

def frontier(count):
    value = deepcopy(node['action_checkpoint_header']['budget'])
    usage = {key: sum(r['usage'][key] for r in node['model_requests'][:count]) for key in ['input_tokens', 'output_tokens', 'total_tokens']}
    value['charged'] = {'attempted_requests': count, **usage}
    value['reported_usage'] = {'requests': count, **usage}
    value.update(completed_requests=count, pending_requests=0, interrupted_requests=0)
    value.pop('input_estimator', None)
    return value

config = next(s for s in BUILTIN_AGENT_SPECS if s['agent_key'] == 'test_generation_coordinator')['runtime_config']
data = {'run': metadata, 'node_id': 447, **states, 'first_confirmation': first, 'second_confirmation': second,
        'first_receipt': record, 'first_request': node['model_requests'][9], 'extra_request': node['model_requests'][10],
        'budget_before': frontier(9), 'budget_after': frontier(10), 'runtime_config': config}
fixture = {'provenance': {'source_run_id': 82, 'source_node_id': 447,
    'source_file': str(base / 'control-audit-state/paused-ledger-snapshot.json'), 'new_model_responses': False,
    'budget_frontier': '已保存真实请求用量之和；限额保留原值，本地分词估计未投影', 'data_sha256': action_state_hash(data)}, 'data': data}
path = Path('backend/tests/agent_platform/fixtures/run82_confirmation_pause_real.json.gz')
path.write_bytes(gzip.compress(json.dumps(fixture, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), mtime=0))
print(path, path.stat().st_size, data['budget_after']['charged'])
