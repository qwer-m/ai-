"""使用真实已成功运行的准备输入验证动态布局，不请求模型、不写线上数据库。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentNodeRun, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.output_repair import OutputValidationError
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.run_repository import AgentRunRepository
from modules.agent_platform.test_generation_batching import (
    GENERATION_MAX_REQUIRED_FACTS_PER_BATCH,
    generation_layout_from_inputs,
    postprocess_generation_batch_item,
    prepare_test_case_batches,
)
from scripts.qa.verify.generation_contract_history import project_historical_generation_inputs


def canonical_bytes(value: object) -> bytes:
    # MySQL JSON 对象的键顺序不是历史序列化顺序，按统一顺序比较完整内容字节。
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify(db, run_id: int, node_id: int, item_index: int) -> dict:
    run = db.get(AgentRun, run_id)
    node = db.get(AgentNodeRun, node_id)
    if (run is None or run.status != "success" or node is None or node.run_id != run_id
            or node.node_key != "prepare_generation" or node.status != "success"):
        raise ValueError("必须指定真实成功运行的 prepare_generation 节点")
    source = AgentRunRepository(db).read_node_input(node)
    persisted = deepcopy(node.output_payload)
    persisted["items"], contract_projection = project_historical_generation_inputs(persisted["items"])
    before = action_state_hash({"input": source, "output": node.output_payload, "run_context": run.run_context})

    def prepare(arguments: dict) -> dict:
        context = ToolExecutionContext(
            db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
            node_key=node.node_key, run_input=deepcopy(run.input_payload),
        )
        return prepare_test_case_batches(context, deepcopy(arguments))

    default = prepare(source)
    assert canonical_bytes(default) == canonical_bytes(persisted)
    layout = generation_layout_from_inputs(default["items"])
    explicit = prepare({**source, "generation_layout": layout})
    assert canonical_bytes(explicit) == canonical_bytes(persisted)
    assert generation_layout_from_inputs(explicit["items"]) == layout
    generation_node = AgentRunRepository(db).latest_node_run(run_id=run.id, node_key="generation")
    assert generation_node is not None and generation_node.status == "success"
    generation_inputs, generated_projection = project_historical_generation_inputs(
        AgentRunRepository(db).read_node_input(generation_node)["items"],
    )
    assert generation_inputs == default["items"]
    outputs = {record["item_index"]: record["output"] for record in generation_node.output_payload["items"]}
    accepted_replays = []
    for saved in generation_node.sdk_state["items"]:
        accepted = [item for item in saved["tool_observations"] if item["status"] == "accepted"]
        assert len(accepted) == 1
        submission = next(item for item in saved["tool_calls"] if item["tool_call_id"] == accepted[0]["tool_call_id"])
        index = saved["item_index"]
        replayed = postprocess_generation_batch_item(None, {
            "item_input": generation_inputs[index], "item_output": deepcopy(submission["arguments"]),
        })
        assert replayed == outputs[index]
        accepted_replays.append(index)
    target = default["items"][item_index]
    target_module = target["batch"]["module_index"]
    target_group = target["batch"]["module_batch_index"]
    assert target["case_budget"] >= 2 and len(target["authoritative_facts"]) >= 2

    def check_contracts(output: dict) -> None:
        assert output["case_budget"] == source["case_budget"]
        assert sum(item["case_budget"] for item in output["items"]) == source["case_budget"]
        expected = {
            entry["module_index"]: {value for group in entry["groups"] for value in group["fact_ids"]}
            for entry in layout
        }
        actual = generation_layout_from_inputs(output["items"])
        assert {entry["module_index"]: {value for group in entry["groups"] for value in group["fact_ids"]}
                for entry in actual} == expected
        for item in output["items"]:
            facts = {fact["fact_id"] for fact in item["authoritative_facts"]}
            contract = item["case_fact_contract"]
            assert set(contract["required_fact_ids"]) == facts
            assert len(contract["target_case_ids"]) == item["case_budget"]
            assert "coverage_slots" not in contract
            assert set(contract["fact_design_item_ids"]) == facts
            assert {value for values in contract["fact_design_item_ids"].values() for value in values} == set(contract["required_test_design_item_ids"])
            assert set(contract["required_test_design_item_ids"]) == {
                value["test_design_item_id"] for value in item["plan"]["test_design_items"]
            }

    split_layout = deepcopy(layout)
    original = split_layout[target_module]["groups"][target_group]
    middle = len(original["fact_ids"]) // 2
    split_layout[target_module]["groups"][target_group:target_group + 1] = [
        {"fact_ids": original["fact_ids"][:middle], "case_budget": 1},
        {"fact_ids": original["fact_ids"][middle:], "case_budget": original["case_budget"] - 1},
    ]
    split = prepare({**source, "generation_layout": split_layout})
    check_contracts(split)
    assert len(split["items"]) == len(default["items"]) + 1

    transferred_layout = deepcopy(layout)
    donor_index, donor = next((index, item) for index, item in enumerate(default["items"])
                             if index != item_index and item["case_budget"] > 1)
    assert target["case_budget"] < source["batch_case_limit"]
    transferred_layout[target_module]["groups"][target_group]["case_budget"] += 1
    transferred_layout[donor["batch"]["module_index"]]["groups"][donor["batch"]["module_batch_index"]]["case_budget"] -= 1
    transferred = prepare({**source, "generation_layout": transferred_layout})
    check_contracts(transferred)
    changed = [index for index, (old, new) in enumerate(zip(default["items"], transferred["items"], strict=True))
               if canonical_bytes(old) != canonical_bytes(new)]
    assert set(changed) == {donor_index, item_index}

    rejected = []

    def reject_layout(label: str, candidate: object, detail_key: str) -> None:
        try:
            prepare({**source, "generation_layout": candidate})
        except OutputValidationError as error:
            assert error.details["field"] == "generation_layout" and detail_key in error.details
            rejected.append({"case": label, "message": str(error), "details": error.details})
        else:
            raise AssertionError(f"无效真实数据布局被接受: {label}")

    candidate = deepcopy(layout)
    target_ids = {value for group in candidate[target_module]["groups"] for value in group["fact_ids"]}
    foreign_id = next(fact["fact_id"] for fact in source["effective_facts"] if fact["fact_id"] not in target_ids)
    candidate[target_module]["groups"][target_group]["fact_ids"][0] = foreign_id
    reject_layout("other_module_real_fact", candidate, "unknown_fact_ids")
    candidate = deepcopy(layout)
    candidate[target_module]["groups"][target_group]["fact_ids"].pop()
    reject_layout("missing_real_fact", candidate, "missing_fact_ids")
    candidate = deepcopy(layout)
    candidate[target_module]["groups"][target_group]["fact_ids"].append(candidate[target_module]["groups"][target_group]["fact_ids"][0])
    reject_layout("duplicate_real_fact", candidate, "duplicate_fact_ids")
    candidate = deepcopy(layout)
    candidate.pop()
    reject_layout("missing_active_module", candidate, "missing_module_indexes")
    candidate = deepcopy(layout)
    candidate.append(deepcopy(candidate[0]))
    reject_layout("duplicate_active_module", candidate, "module_index")
    candidate = deepcopy(layout)
    candidate[target_module]["groups"][target_group]["case_budget"] += 1
    reject_layout("changed_total_budget", candidate, "expected_case_budget")
    candidate = deepcopy(layout)
    candidate[target_module]["groups"][target_group]["case_budget"] = source["batch_case_limit"] + 1
    reject_layout("exceeded_batch_budget", candidate, "maximum")

    # 合并真实已有的来源上下文包，用真实事实触发数量/字符上限，不伪造页面或事实。
    candidate = deepcopy(layout)
    oversized_module = next(entry for entry in candidate
                            if len(entry["groups"]) > 1
                            and sum(len(group["fact_ids"]) for group in entry["groups"]) > GENERATION_MAX_REQUIRED_FACTS_PER_BATCH)
    oversized_module["groups"] = [{
        "fact_ids": [value for group in oversized_module["groups"] for value in group["fact_ids"]],
        "case_budget": 1,
    }]
    reject_layout("merged_real_groups_exceed_context_limits", candidate, "limits")

    damaged = deepcopy(source)
    damaged["effective_facts"][0]["status"] = "inactive"
    try:
        prepare({**damaged, "generation_layout": layout})
    except OutputValidationError as error:
        raise AssertionError("来源损坏被错误反馈成可修复布局问题") from error
    except ValueError as error:
        source_failure = str(error)
    else:
        raise AssertionError("损坏来源被接受")

    assert action_state_hash({"input": source, "output": node.output_payload, "run_context": run.run_context}) == before
    assert not db.new and not db.dirty and not db.deleted
    return {
        "status": "passed", "source_run_id": run.id, "source_node_id": node.id,
        "target_item_index": item_index, "target_batch_id": target["batch"]["batch_id"],
        "comparison": "complete_output_canonical_json_bytes_after_retired_field_projection",
        "historical_generation_contract_projection": contract_projection, "default_output_unchanged": True,
        "generation_contract_projection": generated_projection,
        "real_accepted_raw_submissions_revalidated_unchanged": accepted_replays,
        "explicit_original_layout_output_unchanged": True, "original_layout": layout,
        "split": {"layout": split_layout, "old_batch_count": len(default["items"]),
                  "new_batch_count": len(split["items"]), "output_sha256": action_state_hash(split)},
        "budget_transfer": {"donor_item_index": donor_index, "target_item_index": item_index,
                            "changed_item_indexes": changed, "output_sha256": action_state_hash(transferred)},
        "case_budget_unchanged": source["case_budget"], "invalid_layouts_rejected": rejected,
        "damaged_source_hard_failure": source_failure, "source_database_evidence_unchanged": True,
        "mysql_transaction": "READ ONLY", "online_database_writes": 0, "model_requests_sent": 0,
        "verification_limits": ["未执行模型自主布局选择或新布局生成",
                                "本来源未覆盖跨文档分组或超过十页的布局样本，仍复用同一生产分组上限"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--node-id", type=int, required=True)
    parser.add_argument("--item-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise ValueError("报告已存在，拒绝覆盖真实验证证据")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = verify(db, options.run_id, options.node_id, options.item_index)
        db.rollback()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "invalid_layouts_rejected": len(report["invalid_layouts_rejected"]),
                      "model_requests_sent": 0, "report": str(options.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
