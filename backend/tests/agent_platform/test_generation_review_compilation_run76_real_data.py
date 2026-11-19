"""Run76真实拒绝候选的CPU编译回归；不宣称模型完成了新协议独审。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.generation_review_contract import (
    compile_review_checks, required_case_scope_ids, validate_review_checks,
)
from modules.agent_platform.output_repair import OutputValidationError


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run76_review_compilation_rejected_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    encoded = json.dumps(fixture["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["source_run_id"] == 76
    assert fixture["provenance"]["source_status"] == "failed"
    yield fixture["data"]
    assert path.read_bytes() == original


def cpu_projection(real, index=1):
    """机械去除旧协议重复字段及未提供图片目录项，保留真实语义文字和判断。"""
    state = deepcopy(real["state"])
    raw = real["submissions"][index]
    payload = generation._review_input(state)
    eligible = set(payload["review_requirements"]["source_evidence_ids"])
    required = required_case_scope_ids(state)
    result = {
        "source_checks": [{"evidence_id": row["evidence_id"], "branches": [
            {key: deepcopy(value) for key, value in branch.items() if key != "conflicting_case_ids"}
            for branch in row["branches"]]} for row in raw["source_checks"] if row["evidence_id"] in eligible],
        "case_checks": [{"case_id": row["case_id"], "valid": row["valid"], "scope_reason": row["scope_reason"],
            "additional_evidence_ids": sorted(set(row["scope_evidence_ids"]) - set(required[row["case_id"]]))}
            for row in raw["case_checks"]],
        "business_questions": [], "issue_resolutions": deepcopy(raw["issue_resolutions"]),
    }
    return state, payload, result


def test_real_rejected_candidates_and_state_remain_unmodified(real):
    before = deepcopy(real)
    assert len(real["state"]["cases"]["items"]) == 70
    first, second = real["submissions"]
    conflicts = {identity for row in first["source_checks"] for branch in row["branches"]
                 for identity in branch["conflicting_case_ids"]}
    assert conflicts == {"TC-0015", "TC-0025", "TC-0056"}
    assert all(row["valid"] for row in first["case_checks"])
    assert any(row["evidence_id"] == "EV-0002" for row in second["source_checks"])
    assert real["state"]["review"] is None
    for raw in real["submissions"]:
        with pytest.raises(OutputValidationError, match="完整审查协议"):
            compile_review_checks(real["state"], generation._review_input(real["state"]), raw)
    assert real == before


def test_review_input_only_contains_actually_provided_sources_and_keeps_five_approvals(real):
    state, payload, _ = cpu_projection(real)
    catalog = payload["evidence_catalog"]["items"]
    assert len(catalog) == 19
    assert sum(row["kind"] == "approved_decision" for row in catalog) == 5
    assert "EV-0002" not in {row["evidence_id"] for row in catalog}
    assert any(row["evidence_id"] == "EV-0002" for row in state["evidence_catalog"]["items"])


def test_actual_gap_compiles_to_real_issue_without_repeated_issue_or_verdict(real):
    state, payload, candidate = cpu_projection(real)
    normalized = generation._validate_review(state, payload, candidate)
    assert normalized["verdict"] == "changes_requested"
    assert len(normalized["checked_case_ids"]) == 70
    assert len(normalized["issues"]) == 1
    gap = normalized["issues"][0]
    branch = next(branch for row in candidate["source_checks"] for branch in row["branches"]
                  if branch["disposition"] == "gap")
    assert gap["kind"] == "coverage_gap" and gap["reason"] == branch["rule"]
    assert gap["issue_id"].startswith("ISS-")
    assert any(ref["evidence_id"].startswith("AP-16-") for ref in gap["source_refs"])
    assert validate_review_checks(state, payload, normalized)["case_checks"] == normalized["case_checks"]
    denied = {**normalized, "verdict": "approved"}
    with pytest.raises(OutputValidationError, match="不能批准"):
        validate_review_checks(state, payload, denied)


def test_false_case_flag_derives_one_targeted_error_and_preserves_real_scope_reason(real):
    state, payload, candidate = cpu_projection(real)
    # 仅注入CPU负面布尔值；文字仍来自真实候选，不把此候选当成模型业务结论。
    case = next(row for row in candidate["case_checks"] if row["case_id"] == "TC-0015")
    case["valid"] = False
    normalized = generation._validate_review(state, payload, candidate)
    findings = [issue for issue in normalized["issues"] if issue["kind"] == "case_error"]
    assert len(findings) == 1
    assert findings[0]["target_case_ids"] == ["TC-0015"]
    assert findings[0]["reason"] == case["scope_reason"]
    assert findings[0]["coverage_ids"] == state["cases"]["items"]["TC-0015"]["content"]["coverage_ids"]
    checks = next(row for row in normalized["case_checks"] if row["case_id"] == "TC-0015")
    assert set(required_case_scope_ids(state)["TC-0015"]) <= set(checks["scope_evidence_ids"])
    assert set(case["additional_evidence_ids"]) <= set(checks["scope_evidence_ids"])
    assert normalized["verdict"] == "changes_requested"


def test_full_source_and_case_receipts_and_actual_image_constraint_still_required(real):
    state, payload, candidate = cpu_projection(real)
    unknown = deepcopy(candidate)
    unknown["source_checks"][0]["evidence_id"] = "EV-0002"
    with pytest.raises(OutputValidationError, match="未实际提供"):
        generation._validate_review(state, payload, unknown)
    for key in ("source_checks", "case_checks"):
        missing = deepcopy(candidate)
        missing[key].pop()
        with pytest.raises(OutputValidationError, match="全部"):
            generation._validate_review(state, payload, missing)
    duplicate = deepcopy(candidate)
    duplicate["case_checks"].append(deepcopy(candidate["case_checks"][0]))
    with pytest.raises(OutputValidationError, match="重复"):
        generation._validate_review(state, payload, duplicate)


def test_business_questions_cannot_reintroduce_redundant_case_errors(real):
    state, payload, candidate = cpu_projection(real)
    candidate["business_questions"] = [deepcopy(real["submissions"][1]["issues"][0])]
    with pytest.raises(OutputValidationError, match="business_questions仅允许"):
        generation._validate_review(state, payload, candidate)


def test_empty_additional_sources_still_compile_every_declared_case_and_coverage_constraint(real):
    state, payload, candidate = cpu_projection(real)
    for row in candidate["case_checks"]:
        row["additional_evidence_ids"] = []
    checks = compile_review_checks(state, payload, candidate)
    required = required_case_scope_ids(state)
    assert {row["case_id"]: row["scope_evidence_ids"] for row in checks["case_checks"]} == required
    assert all(row["scope_reason"] for row in checks["case_checks"])


def test_canonical_revalidation_uses_checks_without_replaying_old_resolution_identity_scope(real):
    state, payload, candidate = cpu_projection(real)
    normalized = generation._validate_review(state, payload, candidate)
    approval = state["approved_decisions"][0]
    # 采用真实历史事项编号证明回执重验独立于当前open_issues；不生成新裁决或批准。
    normalized["issue_resolutions"] = [{"issue_id": approval["original_issue_id"],
        "resolution": "resolved", "reason": approval["resolution"],
        "source_refs": [approval["evidence_id"]]}]
    assert approval["original_issue_id"] not in state["open_issues"]
    assert len(validate_review_checks(state, payload, normalized)["case_checks"]) == 70
