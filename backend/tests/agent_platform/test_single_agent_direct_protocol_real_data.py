"""真实Run63候选的当前平台协议回放；不请求模型、不伪造独立审查成功。"""

from copy import deepcopy
from datetime import datetime
import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.test_generation_schemas import GENERATION_EXECUTION_LIMITS


@pytest.fixture(scope="module")
def real():
    path = Path(__file__).with_name("fixtures") / "run63_direct_protocol_cpu_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({key: item for key, item in value.items() if key != "data_sha256"}) == value["data_sha256"]
    assert action_state_hash(value["current_initial_state"]) == value["initial_state_sha256"]
    assert action_state_hash(value["current_read_state"]) == value["read_state_sha256"]
    assert value["provenance"]["historical_status"] == "failed"
    assert value["provenance"]["historical_delivered"] is False
    assert value["provenance"]["new_model_review_performed"] is False
    assert value["provenance"]["unmapped_original_fact_bindings_before"] == {}
    # 当前协议回放用同一文档另一轮真实文字用例填充两个纯图槽位，原始候选保留作拒收回归。
    later = json.loads(gzip.decompress(path.with_name("run71_review_completeness_real.json.gz").read_bytes()))
    supplements = iter(("TC-0008", "TC-0056"))
    replay = []
    for row in value["current_cases_before"]:
        content = row["content"]
        if all(not ref["quote"] for ref in content["source_refs"]):
            key = next(supplements)
            replacement = deepcopy(later["data"]["state"]["cases"]["items"][key]["content"])
            replacement["coverage_ids"] = list(content["coverage_ids"])
            row = {"historical_case_id": "run71:" + key, "content": replacement}
        replay.append(row)
    assert next(supplements, None) is None
    value["replay_cases"] = replay
    return value


def context_from_real(real, db, *, state=None):
    metadata = real["run_metadata"]
    return ToolExecutionContext(db=db, user_id=metadata["user_id"], project_id=metadata["project_id"],
        run_id=metadata["id"], node_key="agent_network", run_input=deepcopy(real["current_input"]),
        action_input=deepcopy(real["current_input"]),
        action_state=scope_state_from_real(real["current_read_state"] if state is None else state),
        artifacts={"requirement_source": deepcopy(real["current_initial_state"]["source_ref"])})


def scope_state_from_real(state):
    """仅在隔离CPU回放投影历史作用域字段，真实存档和模型审查结论保持原样。"""
    result = deepcopy(state)
    if result.get("schema_version") == 4 and "coverage_plan" in result:
        for item in result["coverage_plan"]["items"]:
            if "conditions" not in item:
                item["conditions"] = list(dict.fromkeys(condition
                    for case in result["cases"]["items"].values()
                    if item["coverage_id"] in case["content"]["coverage_ids"]
                    for condition in case["content"]["preconditions"]))
            if "exceptions" not in item:
                item["exceptions"] = []
        generation.refresh_generation_state(result)
    return result


