"""对真实Run61指定行动叶子重放同一确定性规划/生成闸门，不调用模型或写业务。"""
import runpy,json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
ns=runpy.run_path(str(Path(__file__).with_name('capture_cost_progress.py')))
SessionLocal,text,decoded,Reader,receipt=map(ns.get,['SessionLocal','text','decoded','TargetedReader','receipt_record'])
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.test_generation_planning import merge_planning_scope_routes
from modules.agent_platform.test_generation_batching import prepare_test_case_batches
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.action_journal import action_state_hash
KEYS=['plan','planning_inputs','planning_records','route_inputs','route_records','gap_inputs','gap_records','route_review_revisions','completed']
reports=[]
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY')); rd=Reader(db); ev=ns['event_headers'](db,61)
 selected=[e for e in ev if e['event_type']=='agent_action_completed' and e['payload'].get('tool_key') in {'review_planning_route_gaps','rebuild_business_plan'} and e['payload'].get('tool_call_id') in {'call_2dfe5c1c62c742e6ae4e3842b078c3e3','call_ac06fe15479f4ad698c8afebd2fa0c89','call_dac4fc0aaa8c499b83a000a1cf58fd74','call_0cfdcd74a5ff4291bac9e9f0d97510fb','call_6af1d27614b841cba9bb98da9b72311c'}]
 for e in selected:
  r=receipt(db,e['payload']['tool_call_id']); sha=r['post_state_hash'];state={key:rd.subtree(sha,['planning',key]) for key in KEYS}
  feedback=planning._route_feedback(state); unsupported=[{'module_index':entry['module_index'],'module_name':entry['module_name'],'designs':entry['missing_test_design_items'],'planning_batch_indexes':entry['planning_batch_indexes'],'decisions':entry['review']['decisions']} for entry in feedback if entry['review'] and entry['review']['decisions']]
  can_finish=bool(state['plan'] and planning._complete_records(state['route_records']) and not planning._pending(state['gap_records']) and not unsupported and not planning._unrouted_modules(state) and not state['completed'])
  summary={'action_sequence':r['started_sequence'],'tool_key':r['tool_key'],'post_state_hash':sha,'plan_available':state['plan'] is not None,'pending_draft_indexes':planning._pending(state['planning_records']),'pending_route_indexes':planning._pending(state['route_records']),'pending_gap_indexes':planning._pending(state['gap_records']),'unrouted_modules':planning._unrouted_modules(state),'unsupported':unsupported,'planning_can_finish':can_finish,'generation_preflight':None,'read_mode':'指定planning叶子，用既有_route_feedback/_pending/_unrouted_modules重算与observe_planning_state相同的can_finish条件，非完整root hydrate'}
  if can_finish:
   state['source_authority']=rd.subtree(sha,['planning','source_authority']);state['source_semantics']=rd.subtree(sha,['planning','source_semantics']);confirmations=rd.subtree(sha,['conflict_confirmations']);ctx=SimpleNamespace(artifacts={},action_state=state,confirmed_conflicts=confirmations,db=db,run_id=61,user_id=1,project_id=8)
   resolved=merge_planning_scope_routes(planning._isolated_context(ctx),{'plan':state['plan'],'prepared_items':state['route_inputs'],'route_records':state['route_records'],'repair_records':state['gap_records'],'review_revisions':state['route_review_revisions']})
   effective=rd.subtree(sha,['effective_facts']);layout=rd.subtree(sha,['generation_layout']);case_budget=rd.subtree(sha,['prepared_input','case_budget']);batch_limit=rd.subtree(sha,['prepared_input','batch_case_limit'])
   try:
    batches=prepare_test_case_batches(ctx,{'plan':resolved,'effective_facts':effective['effective_facts'],'case_budget':case_budget,'batch_case_limit':batch_limit,**({'generation_layout':layout} if layout is not None else {})})['items'];summary['generation_preflight']={'ready':True,'batch_count':len(batches),'case_budget':sum(x['case_budget'] for x in batches),'source':'与root共用prepare_test_case_batches及Run61冻结生成专家input_projection'}
   except OutputValidationError as err:summary['generation_preflight']={'ready':False,'type':type(err).__name__,'message':str(err),'details':err.details,'source':'与root共用prepare_test_case_batches及Run61冻结生成专家input_projection'}
  reports.append(summary)
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
p=Path(__file__).with_name('planning-gate-progress.json');p.write_text(json.dumps({'run_id':61,'model_requests_sent':0,'business_database_writes':0,'snapshots':reports},ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(reports,ensure_ascii=False))
