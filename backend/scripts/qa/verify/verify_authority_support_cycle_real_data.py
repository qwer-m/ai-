"""回放真实规划与权威回执，验证支持修订不会重抽权威结果，未决复核不会循环。"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from core.db.model_defs import AgentRun
from core.db.database import SessionLocal
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.runtime import _agent_request_reservation, _project_agent_map_input
from modules.agent_platform.sdk_adapter import AgentOutputContract, run_agent_async
from scripts.qa.verify.verify_generation_agent_real_model import _capture_current_definitions
from scripts.qa.verify.verify_table_support_context_real_model import SupportTranscript
from modules.agent_platform.test_generation_semantics import (
    prepare_authority_reconciliation, validate_authority_reconciliation_output,
)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--execute-missing', action='store_true')
    parser.add_argument('--review-results', help='复验此前真实模型调用保存的完整输入及输出')
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    run, node = source['run'], source['nodes'][-1]
    checkpoint = node['sdk_state']['action_checkpoint']
    saved = checkpoint['state']
    revisions = saved['planning']['route_review_revisions']
    # 以原始回执中的下游权威问题划分真实修订前后版本，不制作替代模型输出。
    first_authority_revision = next(i for i, record in enumerate(revisions)
        if record['input']['_planning_feedback']['downstream_problems'])
    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    report = {'source_run_id': run['id'], 'model_requests': 0, 'online_writes': 0,
              'formal_workflow_run': False, 'scope': '真实权威支持循环的回放与缺失复核组件验收'}
    with Session(engine) as db:
        db.add(AgentRun(**deepcopy(run)))
        db.flush()
        context = ToolExecutionContext(db=db, user_id=run['user_id'], project_id=run['project_id'],
            run_id=run['id'], node_key=node['node_key'], run_input=deepcopy(run['input_payload']),
            action_input=deepcopy(node['input_payload']), artifacts=deepcopy(run['run_context']['artifacts']),
            action_state=deepcopy(saved))
        versions = []
        for count in (first_authority_revision, len(revisions)):
            child = generation._planning_context(context, context.action_state)
            child.action_state['route_review_revisions'] = deepcopy(revisions[:count])
            planning._commit(child, child.action_state)
            resolved = planning._resolved_output(child)
            inputs = prepare_authority_reconciliation(context, {
                'plan': resolved['plan'], 'authoritative_facts': resolved['source_semantics']['authoritative_facts'],
            })['items']
            versions.append((child.action_state, resolved, inputs))
        before_state, before_plan, before_inputs = versions[0]
        after_state, after_plan, after_inputs = versions[1]
        assert before_inputs == after_inputs
        old_before = [{**item, 'module': before_plan['plan']['business_modules'][item['module_index']]}
                      for item in before_inputs]
        old_after = [{**item, 'module': after_plan['plan']['business_modules'][item['module_index']]}
                     for item in after_inputs]
        changed = [a['module_index'] for a, b in zip(old_before, old_after, strict=True) if a != b]
        assert changed
        cache = {}
        for index, (old, current) in enumerate(zip(old_before, before_inputs, strict=True)):
            matches = [receipt for receipt in checkpoint['validated_agent_calls'].values()
                       if receipt['identity']['agent_key'] == 'test_authority_reconciliation_reviewer'
                       and receipt['identity']['input_hash'] == action_state_hash(old)]
            assert len(matches) == 1, old['module_index']
            output = validate_authority_reconciliation_output(prepared=current, output=matches[0]['output'])
            record = generation._record(current, output, index)
            cache[record['input_hash']] = record
        report['authority'] = {'original_changed_modules': changed,
            'current_changed_modules': [], 'real_outputs_revalidated': len(cache)}

        # 原裁决仍完整有效，接受规划后可从真实缺口进入定向支持复核。
        state = deepcopy(saved)
        generation._invalidate_downstream(state)
        state['planning'] = before_state
        state['authority_cache'] = cache
        generation._save(context, state)
        observation = generation.accept_generation_plan(context, {})
        scope = observation['available_actions']['review_planning_route_gaps']
        assert scope['problem_ids'] and scope['unresolved_design_selections']
        prepared = generation._support_review_arguments(context, {
            'design_selections': [], 'question': '', 'additional_scope_ids': [], 'problem_ids': [],
        })
        assert prepared['problem_ids'] == scope['problem_ids']
        assert prepared['design_selections'] == scope['unresolved_design_selections']
        report['authority_feedback_injected_without_model_ids'] = True
        supported = revisions[first_authority_revision - 1]['input']
        try:
            generation._support_review_arguments(context, {
                'design_selections': [{'module_index': supported['module_index'],
                    'test_design_item_indexes': [d['test_design_item_index'] for d in supported['missing_test_design_items']]}],
                'question': supported['_planning_feedback']['question'], 'additional_scope_ids': [], 'problem_ids': [],
            })
        except OutputValidationError:
            report['already_supported_real_design_not_reopened'] = True
        else:
            raise AssertionError('真实已支持且无新问题的意图不应重新成为复核目标')

        # 逐份应用真实独立复核输出；校验后同意图同证据不能再次出现在待办目录。
        for original in revisions[first_authority_revision:]:
            selection = [{'module_index': original['input']['module_index'],
                          'test_design_item_indexes': [d['test_design_item_index']
                              for d in original['input']['missing_test_design_items']]}]
            args_for_review = generation._support_review_arguments(context, {
                'design_selections': selection, 'question': original['input']['_planning_feedback']['question'],
                'additional_scope_ids': original['input']['additional_scope_ids'], 'problem_ids': [],
            })
            state = deepcopy(context.action_state)
            child = generation._planning_context(context, state)
            child.action_state['downstream_feedback'] = generation._feedback(state, args_for_review['problem_ids'])
            planning._commit(child, child.action_state)
            action = planning.prepare_route_gap_review(child, args_for_review)
            assert len(action['items']) == 1
            assert action['items'][0]['candidate_facts'] == original['input']['candidate_facts']
            planning.apply_route_gap_review(child, action, [deepcopy(original['output'])])
            child.action_state.pop('downstream_feedback', None)
            planning._commit(child, child.action_state)
            state['planning'] = child.action_state
            generation._invalidate_downstream(state, preserve_authority_scope=True)
            generation._save(context, state)
            observation = generation.accept_generation_plan(context, {})
        remaining = observation['available_actions'].get('review_planning_route_gaps')
        report['remaining_after_historical_replay'] = deepcopy(remaining)
        if remaining and (args.execute_missing or args.review_results):
            prepared = generation._support_review_arguments(context, {
                'design_selections': [], 'question': '', 'additional_scope_ids': [], 'problem_ids': [],
            })
            state = deepcopy(context.action_state)
            child = generation._planning_context(context, state)
            child.action_state['downstream_feedback'] = generation._feedback(state, prepared['problem_ids'])
            planning._commit(child, child.action_state)
            action = planning.prepare_route_gap_review(child, prepared)
            outputs = []
            report['live_reviews'] = []
            with SessionLocal(autoflush=False) as online:
                online.execute(text('SET TRANSACTION READ ONLY'))
                snapshots, _, descriptions = _capture_current_definitions(online, db.get(AgentRun, run['id']),
                                                                         'test_planning_route_gap_reviewer')
                definition, tools = snapshots['test_planning_route_gap_reviewer']
                report['definition_snapshots'] = descriptions
                for item in action['items']:
                    contract = AgentOutputContract(deepcopy(item),
                        AgentMapConfig(item_postprocessor='testing.postprocess_planning_route_repair_item'))
                    if args.review_results:
                        recorded = json.loads(Path(args.review_results).read_text(encoding='utf-8'))
                        matches = [entry for entry in recorded['live_reviews'] if entry['input'] == item]
                        assert len(matches) == 1, '只复用完整真实输入逐字段一致的模型响应'
                        output = contract.validate(definition, child, deepcopy(matches[0]['raw_output']))
                        assert output == matches[0]['output']
                        outputs.append(output)
                        report['live_reviews'].append(deepcopy(matches[0]))
                        report['revalidated_model_report'] = args.review_results
                        continue
                    model_input, projection = _project_agent_map_input(definition=definition, raw_item=item)
                    budget = _agent_request_reservation(definition=definition, tools=tools, input_payload=model_input)
                    hooks = AutonomousBudgetHooks(budget)
                    transcript = SupportTranscript(Path(args.output).with_suffix('.requests.jsonl'), item['module_index'])
                    response = await run_agent_async(db=online, agent_definition=definition, tool_definitions=tools,
                        execution_context=child, input_payload=deepcopy(model_input),
                        request_timeout_seconds=definition.runtime_config['request_timeout_seconds'],
                        disable_server_output_schema=True, skip_output_postprocessor=True, output_contract=contract,
                        request_budget=budget, shared_budget_hooks=hooks, require_validated_submission=True,
                        captured_definitions=snapshots, request_diagnostics=transcript)
                    outputs.append(response.validated_output)
                    report['live_reviews'].append({'input': item, 'raw_output': response.output,
                        'output': response.validated_output, 'usage': response.usage, 'projection': projection})
                    report['model_requests'] += response.usage.get('requests', 0)
                    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                assert not online.new and not online.dirty and not online.deleted
                online.rollback()
            planning.apply_route_gap_review(child, action, outputs)
            child.action_state.pop('downstream_feedback', None)
            planning._commit(child, child.action_state)
            state['planning'] = child.action_state
            generation._invalidate_downstream(state, preserve_authority_scope=True)
            generation._save(context, state)
            observation = generation.accept_generation_plan(context, {})
        assert all(record is not None for record in context.action_state['authority_records'])
        assert context.action_state['generation_inputs'], '必须实际跨过原权威支持缺口，不能只比较哈希'
        assert not context.action_state['problems']
        assert 'generate_cases' in observation['available_actions']
        assert 'review_planning_route_gaps' not in observation['available_actions']
        assert 'rebuild_business_plan' not in observation['available_actions']
        report['real_support_revisions_applied'] = len(revisions) - first_authority_revision
        report['generation_batches_ready'] = len(context.action_state['generation_inputs'])
        report['planned_case_count'] = sum(item['case_budget'] for item in context.action_state['generation_inputs'])
        report['authority_model_calls_needed_after_support_repair'] = 0
        invalidated = deepcopy(context.action_state)
        generation._invalidate_downstream(invalidated)
        assert invalidated['authority_inputs'] == invalidated['authority_records'] == []
        assert invalidated['accepted_plan'] is None and invalidated['effective_facts'] is None
        report['source_or_plan_invalidation_clears_authority_scope'] = True
        # 使用另一真实模块证明事实范围不同仍然不会命中原缓存。
        assert generation._retain([after_inputs[1]], {action_state_hash(after_inputs[0]): cache[action_state_hash(after_inputs[0])]}) == [None]
        report['different_real_scope_not_reused'] = True
        assert action_state_hash(saved) == checkpoint['state_hash']
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {'live_reviews', 'definition_snapshots'}}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
