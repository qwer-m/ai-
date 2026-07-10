import json
from typing import Any

from modules.test_generation_components.postprocess.json_processing import (
    clean_and_parse_json,
    normalize_json_structure,
)
from modules.test_generation_components.postprocess.streaming_gap_supplement import (
    run_gap_supplement_attempts,
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

    def generate_response_stream(self, *_args, **_kwargs):
        self.stream_calls += 1
        response = self.responses.pop(0) if self.responses else "[]"
        yield response


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


def test_run_gap_supplement_attempts_keeps_only_cases_with_coverage_gain() -> None:
    unrelated = _case("TC-UNRELATED")
    related = {
        **_case("TC-UNICODE"),
        "description": "系统表情支持 Unicode 标准编码",
        "expected_result": "回复输入框可输入 Unicode 系统表情，发布后系统自带表情符号正常显示",
    }
    client = _StreamClient(
        [
            json.dumps([unrelated], ensure_ascii=False),
            json.dumps([related], ensure_ascii=False),
        ]
    )

    _chunks, result, _events = _run_with_keyword_coverage(client)

    assert client.stream_calls == 2
    assert len(result.cases) == 1
    assert "Unicode" in str(result.cases[0].get("expected_result") or "")
    assert result.added_count == 1
    assert result.remaining_gap_count == 0
    assert result.filter_stats[0]["coverage_gain_kept_count"] == 0
    assert result.filter_stats[0]["coverage_gain_dropped_count"] == 1
    assert result.filter_stats[1]["coverage_gain_kept_count"] == 1


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
    assert any("Generation failed" in chunk for chunk in chunks)
    assert any("Error: provider unavailable" in chunk for chunk in chunks)
    assert result.attempt_count == 1
    assert result.stopped_by_provider_error is True
    assert result.stop_reason == "provider_error"
    assert result.remaining_gap_count == 1
    assert events[-2]["attempt_status"] == "provider_error"
    assert events[-1]["stopped_by_provider_error"] is True
