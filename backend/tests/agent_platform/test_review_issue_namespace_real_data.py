"""真实 Run73 三次拒收的命名空间与聚合反馈回归；不宣称重新完成模型审查。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.generation_review_contract import required_case_scope_ids
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from review_cpu_projection import review_cpu_candidate
from test_single_agent_direct_protocol_real_data import model_input


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run73_review_rejections_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    data = fixture["data"]
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["source_run_id"] == 73
    assert fixture["provenance"]["source_status"] == "failed"
    assert len(data["rounds"]) == len(data["submissions"]) == len(data["tool_observations"]) == 3
    yield data
    assert path.read_bytes() == original


def cpu_current_shape(state, raw):
    """旧不批准记录仅在CPU投影为当前wire，不伪造新的模型审查。"""
    return review_cpu_candidate(state, raw)


def test_model_view_keeps_approved_business_rules_without_historical_issue_identity(real):
    state = real["state"]
    original = deepcopy(state)
    payload = generation._review_input(state)
    assert payload["open_issues"] == []
    assert payload["review_requirements"]["current_issue_ids"] == []
    assert payload["review_requirements"]["case_scope_evidence_ids"] == required_case_scope_ids(state)
    projected = {item["evidence_id"]: item for item in payload["evidence_catalog"]["items"]
                 if item["kind"] == "approved_decision"}
    assert len(projected) == len(state["approved_decisions"]) == 5
    for approved in state["approved_decisions"]:
        assert approved["original_issue_id"]
        model = projected[approved["evidence_id"]]
        assert "original_issue_id" not in model
        assert model["resolution"] == approved["resolution"]
        assert model["question"] == {key: approved["question"][key] for key in
                                     ("kind", "reason", "coverage_fragment") if key in approved["question"]}
    assert state == original


@pytest.mark.parametrize("round_index", [0, 1, 2])
def test_actual_wrong_ap_resolutions_report_exact_current_diff_without_model_scope_refill(real, round_index):
    state = real["state"]
    record = real["rounds"][round_index]
    raw = deepcopy(record["raw_candidate"])
    assert record["feedback"]["details"] == {}
    expected_unexpected = sorted(item["issue_id"] for item in raw["issue_resolutions"])
    assert expected_unexpected
    assert set(expected_unexpected) <= {item["original_issue_id"] for item in state["approved_decisions"]}
    with pytest.raises(OutputValidationError) as rejected:
        generation._validate_review(state, generation._review_input(state), cpu_current_shape(state, raw))
    details = rejected.value.details
    assert details["expected_issue_ids"] == details["missing_issue_ids"] == details["duplicate_issue_ids"] == []
    assert details["unexpected_issue_ids"] == expected_unexpected
    assert "AP是已确认历史口径" in details["issue_resolution_scope"]
    # 已声明的scope由代码组成，不再要求模型重复补齐同一组来源编号。
    assert "case_scope_errors" not in details
    assert raw == record["raw_candidate"]
    assert real["submissions"][round_index]["arguments"] == raw


def test_correcting_namespace_preserves_real_case_error_without_false_approval(real):
    state = real["state"]
    output = cpu_current_shape(state, real["rounds"][0]["raw_candidate"])
    output["issue_resolutions"] = []
    result = generation._validate_review(state, generation._review_input(state), output)
    assert result["verdict"] == "changes_requested"
    assert any(item["kind"] == "case_error" and item["target_case_ids"] == ["TC-0018"]
               for item in result["issues"])
    assert all(set(required_case_scope_ids(state)[row["case_id"]]) <= set(row["scope_evidence_ids"])
               for row in result["case_checks"])
    assert state["review"] is None and not state["completed"]


def test_real_current_issue_is_explicit_and_cannot_be_replaced_by_historical_approvals(real):
    state = deepcopy(real["state"])
    finding = generation._issue(state, model_input(real["rounds"][0]["raw_candidate"]["issues"][0]),
                               origin="independent_review", require_read=False, allowed_images=[])
    state["open_issues"][finding["issue_id"]] = finding
    payload = generation._review_input(state)
    assert payload["review_requirements"]["current_issue_ids"] == [finding["issue_id"]]
    output = cpu_current_shape(state, real["rounds"][0]["raw_candidate"])
    with pytest.raises(OutputValidationError) as rejected:
        generation._validate_review(state, payload, output)
    assert rejected.value.details["expected_issue_ids"] == rejected.value.details["missing_issue_ids"] == [finding["issue_id"]]
    assert rejected.value.details["unexpected_issue_ids"] == [output["issue_resolutions"][0]["issue_id"]]
    assert state["open_issues"][finding["issue_id"]]["status"] == "open"
    assert real["state"]["open_issues"] == {}


def test_duplicate_unknown_resolutions_are_rejected_explicitly(real):
    state = real["state"]
    output = cpu_current_shape(state, real["rounds"][0]["raw_candidate"])
    output["issue_resolutions"].append(deepcopy(output["issue_resolutions"][0]))
    with pytest.raises(OutputValidationError) as rejected:
        generation._validate_review(state, generation._review_input(state), output)
    assert rejected.value.details["duplicate_issue_ids"] == rejected.value.details["unexpected_issue_ids"]


def test_current_specs_explain_issue_namespace_and_causal_case_scope_without_document_ids(real):
    specs = {item["agent_key"]: item for item in BUILTIN_AGENT_SPECS}
    reviewer = specs["test_generation_reviewer"]
    main = specs["test_generation_coordinator"]
    assert "review_requirements.current_issue_ids" in reviewer["instructions"]
    assert "scope_reason" in reviewer["instructions"]
    assert "conditions" in reviewer["instructions"] and "exceptions" in reviewer["instructions"]
    assert "preconditions" in main["instructions"] and "实际参与的对象集合" in main["instructions"]
    assert not any(item["evidence_id"] in reviewer["instructions"] + main["instructions"]
                   for item in real["state"]["evidence_catalog"]["items"])
