"""只读核对本轮明确清理范围；不把整个dirty工作区算成本轮删除。"""
from datetime import datetime,timezone
from pathlib import Path
import ast,hashlib,json,sys
ROOT=Path(__file__).resolve().parents[3];BACKEND=ROOT/'backend';BASE=Path(__file__).parent
sys.path.insert(0,str(BACKEND))
from jsonschema import Draft202012Validator
from core.db.model_defs import AgentToolDefinition
from modules.agent_platform.registry import ToolExecutionContext,tool_registry
from modules.agent_platform.contracts import AgentProgramDefinition,WorkflowGraph,parse_execution_definition
from modules.agent_platform.sdk_adapter import _function_tool,_validate_runtime_config
from modules.agent_platform.test_generation_builtin_specs import (
 BUILTIN_AGENT_SPECS,BUILTIN_TOOL_SPECS,BUILTIN_WORKFLOW_SPECS,
)
from modules.agent_platform.registry import BUILTIN_WORKFLOW_SPECS as ALL_WORKFLOWS
old=json.loads((BASE/'cleanup-dependency-map.json').read_text(encoding='utf8'))
deleted=json.loads((BASE/'retired-generation-production-manifest.json').read_text(encoding='utf8'))
retired=json.loads((BASE/'retired-generation-tests-manifest.json').read_text(encoding='utf8'))
baseline={item['path']:item for item in old['modules']}
current_paths=['backend/modules/agent_platform/'+name for name in (
 'test_generation_agent.py','test_generation_builtin_specs.py','test_generation_schemas.py',
 'test_generation_workflow.py','generation_evidence.py')]
before_bytes={relative:(ROOT/relative).read_bytes() for relative in current_paths}
def info(relative):
 body=before_bytes[relative];normalized=body.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
 return {'path':relative,'lines':len(normalized.splitlines()),'sha256':hashlib.sha256(body).hexdigest(),
         'normalized_source_sha256':hashlib.sha256(normalized.encode('utf8')).hexdigest()}
current=[info(relative) for relative in current_paths]
rewrites=[{'path':item['path'],'before_lines':baseline[item['path']]['lines'],'after_lines':item['lines'],
 'net_lines':item['lines']-baseline[item['path']]['lines'],
 'before_normalized_source_sha256':baseline[item['path']]['sha256'],'after_sha256':item['sha256']}
 for item in current if item['path'] in baseline]
assert len(deleted['files'])==13 and sum(item['lines'] for item in deleted['files'])==14632
assert len(rewrites)==4 and old['generation_module_lines']==20036
for item in deleted['files']:assert not (ROOT/item['path']).exists()
retired_names={Path(item['path']).stem for item in deleted['files']}
imports=[]
for folder in (BACKEND/'modules',BACKEND/'routers',BACKEND/'tests',BACKEND/'scripts/qa'):
 for path in folder.rglob('*.py'):
  tree=ast.parse(path.read_text(encoding='utf-8-sig'))
  for node in ast.walk(tree):
   if isinstance(node,ast.ImportFrom):
    if (node.module or '').endswith('agent_platform'):
     targets=[alias.name for alias in node.names]
    else:targets=[(node.module or '').rsplit('.',1)[-1]]
    if any(target in retired_names for target in targets) and (
       not node.module or (node.module or '').startswith('modules.agent_platform') or path.parent==BACKEND/'modules/agent_platform'):
     imports.append({'path':path.relative_to(ROOT).as_posix(),'line':node.lineno,'module':node.module})
   elif isinstance(node,ast.Import):
    for alias in node.names:
     if alias.name.startswith('modules.agent_platform.') and alias.name.rsplit('.',1)[-1] in retired_names:
      imports.append({'path':path.relative_to(ROOT).as_posix(),'line':node.lineno,'module':alias.name})
assert not imports,imports
tool_results=[]
ctx=ToolExecutionContext(db=None,user_id=1,project_id=8,run_id=63,node_key='agent_network',run_input={})
for spec in BUILTIN_TOOL_SPECS:
 Draft202012Validator.check_schema(spec['input_schema']);Draft202012Validator.check_schema(spec['output_schema'])
 definition=AgentToolDefinition(project_id=8,user_id=1,**{key:spec[key] for key in (
   'tool_key','name','description','handler_key','input_schema','output_schema')})
 tool=_function_tool(definition,ctx)
 assert tool.name==spec['tool_key'] and tool_registry.is_state_only(spec['handler_key'])
 tool_results.append({'tool_key':tool.name,'handler_key':spec['handler_key'],'strict_sdk_constructed':True,'state_only':True})
