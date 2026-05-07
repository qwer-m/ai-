from typing import Any, Iterator
import json

from core.db.models import LogEntry, TestGeneration
from modules.domain.stage25_switches import STAGE25_SWITCHES
from modules.testing.test_generation_components.prompting.generation_diagnostics import (
    build_context_compression_diagnostics,
    build_coverage_diagnostics,
)
from modules.testing.test_generation_components.prompting.prompt_orchestration import (
    build_supplement_closed_loop_instruction,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    merge_cases_for_append,
    normalize_final_case_priorities,
    strip_case_meta_fields,
    stream_postprocess_cases,
)
from modules.testing.test_generation_components.legacy.adapters import (
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
    clean_and_parse_json,
)


_STOP_REASON_LABELS = {
    "coverage_satisfied": "coverage_satisfied（核心规则覆盖已满足）",
    "stopped_due_to_diminishing_returns": "stopped_due_to_diminishing_returns（继续生成收益递减）",
    "optimal_case_set_reached": "optimal_case_set_reached（当前为最优测试用例集合）",
}
_MAX_GEN_DIAG_MESSAGE_BYTES = 60000


def _render_stop_reason_text(stop_reasons: list[Any]) -> str:
    labels: list[str] = []
    for reason in stop_reasons:
        key = str(reason or "").strip()
        if not key:
            continue
        label = _STOP_REASON_LABELS.get(key, key)
        if label in labels:
            continue
        labels.append(label)
    return "；".join(labels)


def _build_quality_ledger_payload(
    *,
    generation_id: int | None,
    request_id: str,
    mode: str,
    stage_counts: dict[str, Any],
    coverage_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    generation_summary_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    feedback_control_debug_payload: dict[str, Any],
    compression_diag_payload: dict[str, Any],
    context_result: dict[str, Any],
) -> dict[str, Any]:
    """Build a compact evidence ledger for one generation run."""
    context_debug = dict((context_result or {}).get("context_debug") or {})
    fusion_debug = dict((context_result or {}).get("fusion_debug") or {})
    context_source = str((context_result or {}).get("context_source") or "").strip()
    compression_source = str(compression_diag_payload.get("context_source") or "").strip()
    missing_types = coverage_payload.get("missing_types") if isinstance(coverage_payload.get("missing_types"), dict) else {}
    return {
        "kind": "generation_quality_ledger",
        "generation_id": int(generation_id or 0),
        "request_id": str(request_id or ""),
        "generation_mode": str(mode or ""),
        "final_count": int(generation_summary_payload.get("final_count") or convergence_payload.get("final_count") or 0),
        "quality_assessment": str(generation_summary_payload.get("quality_assessment") or ""),
        "stop_reason": list(generation_summary_payload.get("stop_reason") or []),
        "coverage": {
            "coverage_rate": float(coverage_payload.get("coverage_rate") or 0.0),
            "total_rules": int(coverage_payload.get("total_rules") or 0),
            "total_extracted_rules": int(coverage_payload.get("total_extracted_rules") or coverage_payload.get("total_rules") or 0),
            "missing_rules_count": int(len(coverage_payload.get("missing_rules") or [])),
            "missing_boundary_count": int(len(missing_types.get("boundary") or [])),
            "missing_exception_count": int(len(missing_types.get("exception") or [])),
            "non_blocking_rules_count": int(len(coverage_payload.get("non_blocking_rules") or [])),
        },
        "funnel": {
            "primary_count": int(stage_counts.get("primary") or convergence_payload.get("primary_count") or 0),
            "gap_count": int(stage_counts.get("gap") or convergence_payload.get("gap_count") or 0),
            "review_count": int(stage_counts.get("review") or convergence_payload.get("review_count") or 0),
            "candidate_count_before_review": int(convergence_payload.get("candidate_count_before_review") or 0),
            "review_selected_count": int(convergence_payload.get("review_selected_count") or 0),
            "post_review_dedup_drop": int(convergence_payload.get("post_review_dedup_drop") or 0),
            "low_quality_dropped_count": int(convergence_payload.get("low_quality_dropped_count") or 0),
            "semantic_dedup_dropped_count": int(convergence_payload.get("semantic_dedup_dropped_count") or 0),
        },
        "review": {
            "candidate_total": int(review_decision_summary_payload.get("candidate_total") or 0),
            "retained_total": int(review_decision_summary_payload.get("retained_total") or 0),
            "drop_by_review_llm_count": int(review_decision_summary_payload.get("drop_by_review_llm_count") or 0),
            "drop_by_review_gate_count": int(review_decision_summary_payload.get("drop_by_review_gate_count") or 0),
            "drop_by_post_review_dedup_count": int(
                review_decision_summary_payload.get("drop_by_post_review_dedup_count") or 0
            ),
        },
        "judge": {
            "total": int(judge_summary_payload.get("total") or judge_summary_payload.get("input_count") or 0),
            "rejected_out_count": int(judge_summary_payload.get("rejected_out_count") or 0),
            "pending_out_count": int(judge_summary_payload.get("pending_out_count") or 0),
        },
        "context": {
            "snapshot_status": str(context_debug.get("snapshot_status") or ""),
            "snapshot_used": bool(context_debug.get("snapshot_used")),
            "realtime_rag_used": bool(context_debug.get("realtime_rag_used")),
            "current_document_used": bool(context_debug.get("current_document_used")),
            "fusion_mode": str(fusion_debug.get("mode") or context_source or compression_source or ""),
            "compression_ratio": compression_diag_payload.get("compression_ratio"),
            "retained_chunk_count": int(compression_diag_payload.get("retained_chunk_count") or 0),
        },
        "control": {
            "control_state_applied": bool(feedback_control_debug_payload.get("control_state_applied")),
            "generation_coverage_mode": str(feedback_control_debug_payload.get("generation_coverage_mode") or ""),
            "must_cover_rules_count": int(feedback_control_debug_payload.get("must_cover_rules_count") or 0),
            "quality_fix_hints_count": int(feedback_control_debug_payload.get("quality_fix_hints_count") or 0),
        },
    }


