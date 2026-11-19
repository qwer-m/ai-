"""纯CPU重放Run63已保存的来源重检结果，定位后续治理准入容量；不发送模型。"""
import runpy,json,traceback
from copy import deepcopy
from pathlib import Path
ns=runpy.run_path(str(Path(__file__).with_name('capture_cost_progress.py')));SessionLocal,text,decoded,Reader=ns['SessionLocal'],ns['text'],ns['decoded'],ns['TargetedReader'];receipt=ns['receipt_record'];h=ns['action_state_hash']
from core.db.model_defs import AgentRun
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.test_generation_semantics import AuthorityInputCapacityExceeded
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));rd=Reader(db);r=receipt(db,'call_371c189bf7c345e9ae8dc47c8bcc8bd8');args=json.loads(rd.store.get(r['arguments_ref'])['arguments_json']);sha=r['pre_state_hash'];state=rd.subtree(sha,['planning']);prepared=rd.subtree(sha,['prepared_input']);artifacts=rd.subtree(sha,['source','artifacts']);policy=rd.subtree(sha,['conflict_confirmations']);run=db.get(AgentRun,63);calls=[decoded(x) for x in db.execute(text("SELECT c.record FROM agent_validated_calls c JOIN agent_journal_branches b ON b.id=c.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=63 AND c.parent_tool_call_id=:call AND c.agent_key='test_source_semantics_analyst'"),{'call':r['tool_call_id']}).scalars()];assert len(calls)==1;call=calls[0];output=rd.store.get(call['output_ref']);assert h(output)==call['output_hash'];ctx=ToolExecutionContext(db=db,user_id=1,project_id=8,run_id=63,node_key='agent_network',run_input=deepcopy(run.input_payload),action_input=prepared,action_state=state,artifacts=artifacts,confirmed_conflicts=policy)
 before=h([state,prepared,artifacts,policy]);action=planning.prepare_source_reinspection(ctx,args);managed={**action['items'][0],'_user_confirmations':policy} if policy else action['items'][0];assert h(managed)==call['identity']['input_hash'];stage='apply_source_reinspection';report={'run_id':63,'action_sequence':41,'pre_state_hash':sha,'actual_source_input_hash':call['identity']['input_hash'],'reconstructed_source_input_hash_exact':True,'actual_normalized_output_hash':call['output_hash'],'model_requests_sent':0,'business_database_writes':0}
 try:
  applied=planning.apply_source_reinspection(ctx,action,[deepcopy(output)]);report['source_apply_succeeded']=True;report['source_apply_changed']=applied['changed'];stage='prepare_source_authority';planning.prepare_source_authority(ctx);report['unexpected_no_capacity_failure']=True
 except AuthorityInputCapacityExceeded as error:
  report.update(failed_stage=stage,error_type=type(error).__name__,message=str(error),capacity_details=error.details,traceback=traceback.format_exc())
 report['stored_inputs_unchanged']=before==h([state,prepared,artifacts,policy]);assert report['stored_inputs_unchanged'];assert not db.new and not db.dirty and not db.deleted;db.rollback()
Path(__file__).with_name('source-reinspection-capacity-replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))
