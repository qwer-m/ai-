"""同源取证、草案修订与规划路由的自主行动，统一维护候选版本和严格提交边界。"""

from __future__ import annotations

from copy import copy, deepcopy
from functools import partial
import hashlib
import json
from typing import Any, TYPE_CHECKING

from core.db.model_defs import AgentRun

from .action_journal import action_state_hash
from .output_repair import OutputValidationError
from .sources import SourceSnapshot, assert_same_source, persisted_source_snapshot
from .test_generation_facts import fact_source_context_fields
from .test_generation_planning import (
    _planning_module_design_catalog,
    current_planning_support_decisions,
    planning_support_design_hashes,
    merge_planning_scope_routes,
    postprocess_planning_route_repair_item,
    postprocess_planning_scope_routing_item,
    prepare_business_plan_batches,
    prepare_business_plan_consolidation,
    prepare_planning_route_repairs,
    prepare_planning_scope_routes,
    validate_business_plan_draft_output,
    validate_business_plan_output,
)
from .test_generation_semantics import merge_source_semantics

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


_STATE_VERSION = 4
_EVIDENCE_MAX_CHARS = 4000
_SOURCE_ARTIFACT_KEYS = ("source_semantics_source_pages", "source_semantics_coordinate_maps")


def _isolated_context(context: ToolExecutionContext) -> ToolExecutionContext:
    """保留真实页及坐标映射；准备工具的统计产物不写入父行动状态之外。"""
    isolated = copy(context)
    isolated.artifacts = deepcopy(context.artifacts)
    isolated.executed_tools = []
    isolated.tool_calls = []
    return isolated


def _goal(context: ToolExecutionContext) -> dict[str, Any]:
    run = context.db.get(AgentRun, context.run_id)
    if run is None or run.project_id != context.project_id or run.user_id != context.user_id:
        raise ValueError("自主规划无法取得当前用户和项目的运行来源")
    snapshot = persisted_source_snapshot(run)
    if snapshot is None:
        raise ValueError("自主规划缺少不可变运行来源")
    source = context.action_input
    assert_same_source(snapshot, SourceSnapshot.from_dict(source["evidence_source"]))
    persisted_artifacts = dict((run.run_context or {}).get("artifacts") or {})
    persisted_evidence = persisted_artifacts.get("requirement_evidence")
    if (not isinstance(persisted_evidence, dict)
            or persisted_evidence.get("evidence_catalog") != source["evidence_catalog"]):
        raise ValueError("自主规划取证目录与运行已持久化的完整原始证据不一致")
    if any(context.artifacts.get(key) != persisted_artifacts.get(key) for key in _SOURCE_ARTIFACT_KEYS):
        raise ValueError("自主规划原始页或坐标映射与运行持久来源不一致")
    for kind in ("text", "vision"):
        for item in source[f"source_{kind}_inputs"]:
            pages = item.get("pages") if item.get("source_kind") == "document_batch" else [item]
            if not isinstance(pages, list) or not pages:
                raise ValueError("自主规划来源输入缺少真实页面")
            for page in pages:
                if snapshot.kind == "knowledge_document":
                    if page.get("document_id") != snapshot.document_id or page.get("asset_source_sha256") != snapshot.content_hash:
                        raise ValueError("自主规划来源项与运行文档版本不一致")
                elif (page.get("source_kind") != "inline"
                      or page.get("requirement_sha256") != snapshot.content_hash
                      or hashlib.sha256(str(page.get("requirement") or "").encode("utf-8")).hexdigest() != snapshot.content_hash):
                    raise ValueError("自主规划来源项与运行内联需求不一致")
    return {
        "objective": "依据同一真实来源修正事实解释、草案和规划，使全部事实及覆盖意图获得严格可追溯的路由",
        "source_ref": snapshot.to_dict(), "input_sha256": action_state_hash(source),
        "source_artifacts_sha256": action_state_hash({key: context.artifacts.get(key) for key in _SOURCE_ARTIFACT_KEYS}),
        "case_budget": source["case_budget"],
        "acceptance": ["来源锚点与运行版本一致", "草案完整承接当前事实", "规划完整承接当前覆盖组", "路由完整且没有未解决的无依据覆盖意图"],
    }


def _state_hash(state: dict[str, Any]) -> str:
    return action_state_hash({key: value for key, value in state.items() if key != "domain_sha256"})


