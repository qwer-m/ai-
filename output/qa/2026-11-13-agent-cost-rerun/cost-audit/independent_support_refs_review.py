from pathlib import Path
import sys,json,gzip,runpy,asyncio
from copy import deepcopy
ROOT=Path.cwd();DIR=ROOT/'output/qa/2026-11-13-agent-cost-rerun/cost-audit';sys.path[:0]=[str(ROOT/'backend'),str(DIR)]
from modules.agent_platform import test_generation_schemas as new_s
from modules.agent_platform import test_generation_semantics as new_m
from modules.agent_platform import test_generation_planning as new_p
from modules.agent_platform import test_generation_planning_agent as new_a
from modules.agent_platform.output_repair import OutputValidationError
assert new_s.PLANNING_SUPPORT_MODEL_INPUT_PROJECTION_VERSION=='planning-support-v6-fact-refs'
from modules.agent_platform import test_generation_schemas as old_s
from modules.agent_platform import sdk_adapter
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext,tool_registry
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig
from core.db.model_defs import AgentDefinition
from core.db.database import SessionLocal
from sqlalchemy import text
from jsonschema import validate
from agents.tool_context import ToolContext
actual=json.loads(gzip.decompress((ROOT/'backend/tests/agent_platform/fixtures/run59_agent_context_cost_real.json.gz').read_bytes()));base=json.loads((ROOT/'output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json').read_text(encoding='utf8'));rawsha=action_state_hash(base);root_hash=actual['provenance']['planning_observation_source_state_sha256'];common=runpy.run_path(str(ROOT/'output/qa/2026-11-12-single-agent-live/round-10/_audit_planning_readonly.py'));rows=[];checks=[]
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));reader=common['TargetedReader'](db);items=reader.subtree(root_hash,['planning','gap_inputs']);records=reader.subtree(root_hash,['planning','gap_records']);catalog=reader.subtree(root_hash,['prepared_input','evidence_catalog'])
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
context=ToolExecutionContext(db=None,user_id=1,project_id=8,run_id=59,node_key='cpu_support_refs_review',run_input=base['run']['input_payload'],action_input={'evidence_catalog':catalog})
current=new_a._support_review_inputs(context,items)

def encode(output,item):
 ref_by_id={identity:ref for ref,identity in new_m.planning_support_fact_ref_catalog(item['candidate_facts']).items()};raw=deepcopy(output)
 for decision in raw['decisions']:
  decision.pop('disposition');decision['fact_refs']=[ref_by_id[x] for x in decision.pop('fact_ids')]
  for gap in decision['support_gaps']:
   gap['evidence_fact_refs']=[ref_by_id[x] for x in gap.pop('evidence_fact_ids')]
   if gap['kind']=='source_conflict':
    for side in gap['conflict']['sides']:side['fact_ref']=ref_by_id[side.pop('fact_id')]
 return raw

for i,item in enumerate(current):
 expected=records[i]['output'];raw=encode(expected,item);before=action_state_hash([item,raw]);validate(raw,new_s.PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA)
 actual_output=new_p.postprocess_planning_route_repair_item(context,{'item_input':item,'item_output':raw});assert actual_output==expected;assert action_state_hash([item,raw])==before
 rows.append({'module_index':item['module_index'],'design_count':len(expected['decisions']),'facts':len(item['candidate_facts']),'source_conflict_candidate_count':sum(g['kind']=='source_conflict' for d in expected['decisions'] for g in d['support_gaps']),'all_domain_fields_exact':True})
 # 事实列表顺序及所有字典键顺序变化仍给同一ID同一ref；模型目录必须同步于实际输入。
 reordered=deepcopy(item);reordered['candidate_facts'].reverse();reordered['source_review']=new_m.planning_support_source_review(reordered['candidate_facts']);assert new_m.planning_support_fact_ref_catalog(item['candidate_facts'])==new_m.planning_support_fact_ref_catalog(reordered['candidate_facts']);assert new_p.postprocess_planning_route_repair_item(context,{'item_input':reordered,'item_output':raw})==expected
checks.append('7个实际首轮模块合并回执逐字段恢复完全相同；含原模型提交的source_conflict候选及evidence refs/双方fact_ref，反转候选顺序不改变映射')
item=next(x for x in current if any(d['fact_ids'] for d in records[x['module_index']]['output']['decisions']));expected=records[item['module_index']]['output'];wire=encode(expected,item);target=next(i for i,d in enumerate(wire['decisions']) if d['fact_refs']);rejections=[]
for value in (True,1.0,0,-1,len(item['candidate_facts'])+1,'1'):
 bad=deepcopy(wire);bad['decisions'][target]['fact_refs']=[value]
 try:new_p.postprocess_planning_route_repair_item(context,{'item_input':item,'item_output':bad})
 except (ValueError,OutputValidationError) as e:rejections.append({'value':repr(value),'error':type(e).__name__})
 else:raise AssertionError(f'bad ref accepted {value!r}')
