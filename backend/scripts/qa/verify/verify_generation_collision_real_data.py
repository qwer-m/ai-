"""复验真实跨批次重复失败，并可对原待生成批次执行真实模型修复；不写线上数据。"""
import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform.test_generation_batching import validate_generation_case_uniqueness
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    path = Path(args.output)
    if path.exists():
        raise FileExistsError(path)
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    node = next(n for n in reversed(source['nodes']) if n['sdk_state'].get('action_checkpoint'))
    saved = node['sdk_state']['action_checkpoint']
    failed = next(r for r in reversed(sorted(saved['receipts'].values(), key=lambda x:x['started_sequence']))
                  if r['tool_key'] == 'generate_cases' and r['status'] == 'failed')
    candidate = next(r for r in saved['validated_agent_calls'].values()
                     if r['parent_tool_call_id'] == failed['tool_call_id'] and r['identity']['agent_key'] == 'test_case_generator')
    existing = []
    invalid_indexes = []
    report = {'source_run_id': source['run']['id'], 'model_requests': 0, 'online_writes': 0}
    for index, record in enumerate(saved['state']['generation_records']):
        if record is None:
            continue
        try:
            validate_generation_case_uniqueness(record['output']['test_cases'], existing)
        except OutputValidationError as error:
            report['original_collision_rejected_at_submission'] = error.details
            invalid_indexes.append(index)
        else:
            existing.extend(record['output']['test_cases'])
    assert invalid_indexes, '真实重复候选未在提交验收被拒绝'
    validate_generation_case_uniqueness(existing)
    report['previous_valid_cases_preserved'] = len(existing)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not args.execute:
        print(json.dumps({k:v for k,v in report.items() if k != 'original_collision_rejected_at_submission'}, ensure_ascii=False))
        return
    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    with Session(engine) as db, SessionLocal(autoflush=False) as online:
        online.execute(text('SET TRANSACTION READ ONLY'))
        run = AgentRun(**deepcopy(source['run']))
        db.add(run)
        db.flush()
        snapshots, _, _ = _capture_current_definitions(online, run, 'test_generation_coordinator')
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node['node_key'], run_input=deepcopy(run.input_payload),
            artifacts=deepcopy(run.run_context['artifacts']), action_input=deepcopy(node['input_payload']),
            action_state=deepcopy(saved['state']), agent_concurrency=6)
        # 历史旧入口已接纳了重复候选；隔离回放撤回这些不合格批次，并保留其他真实完整验收结果。
        for index in invalid_indexes:
            context.action_state['generation_records'][index] = None
        for index, record in enumerate(context.action_state['generation_records']):
            if record is not None or index in invalid_indexes:
                continue
            item = context.action_state['generation_inputs'][index]
            normalized = generation._validate_generation_batch_output(source_input=item, output=candidate['output'])
            validate_generation_case_uniqueness(normalized['test_cases'], existing)
            context.action_state['generation_records'][index] = generation._record(item, normalized, index)
        generation._save(context, context.action_state)
        report['historical_duplicate_batches_invalidated_for_replay'] = invalid_indexes
        async def live(*, agent_key, input_payload, item_postprocessor, validate_candidate=None,
                       alternate_output_validators=None):
            definition, tools = snapshots[agent_key]
            contract = AgentOutputContract(deepcopy(input_payload), AgentMapConfig(item_postprocessor=item_postprocessor),
                candidate_validator=validate_candidate, alternate_output_validators=alternate_output_validators)
            model_input, projection = _project_agent_map_input(definition=definition, raw_item=input_payload)
            budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
            child = replace(context, artifacts={}, tool_calls=[], executed_tools=[], action_state={}, action_input={})
            result = await run_agent_async(db=online, agent_definition=definition, tool_definitions=tools,
                execution_context=child, input_payload=model_input, request_timeout_seconds=180,
                disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                request_budget=budget, shared_budget_hooks=AutonomousBudgetHooks(budget), require_validated_submission=True,
                captured_definitions=snapshots)
            report['model_requests'] += result.usage['requests']
            report['live_result'] = {'usage': result.usage, 'output': result.output,
                'tool_observations': result.tool_observations, 'model_requests': result.model_requests,
                'projection': projection, 'input': input_payload}
            return result.validated_output
        context.invoke_agent = live
        try:
            await generation.generate_cases(context, {'guidance': '上一候选与其他已生成批次重复。请根据本批完整事实和覆盖契约生成独立用例，若提交返回重复冲突，请核对冲突的实际条件和业务行为，不能仅修改标题。'})
            report['generation_ready'] = context.action_state['generation'] is not None
            report['case_count'] = (context.action_state['generation'] or {}).get('case_count', 0)
            report['remaining_problems'] = context.action_state['problems']
            report['state'] = context.action_state
            report['scope'] = '原失败批次真实生成及合并验证，尚未通过新版本独立终审，不是最终交付'
        finally:
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        online.rollback()
    print(json.dumps({k:v for k,v in report.items() if k not in {'state','live_result','original_collision_rejected_at_submission','remaining_problems'}}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
