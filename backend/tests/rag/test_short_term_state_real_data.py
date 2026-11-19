"""使用真实 Run 48 固化的输入验证短期状态分片，不构造业务规则或人工反馈。"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from modules.knowledge_base_components.context.context_provider import (
    OnDemandContextProvider, ShortTermStateContextSourcePort,
)
from modules.knowledge_base_components.ports.context_memory_port import (
    ContextBudget, ContextQuery, assemble_context, render_selected_context,
)


@pytest.fixture
def actual_input():
    fixture = Path(__file__).parents[1] / "agent_platform/fixtures/run48_generation_batching_real.json"
    return json.loads(fixture.read_text(encoding="utf-8"))["input_payload"]


def _at_pointer(value, pointer):
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _source(state):
    return ShortTermStateContextSourcePort(lambda: state, run_id=48, node_key="resolve_generation",
                                           user_id=1, project_id=9)


@pytest.mark.parametrize("max_chars", [4000, 8000, 20000])
def test_real_large_state_returns_complete_matching_units(actual_input, max_chars):
    source = _source(actual_input)
    query = ContextQuery(text=actual_input["effective_facts"][0]["assertion"], user_id=1, project_id=9,
                         source_kinds=("short_term_state",), max_candidates=5)
    bundle = OnDemandContextProvider({source.source_kind: source}).retrieve(
        query=query, budget=ContextBudget(max_candidates=5, max_chars=max_chars),
    )
    assert bundle.manifest.status == "ready"
    assert len(source._encode(actual_input)) > max_chars
    assert len(bundle.rendered_text) <= max_chars
    fragments = [item for item in bundle.manifest.selected
                 if item.evidence["fragment_kind"] == "complete_structured_unit"]
    assert fragments
    assert any(actual_input["effective_facts"][0]["assertion"] in item.text for item in fragments)
    for item in fragments:
        assert json.loads(item.text)["value"] == _at_pointer(actual_input, item.evidence["state_path"])
        assert item.evidence["state_hash"] == hashlib.sha256(source._encode(actual_input).encode()).hexdigest()
        assert item.source.content_hash == hashlib.sha256(item.text.encode()).hexdigest()
    assert len({item.evidence["unit_hash"] for item in fragments}) == len(fragments)


def test_real_rule_too_large_for_budget_is_never_truncated(actual_input):
    source = _source(actual_input)
    candidates = source.retrieve(
        ContextQuery(text=actual_input["effective_facts"][0]["assertion"], user_id=1, project_id=9,
                     source_kinds=("short_term_state",)),
        budget=ContextBudget(max_chars=1),
    )
    assert all(item.evidence["fragment_kind"] == "structure_index" for item in candidates)
    assert all(item.evidence["fragment_kind"] != "complete_structured_unit" for item in candidates)


def test_safe_projection_preserves_actual_business_input(actual_input):
    projected, excluded = ShortTermStateContextSourcePort._safe_state(actual_input)
    assert projected == actual_input
    assert excluded == 0


@pytest.mark.parametrize("field", ["password", "api_key", "authorization", "accessToken", "密码"])
def test_credential_field_is_never_exposed(field, actual_input):
    # 只把真实内容放入凭据字段验证结构隔离，不制造凭据或业务内容。
    projected, excluded = ShortTermStateContextSourcePort._safe_state({field: actual_input})
    assert projected == {}
    assert excluded == 1


def test_business_rule_field_is_not_treated_as_a_credential(actual_input):
    projected, excluded = ShortTermStateContextSourcePort._safe_state({"authorization_rules": actual_input})
    assert projected == {"authorization_rules": actual_input}
    assert excluded == 0


def test_manifest_history_survives_actual_state_round_trip(actual_input):
    from modules.agent_platform.context_agent_tools import retrieve_context
    from modules.agent_platform.registry import ToolExecutionContext

    def context(state):
        source = _source(state)
        return ToolExecutionContext(db=None, user_id=1, project_id=9, run_id=48, node_key="resolve_generation",
                                    run_input={}, action_state=state,
                                    context_provider=OnDemandContextProvider({source.source_kind: source}))

    arguments = {"query": actual_input["effective_facts"][0]["assertion"],
                 "source_kinds": ["short_term_state"], "max_candidates": 5, "max_chars": 8000}
    first = context(deepcopy(actual_input))
    retrieve_context(first, arguments)
    assert len(first.action_state["context_retrievals"]) == 1
    restored = context(json.loads(json.dumps(first.action_state, ensure_ascii=False)))
    retrieve_context(restored, arguments)
    assert len(restored.action_state["context_retrievals"]) == 2
    assert restored.action_state["context_retrievals"][0] == first.action_state["context_retrievals"][0]
    assert restored.artifacts["context_retrievals"] == restored.action_state["context_retrievals"]


def test_character_budget_includes_fragment_separators(actual_input):
    source = _source(actual_input)
    query = ContextQuery(text=actual_input["effective_facts"][0]["assertion"], user_id=1, project_id=9,
                         source_kinds=("short_term_state",), max_candidates=5)
    candidates = [item for item in source.retrieve(query, budget=ContextBudget(max_chars=20000))
                  if item.evidence["fragment_kind"] == "complete_structured_unit"][:2]
    assert len(candidates) == 2
    budget = ContextBudget(max_chars=sum(len(item.text) for item in candidates))
    manifest = assemble_context(query=query, candidates=candidates, budget=budget)
    assert len(manifest.selected) == 1
    assert manifest.rejected[0].reason == "character_budget_exceeded"
    assert len(render_selected_context(manifest)) <= budget.max_chars


def test_real_state_cannot_be_relabelled_for_another_project(actual_input):
    source = _source(actual_input)
    candidates = source.retrieve(ContextQuery(text=actual_input["effective_facts"][0]["assertion"],
                                              user_id=1, project_id=8, source_kinds=("short_term_state",)),
                                 budget=ContextBudget())
    assert len(candidates) == 1
    assert candidates[0].source.scope_id == 9
    assert candidates[0].status == "unavailable"
    assert candidates[0].evidence["reason"] == "context_scope_mismatch"
    assert candidates[0].text == ""
