from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

from .requirement_semantic_graph import (
    adapt_workflows_from_semantic_graph,
    empty_requirement_semantic_graph,
    normalize_requirement_semantic_graph,
    project_functional_architecture_from_graph,
)

REQUIREMENT_SEMANTIC_CONTRACT_VERSION = "requirement-semantic-v2"
LEGACY_REQUIREMENT_SEMANTIC_CONTRACT_VERSION = "requirement-semantic-v1"
CASE_SEMANTIC_CONTRACT_VERSION = "case-semantic-v1"
CASE_SEMANTIC_CONTRACT_ABORT_CODE = "CASE_SEMANTIC_CONTRACT_FAILED"
MAX_WORKFLOW_STEPS = 16

STATE_SOURCE_VALUES = (
    "previous_stage",
    "current_stage",
    "same_case_setup",
    "external_fixture",
    "historical_state",
    "system_event",
    "unknown",
)
STATE_SCOPE_VALUES = (
    "entity",
    "case",
    "module",
    "workflow",
    "cross_module",
    "global",
    "unknown",
)
STATE_POLARITY_VALUES = ("positive", "negative", "unknown")
STATE_TEMPORAL_VALUES = (
    "before_case",
    "after_previous_stage",
    "during_case",
    "after_case",
    "historical",
    "unknown",
)

STATE_SOURCES = set(STATE_SOURCE_VALUES)
STATE_SCOPES = set(STATE_SCOPE_VALUES)
STATE_POLARITIES = set(STATE_POLARITY_VALUES)
STATE_TEMPORALS = set(STATE_TEMPORAL_VALUES)
TYPED_STATE_REQUIRED_FIELDS = (
    "entity",
    "state",
    "source",
    "scope",
    "polarity",
    "temporal",
    "confidence",
    "evidence",
)
MODULE_ROLE_VALUES = ("primary", "source", "target", "related", "unknown")
MODULE_ROLES = set(MODULE_ROLE_VALUES)
SCOPE_STATUSES = {"in_scope", "out_of_scope", "unknown"}

_MAX_MODULES = 40
_MAX_INTERACTIONS = 40
_MAX_EVIDENCE = 6
_MAX_CASE_STATE_ITEMS = 16
_REQUIRED_CASE_SEMANTIC_ARRAY_FIELDS = (
    "module_candidates",
    "interaction_ids",
    "workflow_stage_candidates",
    "precondition_states",
    "produced_states",
)
_GENERIC_EVIDENCE_KEYS = {
    "用户",
    "页面",
    "系统",
    "功能",
    "模块",
    "操作",
    "数据",
    "结果",
    "正常",
    "成功",
    "内容",
    "user",
    "page",
    "system",
    "feature",
    "module",
    "operation",
    "data",
    "result",
    "normal",
    "success",
    "content",
}


def canonicalize_requirement_semantic_candidate(
    value: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """在唯一解析边界统一语义契约形状，不推断或改写业务语义。"""

    if not isinstance(value, dict):
        return {}, {
            "status": "candidate_not_object",
            "changed": False,
            "wrapper_unwrapped": False,
            "nested_workflows_promoted": False,
            "nested_workflows_duplicate_removed": False,
            "workflow_location_conflict": False,
            "extra_non_primary_workflow_count": 0,
            "primary_workflow_reordered": False,
            "architecture_empty_collections_added": [],
        }

    nested_contract = value.get("requirement_semantic_contract")
    wrapper_unwrapped = isinstance(nested_contract, dict)
    contract = copy.deepcopy(nested_contract if wrapper_unwrapped else value)
    architecture = contract.get("functional_architecture")
    architecture = architecture if isinstance(architecture, dict) else None
    graph_declared = isinstance(contract.get("semantic_graph"), dict) or isinstance(
        contract.get("evidence_facts"),
        list,
    )
    model_functional_architecture_ignored = bool(graph_declared and architecture is not None)
    if model_functional_architecture_ignored:
        # v2 中平铺架构只能由图投影，模型附带的副本不参与任何判断。
        contract.pop("functional_architecture", None)
        architecture = None

    architecture_empty_collections_added: list[str] = []
    if architecture is not None:
        for field in ("functional_modules", "module_interactions"):
            if field not in architecture:
                architecture[field] = []
                architecture_empty_collections_added.append(field)

    nested_workflows_promoted = False
    nested_workflows_duplicate_removed = False
    workflow_location_conflict = False
    if architecture is not None and "workflow_blueprints" in architecture:
        nested_workflows = architecture.pop("workflow_blueprints")
        if "workflow_blueprints" not in contract:
            contract["workflow_blueprints"] = nested_workflows
            nested_workflows_promoted = True
        elif contract.get("workflow_blueprints") == nested_workflows:
            nested_workflows_duplicate_removed = True
        else:
            # 根级字段是公开契约的唯一来源；冲突只记录诊断，禁止嵌套副本污染拓扑锚点。
            workflow_location_conflict = True

    extra_non_primary_workflow_count = 0
    primary_workflow_reordered = False
    workflows = contract.get("workflow_blueprints")
    if isinstance(workflows, list) and len(workflows) > 1:
        primary_indexes = [
            index
            for index, item in enumerate(workflows)
            if isinstance(item, dict) and item.get("primary") is True
        ]
        if len(primary_indexes) == 1 and all(
            index == primary_indexes[0]
            or (isinstance(item, dict) and item.get("primary") is False)
            for index, item in enumerate(workflows)
        ):
            extra_non_primary_workflow_count = len(workflows) - 1
            if primary_indexes[0] != 0:
                primary_workflow = workflows[primary_indexes[0]]
                contract["workflow_blueprints"] = [
                    copy.deepcopy(primary_workflow),
                    *[
                        copy.deepcopy(item)
                        for index, item in enumerate(workflows)
                        if index != primary_indexes[0]
                    ],
                ]
                primary_workflow_reordered = True

    changed = bool(
        wrapper_unwrapped
        or nested_workflows_promoted
        or nested_workflows_duplicate_removed
        or workflow_location_conflict
        or primary_workflow_reordered
        or architecture_empty_collections_added
        or model_functional_architecture_ignored
    )
    return contract, {
        "status": "canonicalized" if changed else "unchanged",
        "changed": changed,
        "wrapper_unwrapped": wrapper_unwrapped,
        "nested_workflows_promoted": nested_workflows_promoted,
        "nested_workflows_duplicate_removed": nested_workflows_duplicate_removed,
        "workflow_location_conflict": workflow_location_conflict,
        "extra_non_primary_workflow_count": int(
            extra_non_primary_workflow_count
        ),
        "primary_workflow_reordered": primary_workflow_reordered,
        "architecture_empty_collections_added": (
            architecture_empty_collections_added
        ),
        "model_functional_architecture_ignored": (
            model_functional_architecture_ignored
        ),
    }


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).lower()
    return re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", text)


