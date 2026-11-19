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
    """只展示主 Agent 显式提交的当前待人工决定问题。"""
    if not state or not state.get("confirmation_requested"):
        return []
    if state.get("schema_version") != 4:
        raise ValueError("人工确认只接受当前生成状态，不恢复旧规划协议")
    evidence = {item["evidence_id"]: item for item in state["evidence_catalog"]["items"]}
    coverage = {item["coverage_id"]: item["objective"] for item in state["coverage_plan"]["items"]}
    items = []
    for identity in state["confirmation_requested"]:
        issue = state["open_issues"].get(identity)
        if issue is None or issue["status"] != "needs_human":
            raise ValueError("人工确认请求与当前未决问题不一致")
        facts = []
        for ref in issue["source_refs"]:
            original = evidence[ref["evidence_id"]]
            assertion = ref["quote"] or f"第{original.get('page_number')}页原图；请核对图像来源。"
            facts.append({"fact_id": ref["evidence_id"], "assertion": assertion,
                          "source_context": {key: deepcopy(original[key]) for key in
                              ("document_id", "page_number", "block_ids", "image_region") if key in original}})
        scope = [coverage[key] for key in issue["coverage_ids"]]
        if not scope:
            scope = [state["cases"]["items"][key]["content"]["title"] for key in issue["target_case_ids"]]
        items.append({"id": identity, "kind": issue["kind"], "reason": issue["reason"],
                      "coverage_fragment": "；".join(scope) or "当前需求规则",
                      "fact_ids": list(dict.fromkeys(ref["evidence_id"] for ref in issue["source_refs"])),
                      "facts": facts, "source_refs": deepcopy(issue["source_refs"]),
                      "target_case_ids": list(issue["target_case_ids"]), "coverage_ids": list(issue["coverage_ids"])})
    return items


def require_conflict_confirmation(state):
    items = pending_conflicts(state)
    if items:
        raise ConflictConfirmationRequired(items)


def apply_conflict_decision(approval, node, run, answers, *, now):
    """原子保存整张表单及运行补充口径，不改写原始文档或自动放行验收。"""
    if (node is None or node.run_id != run.id or approval.run_id != run.id
            or approval.node_run_id != node.id or approval.status != "pending"):
        raise ValueError("人工确认与当前运行、节点或待处理批准不一致")
    sdk = dict(node.sdk_state or {})
    header = sdk.get("action_checkpoint")
    if not isinstance(header, dict) or header.get("version") != 3 or header.get("storage") != "artifact-journal-v1":
        raise ValueError("动作检查点协议不受支持，不能应用确认结果")
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
    if items != pending_conflicts(state):
        raise ValueError("待确认表单内容与当前已请求问题不一致")
    from .test_generation_agent import apply_user_decisions

    apply_user_decisions(
        state, items, answers, approval_id=approval.id, node_id=node.id,
        run_id=run.id, user_id=run.user_id, decided_at=now,
    )
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
            "按确认范围修改相关覆盖项和用例，保留其他已完成内容，再提交独立评审。"
            "不得把同一已确认事项换个问法再次作为未决问题；新的独立疑问仍须如实报告。"
            "确认不等于自动批准原意图，必须使实际覆盖目标符合用户明确的适用条件和处理规则。\n"
            + json.dumps(list(confirmations.values()), ensure_ascii=False))