for spec in BUILTIN_AGENT_SPECS:
 Draft202012Validator.check_schema(spec['output_schema'])
 config=spec['runtime_config'];_validate_runtime_config(config,has_tools=bool(config.get('tool_keys') or config.get('validate_before_submit')))
 assert all(tool_registry.resolve(config[key]) for key in (
  'state_initializer','state_observer','managed_artifacts_provider','output_postprocessor') if key in config)
workflows=[]
for spec in ALL_WORKFLOWS:
 definition=parse_execution_definition(spec['definition']);workflows.append({'key':spec['workflow_key'],'execution_mode':definition.execution_mode,
  'time_budget_seconds':definition.time_budget_seconds if isinstance(definition,AgentProgramDefinition) else None})
assert len(BUILTIN_AGENT_SPECS)==2 and len(BUILTIN_TOOL_SPECS)==10
assert sum(isinstance(parse_execution_definition(spec['definition']),WorkflowGraph) for spec in ALL_WORKFLOWS)==3
assert all((ROOT/path).read_bytes()==content for path,content in before_bytes.items())
assert all((ROOT/path).is_file() and hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha
           for path,sha in retired['preserved_fixture_hashes'].items())
before_capacity=BASE/'direct-review-input-capacity.before.json';latest_capacity=BASE/'direct-review-input-capacity.json'
after_capacity=BASE/'direct-review-input-capacity.after.json';after_capacity.write_bytes(latest_capacity.read_bytes())
before=json.loads(before_capacity.read_text(encoding='utf8'));after=json.loads(after_capacity.read_text(encoding='utf8'))
assert before['fixture_data_sha256']==after['fixture_data_sha256'] and before['case_count']==after['case_count']==70
comparison={key:{'before':before[key],'after':after[key]} for key in (
 'payload_utf8_bytes','without_image_descriptors_payload_utf8_bytes','actual_full_page_and_crop_image_count',
 'json_token_estimate','runtime_input_token_upper_bound_with_actual_images','runtime_input_token_upper_bound_without_images')}
report={'observed_at':datetime.now(timezone.utc).isoformat(),'scope':'本轮清理前固定快照→当前领域；不是Git HEAD→dirty全仓归因',
 'production':{'old_generation_modules':16,'old_generation_lines':20036,'additional_retired_compression_lines':322,
 'total_old_domain_lines':20358,'deleted_module_count':13,'deleted_module_lines':14632,'deleted_modules':deleted['files'],
 'rewritten_same_name_files':rewrites,'new_evidence_file':next(item for item in current if item['path'].endswith('/generation_evidence.py')),
 'current_domain_files':current,'current_domain_file_count':len(current),'current_domain_lines':sum(item['lines'] for item in current),
 'domain_net_line_change':sum(item['lines'] for item in current)-20358,
 'source_bytes_unchanged_during_audit':True,'retired_imports_remaining':imports},
 'test_qa':{'deleted_file_count':retired['deleted_count'],'deleted_lines_at_retirement':retired['deleted_lines'],
 'by_scope':retired['deleted_by_scope'],'original_real_fixture_count':retired['preserved_fixture_count'],
 'original_fixture_bytes_unchanged':True,'new_cpu_fixture_separate':'run63_direct_protocol_cpu_real.json.gz',
 'limits':'测试/QA与生产分开；删除compression测试前已移除71行旧断言，不把这段与删除时83行重复计数。'},
 'current_contract_construction':{'generation_agent_keys':[spec['agent_key'] for spec in BUILTIN_AGENT_SPECS],
 'tools':tool_results,'workflows':workflows,'json_schemas_checked':True,'runtime_configs_valid':True},
 'same_real_candidate_capacity':comparison,'model_requests_sent':0,'business_database_writes':0,
 'acceptance_status':'当前源码/协议CPU验证，未完成新架构在线20min/30w/70条业务验收',
 'capacity_limits':'UTF8 byte upper bound仍不是供应商实计tokens；实际含图评审上界591395仍超过本轮300000准入，未提高预算或截断证据。'}
(BASE/'cleanup-final-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'old_domain_lines':20358,'deleted_modules':13,'deleted_module_lines':14632,
 'rewritten_files':rewrites,'new_current_domain_lines':report['production']['current_domain_lines'],
 'net_line_change':report['production']['domain_net_line_change'],'retired_test_qa_files':retired['deleted_count'],
 'retired_test_qa_lines':retired['deleted_lines'],'contracts_constructed':True,'capacity':comparison},ensure_ascii=False))