def _judge_status_key(row: dict[str, Any]) -> str:
    status = str((row or {}).get("judge_status") or (row or {}).get("status") or "").strip().upper()
    return status


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _build_judge_signal_payload(row: dict[str, Any]) -> dict[str, Any]:
    signals_raw = row.get("signals") if isinstance(row.get("signals"), dict) else {}
    return {
        "violates_confirmed_fact": bool(
            signals_raw.get("violates_confirmed_fact", row.get("violates_confirmed_fact"))
        ),
        "missing_core_flow": bool(
            signals_raw.get("missing_core_flow", row.get("missing_core_flow"))
        ),
        "missing_reuse_risk": bool(
            signals_raw.get("missing_reuse_risk", row.get("missing_reuse_risk"))
        ),
        "contains_pending_logic": bool(
            signals_raw.get("contains_pending_logic", row.get("contains_pending_logic"))
        ),
        "confirmed_fact_hits": _safe_list(
            signals_raw.get("confirmed_fact_hits", row.get("confirmed_fact_hits"))
        ),
        "confirmed_fact_violations": _safe_list(
            signals_raw.get("confirmed_fact_violations", row.get("confirmed_fact_violations"))
        ),
        "reuse_risk_hits": _safe_list(signals_raw.get("reuse_risk_hits", row.get("reuse_risk_hits"))),
        "pending_hits": _safe_list(signals_raw.get("pending_hits", row.get("pending_hits"))),
    }


