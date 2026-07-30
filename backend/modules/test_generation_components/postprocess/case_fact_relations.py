from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal

from .case_access import (
    case_steps,
    case_text_field,
    case_text_list_field,
)


CaseFactRelation = Literal["none", "duplicate", "contains", "contained_by", "overlap"]


@dataclass(frozen=True)
class CaseSemanticIdentity:
    """用例的通用语义身份，只组合已有契约和字段，不绑定具体文档类型。"""

    module_keys: frozenset[str]
    module_names: frozenset[str]
    fact_ids: frozenset[str]
    interaction_ids: frozenset[str]
    workflow_stages: frozenset[tuple[str, str, str]]
    precondition_states: frozenset[tuple[str, str, str, str, str, str]]
    produced_states: frozenset[tuple[str, str, str, str, str, str]]
    path_type: str
    intent_signature: str
    source_evidence: frozenset[str]


@dataclass(frozen=True)
class CaseSemanticComparison:
    """可解释的语义关系结果，供分片、Judge 和 Review 复用。"""

    relation: CaseFactRelation
    confidence: float
    reasons: tuple[str, ...]
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticDeduplicationResult:
    """完整用例集的共享语义去重结果。"""

    cases: list[dict[str, Any]]
    dropped_count: int
    duplicate_count: int
    containment_count: int
    unresolved_duplicate_count: int
    relation_samples: list[dict[str, Any]]
    dropped_case_ids: list[str]
    kept_case_ids: list[str]


_NON_SEMANTIC_TEXT_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_DESCRIPTION_EQUIVALENCE_MIN = 0.40
_OUTCOME_EQUIVALENCE_MIN = 0.58
_INPUT_EQUIVALENCE_MIN = 0.40
_CONTEXT_EQUIVALENCE_MIN = 0.45
_DISTINCT_INPUT_MAX = 0.20
_DISTINCT_PRECONDITION_MAX = 0.25
_DISTINCT_ACTION_MAX = 0.40
_CONTAINMENT_CONFIDENCE_MIN = 0.90


def normalize_case_semantic_text(value: Any) -> str:
    """仅做字符级归一化，不维护产品词典或文档专属同义词。"""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _NON_SEMANTIC_TEXT_RE.sub("", normalized)


def _normalized_id(value: Any) -> str:
    return str(value or "").strip().casefold()


def _contract_id(value: Any) -> str:
    """契约 ID 保留原始大小写，便于诊断和修复提示原样回传。"""

    return str(value or "").strip()