def _slug(value: Any, *, fallback: str) -> str:
    text = unicodedata.normalize("NFKC", _text(value))
    text = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", text).strip("_")
    return (text or fallback)[:80]


def _text_list(value: Any, *, limit: int = _MAX_EVIDENCE) -> list[str]:
    values = value if isinstance(value, list) else ([value] if _text(value) else [])
    output: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _text(item)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text[:240])
        if len(output) >= max(1, int(limit)):
            break
    return output


def _fact_id_list(value: Any, *, limit: int = 32) -> list[str]:
    """保留模型声明的事实身份，不把证据文本或其他值转换成事实 ID。"""

    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        fact_id = _text(item)
        if not fact_id or fact_id in seen:
            continue
        seen.add(fact_id)
        # 不截断 ID；超长或拼写不同的声明必须在图引用校验中作为未知身份拒绝。
        output.append(fact_id)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _confidence(value: Any, *, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return round(max(0.0, min(1.0, parsed)), 4)


def _enum(value: Any, allowed: set[str], *, default: str = "unknown") -> str:
    normalized = _enum_token(value)
    return normalized if normalized in allowed else default


def _enum_token(value: Any) -> str:
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def evidence_supported(evidence: list[str], source_text: str) -> bool:
    """只验证证据是否来自当前输入，不通过关键词猜测语义。"""
    source = _key(source_text)
    if not source:
        return False
    evidence_keys = [_key(item) for item in evidence]
    return bool(evidence_keys) and all(
        len(item_key) >= 2
        and item_key not in _GENERIC_EVIDENCE_KEYS
        and item_key in source
        for item_key in evidence_keys
    )


def _typed_state_schema_rejection(
    value: Any,
    *,
    state_role: str = "",
) -> dict[str, Any]:
    """只记录字段级诊断，避免把整份模型原文写入日志。"""
    if not isinstance(value, dict):
        return {"reason": "state_not_object"}

    missing_or_invalid_fields: list[str] = []
    invalid_enum_fields: list[str] = []
    invalid_enum_values: dict[str, str] = {}
    for field in ("entity", "state"):
        if not _text(value.get(field)):
            missing_or_invalid_fields.append(field)
    if not isinstance(value.get("evidence"), list):
        missing_or_invalid_fields.append("evidence")
    if not _text(value.get("confidence")):
        missing_or_invalid_fields.append("confidence")

    enum_contracts = (
        ("source", STATE_SOURCES),
        ("scope", STATE_SCOPES),
        ("polarity", STATE_POLARITIES),
        ("temporal", STATE_TEMPORALS),
    )
    for field, allowed in enum_contracts:
        token = _enum_token(value.get(field))
        if token in allowed:
            continue
        missing_or_invalid_fields.append(field)
        invalid_enum_fields.append(field)
        if token:
            invalid_enum_values[field] = token[:80]

    source = _enum_token(value.get("source"))
    if state_role == "precondition" and source == "current_stage":
        return {
            "reason": "source_not_allowed_for_precondition",
            "missing_or_invalid_fields": ["source"],
            "incompatible_role_fields": ["source"],
        }

    rejection: dict[str, Any] = {"reason": "state_schema_invalid"}
    if missing_or_invalid_fields:
        field_order = {
            field: index
            for index, field in enumerate(
                (
                    "entity",
                    "state",
                    "source",
                    "scope",
                    "polarity",
                    "temporal",
                    "evidence",
                    "confidence",
                )
            )
        }
        rejection["missing_or_invalid_fields"] = sorted(
            set(missing_or_invalid_fields),
            key=lambda field: field_order.get(field, len(field_order)),
        )
    if invalid_enum_fields:
        rejection["invalid_enum_fields"] = list(dict.fromkeys(invalid_enum_fields))
    if invalid_enum_values:
        rejection["invalid_enum_values"] = invalid_enum_values
    return rejection


def normalize_typed_state(
    value: Any,
    *,
    source_text: str = "",
    state_role: str = "",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    entity = _text(value.get("entity"))
    state = _text(value.get("state"))
    if not entity or not state:
        return {}
    source = _enum_token(value.get("source"))
    scope = _enum_token(value.get("scope"))
    polarity = _enum_token(value.get("polarity"))
    temporal = _enum_token(value.get("temporal"))
    if (
        source not in STATE_SOURCES
        or scope not in STATE_SCOPES
        or polarity not in STATE_POLARITIES
        or temporal not in STATE_TEMPORALS
    ):
        return {}
    if state_role == "precondition" and source == "current_stage":
        return {}
    evidence = _text_list(value.get("evidence"))
    normalized = {
        "entity": entity[:100],
        "state": state[:100],
        "source": source,
        "scope": scope,
        "polarity": polarity,
        "temporal": temporal,
        "evidence": evidence,
        "evidence_verified": evidence_supported(evidence, source_text),
        "confidence": _confidence(value.get("confidence")),
    }
    if "fact_ids" in value:
        # fact_ids 是事实身份，不能在证据文本投影后被规范化流程丢弃。
        normalized["fact_ids"] = _fact_id_list(value.get("fact_ids"))
    return normalized


def normalize_typed_states(
    values: Any,
    *,
    source_text: str = "",
    limit: int = _MAX_CASE_STATE_ITEMS,
    rejected_semantic_items: list[dict[str, Any]] | None = None,
    item_type: str = "typed_state",
    state_role: str = "",
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        if values not in (None, "") and rejected_semantic_items is not None:
            rejected_semantic_items.append(
                {"item_type": item_type, "reason": "collection_not_list"}
            )
        return []
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for index, value in enumerate(values, start=1):
        state = normalize_typed_state(
            value,
            source_text=source_text,
            state_role=state_role,
        )
        if not state:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": item_type,
                        "item_index": index,
                        **_typed_state_schema_rejection(
                            value,
                            state_role=state_role,
                        ),
                    }
                )
            continue
        if state.get("confidence", 0.0) <= 0:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": item_type,
                        "item_index": index,
                        "identifier": f"{state.get('entity')}:{state.get('state')}",
                        "reason": "confidence_not_positive",
                    }
                )
            continue
        if state.get("evidence_verified") is not True:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": item_type,
                        "item_index": index,
                        "identifier": f"{state.get('entity')}:{state.get('state')}",
                        "reason": "evidence_unverified",
                        "evidence": list(state.get("evidence") or []),
                    }
                )
            continue
        marker = (
            _key(state.get("entity")),
            _key(state.get("state")),
            str(state.get("source") or ""),
            str(state.get("scope") or ""),
            str(state.get("polarity") or ""),
            str(state.get("temporal") or ""),
        )
        if marker in seen:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": item_type,
                        "item_index": index,
                        "identifier": f"{state.get('entity')}:{state.get('state')}",
                        "reason": "duplicate",
                    }
                )
            continue
        seen.add(marker)
        output.append(state)
        if len(output) >= max(1, int(limit)):
            break
    return output


