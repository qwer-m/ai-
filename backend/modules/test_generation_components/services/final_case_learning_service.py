"""Derive reusable sample-pool signals from human-final test cases."""

from __future__ import annotations

import csv
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
from modules.test_generation_components.repositories.history_repository import (
    TestGenerationHistoryRepository,
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

_CASE_FIELD_ALIASES = {
    "id": ("id", "case_id", "用例编号", "编号"),
    "description": ("description", "title", "用例标题", "用例名称", "测试点", "测试用例"),
    "test_module": ("test_module", "module", "模块", "所属模块", "功能模块"),
    "preconditions": ("preconditions", "前置条件"),
    "steps": ("steps", "测试步骤", "操作步骤", "步骤"),
    "test_input": ("test_input", "输入", "测试数据", "数据"),
    "expected_result": ("expected_result", "expected", "预期结果", "期望结果"),
    "priority": ("priority", "优先级"),
}

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
    parsed = _parse_csv_cases(text)
    if parsed:
        return parsed
    return []


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
        text = _text(item)
        if not text:
            return
        candidate_id = f"{source_field}-{index}"
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
        }
        candidates.append(
            {
                "id": candidate_id,
                "candidate_type": candidate_type,
                "target": "priority_sample_pool",
                "source_field": source_field,
                "text": text,
                "selected_by_default": bool(selected_by_default),
                "confidence": sample["pattern_confidence"],
                "sample": sample,
            }
        )

    for idx, item in enumerate(_as_text_list(defect.get("missing_points")), start=1):
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

    raw_candidate_count = len(candidates)
    candidates = _aggregate_evaluation_learning_candidates(candidates)
    candidates = candidates[:_MAX_EVALUATION_LEARNING_CANDIDATES]
    return {
        "candidates": candidates,
        "diagnostics": {
            "raw_candidate_count": raw_candidate_count,
            "candidate_count": len(candidates),
            "selected_by_default_count": sum(1 for item in candidates if item.get("selected_by_default") is True),
            "missing_points_count": len(_as_text_list(defect.get("missing_points"))),
            "modifications_count": len(_as_text_list(defect.get("modifications"))),
            "hallucinations_count": len(_as_text_list(defect.get("hallucinations"))),
            "candidate_aggregation_policy": (
                "defect_field_semantic_bucket_positive8_fix6_negative3"
            ),
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
        for candidate in candidate_items:
            if not isinstance(candidate, dict):
                continue
            sample = candidate.get("sample")
            if isinstance(sample, dict):
                samples.append(sample)
            elif _candidate_has_sample_shape(candidate):
                samples.append(candidate)
        samples = samples[:_MAX_EVALUATION_LEARNING_CANDIDATES]
        derived = {
            "samples": samples,
            "diagnostics": {
                "candidate_count": len(candidate_items),
                "sample_count": len(samples),
                "positive_sample_count": sum(1 for item in samples if str(item.get("signal_type") or "") == "positive"),
                "negative_sample_count": sum(1 for item in samples if str(item.get("signal_type") or "") == "negative"),
                "target": "priority_sample_pool",
                "source": "quality_evaluation_defect",
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
        return (
            "ok",
            {
                "project_id": entry.project_id,
                "generation_id": generation_id,
                "artifact_doc_id": doc.id,
                "linked_doc_ids": [doc.id for doc in linked_docs],
                "derived": derived,
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


def _as_text_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item for item in (_text(item) for item in raw) if item]
    text = _text(raw)
    return [text] if text else []


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
    base["text"] = summary
    base["id"] = f"{base.get('source_field')}-{_semantic_bucket_for_learning_text(summary)}"
    base["confidence"] = round(max(float(item.get("confidence") or 0.0) for item in bucket), 4)
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


def _candidate_has_sample_shape(candidate: dict[str, Any]) -> bool:
    if not isinstance(candidate, dict):
        return False
    return bool(candidate.get("signal_type") and candidate.get("pattern_usage") and candidate.get("pattern_summary"))


def _normalize_case_dict(item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for canonical, aliases in _CASE_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in item and item.get(alias) not in (None, ""):
                result[canonical] = item.get(alias)
                break
    for key, value in item.items():
        if key not in result and key not in {alias for aliases in _CASE_FIELD_ALIASES.values() for alias in aliases}:
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
        _text(case.get(key))
        for key in ("description", "test_module", "preconditions", "steps", "test_input", "expected_result")
        if case.get(key) is not None
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
    if _contains_any(text, _STATE_TOKENS):
        return "state_consistency_flow"
    return "manual_final_business_coverage"


def _priority(case: dict[str, Any]) -> str:
    value = str(case.get("priority") or case.get("priority_final") or case.get("model_priority") or "P2").upper()
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
        "user_comment": (
            "Linked human-final case; extra business coverage is positive evidence, "
            "not an anomaly."
        ),
        "pattern_summary": pattern_summary,
        "pattern_grain": "pattern",
        "source_case_title": description[:160],
        "source_case_module": module[:80],
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
        parts.append(f"domain:{module_hint}")
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
            "verify unauthorized, unopened, hidden, or out-of-scope access is blocked "
            "without side effects"
        ),
        "cross_system_business_flow": (
            "cover state and permission consistency across related clients, admin surfaces, "
            "and downstream reports"
        ),
        "transaction_business_risk": (
            "cover payment, refund, order, entitlement, and rollback consistency across the "
            "full business chain"
        ),
        "state_consistency_flow": (
            "verify state transition, persistence, refresh, switch-back, and progress "
            "consistency after user actions"
        ),
        "manual_final_business_coverage": (
            "prefer business workflow and regression coverage with concrete assertions over "
            "isolated static display checks"
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
    return {
        "signal_type": "negative",
        "pattern_usage": "avoid",
        "pattern_category": reason,
        "reason_category": reason,
        "expected_priority": "P2",
        "case_id": _text(case.get("id")) or f"generated-{index}",
        "title": description[:120],
        "user_comment": (
            "AI-only case is treated as negative only because it has a clear quality failure; "
            "missing from human final alone is not enough."
        ),
        "pattern_summary": f"{reason} | {description}"[:180],
        "pattern_grain": "anti_pattern",
        "source_case_title": description[:160],
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
