"""Derive reusable sample-pool signals from human-final test cases."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from io import StringIO
from typing import Any

from core.db.models import LogEntry
from modules.knowledge_base_components.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from modules.testing.priority_sample_pool_store import (
    append_learning_event,
    load_priority_sample_pool,
    upsert_priority_sample_pool,
)
from ..control.workflow_blueprint_repository import WorkflowBlueprintRepository
from ..repositories.history_repository import (
    TestGenerationHistoryRepository,
)
from ..postprocess.case_access import (
    case_field_alias_key_set,
    case_fields,
    case_priority,
    case_text_list_value,
    case_text_parts,
    case_value,
)

_MAX_DERIVED_POSITIVE_SAMPLES = 120
_MAX_DERIVED_POSITIVE_PATTERNS = 40
_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY = 2
_MAX_DERIVED_NEGATIVE_SAMPLES = 80
_MAX_POOL_SAMPLES = 5000
_SIMILARITY_MATCH_THRESHOLD = 0.62
_MAX_EVALUATION_LEARNING_CANDIDATES = 80
_MAX_EVALUATION_POSITIVE_CANDIDATES_PER_FIELD = 8
_MAX_EVALUATION_FIX_CANDIDATES_PER_FIELD = 6
_MAX_EVALUATION_NEGATIVE_CANDIDATES_PER_FIELD = 3
_EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY = "evaluation_defect_reusable_pattern_v1"

_CASE_FIELDS = case_fields()
_CASE_FIELD_ALIAS_KEYS = case_field_alias_key_set()

_NON_ASSERTABLE_EXPECTED_PATTERNS = (
    "正常展示",
    "正常显示",
    "执行成功",
    "符合预期",
    "返回成功",
    "结果正确",
    "结果可核对",
    "按配置",
    "无异常",
)

_LOW_VALUE_UI_TOKENS = (
    "按钮",
    "样式",
    "布局",
    "颜色",
    "文案",
    "展示",
    "显示",
    "页面标题",
    "进度条",
    "时长",
    "打印弹窗",
    "倍速",
    "视频播放",
    "网络异常",
)

_BUSINESS_IMPACT_TOKENS = (
    "退款",
    "退费",
    "购卡",
    "开卡",
    "余额",
    "金额",
    "订单",
    "交易",
    "支付",
    "权限",
    "未开卡",
    "督导",
    "ta",
    "ops",
    "小程序",
    "学习报告",
    "课程管理",
    "学习状态",
    "状态同步",
    "跨端",
    "回滚",
    "隔离",
    "一致",
)

_CROSS_SYSTEM_TOKENS = (
    "跨端",
    "小程序",
    "ops",
    "ta",
    "督导",
    "书房",
    "后台",
    "管理端",
    "admin",
    "client",
    "backend",
    "report",
    "cross",
)
_STATE_TOKENS = (
    "状态",
    "进度",
    "同步",
    "保留",
    "未丢失",
    "一致",
    "记录",
    "state",
    "progress",
    "retain",
    "retained",
    "unchanged",
    "consistent",
    "switch",
    "switching",
)
_TRANSACTION_TOKENS = (
    "支付",
    "购卡",
    "开卡",
    "退款",
    "退费",
    "订单",
    "金额",
    "余额",
    "交易",
    "payment",
    "refund",
    "order",
    "transaction",
    "rollback",
)
_PERMISSION_TOKENS = (
    "权限",
    "未开卡",
    "不可访问",
    "隐藏",
    "绕过",
    "隔离",
    "permission",
    "unauthorized",
    "forbidden",
    "hidden",
    "access",
)


def parse_test_cases_payload(raw: Any) -> list[dict[str, Any]]:
    """Parse JSON/CSV/plain payloads into normalized case dictionaries."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [_normalize_case_dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("cases", "test_cases", "items", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [_normalize_case_dict(item) for item in value if isinstance(item, dict)]
        return [_normalize_case_dict(raw)]

    text = str(raw or "").strip()
    if not text:
        return []
    parsed = _parse_json_cases(text)
    if parsed:
        return parsed
    parsed = _parse_html_table_cases(text)
    if parsed:
        return parsed
    parsed = _parse_csv_cases(text)
    if parsed:
        return parsed
    return []


def parse_test_cases_spreadsheet_bytes(filename: str, content_bytes: bytes) -> list[dict[str, Any]]:
    """Parse uploaded spreadsheet bytes directly into normalized test cases."""
    lowered = str(filename or "").lower()
    if not lowered.endswith((".xlsx", ".xls")):
        return []

    header_markers = _case_table_header_markers()
    all_rows: list[list[str]] = []
    if lowered.endswith(".xlsx"):
        try:
            import openpyxl

            workbook = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True)
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    all_rows.append([_text(cell).strip() for cell in row])
        except Exception:
            all_rows = []

    if not all_rows:
        try:
            import pandas as pd

            sheets = pd.read_excel(io.BytesIO(content_bytes), sheet_name=None, header=None)
            for sheet in sheets.values():
                all_rows.extend(sheet.fillna("").astype(str).values.tolist())
        except Exception:
            return []

    return _parse_case_table_rows(all_rows, header_markers=header_markers)


