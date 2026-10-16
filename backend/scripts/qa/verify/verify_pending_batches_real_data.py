"""使用真实生成检查点验证平台派发待处理集合，以及专业失败的模型投影与完整审计隔离。"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import validate
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--uncommitted-source',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    report={'model_requests':0,'online_writes':0,'states':[]}
    for path in (args.uncommitted_source,args.source):
        source=json.loads(Path(path).read_text(encoding='utf-8'))
        node=next(n for n in reversed(source['nodes']) if n['sdk_state'].get('action_checkpoint'))
        checkpoint=node['sdk_state']['action_checkpoint'];run=source['run']
        engine=create_engine('sqlite+pysqlite:///:memory:');AgentRun.__table__.create(engine)
        with Session(engine) as db:
            db.add(AgentRun(**deepcopy(run)));db.flush()
            context=ToolExecutionContext(db=db,user_id=run['user_id'],project_id=run['project_id'],run_id=run['id'],
                node_key=node['node_key'],run_input=deepcopy(run['input_payload']),action_input=deepcopy(node['input_payload']),
                action_state=deepcopy(checkpoint['state']),artifacts=deepcopy(run['run_context']['artifacts']))
            state=context.action_state
            expected=[i for i,record in enumerate(state['generation_records']) if record is None]
            action=generation._prepare_pending_action(context,'generate_cases',generation.prepare_generation_action,{'guidance':''})
            assert action['item_indexes']==expected
            assert action['items']==[state['generation_inputs'][i] for i in expected]
            assert action_state_hash(state)==checkpoint['state_hash']
            projections=[]
            for problem in state['problems'].values():
                payload=problem['payload']
                if payload.get('result_type')!='specialist_failure':
                    continue
                digest=action_state_hash(payload)
                compact=generation._problem_payload_view(payload)
                detail=generation._problem_payload_view(payload,detailed=True)
                assert detail['submission_attempts']==payload['submission_attempts'][-1:]
                assert detail['consultation_results']==payload['consultation_results']
                assert 'submission_attempts' not in compact and 'tool_observations' not in compact
                latest=payload['submission_attempts'][-1]['feedback']['feedback']
                assert compact['latest_validation']=={key:latest[key] for key in ('error_type','message') if key in latest}
                assert action_state_hash(payload)==digest
                size=lambda value:len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
                projections.append({'audit_bytes':size(payload),'observation_bytes':size(compact),'detail_bytes':size(detail)})
            report['states'].append({'run_id':run['id'],'pending_indexes':expected,
                'already_completed_batches':len(state['generation_records'])-len(expected),
                'all_pending_inputs_exactly_preserved':True,'state_and_audit_unchanged':True,'failure_projections':projections})
    for key in ('generate_cases','reconcile_generation_authority','review_generated_cases'):
        schema=next(s['input_schema'] for s in BUILTIN_TOOL_SPECS if s['tool_key']==key)
        assert 'item_indexes' not in schema['properties']
        validate({'guidance':''} if key=='generate_cases' else {},schema)
    report['model_no_longer_selects_batch_indexes']=True
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
