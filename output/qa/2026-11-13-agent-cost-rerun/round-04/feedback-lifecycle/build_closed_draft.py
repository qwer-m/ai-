"""封闭反馈生命周期的隔离补丁；所有目标均位于QA树。"""

import difflib
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
TREE = BASE / "closed-tree"
files = {}


def load(name):
    path = ROOT / f"backend/modules/agent_platform/{name}.py"
    text = path.read_text(encoding="utf-8")
    files[name] = {"path": path, "before": text, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return text


def once(text, old, new):
    assert text.count(old) == 1, old[:150]
    return text.replace(old, new)


escalation = load("test_generation_escalation")
escalation_helpers = '''
def generation_replan_problem_context(problem: dict[str, Any]) -> dict[str, Any]:
    """平台原子问题的完整语义身份，原报告编号和批次位置只保留为历史出处。"""
    report = problem["payload"]["planning_feedback"]
    if len(report["issues"]) != 1 or report["issues"][0]["requested_change"] != "replan":
        raise ValueError("原子重规划问题必须只包含一个replan issue")
    provenance = problem["generation_provenance"]
    if set(provenance) != {"source_hash", "draft_targets", "reported_design_intents"}:
        raise ValueError("原子生成问题的来源协议不完整")
    if (not isinstance(provenance["source_hash"], str) or len(provenance["source_hash"]) != 64
            or any(value not in "0123456789abcdef" for value in provenance["source_hash"])):
        raise ValueError("原子生成问题缺少完整来源摘要")
    issue = deepcopy(report["issues"][0])
    referenced = {identity for group in issue["scenario_groups"] for identity in group["test_design_item_ids"]}
    if set(provenance["reported_design_intents"]) != referenced:
        raise ValueError("原子生成问题的真实设计映射缺失或超出原报告")
    groups = []
    for group in issue.pop("scenario_groups"):
        groups.append({"fact_ids": sorted(group["fact_ids"]), "condition": deepcopy(group["condition"]),
            "coverage_intents": sorted({provenance["reported_design_intents"][identity]
                                         for identity in group["test_design_item_ids"]})})
    issue["scenario_groups"] = sorted(groups, key=action_state_hash)
    targets = sorted(deepcopy(provenance["draft_targets"]), key=action_state_hash)
    if (not targets or len(targets) != len({target["coverage_intent"] for target in targets})
            or {target["coverage_intent"] for target in targets}
            != {intent for group in groups for intent in group["coverage_intents"]}):
        raise ValueError("原子问题的真实设计引用与草案归属不一致")
    return {"source_hash": provenance["source_hash"], "issue": issue, "draft_targets": targets}


def generation_replan_review_context(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一原子问题只复核一次；问法、旧TD编号和生成布局不能制造新身份。"""
    result = {}
    for problem in problems:
        payload = problem.get("payload", {})
        if (problem.get("origin") != "generation" or payload.get("result_type") != "planning_feedback"
                or not any(issue["requested_change"] == "replan" for issue in payload["planning_feedback"]["issues"])):
            continue
        context = generation_replan_problem_context(problem)
        identity = action_state_hash({"origin": "generation_replan", **context})
        if identity != problem["problem_id"]:
            raise ValueError("原子生成问题的稳定身份与完整上下文不一致")
        result[identity] = {"problem_id": identity, **context}
    return [result[identity] for identity in sorted(result)]

'''
escalation = once(escalation, 'def report_generation_scope_problem(', escalation_helpers + '\ndef report_generation_scope_problem(')

generation = load("test_generation_agent")
generation = (BASE / "draft_test_generation_agent.py").read_text(encoding="utf-8")
generation = once(generation, 'from .test_generation_escalation import validate_generation_scope_feedback',
    'from .test_generation_escalation import (validate_generation_scope_feedback,\n    generation_replan_problem_context, generation_replan_review_context)')
start = generation.index('    identity = action_state_hash([origin, input_hash, payload])', generation.index('def _problem('))
end = generation.index('\n\ndef _current_problems', start)
generation = generation[:start] + '''    problem = {
        "origin": origin, "input_hash": input_hash,
        "fact_ids": sorted(set(fact_ids)), "generation_indexes": sorted(set(batch_indexes)),
        "payload": deepcopy(payload),
        **({"review_provenance": deepcopy(review_provenance)} if review_provenance is not None else {}),
        **({"generation_provenance": deepcopy(generation_provenance)} if generation_provenance is not None else {}),
    }
    identity = (action_state_hash({"origin": "generation_replan", **generation_replan_problem_context(problem)})
                if generation_provenance is not None else action_state_hash([origin, input_hash, payload]))
    problem["problem_id"] = identity
    if generation_provenance is not None:
        # 同源同一原子主张的新报告替代当前出处，原始专家回执仍完整保留。
        state["problems"][identity] = problem
    else:
        state["problems"].setdefault(identity, problem)
''' + generation[end:]
generation = once(generation, '                "source_hash": source_hash,\n                "draft_targets":',
    '                "source_hash": source_hash,\n                "reported_design_intents": {identity: designs[identity]["coverage_intent"]\n                    for group in issue["scenario_groups"] for identity in group["test_design_item_ids"]},\n                "draft_targets":')
start = generation.index('    matched, accepted = [], set()', generation.index('def _project_generation_feedback'))
end = generation.index('    result = deepcopy(problem)', start)
generation = generation[:start] + '''    if _generation_feedback_independently_supported(state, problem):
        return None
    matched = [index for index, item in enumerate(state["generation_inputs"])
               if intents & {design["coverage_intent"] for design in item["plan"]["test_design_items"]}]
''' + generation[end:]
needle = '\ndef _project_generation_feedback(state, problem, targets, source_hash):'
independent = '''
def _generation_replan_problems(state):
    return [problem for problem in _current_problems(state) if "generation_provenance" in problem]


def _generation_feedback_review_decisions(state, problem):
    """回读包含完整原子报告的独立裁决，不把普通支持结论冒充本问题复核。"""
    expected = generation_replan_review_context([problem])[0]
    intents = {target["coverage_intent"] for target in problem["generation_provenance"]["draft_targets"]}
    decisions = {}
    for record in state["planning"]["route_review_revisions"]:
        item = record["input"]
        contexts = generation_replan_review_context(item.get("_planning_feedback", {}).get("downstream_problems", []))
        if expected not in contexts:
            continue
        if record["input_hash"] != action_state_hash(item):
            raise ValueError("原子生成问题的独立复核完整输入身份无效")
        normalized = planning.postprocess_planning_support_decisions(None, {
            "item_input": item, "item_output": record["output"],
        })
        designs = {design["test_design_item_index"]: design for design in item["missing_test_design_items"]}
        for decision in normalized["decisions"]:
            intent = designs[decision["test_design_item_index"]]["coverage_intent"]
            if intent in intents:
                decisions[intent] = {"review_input_hash": record["input_hash"],
                    "decision": {key: deepcopy(value) for key, value in decision.items()
                                 if key != "test_design_item_index"}}
    return decisions


def _generation_feedback_independently_supported(state, problem):
    """仅独立复核能结案；生成器成功不能替代裁决。"""
    decisions = _generation_feedback_review_decisions(state, problem)
    return all(decisions.get(target["coverage_intent"], {}).get("decision", {}).get("disposition") == "supported"
               for target in problem["generation_provenance"]["draft_targets"])


def _generation_independent_support_reviews(state, item):
    """把真实新裁决传入对应生成请求，独立复核后不要求模型改写问法才能重试。"""
    source_hash = _generation_feedback_source_hash(state)
    targets = _generation_feedback_draft_targets(state)
    intents = {design["coverage_intent"] for design in item["plan"]["test_design_items"]}
    problems = {}
    for record in state["planning"]["route_review_revisions"]:
        for problem in record["input"].get("_planning_feedback", {}).get("downstream_problems", []):
            if "generation_provenance" in problem:
                problems[problem["problem_id"]] = problem
    proofs = []
    for problem in problems.values():
        provenance = problem["generation_provenance"]
        if provenance["source_hash"] != source_hash or any(
                len(targets.get(target["coverage_intent"], [])) != 1
                or targets[target["coverage_intent"]][0]["identity"] != target
                for target in provenance["draft_targets"]):
            continue
        if not intents.intersection(target["coverage_intent"] for target in provenance["draft_targets"]):
            continue
        if _generation_feedback_independently_supported(state, problem):
            proofs.append({"problem_id": problem["problem_id"],
                "issue_context": generation_replan_problem_context(problem),
                "decisions": _generation_feedback_review_decisions(state, problem),
                "evidence_role": "独立支持复核记录不是新增事实；测试用例仍须依据当前生效事实验收。"})
    return sorted(proofs, key=lambda proof: proof["problem_id"])


def _generation_feedback_support_selections(state):
    """未评估报告沿当前完整意图目录定位，不能把历史位置当成本轮选择。"""
    intents = {target["coverage_intent"] for problem in _generation_replan_problems(state)
               for target in problem["generation_provenance"]["draft_targets"]}
    return [{"module_index": item["module_index"], "test_design_item_indexes": indexes}
            for item in state["planning"]["gap_inputs"]
            if (indexes := [design["test_design_item_index"] for design in item["missing_test_design_items"]
                            if design["coverage_intent"] in intents])]

'''
generation = once(generation, needle, independent + needle)
generation = once(generation, '    provenance = problem["generation_provenance"]\n    if provenance["source_hash"] != source_hash:',
    '    generation_replan_review_context([problem])\n    provenance = problem["generation_provenance"]\n    if provenance["source_hash"] != source_hash:')
needle = '    retained = {}\n    feedback = [value for value in state["problems"].values() if "generation_provenance" in value]'
generation = once(generation, needle,
    '    generation_replan_review_context(list(state["problems"].values()))\n' + needle)
# 复核选择、详情与草案授权均使用同一当前原子归属。
generation = once(generation, '    for problem in problems:\n        for gap in problem["payload"]["design_support_gaps"]:',
    '    for selection in _generation_feedback_support_selections(state):\n        unresolved.setdefault(selection["module_index"], set()).update(selection["test_design_item_indexes"])\n    for problem in problems:\n        for gap in problem["payload"]["design_support_gaps"]:')
generation = once(generation, '    selections = [{"module_index": index, "test_design_item_indexes": sorted(indexes)}',
    '    problems = [*problems, *_generation_replan_problems(state)]\n    selections = [{"module_index": index, "test_design_item_indexes": sorted(indexes)}')
generation = once(generation, '                                 if problem["problem_id"] in structural_problem_ids for identity in problem["fact_ids"]}',
    '                                 if problem["problem_id"] in structural_problem_ids and "generation_provenance" not in problem\n                                 for identity in problem["fact_ids"]}')
needle = '            actions["revise_planning_draft"] = {\n                "item_indexes": pending_draft_indexes or affected_draft_indexes, "problem_ids": structural_problem_ids,\n            }'
generation = once(generation, needle, '''            affected_draft_indexes = sorted(set(affected_draft_indexes) | {
                target["planning_batch_index"] for problem in _generation_replan_problems(state)
                for target in problem["current_draft_origins"]})
            actions["revise_planning_draft"] = {
                "item_indexes": sorted(set(pending_draft_indexes) | set(affected_draft_indexes)),
                "problem_ids": structural_problem_ids,
            }''')
generation = once(generation, '        draft_indexes = [index for index, item in enumerate(state["planning"]["planning_inputs"])\n                         if fact_ids & {fact["fact_id"] for scope in item["planning_scopes"] for fact in scope["facts"]}]',
    '        draft_indexes = (sorted({target["planning_batch_index"] for target in problem["current_draft_origins"]})\n                         if "generation_provenance" in problem else\n                         [index for index, item in enumerate(state["planning"]["planning_inputs"])\n                          if fact_ids & {fact["fact_id"] for scope in item["planning_scopes"] for fact in scope["facts"]}])')
generation = once(generation, '        if (plan["can_finish"] and state["accepted_plan"] is None',
    '        if (plan["can_finish"] and not _generation_replan_problems(state) and state["accepted_plan"] is None')
generation = once(generation, '                    item["origin"] in {"generation", "review"} and item["problem_id"] in structural_problem_ids',
    '                    item["origin"] in {"generation", "review"} and "generation_provenance" not in item\n                    and item["problem_id"] in structural_problem_ids')
generation = once(generation, '    if reviewed and reviewed["can_finish"] and state["accepted_plan"] is not None:',
    '    if reviewed and reviewed["can_finish"] and state["accepted_plan"] is not None and not _generation_replan_problems(state):')
generation = once(generation, '    if state["accepted_plan"] is None or state["effective_facts"] is None or _pending(state["generation_records"]):',
    '    if _generation_replan_problems(state):\n        raise OutputValidationError("仍有生成范围问题尚未完成含原子报告的独立支持复核")\n    if state["accepted_plan"] is None or state["effective_facts"] is None or _pending(state["generation_records"]):')
generation = once(generation, '        request = _generation_request(item, guidance)',
    '''        request = _generation_request(item, guidance)
        independent = _generation_independent_support_reviews(state, item)
        if independent:
            request.setdefault("_planning_feedback", {})["independent_support_reviews"] = independent''')
generation = once(generation, '            "planning_versions": dict(zip(version_keys, after, strict=True)), "downstream_invalidated": before != after}',
    '''            "planning_versions": dict(zip(version_keys, after, strict=True)), "downstream_invalidated": before != after,
            "independently_reviewed_problem_ids": [problem["problem_id"] for problem in _generation_replan_problems(current)
                if action_key == "review_planning_route_gaps"
                and _generation_feedback_source_hash(state) == problem["generation_provenance"]["source_hash"]
                and _generation_feedback_independently_supported(state, problem)]}''')

planning_agent = load("test_generation_planning_agent")
helper = '''
def _downstream_draft_feedback(state, index):
    """下游问题只授权其真实原子主题，不把共同引用事实扩展成整批可改。"""
    from .test_generation_escalation import generation_replan_review_context

    problems = state.get("downstream_feedback", [])
    generation_replan_review_context(problems)
    designs = {}
    for problem in problems:
        if "generation_provenance" not in problem:
            continue
        for target in problem["current_draft_origins"]:
            if target["planning_batch_index"] != index:
                continue
            record = state["planning_records"][index]
            candidate = record["output"]["module_candidates"][target["module_candidate_index"]]
            topic = candidate["coverage_topics"][target["coverage_topic_index"]]
            identity = target["identity"]
            if (identity["planning_input_hash"] != action_state_hash(state["planning_inputs"][index])
                    or identity["module_candidate_name"] != candidate["name"]
                    or identity["coverage_intent"] != coverage_topic_intent(topic)):
                raise ValueError("下游修订授权不是当前完整草案的原子归属")
            designs[identity["coverage_intent"]] = {"coverage_intent": identity["coverage_intent"]}
    return [{"missing_test_design_items": [designs[key] for key in sorted(designs)]}] if designs else []


def _draft_revision_feedback(state, index):
    return [*_draft_route_feedback(state, index), *_downstream_draft_feedback(state, index)]

'''
planning_agent = once(planning_agent, '\ndef _pending_draft_revisions(', '\n' + helper + '\ndef _pending_draft_revisions(')
# 三个保护调用与可见反馈使用同一授权投影；既有route_gaps仍保留真实route语义。
planning_agent = once(planning_agent, '    item["_planning_feedback"]["protected_topics"] = _protected_draft_topics(previous["output"], item["_planning_feedback"]["route_gaps"]) if previous else []',
    '    item["_planning_feedback"]["protected_topics"] = _protected_draft_topics(previous["output"], _draft_revision_feedback(state, index)) if previous else []')
planning_agent = once(planning_agent, '    item["_planning_feedback"]["protected_candidates"] = _protected_draft_candidates(previous["output"], item["_planning_feedback"]["route_gaps"]) if previous else []',
    '    item["_planning_feedback"]["protected_candidates"] = _protected_draft_candidates(previous["output"], _draft_revision_feedback(state, index)) if previous else []')
planning_agent = once(planning_agent, '        protected = _protected_draft_topics(previous["output"], _draft_route_feedback(state, index)) if previous else []',
    '        protected = _protected_draft_topics(previous["output"], _draft_revision_feedback(state, index)) if previous else []')
planning_agent = once(planning_agent, '        candidates = _protected_draft_candidates(previous["output"], _draft_route_feedback(state, index)) if previous else []',
    '        candidates = _protected_draft_candidates(previous["output"], _draft_revision_feedback(state, index)) if previous else []')
planning_agent = once(planning_agent,
    '        affected_coverage_items=[design["coverage_intent"] for gap in route_gaps for design in gap["missing_test_design_items"]],\n        affected_fact_ids=[fact_id for problem in state.get("downstream_feedback", []) for fact_id in problem["fact_ids"]],',
    '''        affected_coverage_items=[*[design["coverage_intent"] for gap in route_gaps for design in gap["missing_test_design_items"]],
            *[target["coverage_intent"] for problem in state.get("downstream_feedback", [])
              if "generation_provenance" in problem for target in problem["generation_provenance"]["draft_targets"]]],
        affected_fact_ids=[fact_id for problem in state.get("downstream_feedback", [])
                          if "generation_provenance" not in problem for fact_id in problem["fact_ids"]],''')

planning = load("test_generation_planning")
needle = '        subject.pop("_planning_feedback", None)\n        identities.add(action_state_hash(subject))'
planning = once(planning, needle, '''        feedback = subject.pop("_planning_feedback", {})
        from .test_generation_escalation import generation_replan_review_context
        issues = [issue for issue in generation_replan_review_context(feedback.get("downstream_problems", []))
                  if any(target["coverage_intent"] == design["coverage_intent"]
                         for target in issue["draft_targets"])]
        if issues:
            subject["generation_issue_context"] = issues
        identities.add(action_state_hash(subject))''')

# 来源和原子归属协议归并到已有反馈领域文件，子规划与父生成共用同一校验。
start = generation.index('def _generation_feedback_source_hash(state):')
end = generation.index('def _record_generation_feedback(', start)
common = generation[start:end]
common = common.replace('def _generation_feedback_source_hash(state):',
    'def generation_feedback_source_hash(planning_state, effective_facts, confirmations):')
common = common.replace('def _generation_feedback_draft_targets(state):',
    'def generation_feedback_draft_targets(planning_state):')
common = common.replace('state["planning"]', 'planning_state').replace('state["effective_facts"]', 'effective_facts')
common = common.replace('state["conflict_confirmations"]', 'confirmations')
common = common.replace('    catalog = {}\n', '    from .test_generation_planning import coverage_topic_intent\n\n    catalog = {}\n')
common = common.replace('planning.coverage_topic_intent(topic)', 'coverage_topic_intent(topic)')
generation = generation[:start] + '''def _generation_feedback_source_hash(state):
    return generation_feedback_source_hash(state["planning"], state["effective_facts"], state["conflict_confirmations"])


def _generation_feedback_draft_targets(state):
    return generation_feedback_draft_targets(state["planning"])


''' + generation[end:]
generation = once(generation, '    generation_replan_problem_context, generation_replan_review_context)',
    '    generation_replan_problem_context, generation_replan_review_context,\n    generation_feedback_source_hash, generation_feedback_draft_targets)')
escalation = once(escalation, 'def generation_replan_problem_context(', common + '\ndef generation_replan_problem_context(')
validator = '''
def validate_generation_replan_feedback(context, problems):
    """复核的新问题必须来自平台当前完整来源和真实原子草案，不能自报身份刷新门禁。"""
    contexts = generation_replan_review_context(problems)
    if not contexts:
        return
    state = getattr(context, "action_state", None)
    if not isinstance(state, dict) or not isinstance(state.get("source_authority"), dict):
        raise ValueError("原子生成问题的复核缺少当前完整规划上下文")
    source_hash = generation_feedback_source_hash(state, state["source_authority"]["result"], context.confirmed_conflicts)
    targets = generation_feedback_draft_targets(state)
    for issue in contexts:
        if issue["source_hash"] != source_hash or any(
                len(targets.get(target["coverage_intent"], [])) != 1
                or targets[target["coverage_intent"]][0]["identity"] != target
                for target in issue["draft_targets"]):
            raise ValueError("生成问题不是当前来源及原子草案的真实问题上下文")

'''
escalation = once(escalation, 'def report_generation_scope_problem(', validator + '\ndef report_generation_scope_problem(')
planning_agent = once(planning_agent, 'def prepare_draft_revision(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:\n    state = _state(context)',
    '''def prepare_draft_revision(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    from .test_generation_escalation import validate_generation_replan_feedback
    validate_generation_replan_feedback(context, state.get("downstream_feedback", []))''')
planning_agent = once(planning_agent, 'def apply_draft_revision(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:\n    state = _check_action(context, action, outputs)',
    '''def apply_draft_revision(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    state = _check_action(context, action, outputs)
    from .test_generation_escalation import validate_generation_replan_feedback
    validate_generation_replan_feedback(context, state.get("downstream_feedback", []))''')
planning_agent = once(planning_agent, 'def prepare_route_gap_review(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:\n    state = _state(context)',
    '''def prepare_route_gap_review(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    from .test_generation_escalation import validate_generation_replan_feedback
    validate_generation_replan_feedback(context, state.get("downstream_feedback", []))''')
planning = once(planning, '    def accept(item, record):\n        input_hash = action_state_hash(item)',
    '''    def accept(item, record):
        from .test_generation_escalation import validate_generation_replan_feedback
        validate_generation_replan_feedback(context, item.get("_planning_feedback", {}).get("downstream_problems", []))
        input_hash = action_state_hash(item)''')

outputs = {"test_generation_agent": generation, "test_generation_planning_agent": planning_agent,
           "test_generation_planning": planning, "test_generation_escalation": escalation}
patches = []
for name, value in outputs.items():
    rel = f"backend/modules/agent_platform/{name}.py"
    target = TREE / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")
    compile(value, str(target), "exec")
    patches.append(''.join(difflib.unified_diff(files[name]["before"].splitlines(True), value.splitlines(True),
                                             fromfile='a/' + rel, tofile='b/' + rel)))
    assert hashlib.sha256(files[name]["path"].read_bytes()).hexdigest() == files[name]["sha256"]
(BASE / "closed-generation-feedback-lifecycle.draft.patch").write_text(''.join(patches), encoding="utf-8")
(BASE / "closed-draft-manifest.json").write_text(json.dumps({
    "production_edits": 0, "source_sha256": {name: record["sha256"] for name, record in files.items()},
    "qa_tree": str(TREE), "patch_file": str(BASE / "closed-generation-feedback-lifecycle.draft.patch"),
}, ensure_ascii=False, indent=2) + '\n', encoding="utf-8")
print(json.dumps({"files": len(outputs), "production_edits": 0, "qa_tree": str(TREE)}, ensure_ascii=False))
