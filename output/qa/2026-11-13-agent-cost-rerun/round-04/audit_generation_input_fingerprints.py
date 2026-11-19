import runpy,json
from pathlib import Path
from collections import Counter
ns=runpy.run_path('output/qa/2026-11-13-agent-cost-rerun/round-04/capture_cost_progress.py');db=ns['SessionLocal']();T=ns['text'];db.execute(T('SET TRANSACTION READ ONLY'));rd=ns['TargetedReader'](db);r15=ns['receipt_record'](db,'call_a21737a010fd4cf1a25d13051cbf58e8');r25=ns['receipt_record'](db,'call_e4fbc4406fc34ba58ff2a6983c13ea28');oldsha,newsha=r15['pre_state_hash'],r25['pre_state_hash']
def fields(entry):
 if 'value'in entry:return {k:{'value':v} for k,v in entry['value'].items()}
 payload=rd.blob(entry['ref'])
 if payload['type']=='inline':return {k:{'value':v} for k,v in payload['value'].items()}
 if payload['type']=='object':return dict(payload['entries'])
 if payload['type']=='object_chunks':
  out={}
  for part in payload['entries']:out.update(fields(part))
  return out
 raise TypeError(payload['type'])
def h(entry):return entry['ref']['sha256'] if 'ref'in entry else ns['action_state_hash'](entry['value'])
oldentry=rd.entry_at(oldsha,['generation_inputs']);newentry=rd.entry_at(newsha,['generation_inputs']);oldcount=rd.array_length(oldentry['ref']) if 'ref'in oldentry else len(oldentry['value']);newcount=rd.array_length(newentry['ref']) if 'ref'in newentry else len(newentry['value']);rows=[];counts=Counter()
for i in range(min(oldcount,newcount)):
 a,b=fields(rd.entry_at(oldsha,['generation_inputs',i])),fields(rd.entry_at(newsha,['generation_inputs',i]));different=[k for k in set(a)|set(b) if k not in a or k not in b or h(a[k])!=h(b[k])];counts.update(different);row={'index':i,'old_batch_id':rd.subtree(oldsha,['generation_inputs',i,'batch','batch_id']),'new_batch_id':rd.subtree(newsha,['generation_inputs',i,'batch','batch_id']),'old_input_hash':rd.value_hash(oldsha,['generation_inputs',i]),'new_input_hash':rd.value_hash(newsha,['generation_inputs',i]),'changed_top_fields':sorted(different),'changed_subfields':{}}
 for key in different:
  if key not in a or key not in b:continue
  try:
   aa,bb=fields(a[key]),fields(b[key]);row['changed_subfields'][key]=sorted(k for k in set(aa)|set(bb) if k not in aa or k not in bb or h(aa[k])!=h(bb[k]))
  except (TypeError,AttributeError):pass
 rows.append(row)
cache_entry=rd.entry_at(newsha,['generation_cache']);cache=fields(cache_entry);report={'run_id':63,'first_gen_pre':oldsha,'second_gen_pre':newsha,'old_batch_count':oldcount,'new_batch_count':newcount,'exact_input_same_count':sum(r['old_input_hash']==r['new_input_hash'] for r in rows),'changed_top_field_counts':dict(counts),'second_generation_cache_entries':len(cache),'old_input_hashes_still_in_current_cache':sum(r['old_input_hash'] in cache for r in rows),'new_input_hashes_found_in_current_cache':sum(r['new_input_hash'] in cache for r in rows),'batches':rows,'model_requests_sent':0,'business_database_writes':0};assert not db.new and not db.dirty and not db.deleted;db.rollback();db.close();Path('output/qa/2026-11-13-agent-cost-rerun/round-04/generation-input-fingerprint-diff.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({k:v for k,v in report.items() if k!='batches'},ensure_ascii=False));print(json.dumps(rows[:3],ensure_ascii=False))