def model_input(value):
    """机械投影历史数据到当前工具合同，不改原fixture或冒充语义审查成功。"""
    if isinstance(value, list):
        return [model_input(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {key: (list(dict.fromkeys(
        ref if isinstance(ref, str) else ref["evidence_id"] for ref in item)) if key == "source_refs" else model_input(item))
        for key, item in value.items()}
    if {"coverage_id", "objective", "source_refs"} <= set(value):
        result["conditions"] = list(value["conditions"]) if "conditions" in value else []
        result["exceptions"] = list(value["exceptions"]) if "exceptions" in value else []
    if isinstance(value.get("coverage_updates"), list) and isinstance(value.get("cases"), list):
        for original, item in zip(value["coverage_updates"], result["coverage_updates"]):
            if "conditions" not in original:
                item["conditions"] = list(dict.fromkeys(condition for case in value["cases"]
                    if item["coverage_id"] in case["coverage_ids"] for condition in case["preconditions"]))
    return result


def coverage_from_real(real):
    return [{"coverage_id": item["content"]["coverage_ids"][0], "objective": item["content"]["title"],
             "conditions": list(item["content"]["preconditions"]), "exceptions": [],
             "source_refs": model_input(item["content"])["source_refs"]} for item in real["replay_cases"]]


def append_real_candidates(context, real, *, count=70):
    generation.commit_coverage_plan(context, {"expected_version": 0, "items": coverage_from_real(real), "remove_coverage_ids": []})
    maximum = context.action_state["goal"]["batch_case_limit"]
    for offset in range(0, count, maximum):
        generation.append_cases(context, {"expected_coverage_version": context.action_state["coverage_plan"]["version"],
            "coverage_updates": [], "remove_coverage_ids": [], "cases": [model_input(item["content"])
            for item in real["replay_cases"][offset:min(offset + maximum, count)]]})
    return context


@pytest.fixture(scope="module")
def persisted(real):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    metadata = deepcopy(real["run_metadata"])
    for key in ("created_at", "started_at", "finished_at"):
        metadata[key] = datetime.fromisoformat(metadata[key]) if metadata[key] is not None else None
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        # 隔离库复制真实运行归属和冻结来源；状态通过当前真实领域方法建立。
        db.add(AgentRun(**metadata, input_payload=deepcopy(real["original_run_input"]),
                        run_context=deepcopy(real["frozen_run_context"]), output_payload={}))
        db.commit()
        populated = append_real_candidates(context_from_real(real, db), real).action_state
        yield db, deepcopy(populated)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


@pytest.fixture
def context(real, persisted):
    db, state = persisted
    return context_from_real(real, db, state=state)


def actual_patch(real, context):
    position = next(index for index, item in enumerate(real["current_cases_before"])
                    if item["historical_case_id"] == "TC-050")
    identity = f"TC-{position + 1:04d}"
    return {"case_id": identity, "expected_version": context.action_state["cases"]["items"][identity]["version"],
            "content": model_input(real["current_cases_after"][position]["content"])}


def actual_global_review_findings(real, state):
    # 仅机械投影真实历史的“不批准”候选，不能当作当前模型已审查的回执。
    old = next(item for item in real["historical_review_verdicts"] if item["action_sequence"] == 35)
    assert old["approved"] is False
    ids = {item["historical_case_id"]: f"TC-{index + 1:04d}"
           for index, item in enumerate(real["current_cases_before"])}
    findings = []
    for difference in old["differences"]:
        identity = ids[difference["case_id"]]
        content = state["cases"]["items"][identity]["content"]
        findings.append({"kind": "case_error", "target_case_ids": [identity],
            "coverage_ids": list(content["coverage_ids"]), "reason": difference["detail"],
            "source_refs": model_input(content)["source_refs"]})
    return findings


def actual_global_review_candidate(real, state):
    from review_cpu_projection import review_cpu_candidate
    return review_cpu_candidate(state, {"verdict": "changes_requested",
        "checked_case_ids": list(state["cases"]["items"]),
        "issues": actual_global_review_findings(real, state), "issue_resolutions": []})


def test_real_seventy_candidates_are_platform_accepted_but_not_delivered(context, real):
    state = context.action_state
    assert len(state["cases"]["items"]) == state["goal"]["case_budget"] == 70
    assert state["completed"] is False and state["delivery"] is None
    assert "finish_generation" not in generation.observe_generation_state(context)["available_actions"]
    with pytest.raises(OutputValidationError):
        generation.finish_generation(context, {})
    assert "test_generation" not in context.artifacts
    assert len(state["approved_decisions"]) == 3
    assert state["source_ref"] == real["current_initial_state"]["source_ref"]
    assert generation.observe_generation_state(context)["automatic_action"] == "request_review"


def test_real_tc050_patch_keeps_all_other_sixty_nine_records_exact(context, real):
    before = deepcopy(context.action_state["cases"])
    patch = actual_patch(real, context)
    result = generation.patch_cases(context, {"patches": [patch]})
    assert result["changed_case_ids"] == [patch["case_id"]]
    after = context.action_state["cases"]
    assert before["items"].keys() == after["items"].keys()
    assert [identity for identity in before["items"] if before["items"][identity] != after["items"][identity]] == [patch["case_id"]]
    assert after["items"][patch["case_id"]]["version"] == patch["expected_version"] + 1
    assert after["items"][patch["case_id"]]["content_hash"] == action_state_hash(
        generation._case_content(context.action_state, patch["content"]))


def test_patch_rejects_stale_version_and_rolls_back_complete_state(context, real):
    patch = actual_patch(real, context)
    generation.patch_cases(context, {"patches": [patch]})
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="用例已变化"):
        generation.patch_cases(context, {"patches": [patch]})
    assert action_state_hash(context.action_state) == before


def test_multiple_real_historical_findings_survive_local_patch(context, real):
    candidate = actual_global_review_candidate(real, context.action_state)
    first = actual_global_review_findings(real, context.action_state)[0]
    second = deepcopy(first)
    second["reason"] = next(item["difference"]["detail"] for item in real["historical_review_findings"]
                            if item["difference"]["case_id"] == "TC-050")
    assert first["reason"] != second["reason"]
    ids = [generation.report_issue(context, raw)["issue_id"] for raw in (first, second)]
    before = deepcopy(context.action_state["open_issues"])
    generation.patch_cases(context, {"patches": [actual_patch(real, context)]})
    current = context.action_state["open_issues"]
    assert set(current) == set(ids)
    assert all(current[identity]["status"] == "pending_review" for identity in ids)
    for identity in ids:
        assert current[identity]["reason"] == before[identity]["reason"]
        assert current[identity]["source_refs"] == before[identity]["source_refs"]
    payload = generation._review_input(context.action_state)
    with pytest.raises(OutputValidationError, match="遗漏当前未决问题"):
        generation._validate_review(context.action_state, payload, candidate)


def test_real_nonapproved_review_candidate_binds_full_snapshot_without_false_success(context, real):
    payload = generation._review_input(context.action_state)
    candidate = actual_global_review_candidate(real, context.action_state)
    result = generation._apply_review(context, payload, candidate)
    assert result["verdict"] == "changes_requested"
    assert context.action_state["review"]["input_hash"] == action_state_hash(payload)
    assert generation._review_current(context.action_state)
    assert not generation._can_finish(context.action_state)
    assert len(context.action_state["open_issues"]) == len(actual_global_review_findings(real, context.action_state))
    assert "request_review" not in generation.observe_generation_state(context)["available_actions"]
    assert generation.observe_generation_state(context)["automatic_action"] is None
    assert context.action_state["completed"] is False and "test_generation" not in context.artifacts


def test_late_review_cannot_apply_after_real_case_patch(context, real):
    payload = generation._review_input(context.action_state)
    candidate = actual_global_review_candidate(real, context.action_state)
    generation.patch_cases(context, {"patches": [actual_patch(real, context)]})
    before = action_state_hash(context.action_state)
    with pytest.raises(ValueError, match="评审迟到"):
        generation._apply_review(context, payload, candidate)
    assert action_state_hash(context.action_state) == before


def test_real_case_patch_reopens_automatic_review_only_after_current_snapshot_changes(context, real):
    generation._apply_review(context, generation._review_input(context.action_state),
                             actual_global_review_candidate(real, context.action_state))
    assert generation.observe_generation_state(context)["automatic_action"] is None
    generation.patch_cases(context, {"patches": [actual_patch(real, context)]})
    assert generation.observe_generation_state(context)["automatic_action"] == "request_review"


def test_review_cannot_omit_case_ids_from_actual_seventy_case_snapshot(context, real):
    candidate = actual_global_review_candidate(real, context.action_state)
    candidate["case_checks"].pop()
    with pytest.raises(OutputValidationError, match="全部.*用例"):
        generation._validate_review(context.action_state, generation._review_input(context.action_state), candidate)


def test_current_evidence_requires_real_read_and_rejects_model_rewritten_quotes(real, persisted):
    db, _ = persisted
    context = context_from_real(real, db, state=real["current_initial_state"])
    with pytest.raises(OutputValidationError, match="必须先读取"):
        generation.commit_coverage_plan(context, {"expected_version": 0, "items": coverage_from_real(real), "remove_coverage_ids": []})
    context = context_from_real(real, db)
    first = next(ref for item in real["current_cases_before"] for ref in item["content"]["source_refs"] if ref["quote"])
    with pytest.raises(OutputValidationError, match="来源引用|证据编号"):
        generation._source_refs(context.action_state, [deepcopy(first)])


def test_fixed_material_preserves_all_real_text_and_approvals_without_viewing_images(real, persisted):
    db, _ = persisted
    context = context_from_real(real, db, state=real["current_initial_state"])
    before = action_state_hash(context.action_state)
    material = generation.generation_model_material(context)
    assert material["source_ref"] == context.action_state["source_ref"]
    assert material["goal"] == context.action_state["goal"]
    assert action_state_hash(context.action_state) == before
    for item in context.action_state["evidence_catalog"]["items"]:
        if item["kind"] != "image":
            assert f"【{item['evidence_id']}｜{item['kind']}｜第{item.get('page_number')}页】\n{item['text']}" in material["source_text"]
    assert material["image_catalog"]
    assert all(item["kind"] == "image" for item in material["image_catalog"])
    assert context.action_state["image_pages"] == {}
    assert context.action_state["evidence_reads"] == {}


def test_only_unprovided_real_text_remains_a_read_action(real, persisted):
    db, _ = persisted
    context = context_from_real(real, db, state=real["current_initial_state"])
    text_items = [item for item in context.action_state["evidence_catalog"]["items"] if item["kind"] != "image"]
    assert generation.observe_generation_state(context)["available_actions"]["read_source"]["evidence_ids"] == [
        item["evidence_id"] for item in text_items]
    for item in text_items:
        generation._read_record(context.action_state, item["evidence_id"], item["text"], 0, len(item["text"]),
                                provided_by="initial_model_material")
    generation.refresh_generation_state(context.action_state)
    assert "read_source" not in generation.observe_generation_state(context)["available_actions"]
    assert context.action_state["image_pages"] == {}
    first = text_items[0]
    context.action_state["evidence_reads"][first["evidence_id"]]["ranges"] = [[0, 1]]
    generation.refresh_generation_state(context.action_state)
    assert generation.observe_generation_state(context)["available_actions"]["read_source"]["evidence_ids"] == [first["evidence_id"]]
    generation._read_record(context.action_state, first["evidence_id"], first["text"], 0, len(first["text"]))
    context.action_state["evidence_reads"][first["evidence_id"]]["content_hash"] = context.action_state["source_ref"]["content_hash"]
    generation.refresh_generation_state(context.action_state)
    assert generation.observe_generation_state(context)["available_actions"]["read_source"]["evidence_ids"] == [first["evidence_id"]]


def test_first_batch_commits_actual_coverage_and_cases_in_one_atomic_action(real, persisted):
    db, _ = persisted
    context = context_from_real(real, db)
    maximum = context.action_state["goal"]["batch_case_limit"]
    contents = [model_input(item["content"]) for item in real["replay_cases"][:maximum]]
    result = generation.append_cases(context, {"expected_coverage_version": 0,
        "coverage_updates": coverage_from_real(real)[:maximum], "remove_coverage_ids": [], "cases": contents})
    assert result["coverage_version"] == result["cases_version"] == 1
    assert result["created_case_ids"] == [f"TC-{index:04d}" for index in range(1, maximum + 1)]
    assert len(context.action_state["coverage_plan"]["items"]) == result["case_count"] == maximum
    assert [record["content"] for record in context.action_state["cases"]["items"].values()] == [
        generation._case_content(context.action_state, item) for item in contents]
    assert "request_review" not in generation.observe_generation_state(context)["available_actions"]
    assert generation.observe_generation_state(context)["automatic_action"] is None


def test_rejected_real_duplicate_batch_does_not_commit_coverage_or_consume_ids(real, persisted):
    db, _ = persisted
    context = context_from_real(real, db)
    first = model_input(real["current_cases_before"][0]["content"])
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="标题重复"):
        generation.append_cases(context, {"expected_coverage_version": 0,
            "coverage_updates": coverage_from_real(real)[:1], "remove_coverage_ids": [],
            "cases": [deepcopy(first), deepcopy(first)]})
    assert action_state_hash(context.action_state) == before
    assert context.action_state["coverage_plan"] == {"version": 0, "items": []}
    assert context.action_state["cases"]["next_id"] == 1


