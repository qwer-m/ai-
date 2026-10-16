"""核对真实当前状态中的行动错误和终审观察，防止旧目录及已完成规划反复进入模型请求。"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash, project_action_failures
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--current-failure-source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    nodes = [n for n in source['nodes'] if n['sdk_state'].get('action_checkpoint')]
    report = {'source_run_id':source['run']['id'],'model_requests':0,'online_writes':0,'failure_projections':[]}
    for node in nodes:
        checkpoint = node['sdk_state']['action_checkpoint']
        before = action_state_hash(checkpoint)
        failures = project_action_failures(checkpoint,state=checkpoint['state'])
        assert all(item['state_matches_failure'] for item in failures)
        stale = [r for r in checkpoint['receipts'].values() if r['status']=='failed' and r['pre_state_hash']!=checkpoint['state_hash']]
        assert all(r['tool_call_id'] not in {f['tool_call_id'] for f in failures} for r in stale)
        assert action_state_hash(checkpoint) == before
        report['failure_projections'].append({'node_id':node['id'],'current_failures':len(failures),
            'stale_receipts_preserved_but_not_projected':len(stale)})
    assert report['failure_projections'][-1]['stale_receipts_preserved_but_not_projected'] > 0
    failed_source=json.loads(Path(args.current_failure_source).read_text(encoding='utf-8'))
    failed_checkpoint=next(n['sdk_state']['action_checkpoint'] for n in reversed(failed_source['nodes'])
                           if n['sdk_state'].get('action_checkpoint'))
    active_failures=project_action_failures(failed_checkpoint,state=failed_checkpoint['state'])
    assert active_failures and all(item['state_matches_failure'] for item in active_failures)
    report['same_version_failures_preserved']={'source_run_id':failed_source['run']['id'],'count':len(active_failures)}
    node=nodes[-1];checkpoint=node['sdk_state']['action_checkpoint']
    engine=create_engine('sqlite+pysqlite:///:memory:');AgentRun.__table__.create(engine)
    with Session(engine) as db:
        run=source['run'];db.add(AgentRun(**deepcopy(run)));db.flush()
        context=ToolExecutionContext(db=db,user_id=run['user_id'],project_id=run['project_id'],run_id=run['id'],
            node_key=node['node_key'],run_input=deepcopy(run['input_payload']),action_input=deepcopy(node['input_payload']),
            action_state=deepcopy(checkpoint['state']),artifacts=deepcopy(run['run_context']['artifacts']))
        observation=generation.observe_generation_state(context)
        state,plan=generation._state_snapshot(context)
        reviewed=generation.review.observe_review_state(generation._review_context(context,state))
        assert observation['review']==reviewed
        assert observation['available_actions']==generation._available_actions(state,plan,reviewed)
        before=deepcopy(observation)
        before['planning']=generation.planning.compact_planning_observation(plan)
        before['initial_review_batches']=[{'item_index':i,**deepcopy(item['review_batch']),
            'status':'pending' if state['review_records'][i] is None else 'reviewed'} for i,item in enumerate(state['review_inputs'])]
        size=lambda value:len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
        report['observation_before_bytes']=size(before)
        report['observation_after_bytes']=size(observation)
        assert report['observation_after_bytes'] < report['observation_before_bytes']
        try:
            generation._require_action(context,'recheck_review_batch')
        except OutputValidationError as error:
            feedback=AutonomousToolLoop._feedback(error)
            assert error.details=={'action':'recheck_review_batch'}
            assert 'available_actions' not in error.details
            report['unavailable_action_feedback_bytes']=size(feedback)
        else:
            raise AssertionError('真实无待复审批次状态未拒绝复审')
        assert action_state_hash(context.action_state)==checkpoint['state_hash']
        report['current_reviews_actions_and_domain_unchanged']=True
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
