"""Run63独立预算与阶段观察；每分钟一次，只读轻字段及新行动指定叶子。"""
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
import argparse,json,runpy,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT/'backend'))
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.test_generation_semantics import planning_support_fact_ref_catalog
from modules.agent_platform.test_generation_planning import postprocess_planning_support_decisions
TargetedReader=runpy.run_path(str(ROOT/'output/qa/2026-11-12-single-agent-live/round-10/_audit_planning_readonly.py'))['TargetedReader']
RUN_ID=63
DIR=Path(__file__).parent

def decoded(value):return json.loads(value) if isinstance(value,str) else value

def receipt_record(db,call_id):
 identity=db.execute(text("SELECT MAX(r.id) FROM agent_action_receipts r JOIN agent_journal_branches b ON b.id=r.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=:run AND r.tool_call_id=:call"),{'run':RUN_ID,'call':call_id}).scalar_one()
 return decoded(db.execute(text('SELECT record FROM agent_action_receipts WHERE id=:id'),{'id':identity}).scalar_one())

def verify_support(db, record, reader, catalog_cache):
    store = reader.store
    raw = store.get(record["raw_output_ref"])
    normalized = store.get(record["output_ref"])
    assert action_state_hash(raw) == record["raw_output_hash"]
    assert action_state_hash(normalized) == record["output_hash"]
    refs = [value for decision in raw["decisions"] for value in decision["fact_refs"]]
    refs += [value for decision in raw["decisions"] for gap in decision["support_gaps"] for value in gap["evidence_fact_refs"]]
    refs += [side["fact_ref"] for decision in raw["decisions"] for gap in decision["support_gaps"]
        if gap["kind"] == "source_conflict" for side in gap["conflict"]["sides"]]
    assert all(type(value) is int for value in refs)
    assert all("fact_ids" not in decision and "fact_refs" not in normal
        for decision, normal in zip(raw["decisions"], normalized["decisions"], strict=True))
    assert all(isinstance(identity, str) for decision in normalized["decisions"] for identity in decision["fact_ids"])
    receipt = receipt_record(db, record["parent_tool_call_id"])
    arguments = json.loads(store.get(receipt["arguments_ref"])["arguments_json"])
    checked = False
    if not arguments.get("design_selections") and not arguments.get("additional_scope_ids"):
        key = (receipt["pre_state_hash"], raw["module_index"])
        if key not in catalog_cache:
            entry = reader.entry_at(receipt["pre_state_hash"], ["planning", "gap_inputs"])
            count = reader.array_length(entry["ref"]) if "ref" in entry else len(entry["value"])
            index = next(index for index in range(count) if reader.subtree(receipt["pre_state_hash"],
                ["planning", "gap_inputs", index, "module_index"]) == raw["module_index"])
            catalog_cache[key] = reader.subtree(receipt["pre_state_hash"], ["planning", "gap_inputs", index])
        item = deepcopy(catalog_cache[key])
        selected = {decision["test_design_item_index"] for decision in raw["decisions"]}
        item["missing_test_design_items"] = [design for design in item["missing_test_design_items"]
            if design["test_design_item_index"] in selected]
        catalog = planning_support_fact_ref_catalog(item["candidate_facts"])
        decoded_output = deepcopy(raw)
        for decision in decoded_output["decisions"]:
            decision["fact_ids"] = [catalog[value] for value in decision.pop("fact_refs")]
            for gap in decision["support_gaps"]:
                gap["evidence_fact_ids"] = [catalog[value] for value in gap.pop("evidence_fact_refs")]
                if gap["kind"] == "source_conflict":
                    for side in gap["conflict"]["sides"]:
                        side["fact_id"] = catalog[side.pop("fact_ref")]
        assert postprocess_planning_support_decisions(None, {"item_input": item, "item_output": decoded_output}) == normalized
        checked = True
    return {"raw_integer_refs": True, "normalized_stable_ids": True,
        "full_original_catalog_mapping_and_domain_validation": checked, "decision_count": len(raw["decisions"]),
        "raw_output_hash": record["raw_output_hash"], "normalized_output_hash": record["output_hash"],
        "gap_kind_counts": dict(Counter(gap["kind"] for decision in raw["decisions"] for gap in decision["support_gaps"]))}


