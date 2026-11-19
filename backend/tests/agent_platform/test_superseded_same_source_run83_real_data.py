"""Run83实际完整拒收候选的同源替代编译回归，不证明superseded业务判断成立。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from jsonschema import validate
import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.generation_review_contract import validate_review_checks
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.test_generation_schemas import REVIEW_SCHEMA


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run83_superseded_same_source_rejected_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    encoded = json.dumps(fixture["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["source_run_id"] == 83 and fixture["provenance"]["source_status"] == "failed"
    assert generation.action_state_hash(fixture["data"]["payload"]) == fixture["provenance"]["managed_input_hash"]
    yield fixture["data"]
    assert path.read_bytes() == original


def superseded_row(candidate):
    row = next(row for row in candidate["source_checks"] if row["evidence_id"] == "EV-0033")
    return row, next(branch for branch in row["branches"] if branch["disposition"] == "superseded")


def test_actual_complete_medium_review_passes_structure_without_inventing_an_extra_source_or_approval(real):
    original = deepcopy(real)
    candidate = real["actual_review_raw"]
    validate(candidate, REVIEW_SCHEMA)
    row, branch = superseded_row(candidate)
    assert row["evidence_id"] == "EV-0033" and branch["related_evidence_ids"] == []
    assert "旧单选选中自动跳题" in branch["rule"] and "答题类改PDF逐题对答案" in branch["rule"]
    normalized = generation._validate_review(real["state"], real["payload"], candidate)
    assert len(normalized["source_checks"]) == 20 and len(normalized["checked_case_ids"]) == 70
    assert normalized["verdict"] == "needs_human"
    assert any(issue["kind"] == "missing_rule" for issue in normalized["issues"])
    assert {issue["reason"] for issue in normalized["issues"] if issue["kind"] == "missing_rule"} == {
        row["reason"] for row in candidate["business_questions"]}
    assert real == original


def test_primary_source_repeated_as_extra_is_removed_without_changing_semantic_findings(real):
    candidate = deepcopy(real["actual_review_raw"])
    _, branch = superseded_row(candidate)
    branch["related_evidence_ids"] = ["EV-0033"]
    actual = generation._validate_review(real["state"], real["payload"], candidate)
    original = generation._validate_review(real["state"], real["payload"], real["actual_review_raw"])
    row, normalized_branch = superseded_row(actual)
    assert row["evidence_id"] == "EV-0033" and normalized_branch["related_evidence_ids"] == []
    assert actual == original
    assert branch["related_evidence_ids"] == ["EV-0033"]


def test_actual_other_related_source_is_retained_while_current_source_stays_on_row(real):
    candidate = deepcopy(real["actual_review_raw"])
    _, branch = superseded_row(candidate)
    branch["related_evidence_ids"] = ["EV-0035", "EV-0033"]
    normalized = generation._validate_review(real["state"], real["payload"], candidate)
    row, result = superseded_row(normalized)
    assert row["evidence_id"] == "EV-0033"
    assert result["related_evidence_ids"] == ["EV-0035"]
    assert normalized["verdict"] == "needs_human"


def test_unprovided_real_image_id_remains_invalid_related_source(real):
    eligible = {item["evidence_id"] for item in real["payload"]["evidence_catalog"]["items"]}
    unprovided = next(item["evidence_id"] for item in real["state"]["evidence_catalog"]["items"]
        if item["kind"] == "image" and item["evidence_id"] not in eligible)
    candidate = deepcopy(real["actual_review_raw"])
    superseded_row(candidate)[1]["related_evidence_ids"] = [unprovided]
    with pytest.raises(OutputValidationError, match="未知或未实际提供"):
        generation._validate_review(real["state"], real["payload"], candidate)


def test_duplicate_extra_ids_still_follow_the_existing_unique_wire_contract(real):
    candidate = deepcopy(real["actual_review_raw"])
    superseded_row(candidate)[1]["related_evidence_ids"] = ["EV-0033", "EV-0033"]
    with pytest.raises(OutputValidationError, match="重复编号"):
        generation._validate_review(real["state"], real["payload"], candidate)


def test_structural_superseded_acceptance_never_overrides_real_invalid_cases_or_gaps(real):
    normalized = generation._validate_review(real["state"], real["payload"], real["actual_review_raw"])
    assert any(not row["valid"] for row in normalized["case_checks"])
    assert any(branch["disposition"] == "gap" for row in normalized["source_checks"] for branch in row["branches"])
    with pytest.raises(OutputValidationError, match="不能批准"):
        validate_review_checks(real["state"], real["payload"], {**normalized, "verdict": "approved"})
