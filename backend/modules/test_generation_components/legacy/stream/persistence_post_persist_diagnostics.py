from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .persistence_diagnostics import (
    _MAX_GEN_DIAG_MESSAGE_BYTES,
    _fit_table_diag_payload_size,
    _normalize_judge_row,
    _normalize_review_compact_rows,
)


@dataclass(frozen=True)
class StreamPostPersistDiagnosticPayloads:
    before_generation_summary: list[dict[str, Any]]
    generation_summary: dict[str, Any] | None
    after_generation_summary: list[dict[str, Any]]


_CORE_DIAG_KEYS = (
    "kind",
    "generation_id",
    "project_id",
    "request_id",
    "source",
    "case_count",
    "execution_readiness",
    "row_count",
    "row_count_total",
    "final_count",
    "quality_score",
    "quality_score_grade",
)

_EXECUTION_SUITE_TOP_KEYS = (
    "kind",
    "version",
    "case_count",
    "suite_count",
    "runnable_suite_count",
    "linear_executable",
    "workflow_absence_declared",
    "execution_readiness",
    "main_suite_id",
    "metadata_quality",
    "warnings",
)

_EXECUTION_SUITE_KEYS = (
    "suite_id",
    "suite_name",
    "execution_group",
    "run_mode",
    "group_setup",
    "group_teardown",
    "case_count",
    "roles",
    "fixture_keys",
    "missing_dependencies",
    "runnable",
    "warnings",
)

_EXECUTION_CASE_REF_KEYS = (
    "case_id",
    "suite_order",
    "execution_sequence",
    "depends_on",
    "role",
    "session_key",
    "fixture_key",
    "setup_hint",
    "teardown_hint",
    "source_state",
    "target_state",
    "action",
    "transition_action",
    "runnable",
)