def test_human_approved_source_requires_actual_resolution_instead_of_old_question(context):
    decision = context.action_state["approved_decisions"][0]
    assert generation._source_refs(context.action_state, [decision["evidence_id"]]) == [
        {"evidence_id": decision["evidence_id"], "quote": decision["resolution"]}]
    with pytest.raises(OutputValidationError, match="来源引用|证据编号"):
        generation._source_refs(context.action_state, [{"evidence_id": decision["evidence_id"], "quote": decision["question"]["reason"]}])


def test_exact_target_and_batch_limits_do_not_expand_budget(real, persisted, context):
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError):
        generation.append_cases(context, {"expected_coverage_version": context.action_state["coverage_plan"]["version"],
            "coverage_updates": [], "remove_coverage_ids": [], "cases": [model_input(real["current_cases_before"][0]["content"])]})
    assert action_state_hash(context.action_state) == before
    db, _ = persisted
    partial = append_real_candidates(context_from_real(real, db), real, count=5)
    assert not generation._can_finish(partial.action_state)
    assert "finish_generation" not in generation.observe_generation_state(partial)["available_actions"]
    assert GENERATION_EXECUTION_LIMITS["max_total_tokens"] == GENERATION_EXECUTION_LIMITS["max_input_tokens"] == 300000
    assert GENERATION_EXECUTION_LIMITS["max_requests"] == 32


