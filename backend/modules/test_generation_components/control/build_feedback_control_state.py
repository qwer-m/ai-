from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from core.db.models import KnowledgeDocument, RagDataset, RagDatasetSample
from modules.memory_fabric.contracts.memory_context import MemoryContext
from modules.memory_fabric.contracts.memory_fabric import MemoryFabric
from modules.memory_fabric.runtime.diagnostics import record_memory_read
from modules.memory_fabric.runtime.factory import get_memory_fabric
from modules.test_generation_components.control.feedback_control_state import FeedbackControlState
from modules.testing.priority_sample_pool_store import (
    ensure_priority_pool_pattern_index,
    load_priority_sample_pool,
    retrieve_priority_sample_patterns,
)


_RULE_PATTERN = re.compile(r"\b(?:RULE|REQ)[-_ ]?\d+\b", re.IGNORECASE)
_MAX_MUST_COVER_RULES = 12
_MAX_SCENARIOS = 8
_MAX_FORBIDDEN_PATTERNS = 8
_MAX_SOFT_CONSTRAINTS = 14
_MAX_QUALITY_HINTS = 12
_MAX_EVAL_REPORT_DOCS = 6
_MAX_AGENT_LEARNING_DOCS = 3
_MAX_DATASET_SAMPLES = 500
_MAX_PRIORITY_POOL_SAMPLES = 400
_MAX_PRIORITY_POOL_HINTS = 14
_MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS = 8
_MAX_PRIORITY_POOL_SOFT_CONSTRAINTS = 14
_MAX_PRIORITY_POOL_SCENARIOS = 8
_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K = max(
    1,
    min(
        _MAX_PRIORITY_POOL_SAMPLES,
        int(os.getenv("TESTGEN_PRIORITY_POOL_RETRIEVAL_TOP_K", "5")),
    ),
)
_MAX_PRIORITY_POOL_CLUSTER_CAP = max(
    1,
    min(
        _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
        int(os.getenv("TESTGEN_PRIORITY_POOL_CLUSTER_CAP", "2")),
    ),
)
_SYNC_PRIORITY_INDEX_ON_READ = str(
    os.getenv("TESTGEN_PRIORITY_POOL_INDEX_SYNC_ON_READ", "false")
).strip().lower() in {"1", "true", "yes", "on"}
_ASCII_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+", re.IGNORECASE)
_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")


_VALID_REASON_CATEGORY = {
    "core_flow",
    "exception_path",
    "boundary_condition",
    "state_transition",
    "redundant_case",
    "display_issue",
    "other",
}

_REASON_TO_SCENARIO = {
    "core_flow": "核心流程稳定性场景",
    "exception_path": "异常路径与错误处理场景",
    "boundary_condition": "边界值与极端输入场景",
    "state_transition": "状态迁移一致性场景",
}

_REASON_HINTS = {
    "core_flow": "核心流程失败场景优先级不低于P1，阻断风险优先P0。",
    "exception_path": "补充失败重试、异常返回与错误恢复路径。",
    "boundary_condition": "补充边界值、空值、最大最小值与非法输入校验。",
    "state_transition": "补充状态迁移前后约束、回退与幂等验证。",
    "redundant_case": "避免仅措辞差异的语义重复用例，优先保留覆盖增益高的用例。",
    "display_issue": "优先级展示值必须与最终判定一致，以finalPriority为准。",
}


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return int(default)


def _sample_value(sample: Any, key: str, default: Any = None) -> Any:
    if isinstance(sample, dict):
        return sample.get(key, default)
    return getattr(sample, key, default)


