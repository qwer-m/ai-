from __future__ import annotations

import json
from typing import Any

from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    stream_postprocess_cases,
)


def _drain_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def _build_case(index: int) -> dict[str, Any]:
    return {
        "id": f"TC-{index:03d}",
        "description": f"回放用例-{index}",
        "test_module": f"module-{(index % 5) + 1}",
        "preconditions": [],
        "steps": [f"step-{index}"],
        "test_input": f"input-{index}",
        "expected_result": f"ok-{index}",
        "priority": "P1",
    }


def _build_full_content(count: int) -> str:
    return json.dumps([_build_case(i) for i in range(1, count + 1)], ensure_ascii=False)


def _valid_retry_payload(keep_count: int, total_count: int) -> str:
    kept_ids = [f"TC-{i:03d}" for i in range(1, keep_count + 1)]
    dropped = [
        {"case_id": f"TC-{i:03d}", "reason": "coverage_redundant"}
        for i in range(keep_count + 1, total_count + 1)
    ]
    return json.dumps({"kept_case_ids": kept_ids, "dropped": dropped}, ensure_ascii=False)


def _retry_payload_only_kept(keep_count: int) -> str:
    kept_ids = [f"TC-{i:03d}" for i in range(1, keep_count + 1)]
    return json.dumps({"kept_case_ids": kept_ids}, ensure_ascii=False)


def _retry_payload_with_partial_unmapped_dropped() -> str:
    return json.dumps(
        {
            "kept_case_ids": ["TC-001", "TC-002", "TC-003", "TC-004"],
            "dropped": [
                {"case_id": "TC-005", "reason": "duplicate"},
                {"case_id": "TC-006", "reason": "low_value"},
                {"case_id": "fake_999", "reason": "coverage_redundant"},
            ],
        },
        ensure_ascii=False,
    )


class _ReplayClient:
    def __init__(self, *, primary_review_response: str, retry_review_response: str) -> None:
        self.primary_review_response = str(primary_review_response or "")
        self.retry_review_response = str(retry_review_response or "")
        self.model = "deepseek-reasoner"
        self.turbo_model = "deepseek-chat"

    def select_model(self, full_input: str, task_type: str = "generation") -> str:  # noqa: ARG002
        return "deepseek-reasoner"

    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:  # noqa: ARG002
        if str(prompt or "").strip() == "You are a QA Auditor.":
            if str(kwargs.get("model") or "").strip():
                return self.retry_review_response
            return self.primary_review_response
        return "[]"

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
        yield "[]"


def _run_review_replay(client: _ReplayClient, *, case_count: int) -> dict[str, Any]:
    gen = stream_postprocess_cases(
        client=client,
        requirement="回放测试需求",
        base_prompt="BASE",
        kb_context="",
        full_content=_build_full_content(case_count),
        expected_count=20,
        append=False,
        existing_cases=[],
        existing_unique_count=0,
        start_id=1,
        db=None,
        clean_and_parse_json_fn=clean_and_parse_json,
        normalize_json_structure_fn=normalize_json_structure,
        deduplicate_test_cases_fn=deduplicate_test_cases,
        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        count_unique_test_cases_fn=count_unique_test_cases,
        infer_case_kind_fn=infer_case_kind,
        build_supplement_closed_loop_instruction_fn=lambda **_: "",
        multi_pass=True,
        generation_mode="multi_pass",
    )
    result = _drain_with_return(gen)
    assert isinstance(result, dict)
    return result


def test_retry_on_invalid_payload_without_selection_signal() -> None:
    client = _ReplayClient(
        primary_review_response=json.dumps({"summary": "x", "analysis": "x"}, ensure_ascii=False),
        retry_review_response=_valid_retry_payload(keep_count=4, total_count=18),
    )
    result = _run_review_replay(client, case_count=18)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is True
    assert runtime.get("primary_invalid_reason") == "no_mapped_and_no_selection_signal"
    assert runtime.get("retry_invoked") is True
    assert runtime.get("retry_reason") == "no_mapped_and_no_selection_signal"
    assert runtime.get("retry_parse_success") is True
    assert int(runtime.get("retry_mapped_count") or 0) > 0
    assert runtime.get("retry_payload_has_selection_signal") is True
    assert runtime.get("final_source") == "fallback_llm"
    assert runtime.get("applied_reason") == "retry_payload_valid"
    assert int(summary.get("drop_by_review_selector_count") or 0) == 0
    assert int(summary.get("drop_by_review_llm_count") or 0) > 0


def test_retry_on_invalid_payload_with_unmapped_case_ids() -> None:
    client = _ReplayClient(
        primary_review_response=json.dumps(
            {
                "kept_case_ids": ["fake_1", "fake_2"],
                "dropped": [{"case_id": "fake_3", "reason": "duplicate"}],
            },
            ensure_ascii=False,
        ),
        retry_review_response=_valid_retry_payload(keep_count=5, total_count=9),
    )
    result = _run_review_replay(client, case_count=9)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is True
    assert runtime.get("primary_invalid_reason") == "no_mapped_ids"
    assert runtime.get("retry_invoked") is True
    assert runtime.get("retry_reason") == "no_mapped_ids"
    assert int(runtime.get("retry_mapped_count") or 0) > 0
    assert runtime.get("final_source") == "fallback_llm"
    assert int(summary.get("drop_by_review_selector_count") or 0) == 0


