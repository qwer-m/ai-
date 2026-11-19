"""真实Run69回放：人工已回答后先修稿，不制造审查通过或修改线上历史。"""

from copy import deepcopy
from datetime import datetime
import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from review_cpu_projection import review_cpu_candidate
from test_single_agent_direct_protocol_real_data import scope_state_from_real
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext


@pytest.fixture(scope="module")
def saved():
    path = Path(__file__).parent / "fixtures/run69_review_repair_resume_real.json.gz"
    raw = path.read_bytes()
    value = json.loads(gzip.decompress(raw))
    assert action_state_hash({k: v for k, v in value.items() if k != "data_sha256"}) == value["data_sha256"]
    assert value["provenance"]["new_model_responses"] is False
    yield value
    assert path.read_bytes() == raw


@pytest.fixture
def context(saved):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    values = deepcopy(saved["run"])
    for column in AgentRun.__table__.columns:
        if column.type.python_type is datetime and isinstance(values.get(column.name), str):
            values[column.name] = datetime.fromisoformat(values[column.name])
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        db.add(AgentRun(**values))
        db.commit()
        context = ToolExecutionContext(db=db, user_id=values["user_id"], project_id=values["project_id"],
            run_id=values["id"], node_key="agent_network", run_input=deepcopy(values["input_payload"]),
            action_input=deepcopy(saved["input"]), action_state=scope_state_from_real(saved["states"]["paused"]))
        # 只重算当前模型视图的薄投影，历史原文、用例和真实批准不改。
        generation.refresh_generation_state(context.action_state)
        yield context
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def approve_actual(context, saved):
    approval = saved["approval"]
    record = next(item for item in saved["states"]["terminal"]["approved_decisions"] if item["approval_id"] == approval["id"])
    current_hash = action_state_hash(context.action_state)
    generation.apply_user_decisions(context.action_state, approval["request_payload"]["items"],
        approval["decision_payload"]["conflict_answers"], approval_id=approval["id"], node_id=427,
        run_id=69, user_id=approval["decided_by_user_id"], decided_at=datetime.fromisoformat(record["decided_at"]))
    result = context.action_state["approved_decisions"][-1]
    # 当前引用不复写原问题摘要；真实审批身份和原话逐字段保持，旧fixture不改写。
    assert {k: v for k, v in result.items() if k not in {"approval_state_hash", "evidence_id"}} == {
        k: v for k, v in record.items() if k not in {"approval_state_hash", "evidence_id"}}
    assert result["evidence_id"] == f"AP-{approval['id']}-1"
    assert result["approval_state_hash"] == current_hash


def actual_second_review(context, saved):
    approve_actual(context, saved)
    candidate = deepcopy(saved["states"]["terminal"]["review"]["raw_result"])
    assert candidate["verdict"] == "changes_requested"
    refs = {f"AP-{item['approval_id']}-{item['original_issue_id']}": item["evidence_id"]
            for item in context.action_state["approved_decisions"]}

    def current_refs(value):
        # 仅投影由真实审批元数据一一确定的引用编号，不改业务判断或原始证据。
        if isinstance(value, list):
            return [current_refs(item) for item in value]
        if isinstance(value, dict):
            return {key: current_refs(item) for key, item in value.items()}
        return refs.get(value, value) if isinstance(value, str) else value

    generation._apply_review(context, generation._review_input(context.action_state),
                             review_cpu_candidate(context.action_state, current_refs(candidate)))
    return candidate


def actual_reference_patch(context, identity):
    record = context.action_state["cases"]["items"][identity]
    content = deepcopy(record["content"])
    content["source_refs"] = generation._model_source_refs(content["source_refs"])
    # 只在隔离CPU测试绑定实际存在的批准/后续原文；不宣称旧正文已修好或审查通过。
    source = next(item["evidence_id"] for item in context.action_state["approved_decisions"] if item["approval_id"] == 17)
    source = source if identity in {"TC-0007", "TC-0062", "TC-0065"} else "EV-0033"
    content["source_refs"].append(source)
    return {"case_id": identity, "expected_version": record["version"], "content": content}


def test_actual_approval_does_not_trigger_review_of_unchanged_candidates(context, saved):
    before = deepcopy(context.action_state["cases"])
    approve_actual(context, saved)
    observation = generation.observe_generation_state(context)
    assert context.action_state["cases"] == before
    assert "request_review" in observation["available_actions"]
    assert observation["automatic_action"] is None
    issue = next(iter(context.action_state["open_issues"].values()))
    assert generation._requires_human(context.action_state, issue, "open") is False
    assert generation._requires_human(context.action_state, issue, "needs_human") is False


