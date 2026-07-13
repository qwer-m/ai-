from __future__ import annotations

from typing import Any

from modules.memory_fabric.contracts.memory_context import MemoryContext
from modules.memory_fabric.runtime.diagnostics import init_memory_diag
from modules.test_generation_components.control.feedback_control_report_state import (
    build_from_reports,
    extract_forbidden_patterns,
    extract_quality_hints,
    extract_scenarios_from_text,
)


class _MemoryFabric:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def read_semantic(self, query: dict[str, Any], _ctx: MemoryContext) -> list[dict[str, Any]]:
        self.calls.append(dict(query))
        doc_types = list(query.get("doc_types") or [])
        if doc_types == ["evaluation_report"]:
            return [
                {
                    "doc_type": "evaluation_report",
                    "content": "\n".join(
                        [
                            "RULE-AUTH core login must cover permission error retry boundary max",
                            "must not reuse legacy behavior in shared page",
                            "quality coverage assert duplicate in final cases",
                        ]
                    ),
                }
            ]
        if doc_types == ["agent_learning"]:
            return [
                {
                    "doc_type": "agent_learning",
                    "content": "RULE-AGENT timeout concurrent performance shared flow context leak",
                }
            ]
        return []


def _memory_ctx() -> MemoryContext:
    return MemoryContext.from_runtime(
        user_id=1,
        project_id=2,
        run_id="report-state-test",
        request_id="report-state-test",
    )


def test_report_extractors_keep_chinese_and_english_signals() -> None:
    assert extract_forbidden_patterns("禁止 复用旧链路\nnormal line") == ["禁止 复用旧链路"]
    assert extract_quality_hints("覆盖核心流程并包含可验证 assert") == ["覆盖核心流程并包含可验证 assert"]
    assert extract_scenarios_from_text("权限 error retry boundary timeout") == [
        "权限/鉴权异常场景",
        "失败重试与错误处理场景",
        "边界值与极端输入场景",
        "性能与稳定性场景",
    ]


def test_build_from_reports_reads_memory_fabric_documents() -> None:
    memory_fabric = _MemoryFabric()
    memory_diag = init_memory_diag()

    state = build_from_reports(
        db=object(),
        project_id=2,
        user_id=1,
        include_agent_learning=True,
        memory_fabric=memory_fabric,
        memory_ctx=_memory_ctx(),
        memory_diag=memory_diag,
    )

    assert state.must_cover_rules == ["RULE-AUTH", "RULE-AGENT"]
    assert state.rule_quota == {"RULE-AUTH": 1, "RULE-AGENT": 1}
    assert state.forbidden_patterns == ["must not reuse legacy behavior in shared page"]
    assert state.quality_fix_hints == ["quality coverage assert duplicate in final cases"]
    assert any("legacy_behavior_risk" in item for item in state.reuse_risks)
    assert any("shared_page_residual_risk" in item for item in state.reuse_risks)
    assert state.source_meta["doc_count"] == 2
    assert state.source_meta["doc_types"] == {"evaluation_report": 1, "agent_learning": 1}
    assert [call["doc_types"] for call in memory_fabric.calls] == [
        ["evaluation_report"],
        ["agent_learning"],
    ]
    assert memory_diag["memory_fabric_used"] is True
    assert memory_diag["memory_reads"]["semantic"] == 1


def test_build_from_reports_can_skip_agent_learning() -> None:
    memory_fabric = _MemoryFabric()

    state = build_from_reports(
        db=object(),
        project_id=2,
        user_id=1,
        include_agent_learning=False,
        memory_fabric=memory_fabric,
        memory_ctx=_memory_ctx(),
    )

    assert state.must_cover_rules == ["RULE-AUTH"]
    assert state.source_meta["sources"] == ["evaluation_report"]
    assert [call["doc_types"] for call in memory_fabric.calls] == [["evaluation_report"]]