def _state(context: ToolExecutionContext) -> dict[str, Any]:
    state = context.action_state
    if not isinstance(state, dict) or state.get("schema_version") != _STATE_VERSION:
        raise ValueError("自主规划状态未初始化或版本不受支持")
    if state.get("input_sha256") != action_state_hash(context.action_input) or state.get("goal") != _goal(context):
        raise ValueError("自主规划持久输入、目标或来源版本不一致")
    if state.get("domain_sha256") != _state_hash(state):
        raise ValueError("自主规划领域状态与内容指纹不一致")
    for evidence_id, record in state["evidence_reads"].items():
        text = _evidence_text(context, None if evidence_id == "" else evidence_id)
        if record.get("sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest() or record.get("total_chars") != len(text):
            raise ValueError("自主规划持久取证记录与完整原文不一致")
        last_end = -1
        for span in record["ranges"]:
            if (not isinstance(span, list) or len(span) != 2 or any(type(value) is not int for value in span)
                    or not 0 <= span[0] < span[1] <= len(text) or span[0] <= last_end):
                raise ValueError("自主规划持久取证区间无效")
            last_end = span[1]
    return state


def _commit(context: ToolExecutionContext, state: dict[str, Any]) -> None:
    state["domain_sha256"] = _state_hash(state)
    context.action_state = state


def _records(inputs: list[dict[str, Any]], values: list[Any]) -> list[dict[str, Any]]:
    if len(inputs) != len(values):
        raise ValueError("自主规划输入与结果数量不一致")
    records = []
    for index, (item, record) in enumerate(zip(inputs, values, strict=True)):
        if not isinstance(item, dict) or not isinstance(record, dict) or record.get("item_index") != index or not isinstance(record.get("output"), dict):
            raise ValueError(f"自主规划记录顺序或内容无效: item_index={index}")
        input_hash = action_state_hash(item)
        if record.get("input_hash", input_hash) != input_hash:
            raise ValueError(f"自主规划记录与完整输入身份不一致: item_index={index}")
        records.append({"item_index": index, "input_hash": input_hash, "output": deepcopy(record["output"])})
    return records


def _retained_records(inputs: list[dict[str, Any]], records: list[Any]) -> list[Any]:
    """仅以真实完整输入内容复用已校验记录，批次位置或数量变化也会失效。"""
    previous = {record["input_hash"]: record for record in records if isinstance(record, dict)}
    return [
        {**deepcopy(previous[action_state_hash(item)]), "item_index": index}
        if action_state_hash(item) in previous else None
        for index, item in enumerate(inputs)
    ]


def _complete_records(records: list[Any]) -> bool:
    return bool(records) and all(isinstance(record, dict) for record in records)


def _merge_sources(context: ToolExecutionContext, records: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return merge_source_semantics(_isolated_context(context), {
        "text_inputs": context.action_input["source_text_inputs"], "text_records": records["text"],
        "vision_inputs": context.action_input["source_vision_inputs"], "vision_records": records["vision"],
    })


def _planning_inputs(context: ToolExecutionContext, semantics: dict[str, Any]) -> list[dict[str, Any]]:
    return prepare_business_plan_batches(_isolated_context(context), {
        "planning_scopes": semantics["planning_scopes"], "case_budget": context.action_input["case_budget"],
    })["items"]


def _consolidation(context: ToolExecutionContext, state: dict[str, Any]) -> dict[str, Any]:
    if not _complete_records(state["planning_records"]):
        raise OutputValidationError("仍有当前来源版本的规划草案未完成", details={"pending_draft_indexes": _pending(state["planning_records"])})
    prepared = prepare_business_plan_consolidation(_isolated_context(context), {
        "prepared_items": state["planning_inputs"], "plan_records": state["planning_records"],
        "case_budget": context.action_input["case_budget"],
    })
    return {key: prepared[key] for key in ("partial_plans", "planning_metadata", "coverage_group_catalog", "case_budget", "planning_limits")}


def _pending(records: list[Any]) -> list[int]:
    return [index for index, record in enumerate(records) if record is None]


def _coverage_origins(state: dict[str, Any]) -> dict[str, dict[str, list[Any]]]:
    """沿已校验草案的覆盖主题和事实引用定位来源，不靠名称或关键词猜测。"""
    origins: dict[str, dict[str, list[Any]]] = {}
    for index, record in enumerate(state["planning_records"]):
        if record is None:
            continue
        # 来源刚修订、旧计划尚未清除时，旧草案的引用仍应沿其自身输入解析。
        fact_scopes = {fact["fact_id"]: scope["scope_id"]
                       for scope in state["planning_inputs"][index]["planning_scopes"] for fact in scope["facts"]}
        for candidate in record["output"]["module_candidates"]:
            for topic in candidate["coverage_topics"]:
                origin = origins.setdefault(topic["objective"], {"planning_batch_indexes": [], "scope_ids": []})
                origin["planning_batch_indexes"] = sorted(set([*origin["planning_batch_indexes"], index]))
                origin["scope_ids"] = sorted(set([*origin["scope_ids"], *(fact_scopes[value] for value in candidate["fact_ids"])]))
    return origins


def _feedback_designs(designs: list[dict[str, Any]], origins: dict[str, dict[str, list[Any]]]) -> list[dict[str, Any]]:
    return [{**deepcopy(design), **deepcopy(origins.get(design["coverage_intent"], {"planning_batch_indexes": [], "scope_ids": []}))}
            for design in designs]


def _current_support_review(state: dict[str, Any], index: int) -> dict[str, Any] | None:
    """缺口和可选目录共用同一份当前裁决，后续定向复核只覆盖实际选中的意图。"""
    record = state["gap_records"][index]
    if record is None:
        return None
    review = deepcopy(record["output"])
    current = {decision["test_design_item_index"]: decision for decision in review["decisions"]}
    module_index = state["gap_inputs"][index]["module_index"]
    for revision in state["route_review_revisions"]:
        if revision["input"]["module_index"] == module_index:
            for decision in revision["output"]["decisions"]:
                current[decision["test_design_item_index"]] = deepcopy(decision)
    review["decisions"] = [current[key] for key in sorted(current)]
    return review


def _reviewable_designs(state: dict[str, Any]) -> list[dict[str, Any]]:
    """可选范围包含已支持项；按真实裁决分组，避免被误读为待办清单。"""
    if _pending(state["gap_records"]):
        return []
    return [{"module_index": item["module_index"],
             "design_indexes_by_disposition": {
                 disposition: [decision["test_design_item_index"] for decision in review["decisions"]
                               if decision["disposition"] == disposition]
                 for disposition in ("supported", "unsupported")}}
            for index, item in enumerate(state["gap_inputs"])
            if (review := _current_support_review(state, index)) is not None]


def unreviewed_support_selections(state, selections, downstream_feedback):
    """按既有复核身份去掉已裁决的同源目标，让行动目录与执行去重使用相同依据。"""
    previous = set().union(*(planning_support_design_hashes(record["input"])
                            for record in state["route_review_revisions"]))
    inputs = {item["module_index"]: item for item in state["gap_inputs"]}
    result = []
    for selection in selections:
        item = deepcopy(inputs[selection["module_index"]])
        indexes = set(selection["test_design_item_indexes"])
        item["_planning_feedback"] = {"question": "", "previous_decisions": [],
                                     "downstream_problems": deepcopy(downstream_feedback)}
        pending = [design["test_design_item_index"] for design in item["missing_test_design_items"]
                   if design["test_design_item_index"] in indexes and not
                   (planning_support_design_hashes({**item, "missing_test_design_items": [design]}) & previous)]
        if pending:
            result.append({"module_index": selection["module_index"], "test_design_item_indexes": pending})
    return result


def _route_feedback(state: dict[str, Any]) -> list[dict[str, Any]]:
    origins = _coverage_origins(state)
    feedback = []
    for index, item in enumerate(state["gap_inputs"]):
        designs = _feedback_designs(item["missing_test_design_items"], origins)
        facts_by_id = {fact["fact_id"]: fact for fact in item["candidate_facts"]}
        review = _current_support_review(state, index)
        if review is not None:
            for revision in state["route_review_revisions"]:
                if revision["input"]["module_index"] == item["module_index"]:
                    facts_by_id.update({fact["fact_id"]: fact for fact in revision["input"]["candidate_facts"]})
            review["decisions"] = [decision for decision in review["decisions"] if decision["disposition"] == "unsupported"]
            unresolved = {decision["test_design_item_index"] for decision in review["decisions"]}
            designs = [design for design in designs if design["test_design_item_index"] in unresolved]
            if not designs:
                continue
        cited_ids = {fact_id for decision in (review or {}).get("decisions", [])
                     for fact_id in [*decision["fact_ids"],
                         *(value for gap in decision.get("support_gaps", []) for value in gap["evidence_fact_ids"]) ]}
        cited_facts = [fact for fact_id, fact in facts_by_id.items() if fact_id in cited_ids]
        feedback.append({
            **{key: item[key] for key in ("module_index", "module_name", "module_objective")},
            "missing_test_design_items": designs,
            "planning_batch_indexes": sorted({index for design in designs for index in design["planning_batch_indexes"]}),
            "scope_ids": sorted({fact["scope_id"] for fact in [*item["candidate_facts"], *cited_facts]}),
            # 完整候选事实只交给复核专家；协调者保留引用原文以核对结论，避免每轮复制整个事实目录。
            "supporting_facts": [{**{key: fact[key] for key in ("scope_id", "fact_id", "assertion")},
                                  **fact_source_context_fields(fact)}
                                 for fact in cited_facts],
            "review": review,
        })
    return feedback


def _draft_route_feedback(state: dict[str, Any], index: int) -> list[dict[str, Any]]:
    """只传递实际源自当前草案的未决意图，历史通过意见不成为新的修订任务。"""
    selected = []
    for item in _route_feedback(state) or deepcopy(state["last_route_feedback"]):
        designs = [design for design in item["missing_test_design_items"] if index in design["planning_batch_indexes"]]
        if not designs:
            continue
        entry = deepcopy(item)
        entry["missing_test_design_items"] = designs
        indexes = {design["test_design_item_index"] for design in designs}
        if entry["review"] is not None:
            entry["review"]["decisions"] = [value for value in entry["review"]["decisions"] if value["test_design_item_index"] in indexes]
        entry["planning_batch_indexes"] = [index]
        entry["scope_ids"] = sorted({value for design in designs for value in design["scope_ids"]})
        selected.append(entry)
    return selected


def _clear_plan(state: dict[str, Any]) -> None:
    if state["plan"] is not None:
        state["previous_plan"] = deepcopy(state["plan"])
    if state["gap_inputs"]:
        state["last_route_feedback"] = _route_feedback(state)
    # 清除当前计划，保留完整输入一致的已验收记录；新输入仍须重新调用专家。
    for kind in ("route", "gap"):
        state[f"{kind}_cache"].update({record["input_hash"]: deepcopy(record)
                                     for record in state[f"{kind}_records"] if isinstance(record, dict)})
    state.update(plan=None, plan_input_hash=None, route_inputs=[], route_records=[], gap_inputs=[], gap_records=[], route_review_revisions=[])


def _refresh_routes(context: ToolExecutionContext, state: dict[str, Any]) -> None:
    if state["plan"] is None:
        return
    prepared = prepare_planning_scope_routes(_isolated_context(context), {
        "plan": state["plan"], "planning_scopes": state["source_semantics"]["planning_scopes"],
    })
    state["route_inputs"] = prepared["batch_items"]
    state["route_records"] = _retained_records(state["route_inputs"], [*state["route_cache"].values(), *state["route_records"]])
    _refresh_gaps(context, state)


def _refresh_gaps(context: ToolExecutionContext, state: dict[str, Any]) -> None:
    if not _complete_records(state["route_records"]):
        state.update(gap_inputs=[], gap_records=[], route_review_revisions=[])
        return
    prepared = prepare_planning_route_repairs(_isolated_context(context), {
        "plan": state["plan"], "prepared_items": state["route_inputs"], "route_records": state["route_records"],
    })
    if state["gap_inputs"] != prepared["items"]:
        state["route_review_revisions"] = []
    state["gap_inputs"] = prepared["items"]
    state["gap_records"] = _retained_records(state["gap_inputs"], [*state["gap_cache"].values(), *state["gap_records"]])


def initialize_planning_state(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """校验真实来源和已完成草案；恢复时只接受同一输入及同一领域版本。"""
    source = deepcopy(arguments)
    for key in ("source_text_inputs", "source_text_records", "source_vision_inputs", "source_vision_records", "planning_inputs", "planning_records"):
        if not isinstance(source.get(key), list):
            raise ValueError(f"自主规划输入 {key} 必须是数组")
    if (not isinstance(source.get("evidence_source"), dict) or not isinstance(source.get("evidence_catalog"), dict)
            or not isinstance(source["evidence_catalog"].get("items"), list)
            or type(source.get("case_budget")) is not int or source["case_budget"] < 1):
        raise ValueError("自主规划需要完整来源目录和有效用例预算")
    catalog_ids = [item.get("evidence_id") if isinstance(item, dict) else None for item in source["evidence_catalog"]["items"]]
    if not catalog_ids or any(not isinstance(value, str) or not value for value in catalog_ids) or len(catalog_ids) != len(set(catalog_ids)):
        raise ValueError("自主规划证据目录 ID 为空或重复")
    context.action_input = source
    if context.action_state:
        return _state(context)
    source_records = {kind: _records(source[f"source_{kind}_inputs"], source[f"source_{kind}_records"]) for kind in ("text", "vision")}
    semantics = _merge_sources(context, source_records)
    prepared = _planning_inputs(context, semantics)
    if prepared != source["planning_inputs"]:
        raise ValueError("自主规划初始草案输入没有对应完整来源结果")
    planning_records = _records(prepared, source["planning_records"])
    for item, record in zip(prepared, planning_records, strict=True):
        record["output"] = validate_business_plan_draft_output(_isolated_context(context), {"input_payload": item, "output": record["output"]})
    state = {
        "schema_version": _STATE_VERSION, "input_sha256": action_state_hash(source), "goal": _goal(context),
        "source_version": 0, "draft_version": 0, "plan_version": 0, "support_version": 0,
        "source_records": source_records, "source_semantics": semantics,
        "planning_inputs": prepared, "planning_records": planning_records,
        "plan": None, "plan_input_hash": None, "previous_plan": None,
        "route_inputs": [], "route_records": [], "gap_inputs": [], "gap_records": [], "route_review_revisions": [],
        "route_cache": {}, "gap_cache": {},
        "last_route_feedback": [], "evidence_reads": {}, "completed": False,
    }
    _consolidation(context, state)
    _commit(context, state)
    return state


def _source_index(context: ToolExecutionContext) -> list[dict[str, Any]]:
    result = []
    for kind in ("text", "vision"):
        for index, item in enumerate(context.action_input[f"source_{kind}_inputs"]):
            pages = item.get("pages") if item.get("source_kind") == "document_batch" else [item]
            result.append({
                "source_kind": kind, "item_index": index,
                "page_numbers": [page["page_number"] for page in pages if "page_number" in page],
                "scope_ids": list(dict.fromkeys(scope["scope_id"] for page in pages for scope in page.get("source_scopes", []))),
            })
    return result


def _unrouted_modules(state: dict[str, Any]) -> list[int]:
    if state["plan"] is None or not _complete_records(state["route_records"]):
        return []
    routed = {module["module_index"] for record in state["route_records"]
              for route in record["output"]["routes"] for assignment in route["assignments"]
              for module in assignment["module_routes"]}
    return sorted(set(range(len(state["plan"]["business_modules"]))) - routed)


def _unrouted_module_feedback(state: dict[str, Any]) -> list[dict[str, Any]]:
    origins = _coverage_origins(state)
    feedback = []
    for index in _unrouted_modules(state):
        module = state["plan"]["business_modules"][index]
        designs = _feedback_designs(_planning_module_design_catalog(module), origins)
        feedback.append({
            "module_index": index, "module_name": module["name"], "module_objective": module["objective"],
            "test_design_items": designs,
            "planning_batch_indexes": sorted({value for design in designs for value in design["planning_batch_indexes"]}),
            "scope_ids": sorted({value for design in designs for value in design["scope_ids"]}),
        })
    return feedback


def _business_plan_rebuild_blocker(state: dict[str, Any]) -> dict[str, Any] | None:
    """重分组不能改变已固定的覆盖意图；纯支持缺口应保留原规划供定向核实。"""
    authority_gaps = [gap for problem in state.get("downstream_feedback", [])
                      if problem.get("origin") == "preparation" and problem.get("payload", {}).get("kind") == "authority_support_gap"
                      for gap in problem["payload"]["design_support_gaps"]]
    if state["plan"] is not None and authority_gaps:
        return {"reason": "权威判断改变的是事实支持关系，重建相同草案会丢失当前权威缺口；请定向复核受影响意图，必要时修订草案。",
                "design_support_gaps": deepcopy(authority_gaps),
                "available_repairs": ["review_planning_route_gaps", "revise_planning_draft"]}
    if (state["plan"] is None or _pending(state["planning_records"])
            or not _complete_records(state["route_records"]) or _unrouted_modules(state)
            or any(problem.get("origin") in {"generation", "review"}
                   for problem in state.get("downstream_feedback", []))):
        return None
    unresolved = [{"module_index": item["module_index"],
                   "test_design_item_indexes": [decision["test_design_item_index"] for decision in item["review"]["decisions"]]}
                  for item in _route_feedback(state) if item["review"] and item["review"]["decisions"]]
    if not unresolved:
        return None
    return {"reason": "当前未决项是意图支持缺口；重建只能重分组，不能改变草案意图或来源依据。",
            "design_selections": unresolved,
            "available_repairs": ["read_planning_evidence", "reinspect_planning_source",
                                  "revise_planning_draft", "review_planning_route_gaps"]}


def observe_planning_state(context: ToolExecutionContext, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """协调者观察当前可行动缺口，不把历史修复结论伪装成新版本通过。"""
    state = _state(context)
    feedback = _route_feedback(state)
    unsupported = [
        {"module_index": item["module_index"], "module_name": item["module_name"], **decision}
        for item in feedback if item["review"] is not None
        for decision in item["review"]["decisions"] if decision["disposition"] == "unsupported"
    ]
    unsupported_by_batch: dict[int, list[str]] = {}
    for item in feedback:
        rejected = {decision["test_design_item_index"] for decision in (item["review"] or {}).get("decisions", [])
                    if decision["disposition"] == "unsupported"}
        for design in item["missing_test_design_items"]:
            if design["test_design_item_index"] in rejected:
                for index in design["planning_batch_indexes"]:
                    unsupported_by_batch.setdefault(index, []).append(design["coverage_intent"])
    drafts = [{
        "item_index": index, "input_hash": action_state_hash(item),
        "scope_ids": list(dict.fromkeys(scope["scope_id"] for scope in item["planning_scopes"])),
        "fact_count": sum(len(scope["facts"]) for scope in item["planning_scopes"]),
        "status": "pending" if state["planning_records"][index] is None else "validated",
        "has_support_questions": index in unsupported_by_batch,
        "unsupported_coverage_intents": unsupported_by_batch.get(index, []),
        "module_candidates": [] if state["planning_records"][index] is None else [
            {key: candidate[key] for key in ("name", "objective")}
            for candidate in state["planning_records"][index]["output"]["module_candidates"]
        ],
    } for index, item in enumerate(state["planning_inputs"])]
    unrouted = _unrouted_modules(state)
    can_finish = bool(state["plan"] and _complete_records(state["route_records"])
                      and not _pending(state["gap_records"]) and not unsupported and not unrouted)
    rebuild_blocker = _business_plan_rebuild_blocker(state)
    return {
        "goal": deepcopy(state["goal"]),
        "source_version": state["source_version"], "draft_version": state["draft_version"], "plan_version": state["plan_version"],
        "support_version": state["support_version"],
        "support_review_summary": [{"module_index": item["module_index"],
            "design_count": len(item["missing_test_design_items"]),
            "status": "pending" if state["gap_records"][index] is None else "reviewed"}
            for index, item in enumerate(state["gap_inputs"])],
        "reviewable_designs": _reviewable_designs(state),
        "source_items": _source_index(context), "fact_count": len(state["source_semantics"]["authoritative_facts"]),
        "evidence_count": len(context.action_input["evidence_catalog"]["items"]), "read_evidence_ids": list(state["evidence_reads"]),
        "planning_batches": drafts, "pending_draft_indexes": _pending(state["planning_records"]),
        "plan_available": state["plan"] is not None,
        "module_names": [module["name"] for module in state["plan"]["business_modules"]] if state["plan"] else [],
        "route_batch_count": len(state["route_inputs"]), "pending_route_indexes": _pending(state["route_records"]),
        "unrouted_module_indexes": unrouted,
        "unrouted_modules": _unrouted_module_feedback(state),
        "route_gaps": feedback, "unsupported_designs": unsupported,
        "can_rebuild": rebuild_blocker is None, "rebuild_blocker": rebuild_blocker,
        "unresolved_design_selections": [{"module_index": item["module_index"],
            "test_design_item_indexes": [decision["test_design_item_index"] for decision in item["review"]["decisions"]]}
            for item in feedback if item["review"] and item["review"]["decisions"]],
        "pending_gap_indexes": _pending(state["gap_records"]), "can_finish": can_finish and not state["completed"],
        "completed": state["completed"],
        "instruction": "缺口中的 scope_ids 可直接用于同源取证。整组未路由时，根据 unrouted_modules 核对当前模块划分和草案；重叠划分应重建规划，错误意图应修订对应草案。读取原文不会改变路由状态；没有待路由输入时重复路由不能修复缺口。",
    }


def compact_planning_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """给父协调者保留行动决策所需摘要，完整事实只在专业行动内传递。

    规划领域状态仍按完整内容持久化，``observe_planning_state`` 也继续提供完整
    诊断给内部校验。协调者每次请求都会重新读取观察，如果把 supporting_facts
    和 source_context 原样带回，几轮路由审查后会把同一份原文重复计入模型上下文。
    这里仅去掉可由当前行动重新读取的事实正文，保留索引、裁决和行动范围。
    """

    if not isinstance(observation, dict):
        raise TypeError("规划观察必须是对象")

    compact = deepcopy(observation)
    compact_gaps = []
    for item in list(observation.get("route_gaps") or []):
        if not isinstance(item, dict):
            continue
        review = item.get("review")
        compact_review = None
        if isinstance(review, dict):
            compact_review = {
                "decisions": [
                    {
                        key: deepcopy(decision[key])
                        for key in ("test_design_item_index", "fact_ids", "support_gaps")
                        if key in decision
                    }
                    for decision in list(review.get("decisions") or [])
                    if isinstance(decision, dict)
                ],
            }
        compact_gaps.append({
            key: deepcopy(item[key])
            for key in ("module_index", "module_name", "module_objective", "planning_batch_indexes", "scope_ids")
            if key in item
        } | {
            "missing_test_design_items": [
                {
                    key: deepcopy(design[key])
                    for key in ("test_design_item_index", "coverage_intent", "planning_batch_indexes", "scope_ids")
                    if key in design
                }
                for design in list(item.get("missing_test_design_items") or [])
                if isinstance(design, dict)
            ],
            "review": compact_review,
        })
    compact["route_gaps"] = compact_gaps

    compact["unrouted_modules"] = [
        {
            key: deepcopy(item[key])
            for key in ("module_index", "module_name", "module_objective", "test_design_items")
            if key in item
        }
        for item in list(observation.get("unrouted_modules") or [])
        if isinstance(item, dict)
    ]
    return compact


def _evidence_text(context: ToolExecutionContext, evidence_id: str | None) -> str:
    items = context.action_input["evidence_catalog"]["items"]
    if evidence_id is None:
        value = [{key: deepcopy(item[key]) for key in ("evidence_id", "page_number", "source_offset_start", "source_offset_end", "image_region") if key in item} for item in items]
    else:
        value = next((item for item in items if item["evidence_id"] == evidence_id), None)
        if value is None:
            raise OutputValidationError("取证只能引用本次完整目录中的 evidence_id", details={"evidence_id": evidence_id})
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_planning_evidence(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = deepcopy(_state(context))
    if state["completed"]:
        raise OutputValidationError("自主规划已经完成，不能继续取证")
    evidence_id, offset, max_chars = arguments.get("evidence_id"), arguments.get("offset"), arguments.get("max_chars")
    if evidence_id is not None and (not isinstance(evidence_id, str) or not evidence_id):
        raise OutputValidationError("evidence_id 必须是完整目录的 ID 或 null")
    if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= _EVIDENCE_MAX_CHARS:
        raise OutputValidationError("取证分页参数无效", details={"max_chars_limit": _EVIDENCE_MAX_CHARS})
    text = _evidence_text(context, evidence_id)
    if offset >= len(text):
        raise OutputValidationError("取证起始位置超出完整证据范围", details={"total_chars": len(text)})
    end = min(offset + max_chars, len(text))
    key = evidence_id or ""
    previous = state["evidence_reads"].get(key)
    merged = []
    for start, stop in sorted([*(previous["ranges"] if previous else []), [offset, end]]):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    record = {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "total_chars": len(text), "ranges": merged}
    state["evidence_reads"][key] = record
    _commit(context, state)
    return {
        "evidence_id": evidence_id, "source_ref": deepcopy(state["goal"]["source_ref"]),
        "content_sha256": record["sha256"], "content_format": "json_fragment",
        "offset": offset, "end_offset": end, "total_chars": len(text), "content": text[offset:end],
        "next_offset": end if end < len(text) else None, "resource_fully_read": merged == [[0, len(text)]],
        "new_evidence_read": record != previous,
    }


def _action(context: ToolExecutionContext, kind: str, indexes: list[int], inputs: list[dict[str, Any]], *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    state = _state(context)
    if state["completed"]:
        raise OutputValidationError("自主规划已经完成，不能继续修改或评审")
    return {"kind": kind, "pre_state_hash": state["domain_sha256"], "item_indexes": indexes, "items": deepcopy(inputs), **deepcopy(metadata or {})}


def _check_action(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    state = _state(context)
    if action["pre_state_hash"] != state["domain_sha256"]:
        raise ValueError("自主规划行动基于的领域版本已经变化")
    if len(action["items"]) != len(outputs) or any(not isinstance(value, dict) for value in outputs):
        raise OutputValidationError("自主规划专业结果与行动项数不一致")
    return deepcopy(state)


def _validate_single_candidate(callback: Any, context: ToolExecutionContext, action: dict[str, Any], output: dict[str, Any]) -> None:
    """专业会话内验证完整领域候选，失败时不改变父行动状态。"""
    candidate_context = _isolated_context(context)
    candidate_context.action_state = deepcopy(context.action_state)
    callback(candidate_context, action, [output])


def _question(arguments: dict[str, Any]) -> str:
    value = arguments.get("question")
    if not isinstance(value, str) or not value.strip():
        raise OutputValidationError("修订行动需要说明要核对的具体问题")
    return value.strip()


def _index(arguments: dict[str, Any], count: int) -> int:
    index = arguments.get("item_index")
    if type(index) is not int or not 0 <= index < count:
        raise OutputValidationError("修订行动引用了当前范围之外的 item_index", details={"item_count": count})
    return index


def prepare_source_reinspection(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    kind = arguments.get("source_kind")
    if kind not in {"text", "vision"}:
        raise OutputValidationError("source_kind 只能是 text 或 vision")
    inputs = context.action_input[f"source_{kind}_inputs"]
    index = _index(arguments, len(inputs))
    item = deepcopy(inputs[index])
    item["_planning_feedback"] = {"question": _question(arguments), "previous_source_output": deepcopy(state["source_records"][kind][index]["output"]), "route_gaps": _route_feedback(state)}
    return _action(context, "source", [index], [item], metadata={"source_kind": kind})


def apply_source_reinspection(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    state = _check_action(context, action, outputs)
    kind, index = action["source_kind"], action["item_indexes"][0]
    original = context.action_input[f"source_{kind}_inputs"][index]
    records = deepcopy(state["source_records"])
    records[kind][index] = {"item_index": index, "input_hash": action_state_hash(original), "request_input_hash": action_state_hash(action["items"][0]), "output": deepcopy(outputs[0])}
    try:
        semantics = _merge_sources(context, records)
    except ValueError as error:
        raise OutputValidationError(str(error), details={"stage": "source_reinspection", "source_kind": kind, "item_index": index}) from error
    changed = semantics != state["source_semantics"]
    old_pending = len(state["planning_records"])
    if changed:
        state["source_records"] = records
        state["source_semantics"] = semantics
        state["source_version"] += 1
        inputs = _planning_inputs(context, semantics)
        retained = _retained_records(inputs, state["planning_records"])
        if inputs != state["planning_inputs"]:
            _clear_plan(state)
        state["planning_inputs"], state["planning_records"] = inputs, retained
        _refresh_routes(context, state)
    _commit(context, state)
    return {"changed": changed, "previous_draft_count": old_pending, "reused_draft_count": sum(item is not None for item in state["planning_records"]), **observe_planning_state(context)}


async def reinspect_planning_source(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    action = prepare_source_reinspection(context, arguments)
    output = await context.invoke_agent(
        agent_key="test_source_text_semantics_analyst" if action["source_kind"] == "text" else "test_source_semantics_analyst",
        input_payload=deepcopy(action["items"][0]), item_postprocessor="testing.postprocess_source_semantics_item",
        validate_candidate=partial(_validate_single_candidate, apply_source_reinspection, context, action),
    )
    return apply_source_reinspection(context, action, [output])


def prepare_draft_revision(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    index = _index(arguments, len(state["planning_inputs"]))
    item = deepcopy(state["planning_inputs"][index])
    previous = state["planning_records"][index]
    item["_planning_feedback"] = {"question": _question(arguments), "previous_draft": deepcopy(previous["output"]) if previous else None, "route_gaps": _draft_route_feedback(state, index)}
    if state.get("downstream_feedback"):
        item["_planning_feedback"]["downstream_problems"] = deepcopy(state["downstream_feedback"])
    return _action(context, "draft", [index], [item])


def apply_draft_revision(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    state = _check_action(context, action, outputs)
    index = action["item_indexes"][0]
    item = state["planning_inputs"][index]
    try:
        output = validate_business_plan_draft_output(_isolated_context(context), {"input_payload": item, "output": outputs[0]})
    except ValueError as error:
        raise OutputValidationError(str(error), details={"stage": "draft_revision", "item_index": index}) from error
    previous = state["planning_records"][index]
    changed = previous is None or previous["output"] != output
    if changed:
        state["planning_records"][index] = {"item_index": index, "input_hash": action_state_hash(item), "request_input_hash": action_state_hash(action["items"][0]), "output": output}
        state["draft_version"] += 1
        _clear_plan(state)
    _commit(context, state)
    return {"changed": changed, **observe_planning_state(context)}


async def revise_planning_draft(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    action = prepare_draft_revision(context, arguments)
    output = await context.invoke_agent(
        agent_key="test_business_plan_batcher", input_payload=deepcopy(action["items"][0]),
        validate_candidate=partial(_validate_single_candidate, apply_draft_revision, context, action),
    )
    return apply_draft_revision(context, action, [output])


def prepare_business_plan_rebuild(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    blocker = _business_plan_rebuild_blocker(state)
    if blocker is not None:
        raise OutputValidationError(blocker["reason"], details=blocker)
    item = _consolidation(context, state)
    input_hash = action_state_hash(item)
    if state["plan"] is not None or state["previous_plan"] is not None:
        item["_planning_feedback"] = {
            "previous_plan": deepcopy(state["plan"] or state["previous_plan"]),
            "route_gaps": _route_feedback(state) or deepcopy(state["last_route_feedback"]),
            "unrouted_module_indexes": _unrouted_modules(state),
            "unrouted_modules": _unrouted_module_feedback(state),
            "instruction": "这是历史已展开的规划和真实路由差异；当前目录才是提交依据。整组没有事实路由时核对是否与已有模块重复划分；将相同能力域的覆盖组合并到有真实事实承接的模块，不能删除覆盖组。修正模块及测试点目标，不能复制旧编号、遗漏当前覆盖组或把无事实支持的意图藏入 objective。",
        }
    if state.get("downstream_feedback"):
        item.setdefault("_planning_feedback", {})["downstream_problems"] = deepcopy(state["downstream_feedback"])
    return _action(context, "plan", [0], [item], metadata={"plan_input_hash": input_hash})


def _validate_materialized_plan(context: ToolExecutionContext, input_payload: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """从完整展开组恢复提交引用并复验，禁止裁剪组内原子项后写入计划。"""
    catalog = input_payload["coverage_group_catalog"]
    starts = {group["coverage_items"][0]: group for group in catalog}
    if len(starts) != len(catalog):
        raise ValueError("规划覆盖组目录的首项不唯一")
    candidate = deepcopy(plan)
    for module in candidate.get("business_modules", []):
        for point in module.get("test_points", []):
            for design in point.get("test_designs", []):
                values, ids, offset = design.get("coverage_items", []), [], 0
                while offset < len(values):
                    group = starts.get(values[offset])
                    if group is None or values[offset:offset + len(group["coverage_items"])] != group["coverage_items"]:
                        raise OutputValidationError("规划候选没有原样承接完整覆盖语义组", details={"coverage_item": values[offset]})
                    ids.append(group["coverage_group_id"])
                    offset += len(group["coverage_items"])
                design["coverage_items"] = ids
    normalized = validate_business_plan_output(_isolated_context(context), {"input_payload": input_payload, "output": candidate})
    if normalized != plan:
        raise OutputValidationError("规划候选与当前完整目录的严格验收结果不一致")
    return normalized


def apply_business_plan_rebuild(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    state = _check_action(context, action, outputs)
    plan = _validate_materialized_plan(context, action["items"][0], outputs[0])
    changed = state["plan"] != plan or state["plan_input_hash"] != action["plan_input_hash"]
    if changed:
        if state["gap_inputs"]:
            state["last_route_feedback"] = _route_feedback(state)
        state["plan"] = deepcopy(plan)
        state["plan_input_hash"] = action["plan_input_hash"]
        state["plan_version"] += 1
        _refresh_routes(context, state)
    _commit(context, state)
    return {"changed": changed, **observe_planning_state(context)}


async def rebuild_business_plan(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    # 来源变化后的缺失草案是当前重建行动的必需依赖，共享同一预算及父级原子提交。
    for index in _pending(_state(context)["planning_records"]):
        await revise_planning_draft(context, {"item_index": index, "question": "来源复核后本批事实输入已变化，请依据当前真实事实完整重建草案，保留所有事实并纠正无依据的意图。"})
    action = prepare_business_plan_rebuild(context, arguments)
    output = await context.invoke_agent(
        agent_key="test_business_planner", input_payload=deepcopy(action["items"][0]),
        validate_candidate=partial(_validate_single_candidate, apply_business_plan_rebuild, context, action),
    )
    return apply_business_plan_rebuild(context, action, [output])


def prepare_scope_routing(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    indexes = _pending(state["route_records"])
    if state["plan"] is None or not indexes:
        raise OutputValidationError("当前没有待路由的新规划输入", details={"plan_available": state["plan"] is not None})
    return _action(context, "routes", indexes, [state["route_inputs"][index] for index in indexes])


def apply_scope_routing(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    state = _check_action(context, action, outputs)
    for index, item, output in zip(action["item_indexes"], action["items"], outputs, strict=True):
        normalized = postprocess_planning_scope_routing_item(_isolated_context(context), {"item_input": item, "item_output": output})
        state["route_records"][index] = {"item_index": index, "input_hash": action_state_hash(item), "output": normalized}
    _refresh_gaps(context, state)
    _commit(context, state)
    return observe_planning_state(context)


async def route_planning_scopes(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    action = prepare_scope_routing(context, arguments)
    outputs = await context.invoke_agents(
        agent_key="test_planning_scope_router", inputs=deepcopy(action["items"]),
        item_postprocessor="testing.postprocess_planning_scope_routing_item",
    )
    return apply_scope_routing(context, action, outputs)


def prepare_route_gap_review(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    selections = arguments.get("design_selections") or []
    if selections:
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip() or _pending(state["gap_records"]):
            raise OutputValidationError("定向复核需要先完成初次支持审查，并给出具体待核实问题")
        try:
            items = prepare_planning_route_repairs(_isolated_context(context), {
                "plan": state["plan"], "prepared_items": state["route_inputs"],
                "route_records": state["route_records"], "design_selections": selections,
                "additional_scope_ids": arguments.get("additional_scope_ids", []),
            })["items"]
        except OutputValidationError:
            # 选择校验已经提供真实非法位置和合法范围，不能在行动边界丢掉结构化信息。
            raise
        except ValueError as error:
            raise OutputValidationError(str(error)) from error
        current = current_planning_support_decisions(_isolated_context(context), {
            "plan": state["plan"], "prepared_items": state["route_inputs"], "route_records": state["route_records"],
            "repair_records": state["gap_records"], "review_revisions": state["route_review_revisions"],
        })
        for item in items:
            item["_planning_feedback"] = {"question": question.strip(),
                "previous_decisions": [deepcopy(current[item["module_index"]][design["test_design_item_index"]])
                                       for design in item["missing_test_design_items"]],
                "downstream_problems": deepcopy(state.get("downstream_feedback") or [])}
        previous = set().union(*(planning_support_design_hashes(record["input"]) for record in state["route_review_revisions"]))
        if any(planning_support_design_hashes(item) & previous for item in items):
            raise OutputValidationError("相同意图和证据已经复核，改变组合或问法不能再次抽取结论；请修订错误草案或补充真实证据")
        return _action(context, "support_revision", list(range(len(items))), items)
    if arguments.get("additional_scope_ids"):
        raise OutputValidationError("补充来源范围必须同时选择具体待复核意图")
    indexes = _pending(state["gap_records"])
    if not indexes:
        raise OutputValidationError("初次支持审查已完成；定向复核已有结论必须填写非空 design_selections 和具体 question", details={
            "reviewable_designs": observe_planning_state(context)["reviewable_designs"],
            "selection_fields": ["module_index", "test_design_item_indexes"],
        })
    return _action(context, "gaps", indexes, [state["gap_inputs"][index] for index in indexes])


def apply_route_gap_review(context: ToolExecutionContext, action: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    state = _check_action(context, action, outputs)
    for index, item, output in zip(action["item_indexes"], action["items"], outputs, strict=True):
        try:
            normalized = postprocess_planning_route_repair_item(_isolated_context(context), {"item_input": item, "item_output": output})
        except ValueError as error:
            raise OutputValidationError(str(error), details={"stage": "route_gap_review", "item_index": index}) from error
        record = {"item_index": index, "input_hash": action_state_hash(item), "output": normalized}
        if action["kind"] == "support_revision":
            state["route_review_revisions"].append({**record, "input": deepcopy(item)})
        else:
            state["gap_records"][index] = record
    current_planning_support_decisions(_isolated_context(context), {
        "plan": state["plan"], "prepared_items": state["route_inputs"], "route_records": state["route_records"],
        "repair_records": state["gap_records"], "review_revisions": state["route_review_revisions"],
    })
    state["support_version"] += 1
    state["last_route_feedback"] = _route_feedback(state)
    _commit(context, state)
    return observe_planning_state(context)


async def review_planning_route_gaps(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    action = prepare_route_gap_review(context, arguments)
    outputs = await context.invoke_agents(
        agent_key="test_planning_route_gap_reviewer", inputs=deepcopy(action["items"]),
        item_postprocessor="testing.postprocess_planning_route_repair_item",
    )
    return apply_route_gap_review(context, action, outputs)


def _resolved_output(context: ToolExecutionContext) -> dict[str, Any]:
    state = _state(context)
    observation = observe_planning_state(context)
    if state["plan"] is None:
        raise OutputValidationError("尚未建立业务规划，不能接受规划；可依据当前草案调用 rebuild_business_plan", details={
            "plan_available": False, "pending_draft_indexes": observation["pending_draft_indexes"],
        })
    if (not _complete_records(state["route_records"])
            or _pending(state["gap_records"]) or observation["unsupported_designs"] or observation["unrouted_module_indexes"]):
        raise OutputValidationError("当前规划仍有未完成路由或无来源支持的覆盖意图，不能提交", details={
            key: observation[key] for key in ("plan_available", "pending_draft_indexes", "pending_route_indexes", "pending_gap_indexes", "unsupported_designs", "unrouted_modules")
        })
    source = _consolidation(context, state)
    if state["plan_input_hash"] != action_state_hash(source):
        raise ValueError("自主规划当前计划与已验收草案版本不一致")
    _validate_materialized_plan(context, source, state["plan"])
    plan = merge_planning_scope_routes(_isolated_context(context), {
        "plan": state["plan"], "prepared_items": state["route_inputs"],
        "route_records": state["route_records"], "repair_records": state["gap_records"],
        "review_revisions": state["route_review_revisions"],
    })
    if len(plan["business_modules"]) != len(state["plan"]["business_modules"]):
        raise OutputValidationError("仍有未获得真实事实路由的规划模块，不能通过合并时删除模块来完成规划")
    return {"plan": plan, "source_semantics": deepcopy(state["source_semantics"])}


def finish_planning(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    _resolved_output(context)
    state = deepcopy(_state(context))
    state["completed"] = True
    _commit(context, state)
    return {"completed": True}


def validate_planning_resolution(context: ToolExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
    state = _state(context)
    output = arguments.get("output")
    if not isinstance(output, dict) or output.get("completed") is not True or state["completed"] is not True:
        raise OutputValidationError("自主规划必须通过 finish_planning 才能提交最终结果")
    if action_state_hash(arguments.get("input_payload")) != state["input_sha256"]:
        raise ValueError("自主规划最终验收输入与持久状态不一致")
    return _resolved_output(context)
