from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict, deque
from typing import Any, Callable


SEMANTIC_GRAPH_VERSION = "requirement-semantic-graph-v1"

NODE_KINDS = {
    "scope",
    "capability",
    "trigger",
    "constraint",
    "state",
    "entity",
    "carrier",
}
REQUIRED_CONTROL_EDGE_TYPES = {"triggers", "transitions"}

# 边类型、端点种类和归属角色使用同一份封闭契约。提示词与校验器都从这里读取，
# 避免模型看到的关系定义和代码实际接受的关系定义逐步漂移。
_NON_CONSTRAINT_NODE_KINDS = frozenset(NODE_KINDS - {"constraint"})
_CONTROL_NODE_KINDS = frozenset(NODE_KINDS - {"constraint", "entity"})
EDGE_SIGNATURES: dict[str, dict[str, Any]] = {
    "contains": {
        "source_kinds": frozenset({"scope"}),
        "target_kinds": frozenset({"scope"}),
        "ownership_roles": frozenset({"none"}),
        "endpoint_error_code": "contains_endpoint_kind_invalid",
        "meaning": "scope hierarchy only",
    },
    "owns": {
        "source_kinds": frozenset({"scope"}),
        "target_kinds": frozenset({"capability"}),
        "ownership_roles": frozenset({"primary", "shared"}),
        "endpoint_error_code": "ownership_endpoint_invalid",
        "meaning": "responsibility ownership only",
        "extra_contract": "source scope is in_scope",
    },
    "triggers": {
        "source_kinds": _CONTROL_NODE_KINDS,
        "target_kinds": _CONTROL_NODE_KINDS,
        "ownership_roles": frozenset({"none"}),
        "endpoint_error_code": "trigger_endpoint_kind_invalid",
        "meaning": "a source event or behavior starts a target behavior/state",
    },
    "transitions": {
        "source_kinds": _CONTROL_NODE_KINDS,
        "target_kinds": _CONTROL_NODE_KINDS,
        "ownership_roles": frozenset({"none"}),
        "endpoint_error_code": "transition_endpoint_kind_invalid",
        "meaning": "one behavior/state advances to another",
    },
    "depends_on": {
        "source_kinds": frozenset(NODE_KINDS),
        "target_kinds": _NON_CONSTRAINT_NODE_KINDS,
        "ownership_roles": frozenset({"none"}),
        "endpoint_error_code": "dependency_endpoint_kind_invalid",
        "meaning": "a non-restrictive prerequisite relation",
    },
    "constrained_by": {
        "source_kinds": _NON_CONSTRAINT_NODE_KINDS,
        "target_kinds": frozenset({"constraint"}),
        "ownership_roles": frozenset({"none"}),
        "endpoint_error_code": "constraint_endpoint_invalid",
        "meaning": "a semantic subject is restricted by a constraint",
    },
    "interacts_with": {
        "source_kinds": frozenset({"scope", "capability"}),
        "target_kinds": frozenset({"scope", "capability"}),
        "ownership_roles": frozenset({"none"}),
        "endpoint_error_code": "interaction_endpoint_kind_invalid",
        "meaning": "a directional handoff between two different active scopes",
        "extra_contract": (
            "scope endpoints are in_scope; capability endpoints have one active owner; "
            "derived scopes differ; trigger/result_state required"
        ),
    },
}
EDGE_TYPES = frozenset(EDGE_SIGNATURES)
# 可确定交换或改型的边已在规范化阶段处理；剩余签名冲突没有唯一的局部修复解。
STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES = frozenset(
    {
        *(
            signature["endpoint_error_code"]
            for signature in EDGE_SIGNATURES.values()
        ),
        "interaction_source_fact_unbound",
        "interaction_scope_endpoint_unresolved",
        "interaction_same_scope",
        "interaction_target_fact_unbound",
    }
)
REQUIREMENT_LEVELS = {"required", "optional", "unspecified"}
PRIORITIES = {"p0", "p1", "p2", "p3", "unspecified"}
TESTABILITY_VALUES = {"testable", "non_testable", "unknown"}
# A1 事实类型只描述单条原子主张的语义形态，不承载模块或文档类型。
FACT_KINDS = {
    "action",
    "algorithm",
    "constraint",
    "interaction",
    "ui_element",
}
SCOPE_STATUSES = {"in_scope", "out_of_scope", "unknown"}
BOUNDARY_STATUSES = {"resolved", "ambiguous", "unresolved"}
OWNERSHIP_ROLES = {"primary", "shared", "none"}
FACT_DISPOSITIONS = {"non_testable", "out_of_scope", "context_only"}
PRIMARY_FLOW_DECLARATION_ERROR_CODES = frozenset(
    {
        "primary_flow_not_object",
        "primary_flow_node_ids_not_list",
        "primary_flow_edge_ids_not_list",
        "primary_flow_node_id_invalid",
        "primary_flow_edge_id_invalid",
        "primary_flow_node_id_duplicate",
        "primary_flow_edge_id_duplicate",
        "primary_flow_node_unknown",
        "primary_flow_edge_unknown",
        "primary_flow_node_not_required",
        "primary_flow_edge_not_required_control",
        "primary_flow_not_simple_path",
        "primary_flow_missing_for_workflow",
    }
)
UNREPAIRABLE_PRIMARY_FLOW_ERROR_CODES = PRIMARY_FLOW_DECLARATION_ERROR_CODES
_MAX_NODES = 320
_MAX_EDGES = 640
MAX_FACT_EVIDENCE_COUNT = 6
MAX_FACT_STATEMENT_CHARS = 320

EvidenceValidator = Callable[[list[str], str], bool]


def edge_signature_contract_prompt() -> str:
    """从运行时契约生成提示文本，避免提示词另存一套端点规则。"""

    kind_aliases = {
        frozenset(NODE_KINDS): "node",
        _NON_CONSTRAINT_NODE_KINDS: "non_constraint",
        _CONTROL_NODE_KINDS: "control",
    }

    def _kind_label(values: frozenset[str]) -> str:
        return kind_aliases.get(values, "|".join(sorted(values)))

    lines: list[str] = []
    for edge_type in sorted(EDGE_SIGNATURES):
        signature = EDGE_SIGNATURES[edge_type]
        source_kinds = _kind_label(signature["source_kinds"])
        target_kinds = _kind_label(signature["target_kinds"])
        ownership_roles = "|".join(sorted(signature["ownership_roles"]))
        line = (
            f"  - {edge_type}: {source_kinds}->{target_kinds}; "
            f"role={ownership_roles}"
        )
        extra_contract = _text(signature.get("extra_contract"))
        if extra_contract:
            line += f"; {extra_contract}"
        lines.append(line)
    return (
        "  aliases: node=all; non_constraint=node-constraint; "
        "control=non_constraint-entity\n"
        + "\n".join(lines)
    )


def semantic_graph_enum_contract_prompt() -> str:
    """从运行时枚举生成提示词，避免模型契约与校验器分叉。"""

    enum_fields = (
        ("kind", NODE_KINDS),
        ("requirement_level", REQUIREMENT_LEVELS),
        ("priority", PRIORITIES),
        ("testability", TESTABILITY_VALUES),
        ("scope_status", SCOPE_STATUSES),
        ("boundary_status", BOUNDARY_STATUSES),
    )
    values = "; ".join(
        f"{field}={'|'.join(sorted(allowed_values))}"
        for field, allowed_values in enum_fields
    )
    return f"Enums: {values}; confidence=number 0..1."


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).lower()
    return re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", text)


def _identifier(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value))
    return re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", text).strip("_")[:96]


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return round(max(0.0, min(1.0, parsed)), 4)


def _text_list(
    value: Any,
    *,
    limit: int = MAX_FACT_EVIDENCE_COUNT,
) -> list[str]:
    values = value if isinstance(value, list) else []
    output: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _text(item)
        marker = _key(text)
        if not text or not marker or marker in seen:
            continue
        seen.add(marker)
        output.append(text[:MAX_FACT_STATEMENT_CHARS])
        if len(output) >= max(1, int(limit)):
            break
    return output


def _id_list(value: Any, *, limit: int = 64) -> list[str]:
    values = value if isinstance(value, list) else []
    output: list[str] = []
    seen: set[str] = set()
    for item in values:
        identifier = _identifier(item)
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        output.append(identifier)
        if len(output) >= max(1, int(limit)):
            break
    return sorted(output)


def _ordered_id_list(value: Any, *, limit: int = 64) -> list[str]:
    """保留显式路径顺序；主流程节点和边的次序本身就是契约。"""

    values = value if isinstance(value, list) else []
    output: list[str] = []
    for item in values[: max(1, int(limit))]:
        output.append(_identifier(item))
    return output


def _declared_ids(
    values: Any,
    field: str,
    *,
    limit: int | None = None,
) -> set[str]:
    """保留原始声明注册表，用于区分不存在引用和上游校验失败。"""

    if not isinstance(values, list):
        return set()
    candidates = (
        values
        if limit is None
        else values[: max(1, int(limit))]
    )
    return {
        identifier
        for item in candidates
        if isinstance(item, dict)
        for identifier in [_identifier(item.get(field))]
        if identifier
    }