def test_old_run_options_and_old_state_are_rejected_without_migration(real, persisted):
    db, _ = persisted
    context = context_from_real(real, db, state={})
    unknown = set(real["original_run_input"]) - set(real["current_input"])
    assert unknown
    key = sorted(unknown)[0]
    with pytest.raises(ValueError, match="已移除或未知配置"):
        generation.initialize_generation_state(context, {**real["current_input"], key: real["original_run_input"][key]})
    historical = json.loads(gzip.decompress((Path(__file__).with_name("fixtures") / "run59_artifact_read_cost_real.json.gz").read_bytes()))
    context.action_state = historical["state"]
    with pytest.raises(ValueError, match="schema_version=4"):
        generation.observe_generation_state(context)


def test_issue_keeps_coverage_target_alive_even_without_a_case_binding(context, real):
    items = deepcopy(context.action_state["coverage_plan"]["items"])
    # 复制真实覆盖目标，仅分配平台编号；不新增业务断言或模型裁决。
    additional = deepcopy(items[49])
    additional["coverage_id"] = "COV-0071"
    generation.commit_coverage_plan(context, {"expected_version": 1, "items": [model_input(additional)], "remove_coverage_ids": []})
    finding = actual_global_review_findings(real, context.action_state)[0]
    finding["target_case_ids"] = []
    finding["coverage_ids"] = [additional["coverage_id"]]
    identity = generation.report_issue(context, finding)["issue_id"]
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="不能直接删除"):
        generation.commit_coverage_plan(context, {"expected_version": 2, "items": [], "remove_coverage_ids": [additional["coverage_id"]]})
    assert action_state_hash(context.action_state) == before
    assert context.action_state["open_issues"][identity]["coverage_ids"] == [additional["coverage_id"]]


