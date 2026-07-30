import json
from typing import Any

from modules.test_generation_components.postprocess.json_processing import (
    clean_and_parse_json,
    normalize_json_structure,
)
from modules.test_generation_components.postprocess.streaming_gap_supplement import (
    build_gap_supplement_request,
    run_gap_supplement_attempts,
)
from modules.test_generation_components.postprocess.result_postprocess_streaming_impl import (
    deduplicate_streaming_candidates,
    preserve_coverage_witnesses_after_semantic_dedup,
)


def _case(case_id: str) -> dict[str, Any]:
    return {
        "id": case_id,
        "test_module": "forum",
        "description": f"{case_id} validates forum publish detail flow",
        "preconditions": ["user is logged in"],
        "steps": ["open forum", "submit post", "check detail"],
        "test_input": f"post data {case_id}",
        "expected_result": f"{case_id} detail page shows published post",
        "priority": "P1",
    }


def _dedupe(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case.get("id") or "")
        if case_id in seen:
            continue
        seen.add(case_id)
        output.append(case)
    return output


def _semantic_candidate(case_id: str, object_index: int) -> dict[str, Any]:
    object_code = f"OBJECT-{object_index:03d}"
    return {
        "id": case_id,
        "test_module": "object_validation",
        "description": f"validate independent object {object_code}",
        "preconditions": [f"object {object_code} exists"],
        "steps": [f"open object {object_code}", f"submit object {object_code}"],
        "test_input": f"payload for {object_code}",
        "expected_result": f"result for {object_code} is persisted",
        "priority": "P1",
        "_semantic": {
            "workflow_stage_candidates": [
                {
                    "workflow_id": "independent_object_validation",
                    "stage_id": object_code,
                    "stage_kind": "verification",
                }
            ]
        },
    }


def _unified_dedupe(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return deduplicate_streaming_candidates(
        cases,
        structural_deduplicate_fn=_dedupe,
    )


def _analyze_coverage(_requirement: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    gap_count = max(0, 2 - len([case for case in cases if isinstance(case, dict)]))
    return {
        "gap_count": gap_count,
        "missing_rules": ["forum_publish_detail"] if gap_count else [],
        "has_missing_types": False,
    }


def _gap_state(coverage: dict[str, Any]) -> dict[str, Any]:
    return {
        "gap_count": int(coverage.get("gap_count") or 0),
        "missing_rules": list(coverage.get("missing_rules") or []),
        "has_missing_types": bool(coverage.get("has_missing_types")),
    }


def _record(events: list[dict[str, Any]]):
    def record(stage: str, _started_at: float, **fields: Any) -> dict[str, Any]:
        payload = {"stage": stage, **fields}
        events.append(payload)
        return payload

    return record


def _drain(gen):
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as stop:
            return chunks, stop.value


class _StreamClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.stream_calls = 0
        self.system_prompts: list[str] = []

    def generate_response_stream(self, *args, **_kwargs):
        self.stream_calls += 1
        self.system_prompts.append(str(args[1]) if len(args) > 1 else "")
        response = self.responses.pop(0) if self.responses else "[]"
        yield response


def _workflow_blueprint() -> dict[str, Any]:
    return {
        "id": "forum_publish_flow",
        "workflow_id": "forum_publish_flow",
        "primary": True,
        "initial_state": "draft",
        "required_stage_ids": ["entry", "submit", "visible"],
        "terminal_states": ["visible"],
        "closure_declaration_complete": True,
        "steps": [
            {
                "id": "entry",
                "label": "Open entry",
                "action": "Open entry",
                "stage_kind": "entry",
                "state_in": "draft",
                "state_out": "editing",
                "path_type": "positive",
                "required": True,
                "terminal": False,
                "critical": True,
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
                "module_candidates": [{"module_key": "forum"}],
                "interaction_ids": [],
                "required_states": [],
                "produced_states": [],
            },
            {
                "id": "submit",
                "label": "Submit post",
                "action": "Submit post",
                "stage_kind": "commit",
                "state_in": "editing",
                "state_out": "submitted",
                "path_type": "positive",
                "required": True,
                "terminal": False,
                "critical": True,
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
                "module_candidates": [{"module_key": "forum"}],
                "interaction_ids": [],
                "required_states": [],
                "produced_states": [],
            },
            {
                "id": "visible",
                "label": "View published post",
                "action": "View published post",
                "stage_kind": "downstream_visibility",
                "state_in": "submitted",
                "state_out": "visible",
                "path_type": "positive",
                "required": True,
                "terminal": True,
                "critical": True,
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
                "module_candidates": [{"module_key": "forum"}],
                "interaction_ids": [],
                "required_states": [],
                "produced_states": [],
            },
        ],
    }


def _workflow_case(case_id: str, stage_id: str) -> dict[str, Any]:
    blueprint = _workflow_blueprint()
    step = next(item for item in blueprint["steps"] if item["id"] == stage_id)
    description = str(step["action"])
    return {
        "id": case_id,
        "test_module": "forum",
        "description": description,
        "preconditions": ["User is logged in"],
        "steps": [description],
        "test_input": f"input for {stage_id}",
        "expected_result": f"The {stage_id} state is observable",
        "priority": "P1",
        "_semantic": {
            "module_candidates": [
                {
                    "module_key": "forum",
                    "module_name": "forum",
                    "role": "primary",
                    "confidence": 0.9,
                    "evidence": [description],
                    "evidence_verified": True,
                }
            ],
            "interaction_ids": [],
            "workflow_stage_candidates": [
                {
                    "workflow_id": "forum_publish_flow",
                    "stage_id": stage_id,
                    "stage_kind": str(step["stage_kind"]),
                    "confidence": 0.9,
                    "evidence": [description],
                    "evidence_verified": True,
                }
            ],
            "precondition_states": [],
            "produced_states": [],
        },
    }


def _project_profile() -> dict[str, Any]:
    return {
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "forum",
                    "module_name": "forum",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                },
                {
                    "module_key": "official",
                    "module_name": "official",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                },
            ],
            "module_interactions": [],
        }
    }


