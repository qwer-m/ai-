"""真实71材料的CPU协议候选，不构造或宣称新的独审成功回执。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from modules.agent_platform.generation_review_contract import required_review_source_ids, compile_review_checks
from modules.agent_platform.output_repair import OutputValidationError
from review_cpu_projection import cpu_case_scope_ids, cpu_scope_reason


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run71_review_completeness_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    data = fixture["data"]
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["source_run_id"] == 71
    assert fixture["provenance"]["source_status"] == "success"
    plan_path = Path(__file__).parent / "fixtures/run71_review_scope_plan_real.json.gz"
    plan_original = plan_path.read_bytes()
    plan_fixture = json.loads(gzip.decompress(plan_original))
    plan_data = plan_fixture["data"]
    encoded_plan = json.dumps(plan_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded_plan).hexdigest() == plan_fixture["provenance"]["data_sha256"]
    assert plan_fixture["provenance"]["source_run_id"] == 71
    contents = {key: record["content"] for key, record in data["state"]["cases"]["items"].items()}
    encoded_cases = json.dumps(contents, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded_cases).hexdigest() == plan_data["cases_model_content_sha256"]
    # 最小旧fixture只有cases；另取同Run真实计划，再机械投影当前字段，不改原记录。
    data = deepcopy(data)
    data["state"]["coverage_plan"] = deepcopy(plan_data["coverage_plan"])
    for goal in data["state"]["coverage_plan"]["items"]:
        goal["conditions"] = list(dict.fromkeys(condition
            for record in data["state"]["cases"]["items"].values()
            if goal["coverage_id"] in record["content"]["coverage_ids"]
            for condition in record["content"]["preconditions"]))
        goal["exceptions"] = []
    yield data
    assert path.read_bytes() == original
    assert plan_path.read_bytes() == plan_original


def cpu_candidate(real):
    """仅机械组织真实编号与来源归属以测试结构，不代表模型已核实分支语义。"""
    state, payload = real["state"], real["payload"]
    scope_sources = cpu_case_scope_ids(state)
    by_id = {item["evidence_id"]: item for item in payload["evidence_catalog"]["items"]}
    rows = []
    for identity in required_review_source_ids(payload):
        witnesses = [key for key, record in state["cases"]["items"].items()
                     if any(ref["evidence_id"] == identity for ref in record["content"]["source_refs"])]
        item = by_id[identity]
        rows.append({"evidence_id": identity, "branches": [{
            "rule": item.get("resolution") or item["text"], "case_ids": witnesses,
            "disposition": "covered" if witnesses else "reference",
            "related_evidence_ids": [],
        }]})
    return {"source_checks": rows,
        "case_checks": [{"case_id": key, "additional_evidence_ids": [],
                         "scope_reason": cpu_scope_reason(record["content"]), "valid": True}
                        for key, record in state["cases"]["items"].items()],
        "business_questions": [], "issue_resolutions": []}


def branch(candidate, identity):
    return next(row["branches"][0] for row in candidate["source_checks"] if row["evidence_id"] == identity)


def check(real, candidate):
    return compile_review_checks(real["state"], real["payload"], candidate)


def test_actual_run71_approved_without_source_and_condition_receipts_is_rejected(real):
    actual = deepcopy(real["actual_legacy_approved"])
    assert actual["verdict"] == "approved" and len(actual["checked_case_ids"]) == 70
    with pytest.raises(OutputValidationError, match="协议|字段"):
        check(real, actual)


def test_complete_cpu_contract_derives_seventy_ids_and_preserves_raw_material(real):
    before = deepcopy(real)
    candidate = cpu_candidate(real)
    result = check(real, candidate)
    assert len(result["source_checks"]) == 19
    assert len(result["case_checks"]) == len(result["checked_case_ids"]) == 70
    assert result["checked_case_ids"] == list(real["state"]["cases"]["items"])
    assert set(candidate) == {"source_checks", "case_checks", "business_questions", "issue_resolutions"}
    assert "verdict" not in result
    assert real == before


def test_only_real_text_and_approvals_are_required_not_unviewed_images(real):
    selected = set(required_review_source_ids(real["payload"]))
    catalog = real["payload"]["evidence_catalog"]["items"]
    assert len(selected) == 19
    assert sum(item["kind"] == "approved_decision" for item in catalog if item["evidence_id"] in selected) == 5
    images = {item["evidence_id"] for item in catalog if item["kind"] == "image"}
    assert len(images) == 21 and not selected & images
    candidate = cpu_candidate(real)
    candidate["source_checks"][0]["evidence_id"] = next(iter(images))
    with pytest.raises(OutputValidationError, match="未实际提供"):
        check(real, candidate)


@pytest.mark.parametrize("field", ["source_checks", "case_checks"])
def test_receipts_cannot_omit_or_duplicate_real_source_or_case(real, field):
    candidate = cpu_candidate(real)
    candidate[field].pop()
    with pytest.raises(OutputValidationError, match="全部"):
        check(real, candidate)
    candidate = cpu_candidate(real)
    candidate[field].append(deepcopy(candidate[field][0]))
    with pytest.raises(OutputValidationError, match="重复"):
        check(real, candidate)


def test_covered_branch_needs_actual_case_witness_and_unknown_ids_rejected(real):
    candidate = cpu_candidate(real)
    current = branch(candidate, "EV-0004")
    assert current["case_ids"]
    current["case_ids"] = []
    with pytest.raises(OutputValidationError, match="非空编号数组"):
        check(real, candidate)
    current["case_ids"] = ["不存在的用例"]
    with pytest.raises(OutputValidationError, match="未知"):
        check(real, candidate)


def test_source_gap_is_compiled_from_real_branch_without_repeating_an_issue(real):
    candidate = cpu_candidate(real)
    current = branch(candidate, "EV-0004")
    actual = real["actual_source_gap"]
    current.update(disposition="gap", case_ids=[], rule=actual["reason"])
    result = check(real, candidate)
    gap = next(item for item in result["derived_issues"] if item["kind"] == "coverage_gap")
    assert gap["reason"] == actual["reason"]
    assert gap["source_refs"] == ["EV-0004"]
    assert "issues" not in candidate and "verdict" not in candidate


@pytest.mark.parametrize(("disposition", "step_field"), [("transition_covered", "transition_steps"), ("effect_covered", "verification_steps")])
def test_coverage_receipt_requires_existing_action_in_every_cited_case(real, disposition, step_field):
    candidate = cpu_candidate(real)
    current = branch(candidate, "EV-0004")
    identity = "TC-0008"
    steps = real["state"]["cases"]["items"][identity]["content"]["steps"]
    assert "进入" in steps[0]["expected"]
    current.update(disposition=disposition, rule=steps[0]["expected"], case_ids=[identity])
    with pytest.raises(OutputValidationError, match="协议"):
        check(real, candidate)
    current[step_field] = [{"case_id": identity, "step_number": len(steps) + 1}]
    with pytest.raises(OutputValidationError, match="实际操作"):
        check(real, candidate)
    current[step_field][0]["step_number"] = 1
    assert branch(check(real, candidate), "EV-0004")[step_field] == current[step_field]


def test_invalid_case_is_compiled_with_current_scope_and_real_historical_reason(real):
    candidate = cpu_candidate(real)
    case = next(row for row in candidate["case_checks"] if row["case_id"] == "TC-0016")
    actual = real["actual_case_error"]
    case.update(valid=False, scope_reason=actual["reason"], additional_evidence_ids=["EV-0023"])
    result = check(real, candidate)
    finding = next(item for item in result["derived_issues"] if item["kind"] == "case_error")
    assert finding["target_case_ids"] == ["TC-0016"]
    assert finding["reason"] == actual["reason"]
    assert {"EV-0016", "EV-0023"} <= set(finding["source_refs"])


def test_case_scope_is_derived_from_current_state_with_optional_real_cross_source_ids(real):
    candidate = cpu_candidate(real)
    case = next(row for row in candidate["case_checks"] if row["case_id"] == "TC-0016")
    case["additional_evidence_ids"] = ["EV-0023"]
    result = check(real, candidate)
    actual = next(row for row in result["case_checks"] if row["case_id"] == "TC-0016")
    assert set(cpu_case_scope_ids(real["state"])["TC-0016"]) <= set(actual["scope_evidence_ids"])
    assert "EV-0023" in actual["scope_evidence_ids"]
    case["additional_evidence_ids"] = ["不存在的来源"]
    with pytest.raises(OutputValidationError, match="未知"):
        check(real, candidate)


def test_wire_cannot_submit_derived_case_scope_or_old_verdict(real):
    candidate = cpu_candidate(real)
    case = candidate["case_checks"][0]
    case["scope_evidence_ids"] = case.pop("additional_evidence_ids")
    with pytest.raises(OutputValidationError, match="字段|协议"):
        check(real, candidate)
    candidate = cpu_candidate(real)
    candidate["verdict"] = "approved"
    with pytest.raises(OutputValidationError, match="字段|协议"):
        check(real, candidate)


def test_superseded_branch_keeps_primary_source_and_only_extra_related_ids(real):
    candidate = cpu_candidate(real)
    current = branch(candidate, "EV-0007")
    current.update(disposition="superseded", case_ids=[])
    assert branch(check(real, candidate), "EV-0007")["related_evidence_ids"] == []
    current["related_evidence_ids"] = ["EV-0007"]
    assert branch(check(real, candidate), "EV-0007")["related_evidence_ids"] == []
    approved = next(item["evidence_id"] for item in real["payload"]["evidence_catalog"]["items"]
                    if item["kind"] == "approved_decision" and item["evidence_id"].startswith("AP-17-"))
    current["related_evidence_ids"] = ["EV-0007", approved]
    assert branch(check(real, candidate), "EV-0007")["related_evidence_ids"] == [approved]
    current["related_evidence_ids"] = ["不存在的来源"]
    with pytest.raises(OutputValidationError, match="未知"):
        check(real, candidate)
