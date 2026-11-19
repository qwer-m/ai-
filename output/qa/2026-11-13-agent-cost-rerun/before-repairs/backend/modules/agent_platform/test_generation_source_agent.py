"""单主 Agent 的来源准备行动；原始证据及中间回执随领域状态恢复。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from core.db.model_defs import AgentRun

from .action_journal import action_state_hash
from .output_repair import OutputValidationError
from .sources import SOURCE_ARTIFACT_KEY, SourceSnapshot, assert_same_source, persisted_source_snapshot
from .test_generation_planning import (
    prepare_business_plan_batches,
    resolve_requirement_evidence,
    validate_business_plan_draft_output,
)
from .test_generation_semantics import (
    merge_source_authority,
    merge_source_semantics,
    prepare_authority_reconciliation,
    prepare_source_semantics,
)


def _digest(source: dict[str, Any]) -> str:
    return action_state_hash({key: value for key, value in source.items() if key != "source_sha256"})


def _seal(source: dict[str, Any]) -> dict[str, Any]:
    source["source_sha256"] = _digest(source)
    return source


def _pending(records: list[Any]) -> list[int]:
    return [index for index, record in enumerate(records) if record is None]


def _validate(source: dict[str, Any], context=None) -> None:
    if (not isinstance(source, dict) or source.get("schema_version") != 1
            or source.get("source_sha256") != _digest(source)):
        raise ValueError("来源准备状态或完整内容指纹无效")
    if context is not None:
        if source["input_sha256"] != action_state_hash(context.action_input):
            raise ValueError("来源准备状态与本次完整运行输入不一致")
        run = context.db.get(AgentRun, context.run_id)
        if run is None or run.project_id != context.project_id or run.user_id != context.user_id:
            raise ValueError("来源准备无法取得当前用户和项目的真实运行")
        snapshot = persisted_source_snapshot(run)
        if snapshot is None:
            raise ValueError("来源准备缺少不可变运行来源")
        assert_same_source(snapshot, SourceSnapshot.from_dict(source["source_ref"]))


def hydrate_source_context(context, source: dict[str, Any] | None = None):
    """只给隔离上下文注入日志中已验收的来源产物，不修改父上下文产物。"""
    current = source if source is not None else context.action_state.get("source")
    artifacts = deepcopy(context.artifacts)
    if current is not None:
        _validate(current)
        artifacts.update(deepcopy(current["artifacts"]))
    return replace(context, artifacts=artifacts, executed_tools=[], tool_calls=[])


def initialize_source(context, arguments: dict[str, Any]) -> dict[str, Any]:
    """确定性读取唯一真实来源并准备专家输入，不执行模型调用或数据库写入。"""
    seed = {}
    if SOURCE_ARTIFACT_KEY in context.artifacts:
        seed[SOURCE_ARTIFACT_KEY] = deepcopy(context.artifacts[SOURCE_ARTIFACT_KEY])
    isolated = replace(context, artifacts=seed, executed_tools=[], tool_calls=[])
    evidence = resolve_requirement_evidence(isolated, {
        "requirement": arguments.get("requirement"),
        "requirement_doc_id": arguments.get("requirement_doc_id"),
    })
    prepared = prepare_source_semantics(isolated, {
        "requirement": evidence["requirement"],
        "evidence_source": evidence["source"],
        "evidence_catalog": evidence["evidence_catalog"],
    })
    source = _seal({
        "schema_version": 1,
        "input_sha256": action_state_hash(arguments),
        "source_ref": SourceSnapshot.from_dict(evidence["source"]).to_dict(),
        "evidence": deepcopy(evidence),
        "text_inputs": deepcopy(prepared["text_items"]),
        "text_records": [None] * len(prepared["text_items"]),
        "vision_inputs": deepcopy(prepared["vision_items"]),
        "vision_records": [None] * len(prepared["vision_items"]),
        "source_semantics": None,
        "authority_inputs": [], "authority_records": [], "source_authority": None,
        "planning_inputs": [], "planning_records": [],
        "case_budget": arguments["case_budget"],
        "batch_case_limit": arguments["batch_case_limit"],
        "analysis_reuse": None, "confirmed_source_decisions": {},
        "artifacts": deepcopy(isolated.artifacts),
    })
    source_run_id = arguments.get("source_context_run_id")
    if source_run_id is not None:
        from .test_generation_source_context import reuse_source_context

        source, confirmations = reuse_source_context(isolated, source, source_run_id)
        source["confirmed_source_decisions"] = deepcopy(confirmations)
        isolated.confirmed_conflicts = deepcopy(confirmations)
        _complete_source_analysis(isolated, source)
        source["artifacts"] = deepcopy(isolated.artifacts)
        _seal(source)
    # 初始化器尚未把 raw input 放入 action_input 时，也严格检查 Run 固定来源。
    _validate(source, replace(context, action_input=deepcopy(arguments)))
    return source


def _source_item_index(item: dict[str, Any], index: int, completed: bool) -> dict[str, Any]:
    """仅公开可选择的页码、证据编号和坐标范围，不把整页原文塞进观察。"""
    pages = item["pages"] if item.get("source_kind") == "document_batch" else [item]
    scopes = []
    page_refs = []
    for page in pages:
        page_number = page.get("page_number")
        if page_number is not None:
            page_refs.append({key: deepcopy(page[key]) for key in ("page_number", "region") if key in page})
        for scope in page.get("source_scopes", []):
            reference = {key: deepcopy(scope[key]) for key in (
                "scope_id", "source_offset_start", "source_offset_end", "image_region",
            ) if key in scope}
            if page_number is not None:
                reference["page_number"] = page_number
            if reference not in scopes:
                scopes.append(reference)
    return {"item_index": index, "source_kind": item["source_kind"],
            "completed": completed, "pages": page_refs, "scopes": scopes}


def _analysis_reuse_summary(source: dict[str, Any]) -> dict[str, Any] | None:
    """模型只接收复用范围摘要，逐条原调用指纹保留在结构化状态供审计。"""
    reuse = source.get("analysis_reuse")
    if reuse is None:
        return None
    return {
        "source_run_id": reuse["source_run_id"], "source_node_id": reuse["source_node_id"],
        "revalidated_counts": {kind: sum(item["source_kind"] == kind for item in reuse["records"])
                               for kind in ("text", "vision")},
        "confirmed_decision_count": len(source["confirmed_source_decisions"]),
        "authority_baseline_revalidated": bool(reuse["authority_baseline"]),
        "model_requests_sent": reuse["model_requests_sent"],
    }


def observe_source(context, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """投影当前真实待办；允许根观察器仅传入已恢复来源状态。"""
    current = source if source is not None else context.action_state.get("source")
    _validate(current, context)
    text_pending, vision_pending = (_pending(current[f"{kind}_records"]) for kind in ("text", "vision"))
    authority_pending = _pending(current["authority_records"])
    planning_pending = _pending(current["planning_records"])
    actions = {}
    if text_pending or vision_pending:
        actions["analyze_source"] = {
            "text_item_indexes": text_pending, "vision_item_indexes": vision_pending,
        }
    elif current["source_authority"] is None:
        if current["source_semantics"] is None or not authority_pending:
            raise ValueError("来源语义未完整验收或缺少待治理范围")
        actions["govern_source"] = {"item_indexes": authority_pending}
    elif planning_pending:
        actions["draft_source_plan"] = {"item_indexes": planning_pending}
    ready = bool(current["source_authority"] is not None and current["planning_inputs"] and not planning_pending)
    return {
        "ready": ready,
        "source_ref": deepcopy(current["source_ref"]),
        "source_items": {
            kind: {"item_count": len(current[f"{kind}_inputs"]),
                   "pending_indexes": _pending(current[f"{kind}_records"]),
                   "items": [_source_item_index(item, index, current[f"{kind}_records"][index] is not None)
                             for index, item in enumerate(current[f"{kind}_inputs"])]}
            for kind in ("text", "vision")
        },
        "source_fact_count": len((current["source_semantics"] or {}).get("authoritative_facts", [])),
        "analysis_reuse": _analysis_reuse_summary(current),
        "authority_ready": current["source_authority"] is not None,
        "planning_item_count": len(current["planning_inputs"]),
        "pending_planning_indexes": planning_pending,
        "available_actions": actions,
    }


def _begin(context, action: str):
    source = context.action_state.get("source")
    observation = observe_source(context, source)
    if action not in observation["available_actions"]:
        raise OutputValidationError("当前来源状态不允许该行动", details={"available_actions": observation["available_actions"]})
    return deepcopy(source), action_state_hash(context.action_state)


def _indexes(arguments: dict[str, Any], pending: list[int]) -> list[int]:
    indexes = arguments.get("item_indexes")
    if (not isinstance(indexes, list) or not indexes or any(type(index) is not int for index in indexes)
            or len(indexes) != len(set(indexes)) or any(index not in pending for index in indexes)):
        raise OutputValidationError("item_indexes 必须选择当前待处理范围中的不重复索引", details={"pending_indexes": pending})
    return indexes


def _accept_records(source: dict[str, Any], kind: str, indexes: list[int], outputs: list[dict[str, Any]]) -> None:
    if len(indexes) != len(outputs):
        raise ValueError("来源专业调用输出与实际输入数量不一致")
    for index, output in zip(indexes, outputs, strict=True):
        if not isinstance(output, dict):
            raise ValueError("来源专业调用没有返回已验收对象")
        source[f"{kind}_records"][index] = {
            "item_index": index,
            "input_hash": action_state_hash(source[f"{kind}_inputs"][index]),
            "output": deepcopy(output),
        }


def _prepared_input(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_source": deepcopy(source["evidence"]["source"]),
        "evidence_catalog": deepcopy(source["evidence"]["evidence_catalog"]),
        "source_text_inputs": deepcopy(source["text_inputs"]),
        "source_text_records": deepcopy(source["text_records"]),
        "source_vision_inputs": deepcopy(source["vision_inputs"]),
        "source_vision_records": deepcopy(source["vision_records"]),
        "source_authority": deepcopy(source["source_authority"]),
        "planning_inputs": deepcopy(source["planning_inputs"]),
        "planning_records": deepcopy(source["planning_records"]),
        "case_budget": source["case_budget"],
        "batch_case_limit": source["batch_case_limit"],
    }


def _commit(context, source: dict[str, Any], original_hash: str, isolated) -> dict[str, Any]:
    if action_state_hash(context.action_state) != original_hash:
        raise ValueError("来源行动执行期间完整领域状态已变化")
    source["artifacts"] = deepcopy(isolated.artifacts)
    _seal(source)
    context.action_state["source"] = source
    observation = observe_source(context, source)
    if observation["ready"]:
        context.action_state["prepared_input"] = _prepared_input(source)
    return observation


async def analyze_source(context, arguments: dict[str, Any]) -> dict[str, Any]:
    """由主 Agent 选择文本或图像的真实待处理范围，独立批次按平台额度并发。"""
    source, original_hash = _begin(context, "analyze_source")
    kind = arguments.get("source_kind")
    if kind not in {"text", "vision"}:
        raise OutputValidationError("source_kind 必须为 text 或 vision")
    indexes = _indexes(arguments, _pending(source[f"{kind}_records"]))
    isolated = hydrate_source_context(context, source)
    outputs = await isolated.invoke_agents(
        agent_key="test_source_text_semantics_analyst" if kind == "text" else "test_source_semantics_analyst",
        inputs=[deepcopy(source[f"{kind}_inputs"][index]) for index in indexes],
        item_postprocessor="testing.postprocess_source_semantics_item",
    )
    _accept_records(source, kind, indexes, outputs)
    _complete_source_analysis(isolated, source)
    return _commit(context, source, original_hash, isolated)


def _complete_source_analysis(isolated, source):
    """新分析与真实历史分析重验后共用同一完整合并、治理准入入口。"""
    if not _pending(source["text_records"]) and not _pending(source["vision_records"]):
        source["source_semantics"] = merge_source_semantics(isolated, {
            "text_inputs": source["text_inputs"], "text_records": source["text_records"],
            "vision_inputs": source["vision_inputs"], "vision_records": source["vision_records"],
        })
        arguments = {"authoritative_facts": source["source_semantics"]["authoritative_facts"]}
        if source.get("authority_baseline") is not None:
            arguments["authority_baseline"] = source["authority_baseline"]
        source["authority_inputs"] = prepare_authority_reconciliation(isolated, arguments)["items"]
        source["authority_records"] = [None] * len(source["authority_inputs"])


async def govern_source(context, arguments: dict[str, Any]) -> dict[str, Any]:
    """对完整来源事实进行效力治理，结果确定性验收后才开放初始草案。"""
    source, original_hash = _begin(context, "govern_source")
    pending = _pending(source["authority_records"])
    indexes = _indexes(arguments, pending) if "item_indexes" in arguments else pending
    isolated = hydrate_source_context(context, source)
    outputs = await isolated.invoke_agents(
        agent_key="test_authority_reconciliation_reviewer",
        inputs=[deepcopy(source["authority_inputs"][index]) for index in indexes],
        item_postprocessor="testing.postprocess_authority_reconciliation_item",
    )
    _accept_records(source, "authority", indexes, outputs)
    if not _pending(source["authority_records"]):
        source["source_authority"] = merge_source_authority(isolated, {
            "source_semantics": source["source_semantics"],
            "prepared_items": source["authority_inputs"],
            "reconciliation_records": source["authority_records"],
        })
        source["planning_inputs"] = prepare_business_plan_batches(isolated, {
            "planning_scopes": source["source_authority"]["source_semantics"]["planning_scopes"],
            "case_budget": source["case_budget"],
        })["items"]
        source["planning_records"] = [None] * len(source["planning_inputs"])
    return _commit(context, source, original_hash, isolated)


async def draft_source_plan(context, arguments: dict[str, Any]) -> dict[str, Any]:
    """仅抽取主 Agent 当前选择的初始草案；已有验收索引不能重复抽取。"""
    source, original_hash = _begin(context, "draft_source_plan")
    indexes = _indexes(arguments, _pending(source["planning_records"]))
    isolated = hydrate_source_context(context, source)
    outputs = await isolated.invoke_agents(
        agent_key="test_business_plan_batcher",
        inputs=[deepcopy(source["planning_inputs"][index]) for index in indexes],
        item_postprocessor=None,
    )
    outputs = [validate_business_plan_draft_output(isolated, {
        "input_payload": source["planning_inputs"][index], "output": output,
    }) for index, output in zip(indexes, outputs, strict=True)]
    _accept_records(source, "planning", indexes, outputs)
    return _commit(context, source, original_hash, isolated)
