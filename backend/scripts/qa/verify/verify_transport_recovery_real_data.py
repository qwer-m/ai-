"""重验真实网络失败回执和已有生成结果，不发送模型请求、不修改线上数据库。"""

from copy import deepcopy
from dataclasses import replace
from functools import partial
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import interrupted_transport_action, ordered_action_receipts
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import AgentOutputContract, _managed_agent_call_identity, _revalidate_managed_agent_call
from modules.agent_platform.test_generation_agent import prepare_generation_action, validate_generation_scope_feedback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    report = {'source_run_id': source['run']['id'], 'model_requests': 0, 'online_writes': 0, 'attempts': []}
    with Session(engine) as replay_db, SessionLocal() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        run = source['run']
        replay_db.add(AgentRun(**deepcopy(run)))
        replay_db.flush()
        definition = AgentDefinitionRepository(db).get_agent(project_id=run['project_id'], agent_key='test_case_generator')
        for node in source['nodes']:
            checkpoint = node['sdk_state'].get('action_checkpoint')
            if not checkpoint:
                continue
            receipt = interrupted_transport_action(checkpoint)
            assert receipt is not None
            assert receipt == ordered_action_receipts(checkpoint)[-1]
            if receipt['tool_key'] != 'generate_cases':
                continue
            context = ToolExecutionContext(db=replay_db, user_id=run['user_id'], project_id=run['project_id'],
                run_id=run['id'], node_key=node['node_key'], run_input=deepcopy(run['input_payload']),
                action_input=deepcopy(node['input_payload']), action_state=deepcopy(checkpoint['state']),
                artifacts=deepcopy(run['run_context']['artifacts']))
            action = prepare_generation_action(context, json.loads(receipt['arguments']['arguments_json']))
            child_context = replace(context, action_state={}, action_input={})
            recovered = []
            for index, request, item in zip(action['item_indexes'], action['requests'], action['items'], strict=True):
                contract = AgentOutputContract(deepcopy(request),
                    AgentMapConfig(item_postprocessor='testing.postprocess_generation_batch_item'),
                    alternate_output_validators={'planning_feedback': partial(validate_generation_scope_feedback, item)})
                identity = _managed_agent_call_identity(execution_semantic_hash=checkpoint['execution_semantic_hash'],
                    agent_key='test_case_generator', contract=contract)
                matches = [call for call in checkpoint['validated_agent_calls'].values() if call['identity'] == identity]
                assert len(matches) <= 1
                if matches:
                    output = _revalidate_managed_agent_call(saved=matches[0], contract=contract,
                        definition=definition, context=child_context)
                    recovered.append({'batch_index': index, 'case_count': len(output.get('test_cases', []))})
            assert recovered
            report['attempts'].append({'node_id': node['id'], 'attempt': node['attempt'],
                'action': receipt['tool_key'], 'requested_batches': len(action['requests']),
                'revalidated_batches': recovered})
        # 使用本次运行真实的旧状态，核对状态变化后不允许重放末次网络失败。
        checkpoints = [n['sdk_state']['action_checkpoint'] for n in source['nodes'] if n['sdk_state'].get('action_checkpoint')]
        changed = deepcopy(checkpoints[-1])
        changed['state'], changed['state_hash'] = deepcopy(checkpoints[0]['state']), checkpoints[0]['state_hash']
        assert interrupted_transport_action(changed) is None
        report['changed_state_rejected'] = True
        completed = deepcopy(checkpoints[-1])
        completed['receipts'] = {k:v for k,v in completed['receipts'].items() if v['status'] == 'completed'}
        assert interrupted_transport_action(completed) is None
        report['completed_action_not_retried'] = True
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
