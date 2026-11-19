"""Run64当前真实状态只读审计；不执行/审批/重试任何任务。"""
import sys,json
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'backend'))
from sqlalchemy import select,text
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun,AgentNodeRun,AgentRunEvent,AgentActionReceiptRecord,AgentValidatedCallRecord
RUN_ID=64
with SessionLocal() as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 run=db.execute(select(AgentRun.id,AgentRun.status,AgentRun.current_node_key,AgentRun.error_message,AgentRun.started_at,AgentRun.finished_at,AgentRun.run_context['usage'].label('usage'),AgentRun.run_context['execution_limits'].label('limits'),AgentRun.run_context['runtime_registry_signature'].label('signature'),AgentRun.run_context['confirmation_wait_seconds'].label('confirmation_wait_seconds')).where(AgentRun.id==RUN_ID)).mappings().one()
 nodes=db.execute(select(AgentNodeRun.id,AgentNodeRun.node_key,AgentNodeRun.status,AgentNodeRun.error_message,AgentNodeRun.started_at,AgentNodeRun.finished_at,AgentNodeRun.sdk_state['action_checkpoint'].label('checkpoint'),AgentNodeRun.sdk_state['model_requests'].label('model_requests'),AgentNodeRun.sdk_state['action_usage'].label('action_usage')).where(AgentNodeRun.run_id==RUN_ID)).mappings().all()
 events=db.execute(select(AgentRunEvent.sequence,AgentRunEvent.event_type,AgentRunEvent.payload).where(AgentRunEvent.run_id==RUN_ID).order_by(AgentRunEvent.sequence)).mappings().all()
 values=[]
 for node in nodes:
  value=dict(node)
  checkpoint=node['checkpoint'] or {}
  branch=(checkpoint.get('journal_ref') or {}).get('branch_id')
  receipts=[]; children=[]
  if branch:
   receipts=[dict(row) for row in db.execute(select(AgentActionReceiptRecord.id,AgentActionReceiptRecord.tool_call_id,AgentActionReceiptRecord.record,AgentActionReceiptRecord.created_at).where(AgentActionReceiptRecord.branch_id==branch).order_by(AgentActionReceiptRecord.id)).mappings()]
   children=[dict(row) for row in db.execute(select(AgentValidatedCallRecord.id,AgentValidatedCallRecord.agent_key,AgentValidatedCallRecord.parent_tool_call_id,AgentValidatedCallRecord.record).where(AgentValidatedCallRecord.branch_id==branch).order_by(AgentValidatedCallRecord.id)).mappings()]
  value['receipts']=receipts;value['children']=children
  values.append(value)
 db.rollback()
now=datetime.now(timezone.utc)
result={'observed_at':now.isoformat(),'mode':'live SQL READ ONLY; no model/execution/approval','run':dict(run),'nodes':values,'events':[dict(row) for row in events]}
file=Path(__file__).with_name('snapshot-'+now.strftime('%Y%m%dT%H%M%S')+'.json')
file.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
summary={'observed_at':result['observed_at'],'run':dict(run),'nodes':[],'events':len(events),'saved_to':str(file)}
for node in values:
 cp=node['checkpoint'] or {};latest={row['tool_call_id']:row['record'] for row in node['receipts']}
 summary['nodes'].append({'id':node['id'],'status':node['status'],'projection':cp.get('projection'),'budget':cp.get('budget'),'actions':[{'tool_key':r['tool_key'],'status':r['status']} for r in latest.values()],'child_agents':[r['agent_key'] for r in node['children']],'model_request_boundaries':len(node['model_requests'] or [])})
print(json.dumps(summary,ensure_ascii=False,indent=2,default=str))
