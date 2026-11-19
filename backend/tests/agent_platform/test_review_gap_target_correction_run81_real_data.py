"""Run81真实复审的显式缺口归属纠正CPU回放，不推断或合并业务问题。"""

from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

from jsonschema import validate
import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.test_generation_schemas import REVIEW_SCHEMA
from test_confirmation_state_scope_run80_real_data import contexts


OLD_GAP = "ISS-0ad730b9697df627fef3bfee"


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).parent / "fixtures/run81_gap_retarget_review_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    encoded = json.dumps(fixture["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    assert fixture["provenance"]["source_run_id"] == 81 and fixture["provenance"]["source_status"] == "failed"
    assert action_state_hash(fixture["data"]["pre_second_review_state"]) == fixture["provenance"]["source_pre_review_state_hash"]
    yield fixture["data"]
    assert path.read_bytes() == original


def corrected_candidate(real):
    candidate = deepcopy(real["second_review_raw"])
    next(row for row in candidate["issue_resolutions"] if row["issue_id"] == OLD_GAP)["target_case_ids"] = ["TC-0041"]
    return candidate


def resolution(candidate, identity=OLD_GAP):
    return next(row for row in candidate["issue_resolutions"] if row["issue_id"] == identity)


def test_real_review_reason_and_new_branch_already_name_different_scope_without_closing_old_gap(real):
    old = real["pre_second_review_state"]["open_issues"][OLD_GAP]
    decision = resolution(real["second_review_raw"])
    assert old["target_case_ids"] == ["TC-0029"] and old["kind"] == "coverage_gap"
    assert decision["resolution"] == "open" and "应改补语篇填空适用场景" in decision["reason"]
    assert any(branch["disposition"] == "gap" and branch["case_ids"] == ["TC-0041"]
        for row in real["second_review_raw"]["source_checks"] if row["evidence_id"] == "EV-0024"
        for branch in row["branches"])
    assert "target_case_ids" not in decision


def test_explicit_target_correction_keeps_original_issue_open_and_case29_unchanged_then_real_patch_makes_ready(real, contexts):
    context = contexts(real["run"], real["pre_second_review_state"])
    candidate = corrected_candidate(real)
    validate(candidate, REVIEW_SCHEMA)
    case29 = deepcopy(context.action_state["cases"]["items"]["TC-0029"])
    old_budget = deepcopy(context.db.get(generation.AgentRun, context.run_id).run_context)
    old_issue = deepcopy(context.action_state["open_issues"][OLD_GAP])
    result = generation._apply_review(context, generation._review_input(context.action_state), candidate)
    issue = context.action_state["open_issues"][OLD_GAP]
    assert result["verdict"] == "changes_requested"
    assert issue["issue_id"] == old_issue["issue_id"] and issue["status"] == "open"
    assert issue["target_case_ids"] == ["TC-0041"]
    assert issue["coverage_ids"] == context.action_state["cases"]["items"]["TC-0041"]["content"]["coverage_ids"]
    assert issue["target_versions"]["cases"] == {"TC-0041": 1}
    assert issue["reason"] == old_issue["reason"] and issue["source_refs"] == old_issue["source_refs"]
    history = issue["review_history"][-1]
    assert history["previous_target_case_ids"] == ["TC-0029"]
    assert history["current_target_case_ids"] == ["TC-0041"]
    assert history["reason"] == resolution(candidate)["reason"]
    assert {ref["evidence_id"] for ref in history["source_refs"]} == {"EV-0024", "EV-0033"}
    assert context.action_state["cases"]["items"]["TC-0029"] == case29
    assert generation._automatic_review_ready(context.action_state) is False
    generation.patch_cases(context, deepcopy(real["final_patch_arguments"]))
    assert context.action_state["cases"]["items"]["TC-0029"] == case29
    assert context.action_state["open_issues"][OLD_GAP]["status"] == "pending_review"
    assert generation._automatic_review_ready(context.action_state) is True
    assert context.db.get(generation.AgentRun, context.run_id).run_context == old_budget
    assert not context.action_state["completed"] and context.action_state["delivery"] is None