def graph_typed_state_identity_rejections(
    workflows: Any,
    semantic_contract: Any,
) -> list[dict[str, Any]]:
    """校验工作流 typed state 的事实身份属于当前图步骤的局部子图。

    这里只使用 fact_ids 和图结构判断相关性；evidence 即使文本相同，也不能替代
    缺失、未知或不相关的事实身份。
    """

    if not isinstance(workflows, list) or not isinstance(semantic_contract, dict):
        return []

    facts = {
        _text(item.get("fact_id"))
        for item in (semantic_contract.get("evidence_facts") or [])
        if isinstance(item, dict) and _text(item.get("fact_id"))
    }
    graph = semantic_contract.get("semantic_graph")
    graph = graph if isinstance(graph, dict) else {}
    nodes_by_id = {
        _text(item.get("node_id")): item
        for item in (graph.get("nodes") or [])
        if isinstance(item, dict) and _text(item.get("node_id"))
    }
    edges_by_id = {
        _text(item.get("edge_id")): item
        for item in (graph.get("edges") or [])
        if isinstance(item, dict) and _text(item.get("edge_id"))
    }
    flow_nodes = {
        node_id: node
        for node_id, node in nodes_by_id.items()
        if _text(node.get("workflow_role")) != "none"
    }
    rejections: list[dict[str, Any]] = []

    for workflow_index, workflow in enumerate(workflows, start=1):
        if not isinstance(workflow, dict) or not isinstance(workflow.get("steps"), list):
            continue
        for step_index, step in enumerate(workflow.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            step_path = (
                f"$.workflow_blueprints[{workflow_index - 1}]"
                f".steps[{step_index - 1}]"
            )
            step_fact_ids = set(_fact_id_list(step.get("fact_ids")))
            matching_node_ids = sorted(
                node_id
                for node_id, node in flow_nodes.items()
                if step_fact_ids & set(_fact_id_list(node.get("fact_ids")))
            )
            mapped_node_id = matching_node_ids[0] if len(matching_node_ids) == 1 else ""

            relevant_fact_ids: set[str] = set()
            if mapped_node_id:
                relevant_fact_ids.update(
                    _fact_id_list(nodes_by_id[mapped_node_id].get("fact_ids"))
                )

                relation_ids = _fact_id_list(
                    step.get("relation_ids")
                    if isinstance(step.get("relation_ids"), list)
                    else step.get("graph_relation_ids")
                )
                for relation_id in relation_ids:
                    edge = edges_by_id.get(relation_id)
                    if edge:
                        relevant_fact_ids.update(_fact_id_list(edge.get("fact_ids")))

                scope_ids = {
                    _text(candidate.get("scope_id"))
                    for candidate in (step.get("scope_candidates") or [])
                    if isinstance(candidate, dict) and _text(candidate.get("scope_id"))
                }
                for scope_id in scope_ids:
                    scope_node = nodes_by_id.get(scope_id)
                    if scope_node:
                        relevant_fact_ids.update(
                            _fact_id_list(scope_node.get("fact_ids"))
                        )
                # ownership 边属于节点与 scope 的绑定事实，即使步骤不重复声明该边，也应可引用。
                for edge in edges_by_id.values():
                    if _text(edge.get("type")) != "owns":
                        continue
                    endpoints = {
                        _text(edge.get("source_node_id")),
                        _text(edge.get("target_node_id")),
                    }
                    if mapped_node_id in endpoints and scope_ids & endpoints:
                        relevant_fact_ids.update(_fact_id_list(edge.get("fact_ids")))

            for collection in ("required_states", "produced_states"):
                states = step.get(collection)
                if not isinstance(states, list):
                    continue
                for state_index, state in enumerate(states, start=1):
                    if not isinstance(state, dict):
                        continue
                    field_path = (
                        f"{step_path}.{collection}[{state_index - 1}].fact_ids"
                    )
                    raw_fact_ids = state.get("fact_ids")
                    state_fact_ids = _fact_id_list(raw_fact_ids)
                    if (
                        not isinstance(raw_fact_ids, list)
                        or not state_fact_ids
                        or len(state_fact_ids) != len(raw_fact_ids)
                    ):
                        rejections.append(
                            {
                                "workflow_index": workflow_index,
                                "step_index": step_index,
                                "collection": collection,
                                "state_index": state_index,
                                "reason": "graph_typed_state_fact_ids_invalid",
                                "field_path": field_path,
                            }
                        )
                        continue
                    unknown_fact_ids = sorted(set(state_fact_ids) - facts)
                    if unknown_fact_ids:
                        rejections.append(
                            {
                                "workflow_index": workflow_index,
                                "step_index": step_index,
                                "collection": collection,
                                "state_index": state_index,
                                "reason": "graph_typed_state_fact_ids_unknown",
                                "field_path": field_path,
                                "unknown_fact_ids": unknown_fact_ids,
                            }
                        )
                        continue
                    if not mapped_node_id:
                        # 节点映射错误由主图一致性校验报告，避免同一根因产生级联噪声。
                        continue
                    unrelated_fact_ids = sorted(
                        set(state_fact_ids) - relevant_fact_ids
                    )
                    if unrelated_fact_ids:
                        rejections.append(
                            {
                                "workflow_index": workflow_index,
                                "step_index": step_index,
                                "collection": collection,
                                "state_index": state_index,
                                "reason": "graph_typed_state_fact_binding_invalid",
                                "field_path": field_path,
                                "graph_node_id": mapped_node_id,
                                "unrelated_fact_ids": unrelated_fact_ids,
                                "expected_fact_ids": sorted(relevant_fact_ids),
                            }
                        )
    return rejections


def normalize_module_candidates(
    values: Any,
    *,
    source_text: str = "",
    module_catalog: list[dict[str, Any]] | None = None,
    limit: int = 8,
    rejected_semantic_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    modules_by_key: dict[str, dict[str, Any]] = {}
    modules_by_name: dict[str, dict[str, Any]] = {}
    for item in module_catalog or []:
        if not isinstance(item, dict):
            continue
        module_key = _key(item.get("module_key"))
        module_name = _key(item.get("module_name"))
        if module_key:
            modules_by_key[module_key] = item
        if module_name:
            modules_by_name[module_name] = item

    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(values, list):
        if values not in (None, "") and rejected_semantic_items is not None:
            rejected_semantic_items.append(
                {"item_type": "module_candidate", "reason": "collection_not_list"}
            )
        return []
    for index, raw in enumerate(values, start=1):
        if not isinstance(raw, dict):
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": "module_candidate",
                        "item_index": index,
                        "reason": "candidate_not_object",
                    }
                )
            continue
        item = dict(raw)
        module_key = _text(item.get("module_key"))
        module_name = _text(item.get("module_name"))
        referenced = modules_by_key.get(_key(module_key)) or modules_by_name.get(_key(module_name))
        if module_catalog is not None and not referenced:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": "module_candidate",
                        "item_index": index,
                        "identifier": module_key or module_name,
                        "reason": "module_reference_not_active",
                    }
                )
            continue
        if referenced:
            module_key = _text(referenced.get("module_key"))
            module_name = _text(referenced.get("module_name"))
        if not module_key and not module_name:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": "module_candidate",
                        "item_index": index,
                        "reason": "module_identity_missing",
                    }
                )
            continue
        module_key = module_key or _slug(module_name, fallback=f"module_{index:03d}")
        raw_role = _enum_token(item.get("role"))
        role = raw_role if raw_role in MODULE_ROLES else ""
        confidence = _confidence(item.get("confidence"))
        evidence = _text_list(item.get("evidence"))
        evidence_verified = evidence_supported(evidence, source_text)
        if not role or confidence <= 0 or not evidence_verified:
            if rejected_semantic_items is not None:
                reason = (
                    "role_missing_or_invalid"
                    if not role
                    else "confidence_not_positive"
                    if confidence <= 0
                    else "evidence_unverified"
                )
                rejected_semantic_items.append(
                    {
                        "item_type": "module_candidate",
                        "item_index": index,
                        "identifier": module_key,
                        "reason": reason,
                        "evidence": evidence,
                    }
                )
            continue
        marker = (_key(module_key), role)
        if marker in seen:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": "module_candidate",
                        "item_index": index,
                        "identifier": module_key,
                        "reason": "duplicate",
                    }
                )
            continue
        seen.add(marker)
        output.append(
            {
                "module_key": module_key[:80],
                "module_name": module_name[:120],
                "role": role,
                "confidence": confidence,
                "evidence": evidence,
                "evidence_verified": True,
            }
        )
        if len(output) >= max(1, int(limit)):
            break
    return output