def _normalize_judge_row(
    row: dict[str, Any],
    *,
    generation_id: int,
    request_id: str,
) -> dict[str, Any]:
    signals_payload = _build_judge_signal_payload(row)
    before_case = row.get("before_case_snapshot")
    if not isinstance(before_case, dict):
        before_case = row.get("before_case")
    if not isinstance(before_case, dict):
        before_case = {}
    after_case = row.get("after_case_snapshot")
    if not isinstance(after_case, dict):
        after_case = row.get("after_case")
    if not isinstance(after_case, dict):
        after_case = {}

    return {
        "generation_id": int(generation_id),
        "request_id": str(request_id or "").strip(),
        "case_id": str(row.get("case_id") or "").strip(),
        "judge_status": _judge_status_key(row),
        "reject_reason": str(row.get("reject_reason") or "").strip(),
        "pending_reason": str(row.get("pending_reason") or "").strip(),
        "signals": signals_payload,
        "violates_confirmed_fact": bool(signals_payload.get("violates_confirmed_fact")),
        "missing_core_flow": bool(signals_payload.get("missing_core_flow")),
        "missing_reuse_risk": bool(signals_payload.get("missing_reuse_risk")),
        "contains_pending_logic": bool(signals_payload.get("contains_pending_logic")),
        "confirmed_fact_hits": list(signals_payload.get("confirmed_fact_hits") or []),
        "confirmed_fact_violations": list(signals_payload.get("confirmed_fact_violations") or []),
        "reuse_risk_hits": list(signals_payload.get("reuse_risk_hits") or []),
        "pending_hits": list(signals_payload.get("pending_hits") or []),
        "before_case_snapshot": dict(before_case),
        "after_case_snapshot": dict(after_case),
    }


def _normalize_review_compact_rows(
    rows: list[dict[str, Any]],
    *,
    generation_id: int,
    request_id: str,
) -> list[dict[str, Any]]:
    compact_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("dropped_stage") or "") != "review_llm":
            continue
        evidence = row.get("review_llm_drop_reason_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        compact_rows.append(
            {
                "generation_id": int(generation_id),
                "request_id": str(request_id or "").strip(),
                "candidate_index": int(row.get("candidate_index") or 0),
                "case_id": str(row.get("case_id") or "").strip(),
                "test_module": str(row.get("test_module") or "").strip(),
                "model_priority_current": str(row.get("model_priority_current") or "").strip(),
                "bucket": str(row.get("bucket") or "").strip(),
                "dropped_stage": "review_llm",
                "dropped_reason": str(row.get("dropped_reason") or "").strip(),
                "review_llm_drop_reason_raw": str(row.get("review_llm_drop_reason_raw") or "").strip(),
                "review_llm_drop_reason": str(row.get("review_llm_drop_reason") or "").strip(),
                "review_llm_drop_reason_source": str(row.get("review_llm_drop_reason_source") or "").strip(),
                "high_signal": bool(row.get("high_signal")),
                "has_coverage_value": bool(row.get("has_coverage_value")),
                "has_positive_evidence": bool(row.get("has_positive_evidence")),
                "has_coverage_signal": bool(row.get("has_coverage_signal")),
                "has_high_signal": bool(row.get("has_high_signal")),
                "has_competition_signal": bool(row.get("has_competition_signal")),
                "focus_score": int(row.get("focus_score") or 0),
                "evidence": {
                    "selected_case_ids": list(evidence.get("selected_case_ids") or [])[:3],
                    "selected_count_in_bucket": int(evidence.get("selected_count_in_bucket") or 0),
                    "coverage_gain_score": int(evidence.get("coverage_gain_score") or 0),
                    "missing_rule_hits_count": int(len(evidence.get("missing_rule_hits") or [])),
                    "core_rule_hits_count": int(len(evidence.get("core_rule_hits") or [])),
                    "unique_coverage_hits_count": int(len(evidence.get("unique_coverage_hits") or [])),
                    "similarity": float(evidence.get("similarity") or 0.0),
                    "duplicate_of_case_id": str(evidence.get("duplicate_of_case_id") or "").strip(),
                },
            }
        )
    return compact_rows


