"""从真实数据库和HTTP导出验收最终交付，不修改运行、不调用模型。"""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path('backend').resolve()))
import httpx
from sqlalchemy import text
from core.authn.auth import create_access_token
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentNodeRun, User
from modules.agent_platform.checkpoint_storage import hydrate_action_checkpoint
from modules.agent_platform.results import persisted_test_generation_result
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.registry import ToolExecutionContext, runtime_registry_signature

parser = argparse.ArgumentParser()
parser.add_argument('--run-id', type=int, required=True)
parser.add_argument('--output-dir', type=Path, required=True)
args = parser.parse_args()
args.output_dir.mkdir(exist_ok=True, parents=True)
launch = json.loads((args.output_dir/'launch.json').read_text(encoding='utf8'))
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    run = db.get(AgentRun, args.run_id)
    assert run and run.status == 'success', '只有实际成功终态才可验收交付'
    artifact = deepcopy(persisted_test_generation_result(run))
    node_ids = [r.id for r in db.query(AgentNodeRun.id).filter(AgentNodeRun.run_id == run.id).order_by(AgentNodeRun.id).all()]
    checkpoint = hydrate_action_checkpoint(db, db.get(AgentNodeRun, node_ids[-1]))
    state = checkpoint['state']
    assert state['completed'] and generation._review_current(state) and generation._can_finish(state)
    assert state['review']['result']['verdict'] == 'approved'
    review = deepcopy(state['review']['result'])
    assert len(review['case_checks']) == len(state['cases']['items'])
    assert all(item['valid'] for item in review['case_checks'])
    assert not any(branch['disposition'] == 'gap'
                   for row in review['source_checks'] for branch in row['branches'])
    cases = artifact['test_cases']
    assert len(cases) == artifact['target_count'] == state['goal']['case_budget'] == 70
    case_ids = [c['case_id'] for c in cases]
    assert len(set(case_ids)) == 70
    assert set(state['review']['result']['checked_case_ids']) == set(case_ids)
    context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
        run_id=run.id, node_key='agent_network', run_input=deepcopy(run.input_payload), action_state=state)
    assert generation._build_delivery(context, state) == artifact
    source = artifact['evidence']['source']
    assert source['content_hash'] == launch['source']['content_hash']
    assert source['document_id'] == launch['source']['document_id']
    usage = deepcopy(run.run_context['usage'])
    wall_seconds = (run.finished_at - run.started_at).total_seconds()
    human_wait_seconds = float(run.run_context.get('confirmation_wait_seconds') or 0)
    elapsed = wall_seconds - human_wait_seconds
    submitted_at = datetime.fromisoformat(launch['requested_at'])
    submission_wall_seconds = (run.finished_at.replace(tzinfo=timezone.utc)-submitted_at).total_seconds()
    budget = deepcopy(checkpoint['budget'])
    assert elapsed <= 1200 and usage['total_tokens'] <= 300000
    assert submission_wall_seconds - human_wait_seconds <= 1200
    assert budget['pending_requests'] == 0
    assert usage['total_tokens'] <= budget['charged']['total_tokens'] <= 300000
    unknown_reserved_tokens = budget['charged']['total_tokens'] - usage['total_tokens']
    assert launch['runtime_signature'] == runtime_registry_signature()
    username = db.query(User.username).filter(User.id == run.user_id).scalar()
    failed_predecessors = [item.id for item in db.query(AgentRun.id).filter(
        AgentRun.project_id == run.project_id, AgentRun.id >= 66, AgentRun.id < run.id,
        AgentRun.status == 'failed').order_by(AgentRun.id).all()]
    result = {'run_id':run.id,'status':run.status,'source':source,'case_count':len(cases),
        'review_verdict':state['review']['result']['verdict'],'checked_case_count':len(case_ids),
        'review_certificate': artifact['final_review'],
        'elapsed_seconds':elapsed,'wall_seconds':wall_seconds,'human_wait_seconds':human_wait_seconds,
        'submission_wall_seconds':submission_wall_seconds,
        'usage':usage,'budget':budget,
        'actual_usage_complete':unknown_reserved_tokens==0,
        'unknown_reserved_tokens':unknown_reserved_tokens,
        'approved_decisions':[{'evidence_id':a['evidence_id'],'approval_id':a['approval_id'],'resolution':a['resolution']}
                              for a in state['approved_decisions']],
        'case_versions':{k:v['version'] for k,v in state['cases']['items'].items()},
        'runtime_signature':runtime_registry_signature(), 'database_writes':False,'new_model_requests':0}
    db.rollback()
token = create_access_token({'sub':username}, expires_delta=timedelta(minutes=10))
with httpx.Client(base_url='http://127.0.0.1:8000', headers={'Authorization':f'Bearer {token}'}, timeout=60) as client:
    for name in ['summary','metrics']:
        response = client.get(f'/api/agents/runs/{args.run_id}/{name}')
        response.raise_for_status()
        (args.output_dir/f'http-{name}.json').write_text(json.dumps(response.json(),ensure_ascii=False,indent=2),encoding='utf8')
    response = client.get(f'/api/agents/runs/{args.run_id}/test-cases.xlsx')
    response.raise_for_status()
    excel = response.content
    target = args.output_dir/f'同步优学-70条测试用例-Run{args.run_id}.xlsx'
    target.write_bytes(excel)
    result['excel_export']={'status_code':response.status_code,'bytes':len(excel),'sha256':hashlib.sha256(excel).hexdigest(),'path':str(target.resolve())}
    # 只读核对平台原生导出文件；不另行改写工作簿。
    with zipfile.ZipFile(io.BytesIO(excel)) as archive:
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows = sheet.findall('s:sheetData/s:row', ns)
        assert len(rows) == 71
        exported_ids = [''.join(row[0].itertext()) for row in rows[1:]]
        assert exported_ids == case_ids
        result['excel_export']['data_rows'] = len(rows)-1
    result['failed_run_exports']={}
    for previous in failed_predecessors:
        check = client.get(f'/api/agents/runs/{previous}/test-cases.xlsx')
        assert check.status_code == 409
        result['failed_run_exports'][str(previous)] = check.status_code
result['accepted_time_and_reported_usage']=True
(args.output_dir/'delivered-cases.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf8')
(args.output_dir/'final-review-certificate.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf8')
(args.output_dir/'delivery-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf8')
print(json.dumps({k:v for k,v in result.items() if k not in ['budget','case_versions','approved_decisions']},ensure_ascii=False))