def _normalize_module(value: Any, *, index: int, source_text: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    module_name = _text(value.get("module_name"))
    module_key = _slug(value.get("module_key"), fallback="")
    if not module_name or not module_key:
        return {}
    evidence = _text_list(value.get("evidence"))
    return {
        "module_key": module_key,
        "module_name": module_name[:120],
        "aliases": _text_list(value.get("aliases"), limit=12),
        "features": _text_list(value.get("features"), limit=16),
        "scope_status": _enum(value.get("scope_status"), SCOPE_STATUSES),
        "evidence": evidence,
        "evidence_verified": evidence_supported(evidence, source_text),
        "confidence": _confidence(value.get("confidence")),
    }


def normalize_functional_architecture(value: Any, *, source_text: str = "") -> dict[str, Any]:
    architecture = dict(value or {}) if isinstance(value, dict) else {}
    raw_modules = architecture.get("functional_modules") or []
    modules: list[dict[str, Any]] = []
    excluded_modules: list[dict[str, Any]] = []
    rejected_semantic_items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_modules if isinstance(raw_modules, list) else [], start=1):
        if index > _MAX_MODULES:
            break
        required_fields = (
            "module_key",
            "module_name",
            "scope_status",
            "confidence",
            "evidence",
        )
        missing_fields = [
            field
            for field in required_fields
            if not isinstance(raw, dict)
            or (field == "evidence" and not isinstance(raw.get(field), list))
            or (field != "evidence" and not _text(raw.get(field)))
        ]
        if missing_fields:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "reason": "module_schema_invalid",
                    "missing_or_invalid_fields": missing_fields,
                }
            )
            continue
        module = _normalize_module(raw, index=index, source_text=source_text)
        if not module:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "reason": "module_schema_invalid",
                }
            )
            continue
        if module.get("evidence_verified") is not True:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "identifier": str(module.get("module_key") or module.get("module_name") or ""),
                    "reason": "evidence_unverified",
                    "evidence": list(module.get("evidence") or []),
                }
            )
            continue
        if float(module.get("confidence") or 0.0) <= 0:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "identifier": str(module.get("module_key") or ""),
                    "reason": "confidence_not_positive",
                }
            )
            continue
        if module.get("scope_status") == "out_of_scope":
            excluded_modules.append(module)
        elif module.get("scope_status") == "in_scope":
            modules.append(module)
        else:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "identifier": str(module.get("module_key") or ""),
                    "reason": "scope_not_in_scope",
                }
            )
        if len(modules) + len(excluded_modules) >= _MAX_MODULES:
            break

    for index, raw in enumerate(
        architecture.get("excluded_modules") if isinstance(architecture.get("excluded_modules"), list) else [],
        start=len(raw_modules if isinstance(raw_modules, list) else []) + 1,
    ):
        if index > _MAX_MODULES:
            break
        module = _normalize_module(raw, index=index, source_text=source_text)
        if not module:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "reason": "module_invalid",
                }
            )
            continue
        if module.get("scope_status") != "out_of_scope":
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "identifier": str(module.get("module_key") or ""),
                    "reason": "excluded_module_scope_not_declared",
                }
            )
            continue
        if module.get("evidence_verified") is not True:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "identifier": str(module.get("module_key") or module.get("module_name") or ""),
                    "reason": "evidence_unverified",
                    "evidence": list(module.get("evidence") or []),
                }
            )
            continue
        if float(module.get("confidence") or 0.0) <= 0:
            rejected_semantic_items.append(
                {
                    "item_type": "functional_module",
                    "item_index": index,
                    "identifier": str(module.get("module_key") or ""),
                    "reason": "confidence_not_positive",
                }
            )
            continue
        excluded_modules.append(module)

    modules_by_key = {_key(item.get("module_key")): item for item in modules}
    raw_interactions = architecture.get("module_interactions") or []
    interactions: list[dict[str, Any]] = []
    seen_interactions: set[str] = set()
    for index, raw in enumerate(raw_interactions if isinstance(raw_interactions, list) else [], start=1):
        if index > _MAX_INTERACTIONS:
            break
        if not isinstance(raw, dict):
            rejected_semantic_items.append(
                {
                    "item_type": "module_interaction",
                    "item_index": index,
                    "reason": "interaction_not_object",
                }
            )
            continue
        interaction_id = _slug(raw.get("interaction_id"), fallback="")
        source_key = _text(raw.get("source_module_key"))
        target_key = _text(raw.get("target_module_key"))
        trigger = _text(raw.get("trigger"))
        raw_evidence = raw.get("evidence")
        missing_fields = [
            field
            for field, valid in (
                ("interaction_id", bool(interaction_id)),
                ("source_module_key", bool(source_key)),
                ("target_module_key", bool(target_key)),
                ("trigger", bool(trigger)),
                ("evidence", isinstance(raw_evidence, list)),
            )
            if not valid
        ]
        if missing_fields:
            rejected_semantic_items.append(
                {
                    "item_type": "module_interaction",
                    "item_index": index,
                    "identifier": interaction_id,
                    "reason": "interaction_schema_invalid",
                    "missing_or_invalid_fields": missing_fields,
                }
            )
            continue
        source_module = modules_by_key.get(_key(source_key))
        target_module = modules_by_key.get(_key(target_key))
        if not source_module or not target_module:
            rejected_semantic_items.append(
                {
                    "item_type": "module_interaction",
                    "item_index": index,
                    "identifier": _text(raw.get("interaction_id") or raw.get("id")),
                    "reason": "module_reference_not_active",
                }
            )
            continue
        if _key(source_module.get("module_key")) == _key(target_module.get("module_key")):
            rejected_semantic_items.append(
                {
                    "item_type": "module_interaction",
                    "item_index": index,
                    "identifier": _text(raw.get("interaction_id") or raw.get("id")),
                    "reason": "source_target_same_module",
                }
            )
            continue
        if _key(interaction_id) in seen_interactions:
            rejected_semantic_items.append(
                {
                    "item_type": "module_interaction",
                    "item_index": index,
                    "identifier": interaction_id,
                    "reason": "interaction_duplicate",
                }
            )
            continue
        evidence = _text_list(raw_evidence)
        if not evidence_supported(evidence, source_text):
            rejected_semantic_items.append(
                {
                    "item_type": "module_interaction",
                    "item_index": index,
                    "identifier": interaction_id,
                    "reason": "evidence_unverified",
                    "evidence": evidence,
                }
            )
            continue
        confidence = _confidence(raw.get("confidence"))
        if confidence <= 0:
            rejected_semantic_items.append(
                {
                    "item_type": "module_interaction",
                    "item_index": index,
                    "identifier": interaction_id,
                    "reason": "confidence_not_positive",
                }
            )
            continue
        seen_interactions.add(_key(interaction_id))
        interactions.append(
            {
                "interaction_id": interaction_id,
                "source_module_key": _text(source_module.get("module_key")),
                "target_module_key": _text(target_module.get("module_key")),
                "source_module": _text(source_module.get("module_name")),
                "target_module": _text(target_module.get("module_name")),
                "trigger": trigger[:240],
                "transferred_entity": _text(raw.get("transferred_entity"))[:120],
                "result_state": _text(raw.get("result_state"))[:120],
                "evidence": evidence,
                "evidence_verified": True,
                "confidence": confidence,
                "relation_source": "model_semantic_contract",
            }
        )
        if len(interactions) >= _MAX_INTERACTIONS:
            break

    confidence_values = [float(item.get("confidence") or 0.0) for item in [*modules, *interactions]]
    confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
    return {
        "version": "functional-architecture-v3",
        "source": "model_semantic_contract",
        "confidence": round(confidence, 4),
        "functional_modules": modules,
        "excluded_modules": excluded_modules,
        "module_interactions": interactions,
        "shared_capabilities": [],
        "rejected_semantic_items": rejected_semantic_items,
    }