def _run(client: _StreamClient, *, initial_cases: list[dict[str, Any]]):
    events: list[dict[str, Any]] = []
    coverage = _analyze_coverage("forum requirement", initial_cases)
    chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="forum requirement",
            append=False,
            existing_cases=[],
            parsed_result=initial_cases,
            coverage_primary=coverage,
            coverage_gap_state=_gap_state(coverage),
            current_biz_key="forum",
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "fill missing forum coverage",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=_dedupe,
            analyze_coverage_fn=_analyze_coverage,
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
        )
    )
    return chunks, result, events


def _run_with_keyword_coverage(client: _StreamClient):
    events: list[dict[str, Any]] = []

    def analyze(_requirement: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
        covered = any("Unicode" in str(case.get("expected_result") or "") for case in cases)
        return {
            "gap_count": 0 if covered else 1,
            "missing_rules": [] if covered else ["RULE-EMOJI"],
            "has_missing_types": False,
        }

    chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="系统表情支持 Unicode 标准编码",
            append=False,
            existing_cases=[],
            parsed_result=[],
            coverage_primary=analyze("", []),
            coverage_gap_state=_gap_state(analyze("", [])),
            current_biz_key="forum",
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "fill missing emoji coverage",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=_dedupe,
            analyze_coverage_fn=analyze,
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
        )
    )
    return chunks, result, events


def _run_with_required_stage_gap(
    client: _StreamClient,
    *,
    initial_cases: list[dict[str, Any]],
    project_profile: dict[str, Any] | None = None,
    workflow_blueprint: dict[str, Any] | None = None,
):
    events: list[dict[str, Any]] = []
    coverage = {"gap_count": 0, "missing_rules": [], "has_missing_types": False}
    active_blueprint = workflow_blueprint or _workflow_blueprint()
    chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="Forum publish workflow",
            append=False,
            existing_cases=[],
            parsed_result=initial_cases,
            coverage_primary=coverage,
            coverage_gap_state=_gap_state(coverage),
            current_biz_key="forum",
            review_contract_context={"workflow_blueprints": [active_blueprint]},
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "fill exact workflow stage",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=_dedupe,
            analyze_coverage_fn=lambda _requirement, _cases: dict(coverage),
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
            workflow_blueprints=[active_blueprint],
            project_profile=project_profile,
        )
    )
    return chunks, result, events


