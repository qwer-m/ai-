"""只读真实修复结果，验证源输入损坏硬失败、错批候选可反馈及有效产物不变。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_tools import AutonomousToolLoop
from modules.agent_platform.contracts import AgentMapConfig
from modules.agent_platform.output_repair import OutputRepairError, OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext, tool_registry
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.sdk_adapter import AgentOutputContract, OutputPostprocessingError
from modules.agent_platform.test_generation_repair import REVIEW_REPAIR_STRATEGY
from modules.agent_platform.test_generation_review import postprocess_final_review_repair_item
from scripts.qa.verify.verify_specialist_submission_real_data import historical_patch


def verify(run_id: int) -> dict:
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, run_id)
        ids = db.query(AgentNodeRun.id).filter(
            AgentNodeRun.run_id == run_id, AgentNodeRun.node_key == "final_review_repairs",
            AgentNodeRun.status == "success",
        ).order_by(AgentNodeRun.id).all()
        if run is None or not ids:
            raise ValueError("需要真实成功的历史修复节点")
        node = db.get(AgentNodeRun, ids[-1][0])
        definition = db.get(AgentDefinition, node.agent_definition_id)
        if definition is None:
            raise ValueError("真实修复 Agent 定义不存在")
        sources = AgentRunRepository(db).read_node_input(node)["items"]
        outputs = {record["item_index"]: record["output"] for record in node.output_payload["items"]}
        if len(sources) != len(outputs) or len(sources) < 2:
            raise ValueError("需要至少两个真实修审批次以验证错批候选")
        before_hash = action_state_hash({"sources": sources, "outputs": outputs})
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                                       run_id=run.id, node_key=node.node_key,
                                       run_input=deepcopy(run.input_payload))

        def contract(source):
            return AgentOutputContract(deepcopy(source), AgentMapConfig(
                item_postprocessor="testing.postprocess_final_review_repair_item",
            ))

        classifier = AutonomousToolLoop(
            terminal_tool_names={"submit_result"},
            validate_submission=lambda value: contract(sources[0]).validate(definition, context, value),
            recoverable_errors=(OutputValidationError,), max_tool_calls=1,
        )
        valid_hashes = []
        for index, source in enumerate(sources):
            patch = historical_patch(outputs[index], definition.output_schema)
            normalized = contract(source).validate(definition, context, patch)
            assert normalized == outputs[index], f"真实有效修复结果改变: item_index={index}"
            valid_hashes.append(action_state_hash(normalized))

        target_index = next(index for index, source in enumerate(sources) if len(source["test_cases"]) >= 2)
        source = sources[target_index]
        valid_patch = historical_patch(outputs[target_index], definition.output_schema)
        other_index = next(index for index, other in enumerate(sources)
                           if index != target_index and other["test_cases"][0]["module"] != source["test_cases"][0]["module"])
        other = sources[other_index]
        damaged_count = deepcopy(source)
        damaged_count["target_case_count"] += 1
        damaged_scope = deepcopy(source)
        damaged_scope["target_case_ids"] = deepcopy(other["target_case_ids"])
        damaged_module = deepcopy(source)
        damaged_module["test_cases"][0]["module"] = other["test_cases"][0]["module"]
        damaged_original = deepcopy(source)
        damaged_original["test_cases"][0]["steps"][0].pop("expected")
        hard_failures = []
        for name, damaged in (
            ("target_count", damaged_count), ("target_scope", damaged_scope),
            ("module_boundary", damaged_module), ("original_materialize", damaged_original),
        ):
            try:
                postprocess_final_review_repair_item(context, {
                    "item_input": damaged, "item_output": deepcopy(valid_patch),
                })
            except ValueError as error:
                assert not isinstance(error, OutputValidationError), f"{name} 源输入损坏被错误分类为模型可修复错误"
                direct_type = type(error).__name__
            else:
                raise AssertionError(f"{name} 源输入损坏未被拒绝")
            try:
                contract(damaged).validate(definition, context, deepcopy(valid_patch))
            except OutputPostprocessingError as wrapped:
                assert isinstance(wrapped.__cause__, ValueError)
                assert not isinstance(wrapped.__cause__, OutputValidationError)
                assert classifier.feedback_for_error(wrapped) is None, f"{name} 错误不应进入专业模型修正循环"
                hard_failures.append({"fault": name, "direct_error_type": direct_type,
                                      "sdk_error_type": type(wrapped).__name__, "model_feedback": None})
            else:
                raise AssertionError(f"{name} 损坏来源通过了 SDK 完整契约")

        wrong_batch_patch = historical_patch(outputs[other_index], definition.output_schema)
        try:
            contract(source).validate(definition, context, wrong_batch_patch)
        except OutputPostprocessingError as wrapped:
            cause = wrapped.__cause__
            assert isinstance(cause, OutputRepairError)
            assert cause.strategy_key == REVIEW_REPAIR_STRATEGY
            assert tool_registry.resolve_repair_strategy(cause.strategy_key) is not None
            feedback = classifier.feedback_for_error(wrapped)
            assert feedback is not None
            candidate_error = {"sdk_error_type": type(wrapped).__name__, "cause_type": type(cause).__name__,
                               "strategy_key": cause.strategy_key, "model_feedback_available": True}
        else:
            raise AssertionError("错批次真实候选被验收")

        assert action_state_hash({"sources": sources, "outputs": outputs}) == before_hash
        assert not context.artifacts and not context.action_state
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
        return {
            "status": "passed", "verification_scope": "historical_real_outputs_and_corrupted_source_copies",
            "source_run_id": run_id, "source_node_id": node.id,
            "valid_outputs_checked": len(valid_hashes), "valid_output_sha256": valid_hashes,
            "source_errors": hard_failures, "wrong_batch_candidate": candidate_error,
            "target_batch_id": source["review_batch"]["batch_id"],
            "other_actual_batch_id": other["review_batch"]["batch_id"],
            "patch_representation": "normalized_historical_cases_projected_to_existing_patch_schema",
            "source_evidence_unchanged": True, "online_database_writes": 0, "model_requests_sent": 0,
            "natural_model_error_sequence_replayed": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError("报告已存在，拒绝覆盖历史证据")
    result = verify(options.run_id)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "status", "source_run_id", "source_node_id", "valid_outputs_checked", "model_requests_sent",
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
