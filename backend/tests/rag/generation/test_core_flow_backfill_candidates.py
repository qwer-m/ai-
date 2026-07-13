from __future__ import annotations

from modules.test_generation_components.coverage.core_flow_backfill_candidates import (
    accept_backfill_candidate_cases,
    normalize_backfill_candidate_cases,
    safe_json_array,
)
from modules.test_generation_components.coverage.core_flow_coverage_contract import CORE_FLOWS


def _flow_text(flow_id: str) -> str:
    flow = next(item for item in CORE_FLOWS if item["flow_id"] == flow_id)
    required = list(flow.get("require_all") or [])
    optional = list(flow.get("require_any") or [])
    return " ".join([*required, *optional[:1]]).strip()


def test_safe_json_array_accepts_cases_wrapper_in_code_fence() -> None:
    raw = '```json\n{"cases": [{"case_id": "BF-RAW", "description": "raw case"}]}\n```'

    parsed = safe_json_array(raw)

    assert parsed == [{"case_id": "BF-RAW", "description": "raw case"}]


def test_normalize_backfill_candidate_cases_uses_alias_fields_and_plan_flow_name() -> None:
    rows = normalize_backfill_candidate_cases(
        [
            {
                "title": "未付费用户触发付费拦截",
                "module": "付费拦截",
                "testSteps": ["1. 点击学习入口", "2. 观察拦截弹窗"],
                "expectedResult": "接口字段 status=paid_gate 且不创建学习任务记录",
                "priority": "p0",
                "source_flow_key": "paid_gate",
            }
        ],
        {"paid_gate": "付费拦截"},
    )

    assert len(rows) == 1
    assert rows[0]["case_id"] == "BF-001"
    assert rows[0]["id"] == "BF-001"
    assert rows[0]["description"] == "未付费用户触发付费拦截"
    assert rows[0]["test_module"] == "付费拦截"
    assert rows[0]["steps"] == ["1. 点击学习入口", "2. 观察拦截弹窗"]
    assert rows[0]["priority"] == "P0"
    assert rows[0]["source_flow_name"] == "付费拦截"
    assert rows[0]["expected_result_quality"] == "assertable"


def test_normalize_backfill_candidate_cases_keeps_missing_required_stub() -> None:
    rows = normalize_backfill_candidate_cases(
        [{"source_flow_key": "paid_gate", "source_flow_name": "付费拦截"}],
        {"paid_gate": "付费拦截"},
    )

    assert rows == [
        {
            "case_id": "BF-001",
            "raw_case": {"source_flow_key": "paid_gate", "source_flow_name": "付费拦截"},
            "source_flow_key": "paid_gate",
            "source_flow_name": "付费拦截",
            "backfill_generated": True,
            "missing_required_fields": True,
        }
    ]


def test_accept_backfill_candidate_cases_rejects_duplicate_before_mapper() -> None:
    existing = [
        {
            "description": "普通页面加载",
            "test_module": "基础模块",
            "steps": ["1. 打开页面", "2. 查看结果"],
            "test_input": "default",
            "expected_result": "接口字段 status=ready",
        }
    ]
    candidate = {
        **existing[0],
        "id": "BF-001",
        "case_id": "BF-001",
        "source_flow_key": "paid_gate",
        "priority_final": "P1",
        "expected_result_quality": "assertable",
    }

    result = accept_backfill_candidate_cases([candidate], existing)

    assert result["accepted_cases"] == []
    assert len(result["rejected_cases"]) == 1
    assert result["rejected_cases"][0]["rejection_reason"] == "duplicate_with_existing_case"


def test_accept_backfill_candidate_cases_accepts_real_mapper_hit() -> None:
    flow_id = "paid_gate"
    flow_text = _flow_text(flow_id)
    candidate = {
        "id": "BF-001",
        "case_id": "BF-001",
        "description": f"{flow_text} 主链路覆盖",
        "test_module": flow_text,
        "steps": ["1. 点击学习入口", "2. 观察拦截结果"],
        "test_input": "unpaid account",
        "expected_result": "接口字段 status=paid_gate 且不创建学习任务记录",
        "source_flow_key": flow_id,
        "priority_final": "P0",
        "expected_result_quality": "assertable",
    }

    result = accept_backfill_candidate_cases([candidate], [])

    assert result["rejected_cases"] == []
    assert len(result["accepted_cases"]) == 1
    assert result["accepted_cases"][0]["matched_core_flows"] == [flow_id]
    assert flow_id in result["accepted_cases"][0]["mapper_hits"]