def normalize_workflow_stage_candidates(
    values: Any,
    *,
    source_text: str = "",
    rejected_semantic_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        if values not in (None, "") and rejected_semantic_items is not None:
            rejected_semantic_items.append(
                {"item_type": "workflow_stage_candidate", "reason": "collection_not_list"}
            )
        return []
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(values, start=1):
        if not isinstance(raw, dict):
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": "workflow_stage_candidate",
                        "item_index": index,
                        "reason": "candidate_not_object",
                    }
                )
            continue
        workflow_id = _text(raw.get("workflow_id"))
        stage_id = _text(raw.get("stage_id"))
        stage_kind = _text(raw.get("stage_kind")).lower()
        confidence = _confidence(raw.get("confidence"))
        evidence = _text_list(raw.get("evidence"))
        evidence_verified = evidence_supported(evidence, source_text)
        if not workflow_id or not stage_id or not stage_kind or confidence <= 0 or not evidence_verified:
            if rejected_semantic_items is not None:
                reason = (
                    "workflow_id_missing"
                    if not workflow_id
                    else "stage_id_missing"
                    if not stage_id
                    else "stage_kind_missing"
                    if not stage_kind
                    else "confidence_not_positive"
                    if confidence <= 0
                    else "evidence_unverified"
                )
                rejected_semantic_items.append(
                    {
                        "item_type": "workflow_stage_candidate",
                        "item_index": index,
                        "identifier": f"{workflow_id}:{stage_id}".strip(":"),
                        "reason": reason,
                        "evidence": evidence,
                    }
                )
            continue
        marker = (_key(workflow_id), _key(stage_id))
        if marker in seen:
            if rejected_semantic_items is not None:
                rejected_semantic_items.append(
                    {
                        "item_type": "workflow_stage_candidate",
                        "item_index": index,
                        "identifier": f"{workflow_id}:{stage_id}",
                        "reason": "duplicate",
                    }
                )
            continue
        seen.add(marker)
        output.append(
            {
                "workflow_id": workflow_id[:80],
                "stage_id": stage_id[:80],
                "stage_kind": stage_kind[:80],
                "confidence": confidence,
                "evidence": evidence,
                "evidence_verified": True,
            }
        )
        if len(output) >= 12:
            break
    return output


