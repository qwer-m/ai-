"""Run63终态请求与阶段费用核对；只读实例诊断，不展开完整state。"""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import json,runpy
from pathlib import Path
BASE=Path(__file__).parent;ns=runpy.run_path(str(BASE/'capture_cost_progress.py'));SessionLocal,text,decoded=ns['SessionLocal'],ns['text'],ns['decoded']

def union_seconds(intervals):
 total=0.0;current=None
 for start,end in sorted(intervals):
  if current is None:current=[start,end]
  elif start<=current[1]:current[1]=max(current[1],end)
  else:total+=(current[1]-current[0]).total_seconds();current=[start,end]
 return total+((current[1]-current[0]).total_seconds() if current else 0)

with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 run=dict(db.execute(text("SELECT id,status,started_at,finished_at,error_message,JSON_EXTRACT(run_context,'$.usage') AS observed_usage,JSON_EXTRACT(run_context,'$.confirmation_wait_seconds') AS human_wait_seconds FROM agent_runs WHERE id=63")).mappings().one());run['usage']=decoded(run.pop('observed_usage'));run['human_wait_seconds']=float(decoded(run['human_wait_seconds']) or 0)
 nodes=[dict(r) for r in db.execute(text("SELECT id,attempt,status,error_message,JSON_EXTRACT(sdk_state,'$.model_requests') AS requests,JSON_EXTRACT(sdk_state,'$.error_diagnostic.model_requests') AS failure_requests,JSON_EXTRACT(sdk_state,'$.tool_calls') AS tool_calls,JSON_EXTRACT(sdk_state,'$.action_checkpoint.budget') AS budget FROM agent_node_runs WHERE run_id=63")).mappings()]
 events=[{**dict(e),'payload':decoded(e['payload'])} for e in db.execute(text("SELECT sequence,event_type,created_at,payload FROM agent_run_events WHERE run_id=63 AND event_type IN ('agent_action_started','agent_action_completed','agent_action_failed','managed_concurrency_adjusted')")).mappings()]
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
assert run['finished_at'] is not None
requests=[];tools=[];headers=[]
for node in sorted(nodes,key=lambda n:n['id']):
 top=decoded(node['requests']);error=decoded(node['failure_requests']);actual=top if isinstance(top,list) else error or []
 requests.extend({**r,'owner_node_id':node['id']} for r in actual);tools.extend({**t,'owner_node_id':node['id']} for t in decoded(node['tool_calls']) or [])
 headers.append({k:node[k] for k in ('id','attempt','status','error_message')}|{'diagnostic_source':'top_level' if isinstance(top,list) else 'error_diagnostic','top_request_count':len(top) if isinstance(top,list) else None,'failure_request_count':len(error) if isinstance(error,list) else None,'measured_requests':len(actual),'budget_cumulative_do_not_sum':decoded(node['budget'])})
usage=Counter();agents=defaultdict(Counter);models=defaultdict(Counter);by_action=defaultdict(list)
for r in requests:
 measured=r.get('usage') or {};usage.update(measured);agent=agents[r['agent_key']];agent.update(measured);agent['attempted_requests']+=1;agent['failed_requests']+=r['status']!='response_received';agent['sdk_elapsed_sum_seconds']+=r.get('elapsed_seconds',0)
 model=models[r['model']['name']+'/'+r['model']['route']];model.update(measured);model['attempted_requests']+=1;model['failed_requests']+=r['status']!='response_received';by_action[r.get('action_call_id')].append(r)
intervals=[(datetime.fromisoformat(r['started_at']),datetime.fromisoformat(r['finished_at'])) for r in requests];inflight=union_seconds(intervals);wall=(run['finished_at']-run['started_at']).total_seconds();complete=len(requests)==run['usage']['attempted_requests'];reconciled=all(usage[key]==run['usage'][key] for key in ('requests','input_tokens','output_tokens','total_tokens'))
starts={};actions=[]
for event in sorted(events,key=lambda e:e['sequence']):
 p=event['payload'];call=p.get('tool_call_id')
 if event['event_type']=='agent_action_started':starts[call]=event
 elif event['event_type'] in {'agent_action_completed','agent_action_failed'}:
  own=by_action[call];spans=[(datetime.fromisoformat(r['started_at']),datetime.fromisoformat(r['finished_at'])) for r in own];actions.append({'action_sequence':p['started_sequence'],'action':p['tool_key'],'status':p['status'],'wall_seconds':(event['created_at']-starts[call]['created_at']).total_seconds(),'attempted_requests':len(own),'reported_usage':dict(sum((Counter(r.get('usage') or {}) for r in own),Counter())),'sdk_inflight_union_seconds':union_seconds(spans)})
