"""Run81 首次15项修稿的 CPU 回放，不改旧Run或伪造新审查结案。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext


@pytest.fixture(scope="module")
def actual():
    path = Path(__file__).parent / "fixtures/run81_mixed_noop_patch_real.json.gz"
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    data = fixture["data"]
    assert fixture["provenance"]["source_run_id"] == 81
    assert fixture["provenance"]["source_status"] == "failed"
    assert fixture["provenance"]["new_model_responses"] is False
    assert action_state_hash(data) == fixture["provenance"]["data_sha256"]
    assert action_state_hash(data["state"]) == data["first_failed_patch"]["pre_state_hash"]
    assert len(data["arguments"]["patches"]) == 15
    assert len(data["canonical_changed_ids"]) == 14
    assert data["canonical_unchanged_ids"] == ["TC-0029"]
    yield data
    assert path.read_bytes() == original


@pytest.fixture
def context(actual):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    values = deepcopy(actual["run"])
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        db.add(AgentRun(**values))
        db.commit()
        state = deepcopy(actual["state"])
        generation.refresh_generation_state(state)
        result = ToolExecutionContext(db=db, user_id=values["user_id"], project_id=values["project_id"],
            run_id=values["id"], node_key="agent_network", run_input=deepcopy(values["input_payload"]),
            action_input=deepcopy(values["input_payload"]), action_state=state,
            artifacts=deepcopy(values["run_context"]["artifacts"]))
        yield result
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def test_actual_mixed_batch_commits_fourteen_changes_and_preserves_complete_noop_record(context, actual):
    before = deepcopy(context.action_state)
    submitted = deepcopy(actual["arguments"])
    result = generation.patch_cases(context, submitted)
    after = context.action_state
    assert result["changed_case_ids"] == actual["canonical_changed_ids"]
    assert result["unchanged_case_ids"] == actual["canonical_unchanged_ids"]
    assert after["cases"]["version"] == before["cases"]["version"] + 1
    changes = {patch["case_id"]: generation._case_content(before, patch["content"])
               for patch in actual["arguments"]["patches"]}
    for identity, record in before["cases"]["items"].items():
        current = after["cases"]["items"][identity]
        if identity in actual["canonical_changed_ids"]:
            assert current["version"] == record["version"] + 1
            assert current["content"] == changes[identity]
            assert current["content_hash"] == action_state_hash(changes[identity])
            assert current["automatic_repairs"] == record["automatic_repairs"] + int(record["review_failures"] > 0)
        else:
            assert current == record
    assert submitted == actual["arguments"]
    # 只有同值项对应的真实单目标问题仍保持原状，不能把跳过当作完成修复。
    noop_issues = [issue for issue in before["open_issues"].values()
                  if set(issue["target_case_ids"]) == set(actual["canonical_unchanged_ids"])]
    assert noop_issues
    for issue in noop_issues:
        assert after["open_issues"][issue["issue_id"]] == issue
    assert not generation._automatic_review_ready(after)
    assert not generation._can_finish(after)


def test_batch_of_only_real_unchanged_contents_still_rejects_without_versions_or_progress(context, actual):
    patches = []
    for patch in actual["arguments"]["patches"]:
        record = context.action_state["cases"]["items"][patch["case_id"]]
        content = deepcopy(record["content"])
        content["source_refs"] = generation._model_source_refs(content["source_refs"])
        patches.append({"case_id": record["case_id"], "expected_version": record["version"], "content": content})
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="没有实际内容变化") as rejected:
        generation.patch_cases(context, {"patches": patches})
    assert rejected.value.details["unchanged_case_ids"] == [patch["case_id"] for patch in patches]
    assert action_state_hash(context.action_state) == before


@pytest.mark.parametrize("corruption", ["unknown_id", "stale_version", "unviewed_reference"])
def test_invalid_last_patch_rolls_back_the_entire_real_batch(context, actual, corruption):
    submitted = deepcopy(actual["arguments"])
    last = submitted["patches"][-1]
    if corruption == "unknown_id":
        last["case_id"] = f"TC-{context.action_state['cases']['next_id']:04d}"
    elif corruption == "stale_version":
        last["expected_version"] += 1
    else:
        source = next(item for item in context.action_state["evidence_catalog"]["items"]
            if item["kind"] == "image" and str(item["page_number"]) not in context.action_state["image_pages"])
        last["content"]["source_refs"] = [source["evidence_id"]]
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError):
        generation.patch_cases(context, submitted)
    assert action_state_hash(context.action_state) == before


def test_repeated_noop_identity_cannot_bypass_duplicate_id_validation(context, actual):
    unchanged = next(patch for patch in actual["arguments"]["patches"]
                     if patch["case_id"] in actual["canonical_unchanged_ids"])
    changed = next(patch for patch in actual["arguments"]["patches"]
                   if patch["case_id"] in actual["canonical_changed_ids"])
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="重复用例"):
        generation.patch_cases(context, {"patches": [deepcopy(changed), deepcopy(unchanged), deepcopy(unchanged)]})
    assert action_state_hash(context.action_state) == before
