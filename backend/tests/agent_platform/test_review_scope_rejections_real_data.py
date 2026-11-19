"""Run73 三份真实拒收候选的 CPU 协议回归，不补造新的模型审查结论。"""

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
from modules.agent_platform.test_generation_schemas import REVIEW_SCHEMA
from review_cpu_projection import cpu_case_scope_ids, cpu_scope_reason, review_cpu_candidate


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
    assert len(data["submissions"]) == 3
    yield data
    assert path.read_bytes() == original


def cpu_candidate(real, index):
    """纯CPU投影旧非批准材料，不改原候选，也不声称模型重新审查过。"""
    return review_cpu_candidate(real["state"], real["submissions"][index]["arguments"])


@pytest.mark.parametrize("index", range(3))
def test_old_real_review_wire_is_not_compatible(index, real):
    actual = real["submissions"][index]["arguments"]
    assert actual["verdict"] == "changes_requested"
    with pytest.raises(ValidationError):
        validate(actual, REVIEW_SCHEMA)


@pytest.mark.parametrize("index", range(3))
def test_required_case_scope_is_derived_without_a_model_refill_round(index, real):
    state = real["state"]
    candidate = cpu_candidate(real, index)
    result = compile_review_checks(state, generation._review_input(state), candidate)
    expected = cpu_case_scope_ids(state)
    assert len(result["case_checks"]) == 70
    for row in result["case_checks"]:
        assert set(expected[row["case_id"]]) <= set(row["scope_evidence_ids"])
    original = {row["case_id"]: row for row in real["submissions"][index]["arguments"]["case_checks"]}
    actual = {row["case_id"]: row for row in result["case_checks"]}
    assert "EV-0023" not in original["TC-0020"]["scope_evidence_ids"]
    assert {"EV-0023", "EV-0035"} <= set(actual["TC-0020"]["scope_evidence_ids"])
    assert all("scope_evidence_ids" not in row for row in candidate["case_checks"])
    assert "verdict" not in candidate and "issues" not in candidate


@pytest.mark.parametrize("index", range(3))
def test_cpu_projection_preserves_real_material_without_claiming_semantic_success(index, real):
    before = deepcopy(real)
    candidate = cpu_candidate(real, index)
    validate(candidate, REVIEW_SCHEMA)
    result = compile_review_checks(real["state"], generation._review_input(real["state"]), candidate)
    assert len(result["case_checks"]) == 70
    errors = [item for item in result["derived_issues"] if item["kind"] == "case_error"]
    assert errors and any(item["target_case_ids"] == ["TC-0018"] for item in errors)
    assert real == before


@pytest.mark.parametrize("value", ["", " ", None])
def test_scope_reason_must_explicitly_explain_current_case_material(value, real):
    candidate = cpu_candidate(real, 0)
    candidate["case_checks"][0]["scope_reason"] = value
    with pytest.raises(OutputValidationError, match="说明必须是非空文字"):
        compile_review_checks(real["state"], generation._review_input(real["state"]), candidate)
