"""回放真实修复补丁并可执行真实专家调用，验证终审批次调度及来源元信息归类。"""
from __future__ import annotations

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
from modules.agent_platform import test_generation_review_agent as review
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from modules.agent_platform.test_generation_semantics import prepare_authority_reconciliation
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript


def restore_review(source, db):
    """由真实生成输入和完整缓存还原重规划之前的终审领域，并核对其身份。"""
    nodes = [node for node in source['nodes'] if node['sdk_state'].get('action_checkpoint')]
    latest = nodes[-1]['sdk_state']['action_checkpoint']
    previous = next(node for node in reversed(nodes) if node['sdk_state']['action_checkpoint']['state']['generation_inputs'])
    state = deepcopy(previous['sdk_state']['action_checkpoint']['state'])
    run = source['run']
    context = ToolExecutionContext(db=db, user_id=run['user_id'], project_id=run['project_id'], run_id=run['id'],
        node_key=nodes[-1]['node_key'], run_input=deepcopy(run['input_payload']),
        action_input=deepcopy(nodes[-1]['input_payload']), artifacts=deepcopy(run['run_context']['artifacts']))
    for key in ('generation_cache', 'review_cache', 'review_domains'):
        state[key] = deepcopy(latest['state'][key])
    state['generation_records'] = generation._retain(state['generation_inputs'], state['generation_cache'])
    assert all(state['generation_records'])
    generation._prepare_review(context, state)
    generation._save(context, state)
    assert state['review']['state'] == latest['state']['review_domains'][action_state_hash(state['review']['input'])]
    return context, latest


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--recheck-report', help='使用真实调用报告中未通过的完整复审输入验证当前审查定义')
    args = parser.parse_args()
    path = Path(args.output)
    if path.exists():
        raise FileExistsError('请使用新的输出路径，保留原始验证证据')
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    report = {'source_run_id': source['run']['id'], 'online_writes': 0, 'formal_workflow_run': False,
              'model_requests': 0, 'live_calls': []}

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    with Session(engine) as db, SessionLocal(autoflush=False) as online:
        online.execute(text('SET TRANSACTION READ ONLY'))
        db.add(AgentRun(**deepcopy(source['run'])))
        db.flush()
        context, checkpoint = restore_review(source, db)
        snapshots, _, descriptions = _capture_current_definitions(online, db.get(AgentRun, context.run_id),
                                                                 'test_generation_coordinator')
        report['definitions'] = descriptions
        source_review = context.action_state['review']['input']
        initial = review.compose_review_context(context, input_payload=source_review)
        pending = review.observe_review_state(initial)['repairable_batch_ids']
        historical = {}
        # 响应必须来自完整输入哈希匹配的真实回执，不制造替代专业输出。
        for batch_id in pending:
            action = review.prepare_review_repair(initial, {'batch_id': batch_id})
            records = []
            for item in action['items']:
                matches = [entry for entry in checkpoint['validated_agent_calls'].values()
                    if entry['identity']['agent_key'] == 'test_generation_batch_repairer'
                    and entry['identity']['input_hash'] == action_state_hash(item)]
                assert len(matches) == 1, item['review_batch']['batch_id']
                records.append(matches[0])
            historical[batch_id] = records
        report['historical_repair_tasks'] = sum(len(records) for records in historical.values())
        report['current_repair_tasks'] = sum(len(review.prepare_review_repair(initial, {'batch_id': batch_id})['items'])
                                               for batch_id in pending)
        assert report['current_repair_tasks'] == report['historical_repair_tasks']

        replayed = []
        async def replay(*, agent_key, input_payload, item_postprocessor, validate_candidate=None):
            if agent_key == 'test_generation_batch_repairer':
                records = historical[input_payload['review_batch']['batch_id']]
                raw = {'case_patches': [deepcopy(patch) for record in records for patch in record['raw_output']['case_patches']]}
            else:
                assert agent_key == 'test_generation_final_reviewer'
                records = [entry for entry in checkpoint['validated_agent_calls'].values()
                           if entry['identity']['agent_key'] == agent_key
                           and entry['identity']['input_hash'] == action_state_hash(input_payload)]
                assert len(records) == 1
                raw = deepcopy(records[0]['raw_output'])
            definition, _ = snapshots[agent_key]
            contract = AgentOutputContract(deepcopy(input_payload), AgentMapConfig(item_postprocessor=item_postprocessor),
                                           candidate_validator=validate_candidate)
            output = contract.validate(definition, initial, raw)
            if agent_key == 'test_generation_batch_repairer':
                expected = {case['case_id']: case for record in records for case in record['output']['test_cases']}
                assert {case['case_id']: case for case in output['test_cases']} == expected
            else:
                assert output == records[0]['output']
            replayed.append(input_payload['review_batch']['batch_id'])
            return output

        initial.invoke_agent, initial.agent_concurrency = replay, 6
        await review.repair_review_batch(initial, {'batch_ids': pending})
        assert set(replayed) == set(pending)
        assert initial.action_state['generation'] == context.action_state['review']['state']['generation']
        assert set(initial.action_state['pending_rechecks']) == set(pending)
        report['historical_patches_revalidated'] = len(replayed)
        report['repaired_generation_exactly_matches_saved_run'] = True
        # 修复尚未独立复审时不能把候选当成通过，更不能结束终审。
        assert not review.observe_review_state(initial)['can_finish']
        report['unreviewed_repair_cannot_finish'] = True
        await review.recheck_review_batch(initial, {'batch_ids': pending})
        assert initial.action_state['review_records'] == context.action_state['review']['state']['review_records']
        assert initial.action_state['pending_rechecks'] == {}
        report['historical_rechecks_revalidated'] = len(pending)
        report['recheck_results_exactly_match_saved_run'] = True

        observed = generation.observe_generation_state(context)
        case_ids = {item['problem_id'] for item in generation._current_problems(context.action_state)
                    if item['origin'] == 'review' and item['payload']['repair_scope'] == 'case'}
        assert case_ids
        for key in ('rebuild_business_plan', 'revise_planning_draft', 'revise_generation_layout'):
            assert not case_ids.intersection(observed['available_actions'].get(key, {}).get('problem_ids', []))
        report['case_differences_cannot_trigger_plan_or_layout_rebuild'] = len(case_ids)
        report['replay_scope'] = '真实旧补丁沿并发批次入口重验，不代表新模型已生成这些补丁或完成独立终审'
        save()

        if args.execute or args.recheck_report:
            async def live(*, agent_key, input_payload, item_postprocessor, validate_candidate=None):
                definition, tools = snapshots[agent_key]
                child = replace(context, action_input={}, action_state={}, artifacts={}, executed_tools=[], tool_calls=[])
                contract = AgentOutputContract(deepcopy(input_payload), AgentMapConfig(item_postprocessor=item_postprocessor),
                                               candidate_validator=validate_candidate)
                model_input, projection = _project_agent_map_input(definition=definition, raw_item=input_payload)
                budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
                hooks = AutonomousBudgetHooks(budget)
                response = await run_agent_async(db=online, agent_definition=definition, tool_definitions=tools,
                    execution_context=child, input_payload=model_input,
                    request_timeout_seconds=definition.runtime_config['request_timeout_seconds'],
                    disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                    request_budget=budget, shared_budget_hooks=hooks, require_validated_submission=True,
                    captured_definitions=snapshots,
                    request_diagnostics=SupportTranscript(path.with_suffix('.requests.jsonl'), len(report['live_calls'])))
                report['live_calls'].append({'agent_key': agent_key, 'input': input_payload, 'raw_output': response.output,
                    'output': response.validated_output, 'usage': response.usage, 'projection': projection})
                report['model_requests'] += response.usage.get('requests', 0)
                save()
                return response.validated_output

            if args.recheck_report:
                previous = json.loads(Path(args.recheck_report).read_text(encoding='utf-8'))
                inputs = [entry['input'] for entry in previous['live_calls']
                          if entry['agent_key'] == 'test_generation_final_reviewer' and not entry['output']['approved']]
                context.invoke_agent, context.agent_concurrency = live, 6
                await context.invoke_agents(agent_key='test_generation_final_reviewer', inputs=deepcopy(inputs),
                                            item_postprocessor='testing.postprocess_final_review_batch_item')
                report['recheck_source_report'] = args.recheck_report
                report['status'] = 'passed'
                save()
                print(json.dumps([{'batch_id': entry['input']['review_batch']['batch_id'],
                                   'approved': entry['output']['approved'], 'usage': entry['usage']}
                                  for entry in report['live_calls']], ensure_ascii=False))
                return

            # 对触发真实重规划的来源模块执行独立权威复核，不直接改写事实状态。
            metadata = next(fact for fact in context.action_state['effective_facts']['authoritative_facts']
                            if fact['fact_id'] == 'DOC283-P0006-10')
            authority_inputs = prepare_authority_reconciliation(context, {
                'plan': context.action_state['accepted_plan']['plan'],
                'authoritative_facts': context.action_state['accepted_plan']['source_semantics']['authoritative_facts'],
            })['items']
            authority = next(item for item in authority_inputs
                             if metadata['fact_id'] in {fact['fact_id'] for fact in item['authoritative_facts']})
            result = await live(agent_key='test_authority_reconciliation_reviewer', input_payload=deepcopy(authority),
                                item_postprocessor='testing.postprocess_authority_reconciliation_item')
            decisions = {item['fact_id']: item for item in result['decisions']}
            assert decisions[metadata['fact_id']]['status'] == 'reference_only'
            report['document_metadata_independently_reclassified'] = decisions[metadata['fact_id']]
            save()
            # 使用已存在真实问题与候选验证第二轮并发修复，不修改线上失败运行。
            child = generation._review_context(context, context.action_state)
            selected = ['R-001', 'R-002', 'R-011']
            child.invoke_agent, child.agent_concurrency = live, 6
            await review.repair_review_batch(child, {'batch_ids': selected})
            report['live_repair_state'] = deepcopy(child.action_state)
            save()
            await review.recheck_review_batch(child, {'batch_ids': selected})
            report['live_recheck_state'] = deepcopy(child.action_state)
            report['live_observation'] = review.observe_review_state(child)
            save()
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
    report['status'] = 'passed'
    save()
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {'definitions', 'live_calls', 'live_repair_state', 'live_recheck_state', 'live_observation'}},
                     ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
