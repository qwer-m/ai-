"""用真实 PDF 网格的全部可见单元格验证锚点契约，不补写历史模型的单元格选择。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import validate
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_schemas import (
    FACT_SOURCE_CONTEXT_SCHEMA, SOURCE_ANCHOR_SCHEMA, SOURCE_SEMANTICS_AGENT_ANCHOR_SCHEMA,
)


def rejected(callback):
    try:
        callback()
    except ValueError as error:
        return str(error)
    raise AssertionError("无效来源锚点必须被拒绝")


def verify(source_report_path: Path):
    report = json.loads(source_report_path.read_text("utf-8"))
    inputs_path = Path(report["input_path"])
    saved = json.loads(inputs_path.read_text("utf-8"))
    source_hashes = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (source_report_path, inputs_path)}
    positives, context_only_rejections, non_table = [], [], []
    legacy_rejections = []
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, report["source_run_id"])
            before = action_state_hash([run.run_context, run.output_payload, run.status])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key="source_text", run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(saved["source_artifacts"]))
            for fragment in saved["source_fragments"]:
                item = fragment["input"]
                hydrated = semantics._hydrate_source_semantics_item(context, item)
                semantics._validate_source_semantics_input(context, hydrated)
                original_pages = {page["page_number"]: page for page in hydrated.get("pages", [hydrated])}
                for page in item.get("pages", [item]):
                    original = original_pages[page["page_number"]]
                    allowed = {value for scope in page["source_scopes"] for value in scope["allowed_block_ids"]}
                    table_blocks = {segment["block_id"] for table in original.get("table_contexts", [])
                                    for row in table["rows"] for cell in row["cells"] for segment in cell["segments"]}
                    for table in page.get("table_contexts", []):
                        for row in table["rows"]:
                            for cell in row["cells"]:
                                if not cell["segments"]:
                                    continue
                                reference = {"table_id": table["table_id"], "row_index": row["row_index"],
                                             "column_index": cell["column_index"]}
                                visible = [segment for segment in cell["segments"] if segment["block_id"] in allowed]
                                selected = (visible or cell["segments"])[0]
                                anchor = {"document_id": page["document_id"], "page_number": page["page_number"],
                                          "block_id": selected["block_id"], "table_cell": reference}
                                validate(anchor, SOURCE_SEMANTICS_AGENT_ANCHOR_SCHEMA)
                                if not visible:
                                    reason = rejected(lambda: semantics._validated_document_anchor(anchor, original))
                                    context_only_rejections.append({"source_item_index": fragment["source_item_index"],
                                        "table_cell": reference, "reason": reason})
                                    continue
                                canonical, marked, scopes = semantics._validated_document_anchor(anchor, original)
                                validate(canonical, SOURCE_ANCHOR_SCHEMA)
                                assert "source_span" not in canonical and "quote" not in canonical
                                assert canonical["source_segments"] == [
                                    {"block_id": segment["block_id"], "text": segment["text"],
                                     "source_span": segment["original_source_span"]} for segment in cell["segments"]]
                                assert semantics._validated_document_anchor(canonical, original) == (canonical, marked, scopes)
                                compact = semantics._source_context_for_anchor(original, canonical)
                                validate(compact, FACT_SOURCE_CONTEXT_SCHEMA)
                                assert len(compact["table_contexts"]) == 1
                                actual_rows = compact["table_contexts"][0]["rows"]
                                assert {value["row_index"] for value in actual_rows} == {0, row["row_index"]}
                                # 上下文保留完整真实同行与对应表头；精确锚点仍只包含所选格。
                                row_columns = {value["column_index"] for value in row["cells"]}
                                expected_rows = [{"row_index": source_row["row_index"], "cells": [
                                    {"column_index": source_cell["column_index"],
                                     "text": "\n".join(segment["text"] for segment in source_cell["segments"])}
                                    for source_cell in source_row["cells"] if source_cell["column_index"] in row_columns
                                ]} for source_row in table["rows"] if source_row["row_index"] in {0, row["row_index"]}]
                                assert actual_rows == expected_rows
                                target = next(value for value in actual_rows if value["row_index"] == row["row_index"])
                                target_text = next(value["text"] for value in target["cells"]
                                                   if value["column_index"] == cell["column_index"])
                                assert target_text == "\n".join(segment["text"] for segment in cell["segments"])
                                negatives = {
                                    "missing_table_cell": rejected(lambda: semantics._validated_document_anchor(
                                        {key: value for key, value in anchor.items() if key != "table_cell"}, original)),
                                    "null_table_cell": rejected(lambda: semantics._validated_document_anchor(
                                        {**anchor, "table_cell": None}, original)),
                                }
                                altered = deepcopy(canonical)
                                altered["source_segments"][0]["source_span"]["start"] += 1
                                negatives["altered_segment"] = rejected(
                                    lambda: semantics._validated_document_anchor(altered, original))
                                invalid_cell = {**reference, "row_index": max(value["row_index"] for value in table["rows"]) + 1}
                                negatives["unknown_cell"] = rejected(lambda: semantics._validated_document_anchor(
                                    {**anchor, "table_cell": invalid_cell}, original))
                                wrong_block = next((value for value in allowed
                                                   if value not in {segment["block_id"] for segment in cell["segments"]}), None)
                                if wrong_block is not None:
                                    negatives["unrelated_block"] = rejected(lambda: semantics._validated_document_anchor(
                                        {**anchor, "block_id": wrong_block}, original))
                                segment = canonical["source_segments"][0]
                                # 只验证真实坐标切片，不构造业务事实或声称模型已选择这些单元格。
                                selected_value = semantics._materialize_governed_values(
                                    fact={"value_policy": "runtime_configured", "governed_value_spans": [segment["source_span"]]},
                                    prepared=original, normalized_anchor=canonical, fact_id=action_state_hash(reference),
                                    value_policy="runtime_configured", assertion=target_text)
                                assert selected_value == [segment["text"]]
                                neighbor = next((piece for value in row["cells"]
                                                 if value["column_index"] != cell["column_index"]
                                                 for piece in value["segments"]), None)
                                if neighbor is not None:
                                    negatives["neighbor_governed_span"] = rejected(lambda: semantics._materialize_governed_values(
                                        fact={"value_policy": "runtime_configured", "governed_value_spans": [neighbor["original_source_span"]]},
                                        prepared=original, normalized_anchor=canonical, fact_id=action_state_hash(reference),
                                        value_policy="runtime_configured", assertion=target_text))
                                positives.append({"source_item_index": fragment["source_item_index"],
                                    "canonical_anchor": canonical, "source_context": compact,
                                    "scope_ids": sorted(scopes), "negative_checks": negatives})
                    for block in page["blocks"]:
                        if block["block_id"] in table_blocks or block.get("type") == "image":
                            continue
                        anchor = {"document_id": page["document_id"], "page_number": page["page_number"],
                                  "block_id": block["block_id"], "table_cell": None}
                        validate(anchor, SOURCE_SEMANTICS_AGENT_ANCHOR_SCHEMA)
                        canonical, _, _ = semantics._validated_document_anchor(anchor, original)
                        validate(canonical, SOURCE_ANCHOR_SCHEMA)
                        assert "source_segments" not in canonical and "table_cell" not in canonical
                        assert semantics._source_context_for_anchor(original, canonical) is None
                        non_table.append(canonical)
                prior = next(record for record in report["fragments"]
                             if record["source_item_index"] == fragment["source_item_index"])
                legacy_rejections.append(rejected(lambda: semantics.postprocess_source_semantics_item(context, {
                    "item_input": deepcopy(item), "item_output": deepcopy(prior["candidate"])})))
            assert positives and non_table and context_only_rejections and legacy_rejections
            assert before == action_state_hash([run.run_context, run.output_payload, run.status])
            assert not db.new and not db.dirty and not db.deleted
        finally:
            db.rollback()
    return {"status": "passed", "source_run_id": report["source_run_id"], "source_file_hashes": source_hashes,
        "scope": "真实网格单元格的确定性契约校验；未补写历史模型候选、未产生新模型事实",
        "model_requests_sent": 0, "database_writes": 0, "visible_cell_count": len(positives),
        "unique_cell_count": len({action_state_hash(item["canonical_anchor"]["table_cell"]) for item in positives}),
        "multi_segment_cell_count": sum(len(item["canonical_anchor"]["source_segments"]) > 1 for item in positives),
        "non_table_block_count": len(non_table), "context_only_rejections": context_only_rejections,
        "old_unlocated_candidates_rejected": legacy_rejections, "verified_anchors": positives,
        "verification_limits": ["没有单元格定位的旧模型事实明确失效；新模型事实的语义对应须单独真实重提取审查。",
                                "本报告覆盖真实P11网格及其非表格文本，不替代完整多文档与图片模型运行。"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("拒绝覆盖真实网格验收证据")
    result = verify(args.source_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in
        ("status", "visible_cell_count", "unique_cell_count", "multi_segment_cell_count", "non_table_block_count",
         "model_requests_sent", "database_writes")}, ensure_ascii=False))