failures=[r for r in requests if r['status']!='response_received'];rejects=[]
for t in tools:
 observations=[o for o in t.get('tool_observations',[]) if o.get('status') == 'rejected']
 if observations:rejects.append({'agent_tool':t.get('tool_key'),'owner_node_id':t['owner_node_id'],'input_hash':t.get('input_hash'),'status':t.get('status'),'observations':observations,'submissions_count':len(t.get('submissions',[]))})
replayed=[t for t in tools if t.get('replayed')]
managed=defaultdict(Counter)
for t in tools:
 if 'usage' not in t:continue
 k=t['tool_key'].removeprefix('agent:');managed[k].update(t['usage']);managed[k]['validated_sessions']+=1
root=Counter(run['usage'])
for value in managed.values():
 for key in ('requests','input_tokens','output_tokens','total_tokens'):root[key]-=value[key]
managed['test_generation_coordinator']=Counter({key:root[key] for key in ('requests','input_tokens','output_tokens','total_tokens')})
assert all(sum(v[key] for v in managed.values())==run['usage'][key] for key in ('requests','input_tokens','output_tokens','total_tokens'))
first=min(s for s,e in intervals);last=max(e for s,e in intervals)

progress=json.loads((BASE/'cost-progress.json').read_text(encoding='utf8'))
report={'run':run,'run_wall_seconds':wall,'human_wait_seconds':run['human_wait_seconds'],'active_elapsed_seconds':wall-run['human_wait_seconds'],'node_boundaries':headers,'retained_diagnostic_window':{'first_sequence':min(r['sequence'] for r in requests),'last_sequence':max(r['sequence'] for r in requests),'first_started_utc':first,'last_finished_utc':last,'wall_seconds':(last-first).total_seconds(),'sdk_interval_union_seconds':inflight,'outside_sdk_seconds_within_retained_window':(last-first).total_seconds()-inflight},'complete_usage_by_managed_session_includes_nested_agents':dict(managed),'root_usage_derived_total_minus_managed_sessions':True,'request_count':len(requests),'all_attempt_boundaries_retained':complete,'all_reported_usage_reconciled':reconciled,'reported_usage_from_actual_boundaries':dict(usage),'per_agent_from_retained_request_diagnostics_only':dict(agents),'per_model_route_from_retained_request_diagnostics_only':dict(models),'sdk_inflight_union_seconds':inflight,'outside_any_sdk_request_seconds':wall-run['human_wait_seconds']-inflight if complete and reconciled else None,'actions_from_retained_request_diagnostics_only':actions,'failure_type_counts':dict(Counter((r.get('exception_chain') or [{}])[0].get('error_type') for r in failures)),'failed_requests':failures,'local_tool_rejections':rejects,'replayed_managed_call_count':len(replayed),'replayed_managed_calls':replayed,'concurrency_adjustments':[e for e in events if e['event_type']=='managed_concurrency_adjusted'],'source_fact_coverage':progress['source_fact_coverage'],'support_saved_count':len(progress['support_receipts']),'support_decisions_per_session_max':progress['support_decisions_per_session_max'],'draft_revision_actions':progress['draft_repair_arguments'],'generation_default_packing':progress['generation_default_packing'],'candidate_case_count':70,'delivered_case_count':0,'model_requests_sent_by_audit':0,'business_database_writes':0,'limits':['Run63预算独立且包含失败未知费用保留，不能抵销Run61；reported tokens并非供应商正式账单。','SDK在途包含网络/供应商/SDK处理；outside包含初始化、CPU、DB等，不称全部CPU。','Run63没有传输错误；最终来源重检完成，准备下一治理输入时capacity硬失败。70条候选尚未通过最终交付；只保留后200请求详细时序，不能把后200请求分组费用当全283请求费用。']}
(BASE/'terminal-cost-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf8');print(json.dumps({'status':run['status'],'active':report['active_elapsed_seconds'],'requests':len(requests),'all_attempts':complete,'usage_reconciled':reconciled,'failure_types':report['failure_type_counts'],'sdk_inflight':inflight,'outside':report['outside_any_sdk_request_seconds'],'per_agent_from_retained_request_diagnostics_only':dict(agents),'replays':len(replayed),'rejected_submission_count':len(rejects),'complete_usage_by_managed_session_includes_nested_agents':dict(managed)},ensure_ascii=False))
