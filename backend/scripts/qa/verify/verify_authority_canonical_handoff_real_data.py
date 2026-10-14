"""只读失败运行的真实权威裁决回执，验证模型字段到规范字段仅转换一次。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import ValidationError, validate
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun, AgentRunEvent
from modules.agent_platform import test_generation_agent as generation
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import AgentOutputContract, _revalidate_managed_agent_call
from modules.agent_platform.test_generation_schemas import (
    AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA, AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA,
)
from modules.agent_platform.test_generation_semantics import (
    merge_authority_reconciliation, postprocess_authority_reconciliation_item,
    validate_authority_reconciliation_output,
)


def fingerprint(run, node):
    return action_state_hash({
        "run": [run.status, run.input_payload, run.run_context, run.output_payload],
        "node": [node.status, node.input_payload, node.output_payload, node.sdk_state, node.error_message],
    })


def reject(callback):
    try:
        callback()
    except (ValueError, ValidationError) as error:
        return {"type": type(error).__name__, "message": str(error).splitlines()[0]}
    raise AssertionError("错误字段契约被接受")


def verify(run_id, node_id):
    source_files = [Path("modules/agent_platform") / name for name in (
        "test_generation_agent.py", "test_generation_semantics.py", "test_generation_planning_agent.py",
    )]
    source_hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        repo = AgentRunRepository(db)
        run, node = db.get(AgentRun, run_id), db.get(AgentNodeRun, node_id)
        assert run is not None and node is not None and node.run_id == run_id
        assert run.status == node.status == "failed"
        original = fingerprint(run, node)
        event_count = db.query(AgentRunEvent).filter(AgentRunEvent.run_id == run_id).count()
        checkpoint = deepcopy(node.sdk_state["action_checkpoint"])
        initial_state = deepcopy(checkpoint["state"])
        assert all(record is None for record in initial_state["authority_records"])
        prepared_items = initial_state["authority_inputs"]
        receipts = [record for record in checkpoint["validated_agent_calls"].values()
                    if record["identity"]["agent_key"] == "test_authority_reconciliation_reviewer"]
        assert len(prepared_items) == len(receipts) == 11
        child = AgentDefinitionRepository(db).get_agent(project_id=run.project_id,
            agent_key="test_authority_reconciliation_reviewer")
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key=node.node_key, run_input=deepcopy(run.input_payload),
            action_input=repo.read_node_input(node), action_state=deepcopy(initial_state),
            artifacts=deepcopy(run.run_context["artifacts"]))
        cold = generation._isolated(context)
        generation.initialize_generation_state(cold, deepcopy(context.action_input))
        cold_state_hash = action_state_hash(cold.action_state)
        cold_observation = generation.observe_generation_state(cold)
        assert cold_observation["planning"]["plan_available"] is False
        try:
            generation.accept_generation_plan(cold, {})
        except OutputValidationError as error:
            cold_rejection = {"message": str(error), "details": deepcopy(error.details)}
            assert error.details["plan_available"] is False
            assert error.details["pending_draft_indexes"] == cold_observation["planning"]["pending_draft_indexes"]
            assert "pending_route_indexes" not in error.details
        else:
            raise AssertionError("真实冷启动尚无规划却允许接受规划")
        assert action_state_hash(cold.action_state) == cold_state_hash
        outputs, evidence, relations = [], [], []
        relation_source = None
        for index, item in enumerate(prepared_items):
            receipt = next(record for record in receipts if record["identity"]["input_hash"] == action_state_hash(item))
            assert receipt["identity"]["execution_semantic_hash"] == checkpoint["execution_semantic_hash"]
            assert receipt["raw_output_hash"] == action_state_hash(receipt["raw_output"])
            assert receipt["output_hash"] == action_state_hash(receipt["output"])
            raw, normalized = deepcopy(receipt["raw_output"]), deepcopy(receipt["output"])
            validate(raw, AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA)
            validate(normalized, AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA)
            contract = AgentOutputContract(item, AgentMapConfig(item_postprocessor="testing.postprocess_authority_reconciliation_item"))
            assert _revalidate_managed_agent_call(saved=receipt, contract=contract, definition=child, context=context) == normalized
            assert postprocess_authority_reconciliation_item(context, {"item_input": item, "item_output": raw}) == normalized
            assert validate_authority_reconciliation_output(prepared=item, output=normalized) == normalized
            by_fact = {decision["fact_id"]: decision for decision in raw["decisions"]}
            for decision in normalized["decisions"]:
                for relation in decision["governed_by"]:
                    assert {"relation": relation["relation"], "fact_id": relation["directive_fact_id"]} in by_fact[decision["fact_id"]]["governed_by"]
                    relations.append({"item_index": index, "fact_id": decision["fact_id"], **relation})
                    relation_source = (item, raw, normalized)
            outputs.append(normalized)
            evidence.append({"item_index": index, "input_hash": action_state_hash(item),
                "raw_output_hash": receipt["raw_output_hash"], "canonical_output_hash": receipt["output_hash"],
                "decision_count": len(normalized["decisions"]), "parent_tool_call_id": receipt["parent_tool_call_id"]})
        assert relations and relation_source is not None
        action = generation.prepare_authority_action(context, {"item_indexes": list(range(len(prepared_items)))})
        observed = generation.apply_authority_action(context, action, outputs)
        assert observed["authority"]["pending_indexes"] == [] and observed["authority"]["ready"]
        assert context.action_state["authority_records"] == [generation._record(item, value, index)
            for index, (item, value) in enumerate(zip(prepared_items, outputs, strict=True))]
        assert initial_state == checkpoint["state"] and all(record is None for record in initial_state["authority_records"])
        source_facts = initial_state["accepted_plan"]["source_semantics"]["authoritative_facts"]
        merged = merge_authority_reconciliation(generation._isolated(context), {
            "authoritative_facts": source_facts, "prepared_items": prepared_items,
            "reconciliation_records": context.action_state["authority_records"],
        })
        assert merged == context.action_state["effective_facts"]
        merged_by_id = {fact["fact_id"]: fact for fact in merged["authoritative_facts"]}
        for relation in relations:
            assert {key: relation[key] for key in ("relation", "directive_fact_id")} in merged_by_id[relation["fact_id"]]["governed_by"]
        assert sum(item["case_budget"] for item in context.action_state["generation_inputs"]) == context.action_input["case_budget"]
        item, raw, normalized = relation_source
        failures = {
            "agent_fields_at_canonical_boundary": reject(lambda: validate_authority_reconciliation_output(prepared=item, output=raw)),
            "canonical_fields_at_raw_schema": reject(lambda: validate(normalized, AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA)),
            "canonical_sent_to_raw_converter_reproduces_old_failure": reject(lambda: postprocess_authority_reconciliation_item(
                context, {"item_input": item, "item_output": normalized})),
            "non_array_decisions": reject(lambda: validate_authority_reconciliation_output(prepared=item,
                output={"decisions": json.dumps(normalized["decisions"], ensure_ascii=False)})),
        }
        db.expire_all()
        assert fingerprint(db.get(AgentRun, run_id), db.get(AgentNodeRun, node_id)) == original
        assert db.query(AgentRunEvent).filter(AgentRunEvent.run_id == run_id).count() == event_count
        assert not db.new and not db.dirty and not db.deleted
        result = {
            "status": "passed", "source_run_id": run_id, "source_node_id": node_id,
            "source_status": "failed", "original_failure": node.error_message,
            "full_saved_input_and_frozen_semantic_matched": True,
            "validated_specialist_count": len(outputs), "validated_decision_count": sum(len(value["decisions"]) for value in outputs),
            "canonical_directive_relations_preserved": relations,
            "parent_apply_all_items_and_merge_passed": True, "prepared_generation_batch_count": len(context.action_state["generation_inputs"]),
            "cold_start_accept_rejection": cold_rejection, "cold_start_rejection_state_unchanged": True,
            "case_budget_preserved": context.action_input["case_budget"], "wrong_contract_rejections": failures,
            "source_fingerprint": original, "source_event_count_unchanged": event_count,
            "original_state_receipts_budget_and_failure_unchanged": True,
            "production_source_hashes": source_hashes, "model_requests_sent": 0, "database_writes": 0,
            "mysql_transaction": "READ ONLY",
            "verification_limits": ["在内存回放失败前11份真实成功专业回执，不恢复旧semantic执行，不调用模型，不修改原失败运行"],
            "items": evidence,
        }
    assert source_hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("验证报告已存在，禁止覆盖")
    result = verify(args.run_id, args.node_id)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "validated_specialist_count",
        "validated_decision_count", "prepared_generation_batch_count", "model_requests_sent")}, ensure_ascii=False))