def test_review_keeps_actual_quotes_and_observer_only_projects_source_identities(context, real):
    generation.report_issue(context, actual_global_review_findings(real, context.action_state)[0])
    state = context.action_state
    before = action_state_hash(state)
    payload = generation._review_input(state)
    assert "source_references" not in payload
    assert "approved_decisions" not in payload and "_user_confirmations" not in payload
    decisions = {item["evidence_id"]: item for item in payload["evidence_catalog"]["items"]
                 if item["kind"] == "approved_decision"}
    assert set(payload["approved_decision_ids"]) == set(decisions) == {
        item["evidence_id"] for item in state["approved_decisions"]}
    for original in state["approved_decisions"]:
        actual = decisions[original["evidence_id"]]
        assert actual["resolution"] == original["resolution"]
        assert actual["question"] == {key: original["question"].get(key) for key in ("kind", "reason", "coverage_fragment")}
        assert "text" not in actual and "source_refs" not in actual
    for rows, expected in ((payload["cases"], generation._review_case_view(state)),
                           (payload["coverage_plan"]["items"], state["coverage_plan"]["items"]),
                           (payload["open_issues"], generation._review_issue_view(state))):
        assert rows == model_input(expected)
    observed = generation.observe_generation_state(context)
    assert "source_references" not in observed
    for item, original in zip(observed["coverage_plan"]["items"], state["coverage_plan"]["items"]):
        assert item == {"coverage_id": original["coverage_id"], "objective": original["objective"],
            "conditions": original["conditions"], "exceptions": original["exceptions"],
            "evidence_ids": sorted({ref["evidence_id"] for ref in original["source_refs"]})}
    assert action_state_hash(state) == before


def test_main_case_observation_omits_raw_hashes_and_only_lists_nonzero_repair_counters(context, real):
    observed = generation.observe_generation_state(context)
    assert all(set(item) == {"case_id", "title", "module", "coverage_ids"} for item in observed["cases"])
    generation._apply_review(context, generation._review_input(context.action_state),
                             actual_global_review_candidate(real, context.action_state))
    for item in generation.observe_generation_state(context)["cases"]:
        original = context.action_state["cases"]["items"][item["case_id"]]
        assert "content_hash" not in item and "version" not in item
        assert item.get("review_failures", 0) == original["review_failures"]
        assert item.get("automatic_repairs", 0) == original["automatic_repairs"]
        assert item.get("review_failures", 1) > 0 and item.get("automatic_repairs", 1) > 0


def test_review_images_keep_real_pages_and_only_currently_referenced_crops(context):
    state = context.action_state
    by_id = {item["evidence_id"]: item for item in state["evidence_catalog"]["items"]}
    referenced = [ref for item in state["cases"]["items"].values() for ref in item["content"]["source_refs"]]
    expected = {}
    for ref in referenced:
        evidence = by_id[ref["evidence_id"]]
        if evidence["kind"] == "image":
            expected.setdefault(evidence["page_number"], set()).update(evidence["block_ids"])
        elif (evidence["kind"] == "text" and evidence.get("page_number") is not None
                and str(evidence["page_number"]) in state["image_pages"]):
            expected.setdefault(evidence["page_number"], set())
    pages = generation._review_input(state)["image_pages"]
    assert {page["page_number"] for page in pages} == set(expected)
    assert any(page["blocks"] for page in pages)
    for page in pages:
        assert {block["block_id"] for block in page["blocks"]} == expected[page["page_number"]]
        originals = {block["block_id"]: block for block in state["image_pages"][str(page["page_number"])]["blocks"]}
        for block in page["blocks"]:
            assert set(block) == {"block_id", "type", "bbox"}
            assert block == {key: originals[block["block_id"]][key] for key in block}


def test_actual_image_only_candidates_and_image_coverage_are_rejected(real, context):
    state = context.action_state
    for row in real["current_cases_before"]:
        refs = row["content"]["source_refs"]
        if all(not ref["quote"] for ref in refs):
            with pytest.raises(OutputValidationError, match="图片只作佐证"):
                generation._source_refs(state, [ref["evidence_id"] for ref in refs])
    payload = generation._review_input(state)
    candidate = actual_global_review_candidate(real, state)
    image = next(item for item in payload["evidence_catalog"]["items"] if item["kind"] == "image")
    row = next(item for item in candidate["source_checks"] if item["evidence_id"] == image["evidence_id"])
    row["branches"][0]["disposition"] = "gap"
    with pytest.raises(OutputValidationError, match="图片只作参考"):
        generation._validate_review(state, payload, candidate)


