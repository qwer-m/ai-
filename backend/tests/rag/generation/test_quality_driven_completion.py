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
    assert 0 < len(final_cases) < 50
    assert len(final_cases) <= 34

    summary = dict((result or {}).get("generation_summary") or {})
    assert summary.get("final_count") == len(final_cases)
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


class _FailingPersistDb(_DummyDb):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    def commit(self) -> None:
        has_generation_row = any(getattr(row, "generated_result", None) for row in self.rows)
        if has_generation_row and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("simulated generation insert failure")
        return None

    def rollback(self) -> None:
        self.rows.clear()


class _EmptyQuery:
    def filter(self, *args: Any, **kwargs: Any) -> "_EmptyQuery":
        return self

    def order_by(self, *args: Any, **kwargs: Any) -> "_EmptyQuery":
        return self

    def first(self) -> None:
        return None


class _RejectLazyModelDb(_DummyDb):
    def __init__(self) -> None:
        super().__init__()
        self.query_model: Any = None

    def query(self, model: Any) -> _EmptyQuery:
        from modules.testing.test_generation_components.legacy.stream.runtime import LazyAttrProxy

        assert not isinstance(model, LazyAttrProxy)
        self.query_model = model
        return _EmptyQuery()

    def rollback(self) -> None:
        return None


def _stored_generation_result(db: _DummyDb) -> str:
    for row in db.rows:
        generated_result = getattr(row, "generated_result", None)
        if generated_result:
            return str(generated_result)
    raise AssertionError("generated_result was not persisted")


