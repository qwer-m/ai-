"""只读重建真实初始化/重检治理输入，统计无损列式表示；不调用模型。"""
import runpy,json,gzip
from collections import Counter
from copy import deepcopy
from pathlib import Path
ns=runpy.run_path(str(Path(__file__).with_name('capture_cost_progress.py')))
SessionLocal,text,decoded,Reader=ns['SessionLocal'],ns['text'],ns['decoded'],ns['TargetedReader']
receipt,h=ns['receipt_record'],ns['action_state_hash']
from core.db.model_defs import AgentRun
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.autonomous_budget import estimate_json_tokens

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def details(item,policy):
 payload=semantics.authority_model_payload(item,confirmed_conflicts=policy)
 view=payload['source_review'];facts=view['authoritative_facts']
 fields=sorted({key for f in facts for key in f})
 # 每列存在性单独编码，保留缺失与显式null的区别，不按业务内容删除字段。
 matrix={k:deepcopy(v) for k,v in view.items() if k!='authoritative_facts'}
 matrix['fact_columns']=fields
 matrix['fact_rows']=[[sorted(f),[deepcopy(f[key]) for key in sorted(f)]] for f in facts]
 compact={**payload,'source_review':matrix}
 common=[key for key in fields if all(key in f for f in facts)]
 optional=[key for key in fields if key not in common]
 dense={k:deepcopy(v) for k,v in view.items() if k!='authoritative_facts'}
 dense['fact_columns']=common
 dense['fact_rows']=[[deepcopy(f[key]) for key in common]+([{key:deepcopy(f[key]) for key in optional if key in f}] if optional else []) for f in facts]
 dense_payload={**payload,'source_review':dense}
 report={'payload_sha256':h(payload),'estimated_tokens':estimate_json_tokens(payload),'json_chars':len(encoded(payload)),
 'top_fields':{key:len(encoded(value)) for key,value in payload.items()},'view_fields':{key:len(encoded(value)) for key,value in view.items()},
 'fact_field_counts':dict(Counter(key for f in facts for key in f)),
 'fact_field_value_chars':{key:sum(len(encoded(f[key])) for f in facts if key in f) for key in fields},
 'fact_field_key_chars':{key:sum(len(encoded(key))+1 for f in facts if key in f) for key in fields},
 'common_columns':common,'optional_columns':optional,
 'dense_matrix_json_chars':len(encoded(dense_payload)),'dense_matrix_estimated_tokens':estimate_json_tokens(dense_payload),
 'per_row_fields_matrix_estimated_tokens':estimate_json_tokens(compact)}
 return report,payload

with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));rd=Reader(db)
 r=receipt(db,'call_371c189bf7c345e9ae8dc47c8bcc8bd8');sha=r['pre_state_hash']
 state=rd.subtree(sha,['planning']);prepared=rd.subtree(sha,['prepared_input']);artifacts=rd.subtree(sha,['source','artifacts']);policy=rd.subtree(sha,['conflict_confirmations']);run=db.get(AgentRun,63)
 calls=[decoded(x) for x in db.execute(text("SELECT c.record FROM agent_validated_calls c JOIN agent_journal_branches b ON b.id=c.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=63 AND c.parent_tool_call_id=:call AND c.agent_key='test_source_semantics_analyst'"),{'call':r['tool_call_id']}).scalars()]
 assert len(calls)==1;call=calls[0];output=rd.store.get(call['output_ref']);assert h(output)==call['output_hash']
 ctx=ToolExecutionContext(db=db,user_id=1,project_id=8,run_id=63,node_key='agent_network',run_input=deepcopy(run.input_payload),action_input=prepared,action_state=state,artifacts=artifacts,confirmed_conflicts=policy)
 args=json.loads(rd.store.get(r['arguments_ref'])['arguments_json']);action=planning.prepare_source_reinspection(ctx,args);assert h({**action['items'][0],'_user_confirmations':policy})==call['identity']['input_hash']
 planning.apply_source_reinspection(ctx,action,[deepcopy(output)])
 facts=deepcopy(ctx.action_state['source_semantics']['authoritative_facts']);facts.sort(key=semantics._fact_source_order)
 item={'module_index':0,'module':{'name':'完整来源事实治理','fact_ids':[f['fact_id'] for f in facts],'evidence_ids':list(dict.fromkeys(f['scope_id'] for f in facts))},'authoritative_facts':facts,'authority_baseline':deepcopy(ctx.action_state['authority_baseline'])}
 reviewed=semantics._authority_input_facts(item);item['source_review']=semantics.authority_source_review_view(reviewed)
 item['source_review']['revision_fact_ids']=deepcopy(item['authority_baseline']['revision_fact_ids']);item['source_review']['source_delta']=semantics._source_transition_view(item['authority_baseline'])
 reinspection,payload=details(item,policy);assert reinspection['estimated_tokens']==66521;assert reinspection['json_chars']==157623
 initial_receipt=receipt(db,'call_687d8665cb7a4d799ba06beed60d0cd2');initial_sha=initial_receipt['post_state_hash'];initial_item=rd.subtree(initial_sha,['source','authority_inputs',0]);initial_policy=rd.subtree(initial_sha,['conflict_confirmations'])
 initial,initial_payload=details(initial_item,initial_policy)
 fixture={'provenance':{'run_id':63,'initial_state_hash':initial_sha,'reinspection_pre_state_hash':sha,'source_output_hash':call['output_hash'],'model_requests_sent':0,'business_database_writes':0},'initial':{'prepared':initial_item,'confirmations':initial_policy,'original_model_payload':initial_payload},'reinspection':{'prepared':item,'confirmations':policy,'original_model_payload':payload}}
 report={'initial':initial,'reinspection':reinspection,'provenance':fixture['provenance']}
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
 target=Path(__file__).with_name('authority-payload-real.json.gz');target.write_bytes(gzip.compress(encoded(fixture).encode('utf8'),mtime=0))
 Path(__file__).with_name('authority-payload-field-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(report,ensure_ascii=False))
