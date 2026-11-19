"""只清理混合测试中的旧生成协议；通用SDK/预算/评估/恢复断言原样保留。"""
import ast,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];BASE=Path(__file__).parent;TESTS=ROOT/'backend/tests/agent_platform'
manifest=[]
def clean(name,remove_names=(),remove_import_prefixes=(),replacements=None,text_changes=()):
 path=TESTS/name;assert path.resolve().is_relative_to(TESTS.resolve());before=path.read_bytes();source=before.decode('utf-8-sig');tree=ast.parse(source);lines=source.splitlines(keepends=True);edits=[];removed=[]
 for node in tree.body:
  if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and node.name in remove_names:
   start=min([node.lineno,*[d.lineno for d in node.decorator_list]])-1
   replacement=(replacements or {}).get(node.name,'')
   edits.append((start,node.end_lineno,replacement));removed.append({'name':node.name,'old_start':start+1,'old_end':node.end_lineno,'action':'替换当前合同' if replacement else '删除旧协议断言'})
  elif isinstance(node,ast.ImportFrom) and any((node.module or '').startswith(prefix) for prefix in remove_import_prefixes):edits.append((node.lineno-1,node.end_lineno,''))
 for start,end,replacement in sorted(edits,reverse=True):lines[start:end]=[replacement] if replacement else []
 after=''.join(lines)
 for old,new in text_changes:assert old in after;after=after.replace(old,new)
 ast.parse(after);path.write_text(after,encoding='utf8',newline='')
 manifest.append({'path':path.relative_to(ROOT).as_posix(),'before_sha256':hashlib.sha256(before).hexdigest(),
  'after_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'before_lines':len(source.splitlines()),'after_lines':len(after.splitlines()),
  'changed_functions':removed,'retained_tests':[n.name for n in ast.parse(after).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_')]})

path=TESTS/'test_agent_platform_contracts.py';tree=ast.parse(path.read_text(encoding='utf8'))
old_names=set()
for node in ast.walk(tree):
 if isinstance(node,ast.ImportFrom) and (node.module or '').startswith('modules.agent_platform.test_generation_'):old_names.update(alias.asname or alias.name for alias in node.names)
remove={node.name for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and
        {n.id for n in ast.walk(node) if isinstance(n,ast.Name)}&old_names}
remove|={'_route_test_points','_module_route','test_global_final_review_contract_accepts_its_own_review_dimensions',
 'test_planner_separates_business_modules_from_test_points','test_batch_final_reviewer_does_not_copy_fact_ids_from_model_output',
 'test_reviewers_use_structured_single_turn_outputs'}
current_builtin='''def test_builtin_workflow_is_data_driven_and_references_registered_definitions() -> None:
    from modules.agent_platform.test_generation_builtin_specs import (
        BUILTIN_AGENT_SPECS as generation_agents, BUILTIN_TOOL_SPECS as generation_tools,
        BUILTIN_WORKFLOW_SPECS as generation_workflows,
    )
    from modules.agent_platform.test_generation_schemas import GENERATION_EXECUTION_LIMITS

    # 直接核对当前真实注册定义，不保留已删除专家或阶段工具满足旧测试。
    agents = {item["agent_key"]: item for item in BUILTIN_AGENT_SPECS}
    tools = {item["tool_key"]: item for item in BUILTIN_TOOL_SPECS}
    assert len(agents) == len(BUILTIN_AGENT_SPECS)
    assert len(tools) == len(BUILTIN_TOOL_SPECS)
    assert {item["agent_key"] for item in generation_agents} == {
        "test_generation_coordinator", "test_generation_reviewer",
    }
    action_keys = {"read_source", "view_source_image", "commit_coverage_plan", "append_cases", "patch_cases",
                   "inspect_cases", "report_issue", "request_review", "request_confirmation", "finish_generation"}
    assert {item["tool_key"] for item in generation_tools} == action_keys
    coordinator = agents["test_generation_coordinator"]["runtime_config"]
    assert set(coordinator["tool_keys"]) == action_keys | {"retrieve_context"}
    assert coordinator["managed_agent_keys"] == ["test_generation_reviewer"]
    assert coordinator["managed_agent_max_concurrency"] == 1
    assert coordinator["stop_at_tool_keys"] == ["finish_generation"]
    assert coordinator["max_turns"] == 24
    for key in ("state_initializer", "state_observer", "managed_artifacts_provider", "output_postprocessor"):
        assert tool_registry.resolve(coordinator[key])
    for item in generation_tools:
        assert tool_registry.is_state_only(item["handler_key"])
    assert len(generation_workflows) == 1
    generation = parse_execution_definition(generation_workflows[0]["definition"])
    assert isinstance(generation, AgentProgramDefinition)
    assert generation.entry_agent_key == "test_generation_coordinator"
    assert generation.time_budget_seconds == 1200
    assert generation.required_artifact_key == "test_generation"
    assert generation.input_schema["additionalProperties"] is False
    assert not {"source_context_run_id", "enable_context_compression", "context_compression_max_tokens"} & set(generation.input_schema["properties"])
    assert GENERATION_EXECUTION_LIMITS["max_total_tokens"] == 300000
    assert GENERATION_EXECUTION_LIMITS["max_requests"] == 32
    evaluation_workflows = [item for item in BUILTIN_WORKFLOW_SPECS if item["workflow_key"] != "test_generation"]
    assert len(evaluation_workflows) == 3
    for spec in BUILTIN_WORKFLOW_SPECS:
        execution = parse_execution_definition(spec["definition"])
        if isinstance(execution, AgentProgramDefinition):
            assert execution.entry_agent_key in agents
            continue
        assert isinstance(execution, WorkflowGraph)
        assert execution.execution_order()
        for node in execution.nodes:
            allowed = agents if node.node_type in {"agent", "agent_network", "agent_map"} else tools
            assert node.reference_key in allowed
    for spec in BUILTIN_AGENT_SPECS:
        config = spec["runtime_config"]
        for name in ("subagent_keys", "managed_agent_keys"):
            assert set(config.get(name, [])) <= set(agents)
        for key in config.get("tool_keys", []):
            assert key in tools
            assert tool_registry.resolve(tools[key]["handler_key"])

'''
current_reviewer='''def test_reviewers_use_structured_single_turn_outputs() -> None:
    from modules.agent_platform.test_generation_schemas import REVIEW_SCHEMA

    reviewer = next(item for item in BUILTIN_AGENT_SPECS if item["agent_key"] == "test_generation_reviewer")
    definition = AgentDefinition(agent_key=reviewer["agent_key"], output_schema=reviewer["output_schema"])
    assert _sdk_output_type(definition, has_tools=False) is not None
    assert reviewer["output_schema"] == REVIEW_SCHEMA
    assert reviewer["runtime_config"]["stop_at_tool_keys"] == ["submit_result"]
    assert reviewer["runtime_config"]["tool_keys"] == []
    assert reviewer["runtime_config"]["validate_before_submit"] is True
    assert reviewer["runtime_config"]["disable_server_output_schema"] is True

'''
clean('test_agent_platform_contracts.py',remove,('modules.agent_platform.test_generation_',),
 replacements={'test_builtin_workflow_is_data_driven_and_references_registered_definitions':current_builtin,
               'test_reviewers_use_structured_single_turn_outputs':current_reviewer},
 text_changes=[('"submit_generation_batch"','"finish_generation"'),('"test_generation_scenario_designer"','"test_generation_reviewer"')])

clean('test_runtime_retry.py',{
 'test_planning_route_repair_targets_only_invalid_scope','test_agent_map_item_retry_feedback_explains_missing_fact_relocation',
 'test_generation_retry_feedback_includes_missing_fact_assertion','test_generation_retry_feedback_includes_missing_design_intent',
 'test_final_review_retry_feedback_accumulates_requirements_and_missing_fact','test_parallel_agent_map_stops_repeating_identical_invalid_output',
 'test_generation_repair_context_keeps_missing_facts_at_batch_scope'},('modules.agent_platform.test_generation_repair',))
clean('test_context_compression.py',{'test_stale_selection_is_recomputed_when_catalog_gains_evidence',
 'test_selection_is_recomputed_when_compression_budget_changes'},('modules.agent_platform.test_generation_semantics',))
clean('test_artifact_read_cost_real_data.py',{'test_readonly_source_view_and_mutating_context_keep_distinct_ownership',
 'test_readonly_planning_observation_matches_original_isolated_path_without_mutation','test_readonly_view_keeps_full_source_hash_validation',
 'context_for'},('modules.agent_platform.test_generation_source_agent',),
 text_changes=[('from modules.agent_platform import test_generation_agent as generation\n','')])
clean('test_agent_program_input_real_data.py',{'test_real_source_checkpoint_survives_isolated_new_run_and_keeps_current_context_identity'},
 text_changes=[('from modules.agent_platform import runtime, test_generation_agent as generation','from modules.agent_platform import runtime')])
(BASE/'mixed-generation-tests-cleanup-manifest.json').write_text(json.dumps({'files':manifest,'model_requests_sent':0,'business_database_writes':0},ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps([{k:item[k] for k in ('path','before_lines','after_lines')} for item in manifest],ensure_ascii=False))
