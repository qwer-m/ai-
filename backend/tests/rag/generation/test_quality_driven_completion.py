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
from modules.testing.test_generation_components.legacy.stream.persist import (
    LegacyGenerationStreamPersistMixin,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    stream_postprocess_cases,
)


class _NoBackfillClient:
    def __init__(self) -> None:
        self.generate_calls = 0
        self.stream_calls = 0
        self.max_tokens = 4096

    def generate_response(self, requirement: str, prompt: str, db: Any = None, **kwargs) -> str:
        self.generate_calls += 1
        return "[]"

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):
        self.stream_calls += 1
        yield "[]"

    def select_model(self, full_input: str, task_type: str = "generation") -> str:
        return "stub-model"


def _drain_with_return(gen):
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as stop:
            return chunks, stop.value


def _build_case(index: int) -> dict[str, Any]:
    return {
        "id": f"TC-{index:03d}",
        "description": f"高价值边界验证-{index}",
        "test_module": f"module-{(index % 4) + 1}",
        "preconditions": [],
        "steps": [f"执行步骤-{index}"],
        "test_input": f"输入参数-{index}",
        "expected_result": f"结果校验-{index}",
        "priority": "P1",
    }


def test_quality_driven_completion_does_not_fill_to_reference_count() -> None:
    client = _NoBackfillClient()
    full_content = json.dumps([_build_case(i) for i in range(1, 35)], ensure_ascii=False)

    gen = stream_postprocess_cases(
        client=client,
        requirement="",
        base_prompt="BASE",
        kb_context="",
        full_content=full_content,
        expected_count=50,
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
        build_supplement_closed_loop_instruction_fn=lambda **kwargs: "",
        multi_pass=True,
        generation_mode="single_pass",
    )
    _, result = _drain_with_return(gen)

    assert client.generate_calls == 0
    assert client.stream_calls == 0

    final_cases = list((result or {}).get("cases") or [])
    assert len(final_cases) == 34

    summary = dict((result or {}).get("generation_summary") or {})
    assert summary.get("final_count") == 34
    assert summary.get("status") == "completed_with_optimal_set"
    stop_reason = set(summary.get("stop_reason") or [])
    assert "coverage_satisfied" in stop_reason
    assert "stopped_due_to_diminishing_returns" in stop_reason
    assert "optimal_case_set_reached" in stop_reason
    assert all("shortfall" not in str(item).lower() for item in stop_reason)


class _DummyDb:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def add(self, obj: Any) -> None:
        self.rows.append(obj)

    def commit(self) -> None:
        return None


class _PersistRunner(LegacyGenerationStreamPersistMixin):
    def _emit_context_source_log(self, **kwargs) -> None:
        return None