def test_omitted_target_keeps_current_scope_and_original_missing_revision_blocks_re_review(real, contexts):
    context = contexts(real["run"], real["pre_second_review_state"])
    candidate = deepcopy(real["second_review_raw"])
    validate(candidate, REVIEW_SCHEMA)
    generation._apply_review(context, generation._review_input(context.action_state), candidate)
    assert context.action_state["open_issues"][OLD_GAP]["target_case_ids"] == ["TC-0029"]
    generation.patch_cases(context, deepcopy(real["final_patch_arguments"]))
    assert generation._automatic_review_ready(context.action_state) is False
    assert "target_case_ids" not in REVIEW_SCHEMA["properties"]["issue_resolutions"]["items"]["required"]


@pytest.mark.parametrize("targets", [[], ["TC-0041", "TC-0041"], ["EV-0024"]])
def test_bad_target_correction_is_rejected_without_applying_any_review_change(real, contexts, targets):
    context = contexts(real["run"], real["pre_second_review_state"])
    candidate = corrected_candidate(real)
    resolution(candidate)["target_case_ids"] = targets
    before = deepcopy(context.action_state)
    with pytest.raises(OutputValidationError):
        generation._apply_review(context, generation._review_input(context.action_state), candidate)
    assert context.action_state == before


@pytest.mark.parametrize("status", ["resolved", "needs_human"])
def test_target_correction_cannot_be_used_with_other_resolution_statuses(real, contexts, status):
    context = contexts(real["run"], real["pre_second_review_state"])
    candidate = corrected_candidate(real)
    resolution(candidate)["resolution"] = status
    with pytest.raises(OutputValidationError, match="仅保持open的coverage_gap"):
        generation._validate_review(context.action_state, generation._review_input(context.action_state), candidate)


def test_case_error_cannot_escape_its_real_target_through_resolution_override(real, contexts):
    context = contexts(real["run"], real["pre_second_review_state"])
    identity = next(key for key, issue in context.action_state["open_issues"].items() if issue["kind"] == "case_error")
    candidate = deepcopy(real["second_review_raw"])
    row = resolution(candidate, identity)
    row.update(resolution="open", target_case_ids=["TC-0041"])
    with pytest.raises(OutputValidationError, match="仅保持open的coverage_gap"):
        generation._validate_review(context.action_state, generation._review_input(context.action_state), candidate)


def test_repair_limit_uses_new_target_and_does_not_punish_old_case(real, contexts):
    context = contexts(real["run"], real["pre_second_review_state"])
    context.action_state["cases"]["items"]["TC-0029"]["automatic_repairs"] = 2
    generation.refresh_generation_state(context.action_state)
    case29 = deepcopy(context.action_state["cases"]["items"]["TC-0029"])
    candidate = corrected_candidate(real)
    normalized = generation._validate_review(context.action_state, generation._review_input(context.action_state), candidate)
    assert normalized["verdict"] == "changes_requested"
    generation._apply_review(context, generation._review_input(context.action_state), candidate)
    assert context.action_state["open_issues"][OLD_GAP]["status"] == "open"
    assert context.action_state["cases"]["items"]["TC-0029"] == case29
    assert not context.action_state["confirmation_requested"]


def test_new_target_at_repair_limit_still_requires_human_instead_of_resetting_progress(real, contexts):
    context = contexts(real["run"], real["pre_second_review_state"])
    context.action_state["cases"]["items"]["TC-0041"]["automatic_repairs"] = 2
    generation.refresh_generation_state(context.action_state)
    candidate = corrected_candidate(real)
    result = generation._apply_review(context, generation._review_input(context.action_state), candidate)
    assert result["verdict"] == "needs_human"
    assert context.action_state["open_issues"][OLD_GAP]["status"] == "needs_human"
    assert OLD_GAP in context.action_state["confirmation_requested"]


def test_corrected_review_cannot_apply_to_snapshot_changed_by_actual_patch(real, contexts):
    context = contexts(real["run"], real["pre_second_review_state"])
    payload = generation._review_input(context.action_state)
    generation.patch_cases(context, deepcopy(real["final_patch_arguments"]))
    before = deepcopy(context.action_state)
    with pytest.raises(ValueError, match="快照已经变化"):
        generation._apply_review(context, payload, corrected_candidate(real))
    assert context.action_state == before