def _append_error(
    errors: list[dict[str, Any]],
    code: str,
    path: str,
    *,
    identifier: Any = "",
    node_ids: Any = None,
    count: int | None = None,
    limit: int | None = None,
    repair: Any = None,
) -> None:
    item: dict[str, Any] = {"code": str(code), "path": str(path)}
    clean_identifier = _identifier(identifier)
    if clean_identifier:
        item["id"] = clean_identifier
    clean_node_ids = _id_list(node_ids, limit=_MAX_NODES)
    if clean_node_ids:
        item["node_ids"] = clean_node_ids
    if count is not None:
        item["count"] = max(0, int(count))
    if limit is not None:
        item["limit"] = max(0, int(limit))
    if isinstance(repair, dict) and repair:
        item["repair"] = copy.deepcopy(repair)
    marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
    if marker not in {
        json.dumps(existing, ensure_ascii=False, sort_keys=True)
        for existing in errors
    }:
        errors.append(item)


def _declared_enum(
    value: Any,
    allowed: set[str],
    *,
    default: str,
    errors: list[dict[str, Any]],
    code: str,
    path: str,
    identifier: Any = "",
    allow_empty: bool = False,
) -> str:
    """枚举只做规范化，不把非法声明静默改写成默认语义。"""

    raw = _text(value)
    token = raw.lower().replace("-", "_").replace(" ", "_")
    if token in allowed:
        return token
    if raw or not allow_empty:
        _append_error(errors, code, path, identifier=identifier)
    return default


def _fact_is_required(fact: dict[str, Any]) -> bool:
    return bool(
        fact.get("requirement_level") == "required"
        or fact.get("priority") == "p0"
    )


_UNRESOLVED_FACT_REFERENCE_PATTERN = re.compile(
    r"(?:"
    r"上述|前述|如上所述|如前所述|"
    r"同上(?:文|述)?(?=\s*(?:$|[，,。.;；:：!?！？]|"
    r"规则|逻辑|方案|流程|内容|方式|版本))|"
    r"同前(?:文|述)?(?=\s*(?:$|[，,。.;；:：!?！？]|"
    r"规则|逻辑|方案|流程|内容|方式|版本))|"
    r"同原(?:有)?(?:规则|逻辑|方案|流程|内容|方式|版本)?"
    r"(?:一致|相同)?\s*[。.!！?？]?$|"
    r"\b(?:above-mentioned|aforementioned|previously\s+described|"
    r"as\s+described\s+above)\b|"
    r"\b(?:same\s+as|as)\s+(?:above|before|previous(?:ly\s+described)?)\b|"
    r"\b(?:same\s+as|as)\s+(?:the\s+)?original"
    r"(?:\s+(?:rule|logic|version|behavior|flow|content|implementation))?"
    r"\s*[.!?]?$"
    r")",
    re.IGNORECASE,
)

_UNRESOLVED_VISUAL_OR_POSITIONAL_REFERENCE_PATTERN = re.compile(
    r"(?:"
    r"如\s*(?:(?:上|下|前|后)\s*)?(?:原型|设计|示意)?\s*图(?:片)?"
    r"(?:\s*(?:中\s*)?所示|(?=\s*(?:$|[，,。.;；:：!?！？])))|"
    r"(?:参见|详见|请见)\s*(?:(?:上|下|前|后)\s*)?"
    r"(?:原型|设计|示意)?\s*图(?:片)?(?:\s*(?:中|所示|[0-9一二三四五六七八九十]+))?"
    r"(?=\s*(?:$|[，,。.;；:：!?！？]))|"
    r"见\s*(?:上|下|前|后)\s*(?:原型|设计|示意)?\s*图(?:片)?"
    r"(?:\s*(?:中|所示))?(?=\s*(?:$|[，,。.;；:：!?！？]))|"
    r"(?:上|下|前|后)\s*(?:图|表)\s*(?:中|所示)|"
    r"如下(?:所示)?\s*(?:[：:]\s*)?$|"
    r"\bas\s+(?:shown\s+(?:in\s+)?)?(?:the\s+)?"
    r"(?:figure|image|diagram|prototype)(?:\s+(?:above|below))?\b|"
    r"\bas\s+follows\s*:?[\s]*$|"
    r"\bshown\s+(?:above|below)\b"
    r")",
    re.IGNORECASE,
)


def _has_unresolved_fact_reference(value: Any) -> bool:
    """仅拒绝可确定丢失指代对象的省略表达，不用业务关键词猜测语义。"""

    normalized = unicodedata.normalize("NFKC", _text(value))
    return bool(
        _UNRESOLVED_FACT_REFERENCE_PATTERN.search(normalized)
        or _UNRESOLVED_VISUAL_OR_POSITIONAL_REFERENCE_PATTERN.search(
            normalized
        )
    )


def _priority_rank(value: Any) -> int:
    return {"p0": 0, "p1": 1, "p2": 2, "p3": 3}.get(str(value), 9)


def _derived_requirement_level(facts: list[dict[str, Any]]) -> str:
    levels = {str(item.get("requirement_level") or "") for item in facts}
    if "required" in levels:
        return "required"
    if "optional" in levels:
        return "optional"
    return "unspecified"


def _derived_priority(facts: list[dict[str, Any]]) -> str:
    priorities = [str(item.get("priority") or "unspecified") for item in facts]
    return min(priorities, key=_priority_rank) if priorities else "unspecified"