def _run_with_count_floor(
    client: _StreamClient,
    *,
    initial_cases: list[dict[str, Any]],
    minimum_candidate_count: int,
):
    events: list[dict[str, Any]] = []
    coverage = {"gap_count": 0, "missing_rules": [], "has_missing_types": False}
    chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="forum requirement",
            append=False,
            existing_cases=[],
            parsed_result=initial_cases,
            coverage_primary=coverage,
            coverage_gap_state=_gap_state(coverage),
            current_biz_key="forum",
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "coverage is already complete",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=_dedupe,
            analyze_coverage_fn=lambda _requirement, _cases: dict(coverage),
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
            minimum_candidate_count=minimum_candidate_count,
        )
    )
    return chunks, result, events


def test_run_gap_supplement_attempts_stops_when_coverage_converges() -> None:
    client = _StreamClient([json.dumps([_case("TC-002")], ensure_ascii=False)])

    chunks, result, events = _run(client, initial_cases=[_case("TC-001")])

    assert client.stream_calls == 1
    assert any("Gap supplement attempt #1" in chunk for chunk in chunks)
    assert result.attempt_count == 1
    assert result.added_count == 1
    assert result.remaining_gap_count == 0
    assert result.stop_reason == "coverage_converged"
    assert result.stopped_by_provider_error is False
    assert len(result.filter_stats) == 1
    assert events[-1]["stage"] == "gap_supplement"
    assert events[-1]["added_count"] == 1


def test_gap_target_continues_after_coverage_converges_until_expected_pool_size() -> None:
    client = _StreamClient(
        [
            json.dumps([_case("TC-003"), _case("TC-004")], ensure_ascii=False),
            json.dumps([_case("TC-005")], ensure_ascii=False),
        ]
    )

    _chunks, result, events = _run_with_count_floor(
        client,
        initial_cases=[_case("TC-001"), _case("TC-002")],
        minimum_candidate_count=5,
    )

    assert client.stream_calls == 2
    assert len(result.cases) == 5
    assert result.added_count == 3
    assert result.remaining_gap_count == 0
    assert result.stop_reason == "coverage_converged"
    assert "return exactly 3 additional distinct" in client.system_prompts[0]
    assert "return exactly 1 additional distinct" in client.system_prompts[1]
    assert events[-3]["candidate_count_gap_before"] == 3
    assert events[-3]["candidate_count_gap_after"] == 1
    assert events[-3]["requested_additional_count"] == 3
    assert events[-3]["response_overflow_count"] == 0
    assert events[-2]["candidate_count_gap_before"] == 1
    assert events[-2]["candidate_count_gap_after"] == 0
    assert events[-2]["requested_additional_count"] == 1
    assert events[-1]["remaining_candidate_count_gap"] == 0


def test_semantic_dedup_gap_is_supplemented_before_final_assembly() -> None:
    unique_cases = [
        _semantic_candidate(f"TC-{index:03d}", index)
        for index in range(1, 76)
    ]
    duplicate_cases = [
        {
            **unique_cases[index],
            "id": f"TC-DUP-{index + 1:03d}",
        }
        for index in range(5)
    ]
    initial_cases = _unified_dedupe([*unique_cases, *duplicate_cases])
    initial_unique_count = len(initial_cases)
    supplement_cases = [
        _semantic_candidate(f"TC-{index:03d}", index)
        for index in range(76, 81)
    ]
    client = _StreamClient([json.dumps(supplement_cases, ensure_ascii=False)])
    events: list[dict[str, Any]] = []
    coverage = {"gap_count": 0, "missing_rules": [], "has_missing_types": False}

    _chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="validate independent objects",
            append=False,
            existing_cases=[],
            parsed_result=list(initial_cases),
            coverage_primary=coverage,
            coverage_gap_state=_gap_state(coverage),
            current_biz_key="object_validation",
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "fill current count gap",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=lambda data: data,
            deduplicate_test_cases_fn=_unified_dedupe,
            analyze_coverage_fn=lambda _requirement, _cases: dict(coverage),
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
            minimum_candidate_count=80,
        )
    )

    assert initial_unique_count == 75
    assert client.stream_calls == 1
    assert "return exactly 5 additional distinct" in client.system_prompts[0]
    assert events[-2]["candidate_count_gap_before"] == 5
    assert events[-2]["candidate_count_gap_after"] == 0
    assert result.remaining_gap_count == 0
    assert len(result.cases) == 80
    assert len(_unified_dedupe(result.cases)) == 80


