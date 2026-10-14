"""分批与全局终审的自主行动：模型选择行动，领域代码维护版本和验收边界。"""

from __future__ import annotations

from copy import copy, deepcopy
from functools import partial
import hashlib
import json
from typing import Any, TYPE_CHECKING

from core.db.model_defs import AgentRun

from .output_repair import OutputValidationError
from .sources import persisted_source_snapshot
from .test_generation_facts import required_text
from .test_generation_review import (
    _deduplicate_design_items,
    _difference_identity,
    _index_generation_inputs,
    merge_final_review_batches,
    merge_final_review_recheck_records,
    merge_final_review_repairs,
    plan_final_review_repairs,
    postprocess_final_review_batch_item,
    postprocess_global_final_review_output,
    prepare_final_review_rechecks,
    prepare_global_final_review,
    refresh_final_review_input,
    route_final_review_differences,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


_STATE_VERSION = 3
_EVIDENCE_MAX_CHARS = 4000


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _isolated_context(context: ToolExecutionContext) -> ToolExecutionContext:
    # 复用旧确定性处理器，但不让它们的轮次投影影响真实行动账本。
    isolated = copy(context)
    isolated.artifacts = {}
    isolated.executed_tools = []
    isolated.tool_calls = []
    return isolated


def compose_review_context(
    parent: ToolExecutionContext,
    *,
    input_payload: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> ToolExecutionContext:
    """把终审领域装入父行动的独立状态视图，不启动另一层协调 Agent。

    专业 Agent 调用仍由父行动管理；调用方仅在整个父行动成功后采纳
    返回上下文的 action_state，失败时丢弃视图即可保持父状态原子性。
    """
    context = _isolated_context(parent)
    context.action_input = {}
    context.action_state = deepcopy(state) if state is not None else {}
    initialize_review_state(context, input_payload)
    return context


def review_input_identity(
    item_input: dict[str, Any],
    *,
    generation_inputs: list[dict[str, Any]],
    authoritative_facts: list[dict[str, Any]],
) -> str:
    """复用审查结论必须命中完整审查输入及其生成、来源事实身份。"""
    generation_by_id = _index_generation_inputs(generation_inputs)
    batch_ids = item_input["review_batch"]["generation_batch_ids"]
    if not batch_ids or len(set(batch_ids)) != len(batch_ids):
        raise ValueError("终审身份需要不重复的真实生成批次范围")
    source_inputs = [generation_by_id[batch_id] for batch_id in batch_ids]
    facts_by_id = {
        required_text(fact.get("fact_id"), "fact_id"): fact for fact in authoritative_facts
    }
    if len(facts_by_id) != len(authoritative_facts):
        raise ValueError("终审身份的完整事实目录存在重复 ID")
    relevant_facts = {}
    for source in source_inputs:
        for fact in source["authoritative_facts"]:
            fact_id = required_text(fact.get("fact_id"), "fact_id")
            if facts_by_id.get(fact_id) != fact:
                raise ValueError(f"终审生成范围与完整权威事实版本不一致: fact_id={fact_id}")
            relevant_facts[fact_id] = fact
    return _sha256({
        "review_input": item_input,
        "generation_inputs": source_inputs,
        "authoritative_facts": relevant_facts,
    })


def _audit(context: ToolExecutionContext, generation: dict[str, Any]) -> dict[str, Any]:
    # 注册表启动时会载入领域模块，因此运行时导入审计函数以避免循环导入。
    from .test_generation_workflow import build_generation_audit_summary

    source = context.action_input
    return build_generation_audit_summary(_isolated_context(context), {
        "generation": generation,
        "generation_inputs": source["generation_inputs"],
        "authoritative_facts": source["authoritative_facts"],
        "case_budget": source["case_budget"],
    })


def _review_goal(context: ToolExecutionContext) -> dict[str, Any]:
    """目标只取自完整输入和本次运行的来源快照，不能由模型改写验收条件。"""
    run = context.db.get(AgentRun, context.run_id)
    if run is None or run.project_id != context.project_id or run.user_id != context.user_id:
        raise ValueError("自主终审无法取得当前用户和项目的运行来源")
    snapshot = persisted_source_snapshot(run)
    if snapshot is None:
        raise ValueError("自主终审的运行缺少不可变来源快照")
    source = context.action_input
    ids = set()
    for fact in source["authoritative_facts"]:
        fact_id = required_text(fact.get("fact_id"), "fact_id")
        if fact_id in ids:
            raise ValueError("自主终审的权威事实 ID 重复")
        ids.add(fact_id)
        anchor = fact["source_anchor"]
        if snapshot.kind == "knowledge_document":
            if (anchor.get("source_kind") != "document" or anchor.get("document_id") != snapshot.document_id
                    or anchor.get("asset_source_sha256") != snapshot.content_hash):
                raise ValueError("自主终审事实引用与运行文档来源版本不一致")
        elif anchor.get("source_kind") != "inline" or anchor.get("requirement_sha256") != snapshot.content_hash:
            raise ValueError("自主终审事实引用与运行内联来源版本不一致")
    return {
        "objective": "在保持既定用例和事实范围的前提下，完成当前版本的独立分批与全局终审",
        "input_sha256": _sha256(source), "source_ref": snapshot.to_dict(),
        "fact_catalog_sha256": _sha256(source["authoritative_facts"]),
        "case_count": len(source["generation"]["test_cases"]), "case_budget": source["case_budget"],
        "batch_ids": [item["review_batch"]["batch_id"] for item in source["review_inputs"]],
        "acceptance": ["当前版本确定性审计通过", "所有修改均经独立分批复审且无未解决差异", "当前版本独立全局审查通过"],
    }


def _evidence_text(context: ToolExecutionContext, fact_id: str | None) -> str:
    facts = context.action_input["authoritative_facts"]
    if fact_id is None:
        value = [{"fact_id": fact["fact_id"], "scope_id": fact["scope_id"], "status": fact["status"]}
                 for fact in facts]
    else:
        value = next((fact for fact in facts if fact["fact_id"] == fact_id), None)
        if value is None:
            raise OutputValidationError("取证只能引用本次输入的真实事实 ID", details={"fact_id": fact_id})
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _evidence_read_record(context: ToolExecutionContext, fact_id: str | None, record: dict[str, Any]) -> None:
    """持久引用必须仍能还原同一事实，已读区间不能越界或凭恢复扩大。"""
    text = _evidence_text(context, fact_id)
    if (not isinstance(record, dict) or record.get("sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest()
            or record.get("total_chars") != len(text) or not isinstance(record.get("ranges"), list)):
        raise ValueError("持久取证引用与完整原始输入不一致")
    last_end = -1
    for span in record["ranges"]:
        if (not isinstance(span, list) or len(span) != 2 or any(type(value) is not int for value in span)
                or not 0 <= span[0] < span[1] <= len(text) or span[0] <= last_end):
            raise ValueError("持久取证读取区间无效")
        last_end = span[1]


def _state(context: ToolExecutionContext) -> dict[str, Any]:
    state = context.action_state
    if not isinstance(state, dict) or state.get("schema_version") != _STATE_VERSION:
        raise ValueError("自主终审状态未初始化或版本不受支持")
    if state.get("input_sha256") != _sha256(context.action_input):
        raise ValueError("自主终审的原始输入与持久状态不一致")
    if state.get("generation_sha256") != _sha256(state["generation"]):
        raise ValueError("自主终审当前用例内容与版本指纹不一致")
    if state.get("goal") != _review_goal(context):
        raise ValueError("自主终审持久目标或来源版本与当前输入不一致")
    reads = state.get("evidence_reads")
    if not isinstance(reads, dict) or set(reads) != {"catalog", "facts"} or not isinstance(reads["facts"], dict):
        raise ValueError("自主终审持久取证记录无效")
    if reads["catalog"] is not None:
        _evidence_read_record(context, None, reads["catalog"])
    for fact_id, record in reads["facts"].items():
        _evidence_read_record(context, fact_id, record)
    return state


def _review_inputs(context: ToolExecutionContext, state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        refresh_final_review_input(
            item, generation=state["generation"],
            generation_inputs=context.action_input["generation_inputs"],
        )
        for item in context.action_input["review_inputs"]
    ]


def _batch_index(context: ToolExecutionContext, batch_id: str) -> int:
    for index, item in enumerate(context.action_input["review_inputs"]):
        if item["review_batch"]["batch_id"] == batch_id:
            return index
    raise OutputValidationError(
        f"自主终审行动引用未知批次: batch_id={batch_id}",
        details={"batch_id": batch_id, "known_batch_ids": [
            item["review_batch"]["batch_id"] for item in context.action_input["review_inputs"]
        ]},
    )


def _action_batch_id(arguments: dict[str, Any]) -> str:
    value = arguments.get("batch_id")
    if not isinstance(value, str) or not value.strip():
        raise OutputValidationError("batch_id 必须是观察摘要中给出的非空批次 ID", details={"field": "batch_id"})
    return value.strip()


def initialize_review_state(
    context: ToolExecutionContext, arguments: dict[str, Any],
) -> dict[str, Any]:
    """不可变来源保留在节点输入；检查点仅保存发生变化的领域状态。"""

    source = deepcopy(arguments)
    for key in ("generation_inputs", "review_inputs", "review_records", "authoritative_facts"):
        if not isinstance(source.get(key), list):
            raise ValueError(f"自主终审输入 {key} 必须是数组")
    if not isinstance(source.get("generation"), dict) or int(source.get("case_budget") or 0) < 1:
        raise ValueError("自主终审需要完整用例和有效用例预算")
    context.action_input = source
    review_inputs = source["review_inputs"]
    records = source["review_records"]
    if not review_inputs or len(review_inputs) != len(records):
        raise ValueError("自主终审初审输入与结果数量不一致")
    batch_ids = [required_text(item["review_batch"].get("batch_id"), "batch_id") for item in review_inputs]
    if len(set(batch_ids)) != len(batch_ids):
        raise ValueError("自主终审初审批次 ID 重复")
    case_ids = [required_text(case.get("case_id"), "case_id") for item in review_inputs for case in item["test_cases"]]
    generation_ids = [required_text(case.get("case_id"), "case_id") for case in source["generation"]["test_cases"]]
    if len(set(case_ids)) != len(case_ids) or len(set(generation_ids)) != len(generation_ids) or set(case_ids) != set(generation_ids):
        raise ValueError("自主终审初审范围必须完整且不重复地覆盖当前用例")
    generation_by_id = _index_generation_inputs(source["generation_inputs"])
    normalized_records = []
    for index, (item, record) in enumerate(zip(review_inputs, records, strict=True)):
        if not isinstance(record, dict) or record.get("item_index") != index or not isinstance(record.get("output"), dict):
            raise ValueError(f"自主终审初审记录顺序或内容无效: item_index={index}")
        refreshed = refresh_final_review_input(
            item, generation=source["generation"], generation_inputs=source["generation_inputs"],
        )
        expected_designs = _deduplicate_design_items([
            design
            for batch_id in item["review_batch"]["generation_batch_ids"]
            for design in generation_by_id[batch_id]["plan"]["test_design_items"]
        ])
        if item["test_design_items"] != expected_designs:
            raise ValueError("自主终审初审设计范围与当前生成规划不一致")
        if item["review_batch"]["case_ids"] != [case["case_id"] for case in item["test_cases"]]:
            raise ValueError("自主终审初审批次范围与候选用例不一致")
        if item != refreshed:
            raise ValueError("自主终审初审记录没有对应当前完整事实、候选与审计输入")
        review_input_identity(item, generation_inputs=source["generation_inputs"],
                              authoritative_facts=source["authoritative_facts"])
        normalized_records.append({
            "item_index": index,
            "output": postprocess_final_review_batch_item(_isolated_context(context), {
                "item_input": refreshed, "item_output": record["output"],
            }),
        })
    if context.action_state:
        return _state(context)
    state = {
        "schema_version": _STATE_VERSION,
        "input_sha256": _sha256(source),
        "generation_version": 0,
        "generation_sha256": _sha256(source["generation"]),
        "generation": deepcopy(source["generation"]),
        "review_records": normalized_records,
        "pending_rechecks": {},
        "repair_counts": {},
        "global_review": None,
        "reviewed_global_scope_hashes": [],
        "goal": _review_goal(context),
        "evidence_reads": {"catalog": None, "facts": {}},
        "reviewed_scope_hashes": {
            _scope_key(item): [_scope_sha256(source["generation"], [item])]
            for item in review_inputs
        },
        "audit_summary": _audit(context, source["generation"]),
        "completed": False,
    }
    context.action_state = state
    return state


def observe_review_state(
    context: ToolExecutionContext, arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """仅把可行动摘要给协调 Agent，完整事实由选定行动按范围取用。"""

    state = _state(context)
    batches = []
    for index, item in enumerate(_review_inputs(context, state)):
        batch_id = item["review_batch"]["batch_id"]
        review = state["review_records"][index]["output"]
        if batch_id in state["pending_rechecks"]:
            continue
        if review["approved"] and item["audit_summary"]["approved"]:
            continue
        differences = review["differences"]
        batches.append({
            "batch_id": batch_id,
            "case_ids": item["review_batch"]["case_ids"],
            "difference_count": len(differences),
            "categories": sorted({str(item["category"]) for item in differences}),
            "repair_scopes": sorted({str(item["repair_scope"]) for item in differences if item.get("repair_scope")}),
            "review_summary": review["summary"],
            "audit_approved": item["audit_summary"]["approved"],
            "repair_count": state["repair_counts"].get(batch_id, 0),
            "related_fact_ids": sorted({fact_id for difference in differences
                                        for fact_id in difference.get("related_fact_ids", [])}),
        })
    batch_ready = not batches and not state["pending_rechecks"] and state["audit_summary"]["approved"]
    global_review = state["global_review"]
    global_current = bool(global_review and global_review["generation_sha256"] == state["generation_sha256"])
    global_approved = global_current and global_review["output"]["approved"]
    visible_repeated = _global_scope_sha256(context, state["generation"]) in state["reviewed_global_scope_hashes"]
    return {
        "goal": deepcopy(state["goal"]),
        "evidence": {
            "fact_count": len(context.action_input["authoritative_facts"]),
            "read_fact_count": len(state["evidence_reads"]["facts"]),
            "read_fact_ids": list(state["evidence_reads"]["facts"]),
            "catalog_available": True,
            "instruction": "仅在需要核对来源时调用 read_review_evidence；fact_id=null 读取目录，具体 ID 读取原始事实。读取不代表审查通过。",
        },
        "generation_version": state["generation_version"],
        "case_count": len(state["generation"]["test_cases"]),
        "batch_count": len(state["review_records"]),
        "repairable_batches": batches,
        "repairable_batch_ids": [item["batch_id"] for item in batches],
        "recheck_batch_ids": sorted(state["pending_rechecks"]),
        "audit_approved": state["audit_summary"]["approved"],
        "audit_differences": deepcopy(state["audit_summary"]["differences"]),
        "global_review_status": (
            "pending" if global_review is None else "stale" if not global_current
            else "approved" if global_approved else "rejected"
        ),
        "global_difference_count": len(global_review["output"]["differences"]) if global_review else 0,
        "can_review_all_cases": batch_ready and not global_approved and not visible_repeated and not state["completed"],
        "can_finish": batch_ready and global_approved,
        "completed": state["completed"],
    }


def read_review_evidence(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """按完整输入引用分页取证，持久状态只保存指纹和区间，不复制证据正文。"""
    state = _state(context)
    if state["completed"]:
        raise OutputValidationError("自主终审已经完成，不能继续取证")
    fact_id, offset, max_chars = arguments.get("fact_id"), arguments.get("offset"), arguments.get("max_chars")
    if fact_id is not None and (not isinstance(fact_id, str) or not fact_id.strip()):
        raise OutputValidationError("fact_id 必须是当前事实 ID 或 null", details={"field": "fact_id"})
    if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= _EVIDENCE_MAX_CHARS:
        raise OutputValidationError("取证分页参数无效", details={"max_chars_limit": _EVIDENCE_MAX_CHARS})
    text = _evidence_text(context, fact_id)
    if offset >= len(text):
        raise OutputValidationError("取证起始位置超出完整证据范围", details={"total_chars": len(text)})
    end = min(offset + max_chars, len(text))
    updated = deepcopy(state)
    reads = updated["evidence_reads"]
    previous = reads["catalog"] if fact_id is None else reads["facts"].get(fact_id)
    ranges = [*(previous["ranges"] if previous is not None else []), [offset, end]]
    merged: list[list[int]] = []
    for start, stop in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    record = {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "total_chars": len(text), "ranges": merged}
    if fact_id is None:
        reads["catalog"] = record
    else:
        reads["facts"][fact_id] = record
    context.action_state = updated
    return {
        "fact_id": fact_id, "source_ref": deepcopy(state["goal"]["source_ref"]),
        "content_sha256": record["sha256"], "content_format": "json_fragment",
        "offset": offset, "end_offset": end, "total_chars": len(text),
        "content": text[offset:end], "next_offset": end if end < len(text) else None,
        "resource_fully_read": merged == [[0, len(text)]], "new_evidence_read": record != previous,
        "instruction": "这是原始证据的 JSON 文本片段；保留 status 和 source_anchor，不能把读取或引用视为审查通过。",
    }


def _action(batch_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"batch_id": batch_id, "items": items, "input_sha256": _sha256(items)}


def prepare_review_repair(
    context: ToolExecutionContext, arguments: dict[str, Any],
) -> dict[str, Any]:
    state = _state(context)
    batch_id = _action_batch_id(arguments)
    index = _batch_index(context, batch_id)
    if state["completed"] or batch_id in state["pending_rechecks"]:
        raise OutputValidationError(
            "该批次已修复待复审，或自主终审已经完成",
            details={"batch_id": batch_id, "completed": state["completed"],
                     "recheck_batch_ids": sorted(state["pending_rechecks"])},
        )
    source = _review_inputs(context, state)[index]
    record = {"item_index": 0, "output": state["review_records"][index]["output"]}
    plan = plan_final_review_repairs(_isolated_context(context), {
        "review_inputs": [source], "review_records": [record],
        "generation_inputs": context.action_input["generation_inputs"],
    }, repair_cycle=state["repair_counts"].get(batch_id, 0) + 1,
        cross_batch_difference_keys=frozenset(
            tuple(key) for key in (state["global_review"] or {}).get("routed_difference_keys", [])
        ),
    )
    if not plan["items"]:
        available = observe_review_state(context)
        raise OutputValidationError(
            "该批次没有需要修复的差异或事实覆盖缺口",
            details={"batch_id": batch_id, "repairable_batch_ids": available["repairable_batch_ids"],
                     "recheck_batch_ids": available["recheck_batch_ids"]},
        )
    cross_batch_keys = {tuple(key) for key in (state["global_review"] or {}).get("routed_difference_keys", [])}
    for item in plan["items"]:
        if any(_difference_identity(difference) in cross_batch_keys for difference in item["review_result"]["differences"]):
            item["global_review_context"] = {
                "case_index": prepare_global_final_review(_isolated_context(context), {
                    "generation": state["generation"],
                })["case_index"],
            }
            item["repair_requirements"].append(
                "global_review_context.case_index 是当前跨批用例的只读对照，供核对重复和优先级等全局差异；"
                "只允许修改 target_case_ids，不得从对照索引推断未提供的事实或迁移批次外的事实。"
            )
    return _action(batch_id, plan["items"])


def _check_action(action: dict[str, Any], current: dict[str, Any], outputs: list[dict[str, Any]]) -> None:
    if action != current:
        raise OutputValidationError(
            "自主终审行动输入已变化，不能应用过期候选", details={"batch_id": action.get("batch_id")},
        )
    if not isinstance(outputs, list) or len(outputs) != len(action["items"]) or any(not isinstance(item, dict) for item in outputs):
        raise OutputValidationError(
            "自主终审专业 Agent 结果与行动任务数量不一致", details={"expected_count": len(action["items"])},
        )


def _scope_sha256(generation: dict[str, Any], scopes: list[dict[str, Any]]) -> str:
    ids = {case_id for item in scopes for case_id in item["review_batch"]["case_ids"]}
    return _sha256({
        "test_cases": [case for case in generation["test_cases"] if case["case_id"] in ids],
        "case_fact_bindings": [item for item in generation["case_fact_bindings"] if item["case_id"] in ids],
    })


def _scope_key(scope: dict[str, Any]) -> str:
    # 用例集合是审查范围身份；批次展示名称不能绕过同一范围的版本记录。
    return _sha256(sorted(scope["review_batch"]["case_ids"]))


def _new_review_candidates(
    context: ToolExecutionContext, state: dict[str, Any], batch_id: str,
    generation: dict[str, Any], scopes: list[dict[str, Any]],
) -> dict[str, str]:
    candidates = {}
    parent = context.action_input["review_inputs"][_batch_index(context, batch_id)]
    for scope in [*scopes, parent]:
        key = _scope_key(scope)
        before = _scope_sha256(state["generation"], [scope])
        after = _scope_sha256(generation, [scope])
        if before == after:
            raise OutputValidationError(
                "修复没有改变待复审范围的用例和绑定，不能重新评判同一候选",
                details={"batch_id": batch_id, "case_ids": scope["review_batch"]["case_ids"],
                         "reason": "unchanged_candidate"},
            )
        history = state["reviewed_scope_hashes"].setdefault(key, [])
        # 新出现的子范围也继承初审版本及本次修复前已审版本，不能借拆批回到旧候选。
        for previous in (_scope_sha256(context.action_input["generation"], [scope]), before):
            if previous not in history:
                history.append(previous)
        if after in history:
            raise OutputValidationError(
                "该审查范围回到了已经评判过的用例版本，不能重复抽取新的审查结论",
                details={"batch_id": batch_id, "case_ids": scope["review_batch"]["case_ids"],
                         "reason": "previously_reviewed_candidate"},
            )
        candidates[key] = after
    return candidates


def _merge_repair_candidate(
    context: ToolExecutionContext, state: dict[str, Any], batch_id: str,
    items: list[dict[str, Any]], outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return merge_final_review_repairs(_isolated_context(context), {
            "generation": state["generation"], "repair_inputs": items,
            "repair_records": [{"item_index": index, "output": value} for index, value in enumerate(outputs)],
        })
    except ValueError as error:
        raise OutputValidationError(str(error), details={"batch_id": batch_id, "stage": "repair_validation"}) from error


def validate_review_repair_candidate(
    context: ToolExecutionContext, action: dict[str, Any], item_index: int, candidate: dict[str, Any],
) -> None:
    """在专业会话内验证单项范围与版本，不提交父动作或更改共享领域状态。"""
    if action != prepare_review_repair(context, action):
        raise OutputValidationError("修复行动输入已过期，不能验收专业候选")
    state = deepcopy(_state(context))
    item = action["items"][item_index]
    generation = _merge_repair_candidate(context, state, action["batch_id"], [item], [candidate])
    scope = {key: deepcopy(item[key]) for key in ("review_batch", "authoritative_facts", "test_design_items")}
    _new_review_candidates(context, state, action["batch_id"], generation, [scope])


def apply_review_repair(
    context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """完整校验专业 Agent 的已归一化输出后，一次性替换领域状态。"""

    _check_action(action, prepare_review_repair(context, action), outputs)
    state = deepcopy(_state(context))
    batch_id = action["batch_id"]
    generation = _merge_repair_candidate(context, state, batch_id, action["items"], outputs)
    scopes = [
        {key: deepcopy(item[key]) for key in ("review_batch", "authoritative_facts", "test_design_items")}
        for item in action["items"]
    ]
    generation_sha256 = _sha256(generation)
    changed = generation_sha256 != state["generation_sha256"]
    if not changed:
        raise OutputValidationError(
            "修复没有产生新的用例版本，不能重复复审同一候选；请依据现有差异完成实际修复",
            details={"batch_id": batch_id, "generation_version": state["generation_version"],
                     "reason": "unchanged_candidate"},
        )
    candidate_hashes = _new_review_candidates(context, state, batch_id, generation, scopes)
    global_scope_hash = _global_scope_sha256(context, generation)
    if global_scope_hash in state["reviewed_global_scope_hashes"]:
        # 修改隐藏步骤或回退旧索引都不能换取对同一全局候选的新评判。
        raise OutputValidationError(
            "修复后的全局可见用例仍是已审版本，必须针对全局差异产生实际变化",
            details={"batch_id": batch_id, "reason": "previously_reviewed_global_candidate"},
        )
    state["generation_version"] += 1
    state["generation"] = generation
    state["generation_sha256"] = generation_sha256
    state["audit_summary"] = _audit(context, generation)
    state["repair_counts"][batch_id] = state["repair_counts"].get(batch_id, 0) + 1
    # 只有真实产生新版本才授予复审机会，避免靠重复评判消除原有差异。
    state["pending_rechecks"][batch_id] = {
        "scopes": scopes, "scope_sha256": _scope_sha256(generation, scopes),
        "candidate_hashes": candidate_hashes,
    }
    context.action_state = state
    return {"batch_id": batch_id, "status": "awaiting_recheck", "changed": changed, **observe_review_state(context)}


def prepare_review_recheck(
    context: ToolExecutionContext, arguments: dict[str, Any],
) -> dict[str, Any]:
    state = _state(context)
    batch_id = _action_batch_id(arguments)
    _batch_index(context, batch_id)
    pending = state["pending_rechecks"].get(batch_id)
    if state["completed"] or not pending:
        raise OutputValidationError(
            "该批次没有已提交且等待复审的修复",
            details={"batch_id": batch_id, "recheck_batch_ids": sorted(state["pending_rechecks"])},
        )
    if pending["scope_sha256"] != _scope_sha256(state["generation"], pending["scopes"]):
        raise ValueError("待复审范围的用例版本已经变化")
    plan = prepare_final_review_rechecks(_isolated_context(context), {
        "repair_inputs": pending["scopes"], "generation": state["generation"],
    })
    return _action(batch_id, plan["items"])


def apply_review_recheck(
    context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    _check_action(action, prepare_review_recheck(context, action), outputs)
    state = deepcopy(_state(context))
    try:
        records = [{
            "item_index": index,
            "output": postprocess_final_review_batch_item(_isolated_context(context), {
                "item_input": item, "item_output": output,
            }),
        } for index, (item, output) in enumerate(zip(action["items"], outputs, strict=True))]
    except ValueError as error:
        raise OutputValidationError(
            str(error), details={"batch_id": action["batch_id"], "stage": "recheck_validation"},
        ) from error
    merged = merge_final_review_recheck_records(_isolated_context(context), {
        "baseline_inputs": _review_inputs(context, state),
        "baseline_records": state["review_records"],
        "replacement_inputs": action["items"], "replacement_records": records,
    })
    state["review_records"] = merged["items"]
    for key, candidate_hash in state["pending_rechecks"][action["batch_id"]]["candidate_hashes"].items():
        history = state["reviewed_scope_hashes"][key]
        if candidate_hash not in history:
            history.append(candidate_hash)
    del state["pending_rechecks"][action["batch_id"]]
    context.action_state = state
    return {"batch_id": action["batch_id"], "status": "rechecked", **observe_review_state(context)}


async def _execute_review_batches(context: ToolExecutionContext, arguments: dict[str, Any], *, recheck: bool):
    """并发执行独立范围，按原顺序验收；涉及全局对照的修复依次刷新依赖。"""
    prepare = prepare_review_recheck if recheck else prepare_review_repair
    apply = apply_review_recheck if recheck else apply_review_repair
    actions = [prepare(context, {"batch_id": batch_id}) for batch_id in arguments["batch_ids"]]
    if not actions:
        raise OutputValidationError("没有当前待处理的终审批次")

    async def invoke(prepared):
        items = [item for action in prepared for item in action["items"]]
        validators = None if recheck else [
            partial(validate_review_repair_candidate, context, action, index)
            for action in prepared for index in range(len(action["items"]))
        ]
        return await context.invoke_agents(
            agent_key="test_generation_final_reviewer" if recheck else "test_generation_batch_repairer",
            inputs=deepcopy(items), candidate_validators=validators,
            item_postprocessor=("testing.postprocess_final_review_batch_item" if recheck
                                else "testing.postprocess_final_review_repair_item"),
        )

    if any("global_review_context" in item for action in actions for item in action["items"]):
        # 全局差异依赖其他用例的当前版本，前一修复落地后必须重新准备后一个输入。
        for action in actions:
            current = prepare(context, action)
            apply(context, current, await invoke([current]))
    else:
        outputs = await invoke(actions)
        offset = 0
        for action in actions:
            end = offset + len(action["items"])
            apply(context, action, outputs[offset:end])
            offset = end
    return observe_review_state(context)


async def repair_review_batch(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return await _execute_review_batches(context, arguments, recheck=False)


async def recheck_review_batch(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    return await _execute_review_batches(context, arguments, recheck=True)


def _resolved_batch_output(context: ToolExecutionContext) -> dict[str, Any]:
    state = _state(context)
    from .test_generation_facts import validate_generation_design_supports
    validate_generation_design_supports(state["generation"], context.action_input["generation_inputs"])
    if state["pending_rechecks"]:
        raise OutputValidationError(
            "仍有修复批次没有复审，不能结束自主终审",
            details={"recheck_batch_ids": sorted(state["pending_rechecks"])},
        )
    observation = observe_review_state(context)
    if observation["repairable_batch_ids"]:
        raise OutputValidationError(
            "仍有未解决的批次差异，不能进行全局审查或结束终审",
            details={"repairable_batch_ids": observation["repairable_batch_ids"]},
        )
    current_inputs = _review_inputs(context, state)
    if any(item["audit_summary"]["approved"] is not True for item in current_inputs):
        raise OutputValidationError(
            "仍有批次确定性审计未通过，不能结束自主终审",
            details={"failed_batch_ids": [item["review_batch"]["batch_id"] for item in current_inputs
                                          if item["audit_summary"]["approved"] is not True]},
        )
    audit = _audit(context, state["generation"])
    review = merge_final_review_batches(_isolated_context(context), {
        "review_inputs": current_inputs, "review_records": state["review_records"],
        "repair_inputs": [], "recheck_inputs": [], "recheck_records": [],
        "audit_summary": audit,
    })
    if audit["approved"] is not True or review["approved"] is not True or review["differences"]:
        raise OutputValidationError(
            "仍有未解决的审查差异或确定性审计失败，不能结束自主终审",
            details={"audit_differences": audit["differences"],
                     "repairable_batch_ids": observe_review_state(context)["repairable_batch_ids"]},
        )
    review["summary"] = (
        f"分批终审通过：{len(current_inputs)} 个批次，当前用例已通过分批审查和确定性审计。"
    )
    return {"generation": deepcopy(state["generation"]), "audit_summary": audit, "final_review": review}


def _global_scope_sha256(context: ToolExecutionContext, generation: dict[str, Any]) -> str:
    # 背景摘要和修复计数不属于全局评审对象，不能通过改变它们重复抽取结论。
    prepared = prepare_global_final_review(_isolated_context(context), {"generation": generation})
    return _sha256(prepared["case_index"])


def prepare_global_review(
    context: ToolExecutionContext, arguments: dict[str, Any],
) -> dict[str, Any]:
    state = _state(context)
    if state["completed"]:
        raise OutputValidationError("自主终审已经完成，不能重新评审", details={"completed": True})
    resolved = _resolved_batch_output(context)
    item = prepare_global_final_review(_isolated_context(context), {
        "generation": resolved["generation"], "batch_review": resolved["final_review"],
        "audit_summary": resolved["audit_summary"],
    })
    scope_hash = _sha256(item["case_index"])
    if scope_hash in state["reviewed_global_scope_hashes"]:
        raise OutputValidationError(
            "该全局用例索引已经得到有效审查结论，不能重复抽取新的结论",
            details={"reason": "previously_reviewed_global_candidate",
                     "generation_version": state["generation_version"]},
        )
    return {
        "items": [item], "input_sha256": _sha256([item]),
        "generation_sha256": state["generation_sha256"], "scope_sha256": scope_hash,
    }


def apply_global_review(
    context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """同一事务保存全局结论、已审版本和路由差异；批准只对当前完整候选有效。"""

    _check_action(action, prepare_global_review(context, {}), outputs)
    state = deepcopy(_state(context))
    try:
        output = postprocess_global_final_review_output(_isolated_context(context), {
            "input_payload": action["items"][0], "output": outputs[0],
        })
        routed_records = route_final_review_differences(
            generation=state["generation"], review_inputs=_review_inputs(context, state),
            batch_review=action["items"][0]["batch_review"], global_review=output,
        )
    except ValueError as error:
        raise OutputValidationError(str(error), details={"stage": "global_review_validation"}) from error
    state["global_review"] = {
        "generation_sha256": state["generation_sha256"],
        "input_sha256": _sha256(action["items"][0]),
        "scope_sha256": action["scope_sha256"], "output": deepcopy(output),
        "routed_difference_keys": [
            list(_difference_identity(difference))
            for record in routed_records for difference in record["output"]["differences"]
        ],
    }
    state["reviewed_global_scope_hashes"].append(action["scope_sha256"])
    if not output["approved"]:
        state["review_records"] = routed_records
    context.action_state = state
    return {"status": "globally_reviewed", **observe_review_state(context)}


async def review_all_cases(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    action = prepare_global_review(context, arguments)
    output = await context.invoke_agent(
        agent_key="test_generation_global_reviewer", input_payload=deepcopy(action["items"][0]),
    )
    return apply_global_review(context, action, [output])


def resolve_review_output(context: ToolExecutionContext) -> dict[str, Any]:
    """从已通过当前版本分批、全局审查和审计的领域状态导出产物。"""
    resolved = _resolved_batch_output(context)
    state = _state(context)
    global_review = state["global_review"]
    if (not global_review or global_review["generation_sha256"] != state["generation_sha256"]
            or global_review["output"]["approved"] is not True or global_review["output"]["differences"]):
        raise OutputValidationError(
            "当前用例版本尚未通过全局审查，不能结束终审",
            details={"generation_version": state["generation_version"],
                     "global_review_status": observe_review_state(context)["global_review_status"]},
        )
    if global_review["scope_sha256"] != _global_scope_sha256(context, state["generation"]):
        raise ValueError("全局审查结论与当前可见用例索引不一致")
    resolved["final_review"]["summary"] = (
        f"终审通过：{len(state['review_records'])} 个批次及当前版本的全局审查、确定性审计均通过。"
    )
    return resolved


def finish_review(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    resolve_review_output(context)
    state = deepcopy(_state(context))
    state["completed"] = True
    context.action_state = state
    return {"completed": True}


def validate_review_resolution(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """最终产物只从已验收的状态导出，协调 Agent 不能自行改写用例或通过标记。"""

    state = _state(context)
    output = arguments.get("output")
    if not isinstance(output, dict) or output.get("completed") is not True or state["completed"] is not True:
        raise OutputValidationError(
            "自主终审必须通过 finish_review 后才能提交最终结果", details={"completed": state["completed"]},
        )
    if _sha256(arguments.get("input_payload")) != state["input_sha256"]:
        raise ValueError("自主终审最终验收输入与持久状态不一致")
    return resolve_review_output(context)