def test_actual_open_resolution_after_approval_does_not_ask_same_business_again(context, saved):
    actual_second_review(context, saved)
    original = saved["approval"]["request_payload"]["items"][0]["id"]
    assert context.action_state["open_issues"][original]["status"] == "open"
    assert context.action_state["projection"]["progress"]["pending_human"] == 0
    assert generation.observe_generation_state(context)["automatic_action"] is None
    assert context.action_state["review"]["result"]["verdict"] == "changes_requested"
    assert not generation._can_finish(context.action_state)


def test_repair_cases_is_pure_exact_current_target_material(context, saved):
    actual_second_review(context, saved)
    before = action_state_hash(context.action_state)
    observation = generation.observe_generation_state(context)
    expected = {identity for issue in context.action_state["open_issues"].values() for identity in issue["target_case_ids"]}
    assert {item["case_id"] for item in observation["repair_cases"]} == expected
    assert expected == {"TC-0007", "TC-0062", "TC-0065", "TC-0018", "TC-0067"}
    for item in observation["repair_cases"]:
        record = context.action_state["cases"]["items"][item["case_id"]]
        assert item["version"] == record["version"]
        assert item["content"]["steps"] == record["content"]["steps"]
        assert item["content"]["source_refs"] == generation._model_source_refs(record["content"]["source_refs"])
    for issue in observation["open_issues"]:
        assert "review_history" not in issue
        assert all(isinstance(ref, str) for ref in issue["source_refs"])
    assert action_state_hash(context.action_state) == before


def test_partial_target_patch_or_only_coverage_change_does_not_auto_review(context, saved):
    actual_second_review(context, saved)
    generation.patch_cases(context, {"patches": [actual_reference_patch(context, "TC-0007")]})
    assert generation.observe_generation_state(context)["automatic_action"] is None
    goal = next(item for item in context.action_state["coverage_plan"]["items"] if item["coverage_id"] == "COV-0004")
    update = {**deepcopy(goal), "source_refs": generation._model_source_refs(goal["source_refs"])}
    update["source_refs"].append("EV-0023")
    generation.commit_coverage_plan(context, {"expected_version": context.action_state["coverage_plan"]["version"],
        "items": [update], "remove_coverage_ids": []})
    assert generation.observe_generation_state(context)["automatic_action"] is None
    generation.patch_cases(context, {"patches": [actual_reference_patch(context, identity)
        for identity in ["TC-0062", "TC-0065", "TC-0018", "TC-0067"]]})
    assert generation.observe_generation_state(context)["automatic_action"] == "request_review"
    assert context.action_state["open_issues"]
    assert not generation._can_finish(context.action_state)


def test_actual_unanswered_conflict_still_requires_human(context):
    issue = next(iter(context.action_state["open_issues"].values()))
    assert generation._requires_human(context.action_state, issue, "open") is True
    assert generation._requires_human(context.action_state, issue, "resolved") is False


def test_approved_scope_does_not_remove_actual_repair_limit(context, saved):
    actual_second_review(context, saved)
    generation.patch_cases(context, {"patches": [actual_reference_patch(context, "TC-0007")]})
    record = context.action_state["cases"]["items"]["TC-0007"]
    content = deepcopy(record["content"])
    content["source_refs"] = generation._model_source_refs(content["source_refs"])
    content["source_refs"].append("EV-0023")
    generation.patch_cases(context, {"patches": [{"case_id": "TC-0007", "expected_version": record["version"],
                                                 "content": content}]})
    issue_id = saved["approval"]["request_payload"]["items"][0]["id"]
    assert context.action_state["cases"]["items"]["TC-0007"]["automatic_repairs"] == 2
    issue = context.action_state["open_issues"][issue_id]
    assert generation._requires_human(context.action_state, issue) is False
    # 仅注入技术评估状态检查上限，不制造审查响应或成功交付。
    assessed = {**deepcopy(issue), "assessment_status": "reviewed",
        "target_versions": generation._target_versions(context.action_state, issue["target_case_ids"], issue["coverage_ids"])}
    assert generation._requires_human(context.action_state, assessed)


