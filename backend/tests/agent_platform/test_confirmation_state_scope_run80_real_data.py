"""Run80真实混合人工请求的CPU协议投影；不确认其业务冲突成立，不请求模型。"""

from copy import deepcopy
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path

from jsonschema import ValidationError, validate
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.conflict_confirmation import pending_conflicts
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS


def load_real(name):
    path = Path(__file__).parent / "fixtures" / name
    original = path.read_bytes()
    fixture = json.loads(gzip.decompress(original))
    encoded = json.dumps(fixture["data"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == fixture["provenance"]["data_sha256"]
    return fixture, original


@pytest.fixture(scope="module")
def real():
    name = "run80_confirmation_metadata_rejected_real.json.gz"
    fixture, original = load_real(name)
    assert fixture["provenance"]["source_run_id"] == 80 and fixture["provenance"]["source_status"] == "failed"
    assert action_state_hash(fixture["data"]["pre_confirmation_state"]) == fixture["provenance"]["source_pre_state_hash"]
    yield fixture["data"]
    assert (Path(__file__).parent / "fixtures" / name).read_bytes() == original


@pytest.fixture
def contexts():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        def make(run, state):
            values = {key: deepcopy(value) for key, value in run.items()
                if key in {column.name for column in AgentRun.__table__.columns}}
            for key, value in list(values.items()):
                if value is not None and key.endswith("_at"):
                    values[key] = datetime.fromisoformat(value)
            if db.get(AgentRun, values["id"]) is None:
                db.add(AgentRun(**values))
                db.commit()
            state = deepcopy(state)
            # 只重算当前代码派生的薄投影，不改原fixture及业务材料。
            generation.refresh_generation_state(state)
            return ToolExecutionContext(db=db, user_id=values["user_id"], project_id=values["project_id"],
                run_id=values["id"], node_key="agent_network", run_input=deepcopy(values["input_payload"]),
                action_input=deepcopy(values["input_payload"]), action_state=state,
                artifacts=deepcopy(values["run_context"]["artifacts"]))
        yield make
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def actual_request(real):
    return json.loads(real["failed_request_receipt"]["arguments"]["arguments_json"])


def new_wire(real):
    result = actual_request(real)
    result.pop("issue_ids")
    return result


def test_original_six_gap_ids_are_rejected_as_old_wire_without_mutating_state(real, contexts):
    context = contexts(real["run"], real["pre_confirmation_state"])
    arguments = actual_request(real)
    before = deepcopy(context.action_state)
    assert len(arguments["issue_ids"]) == 6
    assert all(context.action_state["open_issues"][key]["kind"] == "coverage_gap" for key in arguments["issue_ids"])
    schema = next(spec["input_schema"] for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] == "request_confirmation")
    assert set(schema["properties"]) == {"issues"}
    with pytest.raises(ValidationError):
        validate(arguments, schema)
    with pytest.raises(OutputValidationError, match="仅接收新问题issues"):
        generation.request_confirmation(context, arguments)
    assert context.action_state == before


def test_actual_new_question_is_not_blocked_by_unrelated_old_gap_ids_and_budget_is_preserved(real, contexts):
    context = contexts(real["run"], real["pre_confirmation_state"])
    original = deepcopy(real)
    gap_ids = actual_request(real)["issue_ids"]
    old_gaps = {key: deepcopy(context.action_state["open_issues"][key]) for key in gap_ids}
    run_before = deepcopy(context.db.get(AgentRun, context.run_id).run_context)
    goal_before = deepcopy(context.action_state["goal"])
    result = generation.request_confirmation(context, new_wire(real))
    assert len(result["requested_issue_ids"]) == 1
    identity = result["requested_issue_ids"][0]
    assert identity not in gap_ids
    issue = context.action_state["open_issues"][identity]
    assert issue["kind"] == "source_conflict" and issue["status"] == "needs_human"
    assert issue["reason"] == new_wire(real)["issues"][0]["reason"]
    assert {ref["evidence_id"] for ref in issue["source_refs"]} == {"EV-0024", "EV-0033", "EV-0035"}
    assert context.action_state["confirmation_requested"] == [identity]
    assert [item["id"] for item in pending_conflicts(context.action_state)] == [identity]
    assert {key: context.action_state["open_issues"][key] for key in gap_ids} == old_gaps
    assert context.action_state["goal"] == goal_before
    assert context.db.get(AgentRun, context.run_id).run_context == run_before
    assert run_before["usage"]["total_tokens"] == 264788
    assert real == original
    # 这里只验证请求进入待用户决定状态，不证明该source_conflict的业务判断正确。
    assert not context.action_state["completed"] and context.action_state["delivery"] is None


def test_all_existing_real_needs_human_items_are_collected_without_model_ids(contexts):
    fixture, original = load_real("run77_sdk_state_admission_real.json.gz")
    data = fixture["data"]
    context = contexts(data["run"], data["pending_human_state"])
    expected = sorted(data["pending_human_state"]["confirmation_requested"])
    assert len(expected) == 2
    before_issues = deepcopy(context.action_state["open_issues"])
    result = generation.request_confirmation(context, {"issues": []})
    assert result["requested_issue_ids"] == expected
    assert context.action_state["confirmation_requested"] == expected
    assert context.action_state["open_issues"] == before_issues
    metadata = generation.observe_generation_state(context)["available_actions"]["request_confirmation"]
    assert metadata == {"pending_human_count": 2, "new_issues_allowed": True}
    assert (Path(__file__).parent / "fixtures/run77_sdk_state_admission_real.json.gz").read_bytes() == original


def test_invalid_new_target_rolls_back_whole_batch_even_after_first_real_question_is_registered(real, contexts):
    context = contexts(real["run"], real["pre_confirmation_state"])
    arguments = new_wire(real)
    invalid = deepcopy(arguments["issues"][0])
    invalid["target_case_ids"] = ["EV-0024"]
    arguments["issues"].append(invalid)
    before = deepcopy(context.action_state)
    with pytest.raises(OutputValidationError, match="当前真实用例与覆盖目标"):
        generation.request_confirmation(context, arguments)
    assert context.action_state == before


def test_new_question_still_requires_actual_source_read_and_empty_request_does_not_confirm_gaps(real, contexts):
    context = contexts(real["run"], real["pre_confirmation_state"])
    with pytest.raises(OutputValidationError, match="至少提交一项真实未决问题"):
        generation.request_confirmation(context, {"issues": []})
    context.action_state["evidence_reads"].pop("EV-0033")
    generation.refresh_generation_state(context.action_state)
    before = deepcopy(context.action_state)
    with pytest.raises(OutputValidationError, match="必须先读取"):
        generation.request_confirmation(context, new_wire(real))
    assert context.action_state == before