def _doc_value(doc: Any, key: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        return doc.get(key, default)
    return getattr(doc, key, default)


def _normalize_rule_id(raw: str) -> str:
    return re.sub(r"[-_ ]+", "-", str(raw or "").strip().upper())


def _extract_rule_ids(text: str) -> list[str]:
    return [_normalize_rule_id(item) for item in _RULE_PATTERN.findall(str(text or ""))]


def _sample_value(sample: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in sample:
            return sample.get(key)
    return None


def _normalize_reason_category(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _VALID_REASON_CATEGORY else ""


def _normalize_expected_priority(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    return value if value in {"P0", "P1", "P2", "P3"} else ""


def _normalize_comment_hint(comment: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(comment or "").strip())
    if len(cleaned) < 6:
        return ""
    return cleaned[:140]


def _extract_forbidden_pattern_from_sample(*, title: str, comment: str) -> str:
    # Keep only concise semantic anchors; avoid hard-coding prefixes that cannot match case text.
    candidate = str(title or "").strip() or str(comment or "").strip()
    candidate = re.sub(r"^[\-*•\d\.\s]+", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if len(candidate) < 4:
        return ""
    return candidate[:40]


def _is_manual_verified_sample(*, reason: str, expected_priority: str, comment: str) -> bool:
    if reason:
        return True
    if expected_priority in {"P0", "P1", "P2", "P3"}:
        return True
    return len(str(comment or "").strip()) >= 6


def _is_pattern_active(sample_like: dict[str, Any]) -> bool:
    status = str(
        _sample_value(sample_like, "governance_status", "pattern_status", "patternStatus")
        or ""
    ).strip().lower()
    return status != "disabled"


def _select_priority_pool_samples_by_requirement(
    *,
    samples: list[dict[str, Any]],
    project_id: int,
    user_id: int,
    generation_id: int | None = None,
    pattern_index_token: str = "",
    requirement_text: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    retrieval_meta: dict[str, Any] = {
        "retrieval_query_used": bool(str(requirement_text or "").strip()),
        "retrieval_top_k": int(_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K),
        "retrieval_hit_count": 0,
        "retrieval_selected_count": 0,
        "retrieval_fallback": "none",
        "retrieval_selected_weight_avg": 0.0,
        "retrieval_selected_quality_avg": 0.0,
        "retrieval_diversity_cluster_cap": int(_MAX_PRIORITY_POOL_CLUSTER_CAP),
        "retrieval_diversity_skipped_count": 0,
        "retrieval_lexical_fallback_used": False,
        "retrieval_active_sample_count": 0,
        "retrieval_disabled_sample_count": 0,
    }
    if not samples:
        return [], retrieval_meta
    active_samples = [item for item in samples if _is_pattern_active(item)]
    retrieval_meta["retrieval_active_sample_count"] = int(len(active_samples))
    retrieval_meta["retrieval_disabled_sample_count"] = int(len(samples) - len(active_samples))
    if not active_samples:
        retrieval_meta["retrieval_fallback"] = "no_active_patterns"
        return [], retrieval_meta

    def _cluster_key(sample_like: dict[str, Any]) -> str:
        return str(
            _sample_value(sample_like, "pattern_cluster_key", "patternClusterKey")
            or _sample_value(sample_like, "pattern_canonical", "patternCanonical")
            or _sample_value(sample_like, "pattern_summary", "patternSummary")
            or _sample_value(sample_like, "title")
            or _sample_value(sample_like, "case_id", "caseId")
            or ""
        ).strip().lower()[:120]

    def _apply_diversity_cap(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cluster_counter: Counter[str] = Counter()
        selected_local: list[dict[str, Any]] = []
        skipped = 0
        for item in candidates:
            key = _cluster_key(item) or "misc"
            if int(cluster_counter.get(key, 0)) >= int(_MAX_PRIORITY_POOL_CLUSTER_CAP):
                skipped += 1
                continue
            cluster_counter[key] += 1
            selected_local.append(item)
            if len(selected_local) >= _MAX_PRIORITY_POOL_RETRIEVAL_TOP_K:
                break
        retrieval_meta["retrieval_diversity_skipped_count"] = int(
            retrieval_meta.get("retrieval_diversity_skipped_count") or 0
        ) + int(skipped)
        return selected_local

    def _ascii_tokens(text: str) -> set[str]:
        return {token.lower() for token in _ASCII_TOKEN_PATTERN.findall(str(text or "")) if token}

    def _cjk_chars(text: str) -> set[str]:
        return {char for char in _CJK_CHAR_PATTERN.findall(str(text or "")) if char.strip()}

    query = str(requirement_text or "").strip()
    query_ascii = _ascii_tokens(query)
    query_cjk = _cjk_chars(query)

    def _lexical_score(sample_like: dict[str, Any]) -> float:
        text = " ".join(
            str(part or "")
            for part in [
                _sample_value(sample_like, "pattern_summary", "patternSummary"),
                _sample_value(sample_like, "pattern_canonical", "patternCanonical"),
                _sample_value(sample_like, "title"),
                _sample_value(sample_like, "user_comment", "userComment"),
                _sample_value(sample_like, "reason_category", "reasonCategory"),
            ]
            if str(part or "").strip()
        )
        if not text:
            return 0.0
        sample_ascii = _ascii_tokens(text)
        sample_cjk = _cjk_chars(text)
        ascii_overlap = len(query_ascii & sample_ascii) if query_ascii else 0
        cjk_overlap = len(query_cjk & sample_cjk) if query_cjk else 0
        try:
            weight = float(_sample_value(sample_like, "pattern_weight") or 0.0)
        except Exception:
            weight = 0.0
        return float(ascii_overlap * 2.0 + cjk_overlap * 0.6 + min(max(weight, 0.0), 2.0) * 0.2)

    if not query:
        candidates = sorted(
            active_samples,
            key=lambda item: (
                float(_sample_value(item, "pattern_weight") or 0.0),
                float(_sample_value(item, "pattern_quality_score") or 0.0),
            ),
            reverse=True,
        )
        selected = _apply_diversity_cap(candidates)
        retrieval_meta["retrieval_selected_count"] = int(len(selected))
        retrieval_meta["retrieval_fallback"] = "top_weight_no_query"
        if selected:
            retrieval_meta["retrieval_selected_weight_avg"] = round(
                sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in selected) / len(selected),
                4,
            )
            retrieval_meta["retrieval_selected_quality_avg"] = round(
                sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in selected) / len(selected),
                4,
            )
        return selected, retrieval_meta

    try:
        retrieved = retrieve_priority_sample_patterns(
            project_id=int(project_id),
            user_id=int(user_id),
            query_text=query,
            generation_id=(int(generation_id) if generation_id is not None else None),
            pattern_index_token=str(pattern_index_token or ""),
            top_k=_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K,
        )
    except Exception:
        retrieved = []

    retrieval_meta["retrieval_hit_count"] = int(len(retrieved))
    index_seen: set[int] = set()
    pattern_seen: set[str] = set()
    selected_raw: list[dict[str, Any]] = []
    for item in retrieved:
        try:
            sample_index = int((item or {}).get("sample_index"))
        except Exception:
            continue
        if sample_index < 0 or sample_index >= len(samples):
            continue
        if sample_index in index_seen:
            continue
        canonical = str((item or {}).get("pattern_canonical") or "").strip().lower()
        if canonical and canonical in pattern_seen:
            continue
        index_seen.add(sample_index)
        picked = samples[sample_index]
        if not _is_pattern_active(picked):
            continue
        if canonical:
            pattern_seen.add(canonical)
        retrieved_weight = float((item or {}).get("pattern_weight") or 0.0)
        retrieved_quality = float((item or {}).get("pattern_quality_score") or 0.0)
        if retrieved_weight > 0:
            picked = dict(picked)
            picked["pattern_weight"] = round(retrieved_weight, 4)
            picked["pattern_quality_score"] = round(max(0.0, min(1.0, retrieved_quality)), 4)
        selected_raw.append(picked)
        if len(selected_raw) >= (_MAX_PRIORITY_POOL_RETRIEVAL_TOP_K * 3):
            break
    selected = _apply_diversity_cap(selected_raw)

    if selected:
        retrieval_meta["retrieval_selected_count"] = int(len(selected))
        retrieval_meta["retrieval_selected_weight_avg"] = round(
            sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in selected) / len(selected),
            4,
        )
        retrieval_meta["retrieval_selected_quality_avg"] = round(
            sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in selected) / len(selected),
            4,
        )
        return selected, retrieval_meta

    lexical_sorted = sorted(
        active_samples,
        key=lambda item: (
            _lexical_score(item),
            float(_sample_value(item, "pattern_weight") or 0.0),
            float(_sample_value(item, "pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    lexical_selected = _apply_diversity_cap(lexical_sorted)
    if lexical_selected:
        retrieval_meta["retrieval_fallback"] = "lexical_fallback"
        retrieval_meta["retrieval_lexical_fallback_used"] = True
        retrieval_meta["retrieval_selected_count"] = int(len(lexical_selected))
        retrieval_meta["retrieval_selected_weight_avg"] = round(
            sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in lexical_selected) / len(lexical_selected),
            4,
        )
        retrieval_meta["retrieval_selected_quality_avg"] = round(
            sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in lexical_selected) / len(lexical_selected),
            4,
        )
        return lexical_selected, retrieval_meta

    retrieval_meta["retrieval_fallback"] = "head_top_k"
    fallback = sorted(
        active_samples,
        key=lambda item: (
            float(_sample_value(item, "pattern_weight") or 0.0),
            float(_sample_value(item, "pattern_quality_score") or 0.0),
        ),
        reverse=True,
    )
    fallback = _apply_diversity_cap(fallback)
    retrieval_meta["retrieval_selected_count"] = int(len(fallback))
    if fallback:
        retrieval_meta["retrieval_selected_weight_avg"] = round(
            sum(float(_sample_value(item, "pattern_weight") or 0.0) for item in fallback) / len(fallback),
            4,
        )
        retrieval_meta["retrieval_selected_quality_avg"] = round(
            sum(float(_sample_value(item, "pattern_quality_score") or 0.0) for item in fallback) / len(fallback),
            4,
        )
    return fallback, retrieval_meta


def _build_from_priority_sample_pool(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    requirement_text: str = "",
) -> FeedbackControlState:
    if db is None or not project_id or not user_id:
        return FeedbackControlState.empty()

    payload = load_priority_sample_pool(
        db=db,
        project_id=int(project_id),
        user_id=int(user_id),
    )
    if not isinstance(payload, dict):
        return FeedbackControlState.empty()

    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        return FeedbackControlState.empty()

    samples: list[dict[str, Any]] = []
    for item in raw_samples[:_MAX_PRIORITY_POOL_SAMPLES]:
        if isinstance(item, dict):
            samples.append(item)
    if not samples:
        return FeedbackControlState.empty()
    if _SYNC_PRIORITY_INDEX_ON_READ:
        try:
            ensure_priority_pool_pattern_index(
                project_id=int(project_id),
                user_id=int(user_id),
                generation_id=_safe_int(payload.get("generation_id"), default=0) or None,
                pattern_index_token=str(payload.get("pattern_index_token") or "").strip(),
                samples=samples,
            )
        except Exception:
            pass
    selected_samples, retrieval_meta = _select_priority_pool_samples_by_requirement(
        samples=samples,
        project_id=int(project_id),
        user_id=int(user_id),
        generation_id=_safe_int(payload.get("generation_id"), default=0) or None,
        pattern_index_token=str(payload.get("pattern_index_token") or "").strip(),
        requirement_text=str(requirement_text or ""),
    )
    if not selected_samples:
        return FeedbackControlState.empty()

    reason_counter: Counter[str] = Counter()
    expected_counter: Counter[str] = Counter()
    rule_counter: Counter[str] = Counter()
    rule_expected_high: set[str] = set()
    scenario_counter: Counter[str] = Counter()
    pattern_counter: Counter[str] = Counter()
    forbidden_patterns: list[str] = []
    soft_constraints: list[str] = []
    quality_hints: list[str] = []
    verified_count = 0
    manual_comment_count = 0

    for sample in selected_samples:
        reason = _normalize_reason_category(
            _sample_value(sample, "reason_category", "reasonCategory")
        )
        expected_priority = _normalize_expected_priority(
            _sample_value(sample, "expected_priority", "expectedPriority")
        )
        comment = str(
            _sample_value(sample, "user_comment", "userComment") or ""
        ).strip()

        if not _is_manual_verified_sample(
            reason=reason,
            expected_priority=expected_priority,
            comment=comment,
        ):
            continue

        verified_count += 1
        if reason:
            reason_counter[reason] += 1
        if expected_priority:
            expected_counter[expected_priority] += 1

        scenario_label = _REASON_TO_SCENARIO.get(reason)
        if scenario_label:
            scenario_counter[scenario_label] += 1

        reason_hint = _REASON_HINTS.get(reason)
        if reason_hint:
            quality_hints.append(reason_hint)

        case_id = str(_sample_value(sample, "case_id", "caseId") or "").strip()
        title = str(_sample_value(sample, "title") or "").strip()
        pattern_key = str(
            _sample_value(sample, "pattern_canonical", "patternCanonical")
            or _sample_value(sample, "pattern_summary", "patternSummary")
            or title
            or case_id
        ).strip()
        if pattern_key:
            pattern_counter[pattern_key[:120]] += 1
        priority_debug = _sample_value(sample, "priority_debug", "priorityDebug")
        sample_text = " ".join(
            [
                case_id,
                title,
                comment,
                str(priority_debug or ""),
            ]
        )
        sample_rules = _extract_rule_ids(sample_text)
        for rule_id in sample_rules:
            rule_counter[rule_id] += 1
            if expected_priority in {"P0", "P1"}:
                rule_expected_high.add(rule_id)

        if reason == "redundant_case":
            pattern = str(
                _sample_value(sample, "pattern_summary", "patternSummary")
                or ""
            ).strip() or _extract_forbidden_pattern_from_sample(title=title, comment=comment)
            if pattern:
                soft_constraints.append(pattern)

        comment_hint = _normalize_comment_hint(comment)
        if comment_hint:
            manual_comment_count += 1
            quality_hints.append(comment_hint)

        if expected_priority in {"P0", "P1"}:
            label = case_id or title or "manual_case"
            quality_hints.append(f"{label} 期望优先级 {expected_priority}（人工标注）。")

    if verified_count <= 0:
        return FeedbackControlState.empty()

    must_cover_rules = [rule for rule, _ in rule_counter.most_common(_MAX_MUST_COVER_RULES)]
    must_have_scenarios = [
        scenario for scenario, _ in scenario_counter.most_common(_MAX_PRIORITY_POOL_SCENARIOS)
    ]
    rule_quota: dict[str, int] = {}
    for rule_id in must_cover_rules:
        base = 2 if rule_id in rule_expected_high else 1
        if int(rule_counter.get(rule_id, 0)) >= 2:
            base = max(base, 2)
        rule_quota[rule_id] = int(base)

    return FeedbackControlState(
        must_cover_rules=must_cover_rules,
        must_have_scenarios=must_have_scenarios,
        forbidden_patterns=forbidden_patterns[:_MAX_PRIORITY_POOL_FORBIDDEN_PATTERNS],
        soft_constraints=soft_constraints[:_MAX_PRIORITY_POOL_SOFT_CONSTRAINTS],
        rule_quota=rule_quota,
        quality_fix_hints=quality_hints[:_MAX_PRIORITY_POOL_HINTS],
        source_meta={
            "sources": ["priority_sample_pool_manual_verified"],
            "priority_pool_sample_count": int(len(samples)),
            "priority_pool_selected_sample_count": int(len(selected_samples)),
            "verified_sample_count": int(verified_count),
            "manual_comment_count": int(manual_comment_count),
            "reason_category_distribution": dict(reason_counter),
            "expected_priority_distribution": dict(expected_counter),
            "pattern_hit_distribution": {
                key: int(value)
                for key, value in pattern_counter.most_common(12)
            },
            "pattern_hit_total": int(sum(pattern_counter.values())),
            "generation_id": payload.get("generation_id"),
            **retrieval_meta,
        },
    )


def _extract_forbidden_patterns(text: str) -> list[str]:
    patterns: list[str] = []
    normalized = str(text or "").strip()
    if not normalized:
        return patterns
    lines = [line.strip() for line in re.split(r"[\n\r]+", normalized) if line.strip()]
    for line in lines:
        lowered = line.lower()
        if any(token in lowered for token in ("禁止", "避免", "不要", "不得", "严禁", "must not", "forbidden")):
            snippet = re.sub(r"^[\-*•\d\.\s]+", "", line)
            if len(snippet) >= 4:
                patterns.append(snippet[:120])
    return patterns


def _extract_quality_hints(text: str) -> list[str]:
    hints: list[str] = []
    normalized = str(text or "").strip()
    if not normalized:
        return hints
    lines = [line.strip() for line in re.split(r"[\n\r]+", normalized) if line.strip()]
    quality_keywords = (
        "预期",
        "断言",
        "步骤",
        "边界",
        "异常",
        "状态",
        "覆盖",
        "去重",
        "可验证",
        "完整",
        "quality",
        "coverage",
        "assert",
        "duplicate",
    )
    for line in lines:
        cleaned = re.sub(r"^[\-*•\d\.\s]+", "", line)
        lowered = cleaned.lower()
        if len(cleaned) < 6:
            continue
        if any(keyword in lowered for keyword in quality_keywords):
            hints.append(cleaned[:140])
    return hints


def _extract_scenarios_from_text(text: str) -> list[str]:
    lowered = str(text or "").lower()
    scenarios: list[str] = []
    rules = [
        ("权限/鉴权异常场景", ("权限", "鉴权", "越权", "auth", "permission")),
        ("失败重试与错误处理场景", ("失败", "错误", "重试", "exception", "error", "retry")),
        ("边界值与极端输入场景", ("边界", "最大", "最小", "临界", "boundary", "max", "min")),
        ("状态流转一致性场景", ("状态", "流转", "state", "transition")),
        ("性能与稳定性场景", ("性能", "超时", "并发", "performance", "timeout", "concurrent")),
    ]
    for label, keys in rules:
        if any(key in lowered for key in keys):
            scenarios.append(label)
    return scenarios


def _build_from_anomaly_pool(
    *,
    db: Any,
    user_id: int,
    max_samples: int = _MAX_DATASET_SAMPLES,
    memory_fabric: MemoryFabric | None = None,
    memory_ctx: MemoryContext | None = None,
    memory_diag: dict[str, Any] | None = None,
) -> FeedbackControlState:
    if db is None or not user_id:
        return FeedbackControlState.empty()

    dataset_map: dict[int, str] = {}
    sample_rows: list[Any] = []
    memory_read_ok = False
    if memory_fabric is not None and memory_ctx is not None:
        try:
            payload = memory_fabric.read_rule(
                {
                    "kind": "anomaly_pool_samples",
                    "db": db,
                    "user_id": int(user_id),
                    "limit": max(1, int(max_samples)),
                },
                memory_ctx,
            )
            if isinstance(payload, dict):
                dataset_map = {int(k): str(v or "").strip().lower() for k, v in (payload.get("dataset_map") or {}).items()}
                sample_rows = [item for item in (payload.get("samples") or [])]
            memory_read_ok = True
            record_memory_read(memory_diag, "rule", via_memory_fabric=True)
        except Exception:
            dataset_map = {}
            sample_rows = []

    if (not memory_read_ok) and (not dataset_map and not sample_rows):
        dataset_rows = (
            db.query(RagDataset.id, RagDataset.type)
            .filter(
                RagDataset.user_id == int(user_id),
                RagDataset.type.in_(["challenge", "regression"]),
            )
            .all()
        )
        dataset_map = {int(row.id): str(row.type or "").strip().lower() for row in dataset_rows}
        if not dataset_map:
            return FeedbackControlState.empty()
        sample_rows = (
            db.query(RagDatasetSample)
            .filter(
                RagDatasetSample.dataset_id.in_(list(dataset_map.keys())),
                RagDatasetSample.enabled.is_(True),
            )
            .order_by(RagDatasetSample.updated_at.desc(), RagDatasetSample.id.desc())
            .limit(max(1, int(max_samples)))
            .all()
        )
        record_memory_read(memory_diag, "rule", via_memory_fabric=False)

    weighted_rule_counter: Counter[str] = Counter()
    scenario_counter: Counter[str] = Counter()
    source_counts = {"challenge_samples": 0, "regression_samples": 0}

    for sample in sample_rows:
        dataset_type = dataset_map.get(_safe_int(_sample_value(sample, "dataset_id")), "challenge")
        weight = 2 if dataset_type == "regression" else 1
        source_counts[f"{dataset_type}_samples"] = int(source_counts.get(f"{dataset_type}_samples", 0)) + 1

        answer_points = " ".join(str(item) for item in (_sample_value(sample, "answer_points", []) or []))
        tags = " ".join(str(item) for item in (_sample_value(sample, "tags", []) or []))
        merged_text = " ".join(
            [
                str(_sample_value(sample, "query", "") or ""),
                str(_sample_value(sample, "gold_answer", "") or ""),
                answer_points,
                tags,
            ]
        )
        for rule in _extract_rule_ids(merged_text):
            weighted_rule_counter[rule] += int(weight)
        for scenario in _extract_scenarios_from_text(merged_text):
            scenario_counter[scenario] += int(weight)

    must_cover = [rule for rule, _ in weighted_rule_counter.most_common(_MAX_MUST_COVER_RULES)]
    must_have_scenarios = [name for name, _ in scenario_counter.most_common(_MAX_SCENARIOS)]
    rule_quota = {
        rule: (2 if int(score) >= 3 else 1)
        for rule, score in weighted_rule_counter.items()
        if rule in must_cover
    }

    return FeedbackControlState(
        must_cover_rules=must_cover,
        must_have_scenarios=must_have_scenarios,
        forbidden_patterns=[],
        soft_constraints=[],
        rule_quota=rule_quota,
        quality_fix_hints=[],
        source_meta={
            "sources": ["anomaly_pool"],
            "sample_count": int(len(sample_rows)),
            "challenge_sample_count": int(source_counts.get("challenge_samples", 0)),
            "regression_sample_count": int(source_counts.get("regression_samples", 0)),
            "rule_frequency": dict(weighted_rule_counter),
        },
    )


def _build_from_reports(
    *,
    db: Any,
    project_id: int,
    user_id: int,
    include_agent_learning: bool = True,
    memory_fabric: MemoryFabric | None = None,
    memory_ctx: MemoryContext | None = None,
    memory_diag: dict[str, Any] | None = None,
) -> FeedbackControlState:
    if db is None or not project_id or not user_id:
        return FeedbackControlState.empty()

    eval_docs: list[Any] = []
    agent_docs: list[Any] = []
    memory_read_ok = False
    if memory_fabric is not None and memory_ctx is not None:
        try:
            eval_docs = list(
                memory_fabric.read_semantic(
                    {
                        "kind": "knowledge_documents",
                        "db": db,
                        "project_id": int(project_id),
                        "user_id": int(user_id),
                        "doc_types": ["evaluation_report"],
                        "limit": _MAX_EVAL_REPORT_DOCS,
                    },
                    memory_ctx,
                )
                or []
            )
            if include_agent_learning:
                agent_docs = list(
                    memory_fabric.read_semantic(
                        {
                            "kind": "knowledge_documents",
                            "db": db,
                            "project_id": int(project_id),
                            "user_id": int(user_id),
                            "doc_types": ["agent_learning"],
                            "limit": _MAX_AGENT_LEARNING_DOCS,
                        },
                        memory_ctx,
                    )
                    or []
                )
            memory_read_ok = True
            record_memory_read(memory_diag, "semantic", via_memory_fabric=True)
        except Exception:
            eval_docs = []
            agent_docs = []

    if (not memory_read_ok) and (not eval_docs and not agent_docs):
        eval_docs = (
            db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.project_id == int(project_id),
                KnowledgeDocument.user_id == int(user_id),
                KnowledgeDocument.doc_type == "evaluation_report",
            )
            .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
            .limit(_MAX_EVAL_REPORT_DOCS)
            .all()
        )

        if include_agent_learning:
            agent_docs = (
                db.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.project_id == int(project_id),
                    KnowledgeDocument.user_id == int(user_id),
                    KnowledgeDocument.doc_type == "agent_learning",
                )
                .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
                .limit(_MAX_AGENT_LEARNING_DOCS)
                .all()
            )
        record_memory_read(memory_diag, "semantic", via_memory_fabric=False)

    all_docs = [*eval_docs, *agent_docs]
    if not all_docs:
        return FeedbackControlState.empty()

    rule_counter: Counter[str] = Counter()
    forbidden_patterns: list[str] = []
    quality_hints: list[str] = []
    must_have_scenarios: list[str] = []
    doc_types: Counter[str] = Counter()

    for doc in all_docs:
        text = str(_doc_value(doc, "content", "") or "")
        doc_types[str(_doc_value(doc, "doc_type", "unknown") or "unknown")] += 1
        for rule in _extract_rule_ids(text):
            rule_counter[rule] += 1
        forbidden_patterns.extend(_extract_forbidden_patterns(text))
        quality_hints.extend(_extract_quality_hints(text))
        must_have_scenarios.extend(_extract_scenarios_from_text(text))

    must_cover = [rule for rule, _ in rule_counter.most_common(_MAX_MUST_COVER_RULES)]
    scenario_counter = Counter(must_have_scenarios)

    return FeedbackControlState(
        must_cover_rules=must_cover,
        must_have_scenarios=[name for name, _ in scenario_counter.most_common(_MAX_SCENARIOS)],
        forbidden_patterns=forbidden_patterns[:_MAX_FORBIDDEN_PATTERNS],
        soft_constraints=[],
        rule_quota={rule: 1 for rule in must_cover},
        quality_fix_hints=quality_hints[:_MAX_QUALITY_HINTS],
        source_meta={
            "sources": ["evaluation_report", "agent_learning"] if agent_docs else ["evaluation_report"],
            "doc_count": int(len(all_docs)),
            "doc_types": dict(doc_types),
        },
    )


def _compact_state(state: FeedbackControlState) -> FeedbackControlState:
    normalized = FeedbackControlState.from_any(state)
    normalized.must_cover_rules = normalized.must_cover_rules[:_MAX_MUST_COVER_RULES]
    normalized.must_have_scenarios = normalized.must_have_scenarios[:_MAX_SCENARIOS]
    normalized.forbidden_patterns = normalized.forbidden_patterns[:_MAX_FORBIDDEN_PATTERNS]
    normalized.soft_constraints = normalized.soft_constraints[:_MAX_SOFT_CONSTRAINTS]
    normalized.quality_fix_hints = normalized.quality_fix_hints[:_MAX_QUALITY_HINTS]
    normalized.rule_quota = {
        rule: max(1, int(quota))
        for rule, quota in normalized.rule_quota.items()
        if rule in set(normalized.must_cover_rules)
    }
    return normalized


def build_feedback_control_state(
    *,
    db: Any = None,
    project_id: int | None = None,
    user_id: int | None = None,
    requirement_text: str = "",
    enable_priority_sample_pool: bool = True,
    include_agent_learning: bool = True,
    memory_fabric: MemoryFabric | None = None,
    memory_ctx: MemoryContext | None = None,
    memory_diag: dict[str, Any] | None = None,
) -> FeedbackControlState:
    """
    中文注释：聚合异常样本池与评估知识，形成生成前可执行控制状态。
    """
    state = FeedbackControlState.empty()

    resolved_memory_fabric = memory_fabric
    if resolved_memory_fabric is None:
        try:
            resolved_memory_fabric = get_memory_fabric()
        except Exception:
            resolved_memory_fabric = None
    resolved_memory_ctx = memory_ctx or MemoryContext.from_runtime(
        user_id=user_id,
        project_id=project_id,
        run_id="legacy-control-state",
        request_id="legacy-control-state",
    )

    if enable_priority_sample_pool:
        priority_pool_state = _build_from_priority_sample_pool(
            db=db,
            project_id=int(project_id or 0),
            user_id=int(user_id or 0),
            requirement_text=str(requirement_text or ""),
        )
        state = state.merge(priority_pool_state)

    anomaly_state = _build_from_anomaly_pool(
        db=db,
        user_id=int(user_id or 0),
        memory_fabric=resolved_memory_fabric,
        memory_ctx=resolved_memory_ctx,
        memory_diag=memory_diag,
    )
    state = state.merge(anomaly_state)

    report_state = _build_from_reports(
        db=db,
        project_id=int(project_id or 0),
        user_id=int(user_id or 0),
        include_agent_learning=bool(include_agent_learning),
        memory_fabric=resolved_memory_fabric,
        memory_ctx=resolved_memory_ctx,
        memory_diag=memory_diag,
    )
    state = state.merge(report_state)

    return _compact_state(state)
