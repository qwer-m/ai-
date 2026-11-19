"""仅QA：未完成审查的意图不授权草案重写；真实Root61投影验证，不写生产。"""
from pathlib import Path
from copy import deepcopy
from difflib import unified_diff
import asyncio,json,runpy,sys,hashlib,os
ROOT=Path.cwd();QA=ROOT/'output/qa/2026-11-13-agent-cost-rerun/run61-confirmation-audit';sys.path[:0]=[str(ROOT/'backend'),str(QA.parent/'cost-audit')]
if os.name=='nt':
 import ctypes
 ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
from prepare_read_path_draft import module_from_text
from modules.agent_platform import test_generation_agent as current
from modules.agent_platform import test_generation_planning_agent as current_plan
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
files=[ROOT/'backend/modules/agent_platform/test_generation_agent.py',ROOT/'backend/modules/agent_platform/test_generation_planning_agent.py'];before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files};gen_old=files[0].read_text(encoding='utf8');plan_old=files[1].read_text(encoding='utf8')
needle='''                index for entry in [*plan["route_gaps"], *plan["unrouted_modules"]]
                for index in entry["planning_batch_indexes"]})''';replacement='''                index for entry in [*[entry for entry in plan["route_gaps"] if entry["review"] is not None],
                                    *plan["unrouted_modules"]]
                for index in entry["planning_batch_indexes"]})''';assert gen_old.count(needle)==1;gen_new=gen_old.replace(needle,replacement)
needle='''    for item in feedback:
        designs = [design for design in item["missing_test_design_items"] if index in design["planning_batch_indexes"]]''';replacement='''    for item in feedback:
        # 尚未审查只表示待复核，不能给已建立的主题发放改写许可。
        if item["review"] is None:
            continue
        designs = [design for design in item["missing_test_design_items"] if index in design["planning_batch_indexes"]]''';assert plan_old.count(needle)==1;plan_new=plan_old.replace(needle,replacement)
patch=''.join(unified_diff(gen_old.splitlines(True),gen_new.splitlines(True),fromfile='a/backend/modules/agent_platform/test_generation_agent.py',tofile='b/backend/modules/agent_platform/test_generation_agent.py'))+''.join(unified_diff(plan_old.splitlines(True),plan_new.splitlines(True),fromfile='a/backend/modules/agent_platform/test_generation_planning_agent.py',tofile='b/backend/modules/agent_platform/test_generation_planning_agent.py'));(QA/'unreviewed-draft-authorization.draft.patch').write_text(patch,encoding='utf8');new_plan=module_from_text('modules.agent_platform._qa_pending_scope_planning',files[1],plan_new);new_gen=module_from_text('modules.agent_platform._qa_pending_scope_generation',files[0],gen_new);new_gen.planning=new_plan
ns=runpy.run_path(str(ROOT/'backend/tests/agent_platform/test_draft_revision_boundaries_real_data.py'));real=ns['real'].__wrapped__();state=ns['actual_root_state'].__wrapped__(real);db_gen=ns['db'].__wrapped__(real);db=next(db_gen);case=ns['case_for'](real,24)
def context():
 return ToolExecutionContext(db=db,user_id=1,project_id=8,run_id=61,node_key='agent_network',run_input=deepcopy(real['input_payload']),action_input=deepcopy(real['input_payload']),action_state=deepcopy(state),artifacts=deepcopy(case['prepare_apply_context']['artifacts']),confirmed_conflicts=deepcopy(case['prepare_apply_context']['confirmed_conflicts']))
old_context=context();old_state,old_scope=current._require_action(old_context,'revise_planning_draft');new_context=context();new_state,new_scope=new_gen._require_action(new_context,'revise_planning_draft');assert new_scope['item_indexes']==[21];removed=sorted(set(old_scope['item_indexes'])-set(new_scope['item_indexes']));assert removed and 0 in removed;assert new_state==old_state==state;index=removed[0]
old_feedback=current_plan._draft_route_feedback(state['planning'],index);new_feedback=new_plan._draft_route_feedback(state['planning'],index);assert old_feedback and any(entry['review'] is None for entry in old_feedback);assert new_feedback==[];args={**case['arguments'],'item_index':index};before_state=action_state_hash(new_context.action_state)
try:asyncio.run(new_gen._planning_action(new_context,args,'revise_planning_draft',new_plan.revise_planning_draft))
except OutputValidationError as error:assert error.details['item_indexes']==[21]
else:raise AssertionError('未审草案仍可以进入修订child')
assert action_state_hash(new_context.action_state)==before_state and new_context.invoke_agent is None and not new_context.tool_calls
legit=new_plan._draft_route_feedback(state['planning'],21);assert legit and all(entry['review'] is not None for entry in legit)
pending=ns['case_for'](real,22)['after']['pending_inference_state'];assert new_plan._pending_draft_revisions(pending)==current_plan._pending_draft_revisions(pending);assert new_plan._draft_route_feedback(pending,21)
assert not db.new and not db.dirty and not db.deleted
try:next(db_gen)
except StopIteration:pass
assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
report={'passed':True,'source_run':61,'root_state_sha256':case['pre_root_sha256'],'model_requests_sent':0,'business_database_writes':0,'production_files_modified':0,'current_allowed_draft_indexes':old_scope['item_indexes'],'draft_allowed_draft_indexes':new_scope['item_indexes'],'removed_unreviewed_indexes':removed,'known_gap_21_preserved':True,'other_pending_after_action22_preserved':True,'root_child_entered':False,'note':'仅隔离QA模块验证。Run62生产继续使用原冻结版本，不因本草稿中止或热更新。'};(QA/'unreviewed-draft-authorization-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))