def _active_requirement_semantic_catalogs(
    requirement_contract: Any,
) -> tuple[list[dict[str, Any]] | None, dict[str, dict[str, Any]] | None, dict[tuple[str, str], dict[str, Any]] | None]:
    if not isinstance(requirement_contract, dict):
        return None, None, None
    architecture = requirement_contract.get("functional_architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    modules = [
        dict(item)
        for item in (architecture.get("functional_modules") or [])
        if isinstance(item, dict)
    ]
    interactions = {
        _key(item.get("interaction_id")): dict(item)
        for item in (architecture.get("module_interactions") or [])
        if isinstance(item, dict) and _key(item.get("interaction_id"))
    }
    stages: dict[tuple[str, str], dict[str, Any]] = {}
    for workflow in requirement_contract.get("workflow_blueprints") or []:
        if not isinstance(workflow, dict):
            continue
        workflow_id = _text(workflow.get("workflow_id") or workflow.get("id"))
        if not workflow_id:
            continue
        for step in workflow.get("steps") or []:
            if not isinstance(step, dict):
                continue
            stage_id = _text(step.get("id"))
            if stage_id:
                stages[(_key(workflow_id), _key(stage_id))] = dict(step)
    return modules, interactions, stages


def validate_case_semantic_contract(
    value: Any,
    *,
    case_text: str = "",
    requirement_contract: Any = None,
) -> dict[str, Any]:
    """校验本轮模型生成用例的结构化语义，禁止再由公开字段反推语义。"""
    rejection_reasons: list[str] = []
    rejected_semantic_items: list[dict[str, Any]] = []
    if not isinstance(value, dict):
        return {
            "valid": False,
            "semantic": {},
            "rejection_reasons": ["semantic_object_missing"],
            "rejected_semantic_items": [
                {"item_type": "case_semantic", "reason": "semantic_object_missing"}
            ],
        }

    for field in _REQUIRED_CASE_SEMANTIC_ARRAY_FIELDS:
        if field not in value:
            rejection_reasons.append(f"{field}:required_collection_missing")
            rejected_semantic_items.append(
                {"item_type": field, "reason": "required_collection_missing"}
            )
        elif not isinstance(value.get(field), list):
            rejection_reasons.append(f"{field}:collection_not_list")
            rejected_semantic_items.append(
                {"item_type": field, "reason": "collection_not_list"}
            )

    raw_schema_contracts = (
        (
            "module_candidates",
            "module_candidate",
            ("module_key", "module_name", "role", "confidence", "evidence"),
        ),
        (
            "workflow_stage_candidates",
            "workflow_stage_candidate",
            ("workflow_id", "stage_id", "stage_kind", "confidence", "evidence"),
        ),
        (
            "precondition_states",
            "precondition_state",
            TYPED_STATE_REQUIRED_FIELDS,
        ),
        (
            "produced_states",
            "produced_state",
            TYPED_STATE_REQUIRED_FIELDS,
        ),
    )
    for collection_field, item_type, required_fields in raw_schema_contracts:
        collection = value.get(collection_field)
        if not isinstance(collection, list):
            continue
        for index, raw_item in enumerate(collection, start=1):
            if not isinstance(raw_item, dict):
                continue
            missing_or_invalid_fields = [
                field
                for field in required_fields
                if (
                    field == "evidence"
                    and not isinstance(raw_item.get(field), list)
                )
                or (
                    field != "evidence"
                    and (field not in raw_item or not _text(raw_item.get(field)))
                )
            ]
            if missing_or_invalid_fields:
                rejected_semantic_items.append(
                    {
                        "item_type": item_type,
                        "item_index": index,
                        "reason": "item_schema_invalid",
                        "missing_or_invalid_fields": missing_or_invalid_fields,
                    }
                )

    modules, interactions_by_id, stages_by_key = _active_requirement_semantic_catalogs(
        requirement_contract
    )
    normalized_modules = normalize_module_candidates(
        value.get("module_candidates"),
        source_text=case_text,
        module_catalog=modules,
        rejected_semantic_items=rejected_semantic_items,
    )
    if not normalized_modules:
        rejection_reasons.append("module_candidates:no_verified_candidate")

    raw_interaction_ids = value.get("interaction_ids")
    normalized_interaction_ids: list[str] = []
    if isinstance(raw_interaction_ids, list):
        seen_interactions: set[str] = set()
        for index, raw_interaction_id in enumerate(raw_interaction_ids, start=1):
            interaction_id = _text(raw_interaction_id)
            marker = _key(interaction_id)
            if not interaction_id:
                rejected_semantic_items.append(
                    {
                        "item_type": "interaction_id",
                        "item_index": index,
                        "reason": "interaction_id_missing",
                    }
                )
                continue
            if interactions_by_id is not None and marker not in interactions_by_id:
                rejected_semantic_items.append(
                    {
                        "item_type": "interaction_id",
                        "item_index": index,
                        "identifier": interaction_id,
                        "reason": "interaction_reference_not_active",
                    }
                )
                continue
            if marker in seen_interactions:
                rejected_semantic_items.append(
                    {
                        "item_type": "interaction_id",
                        "item_index": index,
                        "identifier": interaction_id,
                        "reason": "duplicate",
                    }
                )
                continue
            seen_interactions.add(marker)
            normalized_interaction_ids.append(interaction_id[:80])

    module_roles: dict[str, set[str]] = {}
    for candidate in normalized_modules:
        marker = _key(candidate.get("module_key"))
        if marker:
            module_roles.setdefault(marker, set()).add(str(candidate.get("role") or ""))
    if interactions_by_id is not None and normalized_interaction_ids:
        for interaction_id in normalized_interaction_ids:
            interaction = interactions_by_id.get(_key(interaction_id)) or {}
            source_key = _key(interaction.get("source_module_key"))
            target_key = _key(interaction.get("target_module_key"))
            if source_key not in module_roles or target_key not in module_roles:
                rejection_reasons.append(
                    f"interaction_id:{interaction_id}:source_target_module_candidates_missing"
                )
                continue
            if "source" not in module_roles.get(source_key, set()) or "target" not in module_roles.get(
                target_key, set()
            ):
                rejection_reasons.append(
                    f"interaction_id:{interaction_id}:source_target_roles_missing"
                )
    declared_cross_module_roles = bool(
        "source" in {role for roles in module_roles.values() for role in roles}
        and "target" in {role for roles in module_roles.values() for role in roles}
    )
    if declared_cross_module_roles and not normalized_interaction_ids:
        rejection_reasons.append("interaction_ids:required_for_multiple_modules")

    normalized_workflow_stages = normalize_workflow_stage_candidates(
        value.get("workflow_stage_candidates"),
        source_text=case_text,
        rejected_semantic_items=rejected_semantic_items,
    )
    if stages_by_key is not None:
        active_workflow_stages: list[dict[str, Any]] = []
        for candidate in normalized_workflow_stages:
            marker = (_key(candidate.get("workflow_id")), _key(candidate.get("stage_id")))
            declared = stages_by_key.get(marker)
            if not declared:
                rejected_semantic_items.append(
                    {
                        "item_type": "workflow_stage_candidate",
                        "identifier": f"{candidate.get('workflow_id')}:{candidate.get('stage_id')}",
                        "reason": "workflow_stage_reference_not_active",
                    }
                )
                continue
            if _enum_token(candidate.get("stage_kind")) != _enum_token(declared.get("stage_kind")):
                rejected_semantic_items.append(
                    {
                        "item_type": "workflow_stage_candidate",
                        "identifier": f"{candidate.get('workflow_id')}:{candidate.get('stage_id')}",
                        "reason": "workflow_stage_kind_mismatch",
                    }
                )
                continue
            declared_module_keys = {
                _key(item.get("module_key"))
                for item in (declared.get("module_candidates") or [])
                if isinstance(item, dict) and _key(item.get("module_key"))
            }
            if declared_module_keys and not declared_module_keys.issubset(set(module_roles)):
                rejected_semantic_items.append(
                    {
                        "item_type": "workflow_stage_candidate",
                        "identifier": f"{candidate.get('workflow_id')}:{candidate.get('stage_id')}",
                        "reason": "workflow_stage_module_candidates_mismatch",
                    }
                )
                continue
            declared_interactions = {
                _key(item)
                for item in (declared.get("interaction_ids") or [])
                if _key(item)
            }
            actual_interactions = {_key(item) for item in normalized_interaction_ids if _key(item)}
            if declared_interactions and not declared_interactions.issubset(actual_interactions):
                rejected_semantic_items.append(
                    {
                        "item_type": "workflow_stage_candidate",
                        "identifier": f"{candidate.get('workflow_id')}:{candidate.get('stage_id')}",
                        "reason": "workflow_stage_interactions_missing",
                    }
                )
                continue
            active_workflow_stages.append(candidate)
        normalized_workflow_stages = active_workflow_stages

    normalized_preconditions = normalize_typed_states(
        value.get("precondition_states"),
        source_text=case_text,
        rejected_semantic_items=rejected_semantic_items,
        item_type="precondition_state",
        state_role="precondition",
    )
    normalized_produced = normalize_typed_states(
        value.get("produced_states"),
        source_text=case_text,
        rejected_semantic_items=rejected_semantic_items,
        item_type="produced_state",
        state_role="produced",
    )

    invalid_semantic_item_types = {
        "module_candidate",
        "interaction_id",
        "workflow_stage_candidate",
        "precondition_state",
        "produced_state",
    }
    for item in rejected_semantic_items:
        item_type = str(item.get("item_type") or "")
        reason = str(item.get("reason") or "semantic_item_invalid")
        if item_type in invalid_semantic_item_types:
            rejection_reasons.append(f"{item_type}:{reason}")

    semantic = {
        "version": CASE_SEMANTIC_CONTRACT_VERSION,
        "module_candidates": normalized_modules,
        "interaction_ids": normalized_interaction_ids,
        "workflow_stage_candidates": normalized_workflow_stages,
        "precondition_states": normalized_preconditions,
        "produced_states": normalized_produced,
        "rejected_semantic_items": rejected_semantic_items[:64],
    }
    rejection_reasons = list(dict.fromkeys(rejection_reasons))
    return {
        "valid": not rejection_reasons,
        "semantic": semantic,
        "rejection_reasons": rejection_reasons,
        "rejected_semantic_items": rejected_semantic_items[:64],
    }