def _semantic(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("_semantic")
    return value if isinstance(value, dict) else {}


def _string_set(values: Any) -> frozenset[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        normalized
        for item in values
        if (normalized := _contract_id(item))
    )


def verified_case_fact_ids(case: dict[str, Any]) -> frozenset[str]:
    """读取语义契约已经验收过的事实身份，不从用例正文反推事实。"""

    return _string_set(_semantic(case).get("fact_ids"))


def _verified_interaction_ids(case: dict[str, Any]) -> frozenset[str]:
    return _string_set(_semantic(case).get("interaction_ids"))


def _module_identity(case: dict[str, Any]) -> tuple[frozenset[str], frozenset[str]]:
    keys: set[str] = set()
    names: set[str] = set()
    for item in _semantic(case).get("module_candidates") or []:
        if not isinstance(item, dict):
            continue
        key = _normalized_id(item.get("module_key"))
        name = normalize_case_semantic_text(item.get("module_name"))
        if key:
            keys.add(key)
        if name:
            names.add(name)
    display_module = normalize_case_semantic_text(case_text_field(case, "test_module"))
    if display_module:
        names.add(display_module)
    return frozenset(keys), frozenset(names)


def _workflow_stages(case: dict[str, Any]) -> frozenset[tuple[str, str, str]]:
    output: set[tuple[str, str, str]] = set()
    for item in _semantic(case).get("workflow_stage_candidates") or []:
        if not isinstance(item, dict):
            continue
        workflow_id = _normalized_id(item.get("workflow_id"))
        stage_id = _normalized_id(item.get("stage_id"))
        stage_kind = _normalized_id(item.get("stage_kind"))
        if workflow_id or stage_id or stage_kind:
            output.add((workflow_id, stage_id, stage_kind))
    return frozenset(output)


def _typed_states(
    case: dict[str, Any],
    field: str,
) -> frozenset[tuple[str, str, str, str, str, str]]:
    output: set[tuple[str, str, str, str, str, str]] = set()
    for item in _semantic(case).get(field) or []:
        if not isinstance(item, dict):
            continue
        state = (
            normalize_case_semantic_text(item.get("entity")),
            normalize_case_semantic_text(item.get("state")),
            _normalized_id(item.get("source")),
            _normalized_id(item.get("scope")),
            _normalized_id(item.get("polarity")),
            _normalized_id(item.get("temporal")),
        )
        if state[0] or state[1]:
            output.add(state)
    return frozenset(output)


def _semantic_evidence(case: dict[str, Any]) -> frozenset[str]:
    anchors: set[str] = set()
    semantic = _semantic(case)
    for field in (
        "module_candidates",
        "workflow_stage_candidates",
        "precondition_states",
        "produced_states",
    ):
        for item in semantic.get(field) or []:
            if not isinstance(item, dict):
                continue
            for evidence in item.get("evidence") or []:
                normalized = normalize_case_semantic_text(evidence)
                if normalized:
                    anchors.add(normalized)
    return frozenset(anchors)


def _intent_signature(case: dict[str, Any]) -> str:
    try:
        # 延迟导入，避免 coverage 包初始化时反向加载本关系模块形成循环依赖。
        from ..coverage.coverage_case_classifier import (
            classify_case_flow_stage,
            classify_case_intent_signature,
        )

        stage = classify_case_flow_stage(case, {})
        return str(classify_case_intent_signature(case, stage) or "").strip()
    except Exception:
        return ""


def build_case_semantic_identity(case: dict[str, Any]) -> CaseSemanticIdentity:
    module_keys, module_names = _module_identity(case)
    return CaseSemanticIdentity(
        module_keys=module_keys,
        module_names=module_names,
        fact_ids=verified_case_fact_ids(case),
        interaction_ids=_verified_interaction_ids(case),
        workflow_stages=_workflow_stages(case),
        precondition_states=_typed_states(case, "precondition_states"),
        produced_states=_typed_states(case, "produced_states"),
        path_type=_normalized_id(case.get("path_type")),
        intent_signature=_intent_signature(case),
        source_evidence=_semantic_evidence(case),
    )


def _character_ngrams(value: Any) -> frozenset[str]:
    text = normalize_case_semantic_text(value)
    if len(text) < 2:
        return frozenset({text}) if text else frozenset()
    return frozenset(
        text[index : index + width]
        for width in (2, 3)
        if len(text) >= width
        for index in range(0, len(text) - width + 1)
    )


def _containment_similarity(left: Any, right: Any) -> float:
    left_tokens = _character_ngrams(left)
    right_tokens = _character_ngrams(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return float(len(left_tokens & right_tokens)) / float(min(len(left_tokens), len(right_tokens)))


def _directional_similarity(left: Any, right: Any) -> float:
    """度量 left 的语义片段有多少被 right 覆盖。"""

    left_tokens = _character_ngrams(left)
    right_tokens = _character_ngrams(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return float(len(left_tokens & right_tokens)) / float(len(left_tokens))


def _semantic_public_fields(case: dict[str, Any]) -> dict[str, str]:
    return {
        "description": case_text_field(case, "description"),
        "preconditions": " ".join(case_text_list_field(case, "preconditions")),
        "input": case_text_field(case, "test_input"),
        "action": " ".join(case_steps(case)),
        "outcome": case_text_field(case, "expected_result"),
    }


def _replacement_pairs(left: Any, right: Any) -> set[tuple[str, str]]:
    """提取近模板文本中的稳定替换片段，不引入任何业务词典。"""

    left_text = normalize_case_semantic_text(left)
    right_text = normalize_case_semantic_text(right)
    if not left_text or not right_text or left_text == right_text:
        return set()
    pairs: set[tuple[str, str]] = set()
    matcher = SequenceMatcher(None, left_text, right_text, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        left_part = left_text[i1:i2]
        right_part = right_text[j1:j2]
        if not left_part or not right_part:
            continue
        if len(left_part) <= 8 and len(right_part) <= 8:
            pairs.add((left_part, right_part))
    return pairs


def _has_distinct_object_scope(left_case: dict[str, Any], right_case: dict[str, Any]) -> bool:
    """
    只有当输入对象的替换同时贯穿其他验证字段时，才认定为不同对象。

    这可以保留对象 A/B、上/下册等参数化用例，又不会因为单个助词差异放过重复。
    """

    fields_left = _semantic_public_fields(left_case)
    fields_right = _semantic_public_fields(right_case)
    input_pairs = _replacement_pairs(fields_left["input"], fields_right["input"])
    if not input_pairs:
        return False
    repeated_fields = 0
    for field in ("description", "preconditions", "action", "outcome"):
        if input_pairs & _replacement_pairs(fields_left[field], fields_right[field]):
            repeated_fields += 1
    return repeated_fields >= 1


def _concrete_assertion_anchors(value: Any) -> frozenset[str]:
    """提取数字、代码和引号内容，用于区分通用结构断言与具体值断言。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    anchors = {
        normalize_case_semantic_text(item)
        for item in re.findall(r"[A-Za-z]+[-_A-Za-z0-9]*|\d+(?:\.\d+)?|[“”「」『』《》\"']([^“”「」『』《》\"']{1,30})[“”「」『』《》\"']", text)
        if normalize_case_semantic_text(item)
    }
    # re.findall 含捕获组时会丢失组外的数字/代码，因此单独补充。
    anchors.update(
        normalize_case_semantic_text(item)
        for item in re.findall(r"[A-Za-z]+[-_A-Za-z0-9]*|\d+(?:\.\d+)?", text)
        if normalize_case_semantic_text(item)
        # 1.、2. 这类列表编号不是业务断言值。
        and not (item.isdigit() and len(item) == 1)
    )
    return frozenset(anchors)


def _has_distinct_assertion_scope(
    left_case: dict[str, Any],
    right_case: dict[str, Any],
    similarities: dict[str, float],
) -> bool:
    left_text = " ".join(
        [case_text_field(left_case, "test_input"), case_text_field(left_case, "expected_result")]
    )
    right_text = " ".join(
        [case_text_field(right_case, "test_input"), case_text_field(right_case, "expected_result")]
    )
    left_anchors = _concrete_assertion_anchors(left_text)
    right_anchors = _concrete_assertion_anchors(right_text)
    left_input_identifiers = {
        anchor
        for anchor in _concrete_assertion_anchors(case_text_field(left_case, "test_input"))
        if any(character.isdigit() for character in anchor)
    }
    right_input_identifiers = {
        anchor
        for anchor in _concrete_assertion_anchors(case_text_field(right_case, "test_input"))
        if any(character.isdigit() for character in anchor)
    }
    distinct_input_identifier = bool(
        left_input_identifiers
        and right_input_identifiers
        and left_input_identifiers.isdisjoint(right_input_identifiers)
    )
    distinct_anchors = bool(left_anchors and right_anchors and left_anchors != right_anchors)
    generic_vs_concrete = bool(bool(left_anchors) != bool(right_anchors))
    repeated_assertion_pairs = (
        _replacement_pairs(
            case_text_field(left_case, "description"),
            case_text_field(right_case, "description"),
        )
        & _replacement_pairs(
            case_text_field(left_case, "expected_result"),
            case_text_field(right_case, "expected_result"),
        )
    )
    distinct_assertion_expression = any(
        max(len(left_part), len(right_part)) >= 2
        for left_part, right_part in repeated_assertion_pairs
    )
    return bool(
        distinct_input_identifier
        or distinct_assertion_expression
        or (
            similarities["input"] < 0.70
            and similarities["outcome"] < 0.90
            and (distinct_anchors or generic_vs_concrete)
        )
    )


def _verified_module_contract_conflicts(
    left: CaseSemanticIdentity,
    right: CaseSemanticIdentity,
) -> bool:
    """只把已验证的模块契约当作硬冲突，展示模块名可能只是交叉功能的归属差异。"""

    return bool(
        left.module_keys
        and right.module_keys
        and left.module_keys.isdisjoint(right.module_keys)
    )


def _state_contract_conflicts(
    left: frozenset[tuple[str, str, str, str, str, str]],
    right: frozenset[tuple[str, str, str, str, str, str]],
) -> bool:
    left_by_entity: dict[str, set[tuple[str, str, str, str, str]]] = {}
    right_by_entity: dict[str, set[tuple[str, str, str, str, str]]] = {}
    for entity, *state in left:
        if entity:
            left_by_entity.setdefault(entity, set()).add(tuple(state))
    for entity, *state in right:
        if entity:
            right_by_entity.setdefault(entity, set()).add(tuple(state))
    return any(
        left_by_entity[entity].isdisjoint(right_by_entity[entity])
        for entity in left_by_entity.keys() & right_by_entity.keys()
    )


def _workflow_conflicts(left: CaseSemanticIdentity, right: CaseSemanticIdentity) -> bool:
    if not left.workflow_stages or not right.workflow_stages:
        return False
    return left.workflow_stages.isdisjoint(right.workflow_stages)


def _field_similarities(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    left_preconditions = " ".join(case_text_list_field(left, "preconditions"))
    right_preconditions = " ".join(case_text_list_field(right, "preconditions"))
    return {
        "description": _containment_similarity(
            case_text_field(left, "description"), case_text_field(right, "description")
        ),
        "preconditions": _containment_similarity(left_preconditions, right_preconditions),
        "input": _containment_similarity(
            case_text_field(left, "test_input"), case_text_field(right, "test_input")
        ),
        "action": _containment_similarity(" ".join(case_steps(left)), " ".join(case_steps(right))),
        "outcome": _containment_similarity(
            case_text_field(left, "expected_result"), case_text_field(right, "expected_result")
        ),
    }


def _trigger_context_conflicts(
    left: dict[str, Any],
    right: dict[str, Any],
    similarities: dict[str, float],
) -> bool:
    has_inputs = bool(case_text_field(left, "test_input") and case_text_field(right, "test_input"))
    has_preconditions = bool(
        case_text_list_field(left, "preconditions")
        and case_text_list_field(right, "preconditions")
    )
    return bool(
        has_inputs
        and has_preconditions
        and similarities["input"] < _DISTINCT_INPUT_MAX
        and similarities["preconditions"] < _DISTINCT_PRECONDITION_MAX
        and similarities["action"] < _DISTINCT_ACTION_MAX
    )


def _behaviorally_equivalent(
    left_case: dict[str, Any],
    right_case: dict[str, Any],
    left: CaseSemanticIdentity,
    right: CaseSemanticIdentity,
    similarities: dict[str, float],
) -> bool:
    if _verified_module_contract_conflicts(left, right):
        return False
    if _has_distinct_object_scope(left_case, right_case):
        return False
    if _has_distinct_assertion_scope(left_case, right_case, similarities):
        return False
    same_intent = bool(
        left.intent_signature
        and left.intent_signature == right.intent_signature
    )
    left_input = case_text_field(left_case, "test_input")
    right_input = case_text_field(right_case, "test_input")
    if left_input and right_input:
        trigger_aligned = similarities["input"] >= _INPUT_EQUIVALENCE_MIN
    else:
        trigger_aligned = bool(
            similarities["preconditions"] >= _CONTEXT_EQUIVALENCE_MIN
            or similarities["action"] >= _CONTEXT_EQUIVALENCE_MIN
        )
    return bool(
        (
            same_intent
            and similarities["description"] >= _DESCRIPTION_EQUIVALENCE_MIN
            and similarities["outcome"] >= _OUTCOME_EQUIVALENCE_MIN
            and trigger_aligned
        )
        or (
            similarities["description"] >= 0.90
            and similarities["action"] >= 0.85
            and similarities["outcome"] >= 0.68
            and max(similarities["input"], similarities["preconditions"]) >= 0.45
        )
        or (
            similarities["description"] >= 0.75
            and similarities["input"] >= 0.90
            and similarities["outcome"] >= 0.45
        )
    )


def _behavioral_containment_relation(
    left_case: dict[str, Any],
    right_case: dict[str, Any],
    left: CaseSemanticIdentity,
    right: CaseSemanticIdentity,
) -> CaseSemanticComparison | None:
    if _verified_module_contract_conflicts(left, right):
        return None
    if _has_distinct_object_scope(left_case, right_case):
        return None
    fields_left = _semantic_public_fields(left_case)
    fields_right = _semantic_public_fields(right_case)

    def covered(source: dict[str, str], target: dict[str, str]) -> bool:
        trigger_coverage = max(
            _directional_similarity(source["input"], target["input"]),
            _directional_similarity(source["preconditions"], target["preconditions"]),
        )
        return bool(
            _directional_similarity(source["description"], target["description"]) >= 0.70
            and _directional_similarity(source["action"], target["action"]) >= 0.50
            and _directional_similarity(source["outcome"], target["outcome"]) >= 0.70
            and trigger_coverage >= 0.55
        )

    left_in_right = covered(fields_left, fields_right)
    right_in_left = covered(fields_right, fields_left)
    left_size = len(normalize_case_semantic_text(" ".join(fields_left.values())))
    right_size = len(normalize_case_semantic_text(" ".join(fields_right.values())))
    if left_in_right and right_size >= max(left_size + 12, int(left_size * 1.12)):
        return CaseSemanticComparison(
            relation="contained_by",
            confidence=0.92,
            reasons=("same_trigger_scope", "assertion_scope_contained_by"),
        )
    if right_in_left and left_size >= max(right_size + 12, int(right_size * 1.12)):
        return CaseSemanticComparison(
            relation="contains",
            confidence=0.92,
            reasons=("same_trigger_scope", "assertion_scope_contains"),
        )
    return None


def compare_case_semantic_identity(
    left_case: dict[str, Any],
    right_case: dict[str, Any],
) -> CaseSemanticComparison:
    """比较两个用例的结构化身份，并在结构信息不足时保守比较行为字段。"""

    left = build_case_semantic_identity(left_case)
    right = build_case_semantic_identity(right_case)
    similarities = _field_similarities(left_case, right_case)
    conflicts: list[str] = []
    if left.path_type and right.path_type and left.path_type != right.path_type:
        conflicts.append("different_path_type")
    if left.interaction_ids and right.interaction_ids and left.interaction_ids != right.interaction_ids:
        conflicts.append("different_interaction_contract")
    if _workflow_conflicts(left, right):
        conflicts.append("different_workflow_stage")
    if _state_contract_conflicts(left.precondition_states, right.precondition_states):
        conflicts.append("different_precondition_state")
    if _state_contract_conflicts(left.produced_states, right.produced_states):
        conflicts.append("different_produced_state")
    if _trigger_context_conflicts(left_case, right_case, similarities):
        conflicts.append("different_trigger_context")
    if conflicts:
        return CaseSemanticComparison(
            relation="none",
            confidence=1.0,
            reasons=("explicit_semantic_conflict",),
            conflicts=tuple(conflicts),
        )

    reasons: list[str] = []
    if left.source_evidence & right.source_evidence:
        reasons.append("shared_source_evidence")
    if left.fact_ids and right.fact_ids:
        if left.fact_ids == right.fact_ids:
            return CaseSemanticComparison(
                relation="duplicate",
                confidence=1.0,
                reasons=tuple(["same_verified_fact_set", *reasons]),
            )
        if right.fact_ids < left.fact_ids:
            return CaseSemanticComparison(
                relation="contains",
                confidence=0.98,
                reasons=tuple(["verified_fact_superset", *reasons]),
            )
        if left.fact_ids < right.fact_ids:
            return CaseSemanticComparison(
                relation="contained_by",
                confidence=0.98,
                reasons=tuple(["verified_fact_subset", *reasons]),
            )
        if left.fact_ids & right.fact_ids:
            return CaseSemanticComparison(
                relation="overlap",
                confidence=0.85,
                reasons=tuple(["verified_fact_overlap", *reasons]),
            )

    containment = _behavioral_containment_relation(left_case, right_case, left, right)
    if containment is not None:
        return containment

    if _behaviorally_equivalent(left_case, right_case, left, right, similarities):
        relation_basis = (
            "same_intent_signature"
            if left.intent_signature and left.intent_signature == right.intent_signature
            else "near_exact_behavior_text"
        )
        confidence = min(
            0.95,
            0.45
            + 0.20 * similarities["description"]
            + 0.20 * similarities["outcome"]
            + 0.15 * max(similarities["input"], similarities["preconditions"], similarities["action"]),
        )
        return CaseSemanticComparison(
            relation="duplicate",
            confidence=round(float(confidence), 4),
            reasons=tuple(
                [
                    relation_basis,
                    "equivalent_trigger_action_outcome",
                    *reasons,
                ]
            ),
        )

    return CaseSemanticComparison(
        relation="none",
        confidence=0.0,
        reasons=("insufficient_equivalence_evidence",),
    )


def _case_retention_score(case: dict[str, Any]) -> tuple[int, int, int, int]:
    """选择重复用例中信息更完整的一条，不依赖业务词。"""

    protected = int(
        str(case.get("execution_group") or "").strip().lower() == "main_smoke"
        or bool(case.get("hit_must_cover_rule"))
    )
    semantic = _semantic(case)
    contract_dimensions = sum(
        len(semantic.get(field) or [])
        for field in (
            "fact_ids",
            "interaction_ids",
            "workflow_stage_candidates",
            "precondition_states",
            "produced_states",
        )
    )
    nonempty_fields = sum(
        bool(value)
        for value in _semantic_public_fields(case).values()
    )
    text_size = len(
        normalize_case_semantic_text(" ".join(_semantic_public_fields(case).values()))
    )
    return protected, int(contract_dimensions), int(nonempty_fields), int(text_size)


def deduplicate_cases_by_semantic_identity(
    cases: list[dict[str, Any]],
    *,
    sample_limit: int = 50,
) -> SemanticDeduplicationResult:
    """对最终完整集合执行全量、可解释的共享语义判重。"""

    kept: list[dict[str, Any]] = []
    dropped_case_ids: list[str] = []
    samples: list[dict[str, Any]] = []
    duplicate_count = 0
    containment_count = 0
    unresolved_duplicate_count = 0

    def case_id(item: dict[str, Any]) -> str:
        return str(item.get("id") or "").strip()

    def record(
        comparison: CaseSemanticComparison,
        left_case: dict[str, Any],
        right_case: dict[str, Any],
        *,
        action: str,
    ) -> None:
        if len(samples) >= max(0, int(sample_limit or 0)):
            return
        samples.append(
            {
                "left_case_id": case_id(left_case),
                "right_case_id": case_id(right_case),
                "relation": comparison.relation,
                "confidence": float(comparison.confidence),
                "reasons": list(comparison.reasons),
                "action": action,
            }
        )

    for candidate in [dict(item) for item in cases if isinstance(item, dict)]:
        resolved = False
        for index, retained in enumerate(kept):
            comparison = compare_case_semantic_identity(retained, candidate)
            if comparison.relation == "duplicate":
                duplicate_count += 1
                if _case_retention_score(candidate) > _case_retention_score(retained):
                    kept[index] = candidate
                    dropped_case_ids.append(case_id(retained))
                    record(comparison, retained, candidate, action="replace_with_richer_duplicate")
                else:
                    dropped_case_ids.append(case_id(candidate))
                    record(comparison, retained, candidate, action="drop_duplicate")
                resolved = True
                break
            if comparison.relation in {"contains", "contained_by"}:
                containment_count += 1
                if comparison.confidence < _CONTAINMENT_CONFIDENCE_MIN:
                    unresolved_duplicate_count += 1
                    record(comparison, retained, candidate, action="report_unresolved_containment")
                    continue
                if comparison.relation == "contained_by":
                    kept[index] = candidate
                    dropped_case_ids.append(case_id(retained))
                    record(comparison, retained, candidate, action="replace_with_containing_case")
                else:
                    dropped_case_ids.append(case_id(candidate))
                    record(comparison, retained, candidate, action="drop_contained_case")
                resolved = True
                break
        if not resolved:
            kept.append(candidate)

    return SemanticDeduplicationResult(
        cases=kept,
        dropped_count=int(len(dropped_case_ids)),
        duplicate_count=int(duplicate_count),
        containment_count=int(containment_count),
        unresolved_duplicate_count=int(unresolved_duplicate_count),
        relation_samples=samples,
        dropped_case_ids=dropped_case_ids,
        kept_case_ids=[case_id(item) for item in kept],
    )


def classify_case_fact_relation(
    left: dict[str, Any],
    right: dict[str, Any],
) -> CaseFactRelation:
    """兼容旧调用名；实际统一走完整用例语义身份关系。"""

    return compare_case_semantic_identity(left, right).relation


__all__ = [
    "CaseFactRelation",
    "CaseSemanticComparison",
    "CaseSemanticIdentity",
    "SemanticDeduplicationResult",
    "build_case_semantic_identity",
    "classify_case_fact_relation",
    "compare_case_semantic_identity",
    "deduplicate_cases_by_semantic_identity",
    "normalize_case_semantic_text",
    "verified_case_fact_ids",
]
