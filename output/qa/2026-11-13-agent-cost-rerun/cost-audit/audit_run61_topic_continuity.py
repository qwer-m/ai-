from pathlib import Path
import sys,json,runpy
from copy import deepcopy
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'backend'))
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.test_generation_planning import coverage_topic_intent
from modules.agent_platform.planning_dependencies import support_input
common=runpy.run_path(str(ROOT/'output/qa/2026-11-12-single-agent-live/round-10/_audit_planning_readonly.py'));dec=common['decoded'];out=ROOT/'output/qa/2026-11-13-agent-cost-rerun/run61-confirmation-audit';report={'run_id':61,'model_requests_sent':0,'business_writes':0}
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));reader=common['TargetedReader'](db);rows=db.execute(text("SELECT id,JSON_EXTRACT(record,'$.started_sequence') AS seq,JSON_EXTRACT(record,'$.status') AS status FROM agent_action_receipts WHERE branch_id='c5608b67-728c-49d5-a08e-e890fcddbf39'")).mappings().all();ids={int(dec(r['seq'])):r['id'] for r in rows if dec(r['status'])=='completed'};receipts={k:dec(db.execute(text('SELECT record FROM agent_action_receipts WHERE id=:id'),{'id':ids[k]}).scalar_one()) for k in (24,26)};a=receipts[24];b=receipts[26];before=reader.subtree(a['pre_state_hash'],['planning','planning_records',21]);after=reader.subtree(a['post_state_hash'],['planning','planning_records',21]);before_topics=[t for c in before['output']['module_candidates'] for t in c['coverage_topics']];after_topics=[t for c in after['output']['module_candidates'] for t in c['coverage_topics']];changes=[]
 for old in before_topics:
  new=next((x for x in after_topics if x['name']==old['name']),None);changes.append({'name':old['name'],'old':old,'new':new,'exactly_unchanged':old==new})
 report['batch21_topic_changes']=changes;report['changed_count']=sum(not r['exactly_unchanged'] for r in changes)
 views={}
 for stage,state_hash in [('before24',a['pre_state_hash']),('after26',b['post_state_hash'])]:
  state={k:reader.subtree(state_hash,['planning',k]) for k in ('gap_inputs','gap_records','route_review_revisions')};evidence=reader.subtree(state_hash,['prepared_input','evidence_catalog']);policy=reader.subtree(state_hash,['conflict_confirmations']);ctx=ToolExecutionContext(db=None,user_id=1,project_id=8,run_id=61,node_key='readonly_support_topics',run_input={},action_input={'evidence_catalog':evidence});source_inputs=planning._support_review_inputs(ctx,state['gap_inputs']);mapped={}
  for idx,item in enumerate(state['gap_inputs']):
   review=planning._current_support_review(state,idx)
   if review is None:continue
   by_index={d['test_design_item_index']:d for d in review['decisions']}
   for design in item['missing_test_design_items']:
    if any(word in design['coverage_intent'] for word in ('选择类小题判分','拼写与填空宽松判定')):
     proof=support_input(source_inputs[idx],design,policy=policy);mapped[design['coverage_intent']]={'module_name':item['module_name'],'module_objective':item['module_objective'],'design':design,'decision':by_index[design['test_design_item_index']],'support_input':proof,'support_input_sha':action_state_hash(proof)}
  views[stage]=mapped
 result=[]
 for intent,old in views['before24'].items():
  new=views['after26'].get(intent);diff=[k for k in set(old['support_input'])|set(new['support_input']) if old['support_input'].get(k)!=new['support_input'].get(k)] if new else None
  result.append({'intent':intent,'before24':{k:v for k,v in old.items() if k!='support_input'},'after26':{k:v for k,v in new.items() if k!='support_input'} if new else None,'full_support_input_unchanged':bool(new and old['support_input']==new['support_input']),'changed_dependency_fields':diff})
 report['actual_review_continuity']=result;report['raw_support_input_views']=views;assert not db.new and not db.dirty and not db.deleted;db.rollback()
(out/'action24-topic-continuity.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in report.items() if k!='raw_support_input_views'},ensure_ascii=False))
