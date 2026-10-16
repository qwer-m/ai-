"""用真实 prepare_generation 输入重建范围说明，核对原契约与成功候选保持不变。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentWorkflowDefinition
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import AgentOutputContract
from modules.agent_platform.test_generation_batching import prepare_test_case_batches


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def without_scope(item: dict) -> dict:
    value = deepcopy(item)
    value["plan"].pop("coverage_focus")
    for key in ("coverage_focus", "semantic_summary", "semantic_keywords"):
        value["batch"].pop(key)
    return value


def verify(run_id: int) -> dict:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AgentRunRepository(db)
        run = repo.get_run(run_id=run_id)
        prepared = repo.latest_node_run(run_id=run_id, node_key="prepare_generation")
        generation = repo.latest_node_run(run_id=run_id, node_key="generation")
        source = repo.read_node_input(prepared)
        before = digest([source, prepared.output_payload, generation.output_payload, generation.sdk_state])
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                       run_id=run.id, node_key="prepare_generation", run_input=deepcopy(run.input_payload))
        rebuilt = prepare_test_case_batches(context, deepcopy(source))
        historical = prepared.output_payload
        assert rebuilt.keys() == historical.keys()
        assert rebuilt["case_budget"] == historical["case_budget"]
        assert rebuilt["batch_count"] == historical["batch_count"]
        assert repo.read_node_input(generation)["items"] == historical["items"]
        changed = []
        for index, (original, current) in enumerate(zip(historical["items"], rebuilt["items"], strict=True)):
            assert without_scope(original) == without_scope(current)
            points = list(dict.fromkeys(item["test_point"] for item in current["plan"]["test_design_items"]))
            assert current["batch"]["semantic_keywords"] == list(dict.fromkeys([current["batch"]["module_name"], *points]))
            assert current["plan"]["coverage_focus"] == current["batch"]["coverage_focus"] == current["batch"]["semantic_summary"]
            if points:
                assert current["batch"]["coverage_focus"].endswith("当前覆盖点：" + "；".join(points))
            changed.append({"item_index": index, "batch_id": current["batch"]["batch_id"],
                            "previous_scope": original["batch"]["coverage_focus"],
                            "current_scope": current["batch"]["coverage_focus"], "test_points": points,
                            "facts_designs_routes_budget_contract_unchanged": True,
                            "scope_changed": current["batch"]["coverage_focus"] != original["batch"]["coverage_focus"]})
        workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
        graph = parse_execution_definition(workflow.definition)
        model_node = next(node for node in graph.nodes if node.node_key == "generation")
        definition = db.get(AgentDefinition, generation.agent_definition_id)
        outputs = {item["item_index"]: item["output"] for item in generation.output_payload["items"]}
        verified = []
        for state in generation.sdk_state["items"]:
            if state["status"] != "success":
                continue
            index = state["item_index"]
            candidate = next(call["arguments"] for call in reversed(state["tool_calls"])
                             if call["tool_key"] == "submit_generation_batch")
            candidate_before = deepcopy(candidate)
            original_validated = AgentOutputContract(deepcopy(historical["items"][index]), model_node.map_config).validate(
                definition, context, deepcopy(candidate))
            current_validated = AgentOutputContract(deepcopy(rebuilt["items"][index]), model_node.map_config).validate(
                definition, context, deepcopy(candidate))
            assert original_validated == current_validated == outputs[index]
            assert candidate == candidate_before
            verified.append({"item_index": index, "case_count": len(current_validated["test_cases"]),
                             "normalized_output_sha256": digest(current_validated)})
        assert verified
        db.refresh(prepared)
        db.refresh(generation)
        assert digest([repo.read_node_input(prepared), prepared.output_payload,
                       generation.output_payload, generation.sdk_state]) == before
        assert not db.new and not db.dirty and not db.deleted
        report = {"status": "passed", "source_run_id": run_id, "prepare_node_run_id": prepared.id,
                  "generation_node_run_id": generation.id, "batch_count": rebuilt["batch_count"],
                  "case_budget": rebuilt["case_budget"], "all_non_scope_fields_identical": True,
                  "source_and_candidates_unchanged": True, "scope_changes": changed,
                  "historical_candidates_full_contract_passed": verified,
                  "historical_success_count": len(verified), "model_requests": 0, "online_database_writes": 0}
        db.rollback()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args()
    report = verify(options.run_id)
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"scope_changes", "historical_candidates_full_contract_passed"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
