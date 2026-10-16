"""回放真实取证顺序，核验固定原文不会因已读记录变化而过期，重复正文只保留一份。"""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents import Agent, RunContextWrapper
from agents.items import ItemHelpers
from agents.run_config import RunConfig
from agents.run_internal.turn_preparation import maybe_filter_model_input
from agents.tool_context import ToolContext
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from core.db.model_defs import AgentRun, AgentToolDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash, ordered_action_receipts
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter, _function_tool, _runner_input, _validate_runtime_config
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    node = next(n for n in source['nodes'] if n['sdk_state'].get('action_checkpoint'))
    checkpoint = node['sdk_state']['action_checkpoint']
    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    config = deepcopy(next(s['runtime_config'] for s in BUILTIN_AGENT_SPECS if s['agent_key'] == 'test_generation_coordinator'))
    _validate_runtime_config(config, has_tools=True)
    with Session(engine) as db:
        run = source['run']
        db.add(AgentRun(**deepcopy(run))); db.flush()
        context = ToolExecutionContext(db=db, user_id=run['user_id'], project_id=run['project_id'], run_id=run['id'],
            node_key=node['node_key'], run_input=deepcopy(run['input_payload']), action_input=deepcopy(node['input_payload']),
            artifacts=deepcopy(run['run_context']['artifacts']))
        generation.initialize_generation_state(context, context.action_input)
        observer = generation.observe_generation_state(context)
        initial = _runner_input(execution_context=context, input_payload=observer, runtime_config=config)
        filtering = _PersistentStateInputFilter(context=context, runtime_config=config)
        filtering.bind_initial_input(initial, observer)
        spec = next(s for s in BUILTIN_TOOL_SPECS if s['tool_key'] == 'read_planning_evidence')
        tool = _function_tool(AgentToolDefinition(**spec), context, state_input_filter=filtering)
        agent = Agent(name='真实取证回放', tools=[tool])
        inputs = ItemHelpers.input_to_new_input_list(initial)
        calls = []
        for receipt in ordered_action_receipts(checkpoint):
            if receipt['tool_key'] != 'read_planning_evidence':
                break
            assert receipt['status'] == 'completed'
            assert action_state_hash(context.action_state) == receipt['pre_state_hash']
            arguments = receipt['arguments']['arguments_json']
            output = await tool.on_invoke_tool(ToolContext(context=context, tool_name=tool.name,
                tool_call_id=receipt['tool_call_id'], tool_arguments=arguments), arguments)
            assert json.loads(output) == json.loads(receipt['result']['output'])
            assert action_state_hash(context.action_state) == receipt['post_state_hash']
            inputs.extend([{'type':'function_call','call_id':receipt['tool_call_id'],'name':tool.name,'arguments':arguments},
                           {'type':'function_call_output','call_id':receipt['tool_call_id'],'output':output}])
            calls.append(receipt['tool_call_id'])
        assert len(calls) > 1
        filtered = await maybe_filter_model_input(agent=agent, run_config=RunConfig(call_model_input_filter=filtering),
            context_wrapper=RunContextWrapper(context), input_items=deepcopy(inputs), system_instructions='')
        results = [json.loads(item['output']) for item in filtered.input if item.get('type') == 'function_call_output']
        assert not any(item.get('status') == 'historical_state_snapshot' for item in results)
        retained = [item for item in results if 'content' in item]
        duplicates = [item for item in results if item.get('status') == 'duplicate_snapshot']
        assert duplicates and len({action_state_hash(item) for item in retained}) == len(retained)
        assert all(item['current_tool_call_id'] in calls for item in duplicates)
        # 使用同一真实取证输出核对 state 类型的规则仍会淘汰旧领域版本。
        state_config = deepcopy(config)
        state_config['snapshot_tools'][tool.name] = 'state'
        state_filter = _PersistentStateInputFilter(context=context, runtime_config=state_config)
        state_filter.bind_initial_input(initial, observer)
        first = filtering._snapshots[calls[0]]
        state_filter._snapshots[calls[0]] = {**deepcopy(first), 'version_scope':'state',
            'version_sha256': ordered_action_receipts(checkpoint)[0]['post_state_hash']}
        stale = await maybe_filter_model_input(agent=agent, run_config=RunConfig(call_model_input_filter=state_filter),
            context_wrapper=RunContextWrapper(context), input_items=deepcopy(inputs), system_instructions='')
        assert json.loads(stale.input[2]['output'])['status'] == 'historical_state_snapshot'
        report = {'source_run_id':run['id'], 'real_read_calls':len(calls), 'distinct_evidence_outputs_retained':len(retained),
            'duplicate_outputs_compacted':len(duplicates), 'immutable_evidence_not_expired':True,
            'state_snapshot_expiry_preserved':True, 'model_requests':0, 'online_writes':0}
        Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
