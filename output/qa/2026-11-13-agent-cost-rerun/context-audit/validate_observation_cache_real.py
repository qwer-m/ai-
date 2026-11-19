"""真实Run59观察缓存草案：完整键、变更失效、来源校验、输出隔离和实时预算验证。"""
from pathlib import Path
import sys,json,importlib.util,time,hashlib
from copy import deepcopy
ROOT=Path.cwd();OUT=ROOT/'output/qa/2026-11-13-agent-cost-rerun/context-audit';sys.path.insert(0,str(ROOT/'backend'))
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from agents.run_config import CallModelData,ModelInputData
name='modules.agent_platform._qa_observation_cache';spec=importlib.util.spec_from_file_location(name,OUT/'sdk_observation_cache.draft.py');module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
snapshot_path=ROOT/'output/qa/2026-11-12-single-agent-live/round-11/final-snapshot.json';raw=snapshot_path.read_bytes();snapshot=json.loads(raw);node=snapshot['expanded_checkpoints']['404'];state=deepcopy(node['state']);run=snapshot['run'];config=next(x for x in run['run_context']['execution_contract']['agents'] if x['key']=='test_generation_coordinator')['runtime_config'];finish=next(x for x in node['receipts'].values() if x['tool_key']=='finish_generation');state['completed']=False;state['delivery']=None;state['domain_sha256']=generation._digest(state);assert action_state_hash(state)==finish['pre_state_hash']
checks=[];report={'run_id':59,'passed':False,'model_requests':0,'database_writes':0,'actual_run_mutated':False,'checks':checks}
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 context=ToolExecutionContext(db=db,user_id=1,project_id=8,run_id=59,node_key='agent_network',run_input=deepcopy(run['input_payload']),action_input=deepcopy(run['input_payload']),action_state=state,artifacts=deepcopy(state['source']['artifacts']),confirmed_conflicts={})
 original_hashes={k:action_state_hash(getattr(context,k)) for k in ('run_input','action_input','action_state','artifacts','confirmed_conflicts')}
 started=time.perf_counter();expected=generation.observe_generation_state(context);baseline=time.perf_counter()-started
 f=module._PersistentStateInputFilter(context=context,runtime_config=config);original=f._observer;count=[0]
 def counted(c,a):
  count[0]+=1
  return original(c,a)
 f._observer=counted
 started=time.perf_counter();first=f.observe();cold=time.perf_counter()-started;assert first==expected and count[0]==1
 hit_times=[]
 for i in range(4):
  started=time.perf_counter();fresh=f.observe();hit_times.append(time.perf_counter()-started);assert fresh==expected and count[0]==1;assert fresh is not first
 assert f.available_tool_names()==set(expected['available_actions']) and count[0]==1
 checks.append('首次完整来源/领域校验，4次完整内容相同observe及工具目录读取仅命中缓存，输出逐字相同')
 first['goal']['case_budget']=50;assert f.observe()==expected and context.action_state['domain_sha256']==state['domain_sha256'];checks.append('修改首次返回对象不改变后续缓存或领域状态')
 context.action_state=dict(reversed(list(context.action_state.items())));assert f.observe()==expected and count[0]==1;checks.append('对象替换及字典键顺序改变保持相同完整内容，仍正确命中缓存')
 # 从真实来源中选择不同断言注入既有字段；未更新摘要，应由原观察器拒绝而非复用旧view。
 original_state=context.action_state;bad_state=deepcopy(original_state);facts=bad_state['source']['source_semantics']['authoritative_facts'];assert facts[0]['assertion']!=facts[1]['assertion'];facts[0]['assertion']=facts[1]['assertion'];context.action_state=bad_state
 failure_counts=[]
 for i in range(2):
  before=count[0]
  try:f.observe()
  except ValueError as e:assert '摘要' in str(e) or '内容' in str(e)
  else:raise AssertionError('旧domain_sha256下的真实嵌套内容变化错误命中缓存')
  assert count[0]==before+1;failure_counts.append(count[0])
 context.action_state=original_state;assert f.observe()==expected
 checks.append('保留domain_sha256篡改嵌套真实来源assertion，两次均进入原始完整校验并拒绝；坏状态不缓存')
 original_input=context.action_input;context.action_input={k:v for k,v in original_input.items() if k!='source_context_run_id'}
 before=count[0]
 try:f.observe()
 except ValueError:pass
 else:raise AssertionError('完整action_input变更未重新校验')
 assert count[0]==before+1;context.action_input=original_input
 checks.append('action_input去除真实来源复用参数使完整输入变化，重新执行并触发原输入契约校验')
 # 各键单独变化均重新执行原观察；不制造外部检索或模型回执。
 invalidations=[]
 saved=context.artifacts;context.artifacts={k:v for k,v in saved.items() if k!='context_compression'};before=count[0];assert f.observe()==expected and count[0]==before+1;invalidations.append('artifacts');context.artifacts=saved;f.observe()
 saved=context.confirmed_conflicts;context.confirmed_conflicts=deepcopy(state['conflict_confirmations']);before=count[0];assert f.observe()==expected and count[0]==before+1;invalidations.append('confirmed_conflicts');context.confirmed_conflicts=saved;f.observe()
 saved=context.run_input;context.run_input={k:v for k,v in saved.items() if k!='source_context_run_id'};before=count[0];assert f.observe()==expected and count[0]==before+1;invalidations.append('run_input');context.run_input=saved;f.observe()
 saved=context.project_id;context.project_id=9;before=count[0]
 try:f.observe()
 except ValueError:pass
 else:raise AssertionError('项目变化未重新核验归属')
 assert count[0]==before+1;context.project_id=saved;f.observe();invalidations.append('project_scope')
 checks.append('artifacts/confirmed_conflicts/run_input单独内容变化均失效重验；切换真实CRM项目ID触发原归属校验拒绝')
 observations=json.loads((ROOT/'output/qa/2026-11-12-single-agent-live/round-11/observations.json').read_text(encoding='utf8'));historical=observations['snapshots'][0]['snapshot']['run']['usage'];current=node['budget']['reported_usage'];budget=[historical];f.bind_budget_observer(lambda:deepcopy(budget[0]));initial=json.dumps(expected,ensure_ascii=False,separators=(',',':'));f.bind_initial_input(initial,expected);data=CallModelData(model_data=ModelInputData(input=[{'role':'user','content':initial}],instructions=''),agent=None,context=context)
 before=count[0];one=f(data);budget[0]=current;two=f(data);assert count[0]==before;assert json.loads(one.input[-1]['content'].split('\n',1)[1])['shared_budget']==historical;assert json.loads(two.input[-1]['content'].split('\n',1)[1])['shared_budget']==current;checks.append('缓存状态不缓存预算：同state两次真实历史/最终usage通过SDK输入过滤尾部实时读取；错误投影调用保持原__call__路径')
 assert all(action_state_hash(getattr(context,k))==v for k,v in original_hashes.items());assert not db.new and not db.dirty and not db.deleted;db.rollback()
 report.update(observer_seconds_without_cache=baseline,cold_seconds=cold,hit_seconds=hit_times,full_validation_count=count[0],invalidated_inputs=invalidations,source_tamper_full_validation_counts=failure_counts)
assert snapshot_path.read_bytes()==raw;checks.append('原始真实快照字节、全部输入/产物/确认和领域状态恢复为原值；无DB新增修改删除')
report['passed']=True;(OUT/'observation-cache-real-data-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))