def resolve_case_semantic_gate(control_state: Any) -> tuple[bool, dict[str, Any] | None]:
    """仅对完成当前需求语义编译后的新模型产出启用严格门禁。"""
    if hasattr(control_state, "to_dict"):
        control_state = control_state.to_dict()
    if not isinstance(control_state, dict):
        return False, None
    source_meta = control_state.get("source_meta")
    source_meta = source_meta if isinstance(source_meta, dict) else {}
    contract = source_meta.get("requirement_semantic_contract")
    if source_meta.get("semantic_compile_success") is not True or not isinstance(contract, dict):
        return False, None
    return True, contract


def normalize_case_semantic(value: Any, *, case_text: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    rejected_semantic_items = [
        dict(item)
        for item in (value.get("rejected_semantic_items") or [])
        if isinstance(item, dict)
    ][:32]
    if not isinstance(value.get("module_candidates"), list) or not value.get("module_candidates"):
        rejected_semantic_items.append(
            {"item_type": "module_candidate", "reason": "required_collection_missing"}
        )
    interaction_ids = _text_list(value.get("interaction_ids"), limit=16)
    normalized = {
        "version": CASE_SEMANTIC_CONTRACT_VERSION,
        "module_candidates": normalize_module_candidates(
            value.get("module_candidates"),
            source_text=case_text,
            rejected_semantic_items=rejected_semantic_items,
        ),
        "interaction_ids": interaction_ids,
        "workflow_stage_candidates": normalize_workflow_stage_candidates(
            value.get("workflow_stage_candidates"),
            source_text=case_text,
            rejected_semantic_items=rejected_semantic_items,
        ),
        "precondition_states": normalize_typed_states(
            value.get("precondition_states"),
            source_text=case_text,
            rejected_semantic_items=rejected_semantic_items,
            item_type="precondition_state",
            state_role="precondition",
        ),
        "produced_states": normalize_typed_states(
            value.get("produced_states"),
            source_text=case_text,
            rejected_semantic_items=rejected_semantic_items,
            item_type="produced_state",
            state_role="produced",
        ),
        "rejected_semantic_items": rejected_semantic_items[:64],
    }
    if not any(value for key, value in normalized.items() if key != "version"):
        return {}
    return normalized


def empty_requirement_semantic_contract(*, status: str) -> dict[str, Any]:
    return {
        "semantic_contract_version": REQUIREMENT_SEMANTIC_CONTRACT_VERSION,
        "status": _text(status) or "no_semantic_candidate",
        "source": "current_requirement_blueprint",
        "semantic_compile_success": False,
        "evidence_facts": [],
        "semantic_graph": empty_requirement_semantic_graph(),
        "semantic_graph_validation": {
            "valid": False,
            "publishable": False,
            "errors": [],
            "diagnostics": {},
            "topology_fingerprint": "",
        },
        "functional_architecture": normalize_functional_architecture({}),
        "workflow_blueprints": [],
    }


def _preserve_rejected_graph_validation(
    graph_result: dict[str, Any],
    previous_validation: Any,
) -> dict[str, Any]:
    """已拒绝的图只能继续保持拒绝，不能因规范化后的字段再次通过校验。"""

    if not isinstance(previous_validation, dict):
        return graph_result
    if previous_validation.get("publishable") is not False:
        return graph_result
    errors = [
        dict(item)
        for item in (previous_validation.get("errors") or graph_result.get("errors") or [])
        if isinstance(item, dict)
    ]
    if not errors:
        errors = [
            {
                "code": "previous_semantic_graph_rejected",
                "path": "$.semantic_graph_validation",
            }
        ]
    diagnostics = dict(graph_result.get("diagnostics") or {})
    diagnostics["error_count"] = len(errors)
    diagnostics["error_codes"] = sorted(
        {
            str(item.get("code") or item.get("reason") or "semantic_graph_rejected")
            for item in errors
        }
    )
    return {
        **graph_result,
        "valid": False,
        "publishable": False,
        "errors": errors[:128],
        "diagnostics": diagnostics,
    }


def normalize_requirement_semantic_contract(
    payload: Any,
    *,
    requirement_text: str,
    workflow_blueprints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data = dict(payload or {}) if isinstance(payload, dict) else {}
    graph_declared = "evidence_facts" in data or "semantic_graph" in data
    graph_result: dict[str, Any] = {}
    if graph_declared:
        graph_result = normalize_requirement_semantic_graph(
            data,
            source_text=requirement_text,
            evidence_validator=evidence_supported,
        )
        graph_result = _preserve_rejected_graph_validation(
            graph_result,
            data.get("semantic_graph_validation"),
        )
        if graph_result.get("publishable") is True:
            architecture = project_functional_architecture_from_graph(graph_result)
            raw_workflows = (
                workflow_blueprints
                if workflow_blueprints is not None
                else data.get("workflow_blueprints")
            )
            workflows = adapt_workflows_from_semantic_graph(
                raw_workflows or [],
                graph_result,
            )
        else:
            # 图未通过时禁止向旧消费者投影，避免无效拓扑继续污染生成控制状态。
            architecture = normalize_functional_architecture({})
            workflows = []
    else:
        architecture = normalize_functional_architecture(
            data.get("functional_architecture") or {},
            source_text=requirement_text,
        )
        workflows = [
            dict(item)
            for item in (workflow_blueprints or [])
            if isinstance(item, dict)
        ]
    has_content = bool(
        architecture.get("functional_modules")
        or architecture.get("excluded_modules")
        or architecture.get("module_interactions")
        or workflows
    )
    input_status = _text(data.get("status"))
    if graph_declared and graph_result.get("publishable") is not True:
        normalized_status = "semantic_graph_invalid"
    elif input_status:
        normalized_status = input_status
    elif workflows:
        normalized_status = "applied_with_workflows"
    elif has_content:
        normalized_status = "semantic_architecture_only"
    else:
        normalized_status = "no_semantic_candidate"
    contract = {
        "semantic_contract_version": REQUIREMENT_SEMANTIC_CONTRACT_VERSION,
        "status": normalized_status,
        "source": "current_requirement_blueprint",
        "confidence": _confidence(data.get("confidence"), default=architecture.get("confidence") or 0.0),
        "functional_architecture": architecture,
        "workflow_blueprints": workflows,
    }
    if graph_declared:
        contract.update(
            {
                "evidence_facts": list(graph_result.get("evidence_facts") or []),
                "semantic_graph": dict(graph_result.get("semantic_graph") or {}),
                "semantic_graph_validation": {
                    "valid": bool(graph_result.get("valid")),
                    "publishable": bool(graph_result.get("publishable")),
                    "errors": [
                        dict(item)
                        for item in (graph_result.get("errors") or [])
                        if isinstance(item, dict)
                    ],
                    "diagnostics": dict(graph_result.get("diagnostics") or {}),
                    "topology_fingerprint": str(
                        graph_result.get("topology_fingerprint") or ""
                    ),
                },
            }
        )
    return contract


__all__ = [
    "CASE_SEMANTIC_CONTRACT_ABORT_CODE",
    "CASE_SEMANTIC_CONTRACT_VERSION",
    "MODULE_ROLES",
    "MODULE_ROLE_VALUES",
    "MAX_WORKFLOW_STEPS",
    "LEGACY_REQUIREMENT_SEMANTIC_CONTRACT_VERSION",
    "REQUIREMENT_SEMANTIC_CONTRACT_VERSION",
    "STATE_POLARITIES",
    "STATE_POLARITY_VALUES",
    "STATE_SCOPES",
    "STATE_SCOPE_VALUES",
    "STATE_SOURCES",
    "STATE_SOURCE_VALUES",
    "STATE_TEMPORALS",
    "STATE_TEMPORAL_VALUES",
    "TYPED_STATE_REQUIRED_FIELDS",
    "empty_requirement_semantic_contract",
    "evidence_supported",
    "graph_typed_state_identity_rejections",
    "normalize_case_semantic",
    "normalize_functional_architecture",
    "normalize_module_candidates",
    "normalize_requirement_semantic_contract",
    "normalize_typed_state",
    "normalize_typed_states",
    "normalize_workflow_stage_candidates",
    "resolve_case_semantic_gate",
    "validate_case_semantic_contract",
]