def _fact_evidence(
    fact_ids: list[str],
    facts_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for fact_id in fact_ids:
        for evidence in facts_by_id.get(fact_id, {}).get("evidence") or []:
            marker = _key(evidence)
            if marker and marker not in seen:
                seen.add(marker)
                output.append(str(evidence))
    return output[:12]


def _normalize_facts(
    values: Any,
    *,
    source_text: str,
    evidence_validator: EvidenceValidator,
    errors: list[dict[str, Any]],
    allowed_fact_kinds: set[str] | None = None,
    reject_unresolved_references: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        _append_error(errors, "facts_not_list", "$.evidence_facts")
        return []

    facts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(values):
        path = f"$.evidence_facts[{index}]"
        if not isinstance(raw, dict):
            _append_error(errors, "fact_not_object", path)
            continue
        fact_id = _identifier(raw.get("fact_id"))
        if allowed_fact_kinds is None:
            fact_kind = _identifier(raw.get("fact_kind")).lower()
        else:
            fact_kind = _declared_enum(
                raw.get("fact_kind"),
                allowed_fact_kinds,
                default="",
                errors=errors,
                code="fact_kind_invalid",
                path=f"{path}.fact_kind",
                identifier=fact_id,
            )
        statement = _text(raw.get("statement"))
        raw_evidence = raw.get("evidence")
        evidence = _text_list(raw_evidence)
        requirement_level = _declared_enum(
            raw.get("requirement_level"),
            REQUIREMENT_LEVELS,
            default="unspecified",
            errors=errors,
            code="fact_requirement_level_invalid",
            path=f"{path}.requirement_level",
            identifier=fact_id,
        )
        priority = _declared_enum(
            raw.get("priority"),
            PRIORITIES,
            default="unspecified",
            errors=errors,
            code="fact_priority_invalid",
            path=f"{path}.priority",
            identifier=fact_id,
        )
        testability = _declared_enum(
            raw.get("testability"),
            TESTABILITY_VALUES,
            default="unknown",
            errors=errors,
            code="fact_testability_invalid",
            path=f"{path}.testability",
            identifier=fact_id,
        )
        raw_confidence = raw.get("confidence")
        if not fact_id or not fact_kind or not statement:
            _append_error(errors, "fact_schema_invalid", path, identifier=fact_id)
            continue
        if fact_id in seen_ids:
            _append_error(errors, "fact_id_duplicate", path, identifier=fact_id)
            continue
        if len(statement) > MAX_FACT_STATEMENT_CHARS:
            _append_error(
                errors,
                "fact_statement_exceeds_limit",
                f"{path}.statement",
                identifier=fact_id,
                count=len(statement),
                limit=MAX_FACT_STATEMENT_CHARS,
            )
        if (
            reject_unresolved_references
            and statement
            and _has_unresolved_fact_reference(statement)
        ):
            _append_error(
                errors,
                "fact_statement_unresolved_reference",
                f"{path}.statement",
                identifier=fact_id,
            )
        if (
            not isinstance(raw_confidence, (int, float))
            or isinstance(raw_confidence, bool)
            or not math.isfinite(float(raw_confidence))
        ):
            _append_error(
                errors,
                "fact_confidence_invalid",
                f"{path}.confidence",
                identifier=fact_id,
            )
            continue
        confidence = float(raw_confidence)
        if not 0.0 < confidence <= 1.0:
            _append_error(
                errors,
                "fact_confidence_out_of_range",
                f"{path}.confidence",
                identifier=fact_id,
            )
            continue
        if (
            isinstance(raw_evidence, list)
            and len(raw_evidence) > MAX_FACT_EVIDENCE_COUNT
        ):
            _append_error(
                errors,
                "fact_evidence_count_exceeds_limit",
                f"{path}.evidence",
                identifier=fact_id,
                count=len(raw_evidence),
                limit=MAX_FACT_EVIDENCE_COUNT,
            )
        if not evidence or not evidence_validator(evidence, source_text):
            _append_error(
                errors,
                "fact_evidence_unverified",
                f"{path}.evidence",
                identifier=fact_id,
            )
            continue
        seen_ids.add(fact_id)
        facts.append(
            {
                "fact_id": fact_id,
                "fact_kind": fact_kind,
                "statement": statement[:MAX_FACT_STATEMENT_CHARS],
                "requirement_level": requirement_level,
                "priority": priority,
                "testability": testability,
                "evidence": evidence,
                "evidence_verified": True,
                "confidence": confidence,
            }
        )
        if _fact_is_required(facts[-1]) and testability == "unknown":
            _append_error(
                errors,
                "required_fact_testability_unresolved",
                f"{path}.testability",
                identifier=fact_id,
            )
    # 事实顺序由 A1 来源数据流冻结；fact_id 是内容哈希身份，不是业务排序键。
    # 指纹函数会独立 canonicalize，语义图规范化不能破坏来源邻域。
    return facts


def _normalize_nodes(
    values: Any,
    *,
    facts_by_id: dict[str, dict[str, Any]],
    declared_fact_ids: set[str],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        _append_error(errors, "nodes_not_list", "$.semantic_graph.nodes")
        return []
    nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(values[:_MAX_NODES]):
        path = f"$.semantic_graph.nodes[{index}]"
        if not isinstance(raw, dict):
            _append_error(errors, "node_not_object", path)
            continue
        node_id = _identifier(raw.get("node_id"))
        kind = _declared_enum(
            raw.get("kind"),
            NODE_KINDS,
            default="",
            errors=errors,
            code="node_kind_invalid",
            path=f"{path}.kind",
            identifier=node_id,
        )
        name = _text(raw.get("name"))
        fact_ids = _id_list(raw.get("fact_ids"))
        boundary_status = _declared_enum(
            raw.get("boundary_status"),
            BOUNDARY_STATUSES,
            default="unresolved",
            errors=errors,
            code="node_boundary_status_invalid",
            path=f"{path}.boundary_status",
            identifier=node_id,
        )
        workflow_role = "none"
        confidence = _confidence(raw.get("confidence"))
        if not node_id or not kind or not name or not fact_ids:
            _append_error(errors, "node_schema_invalid", path, identifier=node_id)
            continue
        if node_id in seen_ids:
            _append_error(errors, "node_id_duplicate", path, identifier=node_id)
            continue
        unknown_fact_ids = [
            fact_id
            for fact_id in fact_ids
            if fact_id not in facts_by_id and fact_id not in declared_fact_ids
        ]
        rejected_fact_ids = [
            fact_id
            for fact_id in fact_ids
            if fact_id not in facts_by_id and fact_id in declared_fact_ids
        ]
        if unknown_fact_ids:
            _append_error(
                errors,
                "node_fact_reference_unknown",
                f"{path}.fact_ids",
                identifier=node_id,
                count=len(unknown_fact_ids),
            )
        if rejected_fact_ids:
            _append_error(
                errors,
                "node_fact_dependency_rejected",
                f"{path}.fact_ids",
                identifier=node_id,
                count=len(rejected_fact_ids),
            )
        if unknown_fact_ids or rejected_fact_ids:
            continue
        if confidence <= 0:
            _append_error(errors, "node_confidence_not_positive", path, identifier=node_id)
            continue
        facts = [facts_by_id[fact_id] for fact_id in fact_ids]
        required = any(_fact_is_required(fact) for fact in facts)
        if required and boundary_status != "resolved":
            _append_error(
                errors,
                "required_node_boundary_unresolved",
                f"{path}.boundary_status",
                identifier=node_id,
            )
        raw_scope_status = raw.get("scope_status")
        scope_status = (
            _declared_enum(
                raw_scope_status,
                SCOPE_STATUSES,
                default="unknown",
                errors=errors,
                code="node_scope_status_invalid",
                path=f"{path}.scope_status",
                identifier=node_id,
            )
            if kind == "scope"
            else ""
        )
        if kind != "scope" and _text(raw_scope_status):
            _declared_enum(
                raw_scope_status,
                SCOPE_STATUSES,
                default="",
                errors=errors,
                code="node_scope_status_invalid",
                path=f"{path}.scope_status",
                identifier=node_id,
                allow_empty=True,
            )
        if kind == "scope" and scope_status == "unknown" and required:
            _append_error(
                errors,
                "required_scope_status_unresolved",
                f"{path}.scope_status",
                identifier=node_id,
            )
        seen_ids.add(node_id)
        nodes.append(
            {
                "node_id": node_id,
                "kind": kind,
                "name": name[:160],
                "aliases": sorted(_text_list(raw.get("aliases"), limit=16), key=_key),
                "scope_status": scope_status,
                "boundary_status": boundary_status,
                "workflow_role": workflow_role,
                "fact_ids": fact_ids,
                "requirement_level": _derived_requirement_level(facts),
                "priority": _derived_priority(facts),
                "required": required,
                "evidence": _fact_evidence(fact_ids, facts_by_id),
                "evidence_verified": True,
                "confidence": confidence,
            }
        )
    if len(values) > _MAX_NODES:
        _append_error(
            errors,
            "node_count_exceeds_limit",
            "$.semantic_graph.nodes",
            count=len(values),
        )

    # scope 别名冲突意味着边界仍未消解，代码不依据名称替模型合并。
    alias_owners: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.get("kind") != "scope":
            continue
        for alias in [node.get("name"), *(node.get("aliases") or [])]:
            marker = _key(alias)
            if marker:
                alias_owners[marker].append(str(node.get("node_id")))
    for node_ids in alias_owners.values():
        unique_ids = sorted(set(node_ids))
        if len(unique_ids) > 1:
            _append_error(
                errors,
                "scope_alias_boundary_ambiguous",
                "$.semantic_graph.nodes",
                node_ids=unique_ids,
                count=len(unique_ids),
            )
    return sorted(nodes, key=lambda item: str(item.get("node_id")))


def _normalize_edges(
    values: Any,
    *,
    facts_by_id: dict[str, dict[str, Any]],
    declared_fact_ids: set[str],
    nodes_by_id: dict[str, dict[str, Any]],
    declared_node_ids: set[str],
    errors: list[dict[str, Any]],
    declaration_repairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        _append_error(errors, "edges_not_list", "$.semantic_graph.edges")
        return []
    edges: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(values[:_MAX_EDGES]):
        path = f"$.semantic_graph.edges[{index}]"
        if not isinstance(raw, dict):
            _append_error(errors, "edge_not_object", path)
            continue
        edge_id = _identifier(raw.get("edge_id"))
        edge_type = _declared_enum(
            raw.get("type"),
            EDGE_TYPES,
            default="",
            errors=errors,
            code="edge_type_invalid",
            path=f"{path}.type",
            identifier=edge_id,
        )
        source_node_id = _identifier(raw.get("source_node_id"))
        target_node_id = _identifier(raw.get("target_node_id"))
        fact_ids = _id_list(raw.get("fact_ids"))
        ownership_role = _declared_enum(
            raw.get("ownership_role"),
            OWNERSHIP_ROLES,
            default="none",
            errors=errors,
            code="edge_ownership_role_invalid",
            path=f"{path}.ownership_role",
            identifier=edge_id,
            allow_empty=True,
        )
        confidence = _confidence(raw.get("confidence"))
        if (
            not edge_id
            or not edge_type
            or not source_node_id
            or not target_node_id
            or not fact_ids
        ):
            _append_error(errors, "edge_schema_invalid", path, identifier=edge_id)
            continue
        if edge_id in seen_ids:
            _append_error(errors, "edge_id_duplicate", path, identifier=edge_id)
            continue
        endpoint_invalid = False
        for field, node_id in (
            ("source_node_id", source_node_id),
            ("target_node_id", target_node_id),
        ):
            if node_id in nodes_by_id:
                continue
            endpoint_invalid = True
            _append_error(
                errors,
                (
                    "edge_endpoint_dependency_rejected"
                    if node_id in declared_node_ids
                    else "edge_endpoint_unknown"
                ),
                f"{path}.{field}",
                identifier=edge_id,
            )
        if endpoint_invalid:
            continue
        unknown_fact_ids = [
            fact_id
            for fact_id in fact_ids
            if fact_id not in facts_by_id and fact_id not in declared_fact_ids
        ]
        rejected_fact_ids = [
            fact_id
            for fact_id in fact_ids
            if fact_id not in facts_by_id and fact_id in declared_fact_ids
        ]
        if unknown_fact_ids:
            _append_error(
                errors,
                "edge_fact_reference_unknown",
                f"{path}.fact_ids",
                identifier=edge_id,
                count=len(unknown_fact_ids),
            )
        if rejected_fact_ids:
            _append_error(
                errors,
                "edge_fact_dependency_rejected",
                f"{path}.fact_ids",
                identifier=edge_id,
                count=len(rejected_fact_ids),
            )
        if unknown_fact_ids or rejected_fact_ids:
            continue
        if confidence <= 0:
            _append_error(errors, "edge_confidence_not_positive", path, identifier=edge_id)
            continue
        if source_node_id == target_node_id and edge_type in {
            "contains",
            "owns",
            "interacts_with",
        }:
            _append_error(errors, "edge_self_reference", path, identifier=edge_id)
            continue

        source_node = nodes_by_id[source_node_id]
        target_node = nodes_by_id[target_node_id]
        if (
            edge_type == "owns"
            and source_node.get("kind") == "capability"
            and target_node.get("kind") == "scope"
            and target_node.get("scope_status") == "in_scope"
            and ownership_role in {"primary", "shared"}
        ):
            # owns 的端点种类唯一确定方向，反向声明可无歧义规范化。
            source_node_id, target_node_id = target_node_id, source_node_id
            source_node, target_node = target_node, source_node
            declaration_repairs.append(
                {
                    "code": "reversed_ownership_edge_canonicalized",
                    "path": path,
                    "id": edge_id,
                    "field": "endpoints",
                    "from": [target_node_id, source_node_id],
                    "to": [source_node_id, target_node_id],
                }
            )
        if (
            edge_type == "constrained_by"
            and source_node.get("kind") == "constraint"
            and target_node.get("kind") != "constraint"
        ):
            # constrained_by 的 constraint 只能位于 target，种类足以确定反向声明。
            source_node_id, target_node_id = target_node_id, source_node_id
            source_node, target_node = target_node, source_node
            declaration_repairs.append(
                {
                    "code": "reversed_constraint_edge_canonicalized",
                    "path": path,
                    "id": edge_id,
                    "field": "endpoints",
                    "from": [target_node_id, source_node_id],
                    "to": [source_node_id, target_node_id],
                }
            )
        if (
            edge_type == "contains"
            and source_node.get("kind") == "scope"
            and source_node.get("scope_status") == "in_scope"
            and target_node.get("kind") == "capability"
            and ownership_role in {"primary", "shared"}
        ):
            # 端点种类与显式 ownership_role 已经唯一确定这是归属边；
            # 在声明边界完成无业务猜测的规范化，避免继续派生 owner/orphan 噪声。
            edge_type = "owns"
            declaration_repairs.append(
                {
                    "code": "contains_capability_canonicalized_to_owns",
                    "path": path,
                    "id": edge_id,
                    "field": "type",
                    "from": "contains",
                    "to": "owns",
                }
            )
        if edge_type != "owns" and ownership_role != "none":
            # 非归属关系不消费 ownership_role；统一清空无效附带值。
            declaration_repairs.append(
                {
                    "code": "non_ownership_role_canonicalized",
                    "path": path,
                    "id": edge_id,
                    "field": "ownership_role",
                    "from": ownership_role,
                    "to": "none",
                }
            )
            ownership_role = "none"
        signature = EDGE_SIGNATURES[edge_type]
        endpoint_kinds_valid = bool(
            source_node.get("kind") in signature["source_kinds"]
            and target_node.get("kind") in signature["target_kinds"]
        )
        role_valid = ownership_role in signature["ownership_roles"]
        owner_scope_valid = bool(
            edge_type != "owns" or source_node.get("scope_status") == "in_scope"
        )
        if not endpoint_kinds_valid or not role_valid or not owner_scope_valid:
            _append_error(
                errors,
                str(signature["endpoint_error_code"]),
                path,
                identifier=edge_id,
                node_ids=[source_node_id, target_node_id],
            )
            continue
        trigger = _text(raw.get("trigger"))
        result_state = _text(raw.get("result_state"))
        if edge_type == "interacts_with" and (not trigger or not result_state):
            _append_error(
                errors,
                "interaction_contract_incomplete",
                path,
                identifier=edge_id,
            )
            continue
        raw_transferred_entity_node_ids = raw.get("transferred_entity_node_ids")
        if (
            raw_transferred_entity_node_ids is not None
            and not isinstance(raw_transferred_entity_node_ids, list)
        ):
            _append_error(
                errors,
                "transferred_entity_ids_not_list",
                f"{path}.transferred_entity_node_ids",
                identifier=edge_id,
            )
            continue
        transferred_entity_node_ids = _id_list(
            raw_transferred_entity_node_ids,
            limit=16,
        )
        if isinstance(raw_transferred_entity_node_ids, list) and len(
            transferred_entity_node_ids
        ) != len(raw_transferred_entity_node_ids):
            _append_error(
                errors,
                "transferred_entity_reference_invalid",
                f"{path}.transferred_entity_node_ids",
                identifier=edge_id,
                count=len(raw_transferred_entity_node_ids),
            )
            continue
        unknown_entity_ids = [
            node_id
            for node_id in transferred_entity_node_ids
            if node_id not in nodes_by_id
        ]
        if unknown_entity_ids:
            _append_error(
                errors,
                "transferred_entity_reference_unknown",
                f"{path}.transferred_entity_node_ids",
                identifier=edge_id,
                count=len(unknown_entity_ids),
            )
            continue
        non_entity_ids = [
            node_id
            for node_id in transferred_entity_node_ids
            if nodes_by_id[node_id].get("kind") != "entity"
        ]
        if non_entity_ids:
            _append_error(
                errors,
                "transferred_entity_kind_invalid",
                f"{path}.transferred_entity_node_ids",
                identifier=edge_id,
                count=len(non_entity_ids),
            )
            continue
        facts = [facts_by_id[fact_id] for fact_id in fact_ids]
        required = any(_fact_is_required(fact) for fact in facts)
        if required:
            required_endpoint_invalid = False
            for endpoint_name, endpoint in (
                ("source_node_id", source_node),
                ("target_node_id", target_node),
            ):
                endpoint_path = f"{path}.{endpoint_name}"
                if endpoint.get("required") is not True:
                    _append_error(
                        errors,
                        "required_edge_endpoint_not_required",
                        endpoint_path,
                        identifier=edge_id,
                    )
                    required_endpoint_invalid = True
                if endpoint.get("boundary_status") != "resolved":
                    _append_error(
                        errors,
                        "required_edge_endpoint_boundary_unresolved",
                        endpoint_path,
                        identifier=edge_id,
                    )
                    required_endpoint_invalid = True
                if (
                    endpoint.get("kind") == "scope"
                    and endpoint.get("scope_status") != "in_scope"
                ):
                    _append_error(
                        errors,
                        "required_edge_scope_not_in_scope",
                        endpoint_path,
                        identifier=edge_id,
                    )
                    required_endpoint_invalid = True
            if required_endpoint_invalid:
                continue
        seen_ids.add(edge_id)
        edges.append(
            {
                "edge_id": edge_id,
                "type": edge_type,
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
                "fact_ids": fact_ids,
                "ownership_role": ownership_role if edge_type == "owns" else "none",
                "trigger": trigger[:240],
                "result_state": result_state[:160],
                "transferred_entity_node_ids": transferred_entity_node_ids,
                "requirement_level": _derived_requirement_level(facts),
                "priority": _derived_priority(facts),
                "required": required,
                "evidence": _fact_evidence(fact_ids, facts_by_id),
                "evidence_verified": True,
                "confidence": confidence,
            }
        )
    if len(values) > _MAX_EDGES:
        _append_error(
            errors,
            "edge_count_exceeds_limit",
            "$.semantic_graph.edges",
            count=len(values),
        )
    return sorted(edges, key=lambda item: str(item.get("edge_id")))


def _normalize_dispositions(
    values: Any,
    *,
    facts_by_id: dict[str, dict[str, Any]],
    declared_fact_ids: set[str],
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if values in (None, ""):
        values = []
    if not isinstance(values, list):
        _append_error(
            errors,
            "fact_dispositions_not_list",
            "$.semantic_graph.fact_dispositions",
        )
        return []
    dispositions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(values):
        path = f"$.semantic_graph.fact_dispositions[{index}]"
        if not isinstance(raw, dict):
            _append_error(errors, "fact_disposition_not_object", path)
            continue
        fact_id = _identifier(raw.get("fact_id"))
        disposition = _declared_enum(
            raw.get("disposition"),
            FACT_DISPOSITIONS,
            default="",
            errors=errors,
            code="fact_disposition_invalid",
            path=f"{path}.disposition",
            identifier=fact_id,
        )
        reason = _text(raw.get("reason"))
        schema_invalid = not fact_id or not disposition
        if not reason:
            _append_error(
                errors,
                "fact_disposition_reason_missing",
                f"{path}.reason",
                identifier=fact_id,
            )
        if schema_invalid:
            _append_error(errors, "fact_disposition_schema_invalid", path, identifier=fact_id)
        if schema_invalid or not reason:
            continue
        if fact_id in seen:
            _append_error(errors, "fact_disposition_duplicate", path, identifier=fact_id)
            continue
        fact = facts_by_id.get(fact_id)
        if not fact:
            _append_error(
                errors,
                (
                    "fact_disposition_dependency_rejected"
                    if fact_id in declared_fact_ids
                    else "fact_disposition_reference_unknown"
                ),
                path,
                identifier=fact_id,
            )
            continue
        if _fact_is_required(fact):
            _append_error(errors, "required_fact_disposition_forbidden", path, identifier=fact_id)
            continue
        if disposition == "out_of_scope":
            if (
                fact.get("requirement_level") != "optional"
                or fact.get("testability") not in {"testable", "non_testable"}
            ):
                _append_error(
                    errors,
                    "out_of_scope_disposition_requires_optional_fact",
                    path,
                    identifier=fact_id,
                )
                continue
        elif fact.get("testability") != "non_testable":
            _append_error(
                errors,
                "testable_fact_disposition_forbidden",
                path,
                identifier=fact_id,
            )
            continue
        seen.add(fact_id)
        dispositions.append(
            {
                "fact_id": fact_id,
                "disposition": disposition,
                "reason": reason[:240],
            }
        )
    return sorted(dispositions, key=lambda item: str(item.get("fact_id")))


def _normalize_primary_flow(
    value: Any,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    declared_node_ids: set[str],
    declared_edge_ids: set[str],
    workflow_topology_errors: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], str]:
    """校验显式主流程边投影，不根据名称、节点角色或完整图猜路径。"""

    empty_flow: dict[str, list[str]] = {"node_ids": [], "edge_ids": []}
    initial_error_count = len(workflow_topology_errors)
    if value is None:
        return empty_flow, "not_declared"
    if not isinstance(value, dict):
        _append_error(
            workflow_topology_errors,
            "primary_flow_not_object",
            "$.semantic_graph.primary_flow",
        )
        return empty_flow, "invalid"

    raw_node_ids = value.get("node_ids")
    raw_edge_ids = value.get("edge_ids")
    if not isinstance(raw_node_ids, list):
        _append_error(
            workflow_topology_errors,
            "primary_flow_node_ids_not_list",
            "$.semantic_graph.primary_flow.node_ids",
        )
    if not isinstance(raw_edge_ids, list):
        _append_error(
            workflow_topology_errors,
            "primary_flow_edge_ids_not_list",
            "$.semantic_graph.primary_flow.edge_ids",
        )
    if not isinstance(raw_node_ids, list) or not isinstance(raw_edge_ids, list):
        return empty_flow, "invalid"

    node_ids = _ordered_id_list(raw_node_ids, limit=_MAX_NODES)
    edge_ids = _ordered_id_list(raw_edge_ids, limit=_MAX_EDGES)
    invalid_node_ids = [index for index, node_id in enumerate(node_ids) if not node_id]
    invalid_edge_ids = [index for index, edge_id in enumerate(edge_ids) if not edge_id]
    if invalid_node_ids or len(raw_node_ids) > _MAX_NODES:
        _append_error(
            workflow_topology_errors,
            "primary_flow_node_id_invalid",
            "$.semantic_graph.primary_flow.node_ids",
            count=len(invalid_node_ids) + max(0, len(raw_node_ids) - _MAX_NODES),
        )
    if invalid_edge_ids or len(raw_edge_ids) > _MAX_EDGES:
        _append_error(
            workflow_topology_errors,
            "primary_flow_edge_id_invalid",
            "$.semantic_graph.primary_flow.edge_ids",
            count=len(invalid_edge_ids) + max(0, len(raw_edge_ids) - _MAX_EDGES),
        )
    if len(set(node_ids)) != len(node_ids):
        _append_error(
            workflow_topology_errors,
            "primary_flow_node_id_duplicate",
            "$.semantic_graph.primary_flow.node_ids",
        )
    if len(set(edge_ids)) != len(edge_ids):
        _append_error(
            workflow_topology_errors,
            "primary_flow_edge_id_duplicate",
            "$.semantic_graph.primary_flow.edge_ids",
        )

    if not node_ids and not edge_ids:
        if len(workflow_topology_errors) > initial_error_count:
            return empty_flow, "invalid"
        return empty_flow, "independent_only"

    if len(node_ids) < 2 or len(edge_ids) != len(node_ids) - 1:
        _append_error(
            workflow_topology_errors,
            "primary_flow_not_simple_path",
            "$.semantic_graph.primary_flow",
            node_ids=node_ids,
            count=len(edge_ids),
        )

    nodes_by_id = {
        str(node.get("node_id")): node for node in nodes if node.get("node_id")
    }
    edges_by_id = {
        str(edge.get("edge_id")): edge for edge in edges if edge.get("edge_id")
    }
    for node_id in node_ids:
        node = nodes_by_id.get(node_id)
        if node is None:
            if node_id not in declared_node_ids:
                _append_error(
                    workflow_topology_errors,
                    "primary_flow_node_unknown",
                    "$.semantic_graph.primary_flow.node_ids",
                    identifier=node_id,
                )
        elif node.get("required") is not True:
            _append_error(
                workflow_topology_errors,
                "primary_flow_node_not_required",
                "$.semantic_graph.primary_flow.node_ids",
                identifier=node_id,
            )
    for index, edge_id in enumerate(edge_ids):
        edge = edges_by_id.get(edge_id)
        if edge is None:
            if edge_id not in declared_edge_ids:
                _append_error(
                    workflow_topology_errors,
                    "primary_flow_edge_unknown",
                    "$.semantic_graph.primary_flow.edge_ids",
                    identifier=edge_id,
                )
            continue
        if (
            edge.get("type") not in REQUIRED_CONTROL_EDGE_TYPES
            or edge.get("required") is not True
        ):
            _append_error(
                workflow_topology_errors,
                "primary_flow_edge_not_required_control",
                "$.semantic_graph.primary_flow.edge_ids",
                identifier=edge_id,
            )
        if index >= len(node_ids) - 1:
            continue
        if (
            str(edge.get("source_node_id")) != node_ids[index]
            or str(edge.get("target_node_id")) != node_ids[index + 1]
        ):
            _append_error(
                workflow_topology_errors,
                "primary_flow_not_simple_path",
                "$.semantic_graph.primary_flow",
                identifier=edge_id,
                node_ids=[node_ids[index], node_ids[index + 1]],
            )

    if len(workflow_topology_errors) > initial_error_count:
        return empty_flow, "invalid"
    return {"node_ids": node_ids, "edge_ids": edge_ids}, "selected"


def _derive_primary_flow_roles(
    nodes: list[dict[str, Any]],
    primary_flow: dict[str, list[str]],
) -> None:
    """主流程角色是显式边路径的派生视图，不再接受模型独立声明。"""

    node_ids = list(primary_flow.get("node_ids") or [])
    roles = {
        node_id: (
            "entry"
            if index == 0
            else "terminal" if index == len(node_ids) - 1 else "intermediate"
        )
        for index, node_id in enumerate(node_ids)
    }
    for node in nodes:
        node["workflow_role"] = roles.get(str(node.get("node_id")), "none")


def _validate_scope_dag(
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, int]:
    children: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {
        node_id: 0
        for node_id, node in nodes_by_id.items()
        if node.get("kind") == "scope"
    }
    for edge in edges:
        if edge.get("type") != "contains":
            continue
        source = str(edge.get("source_node_id"))
        target = str(edge.get("target_node_id"))
        if target not in children[source]:
            children[source].add(target)
            indegree[target] = int(indegree.get(target) or 0) + 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    depths = {node_id: 0 for node_id in queue}
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in sorted(children.get(current, set())):
            depths[child] = max(int(depths.get(child) or 0), int(depths[current]) + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(indegree):
        _append_error(errors, "scope_hierarchy_cycle", "$.semantic_graph.edges")
    return depths


def _validate_ownerships(
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    owners: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge.get("type") == "owns":
            owners[str(edge.get("target_node_id"))].append(edge)
    for node_id, node in nodes_by_id.items():
        if node.get("kind") != "capability":
            continue
        capability_owners = owners.get(node_id, [])
        if not capability_owners:
            _append_error(
                errors,
                "capability_owner_missing",
                "$.semantic_graph.edges",
                identifier=node_id,
            )
            continue
        if len(capability_owners) > 1:
            primary_count = sum(
                edge.get("ownership_role") == "primary"
                for edge in capability_owners
            )
            shared_count = sum(
                edge.get("ownership_role") == "shared"
                for edge in capability_owners
            )
            if not (
                shared_count == len(capability_owners)
                or (
                    primary_count == 1
                    and shared_count == len(capability_owners) - 1
                )
            ):
                _append_error(
                    errors,
                    "capability_ownership_ambiguous",
                    "$.semantic_graph.edges",
                    identifier=node_id,
                    count=len(capability_owners),
                )
    return {
        node_id: sorted(items, key=lambda item: str(item.get("edge_id")))
        for node_id, items in owners.items()
    }


def _active_scope_ids_for_node(
    node_id: str,
    *,
    nodes_by_id: dict[str, dict[str, Any]],
    owners: dict[str, list[dict[str, Any]]],
) -> list[str]:
    node = nodes_by_id.get(node_id) or {}
    if node.get("kind") == "scope":
        return [node_id] if node.get("scope_status") == "in_scope" else []
    if node.get("kind") != "capability":
        return []
    return sorted(
        {
            str(edge.get("source_node_id"))
            for edge in owners.get(node_id, [])
            if (nodes_by_id.get(str(edge.get("source_node_id"))) or {}).get(
                "scope_status"
            )
            == "in_scope"
        }
    )


def _validate_interactions(
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    owners: dict[str, list[dict[str, Any]]],
    errors: list[dict[str, Any]],
) -> None:
    for index, edge in enumerate(edges):
        if edge.get("type") != "interacts_with":
            continue
        source_scopes = _active_scope_ids_for_node(
            str(edge.get("source_node_id")),
            nodes_by_id=nodes_by_id,
            owners=owners,
        )
        target_scopes = _active_scope_ids_for_node(
            str(edge.get("target_node_id")),
            nodes_by_id=nodes_by_id,
            owners=owners,
        )
        path = f"$.semantic_graph.edges[{index}]"
        if len(source_scopes) != 1 or len(target_scopes) != 1:
            _append_error(
                errors,
                "interaction_scope_endpoint_unresolved",
                path,
                identifier=edge.get("edge_id"),
            )
            continue
        if source_scopes[0] == target_scopes[0]:
            _append_error(
                errors,
                "interaction_same_scope",
                path,
                identifier=edge.get("edge_id"),
            )
            continue
        # 这里只校验图表示是否闭合，不根据名称猜关系语义。关系事实需要在两端各被引用，
        # 否则无法确定是端点选错、关系事实选错，还是节点漏绑事实，应交给独立重编译。
        edge_fact_ids = {
            str(fact_id) for fact_id in edge.get("fact_ids") or [] if fact_id
        }
        endpoint_fact_unbound = False
        for role, node_id in (
            ("source", str(edge.get("source_node_id"))),
            ("target", str(edge.get("target_node_id"))),
        ):
            endpoint_fact_ids = {
                str(fact_id)
                for fact_id in (nodes_by_id.get(node_id) or {}).get("fact_ids") or []
                if fact_id
            }
            if edge_fact_ids & endpoint_fact_ids:
                continue
            endpoint_fact_unbound = True
            _append_error(
                errors,
                f"interaction_{role}_fact_unbound",
                f"{path}.{role}_node_id",
                identifier=edge.get("edge_id"),
            )
        if endpoint_fact_unbound:
            continue
        edge["source_scope_id"] = source_scopes[0]
        edge["target_scope_id"] = target_scopes[0]


def _validate_orphans(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    incident: set[str] = set()
    for edge in edges:
        incident.add(str(edge.get("source_node_id")))
        incident.add(str(edge.get("target_node_id")))
        incident.update(
            str(node_id)
            for node_id in (edge.get("transferred_entity_node_ids") or [])
            if str(node_id)
        )
    for node in nodes:
        if node.get("node_id") in incident:
            continue
        # scope 可以是多个互不相连的根；非 scope 节点必须参与至少一条语义边。
        if node.get("kind") == "scope":
            continue
        _append_error(
            errors,
            "orphan_node",
            "$.semantic_graph.nodes",
            identifier=node.get("node_id"),
        )


def _validate_fact_coverage(
    facts: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    consumed: set[str] = set()
    for item in [*nodes, *edges]:
        consumed.update(str(fact_id) for fact_id in item.get("fact_ids") or [])
    dispositioned = {
        str(item.get("fact_id")) for item in dispositions if item.get("fact_id")
    }
    overlap = consumed & dispositioned
    for fact_id in sorted(overlap):
        _append_error(
            errors,
            "fact_consumption_disposition_conflict",
            "$.semantic_graph.fact_dispositions",
            identifier=fact_id,
        )
    for fact in facts:
        fact_id = str(fact.get("fact_id"))
        if fact_id in consumed or fact_id in dispositioned:
            continue
        _append_error(
            errors,
            "missing_required_fact" if _fact_is_required(fact) else "uncovered_fact",
            "$.semantic_graph",
            identifier=fact_id,
        )




def _required_control_components(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """分析已经由 primary_flow 选出的 required 控制边。"""

    required_nodes = {
        str(node.get("node_id")): node
        for node in nodes
        if node.get("required") is True and node.get("node_id")
    }
    control_edges = _required_control_edges(nodes, edges)
    incoming: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, set[str]] = defaultdict(set)
    undirected: dict[str, set[str]] = defaultdict(set)
    incident_node_ids: set[str] = set()
    for edge in control_edges:
        source = str(edge.get("source_node_id"))
        target = str(edge.get("target_node_id"))
        outgoing[source].add(target)
        incoming[target].add(source)
        undirected[source].add(target)
        undirected[target].add(source)
        incident_node_ids.update((source, target))

    # 没有 required 控制边但声明了流程角色的节点仍需进入分析，避免孤立终点伪装成闭环。
    component_node_ids = incident_node_ids | {
        node_id
        for node_id, node in required_nodes.items()
        if node.get("workflow_role") != "none"
    }
    weak_components: list[list[str]] = []
    unvisited = set(component_node_ids)
    while unvisited:
        start = min(unvisited)
        queue = deque([start])
        unvisited.remove(start)
        component: set[str] = {start}
        while queue:
            current = queue.popleft()
            for neighbor in sorted(undirected.get(current, set())):
                if neighbor not in unvisited:
                    continue
                unvisited.remove(neighbor)
                component.add(neighbor)
                queue.append(neighbor)
        weak_components.append(sorted(component))

    def strongly_connected_sets(node_ids: list[str]) -> list[list[str]]:
        allowed = set(node_ids)
        index = 0
        indices: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        stack: list[str] = []
        on_stack: set[str] = set()
        output: list[list[str]] = []

        def visit(node_id: str) -> None:
            nonlocal index
            indices[node_id] = index
            lowlinks[node_id] = index
            index += 1
            stack.append(node_id)
            on_stack.add(node_id)
            for target in sorted(outgoing.get(node_id, set()) & allowed):
                if target not in indices:
                    visit(target)
                    lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target])
                elif target in on_stack:
                    lowlinks[node_id] = min(lowlinks[node_id], indices[target])
            if lowlinks[node_id] != indices[node_id]:
                return
            connected: list[str] = []
            while stack:
                current = stack.pop()
                on_stack.remove(current)
                connected.append(current)
                if current == node_id:
                    break
            output.append(sorted(connected))

        for node_id in node_ids:
            if node_id not in indices:
                visit(node_id)
        return sorted(output, key=lambda item: tuple(item))

    components: list[dict[str, Any]] = []
    for node_ids in sorted(weak_components, key=lambda item: tuple(item)):
        node_id_set = set(node_ids)
        component_edges = [
            edge
            for edge in control_edges
            if str(edge.get("source_node_id")) in node_id_set
            and str(edge.get("target_node_id")) in node_id_set
        ]
        source_node_ids = sorted(
            node_id
            for node_id in node_ids
            if not (incoming.get(node_id, set()) & node_id_set)
        )
        sink_node_ids = sorted(
            node_id
            for node_id in node_ids
            if not (outgoing.get(node_id, set()) & node_id_set)
        )
        branch_node_ids = sorted(
            node_id
            for node_id in node_ids
            if len(incoming.get(node_id, set()) & node_id_set) > 1
            or len(outgoing.get(node_id, set()) & node_id_set) > 1
        )
        cycle_node_ids: set[str] = set()
        for connected in strongly_connected_sets(node_ids):
            if len(connected) > 1:
                cycle_node_ids.update(connected)
            elif connected and connected[0] in outgoing.get(connected[0], set()):
                cycle_node_ids.add(connected[0])
        isolated = not component_edges
        linearizable = bool(
            not isolated
            and not cycle_node_ids
            and not branch_node_ids
            and len(source_node_ids) == 1
            and len(sink_node_ids) == 1
        )
        components.append(
            {
                "node_ids": node_ids,
                "source_node_ids": source_node_ids,
                "sink_node_ids": sink_node_ids,
                "branch_node_ids": branch_node_ids,
                "cycle_node_ids": sorted(cycle_node_ids),
                "active_role_node_ids": sorted(
                    node_id
                    for node_id in node_ids
                    if required_nodes[node_id].get("workflow_role") != "none"
                ),
                "isolated": isolated,
                "linearizable": linearizable,
            }
        )
    return components




def _required_control_edges(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """返回投影中端点和关系均为 required 的控制边。"""

    nodes_by_id = {
        str(node.get("node_id")): node
        for node in nodes
        if node.get("required") is True and node.get("node_id")
    }
    output: list[dict[str, Any]] = []
    for edge in edges:
        if (
            edge.get("type") not in REQUIRED_CONTROL_EDGE_TYPES
            or edge.get("required") is not True
        ):
            continue
        source = nodes_by_id.get(str(edge.get("source_node_id"))) or {}
        target = nodes_by_id.get(str(edge.get("target_node_id"))) or {}
        if not source or not target:
            continue
        output.append(edge)
    return output




def _topology_fingerprint(
    facts: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    dispositions: list[dict[str, Any]],
    primary_flow: dict[str, list[str]] | None = None,
) -> str:
    payload = {
        "facts": [
            {
                key: fact.get(key)
                for key in (
                    "fact_id",
                    "fact_kind",
                    "statement",
                    "requirement_level",
                    "priority",
                    "testability",
                )
            }
            # 发布图保留 A1 来源顺序；只有集合身份指纹按稳定 ID 归一化。
            for fact in sorted(
                facts,
                key=lambda item: str(item.get("fact_id") or ""),
            )
        ],
        "nodes": [
            {
                key: node.get(key)
                for key in (
                    "node_id",
                    "kind",
                    "name",
                    "aliases",
                    "scope_status",
                    "boundary_status",
                    "workflow_role",
                    "fact_ids",
                )
            }
            for node in nodes
        ],
        "edges": [
            {
                key: edge.get(key)
                for key in (
                    "edge_id",
                    "type",
                    "source_node_id",
                    "target_node_id",
                    "fact_ids",
                    "ownership_role",
                    "trigger",
                    "result_state",
                    "transferred_entity_node_ids",
                )
            }
            for edge in edges
        ],
        "fact_dispositions": dispositions,
        "primary_flow": copy.deepcopy(primary_flow) if primary_flow is not None else None,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def empty_requirement_semantic_graph() -> dict[str, Any]:
    return {
        "graph_version": SEMANTIC_GRAPH_VERSION,
        "nodes": [],
        "edges": [],
        "fact_dispositions": [],
        "primary_flow": {"node_ids": [], "edge_ids": []},
        "scope_depths": {},
        "derived_critical_entry_ids": [],
    }


def normalize_requirement_semantic_graph(
    payload: Any,
    *,
    source_text: str,
    evidence_validator: EvidenceValidator,
) -> dict[str, Any]:
    """规范化事实与语义图；只验证声明，不根据业务文本猜边界。"""

    data = dict(payload or {}) if isinstance(payload, dict) else {}
    errors: list[dict[str, Any]] = []
    declaration_repairs: list[dict[str, Any]] = []
    # 完整图与主流程分离：这里只校验显式 edge_ids 路径，图中的其他分支原样保留。
    workflow_topology_errors: list[dict[str, Any]] = []
    raw_facts = data.get("evidence_facts")
    declared_fact_ids = _declared_ids(raw_facts, "fact_id")
    facts = _normalize_facts(
        raw_facts,
        source_text=source_text,
        evidence_validator=evidence_validator,
        errors=errors,
    )
    facts_by_id = {str(item.get("fact_id")): item for item in facts}
    raw_graph = data.get("semantic_graph")
    graph_data = dict(raw_graph or {}) if isinstance(raw_graph, dict) else {}
    raw_nodes = graph_data.get("nodes")
    raw_edges = graph_data.get("edges")
    primary_flow_declared = "primary_flow" in graph_data
    declared_node_ids = _declared_ids(raw_nodes, "node_id", limit=_MAX_NODES)
    declared_edge_ids = _declared_ids(raw_edges, "edge_id", limit=_MAX_EDGES)
    if not isinstance(raw_graph, dict):
        _append_error(errors, "semantic_graph_not_object", "$.semantic_graph")
    if _text(graph_data.get("graph_version")) != SEMANTIC_GRAPH_VERSION:
        _append_error(
            errors,
            "graph_version_invalid",
            "$.semantic_graph.graph_version",
        )
    nodes = _normalize_nodes(
        raw_nodes,
        facts_by_id=facts_by_id,
        declared_fact_ids=declared_fact_ids,
        errors=errors,
    )
    nodes_by_id = {str(item.get("node_id")): item for item in nodes}
    edges = _normalize_edges(
        raw_edges,
        facts_by_id=facts_by_id,
        declared_fact_ids=declared_fact_ids,
        nodes_by_id=nodes_by_id,
        declared_node_ids=declared_node_ids,
        errors=errors,
        declaration_repairs=declaration_repairs,
    )
    dispositions = _normalize_dispositions(
        graph_data.get("fact_dispositions"),
        facts_by_id=facts_by_id,
        declared_fact_ids=declared_fact_ids,
        errors=errors,
    )
    primary_flow, primary_flow_status = _normalize_primary_flow(
        graph_data.get("primary_flow") if primary_flow_declared else None,
        nodes=nodes,
        edges=edges,
        declared_node_ids=declared_node_ids,
        declared_edge_ids=declared_edge_ids,
        workflow_topology_errors=workflow_topology_errors,
    )
    _derive_primary_flow_roles(nodes, primary_flow)
    if (
        primary_flow_status in {"not_declared", "independent_only"}
        and isinstance(data.get("workflow_blueprints"), list)
        and bool(data.get("workflow_blueprints"))
    ):
        _append_error(
            workflow_topology_errors,
            "primary_flow_missing_for_workflow",
            "$.semantic_graph.primary_flow",
        )
    if not facts or not nodes:
        _append_error(errors, "empty_semantic_graph", "$.semantic_graph")

    scope_depths = _validate_scope_dag(nodes_by_id, edges, errors)
    owners = _validate_ownerships(nodes_by_id, edges, errors)
    _validate_interactions(nodes_by_id, edges, owners, errors)
    _validate_orphans(nodes, edges, errors)
    _validate_fact_coverage(facts, nodes, edges, dispositions, errors)
    critical_entries = (
        [str(primary_flow["node_ids"][0])]
        if primary_flow_status == "selected" and primary_flow.get("node_ids")
        else []
    )
    primary_edge_ids = set(primary_flow.get("edge_ids") or [])
    required_components = _required_control_components(
        nodes,
        [
            edge
            for edge in edges
            if str(edge.get("edge_id")) in primary_edge_ids
        ],
    )

    graph = {
        "graph_version": SEMANTIC_GRAPH_VERSION,
        "nodes": nodes,
        "edges": edges,
        "fact_dispositions": dispositions,
        "scope_depths": {
            node_id: int(depth)
            for node_id, depth in sorted(scope_depths.items())
        },
        "derived_critical_entry_ids": critical_entries,
    }
    graph["primary_flow"] = primary_flow
    fingerprint = _topology_fingerprint(
        facts,
        nodes,
        edges,
        dispositions,
        primary_flow,
    )
    valid = not errors
    error_codes = sorted({str(item.get("code")) for item in errors})
    workflow_topology_error_codes = sorted(
        {
            str(item.get("code"))
            for item in workflow_topology_errors
            if item.get("code")
        }
    )
    unrepairable_component_error_codes = sorted(
        set(workflow_topology_error_codes)
        & UNREPAIRABLE_PRIMARY_FLOW_ERROR_CODES
    )
    structural_recompile_error_codes = sorted(
        set(error_codes) & STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES
    )
    workflow_topology_status = (
        "not_linearizable"
        if workflow_topology_errors
        else "linearizable" if critical_entries else "independent_only"
    )
    return {
        "evidence_facts": facts,
        "semantic_graph": graph,
        "valid": valid,
        "publishable": valid,
        "errors": errors[:128],
        "diagnostics": {
            "fact_count": len(facts),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "scope_count": sum(node.get("kind") == "scope" for node in nodes),
            "capability_count": sum(
                node.get("kind") == "capability" for node in nodes
            ),
            "interaction_count": sum(
                edge.get("type") == "interacts_with" for edge in edges
            ),
            "max_scope_depth": max(scope_depths.values(), default=0),
            "derived_critical_entry_count": len(critical_entries),
            "required_control_component_count": len(required_components),
            "required_control_non_isolated_component_count": sum(
                component.get("isolated") is not True
                for component in required_components
            ),
            "required_control_linear_component_count": sum(
                component.get("linearizable") is True
                for component in required_components
            ),
            "required_control_cycle_component_count": sum(
                bool(component.get("cycle_node_ids"))
                for component in required_components
            ),
            "required_control_non_linear_component_count": sum(
                component.get("isolated") is not True
                and not component.get("cycle_node_ids")
                and component.get("linearizable") is not True
                for component in required_components
            ),
            "required_flow_isolated_component_count": sum(
                component.get("isolated") is True
                and bool(component.get("active_role_node_ids"))
                for component in required_components
            ),
            "workflow_topology_status": workflow_topology_status,
            "primary_flow_declared": bool(primary_flow_declared),
            "primary_flow_status": primary_flow_status,
            "primary_flow_node_count": len(primary_flow.get("node_ids") or []),
            "primary_flow_edge_count": len(primary_flow.get("edge_ids") or []),
            "full_required_control_edge_count": sum(
                edge.get("type") in REQUIRED_CONTROL_EDGE_TYPES
                and edge.get("required") is True
                for edge in edges
            ),
            "primary_flow_excluded_required_control_edge_count": sum(
                edge.get("type") in REQUIRED_CONTROL_EDGE_TYPES
                and edge.get("required") is True
                and str(edge.get("edge_id")) not in primary_edge_ids
                for edge in edges
            ),
            "workflow_topology_error_count": len(workflow_topology_errors),
            "workflow_topology_error_codes": workflow_topology_error_codes,
            "workflow_topology_repairable_error_codes": (
                []
            ),
            "workflow_topology_errors": workflow_topology_errors[:128],
            "unrepairable_required_component_error_codes": (
                unrepairable_component_error_codes
            ),
            "structural_recompile_error_codes": structural_recompile_error_codes,
            "error_count": len(errors),
            "error_codes": error_codes,
            "declaration_repair_count": len(declaration_repairs),
            "declaration_repairs": declaration_repairs[:64],
        },
        "topology_fingerprint": fingerprint,
    }


def project_functional_architecture_from_graph(
    normalized: Any,
) -> dict[str, Any]:
    """旧消费者的单向兼容投影；模型不能直接提供此结构。"""

    result = dict(normalized or {}) if isinstance(normalized, dict) else {}
    if result.get("publishable") is not True:
        return {
            "version": "functional-architecture-v4",
            "source": "semantic_graph_projection",
            "confidence": 0.0,
            "functional_modules": [],
            "excluded_modules": [],
            "module_interactions": [],
            "shared_capabilities": [],
            "rejected_semantic_items": list(result.get("errors") or []),
        }
    facts = [
        dict(item)
        for item in (result.get("evidence_facts") or [])
        if isinstance(item, dict)
    ]
    graph = dict(result.get("semantic_graph") or {})
    nodes = [
        dict(item) for item in (graph.get("nodes") or []) if isinstance(item, dict)
    ]
    edges = [
        dict(item) for item in (graph.get("edges") or []) if isinstance(item, dict)
    ]
    facts_by_id = {str(item.get("fact_id")): item for item in facts}
    nodes_by_id = {str(item.get("node_id")): item for item in nodes}
    owned_features: dict[str, list[str]] = defaultdict(list)
    owned_fact_ids: dict[str, set[str]] = defaultdict(set)
    child_scope_ids: dict[str, set[str]] = defaultdict(set)
    interaction_scope_ids: set[str] = set()
    for edge in edges:
        if edge.get("type") == "owns":
            scope_id = str(edge.get("source_node_id"))
            capability = nodes_by_id.get(str(edge.get("target_node_id"))) or {}
            name = _text(capability.get("name"))
            if name and name not in owned_features[scope_id]:
                owned_features[scope_id].append(name)
            owned_fact_ids[scope_id].update(
                str(item)
                for item in [
                    *(capability.get("fact_ids") or []),
                    *(edge.get("fact_ids") or []),
                ]
                if str(item).strip()
            )
        elif edge.get("type") == "contains":
            child_scope_ids[str(edge.get("source_node_id"))].add(
                str(edge.get("target_node_id"))
            )
        elif edge.get("type") == "interacts_with":
            interaction_scope_ids.update(
                {
                    str(edge.get("source_scope_id") or ""),
                    str(edge.get("target_scope_id") or ""),
                }
            )
    interaction_scope_ids.discard("")

    modules: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("kind") != "scope":
            continue
        node_id = str(node.get("node_id"))
        if (
            child_scope_ids.get(node_id)
            and not owned_features.get(node_id)
            and node_id not in interaction_scope_ids
        ):
            continue
        item = {
            "module_key": node_id,
            "module_name": str(node.get("name")),
            "aliases": list(node.get("aliases") or []),
            "features": sorted(owned_features.get(node_id, []), key=_key),
            "fact_ids": sorted(
                {
                    str(item)
                    for item in [
                        *(node.get("fact_ids") or []),
                        *owned_fact_ids.get(node_id, set()),
                    ]
                    if str(item).strip()
                }
            ),
            "scope_status": str(node.get("scope_status") or "unknown"),
            "scope_depth": int(
                (graph.get("scope_depths") or {}).get(node_id) or 0
            ),
            "evidence": _fact_evidence(list(node.get("fact_ids") or []), facts_by_id),
            "evidence_verified": True,
            "confidence": float(node.get("confidence") or 0.0),
            "projection_source_node_id": node_id,
        }
        if item["scope_status"] == "in_scope":
            modules.append(item)
        elif item["scope_status"] == "out_of_scope":
            excluded.append(item)
    active_modules = {str(item.get("module_key")): item for item in modules}

    interactions: list[dict[str, Any]] = []
    for edge in edges:
        if edge.get("type") != "interacts_with":
            continue
        source_scope_id = str(edge.get("source_scope_id") or "")
        target_scope_id = str(edge.get("target_scope_id") or "")
        source = active_modules.get(source_scope_id)
        target = active_modules.get(target_scope_id)
        if not source or not target:
            continue
        transferred_names = [
            str((nodes_by_id.get(node_id) or {}).get("name") or "")
            for node_id in (edge.get("transferred_entity_node_ids") or [])
        ]
        interactions.append(
            {
                "interaction_id": str(edge.get("edge_id")),
                "source_module_key": source_scope_id,
                "target_module_key": target_scope_id,
                "source_module": str(source.get("module_name")),
                "target_module": str(target.get("module_name")),
                "trigger": str(edge.get("trigger") or ""),
                "transferred_entity": "、".join(
                    item for item in transferred_names if item
                ),
                "result_state": str(edge.get("result_state") or ""),
                "evidence": _fact_evidence(
                    list(edge.get("fact_ids") or []),
                    facts_by_id,
                ),
                "evidence_verified": True,
                "confidence": float(edge.get("confidence") or 0.0),
                "relation_source": "semantic_graph_projection",
                "projection_source_edge_id": str(edge.get("edge_id")),
            }
        )
    confidence_values = [
        float(item.get("confidence") or 0.0)
        for item in [*modules, *interactions]
    ]
    return {
        "version": "functional-architecture-v4",
        "source": "semantic_graph_projection",
        "confidence": round(
            sum(confidence_values) / len(confidence_values),
            4,
        )
        if confidence_values
        else 0.0,
        "functional_modules": modules,
        "excluded_modules": excluded,
        "module_interactions": interactions,
        "shared_capabilities": [],
        "rejected_semantic_items": [],
    }


def adapt_workflows_from_semantic_graph(
    workflows: Any,
    normalized: Any,
) -> list[dict[str, Any]]:
    """把 graph ID 引用适配到当前 workflow 消费契约，不反向推断图。"""

    if not isinstance(workflows, list):
        return []
    result = dict(normalized or {}) if isinstance(normalized, dict) else {}
    facts = [
        dict(item)
        for item in (result.get("evidence_facts") or [])
        if isinstance(item, dict)
    ]
    graph = dict(result.get("semantic_graph") or {})
    nodes_by_id = {
        str(item.get("node_id")): dict(item)
        for item in (graph.get("nodes") or [])
        if isinstance(item, dict) and item.get("node_id")
    }
    edges_by_id = {
        str(item.get("edge_id")): dict(item)
        for item in (graph.get("edges") or [])
        if isinstance(item, dict) and item.get("edge_id")
    }
    flow_nodes = {
        node_id: node
        for node_id, node in nodes_by_id.items()
        if str(node.get("workflow_role") or "none") != "none"
    }
    facts_by_id = {str(item.get("fact_id")): item for item in facts}

    def attach_evidence(value: Any) -> None:
        if not isinstance(value, dict):
            return
        # v2 的证据只能由 fact_ids 投影，旧字段及派生信任标记不能透传。
        value.pop("evidence", None)
        value.pop("evidence_verified", None)
        fact_ids = _id_list(value.get("fact_ids"))
        if fact_ids:
            value["fact_ids"] = fact_ids
            value["evidence"] = _fact_evidence(fact_ids, facts_by_id)

    output: list[dict[str, Any]] = []
    for raw_workflow in workflows:
        if not isinstance(raw_workflow, dict):
            output.append(copy.deepcopy(raw_workflow))
            continue
        workflow = copy.deepcopy(raw_workflow)
        workflow.pop("module_candidates", None)
        workflow.pop("interaction_ids", None)
        attach_evidence(workflow)
        raw_steps = workflow.get("steps")
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict):
                    continue
                raw_step.pop("module_candidates", None)
                raw_step.pop("interaction_ids", None)
                raw_step.pop("scope_id", None)
                raw_step.pop("graph_node_id", None)
                attach_evidence(raw_step)
                step_fact_ids = set(_id_list(raw_step.get("fact_ids")))
                matching_flow_node_ids = sorted(
                    node_id
                    for node_id, node in flow_nodes.items()
                    if step_fact_ids & set(_id_list(node.get("fact_ids")))
                )
                if len(matching_flow_node_ids) == 1:
                    # 图节点身份由已验证 fact_ids 派生，不能采用模型自报字段。
                    raw_step["graph_node_id"] = matching_flow_node_ids[0]
                scope_candidates = raw_step.get("scope_candidates")
                module_candidates: list[dict[str, Any]] = []
                if isinstance(scope_candidates, list):
                    for raw_candidate in scope_candidates:
                        if not isinstance(raw_candidate, dict):
                            continue
                        attach_evidence(raw_candidate)
                        candidate = copy.deepcopy(raw_candidate)
                        scope_id = _identifier(candidate.get("scope_id"))
                        scope = nodes_by_id.get(scope_id) or {}
                        if (
                            scope.get("kind") != "scope"
                            or scope.get("scope_status") != "in_scope"
                        ):
                            continue
                        candidate_fact_ids = _id_list(candidate.get("fact_ids"))
                        module_candidates.append(
                            {
                                "module_key": scope_id,
                                "module_name": str(scope.get("name") or ""),
                                "role": str(candidate.get("role") or ""),
                                "confidence": _confidence(candidate.get("confidence")),
                                "evidence": _fact_evidence(
                                    candidate_fact_ids,
                                    facts_by_id,
                                ),
                            }
                        )
                raw_step["module_candidates"] = module_candidates
                relation_ids = (
                    _id_list(
                        raw_step.get("relation_ids"),
                        limit=16,
                    )
                    if isinstance(raw_step.get("relation_ids"), list)
                    else []
                )
                raw_step["relation_ids"] = relation_ids
                raw_step["graph_relation_ids"] = list(relation_ids)
                raw_step["interaction_ids"] = [
                    relation_id
                    for relation_id in relation_ids
                    if (edges_by_id.get(relation_id) or {}).get("type")
                    == "interacts_with"
                ]
                for collection in ("required_states", "produced_states"):
                    for state in raw_step.get(collection) or []:
                        attach_evidence(state)
        output.append(workflow)
    return output


__all__ = [
    "BOUNDARY_STATUSES",
    "EDGE_SIGNATURES",
    "EDGE_TYPES",
    "MAX_FACT_EVIDENCE_COUNT",
    "MAX_FACT_STATEMENT_CHARS",
    "NODE_KINDS",
    "PRIMARY_FLOW_DECLARATION_ERROR_CODES",
    "SEMANTIC_GRAPH_VERSION",
    "STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES",
    "UNREPAIRABLE_PRIMARY_FLOW_ERROR_CODES",
    "adapt_workflows_from_semantic_graph",
    "edge_signature_contract_prompt",
    "empty_requirement_semantic_graph",
    "normalize_requirement_semantic_graph",
    "project_functional_architecture_from_graph",
    "semantic_graph_enum_contract_prompt",
]