_FEEDBACK_CONTROL_SOURCE_META_KEYS = (
    "current_requirement_blueprint_status",
    "current_requirement_blueprint_count",
    "current_requirement_blueprint_step_count",
    "current_requirement_blueprint_error",
    "semantic_pipeline_failed_stage",
    "fact_ledger_compile_status",
    "fact_ledger_compile_success",
    "fact_ledger_compile_envelope_count",
    "fact_ledger_compile_candidate_attempt_count",
    "fact_ledger_compile_candidate_attempt_limit",
    "fact_ledger_compile_physical_call_count",
    "fact_ledger_compile_transport_retry_count",
    "fact_ledger_compile_transport_failure_count",
    "fact_ledger_compile_transport_replays_per_envelope",
    "fact_ledger_compile_fresh_candidate_used",
    "fact_ledger_compile_fresh_candidate_trigger_codes",
    "fact_ledger_compile_validated_attempt",
    "fact_ledger_compile_last_parseable_candidate_attempt",
    "fact_ledger_compile_last_parseable_candidate_status",
    "fact_ledger_compile_last_parseable_candidate_fingerprint",
    "fact_ledger_compile_last_parseable_candidate_error_codes",
    "fact_ledger_compile_stop_reason",
    "fact_ledger_compile_attempts",
    "fact_ledger_source_catalog_fingerprint",
    "fact_ledger_compile_max_tokens",
    "fact_ledger_compile_request_timeout_seconds",
    "fact_ledger_compile_chunked",
    "fact_ledger_compile_chunk_count",
    "fact_ledger_compile_chunk_limit",
    "fact_ledger_compile_chunk_budget_units",
    "fact_ledger_compile_catalog_budget_units",
    "fact_ledger_compile_partition_group_count",
    "fact_ledger_compile_oversized_partition_group_count",
    "fact_ledger_compile_completed_chunk_count",
    "fact_ledger_compile_failed_chunk_index",
    "fact_ledger_compile_chunk_summaries",
    "fact_ledger_compile_global_status",
    "fact_ledger_compile_global_error_codes",
    "fact_ledger_compile_collapsed_duplicate_fact_count",
    "fact_ledger_fingerprint",
    "fact_ledger_raw_declarations_fingerprint",
    "fact_ledger_evidence_facts_fingerprint",
    "fact_ledger_fact_count",
    "fact_ledger_source_evidence_count",
    "fact_ledger_source_disposition_count",
    "scope_ledger_compile_status",
    "scope_ledger_compile_success",
    "scope_ledger_compile_mode",
    "scope_ledger_compile_envelope_count",
    "scope_ledger_compile_candidate_attempt_count",
    "scope_ledger_compile_candidate_attempt_limit",
    "scope_ledger_compile_physical_call_count",
    "scope_ledger_compile_transport_retry_count",
    "scope_ledger_compile_transport_failure_count",
    "scope_ledger_compile_transport_replays_per_envelope",
    "scope_ledger_compile_fresh_candidate_used",
    "scope_ledger_compile_fresh_candidate_trigger_codes",
    "scope_ledger_compile_validated_attempt",
    "scope_ledger_compile_last_parseable_candidate_attempt",
    "scope_ledger_compile_last_parseable_candidate_status",
    "scope_ledger_compile_last_parseable_candidate_fingerprint",
    "scope_ledger_compile_last_parseable_candidate_error_codes",
    "scope_ledger_compile_stop_reason",
    "scope_ledger_compile_attempts",
    "scope_ledger_compile_global_status",
    "scope_ledger_compile_global_error_codes",
    "scope_ledger_source_topology",
    "scope_ledger_boundary_selection_status",
    "scope_ledger_boundary_selection_fingerprint",
    "scope_ledger_boundary_selection_count",
    "scope_ledger_membership_assignment_status",
    "scope_ledger_membership_assignment_fingerprint",
    "scope_ledger_membership_assignment_count",
    "scope_ledger_membership_none_count",
    "scope_ledger_boundary_manifest_status",
    "scope_ledger_boundary_manifest_fingerprint",
    "scope_ledger_fingerprint",
    "scope_ledger_boundary_count",
    "scope_ledger_active_scope_count",
    "scope_ledger_external_boundary_count",
    "scope_ledger_fact_binding_count",
    "scope_ledger_binding_role_counts",
    "scope_ledger_membership_relation_count",
    "scope_ledger_explicit_fact_membership_count",
    "scope_ledger_binding_shard_count",
    "scope_ledger_binding_shard_limit",
    "scope_ledger_binding_shard_budget_units",
    "scope_ledger_binding_oversized_fact_count",
    "scope_ledger_binding_completed_shard_count",
    "scope_ledger_binding_failed_shard_index",
    "scope_ledger_binding_shard_summaries",
    "semantic_compile_status",
    "semantic_compile_success",
    "semantic_compile_envelope_count",
    "semantic_compile_physical_call_count",
    "semantic_compile_attempt_count",
    "semantic_compile_candidate_attempt_count",
    "semantic_compile_candidate_attempt_limit",
    "semantic_compile_independent_recompile_limit",
    "semantic_compile_independent_recompile_used",
    "semantic_compile_independent_recompile_attempt",
    "semantic_compile_independent_recompile_trigger_codes",
    "semantic_compile_independent_recompile_outcome",
    "semantic_compile_targeted_repair_used",
    "semantic_compile_targeted_repair_attempt",
    "semantic_compile_targeted_repair_outcome",
    "semantic_compile_transport_retry_count",
    "semantic_compile_transport_failure_count",
    "semantic_compile_retry_used",
    "semantic_compile_timeout_count",
    "semantic_compile_stop_reason",
    "semantic_compile_attempts",
    "workflow_declaration_status",
    "workflow_absence_declared",
    "raw_workflow_candidate_count",
    "normalized_workflow_count",
    "rejected_workflow_count",
    "verified_functional_module_count",
    "requirement_semantic_graph_fact_count",
    "requirement_semantic_graph_node_count",
    "requirement_semantic_graph_edge_count",
    "semantic_graph_diagnostics",
    "source_evidence_catalog",
    "source_evidence_catalog_coverage",
)

_FACT_LEDGER_COMPILE_ATTEMPT_KEYS = (
    "attempt",
    "candidate_mode",
    "compilation_mode",
    "chunk_index",
    "chunk_count",
    "chunk_source_evidence_count",
    "status",
    "raw_chars",
    "parse_error_code",
    "parse_error_type",
    "parsed_type",
    "contract_error_count",
    "contract_error_codes",
    "fact_ledger_fingerprint",
)