def test_semantic_dedup_preserves_public_coverage_witness_and_merges_stage_contract() -> None:
    coverage_witness = {
        **_case("TC-BOUNDARY"),
        "description": "validate upload record limit",
        "expected_result": "at the 20 record limit the new record is saved and the list stays bounded",
        "_semantic": {"fact_ids": ["FACT-LIMIT"]},
    }
    stage_candidate = {
        **_case("TC-STAGE"),
        "description": "validate upload record limit",
        "expected_result": "the new record is saved",
        "_semantic": {
            "fact_ids": ["FACT-LIMIT"],
            "workflow_stage_candidates": [
                {
                    "workflow_id": "upload_flow",
                    "stage_id": "commit",
                    "stage_kind": "commit",
                    "evidence_verified": True,
                }
            ],
        },
    }

    def analyze(_requirement: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
        text = " ".join(
            str(case.get("expected_result") or "")
            for case in cases
            if isinstance(case, dict)
        )
        coverage_types = ["happy"]
        if "20 record limit" in text:
            coverage_types.append("boundary")
        return {
            "rule_diagnostics": [
                {
                    "rule_id": "RULE-LIMIT",
                    "covered": bool(cases),
                    "coverage_types": coverage_types if cases else [],
                    "missing_types": (
                        [] if "boundary" in coverage_types else ["boundary"]
                    ),
                }
            ]
        }

    preserved = preserve_coverage_witnesses_after_semantic_dedup(
        requirement="upload records are limited to 20",
        source_cases=[coverage_witness, stage_candidate],
        deduplicated_cases=[stage_candidate],
        analyze_coverage_fn=analyze,
    )

    assert len(preserved) == 1
    assert "20 record limit" in str(preserved[0].get("expected_result") or "")
    semantic = dict(preserved[0].get("_semantic") or {})
    assert semantic["fact_ids"] == ["FACT-LIMIT"]
    assert semantic["workflow_stage_candidates"][0]["stage_id"] == "commit"


def test_rule_gap_target_is_used_when_candidate_count_is_already_satisfied() -> None:
    client = _StreamClient(
        [
            json.dumps(
                [_case("TC-003"), _case("TC-004"), _case("TC-005"), _case("TC-006")],
                ensure_ascii=False,
            )
        ]
    )

    _chunks, _result, events = _run(
        client,
        initial_cases=[_case("TC-001")],
    )

    assert "return exactly 1 additional distinct" in client.system_prompts[0]
    assert "smallest high-gain candidate set" in client.system_prompts[0]
    assert "return every contract-valid gap candidate" not in client.system_prompts[0]
    assert events[-2]["requested_additional_count"] == 1
    assert events[-2]["response_overflow_count"] == 3


def test_combined_rule_and_stage_gaps_define_target_count() -> None:
    captured_target: list[str] = []
    client = _StreamClient(["[]", "[]"])
    coverage = {
        "gap_count": 2,
        "missing_rules": ["RULE-1", "RULE-2"],
        "has_missing_types": False,
    }
    events: list[dict[str, Any]] = []

    _chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="Forum publish workflow",
            append=False,
            existing_cases=[],
            parsed_result=[
                _workflow_case("TC-001", "entry"),
                _workflow_case("TC-002", "submit"),
            ],
            coverage_primary=coverage,
            coverage_gap_state=_gap_state(coverage),
            current_biz_key="forum",
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "fill current gaps",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=_dedupe,
            analyze_coverage_fn=lambda _requirement, _cases: dict(coverage),
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
            workflow_blueprints=[_workflow_blueprint()],
        )
    )

    captured_target.extend(client.system_prompts)
    assert result.stopped_by_no_gain is True
    assert "return exactly 3 additional distinct" in captured_target[0]
    assert (
        "rule_gaps=2; type_gap_groups=0; workflow_stage_gaps=1"
        in captured_target[0]
    )
    assert "Cover every non-zero component category" in captured_target[0]
    assert events[0]["requested_additional_count"] == 3


