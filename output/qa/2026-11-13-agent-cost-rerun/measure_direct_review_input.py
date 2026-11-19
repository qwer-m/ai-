"""用真实70候选当前协议的完整评审输入测字节/准入上界；不请求模型。"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json,runpy,sys
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'backend'))
ns=runpy.run_path(str(ROOT/'backend/tests/agent_platform/test_single_agent_direct_protocol_real_data.py'))
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.runtime import _agent_input_token_upper_bound
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from modules.agent_platform.autonomous_budget import estimate_json_tokens
from modules.agent_platform.action_journal import action_state_hash
real=ns['real'].__wrapped__();resource=ns['persisted'].__wrapped__(real);db,state=next(resource)
def size(value):return len(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8'))
try:
 payload=generation._review_input(state);reviewer=next(spec for spec in BUILTIN_AGENT_SPECS if spec['agent_key']=='test_generation_reviewer')
 definition=SimpleNamespace(**reviewer);no_images={**payload,'image_pages':[]}
 images=sum(1+sum(block.get('type')=='image' for block in page.get('blocks',[])) for page in payload['image_pages'])
 report={'scope':'真实Run63候选按当前协议CPU投影；不是新模型产出或在线评审',
 'fixture_data_sha256':real['data_sha256'],'model_requests_sent':0,'business_database_writes':0,
 'case_count':len(payload['cases']),'coverage_item_count':len(payload['coverage_plan']['items']),
 'source_evidence_count':len(payload['evidence_catalog']['items']),'approved_decision_count':len(payload['approved_decisions']),
 'referenced_image_page_count':len(payload['image_pages']),'actual_full_page_and_crop_image_count':images,
 'payload_utf8_bytes':size(payload),'without_image_descriptors_payload_utf8_bytes':size(no_images),
 'field_utf8_bytes':{key:size(value) for key,value in payload.items()},
 'json_token_estimate':estimate_json_tokens(payload),
 'runtime_input_token_upper_bound_with_actual_images':_agent_input_token_upper_bound(definition=definition,tools=[],input_payload=payload),
 'runtime_input_token_upper_bound_without_images':_agent_input_token_upper_bound(definition=definition,tools=[],input_payload=no_images),
 'review_input_sha256':action_state_hash(payload),
 'limits':['引用来自真实旧候选全量绑定，表格segments逐段保留，未为缩预算截断；不代表正常新模型必要引用数量。',
  'runtime函数按UTF8字节形成保守上界，非供应商计量token；视觉预留仍计入上界。']}
 (Path(__file__).with_name('direct-review-input-capacity.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(report,ensure_ascii=False))
finally:
 try:next(resource)
 except StopIteration:pass
