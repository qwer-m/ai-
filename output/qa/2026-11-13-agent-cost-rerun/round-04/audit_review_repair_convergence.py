"""Run63终审27之后的实际审修范围和差异轨迹，只读取指定叶子与验收输出。"""
from pathlib import Path
from collections import defaultdict
from types import SimpleNamespace
import runpy,json
ns=runpy.run_path(str(Path(__file__).with_name('capture_cost_progress.py')));SessionLocal,text,decoded,Reader=ns['SessionLocal'],ns['text'],ns['decoded'],ns['TargetedReader'];hashvalue=ns['action_state_hash'];receipt=ns['receipt_record']
from modules.agent_platform.test_generation_review import prepare_final_review_rechecks,_difference_identity
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));rd=Reader(db);events=[decoded(r) for r in db.execute(text("SELECT payload FROM agent_run_events WHERE run_id=63 AND event_type='agent_action_started'")).scalars()];headers=[receipt(db,e['tool_call_id']) for e in events if e.get('started_sequence',0)>=27]
 calls=[{**dict(r),'record':decoded(r['record'])} for r in db.execute(text("SELECT c.id,c.agent_key,c.record FROM agent_validated_calls c JOIN agent_journal_branches b ON b.id=c.branch_id JOIN agent_node_runs n ON n.id=b.owner_node_id WHERE n.run_id=63 AND c.agent_key IN ('test_generation_final_reviewer','test_generation_global_reviewer','test_generation_batch_repairer')")).mappings()];bycall=defaultdict(list)
 for c in calls:bycall[c['record']['parent_tool_call_id']].append(c)
 gen_cache={}
 def generation(sha):
  h=rd.value_hash(sha,['review','state','generation'])
  if h not in gen_cache:gen_cache[h]=rd.subtree(sha,['review','state','generation'])
  return gen_cache[h]
 def percase(gen):
  bindings={x['case_id']:x for x in gen['case_fact_bindings']}
  return {x['case_id']:hashvalue({'case':x,'binding':bindings[x['case_id']]}) for x in gen['test_cases']}
 first=headers[0]['pre_state_hash'];policy=rd.subtree(first,['conflict_confirmations']);timeline=[];verdicts=[]
 for r in sorted(headers,key=lambda r:r['started_sequence']):
  if r['tool_key'] not in {'repair_review_batch','recheck_review_batch','review_all_cases'}:continue
  pre=r['pre_state_hash'];post=r.get('post_state_hash');row={'action_sequence':r['started_sequence'],'tool':r['tool_key'],'status':r['status'],'pre_state_hash':pre,'post_state_hash':post,'source_fact_catalog_hash':rd.subtree(pre,['review','state','goal','fact_catalog_sha256']),'immutable_review_input_hash':rd.subtree(pre,['review','state','input_sha256'])}
  cs=bycall.get(r['tool_call_id'],[]);row['actual_expert_calls']=[{'id':c['id'],'agent':c['agent_key'],'input_hash':c['record']['identity']['input_hash'],'output_hash':c['record']['output_hash']} for c in cs]
  pre_records=rd.subtree(pre,['review','state','review_records']);row['before_differences']=[{'review_record_index':rec['item_index'],'difference_key':list(_difference_identity(d)),'difference':d} for rec in pre_records for d in rec['output']['differences']]
  if r['tool_key']=='repair_review_batch' and r['status']=='completed':
   a,b=percase(generation(pre)),percase(generation(post));assert set(a)==set(b)
   changed=sorted(i for i in a if a[i]!=b[i]);oldpending=rd.subtree(pre,['review','state','pending_rechecks']);pending=rd.subtree(post,['review','state','pending_rechecks']);newkeys=set(pending)-set(oldpending);allowed={case for key in newkeys for scope in pending[key]['scopes'] for case in scope['review_batch']['case_ids']};assert set(changed)<=allowed
   row.update(changed_case_ids=changed,authorized_case_ids=sorted(allowed),only_authorized_cases_changed=True,changed_case_hashes={i:{'before':a[i],'after':b[i]} for i in changed})
  if r['tool_key']=='recheck_review_batch':
   pending=rd.subtree(pre,['review','state','pending_rechecks']);prepared=[]
   for value in pending.values():prepared.extend(prepare_final_review_rechecks(SimpleNamespace(artifacts={}),{'repair_inputs':value['scopes'],'generation':generation(pre)})['items'])
   mapping={hashvalue({**item,'_user_confirmations':policy} if policy else item):item for item in prepared}
   for c in cs:
    h=c['record']['identity']['input_hash'];assert h in mapping;item=mapping[h];out=rd.store.get(c['record']['output_ref']);assert hashvalue(out)==c['record']['output_hash'];cases=item['review_batch']['case_ids'];verdicts.append({'action_sequence':r['started_sequence'],'layer':'batch_recheck','case_ids':cases,'input_hash':h,'case_scope_hash':hashvalue({'test_cases':item['test_cases'],'case_fact_bindings':item['case_fact_bindings']}),'approved':out['approved'],'summary':out['summary'],'differences':[{'difference_key':list(_difference_identity(d)),**d} for d in out['differences']]})
  elif r['tool_key']=='review_all_cases':
   for c in cs:
    out=rd.store.get(c['record']['output_ref']);assert hashvalue(out)==c['record']['output_hash'];verdicts.append({'action_sequence':r['started_sequence'],'layer':'global','case_ids':sorted(percase(generation(pre))),'input_hash':c['record']['identity']['input_hash'],'approved':out['approved'],'summary':out['summary'],'differences':[{'difference_key':list(_difference_identity(d)),**d} for d in out['differences']]})
  if r['status']=='completed' and post:
   post_records=rd.subtree(post,['review','state','review_records']);row['after_differences']=[{'review_record_index':rec['item_index'],'difference_key':list(_difference_identity(d)),'difference':d} for rec in post_records for d in rec['output']['differences']]
  timeline.append(row)
 groups=defaultdict(list)
 for v in verdicts:
  for d in v['differences']:groups[tuple(d['difference_key'])].append({'action_sequence':v['action_sequence'],'layer':v['layer'],'review_input_hash':v['input_hash'],'difference':d})
 report={'run_id':63,'timeline':timeline,'actual_verdicts':verdicts,'repeated_exact_difference_keys':[{'difference_key':list(k),'occurrences':v} for k,v in groups.items() if len(v)>1],'all_repairs_only_changed_authorized_cases':all(r.get('only_authorized_cases_changed',True) for r in timeline),'unique_source_fact_catalog_hashes':sorted({r['source_fact_catalog_hash'] for r in timeline}),'case_versions_loaded_once':len(gen_cache),'model_requests_sent':0,'business_database_writes':0};assert not db.new and not db.dirty and not db.deleted;db.rollback()
Path(__file__).with_name('review-repair-convergence-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'timeline':[{'sequence':r['action_sequence'],'tool':r['tool'],'status':r['status'],'changed':r.get('changed_case_ids'),'before_diff':len(r['before_differences']),'after_diff':len(r.get('after_differences',[]))} for r in timeline],'verdicts':verdicts,'repeated_keys':report['repeated_exact_difference_keys'],'scope_safe':report['all_repairs_only_changed_authorized_cases']},ensure_ascii=False))
