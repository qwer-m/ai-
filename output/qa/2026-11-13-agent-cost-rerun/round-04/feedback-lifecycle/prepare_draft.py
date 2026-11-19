"""生成隔离草稿；绝不写入生产文件。"""

import difflib
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
PATH = ROOT / "backend/modules/agent_platform/test_generation_agent.py"
original = PATH.read_text(encoding="utf-8")
helpers = '''
def _generation_feedback_source_hash(state):
    """反馈只对同一已治理来源生效；规划和生成位置不构成来源身份。"""
    if state["planning"] is None or state["effective_facts"] is None:
        return None
    return action_state_hash({
        "source_ref": state["planning"]["goal"]["source_ref"],
        "source_semantics": state["planning"]["source_semantics"],
        "source_authority": state["planning"]["source_authority"],
        "effective_facts": state["effective_facts"],
        "confirmations": state["conflict_confirmations"],
    })


def _generation_feedback_draft_targets(state):
    """沿真实草案绑定完整原子意图；位置仅供当前执行，不参与稳定身份。"""
    catalog = {}
    for index, (item, record) in enumerate(zip(
            state["planning"]["planning_inputs"], state["planning"]["planning_records"], strict=True)):
        if record is None:
            continue
        input_hash = action_state_hash(item)
        if record["input_hash"] != input_hash:
            raise ValueError("生成反馈的草案来源与当前完整输入不一致")
        for candidate_index, candidate in enumerate(record["output"]["module_candidates"]):
            for topic_index, topic in enumerate(candidate["coverage_topics"]):
                intent = planning.coverage_topic_intent(topic)
                identity = {
                    "planning_input_hash": input_hash,
                    "module_candidate_name": candidate["name"],
                    "candidate_fact_ids": sorted(candidate["fact_ids"]),
                    "actors": deepcopy(candidate["actors"]),
                    "lifecycle": deepcopy(candidate["lifecycle"]),
                    "coverage_intent": intent,
                }
                target = {"identity": identity,
                          "planning_batch_index": index,
                          "module_candidate_index": candidate_index,
                          "coverage_topic_index": topic_index}
                owners = catalog.setdefault(intent, {})
                # 汇总器对同一候选内的同文原子意图保留首次主题，复用相同归属。
                owners.setdefault(action_state_hash(identity), target)
    return {intent: list(owners.values()) for intent, owners in catalog.items()}


def _record_generation_feedback(state, item, normalized, generation_index):
    """按报告内的原子issue登记，不以整批问题的共同存亡代替局部修复。"""
    designs = {design["test_design_item_id"]: design for design in item["plan"]["test_design_items"]}
    targets = _generation_feedback_draft_targets(state)
    source_hash = _generation_feedback_source_hash(state)
    for issue in normalized["planning_feedback"]["issues"]:
        payload = deepcopy(normalized)
        payload["planning_feedback"]["issues"] = [deepcopy(issue)]
        fact_ids = sorted({identity for group in issue["scenario_groups"] for identity in group["fact_ids"]})
        provenance = None
        if issue["requested_change"] == "replan":
            intents = sorted({designs[identity]["coverage_intent"]
                              for group in issue["scenario_groups"] for identity in group["test_design_item_ids"]})
            if source_hash is None or any(len(targets.get(intent, [])) != 1 for intent in intents):
                raise ValueError("生成范围问题缺少唯一的当前来源及原子草案归属")
            provenance = {
                "source_hash": source_hash,
                "draft_targets": [deepcopy(targets[intent][0]["identity"]) for intent in intents],
            }
        _problem(state, origin="generation", input_hash=action_state_hash(item), payload=payload,
                 fact_ids=fact_ids, batch_indexes=[generation_index], generation_provenance=provenance)


def _project_generation_feedback(state, problem, targets, source_hash):
    """目标改变只使旧结论退役；保留的报告仍是待评估主张，不构成业务裁决。"""
    provenance = problem["generation_provenance"]
    if provenance["source_hash"] != source_hash:
        return None
    current_targets = []
    for previous in provenance["draft_targets"]:
        current = targets.get(previous["coverage_intent"], [])
        if len(current) != 1 or current[0]["identity"] != previous:
            return None
        current_targets.append(deepcopy(current[0]))
    intents = {target["coverage_intent"] for target in provenance["draft_targets"]}
    matched, accepted = [], set()
    for index, (item, record) in enumerate(zip(state["generation_inputs"], state["generation_records"], strict=True)):
        covered = {design["coverage_intent"] for design in item["plan"]["test_design_items"]} & intents
        if not covered:
            continue
        matched.append(index)
        if record is not None:
            if record["input_hash"] != action_state_hash(item):
                raise ValueError("生成反馈的候选验收记录与当前完整输入不一致")
            accepted.update(covered)
    if intents <= accepted:
        return None
    result = deepcopy(problem)
    result["generation_indexes"] = matched
    result["current_draft_origins"] = current_targets
    return result

'''
assert original.count('def _project_current_problems(state):') == 1
draft = original.replace('def _project_current_problems(state):', helpers + '\ndef _project_current_problems(state):')
draft = draft.replace('batch_indexes, review_provenance=None):', 'batch_indexes, review_provenance=None, generation_provenance=None):')
assert draft != original
needle = '        **({"review_provenance": deepcopy(review_provenance)} if review_provenance is not None else {}),'
assert draft.count(needle) == 1
draft = draft.replace(needle, needle + '\n        **({"generation_provenance": deepcopy(generation_provenance)} if generation_provenance is not None else {}),')
start = draft.index('    state["problems"] = {key: value for key, value in state["problems"].items()')
end = draft.index('    if state["review"] is None:', start)
draft = draft[:start] + '''    retained = {}
    feedback = [value for value in state["problems"].values() if "generation_provenance" in value]
    targets = _generation_feedback_draft_targets(state) if feedback else {}
    source_hash = _generation_feedback_source_hash(state) if feedback else None
    for key, value in state["problems"].items():
        if "generation_provenance" in value:
            projected = _project_generation_feedback(state, value, targets, source_hash)
            if projected is not None:
                retained[key] = projected
        elif ((value["origin"] == "generation" and value["input_hash"] in generation_hashes)
              or (value["origin"] == "preparation" and state["accepted_plan"] is not None
                  and state["effective_facts"] is not None and not state["generation_inputs"]
                  and value["input_hash"] == _preparation_identity(state))):
            retained[key] = value
    state["problems"] = retained
''' + draft[end:]
needle = '''            _problem(state, origin="generation", input_hash=action_state_hash(item), payload=normalized,
                     fact_ids=[fact["fact_id"] for fact in item["authoritative_facts"]], batch_indexes=[index])'''
assert draft.count(needle) == 1
draft = draft.replace(needle, '            _record_generation_feedback(state, item, normalized, index)')
target = BASE / "draft_test_generation_agent.py"
target.write_text(draft, encoding="utf-8")
patch = ''.join(difflib.unified_diff(original.splitlines(True), draft.splitlines(True),
                                   fromfile='a/backend/modules/agent_platform/test_generation_agent.py',
                                   tofile='b/backend/modules/agent_platform/test_generation_agent.py'))
(BASE / "generation-feedback-lifecycle.draft.patch").write_text(patch, encoding="utf-8")
(BASE / "draft-source-manifest.json").write_text(json.dumps({
    "production_file": str(PATH), "production_sha256": hashlib.sha256(PATH.read_bytes()).hexdigest(),
    "draft_file": str(target), "production_edits": 0,
    "scope": "生命周期最小草稿；不含原子修订授权与新旧反馈去重的后续闭环，不可直接应用。",
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"draft": str(target), "patch": str(BASE / 'generation-feedback-lifecycle.draft.patch'),
                  "production_edits": 0}, ensure_ascii=False))