def test_retry_on_schema_parse_error_payload() -> None:
    client = _ReplayClient(
        primary_review_response="NOT_JSON_PAYLOAD",
        retry_review_response=_valid_retry_payload(keep_count=4, total_count=18),
    )
    result = _run_review_replay(client, case_count=18)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is True
    assert runtime.get("primary_invalid_reason") == "schema_parse_error"
    assert runtime.get("retry_invoked") is True
    assert runtime.get("retry_reason") == "schema_parse_error"
    assert runtime.get("retry_parse_success") is True
    assert runtime.get("final_source") == "fallback_llm"
    assert summary.get("fallback_reason_incomplete") is False
    assert int(summary.get("fallback_dropped_reason_count") or 0) > 0
    assert int(summary.get("fallback_dropped_reason_mapped_count") or 0) > 0
    assert float(summary.get("deterministic_backfill_ratio") or 0.0) < 1.0
    assert float(summary.get("llm_reason_coverage_ratio") or 0.0) > 0.0
    assert int(summary.get("drop_by_review_selector_count") or 0) == 0
    assert isinstance(summary.get("reason_source_breakdown"), dict)
    reason_source_breakdown = dict(summary.get("reason_source_breakdown") or {})
    assert int(reason_source_breakdown.get("fallback") or 0) > 0


def test_retry_valid_selection_but_without_dropped_reasons_marks_incomplete() -> None:
    client = _ReplayClient(
        primary_review_response="NOT_JSON_PAYLOAD",
        retry_review_response=_retry_payload_only_kept(keep_count=4),
    )
    result = _run_review_replay(client, case_count=12)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is True
    assert runtime.get("primary_invalid_reason") == "schema_parse_error"
    assert runtime.get("final_source") == "fallback_llm"
    assert summary.get("fallback_reason_incomplete") is True
    assert int(summary.get("fallback_dropped_reason_count") or 0) == 0
    assert int(summary.get("fallback_dropped_reason_mapped_count") or 0) == 0
    assert int(summary.get("drop_by_review_llm_count") or 0) > 0
    assert float(summary.get("deterministic_backfill_ratio") or 0.0) > 0.0
    assert int(summary.get("drop_by_review_selector_count") or 0) == 0


def test_retry_partial_unmapped_dropped_reasons_keeps_mapped_part() -> None:
    client = _ReplayClient(
        primary_review_response="NOT_JSON_PAYLOAD",
        retry_review_response=_retry_payload_with_partial_unmapped_dropped(),
    )
    result = _run_review_replay(client, case_count=12)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is True
    assert runtime.get("final_source") == "fallback_llm"
    assert int(summary.get("fallback_dropped_reason_count") or 0) > 0
    assert int(summary.get("fallback_dropped_reason_mapped_count") or 0) > 0
    assert int(summary.get("fallback_dropped_reason_unmapped_count") or 0) > 0
    assert summary.get("fallback_reason_incomplete") is False
    assert int(summary.get("drop_by_review_selector_count") or 0) == 0


def test_selector_fallback_after_primary_and_retry_both_invalid() -> None:
    client = _ReplayClient(
        primary_review_response=json.dumps({"summary": "x", "analysis": "x"}, ensure_ascii=False),
        retry_review_response=json.dumps({"summary": "still_invalid"}, ensure_ascii=False),
    )
    result = _run_review_replay(client, case_count=20)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is False
    assert runtime.get("primary_invalid_reason") == "no_mapped_and_no_selection_signal"
    assert runtime.get("retry_invoked") is True
    assert str(runtime.get("retry_reason") or "").strip() != ""
    assert runtime.get("final_source") == "review_selector"
    assert str(runtime.get("applied_reason") or "").strip() != ""
    assert int(summary.get("drop_by_review_selector_count") or 0) > 0


def test_primary_valid_payload_with_dropped_reasons_has_primary_reason_coverage() -> None:
    client = _ReplayClient(
        primary_review_response=_valid_retry_payload(keep_count=6, total_count=14),
        retry_review_response="",
    )
    result = _run_review_replay(client, case_count=14)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is True
    assert runtime.get("final_source") == "primary_llm"
    assert runtime.get("primary_reason_incomplete") is False
    assert int(runtime.get("primary_dropped_reason_count") or 0) > 0
    assert float(runtime.get("primary_reason_coverage_ratio") or 0.0) > 0.0
    assert summary.get("primary_reason_incomplete") is False
    assert int(summary.get("primary_dropped_reason_count") or 0) > 0
    assert float(summary.get("primary_reason_coverage_ratio") or 0.0) > 0.0


def test_primary_valid_payload_without_dropped_reasons_marks_primary_reason_incomplete() -> None:
    client = _ReplayClient(
        primary_review_response=_retry_payload_only_kept(keep_count=6),
        retry_review_response="",
    )
    result = _run_review_replay(client, case_count=14)
    summary = dict((result or {}).get("review_decision_summary") or {})
    runtime = dict(summary.get("review_llm_runtime_debug") or {})

    assert summary.get("review_llm_filter_applied") is True
    assert runtime.get("final_source") == "primary_llm"
    assert runtime.get("primary_reason_incomplete") is True
    assert int(runtime.get("primary_dropped_reason_count") or 0) == 0
    assert float(runtime.get("primary_reason_coverage_ratio") or 0.0) == 0.0
    assert summary.get("primary_reason_incomplete") is True
    assert int(summary.get("primary_dropped_reason_count") or 0) == 0
    assert float(summary.get("primary_reason_coverage_ratio") or 0.0) == 0.0
