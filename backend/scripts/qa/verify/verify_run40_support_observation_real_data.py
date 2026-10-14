"""只读 Run40 真实检查点，核对支持观察、非法索引反馈及同证据复审去重。"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform import test_generation_planning_agent as planning
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_planning import (
    current_planning_support_decisions, planning_support_request_hash, prepare_planning_route_repairs,
)


def verify(snapshot_path: Path):
    source_bytes = snapshot_path.read_bytes()
    saved = json.loads(source_bytes)
    state = saved["checkpoint"]["state"]
    assert saved["run_id"] == 40 and saved["node_id"] == 994
    assert action_state_hash(state) == saved["checkpoint"]["state_hash"]
    domain = state["planning"]
    base = {"plan": domain["plan"], "prepared_items": domain["route_inputs"],
            "route_records": domain["route_records"]}
    before_code = {}
    for name in ("test_generation_agent.py", "test_generation_planning_agent.py",
                 "test_generation_planning.py", "test_generation_builtin_specs.py"):
        path = Path(__file__).resolve().parents[3] / "modules" / "agent_platform" / name
        before_code[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"source_snapshot": str(snapshot_path.resolve()),
              "source_snapshot_sha256": hashlib.sha256(source_bytes).hexdigest(),
              "source_state_sha256": action_state_hash(state), "production_sha256": before_code,
              "model_requests_sent": 0, "online_database_writes": 0,
              "scope": "真实 Run40 原请求及原候选的纯观察/prepare/hash 回放，不恢复旧执行、不提交运行状态"}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, saved["run_id"])
            assert run is not None
            run_before = action_state_hash([run.run_context, run.input_payload, run.output_payload, run.status])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key="generation_agent", run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(run.run_context["artifacts"]), action_input=deepcopy(saved["node_input"]),
                action_state=deepcopy(state))
            observed = generation.observe_generation_state(context)
            assert context.action_state == state
            assert observed["planning"]["route_gaps"] == saved["observation"]["planning"]["route_gaps"]
            assert observed["planning"]["unresolved_design_selections"] == saved["observation"]["planning"]["unresolved_design_selections"]
            action_scope = observed["available_actions"]["review_planning_route_gaps"]
            assert "design_selections" not in action_scope
            assert action_scope["available_designs"] == observed["planning"]["reviewable_designs"]
            counts = Counter()
            for old, current in zip(saved["observation"]["planning"]["reviewable_designs"],
                                    action_scope["available_designs"], strict=True):
                assert old["module_index"] == current["module_index"]
                groups = current["design_indexes_by_disposition"]
                assert set(groups) == {"supported", "unsupported"}
                assert sorted(groups["supported"] + groups["unsupported"]) == old["test_design_item_indexes"]
                assert not set(groups["supported"]) & set(groups["unsupported"])
                for disposition, indexes in groups.items():
                    counts[disposition] += len(indexes)
            assert counts == {"supported": 196, "unsupported": 1}
            report["observation"] = {"decision_counts": dict(counts),
                "unresolved_design_selections": action_scope["unresolved_design_selections"],
                "available_designs": action_scope["available_designs"],
                "route_feedback_unchanged": True}

            child = generation._planning_context(context, state)
            failures = []
            failed_receipts = [receipt for receipt in saved["support_receipts"] if receipt["status"] == "failed"]
            assert len(failed_receipts) == 3
            for receipt in failed_receipts:
                arguments = json.loads(receipt["arguments"]["arguments_json"])
                try:
                    planning.prepare_route_gap_review(child, deepcopy(arguments))
                except OutputValidationError as error:
                    details = error.details
                    assert details["module_index"] == 9
                    assert details["invalid_test_design_item_indexes"] == list(range(6, 12))
                    assert details["available_test_design_item_indexes"] == list(range(6))
                    assert details["requested_test_design_item_indexes"] == list(range(12))
                    failures.append({"tool_call_id": receipt["call_id"], "details": details})
                else:
                    raise AssertionError("真实越界请求未被拒绝")
            report["actual_bad_request_replays"] = failures

            revisions = domain["route_review_revisions"]
            evidence_groups = {}
            for index, record in enumerate(revisions):
                evidence_groups.setdefault(planning_support_request_hash(record["input"]), []).append(index)
            repeated = [indexes for indexes in evidence_groups.values() if len(indexes) > 1]
            assert repeated == [[0, 3, 4]]
            actual_items = [revisions[index]["input"] for index in repeated[0]]
            assert len({action_state_hash(item) for item in actual_items}) == 3
            assert len({planning_support_request_hash(item) for item in actual_items}) == 1
            assert len({item["_planning_feedback"]["question"] for item in actual_items}) == 3
            actual_items_before = deepcopy(actual_items)
            report["same_evidence_revisions"] = [
                {"revision_index": index, "module_index": item["module_index"],
                 "test_design_item_indexes": [value["test_design_item_index"] for value in item["missing_test_design_items"]],
                 "question": item["_planning_feedback"]["question"],
                 "fact_count": len(item["candidate_facts"]), "full_input_sha256": action_state_hash(item),
                 "support_request_sha256": planning_support_request_hash(item)}
                for index, item in zip(repeated[0], actual_items, strict=True)]

            # 旧回执本身真实，但重复采样历史不满足新契约；只验真实前缀，不猜测或重写旧检查点版本。
            prefix = revisions[:3]
            current_planning_support_decisions(child, {**base,
                "repair_records": domain["gap_records"], "review_revisions": prefix})
            try:
                current_planning_support_decisions(child, {**base,
                    "repair_records": domain["gap_records"], "review_revisions": revisions})
            except ValueError as error:
                assert str(error) == "不能对同一个完整支持复核请求重复抽取结论"
                report["old_duplicate_history_rejected"] = str(error)
            else:
                raise AssertionError("新版契约不得静默接受旧重复复审历史")
            report["actual_revision_prefix_revalidated"] = len(prefix)

            original = actual_items[0]
            selection = [{"module_index": original["module_index"],
                "test_design_item_indexes": [item["test_design_item_index"] for item in original["missing_test_design_items"]]}]
            candidates = {fact["scope_id"] for fact in original["candidate_facts"]}
            same_scope = sorted(candidates)[0]
            same_evidence = prepare_planning_route_repairs(child, {**base, "design_selections": selection,
                "additional_scope_ids": [same_scope]})["items"][0]
            same_evidence["_planning_feedback"] = deepcopy(original["_planning_feedback"])
            assert same_evidence["candidate_facts"] == original["candidate_facts"]
            assert action_state_hash(same_evidence) != action_state_hash(original)
            assert planning_support_request_hash(same_evidence) == planning_support_request_hash(original)
            report["existing_scope_reselection"] = {
                "additional_scope_ids": [same_scope], "candidate_facts_unchanged": True,
                "full_input_sha256": action_state_hash(same_evidence),
                "support_request_sha256": planning_support_request_hash(same_evidence)}
            # 从真实已完成补证请求取新增范围，事实仍由原 prepare 从当前来源派生。
            added_scopes = sorted({scope for record in revisions for scope in record["input"]["additional_scope_ids"]}
                                  - candidates)
            assert added_scopes
            expanded = prepare_planning_route_repairs(child, {**base, "design_selections": selection,
                "additional_scope_ids": added_scopes})["items"][0]
            expanded["_planning_feedback"] = deepcopy(original["_planning_feedback"])
            assert {fact["fact_id"] for fact in expanded["candidate_facts"]} > {
                fact["fact_id"] for fact in original["candidate_facts"]}
            assert planning_support_request_hash(expanded) not in evidence_groups
            first_bad = json.loads(failed_receipts[0]["arguments"]["arguments_json"])
            changed_selection = next(value for value in first_bad["design_selections"]
                if value["module_index"] == original["module_index"])
            different_intent = prepare_planning_route_repairs(child, {**base,
                "design_selections": [changed_selection], "additional_scope_ids": []})["items"][0]
            different_intent["_planning_feedback"] = deepcopy(original["_planning_feedback"])
            assert planning_support_request_hash(different_intent) not in evidence_groups
            assert actual_items == actual_items_before
            report["distinct_request_boundaries"] = {
                "scope": "仅验证真实 prepare 输入及去重身份可区分；不声称进行了新模型裁决或正式行动提交",
                "added_scope_ids": added_scopes,
                "expanded_fact_count": len(expanded["candidate_facts"]),
                "expanded_request_sha256": planning_support_request_hash(expanded),
                "different_intent_selection": changed_selection,
                "different_intent_request_sha256": planning_support_request_hash(different_intent)}
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert action_state_hash([run.run_context, run.input_payload, run.output_payload, run.status]) == run_before
        finally:
            db.rollback()
    assert snapshot_path.read_bytes() == source_bytes
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == value for path, value in before_code.items())
    report["status"] = "passed"
    report["production_and_saved_run_unchanged"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    assert not options.output.exists(), "请使用新报告路径"
    result = verify(options.snapshot)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "decision_counts": result["observation"]["decision_counts"],
        "bad_requests": len(result["actual_bad_request_replays"]),
        "same_evidence_revisions": len(result["same_evidence_revisions"]),
        "model_requests_sent": result["model_requests_sent"], "online_database_writes": result["online_database_writes"]},
        ensure_ascii=False))
