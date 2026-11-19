"""真实Run71遗漏分支与跨来源例外的scope合同；隔离CPU不制造审查成功。"""

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


@pytest.fixture(scope="module")
def real_scope():
    path = Path(__file__).with_name("fixtures") / "run71_generation_scope_real.json.gz"
    original = path.read_bytes()
    value = json.loads(gzip.decompress(original))
    assert action_state_hash({k: v for k, v in value.items() if k != "data_sha256"}) == value["data_sha256"]
    assert value["provenance"]["source_run_id"] == 71
    assert value["provenance"]["source_status"] == "success"
    assert value["provenance"]["new_model_review_performed"] is False
    yield value
    assert path.read_bytes() == original


@pytest.fixture
def context(real_scope):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    AgentRun.__table__.create(engine)
    values = deepcopy(real_scope["run_metadata"])
    for key in ("created_at", "started_at", "finished_at"):
        values[key] = datetime.fromisoformat(values[key]) if values[key] else None
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        db.add(AgentRun(**values, input_payload=deepcopy(real_scope["run_input"]),
                        run_context=deepcopy(real_scope["run_context"]), output_payload={}))
        db.commit()
        yield ToolExecutionContext(db=db, user_id=values["user_id"], project_id=values["project_id"],
            run_id=values["id"], node_key="agent_network", run_input=deepcopy(real_scope["run_input"]),
            action_input=deepcopy(real_scope["run_input"]), action_state=deepcopy(real_scope["initial_state"]),
            artifacts={"requirement_source": deepcopy(real_scope["initial_state"]["source_ref"])})
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    engine.dispose()


def source_refs(content):
    return list(dict.fromkeys(x["evidence_id"] for x in content["source_refs"]))


def actual_branch_goals(real_scope):
    recent = real_scope["actual_corrected_recent_case"]["content"]
    absent = real_scope["selected_cases"]["TC-0061"]["content"]
    # 两项都取自实际产出的业务正文，只赋当前平台编号；不声称已获得新的语义批准。
    return [{"coverage_id": f"COV-{index:04d}", "objective": content["title"],
             "conditions": list(content["preconditions"]), "exceptions": [],
             "source_refs": source_refs(content)} for index, content in enumerate((recent, absent), 1)]


def save(context, items, *, version=0):
    return generation.commit_coverage_plan(context, {"expected_version": version,
        "items": items, "remove_coverage_ids": []})


def test_actual_initialization_branches_remain_separate_and_visible(real_scope, context):
    goals = actual_branch_goals(real_scope)
    save(context, goals)
    observed = generation.observe_generation_state(context)["coverage_plan"]["items"]
    assert len(observed) == 2
    for actual, submitted in zip(observed, goals):
        assert actual["objective"] == submitted["objective"]
        assert actual["conditions"] == submitted["conditions"]
        assert actual["exceptions"] == []
        assert "EV-0004" in actual["evidence_ids"]
    assert observed[0]["conditions"] != observed[1]["conditions"]
    assert context.action_state["completed"] is False
    assert context.action_state["review"] is None


def test_actual_cross_source_exception_is_retained_with_general_rule(real_scope, context):
    content = real_scope["selected_cases"]["TC-0016"]["content"]
    exception = real_scope["selected_cases"]["TC-0029"]["content"]["steps"][1]["expected"]
    goal = {"coverage_id": "COV-0001", "objective": content["steps"][1]["expected"],
            "conditions": list(content["preconditions"]), "exceptions": [exception],
            "source_refs": ["EV-0016", "EV-0023"]}
    save(context, [goal])
    stored = context.action_state["coverage_plan"]["items"][0]
    assert stored["conditions"] == goal["conditions"]
    assert stored["exceptions"] == [exception]
    assert {x["evidence_id"] for x in stored["source_refs"]} == {"EV-0016", "EV-0023"}
    assert generation.observe_generation_state(context)["coverage_plan"]["items"][0]["exceptions"] == [exception]
    assert context.action_state["review"] is None and "test_generation" not in context.artifacts


@pytest.mark.parametrize("invalid", ["missing_conditions", "missing_exceptions", "string_conditions",
    "empty_condition", "duplicate_conditions", "duplicate_exceptions"])
def test_scope_contract_rejects_invalid_fields_atomically(real_scope, context, invalid):
    goals = actual_branch_goals(real_scope)
    if invalid.startswith("missing_"):
        goals[0].pop(invalid.removeprefix("missing_"))
    elif invalid == "string_conditions":
        goals[0]["conditions"] = goals[0]["conditions"][0]
    elif invalid == "empty_condition":
        goals[0]["conditions"] = [""]
    elif invalid == "duplicate_conditions":
        goals[0]["conditions"] *= 2
    else:
        text = real_scope["selected_cases"]["TC-0029"]["content"]["steps"][1]["expected"]
        goals[0]["exceptions"] = [text, text]
    before = action_state_hash(context.action_state)
    with pytest.raises(OutputValidationError):
        save(context, goals)
    assert action_state_hash(context.action_state) == before


def test_scope_upsert_preserves_unsubmitted_real_branch(real_scope, context):
    goals = actual_branch_goals(real_scope)
    save(context, goals)
    original = deepcopy(context.action_state["coverage_plan"]["items"])
    changed = deepcopy(goals[0])
    changed["exceptions"] = list(goals[1]["conditions"])
    save(context, [changed], version=1)
    current = context.action_state["coverage_plan"]["items"]
    assert current[1] == original[1]
    assert current[0]["exceptions"] == changed["exceptions"]
    assert context.action_state["coverage_plan"]["version"] == 2
