"""真实Run63来源+候选的CPU当前协议投影；不是模型生成/独立评审成功。"""
import ast,gzip,json,sys,hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'backend'))
from sqlalchemy import text
from core.db.database import SessionLocal
from core.db.model_defs import AgentRun,AgentArtifactBlob
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.checkpoint_storage import ArtifactStore,content_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_agent as generation

def decoded(value):return json.loads(value) if isinstance(value,str) else value
audit=ROOT/'output/qa/2026-11-12-single-agent-live/round-10/_audit_planning_readonly.py'
tree=ast.parse(audit.read_text(encoding='utf8'));reader=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='TargetedReader')
namespace={'AgentArtifactBlob':AgentArtifactBlob,'ArtifactStore':ArtifactStore,'content_hash':content_hash,'decoded':decoded,'action_state_hash':action_state_hash}
exec(compile(ast.Module(body=[reader],type_ignores=[]),str(audit),'exec'),namespace)
Reader=namespace['TargetedReader']
BASE=Path(__file__).parent;ROUND=BASE/'round-04'
audit_result=json.loads((ROUND/'review-repair-convergence-audit.json').read_text(encoding='utf8'))
repair=next(item for item in audit_result['timeline'] if item['action_sequence']==37)
with SessionLocal(autoflush=False) as db:
 db.execute(text('SET TRANSACTION READ ONLY'));rd=Reader(db);run=db.get(AgentRun,63)
 original_input=deepcopy(run.input_payload)
 allowed={'requirement','requirement_doc_id','case_budget','batch_case_limit','disable_result_cache'}
 current_input={key:deepcopy(value) for key,value in original_input.items() if key in allowed}
 context=ToolExecutionContext(db=db,user_id=run.user_id,project_id=run.project_id,run_id=run.id,node_key='agent_network',
     run_input=current_input,action_input=current_input,artifacts={})
 initial=deepcopy(generation.initialize_generation_state(context,current_input))
 before=rd.subtree(repair['pre_state_hash'],['review','state','generation']);after=rd.subtree(repair['post_state_hash'],['review','state','generation'])
 source_facts=rd.subtree(repair['pre_state_hash'],['effective_facts','authoritative_facts'])
 by_fact={fact['fact_id']:fact for fact in source_facts}
 def fact_ids(binding):
  values=set(binding['test_input_fact_ids'])
  for item in binding['precondition_bindings']:values.update(item['fact_ids'])
  for item in binding['step_bindings']:values.update(item['action_fact_ids']);values.update(item['expected_fact_ids'])
  return values
 def project(generated):
  bindings={binding['case_id']:binding for binding in generated['case_fact_bindings']};result=[];unmapped={}
  for case in generated['test_cases']:
   refs=[];missing=[]
   for identity in sorted(fact_ids(bindings[case['case_id']])):
    fact=by_fact[identity];anchor=fact['source_anchor']
    # 只机械读取既有锚点原文：表格锚点全部真实segments保留，不用摘要替代。
    quotes=([anchor['quote']] if anchor.get('quote') else [])+[segment['text'] for segment in anchor.get('source_segments',[])]
    text_refs=[]
    for quote in quotes:
     found=next((item for item in initial['evidence_catalog']['items'] if item['kind']=='text'
       and item['page_number']==anchor.get('page_number') and generation._presentation(quote)
       and generation._presentation(quote) in generation._presentation(item['text'])),None)
     if found is not None:text_refs.append({'evidence_id':found['evidence_id'],'quote':quote})
    if not text_refs:
     found=next((item for item in initial['evidence_catalog']['items'] if item['kind']=='image'
       and item['page_number']==anchor.get('page_number') and anchor.get('block_id') in item['block_ids']),None)
     if found is None:missing.append(identity);continue
     text_refs=[{'evidence_id':found['evidence_id'],'quote':''}]
    for ref in text_refs:
     if ref not in refs:refs.append(ref)
   if not refs:
    print(json.dumps({'unmapped_case':case['case_id'],'facts':[by_fact[i] for i in sorted(fact_ids(bindings[case['case_id']]))]},ensure_ascii=False))
    raise AssertionError(f"真实候选没有可机械映射的原文引用:{case['case_id']}")
   coverage=f"COV-{len(result)+1:04d}"
   result.append({'historical_case_id':case['case_id'],'content':{
    **{key:deepcopy(case[key]) for key in ('title','module','priority','preconditions','test_input','steps','tags')},
    'coverage_ids':[coverage],'source_refs':refs}})
   if missing:unmapped[case['case_id']]=missing
  return result,unmapped
 before_projected,before_missing=project(before);after_projected,after_missing=project(after)
 changes=[old['historical_case_id'] for old,new in zip(before_projected,after_projected,strict=True) if old!=new]
 assert changes==['TC-050'] and len(before_projected)==len(after_projected)==70
 # 原图只作CPU原始资产读取/哈希验证，不声称模型已经看图或作出裁决。
 generation.read_source(context,{'evidence_id':None,'offset':0,'max_chars':60000})
 image_ids={ref['evidence_id'] for case in [*before_projected,*after_projected] for ref in case['content']['source_refs'] if not ref['quote']}
 image_pages={item['page_number'] for item in initial['evidence_catalog']['items'] if item['evidence_id'] in image_ids}
 for number in sorted(image_pages):generation.view_source_image(context,{'page_number':number})
 read_state=deepcopy(context.action_state)
 metadata={key:deepcopy(getattr(run,key)) for key in ('id','user_id','project_id','workflow_definition_id','status','created_at','started_at','finished_at')}
 metadata={key:value.isoformat() if isinstance(value,datetime) else value for key,value in metadata.items()}
 fixture={'provenance':{'run_id':63,'historical_status':run.status,'historical_delivered':False,
  'before_state_hash':repair['pre_state_hash'],'after_state_hash':repair['post_state_hash'],
  'original_cases_sha256':action_state_hash(before),'original_patch_cases_sha256':action_state_hash(after),
  'cpu_projection_only':True,'new_model_review_performed':False,'model_requests_sent':0,'business_database_writes':0,
  'unmapped_original_fact_bindings_before':before_missing,'unmapped_original_fact_bindings_after':after_missing,
  'limits':'只验证当前引用/状态/版本协议；旧候选原文不改，COV/TC重新分配属于新协议元数据；不主张业务正确、全部旧绑定完整映射或当前模型评审成功。'},
  'run_metadata':metadata,'original_run_input':original_input,'current_input':current_input,
  'current_initial_state':initial,'initial_state_sha256':action_state_hash(initial),
  'current_read_state':read_state,'read_state_sha256':action_state_hash(read_state),
  'frozen_run_context':{'artifacts':{'requirement_source':deepcopy(initial['source_ref'])}},
  'historical_generation_before':before,'historical_generation_after':after,
  'current_cases_before':before_projected,'current_cases_after':after_projected,
  'historical_review_findings':audit_result['timeline'][0]['before_differences'],
  'historical_review_verdicts':audit_result['actual_verdicts']}
 assert not db.new and not db.dirty and not db.deleted;db.rollback()
fixture['data_sha256']=action_state_hash(fixture)
target=ROOT/'backend/tests/agent_platform/fixtures/run63_direct_protocol_cpu_real.json.gz'
target.write_bytes(gzip.compress(json.dumps(fixture,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8'),mtime=0))
summary={'path':str(target),'bytes':target.stat().st_size,'data_sha256':fixture['data_sha256'],
 'source_initial_sha256':fixture['initial_state_sha256'],'cases':len(before_projected),'actual_changed_case_ids':changes,
 'evidence_items':len(initial['evidence_catalog']['items']),'approved_decisions':len(initial['approved_decisions']),
 'unmapped_original_fact_bindings_case_count':len(before_missing),'cpu_projection_only':True,'model_requests_sent':0,'business_database_writes':0}
(BASE/'direct-protocol-fixture-provenance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(summary,ensure_ascii=False))