@pytest.fixture(scope="module")
def real_run64_coverage():
    path = Path(__file__).with_name("fixtures") / "run64_coverage_incremental_real.json.gz"
    value = json.loads(gzip.decompress(path.read_bytes()))
    assert action_state_hash({key: item for key, item in value.items() if key != "data_sha256"}) == value["data_sha256"]
    assert action_state_hash(value["before_state"]) == value["provenance"]["source_pre_state_hash"]
    assert action_state_hash(value["historical_after_state"]) == value["provenance"]["source_post_state_hash"]
    assert value["provenance"]["historical_status"] == "failed"
    assert len(value["before_state"]["coverage_plan"]["items"]) == 19
    assert len(value["historical_after_state"]["coverage_plan"]["items"]) == 1
    assert len(value["historical_action_arguments"]["items"]) == 1
    return value


@pytest.fixture
def run64_coverage_context(real_run64_coverage):
    source = real_run64_coverage
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    metadata = deepcopy(source["run_metadata"])
    for key in ("created_at", "started_at", "finished_at"):
        metadata[key] = datetime.fromisoformat(metadata[key]) if metadata[key] is not None else None
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        # 真实失败运行及冻结来源只复制到隔离库；不接管或修改真实Run64。
        db.add(AgentRun(**metadata, input_payload=deepcopy(source["run_input"]),
            run_context=deepcopy(source["frozen_run_context"]), output_payload={}))
        db.commit()
        yield ToolExecutionContext(db=db, user_id=metadata["user_id"], project_id=metadata["project_id"],
            run_id=metadata["id"], node_key="agent_network", run_input=deepcopy(source["run_input"]),
            action_input=deepcopy(source["run_input"]), action_state=scope_state_from_real(source["before_state"]),
            artifacts={"requirement_source": deepcopy(source["before_state"]["source_ref"])})
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def test_real_run64_local_coverage_update_preserves_other_eighteen_goals(run64_coverage_context, real_run64_coverage):
    context = run64_coverage_context
    before = deepcopy(context.action_state)
    arguments = {**model_input(real_run64_coverage["historical_action_arguments"]), "remove_coverage_ids": []}
    # 只投影新合同作用域与显式删除字段，真实目标、引用与历史fixture不变。
    result = generation.commit_coverage_plan(context, arguments)
    current = context.action_state
    old = {item["coverage_id"]: item for item in before["coverage_plan"]["items"]}
    new = {item["coverage_id"]: item for item in current["coverage_plan"]["items"]}
    assert old.keys() == new.keys() and len(new) == 19
    assert result["changed_coverage_ids"] == ["COV-0019"]
    assert current["coverage_plan"]["version"] == 2
    historical = real_run64_coverage["historical_after_state"]["coverage_plan"]["items"][0]
    assert new["COV-0019"] == {**model_input(historical),
        "source_refs": generation._source_refs(current, model_input(historical)["source_refs"])}
    for identity in old.keys() - {"COV-0019"}:
        assert json.dumps(old[identity], ensure_ascii=False, separators=(",", ":")) == json.dumps(
            new[identity], ensure_ascii=False, separators=(",", ":"))
    for key in ("cases", "approved_decisions", "context_retrievals", "confirmation_requested"):
        assert current[key] == before[key]
    assert set(current["open_issues"]) == set(before["open_issues"])
    assert current["completed"] is False and current["delivery"] is None
    assert "test_generation" not in context.artifacts


def test_real_run64_coverage_removal_requires_explicit_current_unreferenced_id(run64_coverage_context):
    context = run64_coverage_context
    before = deepcopy(context.action_state["coverage_plan"])
    identity = before["items"][0]["coverage_id"]
    assert identity == "COV-0001"
    result = generation.commit_coverage_plan(context, {"expected_version": 1, "items": [], "remove_coverage_ids": [identity]})
    assert result["changed_coverage_ids"] == [identity]
    assert context.action_state["coverage_plan"]["items"] == before["items"][1:]
    assert context.action_state["coverage_plan"]["version"] == 2
    assert "COV-0019" in {item["coverage_id"] for item in context.action_state["coverage_plan"]["items"]}


