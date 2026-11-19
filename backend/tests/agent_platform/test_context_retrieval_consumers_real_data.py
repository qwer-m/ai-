"""用真实 Run48 内容执行短期检索，验证持久读模型而不调用模型或写数据库。"""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.agent_platform.checkpoint_storage import context_retrieval_summary, state_projection
from modules.agent_platform.context_agent_tools import retrieve_context
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.serialization import summarize_run_evaluation
from modules.knowledge_base_components.context.context_provider import (
    OnDemandContextProvider, ShortTermStateContextSourcePort,
)


@pytest.fixture
def actual_retrieval():
    folder = Path(__file__).parent / "fixtures"
    actual_input = json.loads((folder / "run48_generation_batching_real.json").read_text(encoding="utf-8"))["input_payload"]
    snapshot = json.loads((folder / "run48.presentation.json").read_text(encoding="utf-8"))
    original = next(node for node in snapshot["nodes"] if node["node_type"] == "agent_network")
    state = deepcopy(actual_input)
    source = ShortTermStateContextSourcePort(lambda: state, run_id=snapshot["run"]["id"],
                                           node_key=original["node_key"], user_id=1, project_id=9)
    context = ToolExecutionContext(db=None, user_id=1, project_id=9, run_id=snapshot["run"]["id"],
                                   node_key=original["node_key"], run_input={}, action_state=state,
                                   context_provider=OnDemandContextProvider({source.source_kind: source}))
    retrieve_context(context, {
        "query": actual_input["effective_facts"][0]["assertion"],
        "source_kinds": ["short_term_state"], "max_candidates": 5, "max_chars": 8000,
    })
    # 只构造真实状态投影的读模型，不制造内容引用或持久化节点。
    header = original["sdk_state"]["action_checkpoint"]
    node = SimpleNamespace(id=original["id"], node_key=original["node_key"],
                           status=original["status"], attempt=original["attempt"],
                           sdk_state={"action_checkpoint": {"version": header["version"],
                                      "storage": header["storage"], "projection": state_projection(state)}})
    run = SimpleNamespace(id=snapshot["run"]["id"], status=original["status"], run_context={"artifacts": {}})
    return run, node, state


@pytest.mark.parametrize("status", ["failed", "waiting_approval", "running"])
def test_unfinished_persistent_run_metrics_read_only_checkpoint_projection(actual_retrieval, status):
    run, node, state = actual_retrieval
    run.status = status
    result = summarize_run_evaluation(run, node_runs=[node])["context_evaluation"]
    expected = context_retrieval_summary(state["context_retrievals"])
    assert result["retrieval_evidence_status"] == "available"
    assert all(result[key] == value for key, value in expected.items())
    assert result["retrieval_count"] == 1
    assert run.run_context["artifacts"] == {}


def test_persistent_manifest_metrics_ignore_stale_artifact_and_retry_copy(actual_retrieval):
    run, node, state = actual_retrieval
    run.run_context["artifacts"]["context_retrievals"] = deepcopy(state["context_retrievals"] * 2)
    retried = deepcopy(node)
    retried.id += 1
    retried.attempt += 1
    result = summarize_run_evaluation(run, node_runs=[retried, node])["context_evaluation"]
    assert result["retrieval_count"] == 1


def test_unrecorded_projection_marks_retrieval_evidence_unavailable(actual_retrieval):
    run, node, state = actual_retrieval
    run.run_context["artifacts"]["context_retrievals"] = state["context_retrievals"]
    del node.sdk_state["action_checkpoint"]["projection"]["context_retrieval_summary"]
    result = summarize_run_evaluation(run, node_runs=[node])["context_evaluation"]
    assert result["retrieval_evidence_status"] == "unavailable"
    assert result["retrieval_count"] is None
    assert result["selected_source_count"] is None


def test_nonpersistent_task_keeps_its_artifact_authority(actual_retrieval):
    run, _, state = actual_retrieval
    run.run_context["artifacts"]["context_retrievals"] = state["context_retrievals"]
    result = summarize_run_evaluation(run)["context_evaluation"]
    assert result["retrieval_count"] == 1
    assert result["retrieval_evidence_status"] == "available"


def test_projection_is_only_a_summary_and_feedback_keeps_exact_source(actual_retrieval):
    from routers.orchestration.agents import _context_source_from_manifest

    _, node, state = actual_retrieval
    record = state["context_retrievals"][0]
    summary = node.sdk_state["action_checkpoint"]["projection"]["context_retrieval_summary"]
    assert "manifest" not in summary and "selected" not in summary and "context_text" not in summary
    selected = record["manifest"]["selected"][0]["source"]
    actual = _context_source_from_manifest(record, selected["source_id"])
    assert actual.to_dict() == selected
    with pytest.raises(ValueError, match="反馈来源不在"):
        _context_source_from_manifest(record, selected["source_id"] + selected["content_hash"])