def test_first_real_human_review_registers_pause_without_extra_model_round(context, saved):
    state = context.action_state
    raw_review = deepcopy(state["review"]["raw_result"])
    for issue in state["open_issues"].values():
        for identity in issue["target_case_ids"]:
            state["cases"]["items"][identity]["review_failures"] -= 1
    state.update(open_issues={}, review=None, confirmation_requested=[])
    generation.refresh_generation_state(state)
    # 按真实首次审查回执的pre_state_hash核对倒退的技术字段，不能伪造前态。
    # 历史原始前态证明独立核对；当前合同投影增加scope字段，不冒充原字节身份。
    historical = deepcopy(saved["states"]["paused"])
    for issue in historical["open_issues"].values():
        for identity in issue["target_case_ids"]:
            historical["cases"]["items"][identity]["review_failures"] -= 1
    historical.update(open_issues={}, review=None, confirmation_requested=[])
    generation.refresh_generation_state(historical)
    assert action_state_hash(historical) == saved["first_review_receipt"]["pre_state_hash"]
    assert generation.observe_generation_state(context)["automatic_action"] == "request_review"
    candidate = review_cpu_candidate(state, raw_review)
    generation._apply_review(context, generation._review_input(state), candidate)
    assert context.action_state["confirmation_requested"] == [item["id"] for item in saved["approval"]["request_payload"]["items"]]
    assert context.action_state["review"]["raw_result"] == candidate
    assert context.action_state["review"]["result"]["verdict"] == "needs_human"
    assert not generation._can_finish(context.action_state)


@pytest.fixture(scope="module")
def run70():
    path = Path(__file__).parent / "fixtures/run70_final_repair_view_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({k: v for k, v in value.items() if k != "data_sha256"}) == value["data_sha256"]
    assert value["provenance"]["model_responses_fabricated"] is False
    return value


def test_actual_run70_only_duplicate_replacement_needs_review_even_after_second_patch(run70):
    state = deepcopy(run70["state"])
    issue = next(item for item in state["open_issues"].values() if item["status"] != "resolved")
    assert issue["target_case_ids"] == ["TC-0015", "TC-0069", "TC-0061"]
    assert state["cases"]["items"]["TC-0015"]["version"] == issue["target_versions"]["cases"]["TC-0015"]
    assert state["cases"]["items"]["TC-0061"]["version"] == issue["target_versions"]["cases"]["TC-0061"]
    assert state["cases"]["items"]["TC-0069"]["automatic_repairs"] == 2
    assert state["cases"]["items"]["TC-0069"]["version"] > issue["target_versions"]["cases"]["TC-0069"]
    assert issue["assessment_status"] == "pending_recheck"
    assert not generation._requires_human(state, issue)
    assert generation._automatic_review_ready(state)
    # 当前修改仍须独立审查，不用本次CPU判断伪造批准。
    assert not generation._can_finish(state)


def test_real_run70_model_view_retains_complete_text_and_business_body_without_technical_metadata(run70):
    state = deepcopy(run70["state"])
    before = action_state_hash(state)
    payload = generation._review_input(state)
    originals = {item["evidence_id"]: item for item in state["evidence_catalog"]["items"]}
    for item in payload["evidence_catalog"]["items"]:
        original = originals[item["evidence_id"]]
        if item["kind"] == "text":
            assert set(item) == {"evidence_id", "kind", "page_number", "text"}
            assert item["text"] == original["text"]
            assert original["asset_source_sha256"] and original["block_ids"]
        elif item["kind"] == "image":
            for key in ("block_ids", "image_region"):
                if key in original:
                    assert item[key] == original[key]
    for item in payload["cases"]:
        raw = state["cases"]["items"][item["case_id"]]
        assert "content_hash" not in item
        assert raw["content_hash"] == action_state_hash(raw["content"])
        assert item["version"] == raw["version"]
        for key in raw["content"].keys() - {"source_refs"}:
            assert item[key] == raw["content"][key]
    from modules.agent_platform.token_counting import estimate_json_tokens
    old = deepcopy(payload)
    old["evidence_catalog"]["items"] = [generation._model_evidence(item) if item["kind"] == "approved_decision"
        else deepcopy(item) for item in state["evidence_catalog"]["items"]]
    for item in old["cases"]:
        item["content_hash"] = state["cases"]["items"][item["case_id"]]["content_hash"]
    assert estimate_json_tokens(payload) < estimate_json_tokens(old)
    assert action_state_hash(state) == before
