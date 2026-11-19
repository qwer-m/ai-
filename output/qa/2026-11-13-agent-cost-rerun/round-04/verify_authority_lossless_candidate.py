"""仅CPU验证容量修复候选，不改生产协议，不表示整体成本目标已达成。"""
from copy import deepcopy
from pathlib import Path
import gzip,json,sys,time
ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT/'backend'))
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import estimate_json_tokens
from modules.agent_platform.test_generation_semantics import restore_authority_source_review
BASE=Path(__file__).parent
fixture=json.loads(gzip.decompress((BASE/'authority-payload-real.json.gz').read_bytes()))
report={'production_code_modified':False,'model_requests_sent':0,'business_database_writes':0,
 'status':'仅真实输入CPU可逆表示验证；未发布新协议，未宣称生产容量缺陷修复', 'phases':{}}
for phase in ('initial','reinspection'):
 original=fixture[phase]['original_model_payload'];before=action_state_hash(original)
 started=time.perf_counter();view=original['source_review'];facts=view['authoritative_facts']
 columns=sorted(set.intersection(*(set(fact) for fact in facts)))
 candidate=deepcopy(original)
 candidate['source_review']={**{key:deepcopy(value) for key,value in view.items() if key!='authoritative_facts'},
     'fact_columns':columns,'fact_rows':[[deepcopy(fact[key]) for key in columns]+
     [{key:deepcopy(value) for key,value in fact.items() if key not in columns}] for fact in facts]}
 decoded=deepcopy(candidate)
 wire=decoded['source_review'];restored=[]
 for row in wire.pop('fact_rows'):
  assert len(row)==len(columns)+1
  assert set(row[-1]).isdisjoint(columns)
  restored.append({**dict(zip(columns,row[:-1],strict=True)),**row[-1]})
 wire.pop('fact_columns');wire['authoritative_facts']=restored
 assert decoded==original and action_state_hash(decoded)==before
 assert restore_authority_source_review(wire)==restore_authority_source_review(view)
 assert action_state_hash(original)==before
 report['phases'][phase]={'fact_count':len(facts),'shared_source_record_count':len(view['source_records']),
   'all_model_fields_roundtrip_exact':True,'facts_and_all_governance_roundtrip_exact':True,
   'migration_delta_exact':wire.get('source_delta')==view.get('source_delta'),
   'user_confirmations_exact':decoded.get('_user_confirmations')==original.get('_user_confirmations'),
   'original_payload_sha256':before,'restored_payload_sha256':action_state_hash(decoded),
   'source_input_unchanged':True,'before_estimated_tokens':estimate_json_tokens(original),
   'candidate_estimated_tokens':estimate_json_tokens(candidate),'cpu_seconds':time.perf_counter()-started}
(BASE/'authority-lossless-candidate-cpu-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(report,ensure_ascii=False))