def source_coverage(rd,receipt):
 sha=receipt['post_state_hash']
 if receipt['tool_key']=='govern_source':
  facts=rd.subtree(sha,['source','source_authority','result','effective_facts']);inputs=rd.subtree(sha,['source','planning_inputs'])
 else:
  facts=rd.subtree(sha,['effective_facts','effective_facts']);inputs=rd.subtree(sha,['planning','planning_inputs'])
 expected={f['fact_id'] for f in facts};planned=[f['fact_id'] for item in inputs for scope in item['planning_scopes'] for f in scope['facts']]
 return {'action_sequence':receipt['started_sequence'],'post_state_hash':sha,'effective_fact_count':len(facts),'planning_unique_fact_count':len(set(planned)),'missing_fact_ids':sorted(expected-set(planned)),'unexpected_fact_ids':sorted(set(planned)-expected),'planning_fact_duplicate_counts':{identity:count for identity,count in Counter(planned).items() if count>1},'effective_ids_sha256':action_state_hash(sorted(expected)),'planning_ids_sha256':action_state_hash(sorted(set(planned))),'repaired_fact_DOC287_P0009_34_included':'DOC287-P0009-34' in expected and 'DOC287-P0009-34' in planned}

def capture(previous):
 with SessionLocal(autoflush=False) as db:
  db.execute(text('SET TRANSACTION READ ONLY'))
  run=dict(db.execute(text("SELECT id,status,started_at,finished_at,error_message,JSON_EXTRACT(run_context,'$.usage') AS observed_usage,JSON_EXTRACT(run_context,'$.confirmation_wait_seconds') AS wait_seconds FROM agent_runs WHERE id=:run"),{'run':RUN_ID}).mappings().one());run['usage']=decoded(run.pop('observed_usage'));wait=float(decoded(run.pop('wait_seconds')) or 0)
  pending=db.execute(text("SELECT JSON_UNQUOTE(JSON_EXTRACT(request_payload,'$.paused_at')) AS paused_at FROM agent_approvals WHERE run_id=:run AND status='pending'"),{'run':RUN_ID}).mappings().all()
  for approval in pending:
   if approval['paused_at']:wait+=max(0,(datetime.now(timezone.utc).replace(tzinfo=None)-datetime.fromisoformat(approval['paused_at'])).total_seconds())
  nodes=[dict(r) for r in db.execute(text("SELECT id,attempt,status,error_message,JSON_EXTRACT(sdk_state,'$.action_checkpoint.budget') AS budget,JSON_EXTRACT(sdk_state,'$.action_checkpoint.projection') AS projection,JSON_EXTRACT(sdk_state,'$.action_checkpoint.managed_parallelism') AS managed_parallelism,JSON_LENGTH(JSON_EXTRACT(sdk_state,'$.model_requests')) AS saved_top_request_count,JSON_LENGTH(JSON_EXTRACT(sdk_state,'$.error_diagnostic.model_requests')) AS saved_current_failure_request_count,JSON_EXTRACT(sdk_state,'$.transient_failure_count') AS transient_failure_count,JSON_EXTRACT(sdk_state,'$.transient_failure_streak') AS transient_failure_streak,JSON_EXTRACT(sdk_state,'$.transient_progress_token') AS transient_progress_token,JSON_EXTRACT(sdk_state,'$.route_health_failure_count') AS route_health_failure_count FROM agent_node_runs WHERE run_id=:run"),{'run':RUN_ID}).mappings()]
  for node in nodes:
   for key in ('budget','projection','managed_parallelism','transient_failure_count','transient_failure_streak','transient_progress_token','route_health_failure_count'):node[key]=decoded(node[key])
  nodes.sort(key=lambda r:r['id'])
  events=list(previous.get('events',[]));cursor=max((e['sequence'] for e in events),default=0)
  rows=db.execute(text("SELECT sequence,event_type,created_at,payload FROM agent_run_events WHERE run_id=:run AND sequence>:cursor AND event_type IN ('agent_action_started','agent_action_completed','agent_action_failed','managed_concurrency_adjusted')"),{'run':RUN_ID,'cursor':cursor}).mappings()
  for row in rows:events.append({**dict(row),'payload':decoded(row['payload'])})
  events.sort(key=lambda e:e['sequence'])
  sessions=[dict(r) for r in db.execute(text("SELECT c.agent_key,COUNT(*) AS validated_sessions FROM agent_validated_calls c JOIN agent_journal_branches b ON b.id=c.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=:run GROUP BY c.agent_key"),{'run':RUN_ID}).mappings()]
  supports=dict(previous.get('support_receipts',{}));rd=TargetedReader(db);catalog_cache={}
  new_calls=db.execute(text("SELECT c.id,c.record FROM agent_validated_calls c JOIN agent_journal_branches b ON b.id=c.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=:run AND c.agent_key='test_planning_route_gap_reviewer' AND c.id>:cursor"),{'run':RUN_ID,'cursor':max((int(x) for x in supports),default=0)}).mappings().all()
  for row in sorted(new_calls,key=lambda r:r['id']):supports[str(row['id'])]=verify_support(db,decoded(row['record']),rd,catalog_cache)
  coverage=dict(previous.get('source_fact_coverage',{}));packing=previous.get('generation_default_packing');repairs=dict(previous.get('draft_repair_arguments',{}))
  for e in events:
   payload=e['payload'];call=payload.get('tool_call_id');key=payload.get('tool_key')
   if key in {'govern_source','reconcile_generation_authority'} and e['event_type']=='agent_action_completed' and call not in coverage:coverage[call]=source_coverage(rd,receipt_record(db,call))
   if key=='revise_planning_draft' and call not in repairs:
    r=receipt_record(db,call);repairs[call]={'action_sequence':r['started_sequence'],'arguments':json.loads(rd.store.get(r['arguments_ref'])['arguments_json']),'started_at_local':str(e['created_at'])}
   if key=='accept_generation_plan' and e['event_type']=='agent_action_completed' and packing is None:
    r=receipt_record(db,call);result=rd.store.get(r['result_ref'])['output'];result=json.loads(result) if isinstance(result,str) else result;generation=result['generation'];batches=generation['batches'];packing={'action_sequence':r['started_sequence'],'result_ref':r['result_ref'],'layout_version':generation['layout_version'],'batch_count':len(batches),'total_case_budget':sum(b['case_budget'] for b in batches),'goal_case_budget':result['goal']['case_budget'],'manual_layout_actions_before_accept':sum(x['event_type']=='agent_action_started' and x['payload'].get('tool_key')=='revise_generation_layout' and x['sequence']<e['sequence'] for x in events)};assert packing['total_case_budget']==packing['goal_case_budget']
  assert not db.new and not db.dirty and not db.deleted;db.rollback()
 elapsed=((run['finished_at'] or datetime.now(timezone.utc).replace(tzinfo=None))-run['started_at']).total_seconds() if run['started_at'] else None
 return {'run_id':RUN_ID,'observed_at':datetime.now(timezone.utc).isoformat(),'run':run,'run_wall_seconds':elapsed,'human_wait_seconds_from_runtime':wait,'active_elapsed_seconds':elapsed-wait if elapsed is not None else None,'nodes':nodes,'events':events,'validated_sessions_by_agent':sessions,'support_receipts':supports,'support_decisions_per_session_max':max((v['decision_count'] for v in supports.values()),default=0),'source_fact_coverage':coverage,'draft_repair_arguments':repairs,'generation_default_packing':packing,'concurrency_adjustments':[e for e in events if e['event_type']=='managed_concurrency_adjusted'],'budget_scope':'Run63全新任务独立累计，不抵销Run59/60/61/62；来源17 raw只读重验不计新模型请求。','evidence_limit':'每60秒读取轻字段；只对首次出现的指定来源/专家输出叶子展开；validated_sessions不等于模型请求。local topic guard是否触发须实际SDK诊断，不能据tokens或改题猜测。','model_requests_sent_by_audit':0,'business_database_writes':0}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--watch-seconds',type=int,default=3600);args=parser.parse_args();out=DIR/'cost-progress.json';previous=json.loads(out.read_text(encoding='utf8')) if out.exists() else {};end=time.monotonic()+args.watch_seconds
 while True:
  value=capture(previous);tmp=out.with_suffix('.json.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf8');tmp.replace(out);previous=value
  last=value['nodes'][-1] if value['nodes'] else {};print(json.dumps({'run_id':RUN_ID,'status':value['run']['status'],'usage':value['run']['usage'],'active_seconds':value['active_elapsed_seconds'],'action':(last.get('projection') or {}).get('current_action'),'retry_windows':[{key:node.get(key) for key in ('id','attempt','status','transient_failure_count','transient_failure_streak','transient_progress_token','route_health_failure_count')} for node in value['nodes']],'sessions':value['validated_sessions_by_agent'],'source_fact_coverage':value['source_fact_coverage'],'packing':value['generation_default_packing']},ensure_ascii=False),flush=True)
  if value['run']['status'] in {'succeeded','failed','cancelled','waiting_approval'} or time.monotonic()>=end:break
  time.sleep(60)
if __name__=='__main__':main()
