"""真实 Run77 初态与 Run74 正文的 CPU 协议回放，不制造模型审查或交付成功。"""

from copy import deepcopy
import gzip
import json
from pathlib import Path

from jsonschema import ValidationError, validate
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_TOOL_SPECS
from modules.agent_platform.test_generation_schemas import CASE_CONTENT_SCHEMA


@pytest.fixture(scope="module")
def saved():
    path = Path(__file__).parent / "fixtures/run77_optional_coverage_real.json.gz"
    original = path.read_bytes()
    value = json.loads(gzip.decompress(original))
    data = value["data"]
    assert value["provenance"]["source_run_id"] == 77
    assert value["provenance"]["new_model_responses"] is False
    assert action_state_hash(data) == value["provenance"]["data_sha256"]
    assert action_state_hash(data["initial_state"]) == value["provenance"]["source_state_hash"]
    assert action_state_hash(data["initial_state"]) == data["first_failed_receipt"]["pre_state_hash"]
    yield data
    assert path.read_bytes() == original


@pytest.fixture
def context(saved):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    values = deepcopy(saved["run"])
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        db.add(AgentRun(**values))
        db.commit()
        state = deepcopy(saved["initial_state"])
        generation.refresh_generation_state(state)
        result = ToolExecutionContext(db=db, user_id=values["user_id"], project_id=values["project_id"],
            run_id=values["id"], node_key="agent_network", run_input=deepcopy(values["input_payload"]),
            action_input=deepcopy(values["input_payload"]), action_state=state,
            artifacts={"requirement_source": deepcopy(state["source_ref"])})
        yield result
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def actual_case(saved, index=0):
    result = deepcopy(saved["historical_cases"][index])
    result.pop("case_id")
    result.pop("coverage_ids")
    result["source_refs"] = generation._model_source_refs(result["source_refs"])
    return result


def append_schema():
    return next(item["input_schema"] for item in BUILTIN_TOOL_SPECS if item["tool_key"] == "append_cases")


def test_same_actual_missing_delete_list_now_uses_explicit_no_delete_semantics(context, saved):
    arguments = deepcopy(saved["first_missing_remove_arguments"])
    assert "remove_coverage_ids" not in arguments and len(arguments["cases"]) == 12
    validate(arguments, append_schema())
    before = action_state_hash(saved)
    result = generation.append_cases(context, arguments)
    assert result["case_count"] == 12
    assert context.action_state["coverage_plan"]["items"]
    assert action_state_hash(saved) == before


def test_only_cases_are_required_and_seventy_real_contents_need_no_duplicate_plan(context, saved):
    assert append_schema()["required"] == ["cases"]
    assert "coverage_ids" not in CASE_CONTENT_SCHEMA["required"]
    originals = deepcopy(saved["historical_cases"])
    maximum = context.action_state["goal"]["batch_case_limit"]
    for offset in range(0, 70, maximum):
        arguments = {"cases": [actual_case(saved, index) for index in range(offset, min(70, offset + maximum))]}
        validate(arguments, append_schema())
        generation.append_cases(context, arguments)
    state = context.action_state
    assert len(state["cases"]["items"]) == 70
    assert state["coverage_plan"] == {"version": 0, "items": []}
    assert all(item["content"]["coverage_ids"] == [] for item in state["cases"]["items"].values())
    assert generation.observe_generation_state(context)["automatic_action"] == "request_review"
    # 空辅助计划不代表语义通过；没有真实当前独审便不能交付。
    assert state["review"] is None and not generation._can_finish(state)
    with pytest.raises(OutputValidationError):
        generation._build_delivery(context, state)
    assert saved["historical_cases"] == originals
    # 若主动建立辅助计划，未关联任何用例的目标仍会阻止审查，不能借可选化绕过它。
    goal = deepcopy(saved["first_missing_remove_arguments"]["coverage_updates"][0])
    generation.commit_coverage_plan(context, {"expected_version": 0, "items": [goal]})
    assert "request_review" not in generation.observe_generation_state(context)["available_actions"]
    first_id = next(iter(context.action_state["cases"]["items"]))
    generation.patch_cases(context, {"patches": [{"case_id": first_id, "expected_version": 1,
        "content": {**actual_case(saved), "coverage_ids": [goal["coverage_id"]]}}]})
    assert generation.observe_generation_state(context)["automatic_action"] == "request_review"
    assert not generation._can_finish(context.action_state)


def test_explicit_empty_controls_need_no_plan_version(context, saved):
    result = generation.append_cases(context, {"cases": [actual_case(saved)],
        "coverage_updates": [], "remove_coverage_ids": []})
    assert result["case_count"] == 1 and result["coverage_version"] == 0


@pytest.mark.parametrize("control", ["coverage_updates", "remove_coverage_ids"])
def test_actual_plan_mutations_still_require_current_version(context, saved, control):
    first = saved["first_missing_remove_arguments"]
    value = first["coverage_updates"][:1] if control == "coverage_updates" else [first["coverage_updates"][0]["coverage_id"]]
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="expected_coverage_version"):
        generation.append_cases(context, {"cases": [actual_case(saved)], control: value})
    assert action_state_hash(context.action_state) == before


@pytest.mark.parametrize("control", ["coverage_updates", "remove_coverage_ids"])
def test_null_control_is_not_treated_as_omission(context, saved, control):
    arguments = {"cases": [actual_case(saved)], control: None}
    with pytest.raises(ValidationError):
        validate(arguments, append_schema())
    with pytest.raises(OutputValidationError, match="必须是数组"):
        generation.append_cases(context, arguments)


def test_optional_case_index_still_rejects_null_unknown_and_source_ids(context, saved):
    empty = actual_case(saved)
    actual_cov_id = saved["first_missing_remove_arguments"]["coverage_updates"][0]["coverage_id"]
    for value in (None, [empty["source_refs"][0]], [actual_cov_id]):
        submitted = {**deepcopy(empty), "coverage_ids": value}
        with pytest.raises(OutputValidationError):
            generation._case_content(context.action_state, submitted)
    with pytest.raises(ValidationError):
        validate({**empty, "coverage_ids": [empty["source_refs"][0]]}, CASE_CONTENT_SCHEMA)


def test_internal_stored_case_shape_stays_complete(context, saved):
    wire = actual_case(saved)
    original = deepcopy(wire)
    stored = generation._case_content(context.action_state, wire)
    assert stored["coverage_ids"] == [] and wire == original
    assert generation._validate_stored_case_content(context.action_state, stored) == stored
    del stored["coverage_ids"]
    with pytest.raises(OutputValidationError, match="完整契约"):
        generation._validate_stored_case_content(context.action_state, stored)


def test_explicit_plan_capability_keeps_version_checks_and_can_remove_last_unused_goal(context, saved):
    goal = deepcopy(saved["first_missing_remove_arguments"]["coverage_updates"][0])
    generation.commit_coverage_plan(context, {"expected_version": 0, "items": [goal]})
    assert context.action_state["coverage_plan"]["version"] == 1
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError, match="当前版本"):
        generation.commit_coverage_plan(context, {"expected_version": 0, "items": [goal]})
    assert action_state_hash(context.action_state) == before
    generation.commit_coverage_plan(context, {"expected_version": 1, "items": [],
        "remove_coverage_ids": [goal["coverage_id"]]})
    assert context.action_state["coverage_plan"] == {"version": 2, "items": []}