def test_combined_type_and_stage_gap_requires_both_categories_in_one_response() -> None:
    coverage = {
        "gap_count": 1,
        "missing_rules": [],
        "has_missing_types": True,
        "rule_diagnostics": [
            {
                "rule_id": "RULE-BOUNDARY",
                "covered": True,
                "missing_types": ["boundary"],
            }
        ],
    }
    client = _StreamClient(["[]", "[]"])
    events: list[dict[str, Any]] = []

    _chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="Forum publish workflow with a bounded record limit",
            append=False,
            existing_cases=[],
            parsed_result=[
                _workflow_case("TC-001", "entry"),
                _workflow_case("TC-002", "submit"),
            ],
            coverage_primary=coverage,
            coverage_gap_state=_gap_state(coverage),
            current_biz_key="forum",
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "fill current gaps",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=_dedupe,
            analyze_coverage_fn=lambda _requirement, _cases: dict(coverage),
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
            workflow_blueprints=[_workflow_blueprint()],
        )
    )

    assert result.stopped_by_no_gain is True
    assert "return exactly 2 additional distinct" in client.system_prompts[0]
    assert (
        "rule_gaps=0; type_gap_groups=1; workflow_stage_gaps=1"
        in client.system_prompts[0]
    )
    assert "Do not spend all GAP_TARGET_COUNT slots on workflow-stage candidates" in (
        client.system_prompts[0]
    )


def test_candidate_count_gap_takes_priority_over_smaller_rule_gap() -> None:
    client = _StreamClient(
        [json.dumps([_case(f"TC-{index:03d}") for index in range(2, 9)], ensure_ascii=False)]
    )
    events: list[dict[str, Any]] = []

    _chunks, result = _drain(
        run_gap_supplement_attempts(
            client=client,
            requirement="forum requirement",
            append=False,
            existing_cases=[],
            parsed_result=[_case("TC-001")],
            coverage_primary=_analyze_coverage("", [_case("TC-001")]),
            coverage_gap_state=_gap_state(_analyze_coverage("", [_case("TC-001")])),
            current_biz_key="forum",
            infer_case_kind_fn=lambda _case: "functional",
            build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "avoid duplicates",
            build_gap_fill_prompt_fn=lambda **_kwargs: "fill current gaps",
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=_dedupe,
            analyze_coverage_fn=_analyze_coverage,
            resolve_coverage_gap_state_fn=_gap_state,
            record_timing_event_fn=_record(events),
            minimum_candidate_count=8,
        )
    )

    assert result.remaining_gap_count == 0
    assert "return exactly 7 additional distinct" in client.system_prompts[0]
    assert events[-2]["requested_additional_count"] == 7
    assert events[-2]["response_overflow_count"] == 0


def test_gap_stage_is_skipped_when_no_gap_exists() -> None:
    client = _StreamClient([json.dumps([_case("TC-003")], ensure_ascii=False)])

    _chunks, result, events = _run(
        client,
        initial_cases=[_case("TC-001"), _case("TC-002")],
    )

    assert client.stream_calls == 0
    assert result.attempt_count == 0
    assert result.added_count == 0
    assert result.remaining_gap_count == 0
    assert len(events) == 1