def test_real_run64_source_read_keeps_recorded_full_text_without_duplicate_catalog(run64_coverage_context, real_run64_coverage):
    context = run64_coverage_context
    before = deepcopy(context.action_state)
    actual = real_run64_coverage["historical_source_read_result"]
    result = generation.read_source(context, {"evidence_id": None, "offset": 0, "max_chars": 60000})
    assert result["content"] == actual["content"]
    assert result["source_ref"] == actual["source_ref"]
    for key in ("evidence_id", "offset", "end_offset", "total_chars", "next_offset"):
        assert result[key] == actual[key]
    assert "evidence_catalog" not in result
    observed = generation.observe_generation_state(context)
    catalog = observed["evidence_catalog"]
    assert len(catalog) == len(context.action_state["evidence_catalog"]["items"]) == len(actual["evidence_catalog"])
    assert [item["evidence_id"] for item in catalog if "evidence_id" in item] == [
        item["evidence_id"] for item in actual["evidence_catalog"] if item["kind"] != "image"]
    assert all(item["reference_available"] is False and "evidence_id" not in item
               for item in catalog if item["kind"] == "image")
    assert all("content_hash" not in item for item in catalog)
    for key in before.keys() - {"evidence_reads", "domain_sha256"}:
        assert context.action_state[key] == before[key]
    for identity, record in context.action_state["evidence_reads"].items():
        assert record == {**before["evidence_reads"][identity], "provided_by": "read_source"}
    assert context.action_state["source_ref"]["content_hash"] == actual["source_ref"]["content_hash"]
    assert all(record["content_hash"] for record in context.action_state["evidence_reads"].values())


@pytest.mark.parametrize("invalid", ["missing_remove_list", "unknown_remove", "modify_and_remove", "active_issue", "empty_result", "no_change", "stale_version"])
def test_real_run64_invalid_coverage_updates_leave_full_state_unchanged(run64_coverage_context, real_run64_coverage, invalid):
    context = run64_coverage_context
    arguments = {"expected_version": 1, "items": [], "remove_coverage_ids": []}
    if invalid == "missing_remove_list":
        arguments.pop("remove_coverage_ids")
    elif invalid == "unknown_remove":
        arguments["remove_coverage_ids"] = ["COV-0020"]  # 仅作为不存在的平台编号做拒绝控制。
    elif invalid == "modify_and_remove":
        arguments["items"] = model_input(real_run64_coverage["historical_action_arguments"]["items"])
        arguments["remove_coverage_ids"] = ["COV-0019"]
    elif invalid == "active_issue":
        arguments["remove_coverage_ids"] = ["COV-0019"]
    elif invalid == "empty_result":
        arguments["remove_coverage_ids"] = [item["coverage_id"] for item in context.action_state["coverage_plan"]["items"]]
    elif invalid == "stale_version":
        arguments["expected_version"] = 0
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError):
        generation.commit_coverage_plan(context, arguments)
    assert action_state_hash(context.action_state) == before


def test_case_bound_coverage_cannot_be_explicitly_removed(context):
    identity = context.action_state["coverage_plan"]["items"][0]["coverage_id"]
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="不能直接删除"):
        generation.commit_coverage_plan(context, {"expected_version": 1, "items": [], "remove_coverage_ids": [identity]})
    assert action_state_hash(context.action_state) == before


def test_model_review_output_rejects_obsolete_object_reference_names(context, real):
    payload = generation._review_input(context.action_state)
    candidate = actual_global_review_candidate(real, context.action_state)
    finding = actual_global_review_findings(real, context.action_state)[0]
    candidate["business_questions"] = [dict(finding,
        kind="source_conflict", source_refs=[{"evidence_id": finding["source_refs"][0]}])]
    with pytest.raises(OutputValidationError):
        generation._validate_review(context.action_state, payload, candidate)


def mixed_actual_findings(real, state):
    """组合真实历史两条意见，只测当前路由，不声称当前模型重审或缺陷仍成立。"""
    mapped_ids = {item["historical_case_id"]: f"TC-{index + 1:04d}"
                  for index, item in enumerate(real["current_cases_before"])}
    findings = []
    for record in real["historical_review_findings"]:
        difference = record["difference"]
        identity = mapped_ids[difference["case_id"]]
        content = state["cases"]["items"][identity]["content"]
        assert difference["case_id"] in {"TC-050", "TC-064"}
        # 历史TC050意见明确原始来源PDF/在线形式冲突，TC064意见是确定性重复错稿。
        kind = "source_conflict" if difference["case_id"] == "TC-050" else "case_error"
        findings.append({"kind": kind, "target_case_ids": [identity],
            "coverage_ids": list(content["coverage_ids"]), "reason": difference["detail"],
            "source_refs": model_input(content)["source_refs"]})
    assert {finding["kind"] for finding in findings} == {"source_conflict", "case_error"}
    return findings


