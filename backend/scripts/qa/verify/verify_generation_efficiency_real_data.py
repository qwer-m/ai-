"""使用 Run 12 的真实候选、失败提交和审查状态核对输入优化与自动完成边界。"""

import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentNodeRun, AgentWorkflowDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig, parse_execution_definition
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, _PersistentStateInputFilter, _function_tool
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_WORKFLOW_SPECS
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions
from scripts.qa.verify.verify_planning_agent_real_model import planning_journal


async def main():
    root = Path('output/qa')
    output = root / '2026-10-13-generation-efficiency-regression.json'
    if output.exists():
        raise FileExistsError(output)
    ready = json.loads((root / '2026-10-13-run12-generation-ready.json').read_text(encoding='utf-8'))
    final = json.loads((root / '2026-10-13-run12-final-source.json').read_text(encoding='utf-8'))
    ready_node = next(n for n in ready['nodes'] if n['id'] == 108)
    final_node = next(n for n in final['nodes'] if n['id'] == 108)
    report = {'source_run_id': 12, 'model_requests': 0, 'online_writes': 0, 'checks': {}}
    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    with Session(engine) as replay, SessionLocal(autoflush=False) as online:
        online.execute(text('SET TRANSACTION READ ONLY'))
        replay.add(AgentRun(**deepcopy(ready['run'])))
        replay.flush()
        run = online.get(AgentRun, 12)
        snapshots, _, _ = _capture_current_definitions(online, run, 'test_generation_coordinator')
        context = ToolExecutionContext(db=replay, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=ready_node['node_key'], run_input=deepcopy(run.input_payload),
            action_input=deepcopy(ready_node['input_payload']),
            action_state=deepcopy(ready_node['sdk_state']['action_checkpoint']['state']),
            artifacts=deepcopy(ready['run']['run_context']['artifacts']))
        initial_hash = action_state_hash(context.action_state)
        action = generation.prepare_generation_action(context, {
            'item_indexes': list(range(len(context.action_state['generation_inputs']))), 'guidance': ''})
        definition = snapshots['test_case_generator'][0]
        size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
        new_bytes = old_bytes = 0
        for item, request in zip(action['items'], action['requests'], strict=True):
            contract = request['case_fact_contract']
            mapped = {entry['test_design_item_id']: set(entry['support_fact_ids'])
                      for entry in contract['design_support_requirements']}
            expected = {key: {fact for fact, ids in contract['fact_design_item_ids'].items() if key in ids}
                        for key in contract['required_test_design_item_ids']}
            assert mapped == expected
            projected, _ = _project_agent_map_input(definition=definition, raw_item=request)
            assert projected['authoritative_facts'] == _project_agent_map_input(
                definition=definition, raw_item=item)[0]['authoritative_facts']
            new_bytes += size(projected)
        for call in final_node['sdk_state']['tool_calls']:
            if call['tool_key'] == 'agent:test_case_generator':
                old_bytes += call['input_projection']['model_json_bytes']
        report['checks']['complete_support_sets_and_source_context_preserved'] = len(action['items'])
        report['first_generation_model_bytes'] = new_bytes
        report['historical_generation_model_bytes_including_retry'] = old_bytes
        assert action_state_hash(context.action_state) == initial_hash

        rejected = accepted = 0
        historical_inputs = {}
        for receipt in final_node['sdk_state']['action_checkpoint']['receipts'].values():
            if receipt['tool_key'] != 'generate_cases' or receipt['status'] != 'completed':
                continue
            guidance = json.loads(receipt['arguments']['arguments_json']).get('guidance') or ''
            for item in action['items']:
                original_request = deepcopy(item)
                if guidance:
                    original_request['_planning_feedback'] = {'instruction': guidance, 'source': '协调者建议，不是新增事实或预算'}
                historical_inputs[action_state_hash(original_request)] = original_request
        for call in final_node['sdk_state']['tool_calls']:
            if call['tool_key'] != 'agent:test_case_generator':
                continue
            item = historical_inputs.get(call['input_hash'])
            if item is None:
                continue  # 带协调修正说明的后续请求不是初次完整输入。
            contract = AgentOutputContract(item, AgentMapConfig(item_postprocessor='testing.postprocess_generation_batch_item'))
            observations = {e['tool_call_id']: e for e in call['tool_observations']}
            for submission in call['submissions']:
                if submission['tool_key'] != 'submit_generation_batch':
                    continue
                event = observations[submission['tool_call_id']]
                try:
                    contract.validate(definition, context, deepcopy(submission['arguments']))
                except Exception:
                    assert event['status'] == 'rejected'
                    rejected += 1
                else:
                    assert event['status'] == 'accepted'
                    accepted += 1
        assert rejected > 0 and accepted > 0
        report['checks']['real_failed_submissions_still_rejected'] = rejected
        report['checks']['real_accepted_submissions_still_valid'] = accepted

        workflow = online.get(AgentWorkflowDefinition, run.workflow_definition_id)
        source_node = online.get(AgentNodeRun, 108)
        spec = next(s for s in BUILTIN_WORKFLOW_SPECS if s['workflow_key'] == 'test_generation')
        node_spec = next(n for n in parse_execution_definition(spec['definition']).nodes if n.node_key == 'resolve_generation')
        journal_path = root / ('2026-10-13-generation-efficiency-terminal-' + datetime.now().strftime('%H%M%S') + '.sqlite')
        report['isolated_action_database'] = str(journal_path)
        with planning_journal(journal_path, run=run,
            workflow=workflow, source_node=source_node, graph_spec=spec, node_spec=node_spec,
            raw_input=final_node['input_payload'], timeout=180) as (journal, copied_node, token):
            ctx = ToolExecutionContext(db=journal.repo.db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key=node_spec.node_key, run_input=deepcopy(run.input_payload),
                action_input=deepcopy(final_node['input_payload']), claim_token=token,
                artifacts=deepcopy(final['run']['run_context']['artifacts']), action_journal=journal)
            state = deepcopy(final_node['sdk_state']['action_checkpoint']['state'])
            # 重放终止动作之前的真实已审状态，唯一撤回的是动作自身写入的完成标记。
            state['completed'] = False
            generation._save(ctx, state)
            coordinator, tools = snapshots['test_generation_coordinator']
            state_filter = _PersistentStateInputFilter(context=ctx, runtime_config=coordinator.runtime_config)
            contract = AgentOutputContract(ctx.action_input, None)
            loop = AutonomousToolLoop(terminal_tool_names={'finish_generation'}, max_tool_calls=160,
                validate_submission=lambda raw: generation.validate_generation_resolution(ctx, {
                    'input_payload': ctx.action_input, 'output': raw}),
                recoverable_errors=(OutputValidationError,), action_context=ctx)
            terminal = next(tool for tool in tools if tool.tool_key == 'finish_generation')
            wrapped = [loop.wrap_tool(_function_tool(terminal, ctx, state_input_filter=state_filter), allow_feedback=True)]
            state_filter.validate_terminal_tool(wrapped)
            # 初审真实状态仍有两个待修批次，自动完成入口必须保持关闭。
            approved = deepcopy(ctx.action_state)
            initial_review = generation.review.compose_review_context(ctx, input_payload=state['review']['input'])
            generation.review.initialize_review_state(initial_review, initial_review.action_input)
            ctx.action_state['review']['state'] = initial_review.action_state
            generation._save(ctx, ctx.action_state)
            assert not await state_filter.complete_if_ready(wrapped, loop)
            assert not loop.events
            ctx.action_state = approved
            journal.init(input_hash=action_state_hash(ctx.action_input), state=ctx.action_state, budget={})
            assert await state_filter.complete_if_ready(wrapped, loop)
            resolved = loop.require_submission().normalized_output
            assert resolved['generation'] == final_node['output_payload']['generation']
            assert not await state_filter.complete_if_ready(wrapped, loop)
            saved = journal.load()
            assert saved['state']['completed'] and len(saved['receipts']) == 1
            assert next(iter(saved['receipts'].values()))['status'] == 'completed'
            report['checks']['unapproved_review_does_not_finish'] = True
            report['checks']['automatic_finish_preserves_50_reviewed_cases_and_receipt'] = len(resolved['generation']['test_cases'])
        online.rollback()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
