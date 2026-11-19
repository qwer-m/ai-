from pathlib import Path
import sys,json,gzip
from datetime import datetime
sys.path.insert(0,'backend')
from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun
from modules.agent_platform import checkpoint_storage as storage
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.runtime import _accepted_retry_progress
root=Path.cwd();decode=lambda v:json.loads(v) if isinstance(v,str) else v;data={'provenance':{'run_id':62,'node_ids':[414,415,416,417],'business_database_writes':0,'model_requests_sent':0,'source':'真实终态62持久检查点、跨分支验证身份及不可变结果摘要；不展开原始模型/领域大JSON'},'node_rows':[],'frontiers':[]}
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));run=dict(db.execute(text("SELECT id,user_id,project_id,workflow_definition_id,status,input_payload,created_at,JSON_EXTRACT(run_context,'$.logical_task_id') AS logical_task_id,JSON_EXTRACT(run_context,'$.artifacts.requirement_source') AS source FROM agent_runs WHERE id=62")).mappings().one());data['run_row']={k:run[k] for k in ('id','user_id','project_id','workflow_definition_id','status','created_at')};data['run_row']['input_payload']=decode(run['input_payload']);data['run_row']['run_context']={'logical_task_id':decode(run['logical_task_id']),'artifacts':{'requirement_source':decode(run['source'])}};branches=set();call_limits={};receipt_limits={}
 for node_id in data['provenance']['node_ids']:
  row=dict(db.execute(text("SELECT id,run_id,node_key,node_type,agent_definition_id,status,attempt,input_payload,created_at,started_at,finished_at,JSON_EXTRACT(sdk_state,'$.action_checkpoint') AS cp,JSON_EXTRACT(sdk_state,'$.transient_failure_count') AS transient_count,JSON_EXTRACT(sdk_state,'$.route_health_failure_count') AS health_count,JSON_EXTRACT(sdk_state,'$.retry_exhausted') AS exhausted,JSON_EXTRACT(sdk_state,'$.model_requests') AS requests FROM agent_node_runs WHERE id=:id"),{'id':node_id}).mappings().one());cp=decode(row.pop('cp'));historical={'transient_failure_count':decode(row.pop('transient_count')),'route_health_failure_count':decode(row.pop('health_count')),'retry_exhausted':decode(row.pop('exhausted'))};requests=decode(row.pop('requests')) or [];row['input_payload']=decode(row['input_payload']);row['sdk_state']={'action_checkpoint':cp};data['node_rows'].append(row);node=AgentNodeRun(**row);canonical=storage.call_records(db,node,cp);proof={k:{f:v[f] for f in ('identity','raw_output_hash','output_hash')} for k,v in canonical.items()};token=action_state_hash({'state_hash':cp['state_hash'],'validated_calls':proof});assert _accepted_retry_progress(AgentRunRepository(db),node)==token;chain=storage.journal_chain(db,node,cp)
  for branch_id,receipt_cursor,call_cursor in chain:
   branches.add(branch_id);call_limits[branch_id]=max(call_limits.get(branch_id,0),call_cursor);receipt_limits[branch_id]=max(receipt_limits.get(branch_id,0),receipt_cursor)
  failures=[{k:v for k,v in r.items() if k in ('status','agent_key','action_tool','action_call_id','input_hash','failure_kind','error_type','http_status','status_code','sequence')} for r in requests if r.get('status')!='response_received'];data['frontiers'].append({'node_id':node_id,'attempt':row['attempt'],'state_hash':cp['state_hash'],'validated_calls':proof,'validated_call_count':len(proof),'progress_token':token,'budget':cp['budget'],'historical_retry_state':historical,'nonresponse_requests':failures})
 data['branch_rows']=[dict(db.execute(text('SELECT * FROM agent_journal_branches WHERE id=:id'),{'id':id}).mappings().one()) for id in sorted(branches)];data['call_rows']=[];data['receipt_rows']=[]
 for branch_id in sorted(branches):
  for table,key,limit in [('agent_validated_calls','call_rows',call_limits[branch_id]),('agent_action_receipts','receipt_rows',receipt_limits[branch_id])]:
   rows=db.execute(text(f'SELECT * FROM {table} WHERE branch_id=:br AND id<=:cursor'),{'br':branch_id,'cursor':limit}).mappings().all()
   for r in rows:
    item=dict(r);item['record']=decode(item['record']);data[key].append(item)
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
# JSON时间只用于复原原元数据，不参与进度token。
def plain(v):
 if isinstance(v,datetime):return v.isoformat()
 if isinstance(v,dict):return {k:plain(x) for k,x in v.items()}
 if isinstance(v,list):return [plain(x) for x in v]
 return v
data=plain(data);data['data_sha256']=action_state_hash(data);path=root/'backend/tests/agent_platform/fixtures/run62_transient_progress_real.json.gz';path.write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),compresslevel=9,mtime=0));summary={'fixture':str(path),'gzip_bytes':path.stat().st_size,'data_sha256':data['data_sha256'],'calls':len(data['call_rows']),'receipts':len(data['receipt_rows']),'frames':[{'node':x['node_id'],'count':x['validated_call_count'],'token':x['progress_token'],'historical':x['historical_retry_state'],'failed':x['nonresponse_requests']} for x in data['frontiers']]};(root/'output/qa/2026-11-13-agent-cost-rerun/run62-retry-progress-extraction.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(summary,ensure_ascii=False))
