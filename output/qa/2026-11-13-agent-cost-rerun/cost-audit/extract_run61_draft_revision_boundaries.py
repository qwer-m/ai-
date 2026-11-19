from pathlib import Path
import sys,json,runpy,gzip
from copy import deepcopy
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'backend'))
from core.db.database import SessionLocal
from sqlalchemy import text
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform import test_generation_planning_agent as planning
common=runpy.run_path(str(ROOT/'output/qa/2026-11-12-single-agent-live/round-10/_audit_planning_readonly.py'));decode=common['decoded'];out=ROOT/'backend/tests/agent_platform/fixtures/run61_draft_revision_boundaries_real.json.gz';value={'provenance':{'run_id':61,'node_id':413,'source_document_id':287,'business_database_writes':0,'model_requests_sent':0,'extraction':'真实行动21/22/24/26不可变pre/post state leaf，未修改旧model/normalized回执'},'cases':[]}
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));reader=common['TargetedReader'](db);run=dict(db.execute(text("SELECT id,user_id,project_id,workflow_definition_id,status,input_payload,JSON_EXTRACT(run_context,'$.artifacts.requirement_source') AS source_ref FROM agent_runs WHERE id=61")).mappings().one());value['run_metadata']={k:run[k] for k in ('id','user_id','project_id','workflow_definition_id','status')};value['input_payload']=decode(run['input_payload']);value['initial_run_context']={'artifacts':{'requirement_source':decode(run['source_ref'])}};headers=db.execute(text("SELECT id,JSON_EXTRACT(record,'$.started_sequence') AS seq,JSON_EXTRACT(record,'$.status') AS status FROM agent_action_receipts WHERE branch_id='c5608b67-728c-49d5-a08e-e890fcddbf39'")).mappings().all();ids={int(decode(r['seq'])):r['id'] for r in headers if decode(r['status'])=='completed'}
 for seq in (21,22,24,26):
  rec=decode(db.execute(text('SELECT record FROM agent_action_receipts WHERE id=:id'),{'id':ids[seq]}).scalar_one());args=reader.store.get(rec['arguments_ref']);args=json.loads(args['arguments_json']);case={'action_sequence':seq,'receipt_id':ids[seq],'tool_key':rec['tool_key'],'tool_call_id':rec['tool_call_id'],'arguments':args,'pre_root_sha256':rec['pre_state_hash'],'post_root_sha256':rec['post_state_hash']}
  for label,h in [('before',rec['pre_state_hash']),('after',rec['post_state_hash'])]:
   state=reader.subtree(h,['planning']);feedback=planning._route_feedback(state);case[label]={'draft_records':{str(i):deepcopy(state['planning_records'][i]) for i in (1,21)},'route_feedback':feedback,'pending_inference_state':{k:deepcopy(state[k]) for k in ('plan','planning_inputs','planning_records','last_route_feedback')},'full_gap_records':deepcopy(state['gap_records']),'full_route_review_revisions':deepcopy(state['route_review_revisions']),'full_gap_inputs':deepcopy(state['gap_inputs']),'planning_state_sha256':action_state_hash(state)}
   if seq==24 and label=='before':
    case['prepare_apply_context']={'planning_state':state,'prepared_input':reader.subtree(h,['prepared_input']),'artifacts':reader.subtree(h,['source','artifacts']),'confirmed_conflicts':reader.subtree(h,['conflict_confirmations'])}
  child_ids=db.execute(text("SELECT id FROM agent_validated_calls WHERE branch_id='c5608b67-728c-49d5-a08e-e890fcddbf39' AND parent_tool_call_id=:call"),{'call':rec['tool_call_id']}).scalars().all();child=[]
  for id in child_ids:
   data=decode(db.execute(text('SELECT record FROM agent_validated_calls WHERE id=:id'),{'id':id}).scalar_one());child.append({'id':id,'identity':data['identity'],'raw_output':reader.store.get(data['raw_output_ref']),'normalized_output':reader.store.get(data['output_ref']),'raw_output_sha256':data['raw_output_hash'],'normalized_output_sha256':data['output_hash']})
  case['original_validated_expert_outputs']=child;value['cases'].append(case)
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
value['data_sha256']=action_state_hash(value);out.write_bytes(gzip.compress(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode(),compresslevel=9,mtime=0));print(json.dumps({'path':str(out),'gzip_bytes':out.stat().st_size,'data_sha256':value['data_sha256'],'cases':[{'seq':x['action_sequence'],'expert_outputs':len(x['original_validated_expert_outputs']),'before_route_gap_count':len(x['before']['route_feedback']),'after_route_gap_count':len(x['after']['route_feedback'])} for x in value['cases']]},ensure_ascii=False))
