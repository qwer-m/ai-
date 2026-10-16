"""只读比较清理前后的真实生成记录和图片资产，不调用模型或构造业务样本。"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select

from core.db.database import engine
from core.db.model_defs import AgentNodeRun
from modules.agent_platform import test_generation_batching as batching
from modules.agent_platform import test_generation_facts as facts
from modules.agent_platform import test_generation_review as review
from modules.agent_platform import test_generation_workflow as workflow
from modules.agent_platform.input_snapshots import restore_node_input
from modules.knowledge_base_components.document.document_asset_service import resolve_document_asset_dir


def _load_baseline(directory: Path) -> tuple[Any, Any, Any, Any]:
    """加载修改前保存的完整源码包，比较对象是原实现而非测试替身。"""

    package_name = "agent_generation_cleanup_baseline"
    spec = importlib.util.spec_from_file_location(
        package_name,
        directory / "__init__.py",
        submodule_search_locations=[str(directory)],
    )
    if spec is None or spec.loader is None:
        raise ValueError("无法加载清理前的 agent_platform 源码目录")
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    spec.loader.exec_module(package)
    return tuple(
        importlib.import_module(f"{package_name}.{name}")
        for name in (
            "test_generation_batching",
            "test_generation_review",
            "test_generation_semantics",
            "test_generation_workflow",
        )
    )


def _outcome(function: Callable[..., Any], **arguments: Any) -> tuple[str, Any]:
    try:
        return "accepted", function(**deepcopy(arguments))
    except ValueError as exc:
        # 历史产物可能不满足当前规则；必须区分既有拒绝与清理引入的变化。
        return type(exc).__name__, {
            "message": str(exc),
            "strategy_key": getattr(exc, "strategy_key", None),
            "details": getattr(exc, "details", None),
        }


def _verify_width_variant(arguments: dict[str, Any]) -> bool:
    """在真实用例副本中注入同标题全角变体，验证最终校验与批次去重规则一致。"""

    for case in arguments["test_cases"]:
        title = case["title"]
        if not any(char.isalnum() and 33 <= ord(char) <= 126 for char in title):
            continue
        others = [item for item in arguments["test_cases"] if item["case_id"] != case["case_id"]]
        if not others:
            continue
        changed = deepcopy(arguments)
        duplicate = deepcopy(case)
        duplicate["case_id"] = others[0]["case_id"]
        duplicate["title"] = "".join(
            chr(ord(char) + 0xFEE0) if char.isalnum() and 33 <= ord(char) <= 126 else char
            for char in title
        )
        changed["test_cases"] = [deepcopy(case), duplicate]
        changed["case_budget"] = len(changed["test_cases"])
        assert facts.text_identity(title) == facts.text_identity(duplicate["title"])
        outcome = _outcome(workflow.validate_generated_test_cases, context=None, arguments=changed)
        assert outcome[0] == "ValueError" and "用例语义重复" in outcome[1]["message"]
        return True
    return False


def _verify_regions(before_batching: Any, before_semantics: Any) -> dict[str, Any]:
    document_ids: list[int] = []
    checked = 0
    rejected = 0
    for manifest_path in sorted(resolve_document_asset_dir().glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        regions = [
            block["bbox"]
            for page in manifest.get("pages", [])
            for block in page.get("blocks", [])
            if block.get("type") == "image" and "bbox" in block
        ]
        if not regions:
            continue
        document_ids.append(int(manifest_path.parent.name))
        for region in regions:
            assert facts.validate_image_region(region) == before_batching._image_evidence_region(region)
            assert facts.validate_image_region(region) == before_semantics._image_region(region)
            checked += 1
        # 仅在真实区域副本中注入坐标故障，验证收敛没有放松原有边界。
        original = regions[0]
        invalid_regions = [
            {key: value for key, value in original.items() if key != "x"},
            {**original, "x": True},
            {**original, "x": -abs(original["width"])},
            {**original, "width": 0},
            {**original, "width": float("nan")},
            {**original, "width": 1 + original["width"]},
        ]
        for invalid in invalid_regions:
            for validate in (
                facts.validate_image_region,
                before_batching._image_evidence_region,
                before_semantics._image_region,
            ):
                try:
                    validate(invalid)
                except ValueError:
                    pass
                else:
                    raise AssertionError("图片区域故障副本未被拒绝")
            rejected += 1
    assert checked, "验证必须读取到真实图片资产"
    return {"document_ids": document_ids, "regions": checked, "rejected_mutations": rejected}


def verify(baseline_directory: Path) -> dict[str, Any]:
    before_batching, before_review, before_semantics, before_workflow = _load_baseline(baseline_directory)
    counts: Counter[str] = Counter()
    run_ids: set[int] = set()
    generation_outcomes: Counter[str] = Counter()
    review_outcomes: Counter[str] = Counter()
    final_validation_outcomes: Counter[str] = Counter()
    with engine.connect() as connection:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        rows = connection.execute(select(
            AgentNodeRun.id,
            AgentNodeRun.run_id,
            AgentNodeRun.status,
            AgentNodeRun.input_payload,
            AgentNodeRun.output_payload,
        ).where(AgentNodeRun.status == "success")).all()
        nodes_by_id = {node.id: node for node in rows}
        for node in rows:
            node_input = restore_node_input(node, nodes_by_id.get)
            node = node._mapping
            node_output = dict(node["output_payload"] or {})
            if "validated_count" in node_output and "test_cases" in node_input:
                arguments = {"context": None, "arguments": node_input}
                original = _outcome(before_workflow.validate_generated_test_cases, **arguments)
                current = _outcome(workflow.validate_generated_test_cases, **arguments)
                assert current == original, f"最终用例校验行为变化: node={node['id']}"
                final_validation_outcomes[current[0]] += 1
                counts["final_validations"] += 1
                if current[0] == "accepted":
                    counts["width_variant_rejections"] += _verify_width_variant(node_input)
            inputs = node_input.get("items")
            records = node_output.get("items")
            if not isinstance(inputs, list) or not isinstance(records, list):
                continue
            for record in records:
                source = inputs[record["item_index"]]
                output = record["output"]
                if "case_fact_contract" in source:
                    arguments = {"source_input": source, "output": output}
                    original = _outcome(before_batching._validate_generation_batch_output, **arguments)
                    current = _outcome(batching._validate_generation_batch_output, **arguments)
                    assert current == original, f"生成校验行为变化: node={node['id']}"
                    generation_outcomes[current[0]] += 1
                    counts["generation_batches"] += 1
                    counts["generated_cases"] += len(output["test_cases"])
                    for case in output["test_cases"]:
                        for field in ("module", "title"):
                            assert facts.text_identity(case[field]) == before_batching._identity(case[field])
                            counts["text_identities"] += 1
                    run_ids.add(node["run_id"])
                if "review_facts" not in source or "case_fact_bindings" not in source:
                    continue
                original = _outcome(
                    before_review.postprocess_final_review_batch_item,
                    context=None,
                    arguments={"item_input": source, "item_output": output},
                )
                current = _outcome(
                    review.postprocess_final_review_batch_item,
                    context=None,
                    arguments={"item_input": source, "item_output": output},
                )
                assert current == original, f"终审后处理行为变化: node={node['id']}"
                review_outcomes[current[0]] += 1
                counts["review_batches"] += 1
                indexed = facts.binding_index(source["case_fact_bindings"])
                for case in source["test_cases"]:
                    # 字段路径从真实用例结构派生，覆盖输入、前置条件和所有步骤槽位。
                    paths = [None, "test_input"]
                    paths.extend(f"preconditions[{i}]" for i in range(len(case["preconditions"])))
                    paths.extend(
                        f"steps[{i}].{field}"
                        for i in range(len(case["steps"]))
                        for field in ("action", "expected")
                    )
                    for path in paths:
                        original_ids = before_review._difference_fact_ids(
                            source_input=source, case_id=case["case_id"], field_path=path,
                        )
                        current_ids = review._difference_fact_ids(
                            bindings_by_case_id=indexed, case_id=case["case_id"], field_path=path,
                        )
                        assert current_ids == original_ids
                        counts["field_bindings"] += 1
                repeated = review._repeated_fact_ids(source_input=source)
                for fact in source["review_facts"]:
                    fact_id = fact["fact_id"]
                    assert (fact_id in repeated) == before_review._has_repeated_fact_obligation(
                        source_input=source, related_fact_ids=[fact_id],
                    )
                    counts["fact_repetition_decisions"] += 1
                run_ids.add(node["run_id"])
        assert counts["generation_batches"] and counts["review_batches"], "缺少真实生成或终审记录"
        assert generation_outcomes["accepted"] and review_outcomes["accepted"]
        assert final_validation_outcomes["accepted"], "缺少最终用例校验的真实成功输入"
        assert counts["width_variant_rejections"], "真实标题没有可验证全半角去重的字符"
    return {
        "run_ids": sorted(run_ids),
        "comparisons": dict(counts),
        "generation_outcomes": dict(generation_outcomes),
        "review_outcomes": dict(review_outcomes),
        "final_validation_outcomes": dict(final_validation_outcomes),
        "images": _verify_regions(before_batching, before_semantics),
        "database_read_only": True,
        "model_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True, help="清理前保存的 agent_platform 源码目录")
    parser.add_argument("--output", type=Path, help="仅保存计数与运行编号，不导出业务正文")
    options = parser.parse_args()
    result = verify(options.baseline_dir.resolve())
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if options.output:
        options.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
