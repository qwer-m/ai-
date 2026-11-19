"""同一目标下的规划、生成和审查行动；版本变化沿真实完整输入传播。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from functools import partial

from .action_journal import action_state_hash
from .autonomous_budget import AgentSubtreeRequestBudgetExceeded
from .autonomous_tools import AgentNoProgressError, repeated_domain_rejection
from .output_repair import OutputValidationError
from . import test_generation_planning_agent as planning
from . import test_generation_review_agent as review
from .test_generation_batching import (
    generation_layout_from_inputs, merge_grounded_generation_batches,
    generation_layout_requirements,
    _validate_generation_batch_output, prepare_test_case_batches,
    validate_generation_case_uniqueness,
)
from .test_generation_escalation import validate_generation_scope_feedback
from .test_generation_facts import bound_fact_ids
from .test_generation_review import (
    _difference_identity, prepare_final_review_batches, postprocess_final_review_batch_item, refresh_final_review_input,
)
from .test_generation_semantics import (
    prepare_authority_reconciliation, validate_authority_reconciliation_output,
    merge_authority_reconciliation,
)


def _isolated(context, *, state=None, input_payload=None):
    from .test_generation_source_agent import hydrate_source_context

    source_context = hydrate_source_context(context)
    return replace(source_context, artifacts=deepcopy(source_context.artifacts), executed_tools=[], tool_calls=[],
                   action_journal=None, action_state=deepcopy(state or {}),
                   action_input=deepcopy(context.action_input if input_payload is None else input_payload))


def _digest(state):
    # 检索审计由整个检查点校验；它不改变业务产物的领域版本。
    return action_state_hash({key: value for key, value in state.items()
                              if key not in {"domain_sha256", "context_retrievals"}})


def refresh_generation_state(state):
    """领域动作和人工确认共用状态投影及摘要计算。"""
    _project_current_problems(state)
    state["domain_sha256"] = _digest(state)


def _save(context, state):
    refresh_generation_state(state)
    context.action_state = state


def _planning_context(context, state):
    return _isolated(context, state=state["planning"], input_payload=state["prepared_input"])


def _state_snapshot(context):
    state = context.action_state
    if not isinstance(state, dict) or state.get("schema_version") != 3:
        raise ValueError("统一生成状态未初始化或版本无效")
    if state.get("input_sha256") != action_state_hash(context.action_input) or state.get("domain_sha256") != _digest(state):
        raise ValueError("统一生成状态与完整输入或内容摘要不一致")
    # 每次恢复均沿规划领域校验持久来源，而非相信模型携带的来源声明。
    plan = planning.observe_planning_state(_planning_context(context, state)) if state["planning"] is not None else None
    return state, plan


def _state(context):
    return _state_snapshot(context)[0]


def _mutable(context, action_key):
    state, _ = _require_action(context, action_key)
    return deepcopy(state)


def _record(item, output, index):
    return {"item_index": index, "input_hash": action_state_hash(item), "output": deepcopy(output)}


def _retain(inputs, cache):
    return [{**deepcopy(cache[action_state_hash(item)]), "item_index": index}
            if action_state_hash(item) in cache else None for index, item in enumerate(inputs)]


def _pending(records):
    return [index for index, item in enumerate(records) if item is None]


def _indexes(arguments, available_indexes):
    indexes = arguments.get("item_indexes")
    if (not isinstance(indexes, list) or not indexes
            or any(type(index) is not int for index in indexes) or len(set(indexes)) != len(indexes)):
        raise OutputValidationError("item_indexes 必须引用当前观察中的有效且不重复的索引")
    if any(index not in available_indexes for index in indexes):
        raise OutputValidationError("所选输入不在当前待处理范围内，不能重复抽取", details={"pending_indexes": available_indexes})
    return indexes


def initialize_generation_state(context, arguments):
    from .test_generation_source_agent import initialize_source

    context.action_input = deepcopy(arguments)
    if context.action_state:
        return _state(context)
    source = initialize_source(context, arguments)
    state = {
        "schema_version": 3, "input_sha256": action_state_hash(arguments),
        "source": source, "prepared_input": None,
        "conflict_confirmations": deepcopy(source["confirmed_source_decisions"]),
        "planning": None, "accepted_plan": None,
        "authority_inputs": [], "authority_records": [],
        "authority_cache": {}, "effective_facts": None,
        "generation_inputs": [], "generation_records": [], "generation_cache": {}, "generation": None,
        "generation_layout": None, "layout_version": 0, "generation_version": 0,
        "review_inputs": [], "review_records": [], "review_cache": {}, "review": None, "review_domains": {},
        "problems": {}, "generation_requests": {}, "revision_history": [],
        "context_retrievals": [], "delivery": None, "completed": False,
    }
    _save(context, state)
    return state


def _activate_planning(context, state):
    """来源和草案已按完整输入验收后，才建立可重规划的领域状态。"""
    prepared = state["prepared_input"]
    if prepared is None or state["planning"] is not None:
        return
    child = _planning_context(context, state)
    state["planning"] = planning.initialize_planning_state(child, prepared)
    authority = prepared["source_authority"]
    state["authority_inputs"] = deepcopy(authority["prepared_items"])
    state["authority_records"] = deepcopy(authority["reconciliation_records"])
    state["effective_facts"] = deepcopy(authority["result"])
    for index, (item, record) in enumerate(zip(state["authority_inputs"], state["authority_records"], strict=True)):
        accepted = _record(item, record["output"], index)
        state["authority_records"][index] = accepted
        state["authority_cache"][accepted["input_hash"]] = accepted


async def _source_action(context, arguments, action_key):
    from . import test_generation_source_agent as source

    state = _mutable(context, action_key)
    child = replace(context, action_state=state)
    await getattr(source, action_key)(child, arguments)
    state = child.action_state
    _activate_planning(child, state)
    _save(context, state)
    # 当前来源和行动范围由统一观察提供，源行动仅返回实际效果。
    return {"action": action_key,
            "source_progress": {kind: {"count": len(state["source"][f"{kind}_records"]),
                                      "completed": len(state["source"][f"{kind}_records"])
                                                   - len(_pending(state["source"][f"{kind}_records"]))}
                                for kind in ("text", "vision", "authority", "planning")},
            "planning_ready": state["planning"] is not None}


async def analyze_source(context, arguments):
    return await _source_action(context, arguments, "analyze_source")


async def govern_source(context, arguments):
    return await _source_action(context, arguments, "govern_source")


async def draft_source_plan(context, arguments):
    return await _source_action(context, arguments, "draft_source_plan")


def _review_context(context, state):
    if state["review"] is None:
        raise OutputValidationError("当前生成版本尚未完成独立初审")
    return review.compose_review_context(context, input_payload=state["review"]["input"], state=state["review"]["state"])


def _problem(state, *, origin, input_hash, payload, fact_ids, batch_indexes, review_provenance=None):
    identity = action_state_hash([origin, input_hash, payload])
    state["problems"].setdefault(identity, {
        "problem_id": identity, "origin": origin, "input_hash": input_hash,
        "fact_ids": sorted(set(fact_ids)), "generation_indexes": sorted(set(batch_indexes)),
        "payload": deepcopy(payload),
        **({"review_provenance": deepcopy(review_provenance)} if review_provenance is not None else {}),
    })


def _current_problems(state):
    return list(state["problems"].values())


def _problem_payload_view(payload, *, detailed=False):
    """专业失败保留完整审计，模型只读取当前反馈和最近一次提交，避免反复处理已修正的旧差异。"""
    if payload.get("result_type") != "specialist_failure":
        return deepcopy(payload)
    attempts = payload.get("submission_attempts", [])
    latest = attempts[-1] if attempts else None
    result = {key: deepcopy(payload[key]) for key in ("result_type", "error_type", "message", "evidence_role")
              if key in payload}
    result["submission_attempt_count"] = len(attempts)
    result["consultation_count"] = len(payload.get("consultation_results", []))
    result["details_available"] = True
    if latest is not None:
        feedback = (latest.get("feedback") or {}).get("feedback", {})
        result["latest_validation"] = {key: deepcopy(feedback[key]) for key in ("error_type", "message") if key in feedback}
    if detailed:
        result["submission_attempts"] = [deepcopy(latest)] if latest is not None else []
        result["consultation_results"] = deepcopy(payload.get("consultation_results", []))
        result["budget_usage"] = deepcopy(payload.get("budget_usage", {}))
        result["instruction"] = "这是最近一次提交及当前反馈。更早的提交保留在原始执行回执；不能将旧候选的差异当作本次仍未修正的问题。"
    return result


def _preparation_identity(state):
    return action_state_hash({"plan": state["accepted_plan"], "facts": state["effective_facts"],
                              "layout": state["generation_layout"]})


def _project_current_problems(state):
    """领域状态只保存当前问题；历史结论由已有行动回执保留，不能因无关修复重复登记。"""
    generation_hashes = {action_state_hash(item) for index, item in enumerate(state["generation_inputs"])
                         if state["generation_records"][index] is None}
    state["problems"] = {key: value for key, value in state["problems"].items()
                         if (value["origin"] == "generation" and value["input_hash"] in generation_hashes)
                         or (value["origin"] == "preparation" and state["accepted_plan"] is not None
                             and state["effective_facts"] is not None and not state["generation_inputs"]
                             and value["input_hash"] == _preparation_identity(state))}
    if state["review"] is None:
        return
    source, current = state["review"]["input"], state["review"]["state"]
    case_facts = {binding["case_id"]: bound_fact_ids(binding)
                  for binding in current["generation"]["case_fact_bindings"]}
    global_review = current["global_review"]
    global_keys = {tuple(key) for key in (global_review or {}).get("routed_difference_keys", [])}
    for item, record in zip(source["review_inputs"], current["review_records"], strict=True):
        batch_id = item["review_batch"]["batch_id"]
        # 修复提交后仍保存旧审查原文供追溯，但不能把旧差异归到尚未复审的新候选。
        if batch_id in current["pending_rechecks"]:
            continue
        refreshed = refresh_final_review_input(item, generation=current["generation"],
                                              generation_inputs=source["generation_inputs"])
        batch_identity = review.review_input_identity(refreshed,
            generation_inputs=source["generation_inputs"], authoritative_facts=source["authoritative_facts"])
        for difference in record["output"]["differences"]:
            identity = batch_identity
            provenance = {"scope": "batch", "status": "current", "batch_id": batch_id,
                          "review_input_hash": batch_identity}
            if _difference_identity(difference) in global_keys:
                # 跨批差异保留原全局审查依赖；其他批次变化只标过期，不冒充新的全局结论。
                global_identity = {"global_input_hash": global_review["input_sha256"],
                                   "global_scope_hash": global_review["scope_sha256"]}
                identity = action_state_hash({"review_input_hash": batch_identity, **global_identity})
                provenance.update(scope="global", **global_identity,
                    status="current" if global_review["generation_sha256"] == current["generation_sha256"] else "stale_global")
            facts = sorted(set(difference.get("related_fact_ids") or [])
                           | case_facts.get(difference.get("case_id"), set()))
            indexes = [index for index, generation_input in enumerate(state["generation_inputs"])
                       if set(facts) & {fact["fact_id"] for fact in generation_input["authoritative_facts"]}]
            _problem(state, origin="review", input_hash=identity, payload=difference,
                     fact_ids=facts, batch_indexes=indexes, review_provenance=provenance)


def _feedback(state, problem_ids):
    current = {item["problem_id"]: item for item in _current_problems(state)}
    if (not isinstance(problem_ids, list) or not problem_ids or len(set(problem_ids)) != len(problem_ids)
            or any(value not in current for value in problem_ids)):
        raise OutputValidationError("规划反馈必须引用当前版本的真实 problem_ids", details={"problem_ids": list(current)})
    return [deepcopy(current[value]) for value in problem_ids]


def _authority_problems(state):
    return [item for item in _current_problems(state)
            if item["origin"] == "preparation" and item["payload"].get("kind") == "authority_support_gap"]


def _support_review_scope(state, plan):
    """复核目标来自真实缺口；既有支持结论和重复裁决不再充当待办。"""
    problems = _authority_problems(state)
    unresolved = {item["module_index"]: set(item["test_design_item_indexes"])
                  for item in plan["unresolved_design_selections"]}
    for problem in problems:
        for gap in problem["payload"]["design_support_gaps"]:
            unresolved.setdefault(gap["module_index"], set()).add(gap["test_design_item_index"])
    selections = [{"module_index": index, "test_design_item_indexes": sorted(indexes)}
                  for index, indexes in sorted(unresolved.items())]
    requires_evidence = []
    if not plan["pending_gap_indexes"]:
        pending = planning.unreviewed_support_selections(state["planning"], selections, problems)
        pending_pairs = {(item["module_index"], index) for item in pending for index in item["test_design_item_indexes"]}
        all_scopes = {item["scope_id"] for item in state["planning"]["source_semantics"]["planning_scopes"]}
        inputs = {item["module_index"]: item for item in state["planning"]["gap_inputs"]}
        for item in selections:
            known_scopes = {fact["scope_id"] for fact in inputs[item["module_index"]]["candidate_facts"]}
            indexes = [index for index in item["test_design_item_indexes"]
                       if (item["module_index"], index) not in pending_pairs]
            if indexes and all_scopes - known_scopes:
                requires_evidence.append({"module_index": item["module_index"], "test_design_item_indexes": indexes})
        selections = pending
    return {"pending_gap_indexes": list(plan["pending_gap_indexes"]),
            "unresolved_design_selections": selections,
            "requires_additional_evidence": requires_evidence,
            "problem_ids": [item["problem_id"] for item in problems]}


def _available_actions(state, plan, reviewed, generation_preflight):
    """观察与执行共用当前合法范围，不按阶段规定唯一行动顺序。"""
    if plan is None:
        from .test_generation_source_agent import observe_source

        return {"retrieve_context": {}, **observe_source(None, state["source"])["available_actions"]}
    actions = {}
    problem_ids = [item["problem_id"] for item in _current_problems(state)]
    structural_problem_ids = [item["problem_id"] for item in _current_problems(state)
                              if item["origin"] != "review" or item["payload"].get("repair_scope") == "cohort"]
    generation_indexes = list(range(len(state["generation_inputs"])))
    if generation_indexes or problem_ids:
        actions["inspect_generation_scope"] = {
            "item_indexes": [] if state["review_inputs"] else generation_indexes,
            "review_batch_ids": [item["review_batch"]["batch_id"] for item in state["review_inputs"]],
            "problem_ids": problem_ids,
        }
    if state["completed"]:
        return actions
    # 检索是主 Agent 按知识缺口选择的行动，必须进入实际发送给模型的目录，
    # 仅在工具绑定中声明不会使其在动态目录过滤后可见。
    actions["retrieve_context"] = {}
    if state["effective_facts"] is None:
        # 来源解释或用户口径改变后先治理，再开放草案、路由与支持复核。
        actions["read_planning_evidence"] = {}
        pending = _pending(state["authority_records"])
        if not pending:
            raise ValueError("未完成来源治理但缺少待处理的来源输入")
        actions["reconcile_generation_authority"] = {"item_indexes": pending}
        return actions
    # 已接受的规划只因当前真实下游问题重新开放修订，不能在生成途中无故重新规划。
    if not plan["completed"] and (state["accepted_plan"] is None or problem_ids):
        actions["read_planning_evidence"] = {}
        if plan["source_items"]:
            actions["reinspect_planning_source"] = {"source_items": deepcopy(plan["source_items"])}
        if plan["planning_batches"] and (state["accepted_plan"] is None or structural_problem_ids):
            actions["revise_planning_draft"] = {
                "item_indexes": [item["item_index"] for item in plan["planning_batches"]], "problem_ids": structural_problem_ids,
            }
            problems = _current_problems(state)
            authority_gap = any(item["origin"] == "preparation" and item["payload"].get("kind") == "authority_support_gap"
                                for item in problems)
            if (not authority_gap and plan["can_rebuild"]
                    and (not plan["plan_available"] or plan["unrouted_module_indexes"])) or any(
                    item["origin"] in {"generation", "review"} and item["problem_id"] in structural_problem_ids
                    for item in problems):
                actions["rebuild_business_plan"] = {"problem_ids": structural_problem_ids}
        if plan["plan_available"] and plan["pending_route_indexes"]:
            actions["route_planning_scopes"] = {}
        support_scope = _support_review_scope(state, plan)
        if (support_scope["pending_gap_indexes"] or support_scope["unresolved_design_selections"]
                or support_scope["requires_additional_evidence"]):
            actions["review_planning_route_gaps"] = support_scope
        if (plan["can_finish"] and state["accepted_plan"] is None
                and generation_preflight is not None and generation_preflight["ready"]):
            actions["accept_generation_plan"] = {}
        if generation_preflight is not None and not generation_preflight["ready"]:
            if generation_preflight["repair_kind"] == "generation_layout":
                # 分组失败不构成改写业务意图的依据；计划验收前也能提交布局。
                actions.pop("revise_planning_draft", None)
                actions["revise_generation_layout"] = {
                    "module_indexes": [item["module_index"] for item in generation_preflight["layout_requirements"]],
                    "problem_ids": [], "preflight": deepcopy(generation_preflight),
                    "requires_complete_layout": state["generation_layout"] is None,
                }
            elif "revise_planning_draft" in actions:
                actions["revise_planning_draft"]["generation_preflight"] = deepcopy(generation_preflight)
                actions["revise_planning_draft"]["item_indexes"] = list(generation_preflight["affected_draft_indexes"])
                if not actions["revise_planning_draft"]["item_indexes"]:
                    actions.pop("revise_planning_draft")
    generation_pending = _pending(state["generation_records"])
    if state["effective_facts"] is not None:
        if generation_pending:
            actions["generate_cases"] = {"item_indexes": generation_pending}
        if structural_problem_ids and state["generation_layout"] is not None:
            actions["revise_generation_layout"] = {
                "module_indexes": [item["module_index"] for item in state["generation_layout"]],
                "problem_ids": structural_problem_ids,
            }
    review_pending = _pending(state["review_records"])
    if state["generation"] is not None and review_pending:
        actions["review_generated_cases"] = {"item_indexes": review_pending}
    if reviewed and not reviewed["completed"]:
        actions["read_review_evidence"] = {}
        if reviewed["repairable_batch_ids"]:
            actions["repair_review_batch"] = {"batch_ids": list(reviewed["repairable_batch_ids"])}
        if reviewed["recheck_batch_ids"]:
            actions["recheck_review_batch"] = {"batch_ids": list(reviewed["recheck_batch_ids"])}
        if reviewed["can_review_all_cases"]:
            actions["review_all_cases"] = {}
    if reviewed and reviewed["can_finish"] and state["accepted_plan"] is not None:
        actions["finish_generation"] = {}
    return actions


def _require_action(context, action_key, *, snapshot=None):
    state, plan = _state_snapshot(context) if snapshot is None else snapshot
    reviewed = review.observe_review_state(_review_context(context, state)) if state["review"] else None
    generation_preflight = _generation_plan_preflight(context, state, plan)
    actions = _available_actions(state, plan, reviewed, generation_preflight)
    if action_key == "accept_generation_plan" and generation_preflight is not None and not generation_preflight["ready"]:
        raise OutputValidationError(generation_preflight["message"], details=generation_preflight["details"])
    if action_key not in actions:
        # 当前完整目录由每轮状态观察提供；错误回执不复制目录，避免旧目录在历史中持续误导模型。
        error = OutputValidationError("当前状态没有该行动的合法目标，请按本轮最新 available_actions 选择",
                                      details={"action": action_key})
        if context.action_journal is not None:
            state_hash = action_state_hash(state)
            for header in reversed(context.action_journal.receipts()):
                if header["status"] != "failed" or header["pre_state_hash"] != state_hash:
                    continue
                receipt = context.action_journal.receipt_by_id(header["tool_call_id"])
                repeated = repeated_domain_rejection({"receipts": {header["tool_call_id"]: receipt}},
                    state_hash=state_hash, error_type=type(error).__name__, message=str(error))
                if repeated is not None:
                    raise AgentNoProgressError("合法行动目录未变，却再次选择没有合法目标的行动，停止无进展协调",
                                               diagnostic={"action": action_key, **repeated})
        raise error
    return state, actions[action_key]


def observe_generation_state(context, arguments=None):
    state, plan = _state_snapshot(context)
    if plan is None:
        from .test_generation_source_agent import observe_source

        source = observe_source(context, state["source"])
        return {"goal": {"objective": "依据固定真实来源交付通过独立审查的可执行测试用例",
                         "case_budget": context.action_input["case_budget"],
                         "source_ref": deepcopy(state["source"]["source_ref"])},
                "source": source, "planning": None, "completed": False, "can_finish": False,
                "available_actions": _available_actions(state, None, None, None)}
    issues = []
    for problem in _current_problems(state):
        fact_ids = set(problem["fact_ids"])
        draft_indexes = [index for index, item in enumerate(state["planning"]["planning_inputs"])
                         if fact_ids & {fact["fact_id"] for scope in item["planning_scopes"] for fact in scope["facts"]}]
        issues.append({key: deepcopy(problem[key]) for key in ("problem_id", "origin", "generation_indexes", "fact_ids")}
                      | {"planning_batch_indexes": draft_indexes,
                         "reported_problem": _problem_payload_view(problem["payload"]), "details_available": True})
        if "review_provenance" in problem:
            issues[-1]["review_provenance"] = deepcopy(problem["review_provenance"])
    reviewed = review.observe_review_state(_review_context(context, state)) if state["review"] else None
    generation_preflight = _generation_plan_preflight(context, state, plan)
    plan_observation = planning.compact_planning_observation(plan)
    if reviewed is not None or state["generation_inputs"]:
        # 生成和终审只需要已验收规划的版本与状态；原规划材料由受控行动按真实范围读取。
        plan_observation = {key: deepcopy(plan[key]) for key in (
            "source_version", "draft_version", "plan_version", "support_version",
            "plan_available", "can_rebuild", "can_finish", "completed",
        )}
    available_actions = _available_actions(state, plan, reviewed, generation_preflight)
    layout = state["generation_layout"]
    # 只有当前允许重新划分批次时才发送完整布局；执行中的事实路由由按需取证读取。
    layout_summary = [{"module_index": item["module_index"], "group_count": len(item["groups"]),
                       "case_budget": sum(group["case_budget"] for group in item["groups"]),
                       "fact_count": sum(len(group["fact_ids"]) for group in item["groups"]),
                       "test_design_item_count": sum(len(group["test_design_item_ids"]) for group in item["groups"])}
                      for item in (layout or [])]
    return {
        "goal": {"objective": "依据本次真实来源完成可执行用例；发现上游范围问题时修订规划并重新验收受影响产物",
                 "source_ref": deepcopy(state["planning"]["goal"]["source_ref"]),
                 "case_budget": context.action_input["case_budget"],
                 "acceptance": ["来源和用户总预算不变", "规划及权威判断对应当前完整输入", "生成范围完整", "当前版本独立批次及全局审查通过"]},
        # 协调者只需索引、裁决和行动范围；完整事实由规划专业行动按需读取。
        "source": {"ready": True}, "planning": plan_observation,
        "plan_accepted": state["accepted_plan"] is not None,
        "generation_preflight": generation_preflight,
        "authority": {"pending_indexes": _pending(state["authority_records"]), "count": len(state["authority_inputs"]),
                      "ready": state["effective_facts"] is not None},
        "generation": {"layout_version": state["layout_version"], "version": state["generation_version"],
                       "layout": deepcopy(layout) if "revise_generation_layout" in available_actions else None,
                       "layout_summary": layout_summary,
                       "pending_indexes": _pending(state["generation_records"]),
                       "batches": [{"item_index": index, "batch_id": item["batch"]["batch_id"],
                                    "module_index": item["batch"]["module_index"], "module_name": item["batch"]["module_name"],
                                    "case_budget": item["case_budget"], "fact_count": len(item["authoritative_facts"]),
                                    "status": "pending" if state["generation_records"][index] is None else "validated"}
                                   for index, item in enumerate(state["generation_inputs"])],
                       "ready": state["generation"] is not None},
        "pending_initial_review_indexes": _pending(state["review_records"]),
        "initial_review_batches": [
            {"item_index": index, **({"batch_id": item["review_batch"]["batch_id"]} if reviewed is not None
                                      else deepcopy(item["review_batch"])),
             "status": "pending" if state["review_records"][index] is None else "reviewed"}
            for index, item in enumerate(state["review_inputs"])
        ],
        "initial_review_count": len(state["review_inputs"]), "review": reviewed,
        "problems": issues, "can_finish": bool(reviewed and reviewed["can_finish"] and state["accepted_plan"]),
        "completed": state["completed"],
        "delivery": {"ready": state["delivery"] is not None},
        "available_actions": available_actions,
    }


def inspect_generation_scope(context, arguments):
    state, scope = _require_action(context, "inspect_generation_scope")
    if sum(arguments.get(key) is not None for key in ("problem_id", "item_index", "review_batch_id")) != 1:
        raise OutputValidationError("读取范围必须且只能选择 item_index、review_batch_id 或 problem_id")
    if arguments.get("problem_id") is not None:
        problem = _feedback(state, [arguments["problem_id"]])[0]
        return {"problem": {**problem, "payload": _problem_payload_view(problem["payload"], detailed=True)}}
    review_batch_id = arguments.get("review_batch_id")
    if review_batch_id is None:
        index = arguments.get("item_index")
        if type(index) is not int or index not in scope["item_indexes"]:
            raise OutputValidationError("读取范围需要当前 generation item_index；终审阶段按 review_batch_id 读取", details=scope)
        return {"item_index": index, "input": deepcopy(state["generation_inputs"][index]),
                "candidate": deepcopy(state["generation_records"][index])}
    if review_batch_id not in scope["review_batch_ids"]:
        raise OutputValidationError("终审范围必须引用当前 review_batch_id", details=scope)
    review_index, item = next((index, item) for index, item in enumerate(state["review_inputs"])
                              if item["review_batch"]["batch_id"] == review_batch_id)
    current = state["review"]["state"] if state["review"] else None
    # 独立修复已替换终审领域中的候选；不能再用初次生成结果解释当前差异。
    awaiting_recheck = bool(current and review_batch_id in current["pending_rechecks"])
    record = current["review_records"][review_index] if current else state["review_records"][review_index]
    return {"review_scope": {
        "item_index": review_index,
        "input": refresh_final_review_input(
            item, generation=current["generation"] if current else state["generation"],
            generation_inputs=state["generation_inputs"],
        ),
        "status": "awaiting_recheck" if awaiting_recheck else "reviewed" if record else "pending_initial_review",
        "review_result": deepcopy(record["output"]) if record and not awaiting_recheck else None,
    }}


def _invalidate_downstream(state, *, preserve_source_authority=False):
    authority_inputs = deepcopy(state["authority_inputs"])
    known_authority = {
        key: deepcopy(state[key]) for key in ("authority_inputs", "authority_records", "effective_facts")
    } if preserve_source_authority and state["effective_facts"] is not None else {}
    state.update(accepted_plan=None, authority_inputs=[], authority_records=[], effective_facts=None,
                 generation_inputs=[], generation_records=[], generation=None, generation_layout=None,
                 review_inputs=[], review_records=[], review=None, delivery=None, completed=False)
    state["authority_inputs"] = authority_inputs
    state["authority_records"] = [None] * len(authority_inputs)
    state.update(known_authority)


async def _planning_action(context, arguments, action_key, callback, *, feedback=False):
    current, scope = _require_action(context, action_key)
    if feedback and "problem_ids" in scope and any(
            item not in scope["problem_ids"] for item in arguments.get("problem_ids", [])):
        raise OutputValidationError("该问题的修复范围不要求修改业务规划", details=scope)
    state = deepcopy(current)
    planning_hash = action_state_hash(state["planning"])
    child = _planning_context(context, state)
    child.action_state.pop("downstream_feedback", None)
    if feedback:
        problems = _feedback(state, arguments.get("problem_ids"))
        child.action_state["downstream_feedback"] = problems
    planning._commit(child, child.action_state)
    version_keys = ("source_version", "draft_version", "plan_version", "support_version")
    before = tuple(state["planning"][key] for key in version_keys)
    source_hash = action_state_hash(state["planning"]["source_semantics"])
    result = callback(child, arguments)
    import inspect
    if inspect.isawaitable(result):
        result = await result
    # 问题只注入本次专业输入；行动后由父领域重新投影当前问题，避免旧反馈豁免新状态的限制。
    child.action_state.pop("downstream_feedback", None)
    planning._commit(child, child.action_state)
    state["planning"] = child.action_state
    after = tuple(child.action_state[key] for key in version_keys)
    if before != after:
        state["revision_history"].append({"before": before, "after": after,
                                           "problem_ids": deepcopy(arguments.get("problem_ids") or [])})
        # 支持裁决变化不改变同一来源、草案和模块规划下已经审过的权威证据范围。
        same_source = source_hash == action_state_hash(child.action_state["source_semantics"])
        _invalidate_downstream(state, preserve_source_authority=same_source)
        if not same_source:
            # 来源重检必须携带按真实事实差异迁移的旧裁决，不能全源退回raw。
            inputs = planning.prepare_source_authority(child)
            state["authority_inputs"] = inputs
            state["authority_records"] = _retain(inputs, state["authority_cache"])
    _save(context, state)
    # 当前完整状态由 SDK 的观察入口提供；行动回执只保留实际效果，避免无变化被当成修复成功。
    return {"action": action_key, "changed": result.get("changed", planning_hash != action_state_hash(child.action_state)),
            "planning_versions": dict(zip(version_keys, after, strict=True)), "downstream_invalidated": before != after}


async def read_planning_evidence(context, arguments):
    state = _mutable(context, "read_planning_evidence")
    child = _planning_context(context, state)
    result = planning.read_planning_evidence(child, arguments)
    state["planning"] = child.action_state
    _save(context, state)
    return result


async def reinspect_planning_source(context, arguments):
    return await _planning_action(context, arguments, "reinspect_planning_source", planning.reinspect_planning_source)


async def revise_planning_draft(context, arguments):
    return await _planning_action(context, arguments, "revise_planning_draft", planning.revise_planning_draft,
                                  feedback=bool(arguments.get("problem_ids")))


async def rebuild_business_plan(context, arguments):
    return await _planning_action(context, arguments, "rebuild_business_plan", planning.rebuild_business_plan,
                                  feedback=bool(arguments.get("problem_ids")))


async def route_planning_scopes(context, arguments):
    return await _planning_action(context, arguments, "route_planning_scopes", planning.route_planning_scopes)


def _support_review_arguments(context, arguments):
    _, scope = _require_action(context, "review_planning_route_gaps")
    selections = arguments.get("design_selections") or scope["unresolved_design_selections"]
    if scope["pending_gap_indexes"]:
        if arguments.get("design_selections"):
            raise OutputValidationError("初次支持审查尚未完成，请先处理当前待审范围")
        selections = []
    else:
        allowed = {(item["module_index"], index) for item in scope["unresolved_design_selections"]
                   for index in item["test_design_item_indexes"]}
        # 补充真实范围仍可复核；既有完整输入去重会拒绝只改标签或重复添加原范围。
        if arguments.get("additional_scope_ids"):
            allowed.update((item["module_index"], index) for item in scope["requires_additional_evidence"]
                           for index in item["test_design_item_indexes"])
        if any((item.get("module_index"), index) not in allowed for item in selections
               for index in item.get("test_design_item_indexes", [])):
            raise OutputValidationError("支持复核只能处理当前未决且未重复裁决的意图", details=scope)
    # 权威结论由平台沿当前状态传递，不能因模型漏填 problem_ids 而重新批准失效事实。
    problem_ids = list(dict.fromkeys([*(arguments.get("problem_ids") or []), *scope["problem_ids"]]))
    return {**arguments, "design_selections": selections, "problem_ids": problem_ids,
            "question": arguments.get("question") or "核对当前未决意图在真实来源与当前权威裁决下的完整支持，逐项说明缺口。"}


async def review_planning_route_gaps(context, arguments):
    prepared = _support_review_arguments(context, arguments)
    return await _planning_action(context, prepared,
                                  "review_planning_route_gaps", planning.review_planning_route_gaps,
                                  feedback=bool(prepared["problem_ids"]))


def _generation_inputs_for_plan(context, state, resolved_plan):
    """预演与正式准备共用完整输入和同一分批器，不另造容量近似规则。"""
    effective = state["effective_facts"]
    if effective is None or state["planning"]["source_authority"] is None:
        raise ValueError("来源治理未完成，不能准备生成批次")
    return prepare_test_case_batches(_isolated(context), {
        "plan": resolved_plan["plan"], "effective_facts": effective["effective_facts"],
        "case_budget": context.action_input["case_budget"],
        "batch_case_limit": context.action_input["batch_case_limit"],
        **({"generation_layout": state["generation_layout"]} if state["generation_layout"] is not None else {}),
    })["items"]


def _generation_plan_preflight(context, state, planning_observation):
    """开放接受规划之前做确定性预演；观察不改变状态、不调用专业模型。"""
    if planning_observation is None or state["accepted_plan"] is not None or not planning_observation["can_finish"]:
        return None
    resolved = planning._resolved_output(_planning_context(context, state))
    return _generation_inputs_preflight(context, state, resolved)


def _generation_inputs_preflight(context, state, resolved_plan):
    """基于已验收的真实规划生成可行性摘要，与旧规划状态的恢复协议无关。"""
    try:
        items = _generation_inputs_for_plan(context, state, resolved_plan)
    except OutputValidationError as error:
        result = {"ready": False, "error_type": type(error).__name__, "message": str(error),
                  "details": deepcopy(error.details), "affected_draft_indexes": []}
        if (error.details.get("reason") == "generation_group_capacity_exhausted"
                or error.details.get("field") == "generation_layout"):
            result.update(repair_kind="generation_layout", layout_requirements=generation_layout_requirements(
                resolved_plan["plan"], state["effective_facts"]["effective_facts"]))
            return result
        design_ids = ([error.details["test_design_item_id"]] if error.details.get("test_design_item_id") else
                      [gap["test_design_item_id"] for gap in error.details.get("incomplete_design_supports", [])])
        # 按已验收原子意图回溯草案，不把共同引用事实误当成产生意图的草案。
        origins = [planning.generation_preflight_draft_origins(
            _planning_context(context, state), state["planning"], resolved_plan, identity,
            effective_facts=state["effective_facts"]["effective_facts"]) for identity in design_ids]
        result.update(repair_kind="planning_draft", draft_origins=origins,
                      affected_draft_indexes=sorted({index for origin in origins for index in origin["planning_batch_indexes"]}))
        return result
    return {"ready": True, "batch_count": len(items),
            "case_budget": sum(item["case_budget"] for item in items)}


def _prepare_generation(context, state):
    effective = state["effective_facts"]
    try:
        items = _generation_inputs_for_plan(context, state, state["accepted_plan"])
    except OutputValidationError as error:
        gaps = error.details.get("incomplete_design_supports")
        if not gaps:
            raise
        # 权威专家已完成有效判断；保存真实结果，将支持缺口交还父 Agent 决策。
        # 不能回滚整次权威行动，让后续复核看不见事实为何失效。
        facts = {fact["fact_id"]: fact for fact in effective["authoritative_facts"]}
        affected = {fact_id for gap in gaps for fact_id in gap["support_fact_ids"]}
        decisions = [deepcopy(decision) for record in state["authority_records"]
                     for decision in record["output"]["decisions"] if decision["fact_id"] in affected]
        _problem(state, origin="preparation", input_hash=_preparation_identity(state),
            payload={"kind": "authority_support_gap", "message": str(error), "design_support_gaps": deepcopy(gaps),
                     "authority_facts": [deepcopy(facts[fact_id]) for fact_id in sorted(affected)],
                     "authority_decisions": decisions,
                     "authority_inactive_fact_ids": sorted(fact_id for fact_id, fact in facts.items() if fact["status"] != "effective")},
            fact_ids=sorted(affected), batch_indexes=[])
        return
    state["generation_inputs"] = items
    state["generation_layout"] = generation_layout_from_inputs(items)
    state["generation_records"] = _retain(items, state["generation_cache"])
    state.update(generation=None, review_inputs=[], review_records=[], review=None)
    if not _pending(state["generation_records"]):
        _prepare_review(context, state)


def accept_generation_plan(context, arguments):
    state = _mutable(context, "accept_generation_plan")
    resolved = planning._resolved_output(_planning_context(context, state))
    state["accepted_plan"] = resolved
    _prepare_generation(context, state)
    _save(context, state)
    return observe_generation_state(context)


def _prepare_action(context, arguments, kind, action_key, *, snapshot=None):
    state, scope = _require_action(context, action_key, snapshot=snapshot)
    indexes = _indexes(arguments, scope["item_indexes"])
    return {"state_hash": _digest(state), "item_indexes": indexes,
            "items": [deepcopy(state[f"{kind}_inputs"][index]) for index in indexes]}


def _apply_action(context, action, outputs, kind, action_key):
    snapshot = _state_snapshot(context)
    state = snapshot[0]
    if action.get("state_hash") != _digest(state):
        raise ValueError("行动基于的完整领域版本已变化")
    indexes = action.get("item_indexes")
    if (not isinstance(indexes, list) or not indexes
            or any(type(index) is not int or not 0 <= index < len(state[f"{kind}_inputs"]) for index in indexes)
            or len(set(indexes)) != len(indexes)):
        raise ValueError("专业行动的完整输入索引与当前领域不一致")
    expected = [state[f"{kind}_inputs"][index] for index in indexes]
    if action["items"] != expected or len(outputs) != len(expected):
        raise ValueError("专业行动的完整输入或结果数量不一致")
    # 迟到候选先按完整版本硬拒绝，不能降级为模型可修复的行动不可用。
    _, scope = _require_action(context, action_key, snapshot=snapshot)
    _indexes({"item_indexes": indexes}, scope["item_indexes"])
    return deepcopy(state)


def prepare_authority_action(context, arguments):
    return _prepare_action(context, arguments, "authority", "reconcile_generation_authority")


def apply_authority_action(context, action, outputs):
    state = _apply_action(context, action, outputs, "authority", "reconcile_generation_authority")
    for index, item, output in zip(action["item_indexes"], action["items"], outputs, strict=True):
        # 专家提交边界已转换模型字段；父行动只复验规范结果，不能重复转换关系引用。
        normalized = validate_authority_reconciliation_output(prepared=item, output=output)
        record = _record(item, normalized, index)
        state["authority_records"][index] = record
        state["authority_cache"][record["input_hash"]] = record
    if not _pending(state["authority_records"]):
        result = merge_authority_reconciliation(_isolated(context), {
            "authoritative_facts": state["planning"]["source_semantics"]["authoritative_facts"],
            "prepared_items": state["authority_inputs"], "reconciliation_records": state["authority_records"],
        })
        child = _planning_context(context, state)
        planning.apply_source_authority(child, result)
        state["planning"] = child.action_state
        state["effective_facts"] = result
    _save(context, state)
    return observe_generation_state(context)


def _prepare_pending_action(context, action_key, prepare, arguments):
    """协调者选择行动，平台从当前状态派发全部独立待处理项，避免模型手写索引造成串行和漏项。"""
    _, scope = _require_action(context, action_key)
    return prepare(context, {**arguments, "item_indexes": list(scope["item_indexes"])})


async def reconcile_generation_authority(context, arguments):
    action = _prepare_pending_action(context, "reconcile_generation_authority", prepare_authority_action, {})
    outputs = await context.invoke_agents(agent_key="test_authority_reconciliation_reviewer", inputs=deepcopy(action["items"]),
                                         item_postprocessor="testing.postprocess_authority_reconciliation_item")
    return apply_authority_action(context, action, outputs)


def revise_generation_layout(context, arguments):
    current, scope = _require_action(context, "revise_generation_layout")
    return _apply_generation_layout(context, current, scope, arguments)


def _apply_generation_layout(context, current, scope, arguments):
    """布局入口与真实历史预检回归共用同一校验和原子状态提交。"""
    state = deepcopy(current)
    before_acceptance = state["accepted_plan"] is None
    if before_acceptance:
        if arguments.get("problem_ids") != [] or "preflight" not in scope:
            raise OutputValidationError("计划验收前调整布局必须对应当前生成预检，problem_ids 使用空数组")
        problems = []
    else:
        problems = _feedback(state, arguments.get("problem_ids"))
        if any(item["problem_id"] not in scope["problem_ids"] for item in problems):
            raise OutputValidationError("当前用例内的差异应在原批次修复，无需修改生成布局", details=scope)
    changes = arguments.get("changes")
    layout = deepcopy(state["generation_layout"])
    if not isinstance(changes, list) or not changes:
        raise OutputValidationError("changes 必须包含待调整模块的完整分组")
    indexes = [item.get("module_index") for item in changes if isinstance(item, dict)]
    if len(indexes) != len(changes) or len(set(indexes)) != len(indexes):
        raise OutputValidationError("布局修改不能重复或缺少 module_index")
    if layout is None:
        if set(indexes) != set(scope["module_indexes"]):
            raise OutputValidationError("首次布局必须完整提交当前全部模块", details={"module_indexes": scope["module_indexes"]})
        layout = [None] * len(scope["module_indexes"])
    for change in changes:
        index = change["module_index"]
        if type(index) is not int or index not in scope["module_indexes"]:
            raise OutputValidationError("布局修改引用未知模块", details=scope)
        layout[index] = deepcopy(change)
    if layout == state["generation_layout"]:
        raise OutputValidationError("布局未发生改变，不能用相同输入重新抽取结果")
    previous = action_state_hash(state["generation_inputs"])
    previous_layout_hash = action_state_hash(state["generation_layout"])
    state["generation_layout"] = layout
    if before_acceptance:
        # 用正式分批器验收同一布局，尚未接受规划时不提前启动生成或保存候选。
        resolved = planning._resolved_output(_planning_context(context, state))
        _generation_inputs_for_plan(context, state, resolved)
    else:
        _prepare_generation(context, state)
    state["layout_version"] += 1
    state["revision_history"].append({"action": "revise_generation_layout", "previous_input_hash": previous,
                                       "current_input_hash": action_state_hash(state["generation_inputs"]),
                                       "previous_layout_hash": previous_layout_hash,
                                       "current_layout_hash": action_state_hash(layout),
                                       "before_plan_acceptance": before_acceptance,
                                       "problem_ids": [item["problem_id"] for item in problems]})
    _save(context, state)
    return observe_generation_state(context)


def _prepare_review(context, state):
    generation = merge_grounded_generation_batches(_isolated(context), {
        "generation_inputs": state["generation_inputs"], "generation_records": state["generation_records"],
        "case_budget": context.action_input["case_budget"],
    })
    state["generation"] = generation
    state["generation_version"] += 1
    items = prepare_final_review_batches(_isolated(context), {
        "generation_inputs": state["generation_inputs"], "generation": generation,
        "batch_case_limit": context.action_input["batch_case_limit"],
    })["items"]
    state["review_inputs"] = items
    state["review_records"] = []
    for index, item in enumerate(items):
        identity = review.review_input_identity(item, generation_inputs=state["generation_inputs"],
                                               authoritative_facts=state["effective_facts"]["authoritative_facts"])
        record = state["review_cache"].get(identity)
        state["review_records"].append({**deepcopy(record), "item_index": index} if record else None)
    state["review"] = None
    if not _pending(state["review_records"]):
        _initialize_review(context, state)


def _generation_request(item, guidance):
    """按真实事实目录的明确列表顺序构建请求，不使用映射的恢复迭代顺序。"""
    request = deepcopy(item)
    contract = request["case_fact_contract"]
    available_fact_ids = contract["available_fact_ids"]
    routes = contract["fact_design_item_ids"]
    if (available_fact_ids != [fact["fact_id"] for fact in request["authoritative_facts"]]
            or set(routes) != set(available_fact_ids)):
        raise ValueError("生成请求事实路由与当前批次事实目录不一致")
    contract["design_support_requirements"] = [
        {"test_design_item_id": design_id,
         "support_fact_ids": [fact_id for fact_id in available_fact_ids if design_id in routes[fact_id]]}
        for design_id in contract["required_test_design_item_ids"]
    ]
    if guidance:
        request["_planning_feedback"] = {"instruction": guidance, "source": "协调者建议，不是新增事实或预算"}
    return request


def prepare_generation_action(context, arguments):
    snapshot = _state_snapshot(context)
    state = snapshot[0]
    guidance = arguments.get("guidance") or ""
    if not isinstance(guidance, str):
        raise OutputValidationError("guidance 必须是中文问题说明")
    action = _prepare_action(context, arguments, "generation", "generate_cases", snapshot=snapshot)
    action["guidance"] = guidance
    action["requests"] = []
    for item in action["items"]:
        request = _generation_request(item, guidance)
        request_hash = action_state_hash(request)
        if request_hash in state["generation_requests"]:
            raise OutputValidationError("该生成请求已有处理记录，需根据实际问题调整规划/布局或提供新的具体修正建议")
        action["requests"].append(request)
    return action


@dataclass(frozen=True)
class _GenerationSessionFailure:
    """只允许运行时局部额度异常进入该分支，模型不能自行声明专业会话失败。"""

    error: AgentSubtreeRequestBudgetExceeded

    def observation(self):
        diagnostic = dict(getattr(self.error, "diagnostic", None) or {})
        observations = deepcopy(diagnostic.get("tool_observations", []))
        feedback = {item.get("tool_call_id"): item for item in observations if item.get("tool_call_id")}
        calls = getattr(self.error, "tool_calls", [])
        return {
            "result_type": "specialist_failure", "error_type": type(self.error).__name__,
            "message": str(self.error), "budget_usage": deepcopy(diagnostic.get("budget_usage", {})),
            "tool_observations": observations,
            "submission_attempts": [
                {**{key: deepcopy(call[key]) for key in ("tool_key", "tool_call_id", "arguments") if key in call},
                 "feedback": deepcopy(feedback.get(call.get("tool_call_id"))),
                 "evidence_role": "提交尝试，不能据此认定候选已验收"}
                for call in calls
                if str(call.get("tool_key", "")).startswith("submit_")
            ],
            # SDK 仅在咨询结果完成提取与后处理后登记 agent:* 的 result。
            "consultation_results": [
                {key: deepcopy(call[key]) for key in ("tool_key", "tool_call_id", "input_hash", "result") if key in call}
                for call in calls if str(call.get("tool_key", "")).startswith("agent:") and "result" in call
            ],
            "evidence_role": "专业会话的运行记录与咨询建议，不是新增事实、合格用例或必须重规划的结论",
        }


def apply_generation_action(context, action, outputs):
    state = _apply_action(context, action, outputs, "generation", "generate_cases")
    prepared = prepare_generation_action(context, {"item_indexes": action["item_indexes"], "guidance": action["guidance"]})
    if action != prepared:
        raise ValueError("生成行动的专业请求与当前完整输入不一致")
    for index, item, request, output in zip(action["item_indexes"], action["items"], action["requests"], outputs, strict=True):
        request_hash = action_state_hash(request)
        failure = isinstance(output, _GenerationSessionFailure)
        state["generation_requests"][request_hash] = {
            "input_hash": action_state_hash(item),
            "result_type": "specialist_failure" if failure else output.get("result_type", "generation"),
        }
        if failure:
            _problem(state, origin="generation", input_hash=action_state_hash(item), payload=output.observation(),
                     fact_ids=[fact["fact_id"] for fact in item["authoritative_facts"]], batch_indexes=[index])
        elif output.get("result_type") == "planning_feedback":
            normalized = validate_generation_scope_feedback(item, output)
            _problem(state, origin="generation", input_hash=action_state_hash(item), payload=normalized,
                     fact_ids=[fact["fact_id"] for fact in item["authoritative_facts"]], batch_indexes=[index])
        else:
            # SDK 已使用同一完整后处理器验收；保留其标准化产物，不能把问题报告加入成功缓存。
            normalized = _validate_generation_batch_output(source_input=item, output=output)
            record = _record(item, normalized, index)
            state["generation_records"][index] = record
            state["generation_cache"][record["input_hash"]] = record
    if not _pending(state["generation_records"]):
        _prepare_review(context, state)
    _save(context, state)
    return observe_generation_state(context)


async def generate_cases(context, arguments):
    action = _prepare_pending_action(context, "generate_cases", prepare_generation_action,
                                     {"guidance": arguments.get("guidance", "")})
    # 各专业会话共享本次已通过的候选索引；回调无 await，校验和登记在同一事件循环内完成。
    # 旧缓存也会经过 candidate_validator 复验，不能跳过当前其他批次的重复约束。
    accepted_cases = {index: record["output"]["test_cases"]
                      for index, record in enumerate(context.action_state["generation_records"]) if record}

    def validate_candidate(index, output):
        validate_generation_case_uniqueness(output["test_cases"],
            [case for other, cases in accepted_cases.items() if other != index for case in cases])
        accepted_cases[index] = deepcopy(output["test_cases"])

    results = await context.invoke_agents(
        agent_key="test_case_generator", inputs=deepcopy(action["requests"]),
        item_postprocessor="testing.postprocess_generation_batch_item",
        candidate_validators=[partial(validate_candidate, index) for index in action["item_indexes"]],
        alternate_output_validators=[{"planning_feedback": partial(validate_generation_scope_feedback, item)}
                                     for item in action["items"]],
        return_subtree_failures=True,
    )
    # 独立批次保持输入顺序验收；局部额度失败仍交回协调者，不能伪装成生成成功。
    outputs = [_GenerationSessionFailure(value) if isinstance(value, AgentSubtreeRequestBudgetExceeded) else value
               for value in results]
    return apply_generation_action(context, action, outputs)


def _initialize_review(context, state):
    source = {"generation": state["generation"], "generation_inputs": state["generation_inputs"],
              "authoritative_facts": state["effective_facts"]["authoritative_facts"],
              "review_inputs": state["review_inputs"], "review_records": state["review_records"],
              "case_budget": context.action_input["case_budget"]}
    identity = action_state_hash(source)
    child = review.compose_review_context(context, input_payload=source, state=state["review_domains"].get(identity))
    review.initialize_review_state(child, source)
    state["review"] = {"input": deepcopy(source), "state": child.action_state}
    state["review_domains"][identity] = deepcopy(child.action_state)


def prepare_initial_review_action(context, arguments):
    return _prepare_action(context, arguments, "review", "review_generated_cases")


def apply_initial_review_action(context, action, outputs):
    state = _apply_action(context, action, outputs, "review", "review_generated_cases")
    for index, item, output in zip(action["item_indexes"], action["items"], outputs, strict=True):
        normalized = postprocess_final_review_batch_item(_isolated(context), {"item_input": item, "item_output": output})
        identity = review.review_input_identity(item, generation_inputs=state["generation_inputs"],
                                               authoritative_facts=state["effective_facts"]["authoritative_facts"])
        record = {"item_index": index, "input_hash": identity, "output": normalized}
        state["review_records"][index] = record
        state["review_cache"][identity] = record
    if not _pending(state["review_records"]):
        _initialize_review(context, state)
    _save(context, state)
    return observe_generation_state(context)


async def review_generated_cases(context, arguments):
    action = _prepare_pending_action(context, "review_generated_cases", prepare_initial_review_action, {})
    outputs = await context.invoke_agents(agent_key="test_generation_final_reviewer", inputs=deepcopy(action["items"]),
                                         item_postprocessor="testing.postprocess_final_review_batch_item")
    return apply_initial_review_action(context, action, outputs)


async def _review_action(context, arguments, action_key, callback):
    current, scope = _require_action(context, action_key)
    if "batch_ids" in scope:
        # 模型选择修复或复审，领域层派发当前全部待办，避免逐个复述批次和完整状态。
        arguments = {"batch_ids": list(scope["batch_ids"])}
    state = deepcopy(current)
    child = _review_context(context, state)
    import inspect
    result = callback(child, arguments)
    if inspect.isawaitable(result):
        result = await result
    state["review"]["state"] = child.action_state
    state["review_domains"][action_state_hash(state["review"]["input"])] = deepcopy(child.action_state)
    _save(context, state)
    return observe_generation_state(context)


async def read_review_evidence(context, arguments):
    state = _mutable(context, "read_review_evidence")
    child = _review_context(context, state)
    result = review.read_review_evidence(child, arguments)
    state["review"]["state"] = child.action_state
    state["review_domains"][action_state_hash(state["review"]["input"])] = deepcopy(child.action_state)
    _save(context, state)
    return result


async def repair_review_batch(context, arguments):
    return await _review_action(context, arguments, "repair_review_batch", review.repair_review_batch)


async def recheck_review_batch(context, arguments):
    return await _review_action(context, arguments, "recheck_review_batch", review.recheck_review_batch)


async def review_all_cases(context, arguments):
    return await _review_action(context, arguments, "review_all_cases", review.review_all_cases)


def _resolved_output(context):
    state = _state(context)
    if state["accepted_plan"] is None or state["effective_facts"] is None or _pending(state["generation_records"]):
        raise OutputValidationError("当前规划、权威判断或生成范围尚未全部验收")
    plan = planning._resolved_output(_planning_context(context, state))
    if plan != state["accepted_plan"]:
        raise ValueError("当前交付规划与已接受版本不一致")
    # 重算当前输入链，防止旧批准与新规划/分批混用。
    candidate = deepcopy(state)
    _prepare_generation(context, candidate)
    if candidate["generation_inputs"] != state["generation_inputs"] or candidate["effective_facts"] != state["effective_facts"]:
        raise ValueError("生成输入没有对应当前规划和权威版本")
    review_input = (state["review"] or {}).get("input") or {}
    if any(review_input.get(key) != expected for key, expected in (
        ("generation", state["generation"]), ("generation_inputs", state["generation_inputs"]),
        ("authoritative_facts", state["effective_facts"]["authoritative_facts"]),
    )):
        raise ValueError("终审依据与当前完整生成输入和事实版本不一致")
    reviewed = review.resolve_review_output(_review_context(context, state))
    return {"plan": plan["plan"], "source_semantics": plan["source_semantics"],
            "authoritative_facts": deepcopy(state["effective_facts"]["authoritative_facts"]), **reviewed}


def finish_generation(context, arguments):
    state = _mutable(context, "finish_generation")
    resolved = _resolved_output(context)
    state["delivery"] = _build_delivery(context, resolved)
    state["completed"] = True
    _save(context, state)
    return {"completed": True}


def _build_delivery(context, resolved):
    """在同一终止行动内校验当前版本，交付产物随状态原子保存。"""
    from .test_generation_workflow import (
        approve_synthesized_test_cases, validate_generated_test_cases, persist_generated_test_cases,
    )
    from .test_generation_batching import (
        prepare_execution_chain_context, select_execution_chain, validate_execution_chain,
    )

    child = _isolated(context)
    evidence = context.action_state["source"]["evidence"]
    approved = approve_synthesized_test_cases(child, {
        **resolved, "case_budget": context.action_input["case_budget"],
    })
    validated = validate_generated_test_cases(child, {
        "requirement": evidence["requirement"], "case_budget": context.action_input["case_budget"],
        "test_cases": approved["test_cases"],
    })
    chains = prepare_execution_chain_context(child, {"plan": resolved["plan"], "test_cases": validated["test_cases"]})
    selection = select_execution_chain(child, chains)
    execution = validate_execution_chain(child, {"test_cases": validated["test_cases"], "chain_selection": selection})
    result = persist_generated_test_cases(child, {
        "requirement": evidence["requirement"], "evidence_source": evidence["source"],
        "test_cases": validated["test_cases"], "case_fact_bindings": approved["case_fact_bindings"],
        "execution_plan": execution["execution_plan"], "final_review": approved["final_review"],
    })
    return {"input_sha256": action_state_hash(context.action_input),
            "resolved_sha256": action_state_hash(resolved), "result": result,
            "artifacts": {key: deepcopy(value) for key, value in child.artifacts.items()
                          if key in {"test_generation", "test_generation_final_review", "execution_chain_candidates",
                                     "execution_chain_selection", "execution_plan_validation"}}}


def managed_generation_artifacts(context, arguments):
    """受管专家只读取持久来源投影，不依赖前置节点或内存中的临时产物。"""
    from .test_generation_source_agent import hydrate_source_context

    return hydrate_source_context(context).artifacts


def _delivery_content(delivery):
    """执行尝试身份属于交付归属，不参与同一目标下业务内容的等价判断。"""
    content = deepcopy(delivery)
    content["artifacts"]["test_generation"].pop("run_id")
    return content


def validate_generation_resolution(context, arguments):
    state = _state(context)
    if arguments.get("output") != {"completed": True} or not state["completed"]:
        raise OutputValidationError("必须通过 finish_generation 才能交付当前目标")
    if action_state_hash(arguments.get("input_payload")) != state["input_sha256"]:
        raise ValueError("交付验收输入与当前持久输入不一致")
    resolved = _resolved_output(context)
    delivery = state["delivery"]
    current_delivery = _build_delivery(context, resolved)
    if delivery is None or _delivery_content(delivery) != _delivery_content(current_delivery):
        raise ValueError("交付产物与当前完整验收版本不一致")
    # 终止回执重放不再执行工具，必须从已提交状态恢复来源和最终产物。
    context.artifacts.update(deepcopy(state["source"]["artifacts"]))
    context.artifacts.update(deepcopy(current_delivery["artifacts"]))
    context.artifacts["context_retrievals"] = deepcopy(state["context_retrievals"])
    return deepcopy(current_delivery["result"])
