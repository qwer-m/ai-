import runpy,json
from pathlib import Path
from types import SimpleNamespace
p=Path('output/qa/2026-11-13-agent-cost-rerun/round-02')
ns=runpy.run_path(str(p/'capture_cost_progress.py')); SessionLocal,text,decoded,Reader,receipt=map(ns.get,['SessionLocal','text','decoded','TargetedReader','receipt_record'])
from modules.agent_platform.test_generation_planning_agent import _support_review_inputs
from modules.agent_platform.test_generation_planning import postprocess_planning_route_repair_item, prepare_planning_route_repairs
from modules.agent_platform.action_journal import action_state_hash
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY')); ident=2329; rec=decoded(db.execute(text('SELECT record FROM agent_validated_calls WHERE id=:id'),{'id':ident}).scalar_one()); r=receipt(db,rec['parent_tool_call_id']); rd=Reader(db)
 rev=rd.subtree(r['post_state_hash'],['planning','route_review_revisions',0]); catalog=rd.subtree(r['pre_state_hash'],['prepared_input','evidence_catalog']); confirmations=rd.subtree(r['pre_state_hash'],['conflict_confirmations']); item=_support_review_inputs(SimpleNamespace(action_input={'evidence_catalog':catalog}),[rev['input']])[0]
 arguments=json.loads(rd.store.get(r['arguments_ref'])['arguments_json']); prepared=prepare_planning_route_repairs(SimpleNamespace(artifacts={}, confirmed_conflicts=confirmations), {'plan':rd.subtree(r['pre_state_hash'],['planning','plan']), 'prepared_items':rd.subtree(r['pre_state_hash'],['planning','route_inputs']), 'route_records':rd.subtree(r['pre_state_hash'],['planning','route_records']), 'design_selections':arguments['design_selections'], 'additional_scope_ids':arguments['additional_scope_ids']})['items'][0]; prepared['_planning_feedback']=rev['input']['_planning_feedback']; item=_support_review_inputs(SimpleNamespace(action_input={'evidence_catalog':catalog}),[prepared])[0]
 managed={**item,'_user_confirmations':confirmations} if confirmations else item
 print('RECORDED',rec['identity']['input_hash'],'ACTUAL',action_state_hash(managed))
 print('DIFFKEYS',[(k,action_state_hash(v),action_state_hash(rev['input'].get(k))) for k,v in prepared.items() if v!=rev['input'].get(k)])
 assert action_state_hash(managed)==rec['identity']['input_hash']
 raw=rd.store.get(rec['raw_output_ref']); expected=rd.store.get(rec['output_ref']); actual=postprocess_planning_route_repair_item(None,{'item_input':managed,'item_output':raw}); assert actual==expected==rev['output']
 report={'run_id':61,'validated_call_id':ident,'parent_tool_call_id':rec['parent_tool_call_id'],'input_hash_exact':True,'source':"真实行动 pre_state plan/route_inputs/route_records 和行动 arguments 由原准备器重建，保留同次 revision 的 previous_decisions；以同来源目录和确认重建，hash与SDK保存完整输入hash完全相同",'fact_count':len(item['candidate_facts']),'decision_count':len(raw['decisions']),'raw_output_hash':action_state_hash(raw),'normalized_output_hash':action_state_hash(actual),'full_raw_ref_to_stable_id_validation':True,'source_conflict_candidate_count':sum(g['kind']=='source_conflict' for d in raw['decisions'] for g in d['support_gaps']),'business_truth_evaluation':'未独立判断业务口径，审批保持等待','model_requests_sent':0,'business_database_writes':0}
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
(p/'directed-support-ref-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False))
progress=json.loads((p/'cost-progress.json').read_text(encoding='utf-8'));progress['support_receipts'][str(ident)]['full_original_catalog_mapping_and_domain_validation']=True;progress['support_receipts'][str(ident)]['independent_validation_report']='directed-support-ref-validation.json';(p/'cost-progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