def test_run_gap_supplement_attempts_forwards_entire_valid_candidate_set() -> None:
    unrelated = _case("TC-UNRELATED")
    related = {
        **_case("TC-UNICODE"),
        "description": "系统表情支持 Unicode 标准编码",
        "expected_result": "回复输入框可输入 Unicode 系统表情，发布后系统自带表情符号正常显示",
    }
    client = _StreamClient(
        [
            json.dumps([related, unrelated], ensure_ascii=False),
        ]
    )

    _chunks, result, events = _run_with_keyword_coverage(client)

    assert client.stream_calls == 1
    assert len(result.cases) == 2
    assert "Unicode" in str(result.cases[0].get("expected_result") or "")
    assert "TC-UNRELATED" in str(result.cases[1].get("description") or "")
    assert result.added_count == 2
    assert result.remaining_gap_count == 0
    assert result.filter_stats[0]["coverage_gain_candidate_count"] == 2
    assert result.filter_stats[0]["coverage_gain_forwarded_count"] == 2
    assert result.filter_stats[0]["coverage_gain_kept_count"] == 2
    assert result.filter_stats[0]["coverage_gain_dropped_count"] == 0
    assert result.filter_stats[0]["coverage_gain_gap_reduction"] == 1
    assert events[-2]["candidate_set_coverage_gain"]["coverage_gain_forwarded_count"] == 2


def test_run_gap_supplement_attempts_does_not_stream_raw_candidates() -> None:
    candidate = {
        **_case("TC-PRIVATE"),
        "expected_result": "Unicode candidate remains internal until unified Review",
        "_semantic": {"marker": "private-semantic-marker"},
    }
    raw_response = json.dumps([candidate], ensure_ascii=False)
    client = _StreamClient([raw_response])

    chunks, result, _events = _run_with_keyword_coverage(client)

    streamed_text = "".join(chunks)
    assert "TC-PRIVATE" not in streamed_text
    assert "_semantic" not in streamed_text
    assert "private-semantic-marker" not in streamed_text
    assert any("TC-PRIVATE" in str(case.get("description") or "") for case in result.cases)


def test_build_gap_supplement_request_forwards_full_review_contract() -> None:
    captured_kwargs: dict[str, Any] = {}
    review_contract_context = {
        "modules": [{"module_key": "official"}],
        "interactions": [{"interaction_id": "official_to_message"}],
        "workflows": [{"workflow_id": "publish_notice", "primary": True}],
        "states": [{"entity": "notice", "state": "published"}],
    }

    def build_gap_prompt(**kwargs: Any) -> str:
        captured_kwargs.update(kwargs)
        return "gap prompt"

    request = build_gap_supplement_request(
        requirement="requirement",
        append=False,
        existing_cases=[],
        parsed_result=[_case("TC-001")],
        coverage_primary={"missing_rules": ["RULE-1"]},
        missing_rules=["RULE-1"],
        current_biz_key="forum",
        review_contract_context=review_contract_context,
        infer_case_kind_fn=lambda _case: "functional",
        build_supplement_closed_loop_instruction_fn=lambda **_kwargs: "closed loop",
        build_gap_fill_prompt_fn=build_gap_prompt,
    )

    assert request.system_prompt
    assert captured_kwargs["review_contract_context"] == review_contract_context
    assert captured_kwargs["review_contract_context"] is not review_contract_context


def test_run_gap_supplement_attempts_stops_after_two_no_gain_attempts() -> None:
    client = _StreamClient(["[]", "[]", "[]"])

    chunks, result, events = _run(client, initial_cases=[_case("TC-001")])

    assert client.stream_calls == 2
    assert any("Gap supplement stopped after 2 no-gain attempts" in chunk for chunk in chunks)
    assert result.attempt_count == 2
    assert result.added_count == 0
    assert result.stopped_by_no_gain is True
    assert result.stop_reason == "no_gain_streak"
    assert events[-1]["stopped_by_no_gain"] is True
    assert events[-2]["stop_reason"] == "no_gain_streak"