def build_learning_samples_from_final_cases(
    *,
    generated_cases: list[dict[str, Any]],
    final_cases: list[dict[str, Any]],
    requirement_text: str = "",
    generation_id: int | None = None,
    linked_doc_ids: list[int] | None = None,
    include_negative_samples: bool = True,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build positive and limited negative samples from generated vs final cases.

    Human-final cases are always positive evidence, including cases that expand
    beyond the uploaded requirement. AI-only cases become negative only when
    they show a concrete quality failure.
    """
    normalized_generated = [_normalize_case_dict(item) for item in generated_cases if isinstance(item, dict)]
    normalized_final = [_normalize_case_dict(item) for item in final_cases if isinstance(item, dict)]
    ledger_summary = _compact_quality_ledger(quality_ledger)

    positive_candidates: list[dict[str, Any]] = []
    for idx, case in enumerate(normalized_final[:_MAX_DERIVED_POSITIVE_SAMPLES], start=1):
        extension = bool(str(requirement_text or "").strip()) and not _case_is_grounded_in_requirement(
            case,
            requirement_text,
        )
        positive_candidates.append(
            _build_positive_sample(
                case,
                index=idx,
                generation_id=generation_id,
                linked_doc_ids=linked_doc_ids or [],
                manual_business_extension=extension,
                quality_ledger=ledger_summary,
            )
        )
    positives = _aggregate_positive_pattern_samples(positive_candidates)
    workflow_blueprint_sample = _build_workflow_blueprint_sample(
        normalized_final,
        generation_id=generation_id,
        linked_doc_ids=linked_doc_ids or [],
        quality_ledger=ledger_summary,
    )
    if workflow_blueprint_sample is not None:
        positives = [workflow_blueprint_sample, *positives]

    negatives: list[dict[str, Any]] = []
    if include_negative_samples:
        matched_generated_indexes = _match_generated_to_final(normalized_generated, normalized_final)
        for idx, case in enumerate(normalized_generated, start=1):
            if (idx - 1) in matched_generated_indexes:
                continue
            reason = _clear_negative_reason(case)
            if not reason:
                continue
            negatives.append(
                _build_negative_sample(
                    case,
                    index=idx,
                    reason=reason,
                    generation_id=generation_id,
                    quality_ledger=ledger_summary,
                )
            )
            if len(negatives) >= _MAX_DERIVED_NEGATIVE_SAMPLES:
                break

    return {
        "positive_samples": positives,
        "negative_samples": negatives,
        "samples": positives + negatives,
        "diagnostics": {
            "generated_case_count": len(normalized_generated),
            "final_case_count": len(normalized_final),
            "positive_candidate_count": len(positive_candidates),
            "positive_sample_count": len(positives),
            "workflow_blueprint_sample_count": 1 if workflow_blueprint_sample is not None else 0,
            "negative_sample_count": len(negatives),
            "manual_business_extension_count": sum(
                1 for item in positives if item.get("manual_business_extension") is True
            ),
            "manual_business_extension_candidate_count": sum(
                1 for item in positive_candidates if item.get("manual_business_extension") is True
            ),
            "positive_aggregation_policy": (
                f"pattern_key_top{_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY}_cap{_MAX_DERIVED_POSITIVE_PATTERNS}"
            ),
            "negative_policy": "ai_only_clear_quality_failure_only",
            "quality_ledger_attached": bool(ledger_summary),
        },
    }


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _first_positive_int(values: Any) -> int | None:
    for raw in (values if isinstance(values, list) else []):
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _workflow_contract_from_learning_sample(sample: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(sample, dict):
        return None
    if _safe_text(sample.get("pattern_grain") or sample.get("patternGrain")).lower() != "workflow_blueprint":
        return None
    blueprint = sample.get("workflow_blueprint") or sample.get("workflowBlueprint")
    if not isinstance(blueprint, dict):
        return None
    steps = [dict(item) for item in (blueprint.get("steps") or []) if isinstance(item, dict)]
    if len(steps) < 2:
        return None
    workflow_id = _safe_text(
        blueprint.get("workflow_id")
        or blueprint.get("id")
        or sample.get("case_id")
        or sample.get("id")
    )
    if not workflow_id:
        return None
    match_terms = [
        _safe_text(blueprint.get("name") or blueprint.get("title")),
        _safe_text(sample.get("pattern_summary") or sample.get("title")),
        _safe_text(sample.get("user_comment")),
    ]
    actors = sorted(
        {
            _safe_text(step.get("actor") or step.get("role"))
            for step in steps
            if _safe_text(step.get("actor") or step.get("role"))
        }
    )
    linked_doc_ids = sample.get("linked_doc_ids") or sample.get("linkedDocIds")
    return {
        **blueprint,
        "id": workflow_id,
        "workflow_id": workflow_id,
        "source_type": "manual_final_case_derived",
        "trusted": True,
        "source_doc_id": _first_positive_int(linked_doc_ids),
        "confidence": sample.get("pattern_confidence") or sample.get("confidence") or 0.8,
        "actors": actors,
        "match_terms": [term for term in match_terms if term],
        "steps": steps,
        "edges": steps,
    }


def _workflow_contract_candidates_from_derived(derived: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sample in (derived.get("samples") or []):
        if not isinstance(sample, dict):
            continue
        contract = _workflow_contract_from_learning_sample(sample)
        if contract is None:
            continue
        key = _safe_text(contract.get("workflow_id")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(contract)
    return candidates


def parse_evaluation_result_payload(raw: Any) -> dict[str, Any]:
    """Parse the quality-evaluation report into a dict.

    The evaluation endpoint may return plain JSON, markdown fenced JSON, or an
    already-parsed object. Keep this parser local so both API and tests share
    the same tolerance as the frontend report renderer.
    """
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if block:
        text = block.group(1).strip()
    first_open = text.find("{")
    last_close = text.rfind("}")
    if first_open >= 0 and last_close > first_open:
        text = text[first_open : last_close + 1]
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_learning_candidates_from_evaluation_result(evaluation_result: Any) -> dict[str, Any]:
    """Convert quality-evaluation defects into user-confirmable learning candidates.

    This does not write anything. The candidate contains the exact sample that
    will later be inserted into the existing priority sample pool if the user
    confirms it.
    """
    payload = parse_evaluation_result_payload(evaluation_result)
    defect = payload.get("defect_analysis") if isinstance(payload.get("defect_analysis"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}

    candidates: list[dict[str, Any]] = []
    raw_candidate_count = 0
    rejected_candidates: list[dict[str, Any]] = []

    def add_candidate(
        *,
        source_field: str,
        item: Any,
        index: int,
        signal_type: str,
        pattern_usage: str,
        pattern_category: str,
        reason_category: str,
        candidate_type: str,
        selected_by_default: bool,
        confidence: float,
    ) -> None:
        nonlocal raw_candidate_count
        raw_candidate_count += 1
        text = _text(item)
        if not text:
            return
        candidate_id = f"{source_field}-{index}"
        gate = _evaluation_learning_candidate_quality_gate(
            text=text,
            source_field=source_field,
            candidate_type=candidate_type,
            signal_type=signal_type,
        )
        if gate["status"] == "rejected":
            rejected_candidates.append(
                {
                    "id": candidate_id,
                    "source_field": source_field,
                    "reason": gate["reason"],
                    "text": text[:160],
                }
            )
            return
        effective_selected = bool(selected_by_default and gate["status"] == "auto_select")
        sample = {
            "signal_type": signal_type,
            "pattern_usage": pattern_usage,
            "pattern_category": pattern_category,
            "reason_category": reason_category,
            "expected_priority": "P1" if signal_type == "positive" else "P2",
            "case_id": candidate_id,
            "title": text[:120],
            "user_comment": text[:240],
            "pattern_summary": _summarize_evaluation_defect_pattern(
                text=text,
                signal_type=signal_type,
                pattern_category=pattern_category,
            ),
            "pattern_grain": "pattern" if signal_type == "positive" else "anti_pattern",
            "source": "quality_evaluation_defect",
            "source_type": "quality_evaluation_defect",
            "source_id": None,
            "source_case_id": str(candidate_id),
            "learning_signal_source": f"defect_analysis.{source_field}",
            "pattern_scope": "project",
            "pattern_confidence": round(max(0.35, min(0.9, confidence)), 4),
            "evaluation_metrics": _compact_evaluation_metrics(metrics),
            "quality_gate_status": gate["status"],
            "quality_gate_reason": gate["reason"],
            "quality_gate_policy": _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
        }
        candidates.append(
            {
                "id": candidate_id,
                "candidate_type": candidate_type,
                "target": "priority_sample_pool",
                "source_field": source_field,
                "text": text,
                "selected_by_default": effective_selected,
                "confidence": sample["pattern_confidence"],
                "quality_gate_status": gate["status"],
                "quality_gate_reason": gate["reason"],
                "sample": sample,
            }
        )

    for idx, item in enumerate(_as_text_list(defect.get("missing_points")), start=1):
        generated_only = _is_generated_only_evaluation_defect(item)
        redundant_or_overgenerated = _is_redundant_or_overgenerated_evaluation_defect(item)
        if generated_only or redundant_or_overgenerated:
            add_candidate(
                source_field="missing_points",
                item=item,
                index=idx,
                signal_type="negative",
                pattern_usage="avoid",
                pattern_category="hallucination_or_redundant_case",
                reason_category=(
                    "generated_only_defect_misfiled_as_missing"
                    if generated_only
                    else "redundant_defect_misfiled_as_missing"
                ),
                candidate_type="negative_pattern",
                selected_by_default=False,
                confidence=_confidence_from_metrics(metrics, base=0.62, metric_name="precision", inverse=True),
            )
            continue
        add_candidate(
            source_field="missing_points",
            item=item,
            index=idx,
            signal_type="positive",
            pattern_usage="prefer",
            pattern_category="recall_gap_missing_business_coverage",
            reason_category="recall_gap",
            candidate_type="positive_pattern",
            selected_by_default=True,
            confidence=_confidence_from_metrics(metrics, base=0.72, metric_name="recall", inverse=True),
        )
    for idx, item in enumerate(_as_text_list(defect.get("modifications")), start=1):
        generated_only = _is_generated_only_evaluation_defect(item)
        redundant_or_overgenerated = _is_redundant_or_overgenerated_evaluation_defect(item)
        if generated_only or redundant_or_overgenerated:
            add_candidate(
                source_field="modifications",
                item=item,
                index=idx,
                signal_type="negative",
                pattern_usage="avoid",
                pattern_category="hallucination_or_redundant_case",
                reason_category=(
                    "generated_only_defect_misfiled_as_modification"
                    if generated_only
                    else "redundant_defect_misfiled_as_modification"
                ),
                candidate_type="negative_pattern",
                selected_by_default=False,
                confidence=_confidence_from_metrics(metrics, base=0.62, metric_name="precision", inverse=True),
            )
            continue
        add_candidate(
            source_field="modifications",
            item=item,
            index=idx,
            signal_type="positive",
            pattern_usage="prefer",
            pattern_category="quality_fix_hint",
            reason_category="quality_fix_hint",
            candidate_type="quality_fix_hint",
            selected_by_default=True,
            confidence=_confidence_from_metrics(metrics, base=0.68, metric_name="semantic_similarity", inverse=False),
        )
    for idx, item in enumerate(_as_text_list(defect.get("hallucinations")), start=1):
        add_candidate(
            source_field="hallucinations",
            item=item,
            index=idx,
            signal_type="negative",
            pattern_usage="avoid",
            pattern_category="hallucination_or_redundant_case",
            reason_category="hallucination_or_redundant_case",
            candidate_type="negative_pattern",
            selected_by_default=False,
            confidence=_confidence_from_metrics(metrics, base=0.6, metric_name="precision", inverse=True),
        )

    candidates = _aggregate_evaluation_learning_candidates(candidates)
    candidates = candidates[:_MAX_EVALUATION_LEARNING_CANDIDATES]
    return {
        "candidates": candidates,
        "diagnostics": {
            "raw_candidate_count": raw_candidate_count,
            "quality_gate_rejected_count": len(rejected_candidates),
            "quality_gate_review_required_count": sum(
                1 for item in candidates if item.get("quality_gate_status") == "review_required"
            ),
            "quality_gate_rejected_examples": rejected_candidates[:8],
            "candidate_count": len(candidates),
            "selected_by_default_count": sum(1 for item in candidates if item.get("selected_by_default") is True),
            "missing_points_count": len(_as_text_list(defect.get("missing_points"))),
            "modifications_count": len(_as_text_list(defect.get("modifications"))),
            "hallucinations_count": len(_as_text_list(defect.get("hallucinations"))),
            "candidate_aggregation_policy": (
                "defect_field_semantic_bucket_positive8_fix6_negative3_generated_redundant_quality_gate"
            ),
            "candidate_quality_policy": _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
            "target": "priority_sample_pool",
            "write_policy": "user_confirmed_only",
        },
    }


class FinalCaseLearningService:
    """Service for writing final-case learning signals into the existing pool."""

    def __init__(self, db):
        self._db = db
        self.history_repo = TestGenerationHistoryRepository(db)
        self.knowledge_repo = KnowledgeDocumentRepository(db)

    def _upsert_workflow_contracts_from_derived(
        self,
        *,
        project_id: int,
        user_id: int,
        derived: dict[str, Any],
    ) -> dict[str, Any]:
        candidates = _workflow_contract_candidates_from_derived(derived if isinstance(derived, dict) else {})
        if not candidates:
            return {
                "candidate_count": 0,
                "upserted_count": 0,
                "doc_ids": [],
                "errors": [],
            }
        repo = WorkflowBlueprintRepository(self._db)
        doc_ids: list[int] = []
        errors: list[dict[str, Any]] = []
        for contract in candidates:
            try:
                doc = repo.upsert_contract(
                    project_id=int(project_id),
                    user_id=int(user_id),
                    contract=contract,
                )
                if getattr(doc, "id", None) is not None:
                    doc_ids.append(int(doc.id))
            except Exception as exc:
                errors.append(
                    {
                        "workflow_id": _safe_text(contract.get("workflow_id")),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return {
            "candidate_count": int(len(candidates)),
            "upserted_count": int(len(doc_ids)),
            "doc_ids": doc_ids,
            "errors": errors,
        }

    def learn_from_case_pair(
        self,
        *,
        project_id: int,
        user_id: int,
        generated_cases: Any,
        final_cases: Any,
        generation_id: int | None = None,
        include_negative_samples: bool = True,
        dry_run: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.history_repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        normalized_generated = parse_test_cases_payload(generated_cases)
        normalized_final = parse_test_cases_payload(final_cases)
        if not normalized_final:
            return (
                "no_final_cases",
                {
                    "project_id": project_id,
                    "samples": [],
                    "diagnostics": {
                        "generated_case_count": len(normalized_generated),
                        "final_case_count": 0,
                    },
                },
            )

        requirement_text = ""
        ledger: dict[str, Any] = {}
        effective_generation_id = generation_id
        if generation_id:
            entry = self.history_repo.get_generation(generation_id=int(generation_id))
            if not entry or int(getattr(entry, "project_id", 0) or 0) != int(project_id):
                return "generation_not_found", None
            requirement_text = getattr(entry, "requirement_text", "") or ""
            ledger = self._find_quality_ledger(entry)
            if not normalized_generated:
                normalized_generated = parse_test_cases_payload(getattr(entry, "generated_result", None))

        derived = build_learning_samples_from_final_cases(
            generated_cases=normalized_generated,
            final_cases=normalized_final,
            requirement_text=requirement_text,
            generation_id=effective_generation_id,
            linked_doc_ids=[],
            include_negative_samples=include_negative_samples,
            quality_ledger=ledger,
        )
        if dry_run:
            return (
                "ok",
                {
                    "project_id": project_id,
                    "artifact_doc_id": None,
                    "derived": derived,
                    "sample_pool_count": None,
                    "updated_at": None,
                    "dry_run": True,
                },
            )

        existing_payload = (
            load_priority_sample_pool(
                db=self._db,
                project_id=project_id,
                user_id=user_id,
            )
            or {}
        )
        existing_samples = existing_payload.get("samples") if isinstance(existing_payload.get("samples"), list) else []
        merged_samples = (existing_samples or []) + derived["samples"]
        if len(merged_samples) > _MAX_POOL_SAMPLES:
            merged_samples = merged_samples[-_MAX_POOL_SAMPLES:]

        doc = upsert_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=None,
            samples=merged_samples,
        )
        payload = (
            load_priority_sample_pool(
                db=self._db,
                project_id=project_id,
                user_id=user_id,
            )
            or {}
        )
        normalized_samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        workflow_contracts = self._upsert_workflow_contracts_from_derived(
            project_id=project_id,
            user_id=user_id,
            derived=derived,
        )
        return (
            "ok",
            {
                "project_id": project_id,
                "artifact_doc_id": doc.id,
                "derived": derived,
                "workflow_contracts": workflow_contracts,
                "sample_pool_count": len(normalized_samples),
                "updated_at": payload.get("updated_at"),
                "dry_run": False,
            },
        )

    def build_learning_candidates_from_evaluation(
        self,
        *,
        project_id: int,
        user_id: int,
        evaluation_result: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.history_repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None
        derived = build_learning_candidates_from_evaluation_result(evaluation_result)
        return (
            "ok",
            {
                "project_id": project_id,
                **derived,
            },
        )

    def apply_learning_candidates(
        self,
        *,
        project_id: int,
        user_id: int,
        candidates: list[dict[str, Any]],
        dry_run: bool = True,
    ) -> tuple[str, dict[str, Any] | None]:
        if not self.history_repo.get_owned_project(project_id=project_id, user_id=user_id):
            return "project_not_found", None

        candidate_items = candidates if isinstance(candidates, list) else []
        samples: list[dict[str, Any]] = []
        rejected_sample_count = 0
        rejected_sample_examples: list[dict[str, Any]] = []
        for candidate in candidate_items:
            if not isinstance(candidate, dict):
                continue
            sample = candidate.get("sample")
            normalized_sample: dict[str, Any] | None = None
            if isinstance(sample, dict):
                normalized_sample = dict(sample)
            elif _candidate_has_sample_shape(candidate):
                normalized_sample = dict(candidate)
            if normalized_sample is None:
                continue
            gated_sample = _filter_quality_evaluation_sample_for_apply(normalized_sample)
            if gated_sample is None:
                rejected_sample_count += 1
                rejected_sample_examples.append(
                    {
                        "id": normalized_sample.get("case_id") or normalized_sample.get("id"),
                        "text": _text(
                            normalized_sample.get("user_comment")
                            or normalized_sample.get("title")
                            or normalized_sample.get("pattern_summary")
                        )[:160],
                    }
                )
                continue
            samples.append(gated_sample)
        samples = samples[:_MAX_EVALUATION_LEARNING_CANDIDATES]
        derived = {
            "samples": samples,
            "diagnostics": {
                "candidate_count": len(candidate_items),
                "sample_count": len(samples),
                "rejected_sample_count": rejected_sample_count,
                "rejected_sample_examples": rejected_sample_examples[:8],
                "positive_sample_count": sum(1 for item in samples if str(item.get("signal_type") or "") == "positive"),
                "negative_sample_count": sum(1 for item in samples if str(item.get("signal_type") or "") == "negative"),
                "target": "priority_sample_pool",
                "source": "quality_evaluation_defect",
                "candidate_quality_policy": _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY,
            },
        }
        if dry_run:
            return (
                "ok",
                {
                    "project_id": project_id,
                    "artifact_doc_id": None,
                    "derived": derived,
                    "sample_pool_count": None,
                    "updated_at": None,
                    "dry_run": True,
                },
            )

        now_iso = datetime.utcnow().isoformat()
        accepted_candidate_ids: list[str] = []
        for sample in samples:
            sample["learning_status"] = "user_confirmed"
            sample["learning_confirmed_at"] = now_iso
            sample["learning_confirmed_by"] = int(user_id)
            cid = sample.get("case_id") or sample.get("id")
            if cid:
                accepted_candidate_ids.append(str(cid))

        existing_payload = (
            load_priority_sample_pool(
                db=self._db,
                project_id=project_id,
                user_id=user_id,
            )
            or {}
        )
        existing_samples = existing_payload.get("samples") if isinstance(existing_payload.get("samples"), list) else []
        merged_samples = (existing_samples or []) + samples
        if len(merged_samples) > _MAX_POOL_SAMPLES:
            merged_samples = merged_samples[-_MAX_POOL_SAMPLES:]

        doc = upsert_priority_sample_pool(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            generation_id=None,
            samples=merged_samples,
        )
        append_learning_event(
            db=self._db,
            project_id=project_id,
            user_id=user_id,
            event_type="quality_evaluation_candidates_applied",
            event_payload={
                "candidate_count": len(candidate_items),
                "accepted_count": len(samples),
                "accepted_candidate_ids": accepted_candidate_ids,
                "source": "quality_evaluation_defect",
            },
        )
        payload = (
            load_priority_sample_pool(
                db=self._db,
                project_id=project_id,
                user_id=user_id,
            )
            or {}
        )
        normalized_samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        return (
            "ok",
            {
                "project_id": project_id,
                "artifact_doc_id": doc.id,
                "derived": derived,
                "sample_pool_count": len(normalized_samples),
                "updated_at": payload.get("updated_at"),
                "dry_run": False,
            },
        )

    def learn_from_generation_final_cases(
        self,
        *,
        generation_id: int,
        user_id: int,
        final_cases: list[dict[str, Any]] | None = None,
        final_case_doc_ids: list[int] | None = None,
        source_doc_ids: list[int] | None = None,
        include_linked_docs: bool = True,
        include_negative_samples: bool = True,
        dry_run: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        entry = self.history_repo.get_generation(generation_id=generation_id)
        if not entry:
            return "not_found", None
        if entry.project_id is None:
            if entry.user_id != user_id:
                return "not_found", None
            return "project_not_found", None
        if not self.history_repo.get_owned_project(project_id=entry.project_id, user_id=user_id):
            return "not_found", None

        generated_cases = parse_test_cases_payload(entry.generated_result)
        linked_docs = []
        if final_case_doc_ids:
            linked_docs = [
                doc
                for doc in self.knowledge_repo.list_project_docs_by_ids(
                    project_id=entry.project_id,
                    doc_ids=final_case_doc_ids,
                )
                if doc.doc_type == "test_case"
            ]
        elif source_doc_ids:
            linked_docs = self.knowledge_repo.list_linked_test_cases_for_sources(
                project_id=entry.project_id,
                source_doc_ids=source_doc_ids,
            )
        elif include_linked_docs:
            linked_docs = self._find_linked_final_case_docs(entry)

        linked_doc_cases: list[dict[str, Any]] = []
        for doc in linked_docs:
            linked_doc_cases.extend(parse_test_cases_payload(doc.content))

        effective_final_cases = []
        if final_cases:
            effective_final_cases.extend(parse_test_cases_payload(final_cases))
        effective_final_cases.extend(linked_doc_cases)
        if not effective_final_cases:
            return (
                "no_final_cases",
                {
                    "generation_id": generation_id,
                    "project_id": entry.project_id,
                    "linked_doc_ids": [doc.id for doc in linked_docs],
                    "samples": [],
                    "diagnostics": {
                        "generated_case_count": len(generated_cases),
                        "final_case_count": 0,
                    },
                },
            )

        derived = build_learning_samples_from_final_cases(
            generated_cases=generated_cases,
            final_cases=effective_final_cases,
            requirement_text=entry.requirement_text or "",
            generation_id=generation_id,
            linked_doc_ids=[int(doc.id) for doc in linked_docs if doc.id is not None],
            include_negative_samples=include_negative_samples,
            quality_ledger=self._find_quality_ledger(entry),
        )
        if dry_run:
            return (
                "ok",
                {
                    "project_id": entry.project_id,
                    "generation_id": generation_id,
                    "artifact_doc_id": None,
                    "linked_doc_ids": [doc.id for doc in linked_docs],
                    "derived": derived,
                    "sample_pool_count": None,
                    "updated_at": None,
                    "dry_run": True,
                },
            )
        existing_payload = (
            load_priority_sample_pool(
                db=self._db,
                project_id=entry.project_id,
                user_id=user_id,
            )
            or {}
        )
        existing_samples = existing_payload.get("samples") if isinstance(existing_payload.get("samples"), list) else []
        merged_samples = (existing_samples or []) + derived["samples"]
        if len(merged_samples) > _MAX_POOL_SAMPLES:
            merged_samples = merged_samples[-_MAX_POOL_SAMPLES:]

        doc = upsert_priority_sample_pool(
            db=self._db,
            project_id=entry.project_id,
            user_id=user_id,
            generation_id=generation_id,
            samples=merged_samples,
        )
        payload = (
            load_priority_sample_pool(
                db=self._db,
                project_id=entry.project_id,
                user_id=user_id,
            )
            or {}
        )
        normalized_samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
        workflow_contracts = self._upsert_workflow_contracts_from_derived(
            project_id=entry.project_id,
            user_id=user_id,
            derived=derived,
        )
        return (
            "ok",
            {
                "project_id": entry.project_id,
                "generation_id": generation_id,
                "artifact_doc_id": doc.id,
                "linked_doc_ids": [doc.id for doc in linked_docs],
                "derived": derived,
                "workflow_contracts": workflow_contracts,
                "sample_pool_count": len(normalized_samples),
                "updated_at": payload.get("updated_at"),
            },
        )

    def _find_linked_final_case_docs(self, entry: Any) -> list[Any]:
        if entry.project_id is None:
            return []
        candidates = self.knowledge_repo.list_project_docs_created_desc(
            project_id=entry.project_id,
            limit=200,
        )
        requirement_fingerprint = _fingerprint(entry.requirement_text or "")
        source_ids = [
            int(doc.id)
            for doc in candidates
            if doc.doc_type == "requirement"
            and doc.id is not None
            and _fingerprint(doc.content or "") == requirement_fingerprint
        ]
        return self.knowledge_repo.list_linked_test_cases_for_sources(
            project_id=entry.project_id,
            source_doc_ids=source_ids,
        )

    def _find_quality_ledger(self, entry: Any) -> dict[str, Any]:
        if getattr(entry, "project_id", None) is None or getattr(entry, "id", None) is None:
            return {}
        try:
            rows = (
                self._db.query(LogEntry)
                .filter(LogEntry.project_id == int(entry.project_id))
                .filter(LogEntry.message.like("%GEN_DIAG:%generation_quality_ledger%"))
                .order_by(LogEntry.id.desc())
                .limit(40)
                .all()
            )
            for row in rows:
                payload = _parse_gen_diag_payload(getattr(row, "message", ""))
                if int(payload.get("generation_id") or 0) == int(entry.id):
                    return payload
        except Exception:
            return {}
        return {}


def _parse_json_cases(text: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    return parse_test_cases_payload(parsed)


def _parse_csv_cases(text: str) -> list[dict[str, Any]]:
    try:
        reader = csv.DictReader(StringIO(text))
        rows = [dict(row) for row in reader if row]
    except Exception:
        return []
    if not rows:
        return []
    return [_normalize_case_dict(row) for row in rows if any(str(v or "").strip() for v in row.values())]


def _parse_html_table_cases(text: str) -> list[dict[str, Any]]:
    if "<table" not in text.lower():
        return []
    try:
        import pandas as pd

        tables = pd.read_html(StringIO(text))
    except Exception:
        return []

    parsed_cases: list[dict[str, Any]] = []
    header_markers = _case_table_header_markers()
    for table in tables:
        rows = table.fillna("").astype(str).values.tolist()
        parsed_cases.extend(_parse_case_table_rows(rows, header_markers=header_markers))
    return parsed_cases


def _case_table_header_markers() -> set[str]:
    return {
        "用例标题",
        "用例名称",
        "测试用例",
        "测试点",
        "测试模块",
        "执行步骤",
        "操作步骤",
        "预期结果",
        "期望结果",
        "用例级别",
    }


def _parse_case_table_rows(rows: list[list[Any]], *, header_markers: set[str] | None = None) -> list[dict[str, Any]]:
    markers = header_markers or _case_table_header_markers()
    header_index = -1
    for idx, row in enumerate(rows):
        non_empty = {_text(cell).strip() for cell in row if _text(cell).strip()}
        if len(non_empty & markers) >= 2:
            header_index = idx
            break
    if header_index < 0:
        return []

    headers = [_text(cell).strip() for cell in rows[header_index]]
    parsed_cases: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        raw: dict[str, Any] = {}
        for header, value in zip(headers, row):
            key = _text(header).strip()
            cell_value = _text(value).strip()
            if not key or key.lower().startswith("unnamed") or not cell_value:
                continue
            raw[key] = cell_value
        normalized = _normalize_case_dict(raw)
        if _text(normalized.get("description")) or _text(normalized.get("expected_result")):
            parsed_cases.append(normalized)
    return parsed_cases


def _as_text_list(raw: Any) -> list[str]:
    return [text for text in (_text(item) for item in case_text_list_value(raw)) if text]


def _compact_evaluation_metrics(metrics: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(metrics, dict):
        return {}
    result: dict[str, float] = {}
    for key in ("precision", "recall", "f1_score", "semantic_similarity"):
        try:
            result[key] = round(float(metrics.get(key)), 4)
        except Exception:
            continue
    return result


def _confidence_from_metrics(
    metrics: dict[str, Any] | None,
    *,
    base: float,
    metric_name: str,
    inverse: bool,
) -> float:
    value = None
    if isinstance(metrics, dict):
        try:
            value = float(metrics.get(metric_name))
        except Exception:
            value = None
    if value is None:
        return round(base, 4)
    value = max(0.0, min(1.0, value))
    if inverse:
        return round(base + ((1.0 - value) * 0.12), 4)
    return round(base + (value * 0.08), 4)


def _summarize_evaluation_defect_pattern(
    *,
    text: str,
    signal_type: str,
    pattern_category: str,
) -> str:
    prefix = "prefer" if signal_type == "positive" else "avoid"
    return f"{prefix} | {pattern_category} | {_text(text)[:140]}"[:180]


def _is_generated_only_evaluation_defect(raw: Any) -> bool:
    text = _text(raw)
    if not text:
        return False
    generated_side_tokens = (
        "生成用例",
        "原生成",
        "AI 生成",
        "AI生成",
        "generated",
    )
    final_absent_tokens = (
        "修改用例未涉及",
        "修改用例未覆盖",
        "修改用例不存在",
        "修改用例完全不存在",
        "修改版本未涉及",
        "修改版本未覆盖",
        "修改版本中未体现",
        "修改后未涉及",
        "在修改用例中不存在",
        "在修改用例中未体现",
        "modified version does not",
        "absent from modified",
    )
    generated_excess_tokens = (
        "生成用例包含大量",
        "生成用例中新增了大量",
        "生成用例新增了大量",
        "生成用例额外包含",
        "generated contains many",
        "generated adds many",
    )
    lowered = text.lower()
    has_generated_side = any(token.lower() in lowered for token in generated_side_tokens)
    if not has_generated_side:
        return False
    return any(token.lower() in lowered for token in final_absent_tokens + generated_excess_tokens)


def _is_redundant_or_overgenerated_evaluation_defect(raw: Any) -> bool:
    text = _text(raw)
    if not text:
        return False
    lowered = text.lower()
    generated_tokens = ("ai", "生成", "generated")
    redundant_tokens = (
        "duplicate_redundant",
        "重复",
        "冗余",
        "合并",
        "过多",
        "大量",
        "多个",
        "未被人工采用",
        "not adopted",
        "redundant",
        "duplicate",
        "merged",
    )
    return any(token in lowered for token in generated_tokens) and any(
        token in lowered for token in redundant_tokens
    )


def _evaluation_learning_candidate_quality_gate(
    *,
    text: str,
    source_field: str,
    candidate_type: str,
    signal_type: str,
) -> dict[str, str]:
    normalized = _text(text)
    if not normalized:
        return {"status": "rejected", "reason": "empty_text"}

    compact_len = len(re.sub(r"\s+", "", normalized))
    has_case_id = _has_case_identifier(normalized)
    if _is_case_identifier_only_learning_text(normalized):
        return {"status": "rejected", "reason": "case_identifier_label_only"}
    if _is_direct_case_rewrite_note(normalized):
        return {"status": "rejected", "reason": "case_identifier_rewrite_note"}

    context_score = _learning_candidate_context_score(normalized)
    has_defect_or_compare_signal = _has_evaluation_defect_or_compare_signal(normalized)
    has_final_side_anchor = _has_final_side_learning_anchor(normalized)
    has_generated_side_anchor = _has_generated_side_learning_anchor(normalized)
    is_negative = signal_type == "negative" or candidate_type == "negative_pattern" or source_field == "hallucinations"

    if is_negative:
        if has_case_id and context_score < 8:
            return {"status": "rejected", "reason": "case_identifier_without_negative_context"}
        if compact_len < 12 or (context_score < 6 and not has_defect_or_compare_signal):
            return {"status": "rejected", "reason": "low_context_negative_pattern"}
        return {"status": "review_required", "reason": "negative_patterns_require_confirmation"}

    if _is_process_count_learning_note(normalized):
        return {"status": "rejected", "reason": "process_count_note_not_reusable_pattern"}
    if compact_len < 18 and not has_final_side_anchor:
        return {"status": "rejected", "reason": "low_context_positive_pattern"}
    if context_score < 8 and not has_final_side_anchor:
        return {"status": "rejected", "reason": "not_enough_reusable_context"}
    if not has_defect_or_compare_signal and compact_len < 24:
        return {"status": "rejected", "reason": "missing_defect_or_comparison_signal"}
    if _is_ai_to_human_process_note(normalized):
        return {"status": "review_required", "reason": "ai_human_process_note_requires_review"}
    if has_final_side_anchor and not has_generated_side_anchor and compact_len < 24:
        return {"status": "review_required", "reason": "compact_final_side_pattern_requires_review"}
    if not has_final_side_anchor and compact_len < 24:
        return {"status": "review_required", "reason": "compact_positive_pattern_requires_review"}
    return {"status": "auto_select", "reason": "reusable_evaluation_pattern"}


def _has_case_identifier(text: str) -> bool:
    return bool(re.search(r"(?:TC|CASE)[-\s]?\d+", text, flags=re.IGNORECASE))


def _strip_case_identifiers(text: str) -> str:
    return re.sub(r"(?:TC|CASE)[-\s]?\d+", "", text, flags=re.IGNORECASE)


def _is_case_identifier_only_learning_text(text: str) -> bool:
    without_ids = _strip_case_identifiers(text)
    without_ids = re.sub(r"\bAI\b", "", without_ids, flags=re.IGNORECASE)
    without_ids = re.sub(r"group\d+", "", without_ids, flags=re.IGNORECASE)
    without_ids = re.sub(
        r"(修正|修改|调整|对应|匹配|判断|逻辑|功能|验证|测试|题目|场景|页面|模块|用例|类问题聚合|代表例|合并|和|在|中|；|;|:|：|、|，|,|\s)+",
        "",
        without_ids,
    )
    return _has_case_identifier(text) and len(without_ids) < 8


def _is_direct_case_rewrite_note(text: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:TC|CASE)[-\s]?\d+\s*(?:修正|修改|调整为|对应|合并到?)\s*(?:TC|CASE)[-\s]?\d+\s*$",
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_process_count_learning_note(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.search(r"AI生成\d+个用例.*人工(?:修改|合并|拆分)为\d+个", compact, flags=re.IGNORECASE)
        or re.search(r"人工将多个AI用例(?:合并|修改)为", compact, flags=re.IGNORECASE)
    )


def _is_ai_to_human_process_note(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.search(r"AI(?:的|用例|生成)?.{0,16}人工(?:修改|补充|拆分|修正)", compact, flags=re.IGNORECASE)
        or re.search(r"人工(?:修改|补充|拆分|修正).{0,16}AI", compact, flags=re.IGNORECASE)
    )


def _has_final_side_learning_anchor(text: str) -> bool:
    lowered = text.lower()
    tokens = (
        "修改版",
        "修改用例",
        "人工版",
        "人工用例",
        "人工最终",
        "最终用例",
        "modified",
        "human",
        "final case",
    )
    return any(token in lowered for token in tokens)


def _has_generated_side_learning_anchor(text: str) -> bool:
    lowered = text.lower()
    tokens = (
        "生成版",
        "生成用例",
        "原生成",
        "ai",
        "generated",
    )
    return any(token in lowered for token in tokens)


def _has_evaluation_defect_or_compare_signal(text: str) -> bool:
    lowered = text.lower()
    tokens = (
        "缺失",
        "缺少",
        "遗漏",
        "未包含",
        "未覆盖",
        "未涉及",
        "未提及",
        "无对应",
        "不完全对应",
        "无关",
        "多余",
        "冗余",
        "重复",
        "合并",
        "修正",
        "修改",
        "补充",
        "变更",
        "改为",
        "更具体",
        "过于笼统",
        "缺乏",
        "missing",
        "lacks",
        "lack",
        "should",
        "assert",
        "not generic",
        "unrelated",
        "redundant",
        "duplicate",
        "modified",
        "generated",
    )
    return any(token in lowered for token in tokens)


def _learning_candidate_context_score(text: str) -> int:
    cleaned = _strip_case_identifiers(text)
    cleaned = re.sub(r"\b(?:AI|TC|CASE|generated|modified|human|final|case|expected|result|should|assert)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"(生成版|修改版|人工|生成|用例|测试|验证|缺失|缺少|遗漏|未包含|未覆盖|未涉及|未提及|无对应|修正|修改|补充|变更|改为|更具体|过于笼统|缺乏|功能|场景|逻辑|条件|具体|精确|页面|模块|类问题聚合|代表例|个|为|从)",
        "",
        cleaned,
    )
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", cleaned)
    english_words = re.findall(r"[A-Za-z]{3,}", cleaned)
    return len(chinese_chars) + len(english_words) * 4


def _aggregate_evaluation_learning_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = _evaluation_candidate_key(candidate)
        buckets.setdefault(key, []).append(candidate)

    selected: list[dict[str, Any]] = []
    field_counts: dict[str, int] = {}
    for _key, bucket in sorted(buckets.items(), key=lambda item: _evaluation_candidate_bucket_rank(item[1])):
        candidate = _merge_evaluation_candidate_bucket(bucket)
        source_field = str(candidate.get("source_field") or "")
        limit = _evaluation_candidate_field_limit(source_field, candidate)
        if field_counts.get(source_field, 0) >= limit:
            continue
        field_counts[source_field] = field_counts.get(source_field, 0) + 1
        selected.append(candidate)
    return selected


def _evaluation_candidate_key(candidate: dict[str, Any]) -> str:
    source_field = str(candidate.get("source_field") or "")
    candidate_type = str(candidate.get("candidate_type") or "")
    text = _text(candidate.get("text"))
    return "|".join(
        [
            source_field,
            candidate_type,
            _semantic_bucket_for_learning_text(text),
        ]
    )


def _semantic_bucket_for_learning_text(text: str) -> str:
    normalized = text.lower()
    token_groups = [
        ("schedule_time", ("排课", "课程时间", "时间区间", "顺延", "课程延期", "节假日", "时间冲突", "schedule")),
        ("learning_plan", ("学习计划", "计划页", "卡片", "周列表", "学习中", "复习", "计划")),
        ("course_status", ("课程状态", "已完成", "未完成", "进度", "归档", "下架", "状态")),
        ("navigation_flow", ("跳转", "进入", "返回", "下一步", "页面流转", "入口")),
        ("teacher_admin", ("督导", "老师", "书房", "中房端", "后台", "管理端", "ta", "ops")),
        ("ui_copy", ("文案", "提示", "按钮", "标题", "标签", "弹窗", "显示")),
        ("duplicate_redundant", ("重复", "相似", "合并", "大量", "冗余")),
        ("buried_point", ("埋点", "pv", "uv", "上报")),
    ]
    for name, tokens in token_groups:
        if any(token in normalized for token in tokens):
            return name
    compact = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text.lower())
    return compact[:24] or "general"


def _evaluation_candidate_field_limit(source_field: str, candidate: dict[str, Any]) -> int:
    if source_field == "hallucinations" or str(candidate.get("candidate_type") or "") == "negative_pattern":
        return _MAX_EVALUATION_NEGATIVE_CANDIDATES_PER_FIELD
    if source_field == "modifications":
        return _MAX_EVALUATION_FIX_CANDIDATES_PER_FIELD
    return _MAX_EVALUATION_POSITIVE_CANDIDATES_PER_FIELD


def _evaluation_candidate_bucket_rank(bucket: list[dict[str, Any]]) -> tuple[int, float, str]:
    first = bucket[0] if bucket else {}
    candidate_type = str(first.get("candidate_type") or "")
    type_rank = {
        "positive_pattern": 0,
        "quality_fix_hint": 1,
        "negative_pattern": 2,
    }.get(candidate_type, 3)
    confidence = float(first.get("confidence") or 0.0)
    return (type_rank, -confidence, _evaluation_candidate_key(first))


def _merge_evaluation_candidate_bucket(bucket: list[dict[str, Any]]) -> dict[str, Any]:
    if not bucket:
        return {}
    base = dict(bucket[0])
    if len(bucket) <= 1:
        return base
    texts = [_text(item.get("text")) for item in bucket if _text(item.get("text"))]
    summary = _summarize_candidate_texts(texts)
    selected_by_default = any(item.get("selected_by_default") is True for item in bucket)
    gate_statuses = {str(item.get("quality_gate_status") or "") for item in bucket}
    gate_status = "auto_select" if selected_by_default else "review_required"
    if "auto_select" not in gate_statuses and "review_required" in gate_statuses:
        gate_status = "review_required"
    base["text"] = summary
    base["id"] = f"{base.get('source_field')}-{_semantic_bucket_for_learning_text(summary)}"
    base["confidence"] = round(max(float(item.get("confidence") or 0.0) for item in bucket), 4)
    base["selected_by_default"] = selected_by_default
    base["quality_gate_status"] = gate_status
    base["quality_gate_reason"] = "aggregated_reusable_evidence" if selected_by_default else "aggregated_review_required"
    sample = dict(base.get("sample") or {})
    sample["case_id"] = str(base["id"])
    sample["title"] = summary[:120]
    sample["user_comment"] = summary[:240]
    sample["pattern_summary"] = _summarize_evaluation_defect_pattern(
        text=summary,
        signal_type=str(sample.get("signal_type") or "positive"),
        pattern_category=str(sample.get("pattern_category") or base.get("candidate_type") or "evaluation_defect"),
    )
    sample["pattern_confidence"] = base["confidence"]
    sample["aggregated_evidence_count"] = len(bucket)
    sample["aggregated_evidence_examples"] = texts[:5]
    sample["quality_gate_status"] = gate_status
    sample["quality_gate_reason"] = str(base["quality_gate_reason"])
    sample["quality_gate_policy"] = _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY
    base["sample"] = sample
    base["aggregated_count"] = len(bucket)
    return base


def _summarize_candidate_texts(texts: list[str]) -> str:
    if not texts:
        return ""
    if len(texts) == 1:
        return texts[0]
    first = texts[0]
    bucket = _semantic_bucket_for_learning_text(first)
    examples = "；".join(text[:60] for text in texts[:3])
    return f"{bucket} 类问题聚合：{len(texts)} 条相似缺陷，代表例：{examples}"[:240]


def _filter_quality_evaluation_sample_for_apply(sample: dict[str, Any]) -> dict[str, Any] | None:
    source_type = str(sample.get("source_type") or sample.get("source") or "")
    if source_type != "quality_evaluation_defect":
        return sample
    text = _text(sample.get("user_comment") or sample.get("title") or sample.get("pattern_summary"))
    signal_type = str(sample.get("signal_type") or sample.get("sample_kind") or "")
    pattern_category = str(sample.get("pattern_category") or "")
    candidate_type = "negative_pattern" if signal_type == "negative" else "positive_pattern"
    if pattern_category == "quality_fix_hint":
        candidate_type = "quality_fix_hint"
    source_field = str(sample.get("learning_signal_source") or "")
    if "." in source_field:
        source_field = source_field.rsplit(".", 1)[-1]
    if not source_field:
        source_field = "hallucinations" if signal_type == "negative" else "missing_points"
    gate = _evaluation_learning_candidate_quality_gate(
        text=text,
        source_field=source_field,
        candidate_type=candidate_type,
        signal_type=signal_type,
    )
    if gate["status"] == "rejected":
        return None
    result = dict(sample)
    result.setdefault("quality_gate_status", gate["status"])
    result.setdefault("quality_gate_reason", gate["reason"])
    result.setdefault("quality_gate_policy", _EVALUATION_LEARNING_CANDIDATE_QUALITY_POLICY)
    return result


def _candidate_has_sample_shape(candidate: dict[str, Any]) -> bool:
    if not isinstance(candidate, dict):
        return False
    return bool(candidate.get("signal_type") and candidate.get("pattern_usage") and candidate.get("pattern_summary"))


def _normalize_case_dict(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for canonical in _CASE_FIELDS:
        value = case_value(item, canonical, None)
        if value not in (None, ""):
            result[canonical] = value
    for key, value in item.items():
        if key not in result and key not in _CASE_FIELD_ALIAS_KEYS:
            result[key] = value
    return result


def _text(raw: Any) -> str:
    if isinstance(raw, list):
        return " ".join(_text(item) for item in raw)
    if isinstance(raw, dict):
        return " ".join(_text(value) for value in raw.values())
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def _case_text(case: dict[str, Any]) -> str:
    return " ".join(
        case_text_parts(
            case,
            ("description", "test_module", "preconditions", "steps", "test_input", "expected_result"),
            dedupe=False,
        )
    ).strip()


def _fingerprint(raw: str) -> str:
    return re.sub(r"\s+", "", str(raw or "").lower())[:5000]


def _case_signature(case: dict[str, Any]) -> str:
    text = _case_text(case).lower()
    text = re.sub(r"tc[-_ ]?\d+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:1200]


def _case_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_sig = _case_signature(left)
    right_sig = _case_signature(right)
    if not left_sig or not right_sig:
        return 0.0
    return SequenceMatcher(None, left_sig, right_sig).ratio()


def _match_generated_to_final(generated_cases: list[dict[str, Any]], final_cases: list[dict[str, Any]]) -> set[int]:
    matched: set[int] = set()
    for gen_idx, generated in enumerate(generated_cases):
        best = 0.0
        for final in final_cases:
            best = max(best, _case_similarity(generated, final))
            if best >= _SIMILARITY_MATCH_THRESHOLD:
                break
        if best >= _SIMILARITY_MATCH_THRESHOLD:
            matched.add(gen_idx)
    return matched


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _is_state_consistency_case(text: str) -> bool:
    lowered = text.lower()
    strong_phrases = (
        "状态流转",
        "状态迁移",
        "状态变化",
        "状态同步",
        "状态一致",
        "跨端同步",
        "跨端一致",
        "刷新后保持",
        "切换后保持",
        "返回后保持",
        "state transition",
        "state consistency",
        "status transition",
        "status consistency",
        "switch-back",
        "switch back",
    )
    if any(token in lowered for token in strong_phrases):
        return True
    state_terms = (
        "状态",
        "status",
        "state",
    )
    transition_terms = (
        "流转",
        "迁移",
        "切换",
        "跳转",
        "返回",
        "变更",
        "从",
        "到",
        "transition",
        "switch",
        "change",
    )
    consistency_terms = (
        "一致",
        "同步",
        "保留",
        "保持",
        "未丢失",
        "持久",
        "刷新后",
        "回到",
        "回退",
        "consistent",
        "sync",
        "retain",
        "retained",
        "unchanged",
        "persist",
        "persistence",
        "refresh",
        "rollback",
    )
    weak_progress_terms = (
        "进度",
        "记录",
        "progress",
        "record",
    )
    has_state = any(token in lowered for token in state_terms)
    has_transition = any(token in lowered for token in transition_terms)
    has_consistency = any(token in lowered for token in consistency_terms)
    has_weak_progress = any(token in lowered for token in weak_progress_terms)
    if has_state and (has_transition or has_consistency):
        return True
    if has_weak_progress and has_transition and has_consistency:
        return True
    return False


def _case_is_grounded_in_requirement(case: dict[str, Any], requirement_text: str) -> bool:
    requirement = _fingerprint(requirement_text)
    if not requirement:
        return False
    case_tokens = [
        token
        for token in re.split(r"[\s,，。；;、:：/\\|（）()\[\]【】]+", _case_text(case))
        if len(token) >= 2
    ]
    if not case_tokens:
        return False
    hits = sum(1 for token in case_tokens[:80] if token.lower() in requirement)
    return hits >= 2


def _infer_pattern_category(case: dict[str, Any]) -> str:
    text = _case_text(case)
    if _contains_any(text, _PERMISSION_TOKENS):
        return "permission_or_scope_guard"
    if _contains_any(text, _CROSS_SYSTEM_TOKENS):
        return "cross_system_business_flow"
    if _contains_any(text, _TRANSACTION_TOKENS):
        return "transaction_business_risk"
    if _is_state_consistency_case(text):
        return "state_consistency_flow"
    return "manual_final_business_coverage"


def _priority(case: dict[str, Any]) -> str:
    value = case_priority(case) or str(case.get("model_priority") or "P2").strip().upper()
    return value if value in {"P0", "P1", "P2"} else "P2"


def _aggregate_positive_pattern_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep representative final-case patterns instead of storing every final case."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        key = _positive_pattern_key(sample)
        buckets.setdefault(key, []).append(sample)

    selected: list[dict[str, Any]] = []
    # Prefer high-risk and cross-system buckets first; within a bucket keep only
    # a few representative final cases so the pool stores reusable patterns.
    for _key, bucket in sorted(buckets.items(), key=lambda item: _positive_bucket_rank(item[1])):
        selected.extend(bucket[:_MAX_POSITIVE_SAMPLES_PER_PATTERN_KEY])
        if len(selected) >= _MAX_DERIVED_POSITIVE_PATTERNS:
            break
    return selected[:_MAX_DERIVED_POSITIVE_PATTERNS]


def _positive_pattern_key(sample: dict[str, Any]) -> str:
    return "|".join(
        [
            str(sample.get("pattern_category") or ""),
            str(sample.get("expected_priority") or ""),
            "ext" if sample.get("manual_business_extension") is True else "req",
            _summarize_module_hint(str(sample.get("source_case_module") or "")).lower(),
        ]
    )


def _positive_bucket_rank(bucket: list[dict[str, Any]]) -> tuple[int, int, str]:
    first = bucket[0] if bucket else {}
    category = str(first.get("pattern_category") or "")
    priority = str(first.get("expected_priority") or "P2")
    category_rank = {
        "transaction_business_risk": 0,
        "permission_or_scope_guard": 1,
        "cross_system_business_flow": 2,
        "state_consistency_flow": 3,
        "manual_final_business_coverage": 4,
    }.get(category, 5)
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}.get(priority, 2)
    return (category_rank, priority_rank, _positive_pattern_key(first))


def _workflow_actor(case: dict[str, Any]) -> str:
    explicit = str(case.get("role") or case.get("actor") or "").strip().lower()
    if explicit == "teacher":
        return "supervisor"
    if explicit in {"admin", "supervisor", "student", "member", "student_free"}:
        return explicit
    text = _case_text(case).lower()
    if any(token in text for token in ("admin", "后台", "管理员", "审核")):
        return "admin"
    if any(token in text for token in ("supervisor", "督导", "老师", "教师")):
        return "supervisor"
    return "student"


def _workflow_step_keywords(case: dict[str, Any]) -> list[str]:
    raw = " ".join(
        [
            _text(case.get("test_module")),
            _text(case.get("description")),
            _text(case.get("expected_result")),
        ]
    )
    tokens = [
        token.strip()
        for token in re.split(r"[\s,，。；;、|/]+", raw)
        if len(token.strip()) >= 2
    ]
    return list(dict.fromkeys(tokens))[:8]


def _workflow_transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def _workflow_stage_kind(case: dict[str, Any]) -> str:
    # Use the executed action surface first. Expected results often mention the
    # next page and would otherwise shift a configure step into preview/display.
    text = " ".join(
        _text(case.get(key))
        for key in ("description", "test_module", "steps", "test_input")
        if case.get(key) is not None
    ).lower()
    if _contains_any(text, ("学习完成", "完成学习", "进度更新", "进度同步", "completion", "progress sync")):
        return "completion_sync"
    if _contains_any(
        text,
        (
            "首页",
            "本周任务",
            "学习计划页",
            "下游",
            "同步展示",
            "状态同步",
            "paid status",
            "order status",
            "visible",
            "reflect",
            "sync",
            "学生端",
            "学员端",
            "书房端",
            "用户端",
            "展示一致",
            "同步展示",
            "可见",
            "student side",
            "learner side",
        ),
    ):
        return "downstream_visibility"
    if _contains_any(text, ("保存", "提交", "确认", "发布", "支付", "save", "submit", "commit", "publish", "payment")):
        return "commit"
    if _contains_any(text, ("预览", "确认前", "preview", "review")):
        return "preview"
    if _contains_any(text, ("新增", "创建", "选课", "选择", "设置", "配置", "编辑", "create", "select", "set", "configure", "edit")):
        return "configure"
    if _contains_any(text, ("点击学习", "进入课程", "打开课程", "open course", "start learning", "click course")):
        return "consume"
    if _contains_any(text, ("进入", "访问", "打开", "入口", "enter", "access", "open")):
        return "entry"
    return "unknown"


def _workflow_scope(case: dict[str, Any], stage_kind: str) -> str:
    text = _case_text(case).lower()
    if _contains_any(text, ("排课", "新增计划", "编辑计划", "schedule", "lesson plan")):
        return "schedule_plan"
    if _contains_any(text, ("首页", "本周任务", "homepage", "home page", "weekly task")):
        return "student_home_weekly_task"
    if _contains_any(text, ("学习计划", "learning plan")):
        return "learning_plan"
    if _contains_any(text, ("课程学习", "点击学习", "进入课程", "course learning", "open course")):
        return "course_learning"
    if _contains_any(text, ("checkout", "payment", "支付", "订单", "order")):
        return "checkout_order"
    module = re.sub(r"[^a-z0-9]+", "_", _text(case.get("test_module")).lower()).strip("_")
    return module[:48] or ("workflow" if stage_kind == "unknown" else f"workflow_{stage_kind}")


def _workflow_action(case: dict[str, Any], *, stage_kind: str, scope: str) -> str:
    transition = _workflow_transition_payload(case)
    explicit = _text(case.get("action") or transition.get("action"))
    if explicit:
        return explicit[:160]
    text = _case_text(case).lower()
    if stage_kind == "configure" and _contains_any(text, ("选课", "选择课程", "select course")):
        return "select_courses"
    if stage_kind == "configure" and _contains_any(text, ("日期", "时间", "上课时间", "date", "time")):
        return "configure_schedule_time"
    if stage_kind == "preview":
        return "go_to_preview"
    if stage_kind == "commit" and scope == "schedule_plan":
        return "save_plan"
    if stage_kind == "commit" and _contains_any(text, ("payment", "支付")):
        return "submit_payment"
    if stage_kind == "downstream_visibility" and scope == "student_home_weekly_task":
        return "verify_weekly_task_visible"
    if stage_kind == "consume" and scope == "course_learning":
        return "open_course_learning"
    if stage_kind == "completion_sync":
        return "complete_learning_and_sync_progress"
    return f"{stage_kind}_{scope}"[:160]


def _workflow_target_state(case: dict[str, Any], *, stage_kind: str, scope: str) -> str:
    transition = _workflow_transition_payload(case)
    explicit = _text(case.get("target_state") or case.get("state_out") or transition.get("target_state"))
    if explicit:
        return explicit[:120]
    text = _case_text(case).lower()
    if stage_kind == "configure" and _contains_any(text, ("选课", "选择课程", "select course")):
        return "schedule_courses_selected"
    if stage_kind == "configure" and _contains_any(text, ("日期", "时间", "上课时间", "date", "time")):
        return "schedule_time_configured"
    suffix_by_kind = {
        "entry": "entry_ready",
        "configure": "configured",
        "preview": "preview_ready",
        "commit": "saved",
        "downstream_visibility": "visible",
        "consume": "opened",
        "completion_sync": "progress_synced",
    }
    return f"{scope}_{suffix_by_kind.get(stage_kind, 'ready')}"[:120]


def _workflow_candidate(case: dict[str, Any], *, explicit_main_smoke: bool) -> tuple[bool, str]:
    transition = _workflow_transition_payload(case)
    text = _case_text(case).lower()
    path_type = str(case.get("path_type") or transition.get("path_type") or "").strip().lower()
    if path_type and path_type != "positive":
        return False, "negative_path"
    if case.get("blocking") is True or transition.get("blocking") is True:
        return False, "blocking_path"
    if case.get("destructive") is True or transition.get("destructive") is True:
        return False, "destructive_path"
    if transition.get("can_advance_main_flow") is False:
        return False, "non_advancing_path"
    if _contains_any(text, ("埋点", "pv", "uv", "tracking", "analytics")):
        return False, "analytics"
    if _contains_any(text, ("权限", "无权限", "越权", "permission", "forbidden", "unauthorized")):
        return False, "permission"
    if _contains_any(text, ("删除", "下架", "归档", "作废", "delete", "remove", "archive", "unpublish")):
        return False, "destructive_action"
    if _contains_any(
        text,
        (
            "失败",
            "异常",
            "超时",
            "错误",
            "拒绝",
            "不可",
            "置灰",
            "冲突",
            "上限",
            "下限",
            "空状态",
            "无数据",
            "failed",
            "failure",
            "timeout",
            "error",
            "invalid",
            "blocked",
            "cannot",
            "empty",
            "boundary",
        ),
    ):
        return False, "negative_or_boundary"
    stage_kind = _workflow_stage_kind(case)
    if stage_kind == "unknown":
        return False, "unknown_stage"
    if not explicit_main_smoke and _contains_any(text, ("文案", "样式", "布局", "颜色", "排序", "筛选", "标签", "copy", "layout", "sorting", "filter")):
        return False, "display_only"
    return True, stage_kind


_WORKFLOW_STAGE_ORDER = (
    "entry",
    "configure",
    "preview",
    "commit",
    "downstream_visibility",
    "consume",
    "completion_sync",
)
_WORKFLOW_STAGE_LIMITS = {
    "entry": 1,
    "configure": 3,
    "preview": 1,
    "commit": 1,
    "downstream_visibility": 2,
    "consume": 1,
    "completion_sync": 1,
}


def _workflow_selection_score(
    case: dict[str, Any],
    *,
    stage_kind: str,
    original_index: int,
) -> tuple[int, int, int, int, int]:
    text = _case_text(case).lower()
    priority_score = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(_priority(case), 0)
    transition = _workflow_transition_payload(case)
    explicit_main = int(
        str(case.get("execution_group") or "").strip() == "main_smoke"
        or str(case.get("chain_id") or "").strip() == "main_smoke_chain"
        or transition.get("main_path_step") is True
    )
    assertion_score = 1 if _text(case.get("expected_result")) else 0
    step_score = 1 if _text(case.get("steps")) else 0
    semantic_score = 0
    if stage_kind == "downstream_visibility":
        if _contains_any(
            text,
            (
                "学生端",
                "学员端",
                "书房端",
                "用户端",
                "student side",
                "learner side",
            ),
        ):
            semantic_score += 3
        if _contains_any(
            text,
            (
                "展示一致",
                "同步展示",
                "一致",
                "consistent",
                "sync",
                "reflect",
            ),
        ):
            semantic_score += 2
        if _contains_any(
            text,
            (
                "排行榜",
                "卡片可滑动",
                "样式",
                "布局",
                "ranking",
                "layout",
                "style",
            ),
        ):
            semantic_score -= 2
    if stage_kind == "configure":
        if _contains_any(
            text,
            (
                "新增",
                "选课",
                "选择",
                "选时间",
                "设置",
                "配置",
                "下一步",
                "添加",
                "create",
                "select",
                "configure",
                "next",
                "add",
            ),
        ):
            semantic_score += 2
        if _contains_any(
            text,
            (
                "查看",
                "展示",
                "view",
                "display",
            ),
        ):
            semantic_score -= 1
    stage_score = {
        "commit": 5,
        "downstream_visibility": 4,
        "consume": 4,
        "completion_sync": 4,
        "preview": 3,
        "configure": 2,
        "entry": 1,
    }.get(stage_kind, 0)
    return (
        explicit_main,
        priority_score,
        stage_score,
        semantic_score + assertion_score + step_score,
        -int(original_index),
    )


def _stage_ordered_workflow_cases(
    accepted: list[tuple[int, dict[str, Any], str]]
) -> list[tuple[dict[str, Any], str]]:
    buckets: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    for original_index, case, stage_kind in accepted:
        buckets.setdefault(stage_kind, []).append((original_index, case, stage_kind))

    selected: list[tuple[int, dict[str, Any], str]] = []
    for stage_kind in _WORKFLOW_STAGE_ORDER:
        bucket = buckets.get(stage_kind) or []
        if not bucket:
            continue
        ranked = sorted(
            bucket,
            key=lambda item: _workflow_selection_score(
                item[1],
                stage_kind=item[2],
                original_index=item[0],
            ),
            reverse=True,
        )
        picked = ranked[: int(_WORKFLOW_STAGE_LIMITS.get(stage_kind, 1))]
        selected.extend(sorted(picked, key=lambda item: item[0]))
    return [(case, stage_kind) for _index, case, stage_kind in selected[:10]]


def _select_workflow_cases(cases: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], str]], str]:
    explicit = [
        case
        for case in cases
        if isinstance(case, dict)
        and (
            str(case.get("execution_group") or "").strip() == "main_smoke"
            or str(case.get("chain_id") or "").strip() == "main_smoke_chain"
        )
    ]
    source = "explicit_main_smoke" if explicit else "inferred_positive_main_flow"
    candidates = explicit or [
        case
        for case in cases
        if isinstance(case, dict) and _priority(case) in {"P0", "P1"}
    ]
    accepted: list[tuple[int, dict[str, Any], str]] = []
    for index, case in enumerate(candidates):
        if not (_text(case.get("description")) or _text(case.get("test_module"))):
            continue
        is_accepted, stage_kind = _workflow_candidate(case, explicit_main_smoke=bool(explicit))
        if is_accepted:
            accepted.append((index, case, stage_kind))
    if explicit:
        selected = [(case, stage_kind) for _index, case, stage_kind in accepted[:10]]
        return selected, source
    selected = _stage_ordered_workflow_cases(accepted)
    if _workflow_chain_is_executable(selected):
        return selected, "inferred_stage_ordered_positive_main_flow"
    selected = [(case, stage_kind) for _index, case, stage_kind in accepted[:10]]
    return selected, source


def _workflow_chain_is_executable(selected: list[tuple[dict[str, Any], str]]) -> bool:
    stage_kinds = [stage_kind for _case, stage_kind in selected]
    if len(stage_kinds) < 2 or "commit" not in stage_kinds:
        return False
    commit_index = stage_kinds.index("commit")
    return any(
        index > commit_index and stage_kind in {"downstream_visibility", "consume", "completion_sync"}
        for index, stage_kind in enumerate(stage_kinds)
    )


def _build_workflow_blueprint_sample(
    cases: list[dict[str, Any]],
    *,
    generation_id: int | None,
    linked_doc_ids: list[int],
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    selected, selection_source = _select_workflow_cases(cases)
    if not _workflow_chain_is_executable(selected):
        return None
    steps: list[dict[str, Any]] = []
    previous_state = ""
    workflow_id = f"workflow_blueprint_{generation_id or 'manual'}"
    for index, (case, stage_kind) in enumerate(selected, start=1):
        description = _text(case.get("description")) or f"workflow-step-{index}"
        module = _text(case.get("test_module"))
        expected = _text(case.get("expected_result"))
        transition = _workflow_transition_payload(case)
        scope = _workflow_scope(case, stage_kind)
        source_state = previous_state or _text(
            case.get("source_state") or case.get("state_in") or transition.get("source_state")
        ) or f"{scope}_initial"
        state_out = _workflow_target_state(case, stage_kind=stage_kind, scope=scope)
        steps.append(
            {
                "id": f"step_{index:03d}",
                "label": description[:160],
                "module": module[:80],
                "actor": _workflow_actor(case),
                "action": _workflow_action(case, stage_kind=stage_kind, scope=scope),
                "state_in": source_state[:120],
                "state_out": state_out,
                "assertion": expected[:240],
                "test_steps": case.get("steps") if isinstance(case.get("steps"), list) else [],
                "match_keywords": _workflow_step_keywords(case),
                "source_case_id": _text(case.get("id")),
                "allow_bridge": False,
                "workflow_id": workflow_id,
                "stage_kind": stage_kind,
                "path_type": "positive",
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "main_path_step": True,
                "state_transition_reason": selection_source,
            }
        )
        previous_state = state_out
    title = _text(selected[0][0].get("test_module")) or "final_case_workflow"
    return {
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": "main_smoke_flow",
        "reason_category": "main_smoke_flow",
        "expected_priority": "P0",
        "case_id": workflow_id,
        "title": f"Workflow blueprint: {title}"[:120],
        "user_comment": "Derived from ordered human-final cases; use as executable flow structure, not fixed domain copy.",
        "pattern_summary": f"workflow_blueprint | main_smoke_flow | {title}"[:180],
        "pattern_grain": "workflow_blueprint",
        "source": "linked_final_case_workflow_blueprint",
        "source_type": "linked_final_case_workflow_blueprint",
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(selected[0][0].get("id")) or None,
        "learning_signal_source": "final_case_workflow_blueprint",
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=True),
        "quality_ledger": dict(quality_ledger or {}),
        "generation_id": generation_id,
        "linked_doc_ids": linked_doc_ids,
        "workflow_blueprint": {
            "id": workflow_id,
            "name": title[:120],
            "source": "linked_final_case_workflow_blueprint",
            "selection_source": selection_source,
            "state_machine_version": "workflow-blueprint-v2",
            "steps": steps,
            "terminal_state": previous_state,
        },
    }


def _build_positive_sample(
    case: dict[str, Any],
    *,
    index: int,
    generation_id: int | None,
    linked_doc_ids: list[int],
    manual_business_extension: bool,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = _text(case.get("description")) or f"final-case-{index}"
    module = _text(case.get("test_module"))
    expected_result = _text(case.get("expected_result"))
    steps = _text(case.get("steps"))
    category = _infer_pattern_category(case)
    pattern_summary = _summarize_positive_pattern(description, module, category)
    extension_note = "manual_business_extension" if manual_business_extension else "requirement_grounded_final_case"
    return {
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": category,
        "reason_category": category,
        "expected_priority": _priority(case),
        "case_id": _text(case.get("id")) or f"final-{index}",
        "title": description[:120],
        "user_comment": _text(case.get("user_comment") or case.get("comment"))[:240],
        "pattern_summary": pattern_summary,
        "pattern_grain": "pattern",
        "source_case_title": description[:160],
        "source_case_module": module[:80],
        "source_case_steps": steps[:240],
        "source_case_expected_result": expected_result[:240],
        "business_assertion": expected_result[:240],
        "source": (
            "linked_final_case_business_extension"
            if manual_business_extension
            else "linked_final_case_pattern"
        ),
        "source_type": (
            "linked_final_case_business_extension"
            if manual_business_extension
            else "linked_final_case_pattern"
        ),
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(case.get("id")) or None,
        "learning_signal_source": extension_note,
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=True),
        "quality_ledger": dict(quality_ledger or {}),
        "manual_business_extension": manual_business_extension,
        "generation_id": generation_id,
        "linked_doc_ids": linked_doc_ids,
    }


def _summarize_positive_pattern(description: str, module: str, category: str) -> str:
    """Convert a human-final case into a reusable generation pattern."""
    module_hint = _summarize_module_hint(module)
    detail = _positive_pattern_detail(category)
    parts = [category, detail]
    if module_hint:
        parts.append(f"领域:{module_hint}")
    return " | ".join(part for part in parts if part)[:180]


def _summarize_module_hint(module: str) -> str:
    normalized = re.sub(r"\s+", " ", _text(module)).strip()
    if not normalized:
        return ""
    # Keep only a compact domain hint; the concrete case title is stored separately.
    normalized = re.sub(r"[-_]+", " ", normalized)
    return normalized[:40]


def _positive_pattern_detail(category: str) -> str:
    mapping = {
        "permission_or_scope_guard": (
            "覆盖未授权、未开通、隐藏或越权访问的拦截，并验证无副作用"
        ),
        "cross_system_business_flow": (
            "覆盖客户端、管理端和下游报表之间的状态与权限一致性"
        ),
        "transaction_business_risk": (
            "覆盖支付、退款、订单、权益和回滚在完整业务链路中的一致性"
        ),
        "state_consistency_flow": (
            "验证用户操作后的状态迁移、持久化、刷新、切回和进度一致性"
        ),
        "manual_final_business_coverage": (
            "优先学习带明确断言的业务流程和回归覆盖，弱化孤立静态展示检查"
        ),
    }
    return mapping.get(category) or mapping["manual_final_business_coverage"]


def _clear_negative_reason(case: dict[str, Any]) -> str:
    expected = _text(case.get("expected_result"))
    text = _case_text(case)
    expected_compact = re.sub(r"\s+", "", expected)
    if expected_compact and len(expected_compact) <= 16:
        if any(pattern in expected_compact for pattern in _NON_ASSERTABLE_EXPECTED_PATTERNS):
            return "non_assertable_expected_result"
    if any(pattern in expected for pattern in _NON_ASSERTABLE_EXPECTED_PATTERNS):
        if len(expected_compact) <= 40:
            return "non_assertable_expected_result"

    priority = _priority(case)
    if priority == "P0":
        low_value = _contains_any(text, _LOW_VALUE_UI_TOKENS)
        business_impact = _contains_any(text, _BUSINESS_IMPACT_TOKENS)
        if low_value and not business_impact:
            return "priority_overpromotion_for_low_value_ui_case"
    return ""


def _build_negative_sample(
    case: dict[str, Any],
    *,
    index: int,
    reason: str,
    generation_id: int | None,
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    description = _text(case.get("description")) or f"generated-case-{index}"
    expected_result = _text(case.get("expected_result"))
    steps = _text(case.get("steps"))
    module = _text(case.get("test_module"))
    return {
        "signal_type": "negative",
        "pattern_usage": "avoid",
        "pattern_category": reason,
        "reason_category": reason,
        "expected_priority": "P2",
        "case_id": _text(case.get("id")) or f"generated-{index}",
        "title": description[:120],
        "user_comment": _text(case.get("user_comment") or case.get("comment"))[:240],
        "pattern_summary": f"{reason} | {description}"[:180],
        "pattern_grain": "anti_pattern",
        "source_case_title": description[:160],
        "source_case_module": module[:80],
        "source_case_steps": steps[:240],
        "source_case_expected_result": expected_result[:240],
        "business_assertion": expected_result[:240],
        "source": "quality_evaluation_defect",
        "source_type": "quality_evaluation_defect",
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(case.get("id")) or None,
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=False),
        "quality_ledger": dict(quality_ledger or {}),
        "generation_id": generation_id,
    }


def _compact_quality_ledger(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    judge = payload.get("judge") if isinstance(payload.get("judge"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    return {
        "generation_id": int(payload.get("generation_id") or 0),
        "quality_assessment": str(payload.get("quality_assessment") or ""),
        "initial_quality_score": int(payload.get("initial_quality_score") or payload.get("quality_score") or 0),
        "quality_score_grade": str(payload.get("quality_score_grade") or ""),
        "final_count": int(payload.get("final_count") or 0),
        "coverage_rate": float(coverage.get("coverage_rate") or 0.0),
        "missing_rules_count": int(coverage.get("missing_rules_count") or 0),
        "non_blocking_rules_count": int(coverage.get("non_blocking_rules_count") or 0),
        "review_candidate_total": int(review.get("candidate_total") or funnel.get("candidate_count_before_review") or 0),
        "review_retained_total": int(review.get("retained_total") or funnel.get("review_selected_count") or 0),
        "judge_rejected_out_count": int(judge.get("rejected_out_count") or 0),
        "judge_pending_out_count": int(judge.get("pending_out_count") or 0),
        "snapshot_used": bool(context.get("snapshot_used")),
        "fusion_mode": str(context.get("fusion_mode") or ""),
    }


def _pattern_confidence_from_ledger(payload: dict[str, Any] | None, *, positive: bool) -> float:
    if not isinstance(payload, dict) or not payload:
        return 0.72 if positive else 0.65
    coverage_rate = float(payload.get("coverage_rate") or 0.0)
    missing_rules = int(payload.get("missing_rules_count") or 0)
    rejected = int(payload.get("judge_rejected_out_count") or 0) + int(payload.get("judge_pending_out_count") or 0)
    confidence = 0.68
    if coverage_rate >= 0.9:
        confidence += 0.08
    if missing_rules <= 2:
        confidence += 0.06
    if rejected <= 0:
        confidence += 0.04
    if positive:
        confidence += 0.06
    else:
        confidence -= 0.02
    return round(max(0.35, min(0.92, confidence)), 4)


def _parse_gen_diag_payload(raw_message: Any) -> dict[str, Any]:
    text = str(raw_message or "")
    marker = "GEN_DIAG:"
    if marker not in text:
        return {}
    payload_text = text.split(marker, 1)[1].strip()
    try:
        parsed = json.loads(payload_text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}
