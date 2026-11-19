"""上下文来源契约的纯单元测试。

测试只验证数据边界和确定性选择，不连接数据库、向量库或外部模型。
"""

import pytest

from modules.knowledge_base_components.ports.context_memory_port import (
    ContextBudget,
    ContextCandidate,
    ContextQuery,
    ContextSourceRef,
    ShortTermStatePort,
    assemble_context,
    render_selected_context,
)
from modules.knowledge_base_components.context.context_provider import (
    ShortTermStateContextSourcePort,
)
from modules.knowledge_base_components.context.context_requests import (
    ContextRecordCreateRequest,
)


def _candidate(
    *,
    source_kind: str = "project_knowledge",
    source_id: str = "doc-1",
    scope: str = "project",
    scope_id: int | str | None = 7,
    status: str = "available",
    text: str = "项目验收规则",
    score: float = 0.8,
) -> ContextCandidate:
    return ContextCandidate(
        source=ContextSourceRef(
            source_kind=source_kind,
            source_id=source_id,
            scope=scope,
            scope_id=scope_id,
            revision="asset-v3",
            content_hash="a" * 64,
            status=status,
        ),
        text=text,
        score=score,
        evidence={"retrieval_route": "original_raw"},
    )


def test_assemble_context_preserves_provenance_and_rejection_reasons() -> None:
    query = ContextQuery(
        text="如何验收登录流程",
        user_id=11,
        project_id=7,
        source_kinds=("project_knowledge", "long_term_memory"),
        max_candidates=3,
    )
    manifest = assemble_context(
        query=query,
        candidates=(
            _candidate(source_id="doc-1", score=0.9),
            _candidate(source_id="other-project", scope_id=8, score=0.85),
            _candidate(source_id="revoked-doc", status="revoked", score=0.7),
            _candidate(
                source_kind="long_term_memory",
                source_id="memory-1",
                scope="user",
                scope_id=11,
                status="unavailable",
                text="",
                score=0.6,
            ),
        ),
        budget=ContextBudget(max_candidates=3, max_chars=100),
        unavailable_source_kinds=("long_term_memory",),
    )

    assert manifest.status == "partial"
    assert [item.source.source_id for item in manifest.selected] == ["doc-1"]
    assert {item.source.source_id: item.reason for item in manifest.rejected} == {
        "other-project": "project_scope_mismatch",
        "revoked-doc": "revoked",
        "memory-1": "unavailable",
    }
    assert manifest.selected[0].source.revision == "asset-v3"
    assert manifest.selected[0].source.content_hash == "a" * 64
    assert manifest.to_dict()["selected"][0].get("text") is None
    assert manifest.unavailable_source_kinds == ("long_term_memory", "project_knowledge")
    assert render_selected_context(manifest) == "项目验收规则"


def test_unavailable_long_term_memory_is_explicit() -> None:
    manifest = assemble_context(
        query=ContextQuery(
            text="读取历史反馈",
            user_id=11,
            project_id=7,
            source_kinds=("long_term_memory",),
        ),
        candidates=(),
        budget=ContextBudget(max_candidates=2, max_chars=100),
        unavailable_source_kinds=("long_term_memory",),
    )

    assert manifest.status == "unavailable"
    assert manifest.selected == ()
    assert manifest.rejected == ()
    assert manifest.unavailable_source_kinds == ("long_term_memory",)


def test_short_term_port_matches_bound_action_journal_shape() -> None:
    """短期上下文只展开当前领域状态，不加载整份动作审计日志。"""

    from modules.agent_platform.action_journal import AgentActionJournal

    assert hasattr(AgentActionJournal, "load_state")
    assert hasattr(ShortTermStatePort, "load_state")
    assert hasattr(AgentActionJournal, "init")


def test_short_term_context_source_returns_current_state_with_identity() -> None:
    source = ShortTermStateContextSourcePort(
        lambda: {"pending_scope": "source-1", "revision": 3},
        run_id=19,
        node_key="resolve_generation",
        user_id=11,
        project_id=7,
    )
    candidates = source.retrieve(
        ContextQuery(
            text="当前待处理范围",
            user_id=11,
            project_id=7,
            source_kinds=("short_term_state",),
        ), budget=ContextBudget(),
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source.source_id == "run:19:node:resolve_generation"
    assert candidate.source.scope == "project"
    assert candidate.source.scope_id == 7
    assert candidate.evidence["run_id"] == 19
    assert '"pending_scope":"source-1"' in candidate.text


def test_unavailable_candidate_promotes_source_status() -> None:
    manifest = assemble_context(
        query=ContextQuery(
            text="读取历史反馈",
            user_id=11,
            project_id=7,
            source_kinds=("long_term_memory",),
        ),
        candidates=(
            _candidate(
                source_kind="long_term_memory",
                source_id="memory-adapter",
                scope="user",
                scope_id=11,
                status="unavailable",
                text="",
            ),
        ),
        budget=ContextBudget(),
    )

    assert manifest.status == "unavailable"
    assert manifest.unavailable_source_kinds == ("long_term_memory",)
    assert manifest.rejected[0].reason == "unavailable"


def test_budget_rejection_is_deterministic() -> None:
    manifest = assemble_context(
        query=ContextQuery(text="读取项目规则", project_id=7, max_candidates=2),
        candidates=(
            _candidate(source_id="first", text="12345", score=0.9),
            _candidate(source_id="second", text="67890", score=0.8),
            _candidate(source_id="third", text="too-long", score=0.7),
        ),
        budget=ContextBudget(max_candidates=2, max_chars=8),
        unavailable_source_kinds=(),
    )

    assert [item.source.source_id for item in manifest.selected] == ["first"]
    assert [(item.source.source_id, item.reason) for item in manifest.rejected] == [
        ("second", "character_budget_exceeded"),
        ("third", "character_budget_exceeded"),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_kind", "unknown"),
        ("scope", "session"),
        ("status", "deleted"),
    ],
)
def test_source_reference_rejects_unknown_contract_values(field: str, value: str) -> None:
    kwargs = {
        "source_kind": "project_knowledge",
        "source_id": "doc-1",
        "scope": "project",
        "scope_id": 7,
        "status": "available",
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        ContextSourceRef(**kwargs)


def test_validated_feedback_is_project_scoped() -> None:
    common = {
        "source_kind": "validated_feedback",
        "revision": "run-15-source-v1",
        "content": {"text": "人工确认该来源支持当前结果"},
        "evidence_refs": [{"run_id": 15, "source_id": "document:283:chunk:chunk_1"}],
    }

    record = ContextRecordCreateRequest(
        project_id=9,
        scope="project",
        scope_id="9",
        **common,
    )
    assert record.scope == "project"
    assert record.scope_id == "9"

    with pytest.raises(ValueError, match="只能按项目作用域保存"):
        ContextRecordCreateRequest(
            project_id=9,
            scope="user",
            scope_id="1",
            **common,
        )
    with pytest.raises(ValueError, match="scope_id 必须等于 project_id"):
        ContextRecordCreateRequest(
            project_id=9,
            scope="project",
            scope_id="8",
            **common,
        )

