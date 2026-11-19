"""只将结合来源定向复核后仍无答案的业务决定交给用户。"""

from copy import deepcopy
from datetime import datetime, timedelta

from .action_journal import action_state_hash


class ConflictConfirmationRequired(BaseException):
    """控制流暂停，不作为工具失败、重试或模型修复反馈。"""

    def __init__(self, items):
        self.items = items
        super().__init__("存在待用户明确的事项")


def pending_conflicts(state):
    """整轮初审和未决事项的定向复核完成前，不暂停任务。"""
    planning = state.get("planning", {})
    if not planning or not planning.get("gap_records") or any(
            record is None for record in planning["gap_records"]):
        return []
    from .test_generation_planning_agent import _current_support_review

    reviewed_inputs = {(revision["input"]["module_index"], decision["test_design_item_index"]): revision["input"]
                       for revision in planning["route_review_revisions"]
                       for decision in revision["output"]["decisions"]}
    confirmed = state.get("conflict_confirmations", {})
    items = {}
    for index in range(len(planning["gap_records"])):
        review = _current_support_review(planning, index)
        for decision in review.get("decisions", []):
            reviewed_input = reviewed_inputs.get((review["module_index"], decision["test_design_item_index"]))
            for gap in decision.get("support_gaps", []):
                ids = sorted(set(gap.get("evidence_fact_ids") or decision.get("fact_ids") or []))
                # 同一组事实可以有多个独立疑问，不能只按事实编号合并而漏掉问题。
                identity = action_state_hash({
                    "kind": gap["kind"], "fact_ids": ids,
                    "coverage_fragment": gap["coverage_fragment"], "reason": gap["reason"],
                })
                if identity in confirmed:
                    continue
                # 草案自行扩大范围应先修正；初审发现的缺口仍可能由相关来源解释。
                if gap["kind"] == "draft_revision" or reviewed_input is None:
                    return []
                if identity in items:
                    continue
                facts = {fact["fact_id"]: fact for fact in reviewed_input["candidate_facts"]}
                items[identity] = {
                    "id": identity, "kind": gap["kind"],
                    "reason": gap["reason"],
                    "coverage_fragment": gap["coverage_fragment"], "fact_ids": ids,
                    "facts": [{"fact_id": key, "assertion": facts[key]["assertion"],
                               "source_context": facts[key].get("source_context", {})}
                              for key in ids],
                }
                if gap["kind"] == "source_conflict":
                    # 人工确认必须保留已验收的双方原文与比较依据，不能只留下模型标签。
                    items[identity]["conflict"] = deepcopy(gap["conflict"])
    return list(items.values())


def require_conflict_confirmation(state):
    items = pending_conflicts(state)
    if items:
        raise ConflictConfirmationRequired(items)


def apply_conflict_decision(approval, node, run, answers, *, now):
    """原子保存整张表单及运行补充口径，不改写原始文档或自动放行验收。"""
    sdk = dict(node.sdk_state or {})
    header = sdk.get("action_checkpoint")
    if not isinstance(header, dict) or header.get("version") != 3 or header.get("storage") != "artifact-journal-v1":
        raise ValueError("旧动作检查点需要显式迁移后才能应用确认结果")
    request = approval.request_payload
    items = request.get("items", [])
    expected = {item["id"] for item in items}
    if not items or set(answers) != expected or any(
            not isinstance(value, str) or not value.strip() or len(value) > 4000
            for value in answers.values()):
        raise ValueError("请逐项填写全部待明确事项的确认口径，每项不超过 4000 字")
    if header["state_hash"] != request["state_hash"]:
        raise ValueError("任务状态已变化，请刷新待确认表单")
    from sqlalchemy.orm import object_session
    from .checkpoint_storage import hydrate_checkpoint_state, replace_checkpoint_state

    db = object_session(node)
    if db is None or object_session(run) is not db:
        raise ValueError("确认结果必须在同一运行事务内应用")
    # 只有用户决定确实修改领域时才按引用加载状态；预算与展示路径不做 hydration。
    state = hydrate_checkpoint_state(db, node, checkpoint=header)
    confirmations = state.setdefault("conflict_confirmations", {})
    for item in items:
        confirmations[item["id"]] = {**deepcopy(item), "resolution": answers[item["id"]].strip()}
    if state.get("planning", {}).get("source_authority") is not None:
        # 用户答复是新的业务依据；保留审计回执及已用预算，不能沿用答复前的来源裁决或候选缓存。
        from core.db.model_defs import AgentNodeRun
        from .input_snapshots import restore_node_input
        from .registry import ToolExecutionContext
        from . import test_generation_agent as generation
        from . import test_generation_planning_agent as planning

        context = ToolExecutionContext(
            db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key=node.node_key, run_input=run.input_payload,
            action_input=restore_node_input(node, lambda key: db.get(AgentNodeRun, key)),
            artifacts=deepcopy((run.run_context or {}).get("artifacts") or {}), action_state=state,
            confirmed_conflicts=deepcopy(confirmations),
        )
        child = generation._planning_context(context, state)
        state["authority_inputs"] = planning.clear_source_authority(child)
        state["planning"] = child.action_state
        generation._invalidate_downstream(state)
        for key in ("authority_cache", "generation_cache", "review_cache", "review_domains", "generation_requests"):
            state[key] = {}
    from .test_generation_agent import refresh_generation_state

    refresh_generation_state(state)
    replace_checkpoint_state(db, run, node, state, checkpoint=header)
    # 用户等待时间不消耗执行截止时间；模型预算和已完成回执保持原值。
    context = dict(run.run_context or {})
    paused_at = datetime.fromisoformat(request["paused_at"])
    elapsed = max(0, (now - paused_at).total_seconds())
    updated_sdk = dict(node.sdk_state or {})
    updated_sdk["confirmation_wait_seconds"] = sdk.get("confirmation_wait_seconds", 0) + elapsed
    node.sdk_state = updated_sdk
    for key in ("deadline_at", "stage_deadline_at"):
        if context.get(key):
            context[key] = (datetime.fromisoformat(context[key]) + timedelta(seconds=elapsed)).isoformat()
    context["confirmation_wait_seconds"] = context.get("confirmation_wait_seconds", 0) + elapsed
    run.run_context = context


def confirmation_instructions(confirmations):
    if not confirmations:
        return ""
    import json

    return ("\n\n以下为当前用户逐项明确的本次运行补充口径，包括来源冲突、缺失依据和范围歧义。"
            "在对应事项范围内补充原来源未明确的规则，并优先于相互矛盾的来源表述；保留原始来源追溯。"
            "先结合全部确认结果统一修正草案和规划，不能逐个问题修改后反复重建整套规划。"
            "不得把同一已确认事项换个问法再次作为未决问题；新的独立疑问仍须如实报告。"
            "确认不等于自动批准原意图，必须使实际覆盖目标符合用户明确的适用条件和处理规则。\n"
            + json.dumps(list(confirmations.values()), ensure_ascii=False))
