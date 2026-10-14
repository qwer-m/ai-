"""重放真实 Run 的规划产物，验证行动目录、复核去重与完整输入缓存；不调用模型或写业务库。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentToolDefinition
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.sdk_adapter import _PersistentStateInputFilter, _ActionCatalogModel, _function_tool, _sdk_model
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_planning import planning_support_design_hashes
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from core.ai.ai_client import get_client_for_user
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--authority-source', help='包含真实权威缺口回执的运行快照')
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding='utf-8'))
    run = source['run']
    node = [value for value in source['nodes'] if value['sdk_state'].get('action_checkpoint')][-1]
    checkpoint = node['sdk_state']['action_checkpoint']
    report = {'source_run_id': run['id'], 'checks': {}, 'model_requests': 0, 'business_writes': 0}
    # 历史运行可能已按平台保留策略清理；回放直接使用此前保存的真实记录，不依赖线上历史留存。
    engine = create_engine('sqlite+pysqlite:///:memory:')
    AgentRun.__table__.create(engine)
    with SessionLocal(autoflush=False) as db, Session(engine) as replay_db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        replay_db.add(AgentRun(**deepcopy(run)))
        replay_db.flush()
        context = ToolExecutionContext(db=replay_db, user_id=run['user_id'], project_id=run['project_id'],
            run_id=run['id'], node_key=node['node_key'], run_input=deepcopy(run['input_payload']),
            action_input=deepcopy(node['input_payload']), artifacts=deepcopy(run['run_context']['artifacts']))
        generation.initialize_generation_state(context, context.action_input)
        spec = next(value for value in BUILTIN_AGENT_SPECS if value['runtime_config'].get('state_observer') == 'testing.observe_generation_state')
        state_filter = _PersistentStateInputFilter(context=context, runtime_config=spec['runtime_config'])
        tools = [_function_tool(AgentToolDefinition(**value), context) for value in BUILTIN_TOOL_SPECS
                 if value['tool_key'] in spec['runtime_config']['tool_keys']]
        definition = AgentDefinitionRepository(db).get_agent(project_id=run['project_id'], agent_key=spec['agent_key'])
        clients = []
        model = _sdk_model(get_client_for_user(run['user_id'], db), definition,
                           request_timeout_seconds=None, allocated_clients=clients, model_route_override=None)
        wrapped = _ActionCatalogModel(model, state_filter)
        available = {tool.name for tool in wrapped._request({'tools': tools})['tools']}
        assert available == set(generation.observe_generation_state(context)['available_actions'])
        assert 'accept_generation_plan' not in available and 'generate_cases' not in available
        assert 'accept_generation_plan' in {tool.name for tool in tools}
        report['checks']['model_tools_match_legal_actions_with_stable_sdk_registry'] = sorted(available)

        child = planning._isolated_context(context)
        child.action_input = deepcopy(context.action_input)
        child.action_state = deepcopy(context.action_state['planning'])
        callbacks = {
            'rebuild_business_plan': (planning.prepare_business_plan_rebuild, planning.apply_business_plan_rebuild, 'test_business_planner'),
            'route_planning_scopes': (planning.prepare_scope_routing, planning.apply_scope_routing, 'test_planning_scope_router'),
            'review_planning_route_gaps': (planning.prepare_route_gap_review, planning.apply_route_gap_review, 'test_planning_route_gap_reviewer'),
        }
        replayed = []
        for receipt in sorted(checkpoint['receipts'].values(), key=lambda value: value['started_sequence']):
            key = receipt['tool_key']
            if receipt['status'] != 'completed' or key not in callbacks:
                continue
            prepare, apply, agent_key = callbacks[key]
            arguments = json.loads(receipt['arguments']['arguments_json'])
            action = prepare(child, arguments)
            outputs = []
            for item in action['items']:
                matches = [value for value in checkpoint['validated_agent_calls'].values()
                           if value['parent_tool_call_id'] == receipt['tool_call_id']
                           and value['identity']['agent_key'] == agent_key
                           and value['identity']['input_hash'] == action_state_hash(item)]
                assert len(matches) == 1, (key, action_state_hash(item))
                outputs.append(deepcopy(matches[0]['output']))
            apply(child, action, outputs)
            replayed.append({'action': key, 'validated_outputs': len(outputs)})
            if key == 'review_planning_route_gaps':
                break
        assert len(replayed) == 3
        report['checks']['real_planning_outputs_revalidated'] = replayed
        before = deepcopy(child.action_state)
        planning._clear_plan(child.action_state)
        child.action_state['plan'] = deepcopy(before['plan'])
        planning._refresh_routes(child, child.action_state)
        assert child.action_state['route_records'] == before['route_records']
        assert child.action_state['gap_records'] == before['gap_records']
        # 使用另一实际批次作为不同输入，不能按位置错误命中前一批次。
        inputs = before['gap_inputs']
        assert len(inputs) > 1
        assert planning._retained_records([inputs[1]], [before['gap_records'][0]]) == [None]
        report['checks']['unchanged_full_inputs_retained_and_different_real_input_rejected'] = True
        multi = next(item for item in inputs if len(item['missing_test_design_items']) > 1)
        single = {**multi, 'missing_test_design_items': multi['missing_test_design_items'][:1]}
        assert planning_support_design_hashes(single) <= planning_support_design_hashes(multi)
        report['checks']['regrouping_does_not_change_design_review_identity'] = True
        if args.authority_source:
            authority_source = json.loads(Path(args.authority_source).read_text(encoding='utf-8'))
            authority_run = authority_source['run']
            authority_node = [value for value in authority_source['nodes'] if value['sdk_state'].get('action_checkpoint')][-1]
            saved = authority_node['sdk_state']['action_checkpoint']
            problems = []
            for receipt in saved['receipts'].values():
                if receipt['tool_key'] == 'inspect_generation_scope' and receipt['status'] == 'completed':
                    result = receipt['result']['output']
                    result = json.loads(result) if isinstance(result, str) else result
                    problem = result.get('problem')
                    if problem and problem['payload'].get('kind') == 'authority_support_gap':
                        problems.append(problem)
            assert problems, '真实回执必须含有待验证的权威缺口'
            replay_db.add(AgentRun(**deepcopy(authority_run)))
            replay_db.flush()
            authority_context = ToolExecutionContext(db=replay_db, user_id=authority_run['user_id'],
                project_id=authority_run['project_id'], run_id=authority_run['id'], node_key=authority_node['node_key'],
                run_input=deepcopy(authority_run['input_payload']), action_input=deepcopy(authority_node['input_payload']),
                artifacts=deepcopy(authority_run['run_context']['artifacts']), action_state=deepcopy(saved['state']))
            plan = planning.observe_planning_state(generation._planning_context(authority_context, authority_context.action_state))
            # 将实际发生过的缺口回执交给同一来源的行动守卫，不生成业务候选或替换模型执行器。
            state = deepcopy(authority_context.action_state)
            state['problems'] = {value['problem_id']: deepcopy(value) for value in problems}
            actions = generation._available_actions(state, plan, None)
            assert 'rebuild_business_plan' not in actions
            assert 'review_planning_route_gaps' in actions
            targets = {(item['module_index'], index)
                       for item in actions['review_planning_route_gaps']['unresolved_design_selections']
                       for index in item['test_design_item_indexes']}
            # 已按相同来源和权威反馈复核的目标不再列为待办；历史未决结论仍保留供修订草案。
            expected_selections = planning.unreviewed_support_selections(state['planning'], [
                {'module_index': gap['module_index'], 'test_design_item_indexes': [gap['test_design_item_index']]}
                for problem in problems for gap in problem['payload']['design_support_gaps']
            ], problems)
            assert all((item['module_index'], index) in targets
                       for item in expected_selections for index in item['test_design_item_indexes'])
            state['planning']['downstream_feedback'] = deepcopy(problems)
            blocker = planning._business_plan_rebuild_blocker(state['planning'])
            assert blocker and blocker['design_support_gaps']
            report['checks']['real_authority_gaps_preserve_plan_and_require_targeted_review'] = {
                'source_run_id': authority_run['id'], 'problem_count': len(problems),
                'unreviewed_targets': expected_selections, 'blocker': blocker}
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