def _fit_table_diag_payload_size(payload: dict[str, Any], *, max_bytes: int = _MAX_GEN_DIAG_MESSAGE_BYTES) -> dict[str, Any]:
    fitted = dict(payload or {})
    rows = [item for item in (fitted.get("rows") or []) if isinstance(item, dict)]
    fitted["rows"] = rows
    fitted["row_count"] = int(len(rows))
    fitted.setdefault("row_count_total", int(len(rows)))

    def _payload_size_bytes(obj: dict[str, Any]) -> int:
        return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    if _payload_size_bytes(fitted) <= max_bytes:
        return fitted

    sampled = list(rows)
    while sampled:
        candidate = dict(fitted)
        candidate["rows"] = sampled
        candidate["row_count"] = int(len(sampled))
        candidate["row_count_total"] = int(len(rows))
        candidate["rows_scope"] = "sampled_due_to_size"
        if _payload_size_bytes(candidate) <= max_bytes:
            return candidate
        if len(sampled) <= 1:
            break
        sampled = sampled[: max(1, int(len(sampled) // 2))]

    fallback = dict(fitted)
    fallback["rows"] = []
    fallback["row_count"] = 0
    fallback["row_count_total"] = int(len(rows))
    fallback["rows_scope"] = "summary_only_due_to_size"
    return fallback


class LegacyGenerationStreamPersistMixin:

    def _stream_persist_phase(
        self,
        *,
        state: dict[str, Any],
    ) -> Iterator[None]:
        client = state["client"]
        requirement = state["requirement"]
        project_id = state["project_id"]
        db = state["db"]
        doc_type = state["doc_type"]
        compress = state["compress"]
        expected_count = state["expected_count"]
        overwrite = state["overwrite"]
        append = state["append"]
        user_id = state["user_id"]
        original_requirement = state["original_requirement"]
        kb_context = state.get("kb_context") or ""
        start_id = int(state.get("start_id") or 1)
        existing_cases = state.get("existing_cases") or []
        existing_entry = state.get("existing_entry")
        context_result = state.get("context_result") or {}
        gate_debug = state.get("gate_debug") or {}
        base_prompt = state.get("base_prompt") or ""
        full_content = state.get("full_content") or ""
        existing_unique_count = int(state.get("existing_unique_count") or 0)
        system_prompt = state.get("system_prompt") or ""
        current_biz_key = str(state.get("current_biz_key") or "")
        multi_pass = bool(state.get("multi_pass", True))
        generation_mode = str(state.get("generation_mode") or "").strip().lower()
        request_id = str(state.get("request_id") or "").strip()
        feedback_control_state = state.get("feedback_control_state") or {}
        requirement_semantics_context = state.get("requirement_semantics_context") or {}
        memory_diag = state.get("memory_diag") if isinstance(state.get("memory_diag"), dict) else {}

        try:
            postprocess_result = yield from stream_postprocess_cases(
                client=client,
                requirement=requirement,
                base_prompt=base_prompt,
                kb_context=kb_context,
                full_content=full_content,
                expected_count=expected_count,
                append=append,
                existing_cases=existing_cases,
                existing_unique_count=existing_unique_count,
                start_id=start_id,
                db=db,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                count_unique_test_cases_fn=count_unique_test_cases,
                infer_case_kind_fn=infer_case_kind,
                build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction,
                current_biz_key=current_biz_key,
                multi_pass=multi_pass,
                generation_mode=generation_mode,
                feedback_control_state=feedback_control_state,
                requirement_semantics_context=requirement_semantics_context,
            )

            stage_counts: dict[str, Any] = {}
            coverage_payload: dict[str, Any] = {}
            convergence_payload: dict[str, Any] = {}
            generation_summary_payload: dict[str, Any] = {}
            review_decision_summary_payload: dict[str, Any] = {}
            review_decision_table_payload: list[dict[str, Any]] = []
            judge_decision_table_payload: list[dict[str, Any]] = []
            feedback_control_debug_payload: dict[str, Any] = {}
            judge_summary_payload: dict[str, Any] = {}
            if isinstance(postprocess_result, dict):
                parsed_result = postprocess_result.get("cases")
                if not isinstance(parsed_result, list):
                    parsed_result = []
                stage_counts = dict(postprocess_result.get("stage_counts") or {})
                coverage_payload = dict(postprocess_result.get("coverage") or {})
                convergence_payload = dict(postprocess_result.get("convergence_debug") or {})
                generation_summary_payload = dict(postprocess_result.get("generation_summary") or {})
                review_decision_summary_payload = dict(postprocess_result.get("review_decision_summary") or {})
                review_decision_table_payload = [
                    item
                    for item in (postprocess_result.get("review_decision_table") or [])
                    if isinstance(item, dict)
                ]
                judge_decision_table_payload = [
                    item
                    for item in (postprocess_result.get("judge_decision_table") or [])
                    if isinstance(item, dict)
                ]
                feedback_control_debug_payload = dict(postprocess_result.get("feedback_control_debug") or {})
                judge_summary_payload = dict(postprocess_result.get("judge_summary") or {})
            else:
                parsed_result = postprocess_result if isinstance(postprocess_result, list) else []

            if len(parsed_result) == 0:
                yield "\n@@STATUS@@:鐢熸垚澶辫触\n"
                yield "Error: 妯″瀷杩斿洖绌虹粨鏋滄垨瑙ｆ瀽涓嶅埌鏈夋晥鐢ㄤ緥锛岃妫€鏌ユā鍨嬮厤缃?鎻愮ず璇?缃戠粶鍚庨噸璇昞n"

            parsed_result = normalize_final_case_priorities(parsed_result, requirement_text=requirement)
            parsed_result = strip_case_meta_fields(parsed_result)
            cleaned_response = json.dumps(parsed_result, ensure_ascii=False)
            persisted_generation_id: int | None = None

            if db:
                if overwrite:
                    from sqlalchemy import desc

                    query = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == original_requirement,
                    )
                    if user_id:
                        query = query.filter(TestGeneration.user_id == user_id)
                    existing_entry_overwrite = query.order_by(desc(TestGeneration.created_at)).first()
                    if existing_entry_overwrite:
                        existing_entry_overwrite.generated_result = cleaned_response
                        db.commit()
                        persisted_generation_id = int(existing_entry_overwrite.id or 0) or None
                    else:
                        new_entry = TestGeneration(
                            requirement_text=original_requirement,
                            generated_result=cleaned_response,
                            project_id=project_id,
                            user_id=user_id,
                        )
                        db.add(
                            new_entry
                        )
                        db.commit()
                        persisted_generation_id = int(new_entry.id or 0) or None
                elif append and existing_entry:
                    merged_result = merge_cases_for_append(
                        existing_cases,
                        parsed_result,
                        deduplicate_test_cases_fn=deduplicate_test_cases,
                        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                    )
                    existing_entry.generated_result = json.dumps(merged_result, ensure_ascii=False)
                    db.commit()
                    persisted_generation_id = int(existing_entry.id or 0) or None
                else:
                    new_entry = TestGeneration(
                        requirement_text=original_requirement,
                        generated_result=cleaned_response,
                        project_id=project_id,
                        user_id=user_id,
                    )
                    db.add(
                        new_entry
                    )
                    db.commit()
                    persisted_generation_id = int(new_entry.id or 0) or None

                # 中文注释：把本次最终落库 generation_id 回传给前端，便于流式完成后回拉最终结果。
                if persisted_generation_id:
                    persisted_payload = {
                        "kind": "generation_persisted",
                        "generation_id": int(persisted_generation_id),
                        "project_id": int(project_id),
                    }
                    if request_id:
                        persisted_payload["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(persisted_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(persisted_payload, ensure_ascii=False)}\n"

                mode_payload = {
                    "kind": "generation_mode",
                    "mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    "biz_keys": [current_biz_key or "unknown"],
                    "current_biz_key": current_biz_key or "unknown",
                    "multi_pass": bool(multi_pass),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(mode_payload, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(mode_payload, ensure_ascii=False)}\n"

                # 中文注释：记录阶段日志，便于观察 multi-pass 执行情况。
                for stage in ("primary", "gap", "review"):
                    payload = {
                        "kind": "generation_stage",
                        "stage": stage,
                        "case_count": int(stage_counts.get(stage, 0)),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}\n"

                # GEN_DIAG 鎬昏
                full_input = (system_prompt or "") + requirement
                actual_model = client.select_model(full_input, task_type="generation")
                compression_diag_payload = build_context_compression_diagnostics(
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                diag = {
                    "kind": "gen_diag",
                    "mode": "stream",
                    "doc_type": doc_type,
                    "compress": compress,
                    "expected_count": expected_count,
                    "generated_count": count_unique_test_cases(parsed_result),
                    "content_length": len(requirement),
                    "kb_length": len(kb_context or ""),
                    "model": actual_model,
                    "max_tokens": client.max_tokens,
                    "multi_pass": bool(multi_pass),
                    "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    "context_compression_ratio": compression_diag_payload.get("compression_ratio"),
                    "context_retained_chunk_count": compression_diag_payload.get("retained_chunk_count"),
                    "context_relevance_distribution": compression_diag_payload.get("relevance_distribution") or {},
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}\n"
                compression_diag = {
                    "kind": "generation_context_compression",
                    **compression_diag_payload,
                    "multi_pass": bool(multi_pass),
                    "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                }
                if request_id:
                    compression_diag["request_id"] = request_id
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(compression_diag, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(compression_diag, ensure_ascii=False)}\n"

                # 中文注释：记录“质量/覆盖收敛”诊断，数量仅作为参考差异，不再判定为失败。
                if convergence_payload:
                    convergence_diag = {
                        "kind": "generation_convergence",
                        **convergence_payload,
                        "expected_count": int(expected_count or 0),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(convergence_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(convergence_diag, ensure_ascii=False)}\n"

                if review_decision_summary_payload:
                    review_summary_diag = {
                        "kind": "review_decision_summary",
                        **review_decision_summary_payload,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    if request_id:
                        review_summary_diag["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(review_summary_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(review_summary_diag, ensure_ascii=False)}\n"

                if feedback_control_debug_payload:
                    control_diag = {
                        "kind": "feedback_control_state",
                        **feedback_control_debug_payload,
                    }
                    if request_id:
                        control_diag["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(control_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(control_diag, ensure_ascii=False)}\n"
                if judge_summary_payload:
                    judge_diag = {
                        "kind": "judge_summary",
                        **judge_summary_payload,
                    }
                    if persisted_generation_id:
                        judge_diag["generation_id"] = int(persisted_generation_id)
                    if request_id:
                        judge_diag["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(judge_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(judge_diag, ensure_ascii=False)}\n"
                if judge_summary_payload or judge_decision_table_payload:
                    normalized_rows = [
                        _normalize_judge_row(
                            item,
                            generation_id=int(persisted_generation_id or 0),
                            request_id=request_id,
                        )
                        for item in judge_decision_table_payload
                        if isinstance(item, dict)
                    ]
                    reject_pending_rows = [
                        row
                        for row in normalized_rows
                        if str(row.get("judge_status") or "").upper() in {"REJECT", "PENDING"}
                    ]
                    rows_to_persist = reject_pending_rows or normalized_rows
                    judge_table_diag = {
                        "kind": "judge_decision_table",
                        "generation_id": int(persisted_generation_id or 0),
                        "rows": rows_to_persist,
                        "row_count": int(len(rows_to_persist)),
                        "row_count_total": int(len(normalized_rows)),
                        "row_count_reject_pending": int(len(reject_pending_rows)),
                        "rows_scope": "reject_pending_only" if reject_pending_rows else "all_when_no_reject_pending",
                        "row_evidence_incomplete": bool(
                            int(judge_summary_payload.get("rejected_out_count") or 0)
                            + int(judge_summary_payload.get("pending_out_count") or 0) > 0
                            and len(reject_pending_rows) == 0
                        ),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    if request_id:
                        judge_table_diag["request_id"] = request_id
                    judge_table_diag = _fit_table_diag_payload_size(judge_table_diag)
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(judge_table_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(judge_table_diag, ensure_ascii=False)}\n"
                if memory_diag:
                    memory_diag_payload = {
                        "kind": "memory_fabric_diag",
                        **dict(memory_diag),
                    }
                    if request_id:
                        memory_diag_payload["request_id"] = request_id
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(memory_diag_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(memory_diag_payload, ensure_ascii=False)}\n"

                if review_decision_table_payload:
                    review_table_diag = {
                        "kind": "review_decision_table",
                        "generation_id": int(persisted_generation_id or 0),
                        "rows": review_decision_table_payload,
                        "row_count": int(len(review_decision_table_payload)),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    if request_id:
                        review_table_diag["request_id"] = request_id
                    review_table_diag = _fit_table_diag_payload_size(review_table_diag)
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(review_table_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(review_table_diag, ensure_ascii=False)}\n"

                    compact_rows = _normalize_review_compact_rows(
                        review_decision_table_payload,
                        generation_id=int(persisted_generation_id or 0),
                        request_id=request_id,
                    )
                    if compact_rows:
                        review_table_compact_diag = {
                            "kind": "review_decision_table_compact",
                            "generation_id": int(persisted_generation_id or 0),
                            "rows": compact_rows,
                            "row_count": int(len(compact_rows)),
                            "multi_pass": bool(multi_pass),
                            "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                        }
                        if request_id:
                            review_table_compact_diag["request_id"] = request_id
                        review_table_compact_diag = _fit_table_diag_payload_size(review_table_compact_diag)
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(review_table_compact_diag, ensure_ascii=False)}",
                                user_id=user_id,
                            )
                        )
                        yield f"GEN_DIAG:{json.dumps(review_table_compact_diag, ensure_ascii=False)}\n"

                if generation_summary_payload:
                    generation_summary_diag = {
                        "kind": "generation_summary",
                        **generation_summary_payload,
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(generation_summary_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(generation_summary_diag, ensure_ascii=False)}\n"
                    status = str(generation_summary_payload.get("status") or "")
                    stop_reason_text = _render_stop_reason_text(
                        list(generation_summary_payload.get("stop_reason") or [])
                    )
                    if status in {"completed_with_optimal_set", "completed_with_quality_stop"}:
                        yield "@@STATUS@@:正常完成\n"
                        if stop_reason_text:
                            yield f"@@STATUS@@:停止原因：{stop_reason_text}\n"
                    if status == "completed_with_optimal_set":
                        yield "@@STATUS@@:已达到质量停止条件\n"
                        yield "@@STATUS@@:当前为最优测试用例集合\n"
                        yield "@@STATUS@@:继续生成将降低质量或增加冗余\n"

                quality_ledger_payload = _build_quality_ledger_payload(
                    generation_id=persisted_generation_id,
                    request_id=request_id,
                    mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    stage_counts=stage_counts,
                    coverage_payload=coverage_payload,
                    convergence_payload=convergence_payload,
                    generation_summary_payload=generation_summary_payload,
                    review_decision_summary_payload=review_decision_summary_payload,
                    judge_summary_payload=judge_summary_payload,
                    feedback_control_debug_payload=feedback_control_debug_payload,
                    compression_diag_payload=compression_diag_payload,
                    context_result=context_result if isinstance(context_result, dict) else {},
                )
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(quality_ledger_payload, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(quality_ledger_payload, ensure_ascii=False)}\n"

                # 中文注释：记录覆盖检查日志。
                if coverage_payload:
                    coverage_payload["multi_pass"] = bool(multi_pass)
                    coverage_payload["generation_mode"] = generation_mode or ("multi_pass" if multi_pass else "single_pass")
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}\n"

                # 淇濈暀鏃㈡湁瑕嗙洊璇婃柇
                if STAGE25_SWITCHES.coverage_diagnostics_enabled:
                    coverage_diag = build_coverage_diagnostics(
                        requirement=requirement,
                        generated_cases=[x for x in parsed_result if isinstance(x, dict)],
                        kb_context=kb_context,
                        fusion_debug=(context_result or {}).get("fusion_debug") or {},
                        expected_count=int(expected_count or 0),
                    )
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}\n"

                db.commit()

                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result=context_result,
                    gate_debug=gate_debug,
                    doc_type=doc_type,
                    compress=compress,
                    requirement_length=len(requirement or ""),
                )
        except Exception as e:
            print(f"Failed to save streamed result to DB: {e}")
