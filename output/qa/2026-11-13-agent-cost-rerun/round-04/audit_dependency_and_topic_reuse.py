"""Run63单次指定叶子审计：完整依赖复用与第二次局部topic保护。"""
import runpy,json
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from collections import Counter
ns=runpy.run_path(str(Path(__file__).with_name('capture_cost_progress.py')));SessionLocal,text,decoded,Reader=ns['SessionLocal'],ns['text'],ns['decoded'],ns['TargetedReader'];receipt=ns['receipt_record']
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.planning_dependencies import ownership_batches,validate_ownership_output,support_input
from modules.agent_platform.test_generation_planning_agent import _support_review_inputs,_protected_draft_topics,_validate_protected_draft_topics
from modules.agent_platform.test_generation_planning import postprocess_planning_support_decisions,coverage_topic_intent
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));rd=Reader(db)
 r6=receipt(db,'call_d78ff61e90a64bcb88368db045af75ab');r7=receipt(db,'call_4a4a2676bf12485598c76801c6b56399');sha=r7['post_state_hash'];before_own=rd.subtree(r6['pre_state_hash'],['planning','ownership_inputs']);after_own=rd.subtree(sha,['planning','ownership_inputs']);assert before_own==after_own
 values={}
 def validate_dependency(kind,request):
  h=action_state_hash(request);record=rd.subtree(sha,['planning','dependency_records',kind,h]);assert record['input_sha256']==h
  restored={}
  for key,ref in record['input_refs'].items():
   if ref not in values:values[ref]=rd.subtree(sha,['planning','dependency_records','values',ref]);assert action_state_hash(values[ref])==ref
   restored[key]=values[ref]
  assert restored==request and action_state_hash(restored)==h;assert action_state_hash(record['output'])==record['output_sha256']
  before_output_hash=rd.subtree(r6['pre_state_hash'],['planning','dependency_records',kind,h,'output_sha256']);assert before_output_hash==record['output_sha256']
  return record
 own_hashes=[]
 for item in after_own:
  record=validate_dependency('ownership',item);assert validate_ownership_output(item,record['output'])==record['output'];own_hashes.append(record['input_sha256'])
 before_batches=ownership_batches(before_own);after_batches=ownership_batches(after_own);assert before_batches==after_batches
 inputs=rd.subtree(sha,['planning','gap_inputs']);records=rd.subtree(sha,['planning','gap_records']);catalog=rd.subtree(sha,['prepared_input','evidence_catalog']);policy=rd.subtree(sha,['conflict_confirmations']);actual_inputs=_support_review_inputs(SimpleNamespace(action_input={'evidence_catalog':catalog}),inputs)
 modules=[];support_count=0
 for item,actual,record in zip(inputs,actual_inputs,records,strict=True):
  if record is None:continue
  assert record['input_hash']==action_state_hash(item);normalized=postprocess_planning_support_decisions(None,{'item_input':item,'item_output':record['output']});assert normalized==record['output'];byindex={d['test_design_item_index']:d for d in item['missing_test_design_items']}
  for decision in normalized['decisions']:
   request=support_input(actual,byindex[decision['test_design_item_index']],policy=policy);dep=validate_dependency('support',request);assert dep['output']=={k:v for k,v in decision.items() if k!='test_design_item_index'};support_count+=1
  modules.append({'module_index':item['module_index'],'module_name':item['module_name'],'current_complete_input_hash':record['input_hash'],'decision_count':len(normalized['decisions']),'domain_normalization_exact':True,'all_dependency_bodies_and_output_hashes_revalidated':True,'dependency_outputs_unchanged_from_before_action6':True})
 events=[decoded(e) for e in db.execute(text("SELECT payload FROM agent_run_events WHERE run_id=63 AND event_type='agent_action_started'")).scalars()];call11=next(e['tool_call_id'] for e in events if e.get('started_sequence')==11);r11=receipt(db,call11);args=json.loads(rd.store.get(r11['arguments_ref'])['arguments_json']);idx=args['item_index'];before=rd.subtree(r11['pre_state_hash'],['planning','planning_records',idx,'output']);after=rd.subtree(r11['post_state_hash'],['planning','planning_records',idx,'output']);feedback=json.loads(Path(__file__).with_name('second-revision-reason.json').read_text(encoding='utf8'))['latest_revision_feedback'];selected=[{**e,'missing_test_design_items':[d for d in e['missing_test_design_items'] if idx in d['planning_batch_indexes']]} for e in feedback];selected=[e for e in selected if e['missing_test_design_items']];protected=_protected_draft_topics(before,selected);_validate_protected_draft_topics(after,protected);editable={d['coverage_intent'] for e in selected for d in e['missing_test_design_items']};after_byname={c['name']:c for c in after['module_candidates']};changed=[{'candidate':c['name'],'old_topic':topic,'authorized':coverage_topic_intent(topic) in editable} for c in before['module_candidates'] for topic in c['coverage_topics'] if topic not in after_byname.get(c['name'],{}).get('coverage_topics',[])];assert all(x['authorized'] for x in changed)
 report={'run_id':63,'ownership':{'before_state_hash':r6['pre_state_hash'],'after_rebuild7_state_hash':sha,'scope_count':len(after_own),'sdk_batch_count':len(after_batches),'all_current_requests_exact_equal_before_repair':True,'mechanical_batched_inputs_exact_equal':True,'dependency_input_hashes':own_hashes,'complete_reference_bodies_revalidated':True,'dependency_outputs_exact_equal_before_repair':True},'support':{'current_completed_modules':len(modules),'current_pending_modules':[x['module_index'] for x,r in zip(inputs,records,strict=True) if r is None],'revalidated_single_intent_dependency_count':support_count,'modules':modules,'source':'当前完整gap input +当前source_review及scope_evidence +当前真实confirmation policy，经support_input重建完整依赖，逐引用正文/hash/normalized验收；非只信影子旧output'},'revision11':{'item_index':idx,'question':args['question'],'protected_topic_count':len(protected),'all_protected_topics_exact':True,'changed_topics':changed,'before_topics':sum(len(c['coverage_topics']) for c in before['module_candidates']),'after_topics':sum(len(c['coverage_topics']) for c in after['module_candidates']),'new_business_question_answered_by_audit':False},'shared_dependency_value_bodies_read_once':len(values),'model_requests_sent':0,'business_database_writes':0}
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
Path(__file__).with_name('dependency-and-topic-reuse-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'ownership':{k:v for k,v in report['ownership'].items() if k!='dependency_input_hashes'},'support_completed':len(modules),'support_intents':support_count,'revision11':report['revision11'],'memoized_value_bodies':len(values)},ensure_ascii=False))