def test_actual_business_conflict_can_register_and_request_confirmation_once(context, real):
    source = next(item for item in mixed_actual_findings(real, context.action_state) if item["kind"] == "source_conflict")
    before_cases = deepcopy(context.action_state["cases"])
    result = generation.request_confirmation(context, {"issues": [source]})
    assert len(result["requested_issue_ids"]) == 1
    identity = result["requested_issue_ids"][0]
    issue = context.action_state["open_issues"][identity]
    assert issue["status"] == "needs_human" and issue["reason"] == source["reason"]
    assert issue["source_refs"] == generation._source_refs(context.action_state, source["source_refs"])
    assert context.action_state["confirmation_requested"] == [identity]
    assert context.action_state["cases"] == before_cases


def test_fixable_real_case_error_cannot_be_added_to_atomic_human_request(context, real):
    findings = mixed_actual_findings(real, context.action_state)
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="先自动修复"):
        generation.request_confirmation(context, {"issues": findings})
    assert action_state_hash(context.action_state) == before


def test_mixed_review_routes_only_real_source_question_to_human(context, real):
    findings = mixed_actual_findings(real, context.action_state)
    payload = generation._review_input(context.action_state)
    # verdict是CPU控制组合；意见正文均来自历史失败证据，不保存为新模型回执。
    from review_cpu_projection import review_cpu_candidate
    generation._apply_review(context, payload, review_cpu_candidate(context.action_state, {
        "verdict": "needs_human", "checked_case_ids": list(context.action_state["cases"]["items"]),
        "issues": findings, "issue_resolutions": [],
    }))
    issues = list(context.action_state["open_issues"].values())
    source = next(issue for issue in issues if issue["kind"] == "source_conflict")
    correctable = next(issue for issue in issues if issue["kind"] == "case_error")
    assert source["status"] == "needs_human"
    expected_coverage = sorted({key for identity in source["target_case_ids"]
        for key in context.action_state["cases"]["items"][identity]["content"]["coverage_ids"]})
    assert expected_coverage and source["coverage_ids"] == expected_coverage
    assert correctable["status"] == "open"
    assert generation.observe_generation_state(context)["available_actions"]["request_confirmation"]["pending_human_count"] == 1
    assert not context.action_state["completed"] and "test_generation" not in context.artifacts
    assert generation.observe_generation_state(context)["automatic_action"] is None


def test_mixed_issue_resolutions_do_not_broadcast_human_status_to_open_case_error(context, real):
    findings = mixed_actual_findings(real, context.action_state)
    identities = [generation.report_issue(context, finding)["issue_id"] for finding in findings]
    payload = generation._review_input(context.action_state)
    resolutions = [{"issue_id": identity, "resolution": "needs_human" if finding["kind"] == "source_conflict" else "open",
                    "reason": finding["reason"], "source_refs": deepcopy(finding["source_refs"])}
                   for identity, finding in zip(identities, findings)]
    from review_cpu_projection import review_cpu_candidate
    generation._apply_review(context, payload, review_cpu_candidate(context.action_state, {
        "verdict": "needs_human", "checked_case_ids": list(context.action_state["cases"]["items"]),
        "issues": [], "issue_resolutions": resolutions,
    }))
    for identity, finding in zip(identities, findings):
        expected = "needs_human" if finding["kind"] == "source_conflict" else "open"
        assert context.action_state["open_issues"][identity]["status"] == expected
    assert not context.action_state["completed"] and "test_generation" not in context.artifacts


def test_viewed_real_text_only_page_reaches_independent_review_as_full_page(context):
    state = context.action_state
    text = next(item for item in state["evidence_catalog"]["items"]
                if item["kind"] == "text" and item.get("page_number") == 6)
    assert not any(item["kind"] == "image" and item.get("page_number") == 6
                   for item in state["evidence_catalog"]["items"])
    assert any(ref["evidence_id"] == text["evidence_id"]
               for case in state["cases"]["items"].values() for ref in case["content"]["source_refs"])
    assert "6" not in state["image_pages"]
    # 实际读取同版PDF第6页PNG并校验指纹，不补造模型图片回执或视觉业务结论。
    viewed = generation.view_source_image(context, {"page_number": 6})
    page = next(item for item in generation._review_input(context.action_state)["image_pages"]
                if item["page_number"] == 6)
    assert page == viewed["page_image"]
    assert page["blocks"] == []
    assert page["page_image_sha256"] == text["page_image_sha256"]
    assert page["asset_source_sha256"] == context.action_state["source_ref"]["content_hash"]


def test_unviewed_real_text_only_page_is_not_automatically_attached_to_review(context):
    state = context.action_state
    assert any(item["kind"] == "text" and item.get("page_number") == 6
               for item in state["evidence_catalog"]["items"])
    assert "6" not in state["image_pages"]
    before = action_state_hash(state)
    assert 6 not in {page["page_number"] for page in generation._review_input(state)["image_pages"]}
    assert action_state_hash(state) == before