def test_run_gap_supplement_attempts_stops_on_provider_error() -> None:
    client = _StreamClient(["Error: provider unavailable"])

    chunks, result, events = _run(client, initial_cases=[_case("TC-001")])

    assert client.stream_calls == 1
    assert any("generation failed" in chunk.lower() for chunk in chunks)
    assert all("Error: provider unavailable" not in chunk for chunk in chunks)
    assert result.attempt_count == 1
    assert result.stopped_by_provider_error is True
    assert result.stop_reason == "provider_error"
    assert result.remaining_gap_count == 1
    assert events[-2]["attempt_status"] == "provider_error"
    assert events[-1]["stopped_by_provider_error"] is True


def test_required_stage_gap_starts_when_rule_and_count_gap_are_zero() -> None:
    client = _StreamClient(
        [json.dumps([_workflow_case("TC-003", "visible")], ensure_ascii=False)]
    )

    chunks, result, events = _run_with_required_stage_gap(
        client,
        initial_cases=[
            _workflow_case("TC-001", "entry"),
            _workflow_case("TC-002", "submit"),
        ],
    )

    assert client.stream_calls == 1
    assert any("Gap supplement attempt #1" in chunk for chunk in chunks)
    assert result.required_stage_coverage["required_stage_coverage_complete"] is True
    assert result.remaining_gap_count == 0
    assert events[-2]["required_stage_gap_reduction"] == 1


def test_invalid_blueprint_typed_state_chain_does_not_start_gap_generation() -> None:
    blueprint = _workflow_blueprint()
    blueprint["steps"][0]["required_states"] = [
        {
            "entity": "document",
            "state": "ready",
            "source": "previous_stage",
            "scope": "workflow",
            "polarity": "positive",
            "temporal": "after_previous_stage",
            "evidence_verified": True,
            "confidence": 0.9,
        }
    ]
    client = _StreamClient([])

    _chunks, result, _events = _run_with_required_stage_gap(
        client,
        initial_cases=[_workflow_case("TC-001", "entry")],
        workflow_blueprint=blueprint,
    )

    assert client.stream_calls == 0
    assert result.attempt_count == 0
    assert result.required_stage_coverage["source_generation_allowed"] is False
    assert result.required_stage_coverage["actionable_stage_ids"] == []
    assert result.required_stage_coverage["failure_reason"] == (
        "workflow_blueprint_typed_state_chain_invalid"
    )


def test_required_stage_gap_no_gain_ignores_unrelated_new_case_count() -> None:
    client = _StreamClient(
        [
            json.dumps([_case("TC-010")], ensure_ascii=False),
            json.dumps([_case("TC-011")], ensure_ascii=False),
        ]
    )

    _chunks, result, events = _run_with_required_stage_gap(
        client,
        initial_cases=[
            _workflow_case("TC-001", "entry"),
            _workflow_case("TC-002", "submit"),
        ],
    )

    assert client.stream_calls == 2
    assert result.added_count == 2
    assert result.stopped_by_no_gain is True
    assert result.remaining_gap_count == 1
    assert events[-2]["required_stage_gap_reduction"] == 0
    assert events[-2]["no_gain_streak"] == 2


def test_gap_rechecks_stage_coverage_after_functional_module_contract() -> None:
    mismatched_visible = _workflow_case("TC-003", "visible")
    mismatched_visible["test_module"] = "official"
    client = _StreamClient(
        [
            json.dumps([mismatched_visible], ensure_ascii=False),
            json.dumps([mismatched_visible], ensure_ascii=False),
        ]
    )

    _chunks, result, events = _run_with_required_stage_gap(
        client,
        initial_cases=[
            _workflow_case("TC-001", "entry"),
            _workflow_case("TC-002", "submit"),
        ],
        project_profile=_project_profile(),
    )

    assert client.stream_calls == 2
    assert result.module_contract_rejected_count == 2
    assert result.required_stage_coverage["required_stage_coverage_complete"] is False
    assert result.required_stage_coverage["actionable_stage_ids"] == ["visible"]
    assert result.remaining_gap_count == 1
    assert events[-2]["stage_gap_after"] == 1
    assert all(item.get("id") != "TC-003" for item in result.cases)