def test_persist_status_reports_normal_completion_and_stop_reasons(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module

    def _fake_stream_postprocess_cases(**kwargs):
        if False:
            yield ""
        return {
            "cases": [_build_case(1)],
            "stage_counts": {"primary": 1, "gap": 0, "review": 1},
            "coverage": {"kind": "coverage_check", "missing_rules": [], "rule_diagnostics": []},
            "convergence_debug": {"converged": True},
            "review_decision_summary": {
                "candidate_total": 1,
                "retained_total": 1,
                "dropped_total": 0,
            },
            "review_decision_table": [
                {
                    "candidate_index": 1,
                    "case_id": "TC-001",
                    "retained_final": True,
                    "dropped_reason": "retained",
                }
            ],
            "generation_summary": {
                "recommended_range": "30-50",
                "final_count": 34,
                "status": "completed_with_optimal_set",
                "stop_reason": [
                    "coverage_satisfied",
                    "stopped_due_to_diminishing_returns",
                    "optimal_case_set_reached",
                ],
                "quality_assessment": "high",
            },
        }

    monkeypatch.setattr(persist_module, "stream_postprocess_cases", _fake_stream_postprocess_cases)

    state = {
        "client": _NoBackfillClient(),
        "requirement": "REQ",
        "project_id": 1,
        "db": _DummyDb(),
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 50,
        "overwrite": False,
        "append": False,
        "user_id": 1001,
        "original_requirement": "REQ",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "existing_entry": None,
        "context_result": {},
        "gate_debug": {},
        "base_prompt": "BASE",
        "full_content": "[]",
        "existing_unique_count": 0,
        "system_prompt": "",
        "current_biz_key": "default",
        "multi_pass": True,
        "generation_mode": "multi_pass",
    }

    runner = _PersistRunner()
    chunks, _ = _drain_with_return(runner._stream_persist_phase(state=state))

    assert any("@@STATUS@@:正常完成" in chunk for chunk in chunks)
    assert any("@@STATUS@@:停止原因：" in chunk for chunk in chunks)
    assert not any("未达到目标数量" in chunk for chunk in chunks)
    assert not any("shortfall" in chunk.lower() for chunk in chunks)

    generation_summary_payload = None
    review_summary_payload = None
    review_table_payload = None
    for chunk in chunks:
        if not chunk.startswith("GEN_DIAG:"):
            continue
        payload = json.loads(chunk.removeprefix("GEN_DIAG:").removesuffix("\\n").strip())
        if payload.get("kind") == "generation_summary":
            generation_summary_payload = payload
        if payload.get("kind") == "review_decision_summary":
            review_summary_payload = payload
        if payload.get("kind") == "review_decision_table":
            review_table_payload = payload

    assert isinstance(generation_summary_payload, dict)
    assert "expected_count" not in generation_summary_payload
    assert isinstance(review_summary_payload, dict)
    assert review_summary_payload.get("candidate_total") == 1
    assert isinstance(review_table_payload, dict)
    assert int(review_table_payload.get("row_count") or 0) == 1


def test_persist_strips_priority_debug_and_uses_final_priority(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module

    def _fake_stream_postprocess_cases(**kwargs):
        if False:
            yield ""
        return {
            "cases": [
                {
                    **_build_case(1),
                    "priority": "P0",
                    "priority_final": "P1",
                    "model_priority_current": "P0",
                    "priority_decision_source": "conflict_resolved_by_core_business_rule",
                }
            ],
            "stage_counts": {"primary": 1, "gap": 0, "review": 1},
            "coverage": {"kind": "coverage_check", "missing_rules": [], "rule_diagnostics": []},
            "generation_summary": {"final_count": 1, "status": "completed"},
        }

    monkeypatch.setattr(persist_module, "stream_postprocess_cases", _fake_stream_postprocess_cases)

    state = {
        "client": _NoBackfillClient(),
        "requirement": "REQ",
        "project_id": 1,
        "db": _DummyDb(),
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 50,
        "overwrite": False,
        "append": False,
        "user_id": 1001,
        "original_requirement": "REQ",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "existing_entry": None,
        "context_result": {},
        "gate_debug": {},
        "base_prompt": "BASE",
        "full_content": "[]",
        "existing_unique_count": 0,
        "system_prompt": "",
        "current_biz_key": "default",
        "multi_pass": True,
        "generation_mode": "multi_pass",
    }

    runner = _PersistRunner()
    _drain_with_return(runner._stream_persist_phase(state=state))

    stored = json.loads(state["db"].rows[0].generated_result)
    assert stored[0]["priority"] == "P1"
    assert "priority_final" not in stored[0]
    assert "model_priority_current" not in stored[0]


def test_persist_recalculates_priority_when_upstream_final_priority_was_stripped(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module

    def _fake_stream_postprocess_cases(**kwargs):
        if False:
            yield ""
        return {
            "cases": [
                {
                    "id": "TC-010",
                    "description": "学员信息表格-课后显示报告和历史记录按钮：课程完成后，信息表格显示「报告」和「历史记录」按钮",
                    "test_module": "学员信息表格",
                    "preconditions": ["选取一名已完成某课程的学员", "该课程有课后报告功能"],
                    "steps": ["1. 进入课程管理页面", "2. 查看学习进度区域"],
                    "test_input": "无",
                    "expected_result": "学习进度区域显示最近完成的课程信息；显示「报告」按钮和「历史记录」按钮；点击后分别跳转到学习报告和学习历史页面。",
                    "priority": "P0",
                }
            ],
            "stage_counts": {"primary": 1, "gap": 0, "review": 1},
            "coverage": {"kind": "coverage_check", "missing_rules": [], "rule_diagnostics": []},
            "generation_summary": {"final_count": 1, "status": "completed"},
        }

    monkeypatch.setattr(persist_module, "stream_postprocess_cases", _fake_stream_postprocess_cases)

    state = {
        "client": _NoBackfillClient(),
        "requirement": "近期课程和排课页面需要展示学习计划、学习报告、历史记录等入口，验证按钮跳转和状态展示。",
        "project_id": 1,
        "db": _DummyDb(),
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 50,
        "overwrite": False,
        "append": False,
        "user_id": 1001,
        "original_requirement": "REQ",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "existing_entry": None,
        "context_result": {},
        "gate_debug": {},
        "base_prompt": "BASE",
        "full_content": "[]",
        "existing_unique_count": 0,
        "system_prompt": "",
        "current_biz_key": "default",
        "multi_pass": True,
        "generation_mode": "multi_pass",
    }

    runner = _PersistRunner()
    _drain_with_return(runner._stream_persist_phase(state=state))

    stored = json.loads(state["db"].rows[0].generated_result)
    assert stored[0]["priority"] == "P1"
    assert "priority_final" not in stored[0]
