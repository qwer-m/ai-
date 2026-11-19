"""只读重建Run70/71独审真实输入，逐项匹配已持久化的请求身份。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path('backend').resolve()))
from sqlalchemy import text
from core.db.database import SessionLocal
from modules.agent_platform.checkpoint_storage import ArtifactStore
from modules.agent_platform.action_journal import action_state_hash


OUT = Path('output/qa/2026-11-16-review-completeness')


def references(refs):
    return [{'evidence_id': identity} for identity in sorted({ref['evidence_id'] for ref in refs})]


def approved_view(item):
    question = item['question']
    return {**{key:deepcopy(item[key]) for key in (
        'evidence_id','kind','approval_id','origin_run_id','origin_node_id','original_issue_id',
        'decided_by_user_id','decided_at','approval_state_hash') if key in item},
        'question':{key:deepcopy(question[key]) for key in ('kind','reason','coverage_fragment') if key in question},
        'resolution':item['resolution']}


def actual_view(state, run_id):
    evidence=[]
    for item in state['evidence_catalog']['items']:
        if item['kind']=='approved_decision':
            evidence.append(approved_view(item))
        elif run_id==70:
            evidence.append(deepcopy(item))
        else:
            current={key:deepcopy(item[key]) for key in ('evidence_id','kind','page_number','text') if key in item}
            if item['kind']=='image':
                current.update({key:deepcopy(item[key]) for key in ('block_ids','image_region') if key in item})
            evidence.append(current)
    cases=[]
    for record in state['cases']['items'].values():
        item={'case_id':record['case_id'],'version':record['version'],**deepcopy(record['content'])}
        if run_id==70:
            item['content_hash']=record['content_hash']
        cases.append(item)
    issues=[{key:deepcopy(issue[key]) for key in (
        'issue_id','kind','target_case_ids','coverage_ids','reason','source_refs','status',
        'target_versions','origin','assessment_status')}
        for issue in sorted(state['open_issues'].values(),key=lambda value:value['issue_id'])
        if issue['status']!='resolved']
    # 两轮真实任务均没有调用看图，不能伪造图片输入。
    assert not state['image_pages']
    result={'source_ref':deepcopy(state['source_ref']),
        'evidence_catalog':{'document_id':state['evidence_catalog']['document_id'],'items':evidence},
        'approved_decision_ids':[item['evidence_id'] for item in state['approved_decisions']],
        'goal':deepcopy(state['goal']),'coverage_plan':deepcopy(state['coverage_plan']),
        'cases':cases,'open_issues':issues,'image_pages':[]}
    for item in (*result['cases'],*result['coverage_plan']['items'],*result['open_issues']):
        item['source_refs']=references(item['source_refs'])
    return result


def lite_case(item):
    return {key:deepcopy(value) for key,value in item.items() if key not in {'source_refs','content_hash'}} | {
        'source_refs':references(item.get('source_refs',[]))}


def main():
    OUT.mkdir(exist_ok=True,parents=True)
    evidence=[]
    with SessionLocal(autoflush=False) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        store=ArtifactStore(db,user_id=1,project_id=8)
        for run_id,attempt in [(70,'attempt-05'),(71,'attempt-06')]:
            saved=json.loads(Path(f'output/qa/2026-11-16-generation-acceptance/{attempt}/terminal-snapshot.json').read_text(encoding='utf8'))
            cp=list(saved['expanded_checkpoints'].values())[-1]
            receipts={key:row for key,row in cp['receipts'].items() if row['tool_key']=='request_review' and row['status']=='completed'}
            calls={call['parent_tool_call_id']:call for call in cp['validated_agent_calls'].values()}
            for receipt in sorted(receipts.values(),key=lambda row:row['started_sequence']):
                pre_state=store.get(store._ref(receipt['pre_state_hash']))
                assert action_state_hash(pre_state)==receipt['pre_state_hash']
                view=actual_view(pre_state,run_id)
                call=calls[receipt['tool_call_id']]
                assert action_state_hash(view)==call['identity']['input_hash'], (run_id,receipt['started_sequence'])
                by_id={item['evidence_id']:item for item in view['evidence_catalog']['items']}
                raw_by_id={item['evidence_id']:item for item in pre_state['evidence_catalog']['items']}
                for identity in ('EV-0004','EV-0016','EV-0023'):
                    assert by_id[identity]['text']==raw_by_id[identity]['text']
                cases_by_id={item['case_id']:item for item in view['cases']}
                row={'run_id':run_id,'action_sequence':receipt['started_sequence'],
                    'tool_call_id':receipt['tool_call_id'],'pre_state_hash':receipt['pre_state_hash'],
                    'actual_input_hash':call['identity']['input_hash'],'input_hash_verified':True,
                    'all_cases_present':len(cases_by_id)==70,
                    'source_text_hashes':{identity:hashlib.sha256(by_id[identity]['text'].encode('utf8')).hexdigest()
                                          for identity in ('EV-0004','EV-0016','EV-0023')},
                    'source_texts':{identity:by_id[identity]['text'] for identity in ('EV-0004','EV-0016','EV-0023')},
                    'target_cases':{identity:lite_case(cases_by_id[identity]) for identity in ('TC-0016','TC-0029')},
                    'source_EV4_goals':[item for item in view['coverage_plan']['items'] if any(
                        ref['evidence_id']=='EV-0004' for ref in item['source_refs'])],
                    'source_EV4_cases':[lite_case(item) for item in view['cases'] if any(
                        ref['evidence_id']=='EV-0004' for ref in item['source_refs'])],
                    'open_issue_ids':[item['issue_id'] for item in view['open_issues']],
                    'raw_result':deepcopy(call['raw_output'])}
                evidence.append(row)
            patch_ids=[]
            for receipt in cp['receipts'].values():
                if receipt['tool_key']=='patch_cases' and receipt['status']=='completed':
                    output=receipt['result']['output']
                    patch_ids.append(json.loads(output) if isinstance(output,str) else output)
            (OUT/f'run{run_id}-review-inputs.json').write_text(json.dumps({
                'scope':'以真实不可变pre-state重建输入，并匹配真实managed-call input_hash；不是新模型调用',
                'run_id':run_id,'reviews':[row for row in evidence if row['run_id']==run_id],
                'actual_patch_receipts':patch_ids},ensure_ascii=False,indent=2),encoding='utf8')
        db.rollback()
    result={'captured_at':datetime.now(timezone.utc).isoformat(),'database_writes':False,'model_requests_sent':0,
        'verified_review_inputs':len(evidence),'run71_source_present_in_both_reviews':True,
        'run71_case16_versions':[row['target_cases']['TC-0016']['version'] for row in evidence if row['run_id']==71],
        'run71_verdicts':[row['raw_result']['verdict'] for row in evidence if row['run_id']==71],
        'run70_verdicts':[row['raw_result']['verdict'] for row in evidence if row['run_id']==70]}
    (OUT/'audit-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