_FACT_LEDGER_CHUNK_SUMMARY_KEYS = (
    "chunk_index",
    "status",
    "target_source_evidence_count",
    "budget_units",
    "target_fingerprint",
    "candidate_attempt_count",
    "envelope_count",
    "physical_call_count",
    "validated_attempt",
    "ledger_fingerprint",
    "fact_count",
    "source_disposition_count",
)

_SCOPE_LEDGER_COMPILE_ATTEMPT_KEYS = (
    "attempt",
    "candidate_mode",
    "compilation_mode",
    "phase",
    "shard_index",
    "shard_count",
    "target_fact_count",
    "status",
    "raw_chars",
    "finish_reason",
    "user_input_fingerprint",
    "source_topology_wire_present",
    "source_topology_version",
    "source_topology_fingerprint",
    "source_topology_group_count",
    "source_topology_relation_count",
    "source_topology_anchored_fact_count",
    "boundary_selection_version_wire",
    "boundary_selection_fingerprint_wire",
    "boundary_selection_count_wire",
    "boundary_manifest_fingerprint_wire",
    "parse_error_code",
    "parse_error_type",
    "parsed_type",
    "contract_error_count",
    "contract_error_codes",
    "payload_fingerprint",
)

_SCOPE_LEDGER_BINDING_SHARD_SUMMARY_KEYS = (
    "shard_index",
    "status",
    "target_fact_count",
    "budget_units",
    "target_fingerprint",
    "candidate_attempt_count",
    "envelope_count",
    "physical_call_count",
    "validated_attempt",
    "binding_count",
    "payload_fingerprint",
)

_SCOPE_LEDGER_SOURCE_TOPOLOGY_KEYS = (
    "version",
    "fingerprint",
    "group_count",
    "relation_count",
    "anchored_fact_count",
)

_SEMANTIC_COMPILE_ATTEMPT_KEYS = (
    "attempt",
    "candidate_mode",
    "semantic_attempt",
    "compilation_mode",
    "independent_recompile",
    "status",
    "raw_chars",
    "contract_status",
    "request_input_chars",
    "system_prompt_chars",
    "evidence_binding",
    "workflow_topology_status",
    "workflow_topology_error_codes",
    "workflow_consistency_rejection_count",
    "workflow_consistency_rejection_codes",
    "unrepairable_required_component_error_codes",
    "projection_error_codes",
    "independent_recompile_scheduled",
)

_SEMANTIC_GRAPH_DIAGNOSTIC_KEYS = (
    "fact_count",
    "node_count",
    "edge_count",
    "scope_count",
    "capability_count",
    "interaction_count",
    "derived_critical_entry_count",
    "required_control_component_count",
    "required_control_linear_component_count",
    "required_control_non_linear_component_count",
    "required_flow_isolated_component_count",
    "workflow_topology_status",
    "workflow_topology_error_count",
    "workflow_topology_error_codes",
    "workflow_topology_repairable_error_codes",
    "unrepairable_required_component_error_codes",
    "error_count",
    "error_codes",
)


def _message_size_bytes(prefix: str, payload_text: str) -> int:
    return len(f"{prefix}:{payload_text}".encode("utf-8"))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _has_compact_value(value: Any) -> bool:
    return value not in (None, "", [])


def _truncate_text_by_bytes(value: Any, *, max_bytes: int = 2000) -> str:
    text = str(value or "")
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    encoded = text.encode("utf-8")[: max(0, max_bytes - 3)]
    return encoded.decode("utf-8", errors="ignore").rstrip() + "..."


def _compact_list(value: Any, *, max_items: int = 100) -> list[Any]:
    if not isinstance(value, list):
        return []
    compact: list[Any] = []
    for item in value[:max_items]:
        if _is_scalar(item) and _has_compact_value(item):
            compact.append(item)
        elif isinstance(item, list):
            compact.append([child for child in item if _is_scalar(child) and _has_compact_value(child)][:20])
        elif isinstance(item, dict):
            compact.append(
                {
                    key: child
                    for key, child in item.items()
                    if _is_scalar(child) and _has_compact_value(child)
                }
            )
    return compact


