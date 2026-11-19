"""只读运行真实Run59上下文草案；不派发模型、不修改领域及审计数据。"""
from pathlib import Path
import sys,json,importlib.util,runpy,time
from copy import deepcopy
ROOT=Path.cwd(); OUT=ROOT/'output/qa/2026-11-13-agent-cost-rerun/context-audit';sys.path.insert(0,str(ROOT/'backend'))
from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentToolDefinition
from modules.agent_platform import sdk_adapter as original_sdk
from modules.agent_platform import test_generation_agent as original_gen
from modules.agent_platform import test_generation_planning_agent as original_plan
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.action_journal import action_state_hash
from agents import ModelSettings

def load(name):
 fullname='modules.agent_platform._qa_context_'+name
 spec=importlib.util.spec_from_file_location(fullname,OUT/(name+'.draft.py'));module=importlib.util.module_from_spec(spec);sys.modules[fullname]=module;spec.loader.exec_module(module);return module
new_sdk=load('sdk_adapter');new_plan=load('test_generation_planning_agent');new_gen=load('test_generation_agent');new_gen.planning=new_plan
raw=(ROOT/'output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json').read_bytes();snapshot=json.loads(raw);checkpoint=snapshot['expanded_checkpoints']['404'];receipts=sorted(checkpoint['receipts'].values(),key=lambda x:x['started_sequence']);run=snapshot['run'];contract=run['run_context']['execution_contract'];config=next(x for x in contract['agents'] if x['key']=='test_generation_coordinator')['runtime_config']
size=lambda x:len(json.dumps(x,ensure_ascii=False,separators=(',',':')))
checks=[];report={'run_id':59,'database_writes':0,'model_requests':0,'actual_run_mutated':False,'checks':checks}
state=deepcopy(checkpoint['state']);finish=receipts[-1];assert finish['tool_key']=='finish_generation';state['completed']=False;state['delivery']=None;state['domain_sha256']=original_gen._digest(state)
assert action_state_hash(state)==finish['pre_state_hash'];checks.append('真实末态回退唯一finish实际改动后，与持久完成动作pre_state_hash完全相同')
common=runpy.run_path(str(ROOT/'output/qa/2026-11-12-single-agent-live/round-10/_audit_planning_readonly.py'))
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 context=ToolExecutionContext(db=db,user_id=run['user_id'],project_id=run['project_id'],run_id=59,node_key='agent_network',run_input=run['input_payload'],action_input=run['input_payload'],action_state=state,artifacts=deepcopy(state['source']['artifacts']))
 before_state=action_state_hash(state)
 started=time.perf_counter();before=original_gen.observe_generation_state(context);old_elapsed=time.perf_counter()-started
 started=time.perf_counter();after=new_gen.observe_generation_state(context);new_elapsed=time.perf_counter()-started
 assert before['available_actions']==after['available_actions'];assert before['goal']==after['goal'];assert before['review']==after['review'];assert before['can_finish']==after['can_finish']
 assert after['generation']['layout'] is None;assert sum(x['case_budget'] for x in after['generation']['layout_summary'])==70
 assert after['generation']['batches']==before['generation']['batches'];assert after['problems']==before['problems'];assert action_state_hash(state)==before_state
 checks.append('实际完成前观察：目标、合法行动、审查、可完成状态、批次索引和问题保持一致；布局仍保留state，模型只见统计')
 report['pre_finish_observation']={'before_chars':size(before),'after_chars':size(after),'char_reduction':size(before)-size(after),'before_seconds':old_elapsed,'after_seconds':new_elapsed}
 filter_=new_sdk._PersistentStateInputFilter(context=context,runtime_config=config)
 tool_rows=[]
 for spec in contract['tools']:
  if spec['key'] in config['tool_keys']:
   row=AgentToolDefinition(id=spec['id'],project_id=spec['project_id'],tool_key=spec['key'],name=spec['name'],description=spec['description'],handler_key=spec['handler_key'],input_schema=spec['input_schema'],output_schema=spec['output_schema'],requires_approval=spec['requires_approval'])
   tool_rows.append(original_sdk._function_tool(row,context))
 kwargs={'tools':tool_rows,'model_settings':ModelSettings(tool_choice='auto')}
 projected=new_sdk._ActionCatalogModel(None,filter_)._request(kwargs)
 assert {x.name for x in projected['tools']}==set(before['available_actions']);assert len(kwargs['tools'])==21;assert len(tool_rows)==21
 checks.append('21个真实冻结定义构造原SDK FunctionTool；仅传输层选择当前合法4个工具，原注册列表不变，未调用model')
 report['actual_sdk_tool_filter']={'full_count':len(tool_rows),'legal_count':len(projected['tools']),'legal_tools':[x.name for x in projected['tools']]}
 reader=common['TargetedReader'](db);planning_hash=next(x for x in receipts if x['started_sequence']==6)['pre_state_hash']
 planning_state=reader.subtree(planning_hash,['planning']);prepared=reader.subtree(planning_hash,['prepared_input']);source_artifacts=reader.subtree(planning_hash,['source','artifacts'])
 pcontext=ToolExecutionContext(db=db,user_id=1,project_id=8,run_id=59,node_key='agent_network',run_input=run['input_payload'],action_input=prepared,action_state=planning_state,artifacts=source_artifacts)
 plan=original_plan.observe_planning_state(pcontext);p_before=original_plan.compact_planning_observation(plan);p_after=new_plan.compact_planning_observation(plan)
 assert len(plan['unsupported_designs'])>0
 before_decisions=[(x['module_index'],r) for x in plan['route_gaps'] if x['review'] for r in x['review']['decisions']]
 after_decisions=[(x['module_index'],r) for x in p_after['route_gaps'] if x['review'] for r in x['review']['decisions']]
 assert before_decisions==after_decisions
 assert all(x['fact_count']==y['fact_count'] and x['scope_ids']==y['scope_ids'] and x['item_index']==y['item_index'] for x,y in zip(p_before['planning_batches'],p_after['planning_batches'],strict=True))
 assert p_before['can_finish']==p_after['can_finish'];assert p_before['pending_gap_indexes']==p_after['pending_gap_indexes']
 checks.append('真实首次草案修订前leafref：支持/未支持每项完整裁决、reason、gaps逐项相同；规划批次index/scope/count与完成门禁不变，重复目录不再发送')
 report['first_revision_planning_observation']={'actual_state_hash':planning_hash,'unsupported_design_count':len(plan['unsupported_designs']),'before_chars':size(p_before),'after_chars':size(p_after),'char_reduction':size(p_before)-size(p_after),'canonical_decision_count':len(after_decisions)}
 for seq in [1,2]:
  receipt=next(x for x in receipts if x['started_sequence']==seq);post_source=reader.subtree(receipt['post_state_hash'],['source']);post_planning=reader.entry_at(receipt['post_state_hash'],['planning'])
  effect={'action':receipt['tool_key'],'source_progress':{kind:{'count':len(post_source[f'{kind}_records']),'completed':sum(x is not None for x in post_source[f'{kind}_records'])} for kind in ('text','vision','authority','planning')},'planning_ready':post_planning.get('value','ref') is not None}
  old=json.loads(receipt['result']['output']);assert effect['source_progress']['text']['count']==old['source_items']['text']['item_count'];assert effect['source_progress']['vision']['count']==old['source_items']['vision']['item_count'];assert effect['source_progress']['planning']['count']==old['planning_item_count'];assert effect['source_progress']['planning']['count']-effect['source_progress']['planning']['completed']==len(old['pending_planning_indexes'])
  report.setdefault('source_effects',[]).append({'sequence':seq,'before_chars':size(old),'after_chars':size(effect),'effect':effect})
 checks.append('两个真实source行动post_state叶数据：压缩回执完成数与旧子观察完全对应，包含原始分析、治理、草案和planning激活状态')
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
assert (ROOT/'output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json').read_bytes()==raw
checks.append('原始完整快照字节保持不变；SDK原注册及持久领域数据未修改')
report['passed']=True
(OUT/'draft-real-data-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))