for kind in ('duplicate','legacy_field','hidden_source','visible_float','visible_bool'):
 altered=deepcopy(item);bad=deepcopy(wire)
 if kind=='duplicate':bad['decisions'][target]['fact_refs']*=2
 elif kind=='legacy_field':bad['decisions'][target]['fact_ids']=expected['decisions'][target]['fact_ids']
 elif kind=='hidden_source':altered['source_review']['authoritative_facts'].pop()
 else:
  row=next(x for x in altered['source_review']['authoritative_facts'] if x['fact_ref']==1);row['fact_ref']=1.0 if kind=='visible_float' else True
 try:new_p.postprocess_planning_route_repair_item(context,{'item_input':altered,'item_output':bad})
 except (ValueError,OutputValidationError) as e:rejections.append({'kind':kind,'error':type(e).__name__})
 else:raise AssertionError(kind+' accepted')
checks.append('bool/float/零/负/越界/字符串/重复/夹带旧key/不可见目录/可见行类型篡改全部拒绝；无双协议探测')
assert new_m.planning_support_fact_ref_catalog([])=={}
assert new_m.planning_support_source_review([])=={'authoritative_facts':[],'source_records':{}}
empty=deepcopy(item);empty['candidate_facts']=[]
try:new_p.postprocess_planning_support_decisions(context,{'item_input':empty,'item_output':expected})
except ValueError:pass
else:raise AssertionError('domain空事实准入被放宽')
checks.append('空候选presentation保持空目录可表示，domain空事实原准入仍拒绝，未增加表征层业务限制')
# 用真实存在的两段原文检验schema的同fact两side表示能力；只验证结构，不构造新的业务冲突结论。
first_conflict=next((it,out,d,g) for it,out in [(it,records[it['module_index']]['output']) for it in current] for d in out['decisions'] for g in d['support_gaps'] if g['kind']=='source_conflict');ci,co,cd,cg=first_conflict;cf=encode(co,ci);rawconf=next(g['conflict'] for d in cf['decisions'] for g in d['support_gaps'] if g['kind']=='source_conflict');two=deepcopy(rawconf);two['sides'][1]['fact_ref']=two['sides'][0]['fact_ref'];assert two['sides'][0]['quote']!=two['sides'][1]['quote'];side_schema=next(g for g in new_s.PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA['properties']['decisions']['items']['properties']['support_gaps']['items']['oneOf'] if g['properties']['kind']['const']=='source_conflict')['properties']['conflict'];validate(two,side_schema);checks.append('同fact_ref不同quote的两方schema允许；未将此结构探测当成有效业务冲突')
# 原domain schema和其它专家仍保留stable IDs，未被模型schema重命名污染。
assert 'fact_ids' in old_s.PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA['properties']['decisions']['items']['properties'];assert 'fact_id' in old_s.PLANNING_SOURCE_CONFLICT_SCHEMA['properties']['sides']['items']['properties'];assert 'fact_ids' in new_s.PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA['properties']['decisions']['items']['properties'];checks.append('生产domain和source_conflict领域schema均使用stable IDs，只有模型输出schema使用短refs')
# 完整SDK提交工具先检新schema，再调用当前postprocessor；raw和normalized两份输出独立保存。
frozen=next(a for a in base['run']['run_context']['execution_contract']['agents'] if a['key']=='test_planning_route_gap_reviewer');definition=AgentDefinition(id=frozen['id'],agent_key=frozen['key'],name=frozen['name'],output_schema=new_s.PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA,runtime_config=frozen['runtime_config']);ctx=ToolExecutionContext(db=None,user_id=1,project_id=8,run_id=59,node_key='cpu_sdk_ref_submit',run_input=base['run']['input_payload']);contract=sdk_adapter.AgentOutputContract(deepcopy(item),AgentMapConfig(item_postprocessor='testing.postprocess_planning_route_repair_item'));old_handler=tool_registry._handlers['testing.postprocess_planning_route_repair_item'];tool_registry._handlers['testing.postprocess_planning_route_repair_item']=new_p.postprocess_planning_route_repair_item
try:
 tool=sdk_adapter._output_submission_tool(definition,ctx);loop=AutonomousToolLoop(terminal_tool_names={'submit_result'},validate_submission=lambda value:contract.validate(definition,ctx,value),recoverable_errors=(),max_tool_calls=1);wrapped=loop.wrap_tool(tool,allow_feedback=True);args=json.dumps(wire,ensure_ascii=False);asyncio.run(wrapped.on_invoke_tool(ToolContext(context=ctx,tool_name='submit_result',tool_call_id='cpu_review_only',tool_arguments=args),args));accepted=loop.require_submission();assert accepted.raw_output==wire and accepted.normalized_output==expected;assert ctx.tool_calls[-1]['arguments']==wire
finally:tool_registry._handlers['testing.postprocess_planning_route_repair_item']=old_handler
checks.append('真实SDK submit FunctionTool→完整schema→domain后处理顺序通过；raw refs和normalized stableID逐字段隔离保留，CPU未保存伪模型回执')
assert action_state_hash(base)==rawsha
report={'passed':True,'source_run_id':59,'source_state_sha256':root_hash,'model_requests_sent':0,'business_database_writes':0,'production_files_changed':0,'historical_model_receipts_rewritten':0,'business_truth_evaluation':'not_performed','modules':rows,'rejections':rejections,'checks':checks,'limits':'CPU转换既有真实回执，非新模型调用；source_conflict是原模型候选及证明结构，未独立确认为真实业务矛盾，不新增人工确认'};(DIR/'independent-support-refs-review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))
