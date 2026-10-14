"""用真实终审回执验证范围读取去重，保留完整当前用例、事实、绑定和审查结论。"""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.tool_context import ToolContext
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from core.db.model_defs import AgentRun, AgentToolDefinition
from modules.agent_platform.action_journal import action_state_hash, ordered_action_receipts
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import _function_tool
from modules.agent_platform.test_generation_agent import observe_generation_state, inspect_generation_scope
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS
from modules.agent_platform.output_repair import OutputValidationError


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    node = next(n for n in reversed(source['nodes']) if n['sdk_state'].get('action_checkpoint'))
    checkpoint = node['sdk_state']['action_checkpoint']
    historical = []
    for receipt in ordered_action_receipts(checkpoint):
        if (receipt['tool_key'] == 'inspect_generation_scope' and receipt['status'] == 'completed'
                and receipt['pre_state_hash'] == checkpoint['state_hash']):
            output = json.loads(receipt['result']['output'])
            if output.get('review_scopes'):
                historical.append(output)
    assert historical
    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    with Session(engine) as db:
        run = source['run']; db.add(AgentRun(**deepcopy(run))); db.flush()
        context = ToolExecutionContext(db=db, user_id=run['user_id'], project_id=run['project_id'], run_id=run['id'],
            node_key=node['node_key'], run_input=deepcopy(run['input_payload']), action_input=deepcopy(node['input_payload']),
            action_state=deepcopy(checkpoint['state']), artifacts=deepcopy(run['run_context']['artifacts']))
        scope = observe_generation_state(context)['available_actions']['inspect_generation_scope']
        assert not scope['item_indexes']
        tool = _function_tool(AgentToolDefinition(**next(s for s in BUILTIN_TOOL_SPECS if s['tool_key']=='inspect_generation_scope')), context)
        outputs = []
        for batch_id in scope['review_batch_ids']:
            arguments = json.dumps({'item_index':None,'problem_id':None,'review_batch_id':batch_id})
            result = await tool.on_invoke_tool(ToolContext(context=context,tool_name=tool.name,
                tool_call_id=f'real_scope_{batch_id}',tool_arguments=arguments),arguments)
            output = json.loads(result); outputs.append(output)
            previous = [value for item in historical for value in item['review_scopes']
                        if value['input']['review_batch']['batch_id'] == batch_id]
            assert previous and all(value == output['review_scope'] for value in previous)
        try:
            inspect_generation_scope(context, {'item_index':historical[0]['item_index']})
        except OutputValidationError:
            pass
        else:
            raise AssertionError('终审仍可绕过批次目录重复读取原生成输入')
        assert action_state_hash(context.action_state) == checkpoint['state_hash']
        compact = lambda value: json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')
        before = sum(len(compact(value)) for value in historical)
        after = sum(len(compact(value)) for value in outputs)
        report = {'source_run_id':run['id'],'old_scope_reads':len(historical),'unique_review_scopes':len(outputs),
            'old_output_bytes':before,'new_output_bytes':after,'reduction_percent':round((before-after)*100/before,2),
            'complete_review_inputs_and_outputs_unchanged':True,'domain_state_unchanged':True,
            'model_requests':0,'online_writes':0}
        Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
