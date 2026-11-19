"""真实Run63当前协议CPU回归；新问题上下文的独立复核仍待真实模型验收。"""

from copy import deepcopy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
sys.path.insert(0, str(ROOT / "backend"))
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError


def load(name):
    full = "modules.agent_platform." + name
    spec = importlib.util.spec_from_file_location(full, BASE / f"closed-tree/backend/modules/agent_platform/{name}.py")
    value = importlib.util.module_from_spec(spec)
    sys.modules[full] = value
    spec.loader.exec_module(value)
    package = sys.modules["modules.agent_platform"]
    setattr(package, name, value)
    return value


escalation = load("test_generation_escalation")
planning = load("test_generation_planning")
planner = load("test_generation_planning_agent")
generation = load("test_generation_agent")
from modules.agent_platform.test_generation_semantics import planning_support_model_payload
from modules.agent_platform.model_input_projection import project_input_value


@pytest.fixture(scope="module")
def real():
    raw = gzip.decompress((BASE / "run63_generation_feedback_closed_real_v2.json.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == "f013762eb4141c5b559a975e46fa603352960e0aed1761b60a95ac56c64ef74b"
    value = json.loads(raw)
    bindings = value["source_bindings"]
    assert all(binding == bindings["before18"] for binding in bindings.values())
    assert action_state_hash(value["shared_planning"]["source_semantics"]) == bindings["before18"]["source_semantics_hash"]
    assert action_state_hash(value["shared_planning"]["source_authority"]) == bindings["before18"]["source_authority_hash"]
    assert action_state_hash(value["original"]["effective_facts"]) == bindings["before18"]["effective_facts_hash"]
    assert action_state_hash(value["original"]["conflict_confirmations"]) == bindings["before18"]["confirmation_hash"]
    return value


def state_for(real, name="before18"):
    frame = real["snapshots"][name]
    state = {key: deepcopy(real["original"][key]) for key in (
        "accepted_plan", "effective_facts", "conflict_confirmations",
    )}
    state["planning"] = deepcopy(real["shared_planning"])
    for key in ("planning_records", "plan", "plan_input_hash", "gap_inputs", "gap_records", "route_inputs",
                "route_records", "route_review_revisions", "last_route_feedback", "dependency_records"):
        state["planning"][key] = deepcopy(frame[key])
    for key in ("generation_inputs", "generation_records"):
        state[key] = deepcopy(frame[key])
    state["accepted_plan"] = deepcopy(frame["accepted_plan"])
    state.update(problems={}, review=None)
    return state


def record(real, state):
    item = real["original_generation_input"]
    normalized = escalation.validate_generation_scope_feedback(item, real["original_problem"]["payload"])
    assert normalized == real["original_problem"]["payload"]
    generation._record_generation_feedback(state, item, normalized, real["original_generation_index"])
    generation._project_current_problems(state)
    return deepcopy(state["problems"])


def auto_problem(problems):
    # 仅测试从原始真实条件中选择样本，不将业务编号写入生产规则。
    return next(problem for problem in problems.values() if "DOC287-P0013-f6" in problem["fact_ids"])


def derived_current_review_selection(state):
    unresolved = []
    for index, item in enumerate(state["planning"]["gap_inputs"]):
        current = planner._current_support_review(state["planning"], index)
        indexes = [decision["test_design_item_index"] for decision in (current or {}).get("decisions", [])
                   if decision["disposition"] == "unsupported"]
        if indexes:
            unresolved.append({"module_index": item["module_index"], "test_design_item_indexes": indexes})
    return {"unresolved_design_selections": unresolved,
            "pending_gap_indexes": [index for index, value in enumerate(state["planning"]["gap_records"]) if value is None]}


def cpu_review_candidate_from_real_supported_output(real, state, problem, *, partial=False):
    """真实既有supported输出用作CPU边界候选；不伪造新模型收据或宣称reviewer已看到issue。"""
    state["problems"] = {problem["problem_id"]: deepcopy(problem)}
    selection = generation._generation_feedback_support_selections(state)
    assert len(selection) == 1
    if partial:
        selection[0]["test_design_item_indexes"] = selection[0]["test_design_item_indexes"][:1]
    ctx = planner._isolated_context(SimpleNamespace(artifacts={}, action_state=state["planning"],
        confirmed_conflicts=state["conflict_confirmations"]))
    item = planning.prepare_planning_route_repairs(ctx, {
        "plan": state["planning"]["plan"], "prepared_items": state["planning"]["route_inputs"],
        "route_records": state["planning"]["route_records"], "design_selections": selection,
    })["items"][0]
    previous = planning.current_planning_support_decisions(ctx, {
        "plan": state["planning"]["plan"], "prepared_items": state["planning"]["route_inputs"],
        "route_records": state["planning"]["route_records"], "repair_records": state["planning"]["gap_records"],
        "review_revisions": state["planning"]["route_review_revisions"],
    })
    indexes = selection[0]["test_design_item_indexes"]
    original = [deepcopy(previous[item["module_index"]][index]) for index in indexes]
    item["_planning_feedback"] = {"question": problem["payload"]["planning_feedback"]["issues"][0]["explanation"],
        "previous_decisions": original, "downstream_problems": [deepcopy(problem)]}
    # 原有postprocessor不接收派生intent_id/review_input字段；仅选出真实模型原有字段。
    output = {"module_index": item["module_index"], "decisions": [
        {key: deepcopy(value) for key, value in decision.items() if key in (
            "test_design_item_index", "disposition", "fact_ids", "support_gaps", "reason")}
        for decision in original]}
    assert all(decision["disposition"] == "supported" for decision in output["decisions"])
    normalized = planning.postprocess_planning_support_decisions(None, {"item_input": item, "item_output": output})
    candidate = {"item_index": len(state["planning"]["route_review_revisions"]),
                 "input": item, "input_hash": action_state_hash(item), "output": normalized}
    return candidate


def test_real_atomic_feedback_preserves_only_unchanged_issue_across_invalidations(real):
    original = state_for(real)
    problems = record(real, original)
    assert len(problems) == 2
    for name in ("after18", "after21", "after22"):
        state = state_for(real, name)
        state["problems"] = deepcopy(problems)
        generation._project_current_problems(state)
        assert list(state["problems"]) == [auto_problem(problems)["problem_id"]]
        assert not state["generation_inputs"]


def test_source_and_original_atomic_design_mapping_match_existing_formal_origins(real):
    state = state_for(real)
    record(real, state)
    ctx = SimpleNamespace(artifacts={}, action_state=state["planning"],
        action_input=real["original"]["prepared_input"], confirmed_conflicts=state["conflict_confirmations"])
    designs = {design["test_design_item_id"]: design for design in real["original_generation_input"]["plan"]["test_design_items"]}
    for problem in state["problems"].values():
        for group in problem["payload"]["planning_feedback"]["issues"][0]["scenario_groups"]:
            for identifier in group["test_design_item_ids"]:
                origin = planner.generation_preflight_draft_origins(ctx, state["planning"], state["accepted_plan"], identifier,
                    effective_facts=state["effective_facts"]["effective_facts"])
                target = next(target for target in problem["current_draft_origins"]
                              if target["identity"]["coverage_intent"] == designs[identifier]["coverage_intent"])
                for key in ("planning_batch_index", "module_candidate_index", "coverage_topic_index"):
                    assert target[key] == origin["draft_origins"][0][key]


def test_old_supported_review_without_issue_context_cannot_retire_problem(real):
    state = state_for(real)
    problems = record(real, state)
    assert not generation._generation_feedback_independently_supported(state, auto_problem(problems))
    assert len(state["problems"]) == 2


def test_replan_is_available_for_current_directed_review_with_complete_issue_context(real):
    state = state_for(real)
    problems = record(real, state)
    scope = generation._support_review_scope(state, derived_current_review_selection(state))
    selected = {(entry["module_index"], index) for entry in scope["unresolved_design_selections"]
                for index in entry["test_design_item_indexes"]}
    expected = {(entry["module_index"], design["test_design_item_index"])
                for entry in state["planning"]["gap_inputs"] for design in entry["missing_test_design_items"]
                if any(design["coverage_intent"] == target["coverage_intent"] for problem in problems.values()
                       for target in problem["generation_provenance"]["draft_targets"])}
    assert selected == expected and len(selected) == 4
    assert set(scope["problem_ids"]) == set(problems)
    review = cpu_review_candidate_from_real_supported_output(real, state, auto_problem(problems))
    actual = planner._support_review_inputs(SimpleNamespace(action_input=real["original"]["prepared_input"]), [review["input"]])[0]
    projected = planning_support_model_payload(actual)
    assert projected["_planning_feedback"]["downstream_problems"] == [auto_problem(problems)]


def test_same_source_same_issue_new_report_replaces_instead_of_duplicates(real):
    state = state_for(real)
    first = record(real, state)
    # 完整真实生成输入仅做无业务变化的字典恢复顺序调整，不编造新内容。
    item = dict(reversed(list(real["original_generation_input"].items())))
    report = escalation.validate_generation_scope_feedback(item, real["original_problem"]["payload"])
    generation._record_generation_feedback(state, item, report, real["original_generation_index"])
    generation._project_current_problems(state)
    assert set(state["problems"]) == set(first) and len(state["problems"]) == 2


def test_real_successful_generation25_does_not_replace_independent_issue_judgment(real):
    state = state_for(real)
    problems = record(real, state)
    current = state_for(real, "after25")
    assert all(record is not None for record in current["generation_records"])
    current["problems"] = deepcopy(problems)
    generation._project_current_problems(current)
    assert list(current["problems"]) == [auto_problem(problems)["problem_id"]]
    assert not generation._generation_feedback_independently_supported(current, auto_problem(problems))


def test_original_real_atomic_report_revalidates_under_real_new_input_with_same_stable_identity(real):
    initial = state_for(real)
    problems = record(real, initial)
    old = auto_problem(problems)
    current = state_for(real, "after25")
    current["problems"] = {old["problem_id"]: deepcopy(old)}
    intents = {target["coverage_intent"] for target in old["generation_provenance"]["draft_targets"]}
    index, item = next((index, item) for index, item in enumerate(current["generation_inputs"])
                      if intents <= {design["coverage_intent"] for design in item["plan"]["test_design_items"]})
    assert action_state_hash(item) != old["input_hash"]
    # 去掉平台历史信封，原真实issue逐字不改；仅验证该旧候选在真实新输入上的当前协议。
    raw_report = {"result_type": "planning_feedback", "planning_feedback": {
        "issues": deepcopy(old["payload"]["planning_feedback"]["issues"])}}
    current_report = escalation.validate_generation_scope_feedback(item, raw_report)
    generation._record_generation_feedback(current, item, current_report, index)
    generation._project_current_problems(current)
    assert list(current["problems"]) == [old["problem_id"]]
    assert current["problems"][old["problem_id"]]["input_hash"] == action_state_hash(item)


def test_source_identity_loss_retires_old_claim_without_business_resolution(real):
    state = state_for(real)
    record(real, state)
    state["effective_facts"] = None  # 仅移除已验证元数据，表示治理已失效。
    generation._project_current_problems(state)
    assert not state["problems"]


def test_review_issue_requires_actual_current_planning_context(real):
    state = state_for(real)
    problems = record(real, state)
    with pytest.raises(ValueError, match="当前完整规划上下文"):
        escalation.validate_generation_replan_feedback(SimpleNamespace(artifacts={}), list(problems.values()))
    context = SimpleNamespace(action_state=state["planning"], confirmed_conflicts=state["conflict_confirmations"])
    escalation.validate_generation_replan_feedback(context, list(problems.values()))
    changed = state_for(real, "after18")
    context.action_state = changed["planning"]
    with pytest.raises(ValueError, match="不是当前来源及原子草案"):
        escalation.validate_generation_replan_feedback(context, list(problems.values()))


@pytest.mark.parametrize("missing", ["generation_provenance", "reported_design_intents", "input_hash"])
def test_missing_current_protocol_or_draft_identity_is_rejected(real, missing):
    state = state_for(real)
    record(real, state)
    problem = auto_problem(state["problems"])
    if missing == "generation_provenance":
        problem.pop(missing)
    elif missing == "reported_design_intents":
        problem["generation_provenance"].pop(missing)
    else:
        state["planning"]["planning_records"][23].pop(missing)
    with pytest.raises((ValueError, KeyError)):
        generation._project_current_problems(state)


def test_current_problem_authorizes_only_its_exact_draft_topics(real):
    state = state_for(real)
    problems = record(real, state)
    selected = auto_problem(problems)
    ps = state["planning"]
    ps["downstream_feedback"] = [deepcopy(selected)]
    selected_index = max(target["planning_batch_index"] for target in selected["current_draft_origins"])
    feedback = planner._draft_revision_feedback(ps, selected_index)
    previous = ps["planning_records"][selected_index]["output"]
    protected = planner._protected_draft_topics(previous, feedback)
    editable = {target["identity"]["coverage_intent"] for target in selected["current_draft_origins"]
                if target["planning_batch_index"] == selected_index}
    assert editable
    assert len(protected) == sum(len(c["coverage_topics"]) for c in previous["module_candidates"]) - len(editable)
    # 移除真实未授权主题，仅破坏结构，不生成业务事实。
    broken = deepcopy(previous)
    victim = protected[0]
    candidate = next(c for c in broken["module_candidates"] if c["name"] == victim["module_candidate_name"])
    candidate["coverage_topics"].remove(victim["coverage_topic"])
    with pytest.raises(OutputValidationError):
        planner._validate_protected_draft_topics(broken, protected)


def test_original_real_revision18_is_allowed_only_for_selected_answer_issue(real):
    state = state_for(real)
    problems = record(real, state)
    selected = next(problem for problem in problems.values() if problem is not auto_problem(problems))
    state["planning"]["downstream_feedback"] = [deepcopy(selected)]
    previous = state["planning"]["planning_records"][22]["output"]
    actual = real["snapshots"]["after18"]["planning_records"][22]["output"]
    feedback = planner._draft_revision_feedback(state["planning"], 22)
    planner._validate_protected_draft_topics(actual, planner._protected_draft_topics(previous, feedback))
    planner._validate_protected_draft_candidates(actual, planner._protected_draft_candidates(previous, feedback))


def test_issue_context_changes_review_gate_but_wording_and_positions_do_not(real):
    state = state_for(real)
    problems = record(real, state)
    candidate = cpu_review_candidate_from_real_supported_output(real, state, auto_problem(problems))
    item = candidate["input"]
    new = planning.planning_support_design_hashes(item)
    old = deepcopy(item)
    old["_planning_feedback"]["downstream_problems"] = []
    assert not (new & planning.planning_support_design_hashes(old))
    changed_question = deepcopy(item)
    changed_question["_planning_feedback"]["question"] = real["receipts"]["18"]["tool_key"]
    assert new == planning.planning_support_design_hashes(changed_question)
    unrelated_problem = deepcopy(item)
    unrelated_problem["_planning_feedback"]["downstream_problems"] = list(deepcopy(problems).values())
    assert new == planning.planning_support_design_hashes(unrelated_problem)


def test_reordered_draft_positions_rebind_without_losing_atomic_report(real):
    state = state_for(real)
    problems = record(real, state)
    state = state_for(real, "after22")
    state["problems"] = deepcopy(problems)
    state["planning"]["planning_inputs"].reverse()
    state["planning"]["planning_records"].reverse()
    generation._project_current_problems(state)
    assert list(state["problems"]) == [auto_problem(problems)["problem_id"]]
    assert next(iter(state["problems"].values()))["generation_provenance"] == auto_problem(problems)["generation_provenance"]


def test_cpu_partial_real_supported_candidate_cannot_close_whole_issue(real):
    state = state_for(real)
    problems = record(real, state)
    problem = auto_problem(problems)
    candidate = cpu_review_candidate_from_real_supported_output(real, state, problem, partial=True)
    state["planning"]["route_review_revisions"].append(candidate)
    generation._project_current_problems(state)
    assert problem["problem_id"] in state["problems"]


def test_cpu_complete_real_supported_candidate_closes_only_independently_reviewed_issue(real):
    state = state_for(real)
    problems = record(real, state)
    problem = auto_problem(problems)
    candidate = cpu_review_candidate_from_real_supported_output(real, state, problem)
    state["planning"]["route_review_revisions"].append(candidate)
    generation._project_current_problems(state)
    assert problem["problem_id"] not in state["problems"]
    # 此测试验证机械边界，不将组合后的输入/旧候选称为真实新模型复核。


def test_cpu_real_candidate_uses_existing_dependency_store_refresh_and_domain_validation(real):
    from modules.agent_platform.planning_dependencies import PlanningDependencies

    state = state_for(real)
    problems = record(real, state)
    problem = auto_problem(problems)
    candidate = cpu_review_candidate_from_real_supported_output(real, state, problem)
    context = SimpleNamespace(action_input=real["original"]["prepared_input"], action_state=state["planning"],
        artifacts={}, confirmed_conflicts=state["conflict_confirmations"])
    actual = planner._support_review_inputs(context, [candidate["input"]])[0]
    PlanningDependencies(state["planning"]["dependency_records"]).save_support(
        actual, candidate["output"], policy=context.confirmed_conflicts)
    planner._refresh_gaps(context, state["planning"])
    assert any(escalation.generation_replan_review_context(
        revision["input"].get("_planning_feedback", {}).get("downstream_problems", []))
        for revision in state["planning"]["route_review_revisions"])
    isolated = planner._isolated_context(context)
    planning.current_planning_support_decisions(isolated, {
        "plan": state["planning"]["plan"], "prepared_items": state["planning"]["route_inputs"],
        "route_records": state["planning"]["route_records"], "repair_records": state["planning"]["gap_records"],
        "review_revisions": state["planning"]["route_review_revisions"],
    })
    generation._project_current_problems(state)
    assert problem["problem_id"] not in state["problems"]


def test_independent_verdict_enters_next_generator_request_and_changes_identity(real):
    state = state_for(real)
    problems = record(real, state)
    problem = auto_problem(problems)
    candidate = cpu_review_candidate_from_real_supported_output(real, state, problem)
    item = real["original_generation_input"]
    assert not generation._generation_independent_support_reviews(state, item)
    before = generation._generation_request(item, "")
    state["planning"]["route_review_revisions"].append(candidate)
    proofs = generation._generation_independent_support_reviews(state, item)
    assert len(proofs) == 1 and proofs[0]["problem_id"] == problem["problem_id"]
    after = deepcopy(before)
    after.setdefault("_planning_feedback", {})["independent_support_reviews"] = proofs
    assert action_state_hash(before) != action_state_hash(after)
    from modules.agent_platform.test_generation_schemas import GENERATION_MODEL_INPUT_PROJECTION
    # 使用SDK实际投影函数，确认复核信息不会在transport前被裁掉。
    projected = project_input_value(after, GENERATION_MODEL_INPUT_PROJECTION)
    assert projected["_planning_feedback"]["independent_support_reviews"] == proofs


def test_fixture_and_production_bytes_remain_unchanged(real):
    manifest = json.loads((BASE / "closed-draft-manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["source_sha256"].items():
        path = ROOT / f"backend/modules/agent_platform/{name}.py"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
