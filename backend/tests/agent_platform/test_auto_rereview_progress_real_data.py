"""Run74 真实修稿前后状态的 CPU 回放，不制造模型响应、审查通过或历史写入。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest

from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash


@pytest.fixture(scope="module")
def saved():
    path = Path(__file__).parent / "fixtures/run74_auto_rereview_real.json.gz"
    original = path.read_bytes()
    value = json.loads(gzip.decompress(original))
    assert value["provenance"]["source_run_id"] == 74
    assert value["provenance"]["model_responses_fabricated"] is False
    data = value["data"]
    assert action_state_hash(data) == value["provenance"]["data_sha256"]
    assert action_state_hash(data["plan_only"]) == data["patch_receipt"]["pre_state_hash"]
    assert action_state_hash(data["repaired"]) == data["patch_receipt"]["post_state_hash"]
    yield data
    assert path.read_bytes() == original


def changed_case_ids(saved):
    return {identity for identity, record in saved["repaired"]["cases"]["items"].items()
            if record != saved["plan_only"]["cases"]["items"][identity]}


def only_actual_case_changes(saved, identities):
    """从真实批次选择部分改稿做隔离回放，不宣称该子集在线单独执行过。"""
    result = deepcopy(saved["plan_only"])
    assert set(identities) <= changed_case_ids(saved)
    for identity in identities:
        result["cases"]["items"][identity] = deepcopy(saved["repaired"]["cases"]["items"][identity])
    result["cases"]["version"] = saved["repaired"]["cases"]["version"]
    generation.refresh_generation_state(result)
    return result


def isolated_issue(state, issue):
    result = deepcopy(state)
    result["open_issues"] = {issue["issue_id"]: deepcopy(issue)}
    generation.refresh_generation_state(result)
    return result


def test_actual_plan_only_cannot_replace_case_repair_but_completed_patch_triggers_review(saved):
    before = deepcopy(saved)
    assert len(saved["repaired"]["cases"]["items"]) == 70
    assert len(saved["repaired"]["open_issues"]) == 15
    assert len(changed_case_ids(saved)) == 15
    assert saved["plan_only"]["coverage_plan"] == saved["repaired"]["coverage_plan"]
    assert saved["plan_only"]["cases"]["version"] == 5
    assert saved["repaired"]["cases"]["version"] == 6
    assert not generation._automatic_review_ready(saved["plan_only"])
    assert generation._automatic_review_ready(saved["repaired"])
    # 准备复审不等于结案或生成交付，原始15个问题与完整状态均保持不变。
    assert not generation._can_finish(saved["repaired"])
    assert all(issue["status"] != "resolved" for issue in saved["repaired"]["open_issues"].values())
    assert saved == before


def test_all_three_real_source_only_gaps_follow_related_actual_case_repair(saved):
    issues = [issue for issue in saved["plan_only"]["open_issues"].values()
              if not issue["target_case_ids"] and not issue["coverage_ids"]]
    assert len(issues) == 3
    changed = changed_case_ids(saved)
    for issue in issues:
        scope = generation._issue_repair_case_ids(saved["repaired"], issue)
        related, unrelated = changed & scope, changed - scope
        assert related and unrelated
        assert not generation._automatic_review_ready(isolated_issue(saved["plan_only"], issue))
        assert generation._automatic_review_ready(isolated_issue(
            only_actual_case_changes(saved, [min(related)]), issue))
        assert not generation._automatic_review_ready(isolated_issue(
            only_actual_case_changes(saved, [min(unrelated)]), issue))


def test_real_coverage_only_gap_tracks_cases_instead_of_unchanged_coverage_hash(saved):
    issues = [issue for issue in saved["plan_only"]["open_issues"].values()
              if not issue["target_case_ids"] and issue["coverage_ids"]]
    assert len(issues) == 1
    issue = issues[0]
    assert generation._target_versions(saved["repaired"], [], issue["coverage_ids"]) == issue["target_versions"]
    scope = generation._issue_repair_case_ids(saved["repaired"], issue)
    related, unrelated = changed_case_ids(saved) & scope, changed_case_ids(saved) - scope
    assert related and unrelated
    assert generation._automatic_review_ready(isolated_issue(
        only_actual_case_changes(saved, [min(related)]), issue))
    assert not generation._automatic_review_ready(isolated_issue(
        only_actual_case_changes(saved, [min(unrelated)]), issue))


def test_explicit_case_target_does_not_accept_another_case_with_the_same_coverage(saved):
    issue = next(issue for issue in saved["plan_only"]["open_issues"].values()
                 if issue["kind"] == "case_error")
    assert len(issue["target_case_ids"]) == 1
    identity = issue["target_case_ids"][0]
    unrelated = next(key for key in changed_case_ids(saved) - {identity}
        if set(saved["repaired"]["cases"]["items"][key]["content"]["coverage_ids"]).intersection(issue["coverage_ids"]))
    assert generation._issue_repair_case_ids(saved["repaired"], issue) == {identity}
    assert not generation._automatic_review_ready(isolated_issue(
        only_actual_case_changes(saved, [unrelated]), issue))


def test_true_final_review_cannot_retrigger_unchanged_approved_snapshot(saved):
    final = deepcopy(saved["final"])
    assert final["completed"] and final["review"]["result"]["verdict"] == "approved"
    # 原始批准属于历史wire，保持其真实记录，不补造当前合同的新批准。
    assert not generation._automatic_review_ready(final)


def test_actual_human_decision_records_remain_exactly_preserved_across_trigger(saved):
    assert saved["plan_only"]["approved_decisions"] == saved["repaired"]["approved_decisions"]
    assert saved["repaired"]["approved_decisions"] == saved["final"]["approved_decisions"]
    assert len(saved["repaired"]["approved_decisions"]) == 5