def _compact_execution_case_ref(case_ref: Any) -> dict[str, Any]:
    if not isinstance(case_ref, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in _EXECUTION_CASE_REF_KEYS:
        value = case_ref.get(key)
        if _is_scalar(value) and _has_compact_value(value):
            compact[key] = value
        elif isinstance(value, list):
            compact[key] = _compact_list(value, max_items=20)
    return compact


def _compact_execution_suite_for_log(suite: Any) -> dict[str, Any] | None:
    if not isinstance(suite, dict) or not isinstance(suite.get("suites"), list):
        return None

    compact: dict[str, Any] = {
        "compaction": "execution_case_refs",
    }
    for key in _EXECUTION_SUITE_TOP_KEYS:
        value = suite.get(key)
        if _is_scalar(value) and _has_compact_value(value):
            compact[key] = value
        elif isinstance(value, list):
            compact[key] = _compact_list(value, max_items=30)
        elif isinstance(value, dict):
            compact[key] = {
                child_key: child
                for child_key, child in value.items()
                if _is_scalar(child) and _has_compact_value(child)
            }

    compact_suites: list[dict[str, Any]] = []
    for suite_item in suite.get("suites") or []:
        if not isinstance(suite_item, dict):
            continue
        compact_suite: dict[str, Any] = {}
        for key in _EXECUTION_SUITE_KEYS:
            value = suite_item.get(key)
            if _is_scalar(value) and _has_compact_value(value):
                compact_suite[key] = value
            elif isinstance(value, list):
                compact_suite[key] = _compact_list(value, max_items=50)
        compact_cases = [
            case
            for case in (_compact_execution_case_ref(item) for item in (suite_item.get("cases") or []))
            if case
        ]
        compact_suite["cases"] = compact_cases
        compact_suite["case_count"] = int(suite_item.get("case_count") or len(compact_cases))
        compact_suites.append(compact_suite)

    compact["suites"] = compact_suites
    compact["suite_count"] = int(suite.get("suite_count") or len(compact_suites))
    return compact


def _compact_selected_fields(
    value: Any,
    *,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in keys:
        if key not in value:
            continue
        child = value.get(key)
        if _is_scalar(child):
            compact[key] = child
        elif isinstance(child, list):
            compact[key] = _compact_list(child, max_items=32)
        elif isinstance(child, dict):
            compact[key] = {
                child_key: child_value
                for child_key, child_value in child.items()
                if _is_scalar(child_value)
            }
    return compact


def _compact_feedback_control_source_meta(value: Any) -> dict[str, Any]:
    """保留语义编译验收字段，避免超限日志把 source_meta 整体折叠。"""

    source_meta = dict(value or {}) if isinstance(value, dict) else {}
    compact: dict[str, Any] = {"compaction": "semantic_compilation_summary"}
    for key in _FEEDBACK_CONTROL_SOURCE_META_KEYS:
        if key not in source_meta:
            continue
        child = source_meta.get(key)
        if key == "fact_ledger_compile_attempts":
            attempts = child if isinstance(child, list) else []
            compact[key] = [
                attempt
                for attempt in (
                    _compact_selected_fields(
                        item,
                        keys=_FACT_LEDGER_COMPILE_ATTEMPT_KEYS,
                    )
                    for item in attempts[:32]
                )
                if attempt
            ]
            continue
        if key == "fact_ledger_compile_chunk_summaries":
            summaries = child if isinstance(child, list) else []
            compact[key] = [
                summary
                for summary in (
                    _compact_selected_fields(
                        item,
                        keys=_FACT_LEDGER_CHUNK_SUMMARY_KEYS,
                    )
                    for item in summaries[:32]
                )
                if summary
            ]
            continue
        if key == "scope_ledger_compile_attempts":
            attempts = child if isinstance(child, list) else []
            compact[key] = [
                attempt
                for attempt in (
                    _compact_selected_fields(
                        item,
                        keys=_SCOPE_LEDGER_COMPILE_ATTEMPT_KEYS,
                    )
                    for item in attempts[:40]
                )
                if attempt
            ]
            continue
        if key == "scope_ledger_binding_shard_summaries":
            summaries = child if isinstance(child, list) else []
            compact[key] = [
                summary
                for summary in (
                    _compact_selected_fields(
                        item,
                        keys=_SCOPE_LEDGER_BINDING_SHARD_SUMMARY_KEYS,
                    )
                    for item in summaries[:32]
                )
                if summary
            ]
            continue
        if key == "scope_ledger_source_topology":
            compact[key] = _compact_selected_fields(
                child,
                keys=_SCOPE_LEDGER_SOURCE_TOPOLOGY_KEYS,
            )
            continue
        if key == "semantic_compile_attempts":
            attempts = child if isinstance(child, list) else []
            compact[key] = [
                attempt
                for attempt in (
                    _compact_selected_fields(
                        item,
                        keys=_SEMANTIC_COMPILE_ATTEMPT_KEYS,
                    )
                    for item in attempts[:4]
                )
                if attempt
            ]
            continue
        if key == "semantic_graph_diagnostics":
            compact[key] = _compact_selected_fields(
                child,
                keys=_SEMANTIC_GRAPH_DIAGNOSTIC_KEYS,
            )
            continue
        if _is_scalar(child):
            compact[key] = child
        elif isinstance(child, list):
            compact[key] = _compact_list(child, max_items=32)
        elif isinstance(child, dict):
            compact[key] = {
                child_key: child_value
                for child_key, child_value in child.items()
                if _is_scalar(child_value)
            }
    return compact


def _summarize_large_value(value: Any) -> dict[str, Any] | list[Any] | str:
    if isinstance(value, dict):
        summary: dict[str, Any] = {
            "omitted_due_to_size": True,
            "value_type": "dict",
            "key_count": len(value),
        }
        for key in _CORE_DIAG_KEYS:
            child = value.get(key)
            if _is_scalar(child):
                summary[key] = child
        suites = value.get("suites")
        if isinstance(suites, list):
            summary["suite_count"] = len(suites)
        cases = value.get("cases")
        if isinstance(cases, list):
            summary["case_count"] = len(cases)
        return summary
    if isinstance(value, list):
        return {
            "omitted_due_to_size": True,
            "value_type": "list",
            "item_count": len(value),
        }
    if isinstance(value, str):
        return _truncate_text_by_bytes(value)
    return str(value)


def _compact_payload_for_log(payload: dict[str, Any], *, original_size_bytes: int) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "payload_omitted_due_to_size": True,
        "original_payload_size_bytes": int(original_size_bytes),
    }
    for key in _CORE_DIAG_KEYS:
        value = payload.get(key)
        if _is_scalar(value):
            compact[key] = value

    omitted_keys: list[str] = []
    for key, value in payload.items():
        if key in compact:
            continue
        if (
            key == "source_meta"
            and payload.get("kind") == "feedback_control_state"
            and isinstance(value, dict)
        ):
            compact[key] = _compact_feedback_control_source_meta(value)
            omitted_keys.append(key)
            continue
        if key == "execution_suite" and isinstance(value, dict):
            compact["execution_suite_omitted_due_to_size"] = True
            compact["execution_suite_summary"] = _summarize_large_value(value)
            execution_suite_compact = _compact_execution_suite_for_log(value)
            if execution_suite_compact:
                compact["execution_suite_compact"] = execution_suite_compact
            omitted_keys.append(key)
            continue
        if _is_scalar(value):
            compact[key] = value
            continue
        compact[key] = _summarize_large_value(value)
        omitted_keys.append(key)
    if omitted_keys:
        compact["omitted_keys"] = omitted_keys
    return compact


def _fit_diagnostic_payload_for_log(
    payload: dict[str, Any],
    *,
    prefix: str,
    max_bytes: int = _MAX_GEN_DIAG_MESSAGE_BYTES,
) -> tuple[dict[str, Any], str]:
    payload_text = json.dumps(payload, ensure_ascii=False)
    message_size = _message_size_bytes(prefix, payload_text)
    if message_size <= max_bytes:
        return payload, payload_text

    compact = _compact_payload_for_log(payload, original_size_bytes=message_size)
    compact_text = json.dumps(compact, ensure_ascii=False)
    if _message_size_bytes(prefix, compact_text) <= max_bytes:
        return compact, compact_text

    fallback = {
        key: compact.get(key)
        for key in _CORE_DIAG_KEYS
        if key in compact
    }
    fallback.update(
        {
            "payload_omitted_due_to_size": True,
            "original_payload_size_bytes": int(message_size),
            "fallback_summary_only": True,
        }
    )
    fallback_text = json.dumps(fallback, ensure_ascii=False)
    return fallback, fallback_text


def stream_generation_mode(generation_mode: str, multi_pass: bool) -> str:
    return generation_mode or ("multi_pass" if multi_pass else "single_pass")


def add_diagnostic_log(
    *,
    db: Any,
    log_entry_type: Any,
    project_id: int,
    user_id: int | None,
    payload: dict[str, Any],
    prefix: str = "GEN_DIAG",
) -> str:
    _, payload_text = _fit_diagnostic_payload_for_log(payload, prefix=prefix)
    if db:
        db.add(
            log_entry_type(
                project_id=project_id,
                log_type="system",
                message=f"{prefix}:{payload_text}",
                user_id=user_id,
            )
        )
    return f"{prefix}:{payload_text}\n"


def build_stream_post_persist_diagnostic_payloads(
    *,
    generation_id: int | None,
    project_id: int,
    request_id: str,
    generation_mode: str,
    multi_pass: bool,
    current_biz_key: str,
    timing_payload: dict[str, Any] | None,
    stage_counts: dict[str, Any],
    duration_by_stage_ms: dict[str, int],
    doc_type: str,
    compress: bool,
    expected_count: int,
    generated_count: int,
    requirement_length: int,
    kb_length: int,
    model: str,
    max_tokens: Any,
    compression_diag_payload: dict[str, Any],
    convergence_payload: dict[str, Any],
    review_decision_summary_payload: dict[str, Any],
    feedback_control_debug_payload: dict[str, Any],
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
    memory_diag: dict[str, Any],
    review_decision_table_payload: list[dict[str, Any]],
    generation_summary_payload: dict[str, Any],
    quality_ledger_payload: dict[str, Any],
    coverage_payload: dict[str, Any],
) -> StreamPostPersistDiagnosticPayloads:
    mode = stream_generation_mode(generation_mode, multi_pass)
    generation_id_int = int(generation_id or 0)
    before_summary: list[dict[str, Any]] = []

    if generation_id:
        persisted_payload = {
            "kind": "generation_persisted",
            "generation_id": generation_id_int,
            "project_id": int(project_id),
        }
        if request_id:
            persisted_payload["request_id"] = request_id
        before_summary.append(persisted_payload)

    if timing_payload:
        before_summary.append(dict(timing_payload))

    before_summary.append(
        {
            "kind": "generation_mode",
            "mode": mode,
            "biz_keys": [current_biz_key or "unknown"],
            "current_biz_key": current_biz_key or "unknown",
            "multi_pass": bool(multi_pass),
        }
    )

    for stage in ("primary", "gap", "review"):
        before_summary.append(
            {
                "kind": "generation_stage",
                "stage": stage,
                "case_count": int(stage_counts.get(stage, 0)),
                "duration_ms": int(duration_by_stage_ms.get(stage) or 0),
                "multi_pass": bool(multi_pass),
                "generation_mode": mode,
            }
        )

    before_summary.append(
        {
            "kind": "gen_diag",
            "mode": "stream",
            "doc_type": doc_type,
            "compress": compress,
            "expected_count": expected_count,
            "generated_count": generated_count,
            "content_length": requirement_length,
            "kb_length": kb_length,
            "model": model,
            "max_tokens": max_tokens,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
            "context_compression_ratio": compression_diag_payload.get("compression_ratio"),
            "context_retained_chunk_count": compression_diag_payload.get("retained_chunk_count"),
            "context_relevance_distribution": compression_diag_payload.get("relevance_distribution") or {},
        }
    )

    compression_diag = {
        "kind": "generation_context_compression",
        **compression_diag_payload,
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
    }
    if request_id:
        compression_diag["request_id"] = request_id
    before_summary.append(compression_diag)

    if convergence_payload:
        before_summary.append(
            {
                "kind": "generation_convergence",
                **convergence_payload,
                "expected_count": int(expected_count or 0),
                "multi_pass": bool(multi_pass),
                "generation_mode": mode,
            }
        )

    if review_decision_summary_payload:
        review_summary_diag = {
            "kind": "review_decision_summary",
            **review_decision_summary_payload,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
        if request_id:
            review_summary_diag["request_id"] = request_id
        before_summary.append(review_summary_diag)

    if feedback_control_debug_payload:
        control_diag = {
            "kind": "feedback_control_state",
            **feedback_control_debug_payload,
        }
        if request_id:
            control_diag["request_id"] = request_id
        before_summary.append(control_diag)

    if judge_summary_payload:
        judge_diag = {
            "kind": "judge_summary",
            **judge_summary_payload,
        }
        if generation_id:
            judge_diag["generation_id"] = generation_id_int
        if request_id:
            judge_diag["request_id"] = request_id
        before_summary.append(judge_diag)

    if judge_summary_payload or judge_decision_table_payload:
        before_summary.append(
            _build_judge_table_payload(
                generation_id=generation_id_int,
                request_id=request_id,
                multi_pass=multi_pass,
                mode=mode,
                judge_summary_payload=judge_summary_payload,
                judge_decision_table_payload=judge_decision_table_payload,
            )
        )

    if memory_diag:
        memory_diag_payload = {
            "kind": "memory_fabric_diag",
            **dict(memory_diag),
        }
        if request_id:
            memory_diag_payload["request_id"] = request_id
        before_summary.append(memory_diag_payload)

    before_summary.extend(
        _build_review_table_payloads(
            generation_id=generation_id_int,
            request_id=request_id,
            multi_pass=multi_pass,
            mode=mode,
            review_decision_table_payload=review_decision_table_payload,
        )
    )

    generation_summary_diag = None
    if generation_summary_payload:
        generation_summary_diag = {
            "kind": "generation_summary",
            **generation_summary_payload,
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }

    after_summary = [dict(quality_ledger_payload)]
    case_quality_gate_payload = dict(quality_ledger_payload.get("case_quality_gate") or {})
    if case_quality_gate_payload:
        if generation_id:
            case_quality_gate_payload["generation_id"] = generation_id_int
        if request_id:
            case_quality_gate_payload["request_id"] = request_id
        after_summary.append(case_quality_gate_payload)

    if coverage_payload:
        coverage_diag = dict(coverage_payload)
        coverage_diag["multi_pass"] = bool(multi_pass)
        coverage_diag["generation_mode"] = mode
        after_summary.append(coverage_diag)

    return StreamPostPersistDiagnosticPayloads(
        before_generation_summary=before_summary,
        generation_summary=generation_summary_diag,
        after_generation_summary=after_summary,
    )


def _build_judge_table_payload(
    *,
    generation_id: int,
    request_id: str,
    multi_pass: bool,
    mode: str,
    judge_summary_payload: dict[str, Any],
    judge_decision_table_payload: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_rows = [
        _normalize_judge_row(
            item,
            generation_id=int(generation_id or 0),
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
        "generation_id": int(generation_id or 0),
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
        "generation_mode": mode,
    }
    if request_id:
        judge_table_diag["request_id"] = request_id
    return _fit_table_diag_payload_size(judge_table_diag)


def _build_review_table_payloads(
    *,
    generation_id: int,
    request_id: str,
    multi_pass: bool,
    mode: str,
    review_decision_table_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not review_decision_table_payload:
        return []

    review_table_diag = {
        "kind": "review_decision_table",
        "generation_id": int(generation_id or 0),
        "rows": review_decision_table_payload,
        "row_count": int(len(review_decision_table_payload)),
        "multi_pass": bool(multi_pass),
        "generation_mode": mode,
    }
    if request_id:
        review_table_diag["request_id"] = request_id
    payloads = [_fit_table_diag_payload_size(review_table_diag)]

    compact_rows = _normalize_review_compact_rows(
        review_decision_table_payload,
        generation_id=int(generation_id or 0),
        request_id=request_id,
    )
    if compact_rows:
        review_table_compact_diag = {
            "kind": "review_decision_table_compact",
            "generation_id": int(generation_id or 0),
            "rows": compact_rows,
            "row_count": int(len(compact_rows)),
            "multi_pass": bool(multi_pass),
            "generation_mode": mode,
        }
        if request_id:
            review_table_compact_diag["request_id"] = request_id
        payloads.append(_fit_table_diag_payload_size(review_table_compact_diag))

    return payloads
