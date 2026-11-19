"""Run79真实拒收候选的业务问题作用域编译回归，不构造新模型语义批准。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from jsonschema import ValidationError, validate
import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.generation_review_contract import compile_review_checks
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.test_generation_schemas import ISSUE_SCHEMA, REVIEW_SCHEMA


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run79_review_business_scope_rejected_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    encoded = json.dumps(fixture["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["source_run_id"] == 79
    assert fixture["provenance"]["source_status"] == "failed"
    yield fixture["data"]
    assert path.read_bytes() == original


def wire_candidate(real, index):
    """仅移除不再属于wire的机械覆盖编号，所有语义判断和真实证据编号保持原值。"""
    candidate = deepcopy(real["submissions"][index])
    for question in candidate["business_questions"]:
        question.pop("coverage_ids")
    return candidate


def test_actual_second_and_third_rejections_are_misplaced_case_ids_in_coverage_field(real):
    assert real["state"]["coverage_plan"]["items"] == []
    assert len(real["state"]["cases"]["items"]) == 70
    assert [row["feedback"]["message"] for row in real["tool_observations"]][1:] == [
        "问题只能引用当前真实用例与覆盖目标", "问题只能引用当前真实用例与覆盖目标"]
    for candidate in real["submissions"]:
        assert [question["coverage_ids"] for question in candidate["business_questions"]] == [["TC-0035"], ["TC-0036"]]
        assert [question["target_case_ids"] for question in candidate["business_questions"]] == [["TC-0035"], ["TC-0036"]]
    assert real["state"]["review"] is None and real["state"]["delivery"] is None


@pytest.mark.parametrize("index", [1, 2])
def test_actual_candidates_compile_without_repeating_mechanical_coverage_ids_and_keep_human_semantics(real, index):
    before = deepcopy(real)
    state, candidate = real["state"], wire_candidate(real, index)
    validate(candidate, REVIEW_SCHEMA)
    normalized = generation._validate_review(state, generation._review_input(state), candidate)
    questions = [finding for finding in normalized["issues"] if finding["kind"] == "missing_rule"]
    assert len(questions) == 2
    assert normalized["verdict"] == "needs_human"
    assert len(normalized["checked_case_ids"]) == 70
    assert all(question["coverage_ids"] == [] for question in questions)
    assert {tuple(question["target_case_ids"]) for question in questions} == {("TC-0035",), ("TC-0036",)}
    assert {question["reason"] for question in questions} == {row["reason"] for row in candidate["business_questions"]}
    assert {ref["evidence_id"] for question in questions for ref in question["source_refs"]} == {"EV-0018", "EV-0019", "EV-0033"}
    assert {finding["target_case_ids"][0] for finding in normalized["issues"] if finding["kind"] == "case_error"} == {"TC-0035", "TC-0036"}
    assert real == before


def test_old_business_wire_is_strictly_rejected_and_main_issue_schema_keeps_coverage_ids(real):
    candidate = deepcopy(real["submissions"][1])
    with pytest.raises(ValidationError):
        validate(candidate, REVIEW_SCHEMA)
    with pytest.raises(OutputValidationError, match="业务问题字段必须符合完整审查协议"):
        compile_review_checks(real["state"], generation._review_input(real["state"]), candidate)
    assert "coverage_ids" in ISSUE_SCHEMA["required"]
    assert "coverage_ids" not in REVIEW_SCHEMA["properties"]["business_questions"]["items"]["properties"]


def test_unknown_target_case_still_rejects_instead_of_turning_into_empty_coverage_scope(real):
    candidate = wire_candidate(real, 1)
    candidate["business_questions"][0]["target_case_ids"] = ["EV-0019"]
    with pytest.raises(OutputValidationError, match="业务问题目标用例引用未知") as error:
        generation._validate_review(real["state"], generation._review_input(real["state"]), candidate)
    assert error.value.details["unknown_ids"] == ["EV-0019"]


def test_first_actual_candidate_retains_rejection_for_truncated_approval_id(real):
    candidate = wire_candidate(real, 0)
    with pytest.raises(OutputValidationError, match="相关约束来源引用未知") as error:
        generation._validate_review(real["state"], generation._review_input(real["state"]), candidate)
    assert error.value.details["unknown_ids"] == ["AP-13-b9f87821f906b0d1fe55ee3489cec2686b2"]