def test_persist_status_reports_normal_completion_and_stop_reasons(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module
    monkeypatch.setattr(persist_module.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

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


def test_persist_emits_generation_timing_ledger_from_state_and_postprocess_events(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module

    monkeypatch.setattr(persist_module.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    def _fake_stream_postprocess_cases(**kwargs):
        if False:
            yield ""
        return {
            "cases": [_build_case(1)],
            "stage_counts": {"primary": 1, "gap": 0, "review": 1},
            "coverage": {"kind": "coverage_check", "missing_rules": [], "rule_diagnostics": []},
            "generation_summary": {"final_count": 1, "status": "completed"},
            "timing_events": [
                {"stage": "gap_supplement", "duration_ms": 7},
                {"stage": "review_selection", "duration_ms": 11},
                {"stage": "final_shortfall_supplement", "duration_ms": 13},
                {"stage": "postprocess_total", "duration_ms": 31},
            ],
        }

    monkeypatch.setattr(persist_module, "stream_postprocess_cases", _fake_stream_postprocess_cases)

    db = _DummyDb()
    state = {
        "client": _NoBackfillClient(),
        "requirement": "REQ",
        "project_id": 1,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 20,
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
        "generation_timing_events": [
            {"stage": "prepare_total", "duration_ms": 101},
            {"stage": "snapshot_gate", "duration_ms": 2},
            {"stage": "hybrid_context", "duration_ms": 17},
            {"stage": "feedback_control_state", "duration_ms": 5},
            {"stage": "current_requirement_blueprint", "duration_ms": 19},
            {"stage": "requirement_compress", "duration_ms": 23},
            {"stage": "long_requirement_compress", "duration_ms": 29},
            {"stage": "kb_context_compress", "duration_ms": 31},
            {"stage": "meta_analysis", "duration_ms": 3},
            {"stage": "primary_batches", "duration_ms": 37},
            {"stage": "stream_generation_phase", "duration_ms": 43},
        ],
    }

    runner = _PersistRunner()
    chunks, _ = _drain_with_return(runner._stream_persist_phase(state=state))

    timing_payload = None
    for chunk in chunks:
        if not chunk.startswith("GEN_DIAG:"):
            continue
        payload = json.loads(chunk.removeprefix("GEN_DIAG:").removesuffix("\n").strip())
        if payload.get("kind") == "generation_timing_ledger":
            timing_payload = payload
            break

    assert isinstance(timing_payload, dict)
    durations = timing_payload.get("duration_by_stage_ms")
    assert durations == {
        "prepare_total": 101,
        "client_resolution": 0,
        "linked_final_case_signal": 0,
        "append_existing_lookup": 0,
        "snapshot_gate": 2,
        "hybrid_context": 17,
        "feedback_control_state": 5,
        "current_requirement_blueprint": 19,
        "requirement_compress": 52,
        "kb_context_compress": 31,
        "meta_analysis": 3,
        "primary": 37,
        "stream_generation_phase": 43,
        "gap": 7,
        "review": 11,
        "final_shortfall": 13,
        "postprocess_total": 31,
    }
    assert timing_payload.get("event_count") == 15


def test_persist_exception_emits_diagnostic_instead_of_silent_completion(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module

    monkeypatch.setattr(persist_module.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    def _fake_stream_postprocess_cases(**kwargs):
        if False:
            yield ""
        return {
            "cases": [_build_case(1)],
            "stage_counts": {"primary": 1, "gap": 0, "review": 1},
            "coverage": {"kind": "coverage_check", "missing_rules": [], "rule_diagnostics": []},
            "generation_summary": {"final_count": 1, "status": "completed"},
        }

    monkeypatch.setattr(persist_module, "stream_postprocess_cases", _fake_stream_postprocess_cases)

    db = _FailingPersistDb()
    state = {
        "client": _NoBackfillClient(),
        "requirement": "REQ",
        "project_id": 1,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 20,
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

    chunks, _ = _drain_with_return(_PersistRunner()._stream_persist_phase(state=state))

    assert any("stream_persist_exception" in chunk for chunk in chunks)
    assert any("STREAM_PERSISTENCE_FAILED" in chunk for chunk in chunks)
    assert any("生成结果落库失败" in chunk for chunk in chunks)


def test_persist_overwrite_resolves_lazy_model_before_query(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module

    monkeypatch.setattr(persist_module.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    def _fake_stream_postprocess_cases(**kwargs):
        if False:
            yield ""
        return {
            "cases": [_build_case(1)],
            "stage_counts": {"primary": 1, "gap": 0, "review": 1},
            "coverage": {"kind": "coverage_check", "missing_rules": [], "rule_diagnostics": []},
            "generation_summary": {"final_count": 1, "status": "completed"},
        }

    monkeypatch.setattr(persist_module, "stream_postprocess_cases", _fake_stream_postprocess_cases)

    db = _RejectLazyModelDb()
    state = {
        "client": _NoBackfillClient(),
        "requirement": "REQ",
        "project_id": 1,
        "db": db,
        "doc_type": "requirement",
        "compress": False,
        "expected_count": 20,
        "overwrite": True,
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

    chunks, _ = _drain_with_return(_PersistRunner()._stream_persist_phase(state=state))

    assert getattr(db.query_model, "__name__", "") == "TestGeneration"
    assert "TC-001" in _stored_generation_result(db)
    assert not any("STREAM_PERSISTENCE_FAILED" in chunk for chunk in chunks)


def test_persist_strips_priority_debug_and_uses_final_priority(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module
    monkeypatch.setattr(persist_module.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

    def _fake_stream_postprocess_cases(**kwargs):
        if False:
            yield ""
        return {
            "cases": [
                {
                    **_build_case(1),
                    "priority": "P0",
                    "priority_final": "P1",
                    "execution_group": "main_smoke",
                    "execution_sequence": 1,
                    "depends_on": [],
                    "fixture_key": "workflow_seed",
                    "group_setup": "seed_workflow_dataset()",
                    "group_teardown": "cleanup_workflow_dataset()",
                    "main_chain_stage_kind": "commit",
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

    stored = json.loads(_stored_generation_result(state["db"]))
    assert stored[0]["priority"] == "P1"
    assert stored[0]["priority_final"] == "P1"
    assert stored[0]["execution_group"] == "main_smoke"
    assert stored[0]["execution_sequence"] == 1
    assert stored[0]["depends_on"] == []
    assert stored[0]["fixture_key"] == "workflow_seed"
    assert stored[0]["group_setup"] == "seed_workflow_dataset()"
    assert stored[0]["group_teardown"] == "cleanup_workflow_dataset()"
    assert stored[0]["main_chain_stage_kind"] == "commit"
    assert "model_priority_current" not in stored[0]
    assert "priority_decision_source" not in stored[0]


def test_persist_recalculates_priority_when_upstream_final_priority_was_stripped(monkeypatch) -> None:
    from modules.testing.test_generation_components.legacy.stream import persist as persist_module
    monkeypatch.setattr(persist_module.settings, "EXECUTION_PLAN_GATE_MODE", "shadow", raising=False)

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

    stored = json.loads(_stored_generation_result(state["db"]))
    assert stored[0]["priority"] == "P1"
    assert stored[0]["priority_final"] == "P1"
