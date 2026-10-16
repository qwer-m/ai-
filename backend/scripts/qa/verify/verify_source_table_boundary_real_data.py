"""使用保存的真实失败输入和原 PDF 验证表格边界、单元格坐标及重试反馈。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import io
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pdfplumber
from jsonschema import validate

from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_schemas import SOURCE_SEMANTICS_INPUT_SCHEMA
from modules.knowledge_base_components.document import document_asset_service as assets


def verify(saved: dict) -> dict:
    document_id = saved["input_payload"]["requirement_doc_id"]
    original_artifacts = saved["source_artifacts"]
    source = original_artifacts["requirement_evidence"]
    context = ToolExecutionContext(
        db=None, user_id=saved["user_id"], project_id=saved["project_id"], run_id=0,
        node_key="prepare_source_semantics", run_input=deepcopy(saved["input_payload"]),
        artifacts=deepcopy(original_artifacts),
    )
    manifest = assets.load_document_manifest(document_id)
    geometry = []
    with pdfplumber.open(io.BytesIO(assets._document_source_bytes(document_id, manifest))) as pdf:
        for page in pdf.pages:
            before = original_artifacts["source_semantics_source_pages"][f"{document_id}:{page.page_number}"]
            after = assets._page_table_layouts(page, assets.document_page_layout(document_id, page.page_number))
            tables = sorted(page.find_tables(), key=lambda table: (table.bbox[1], table.bbox[0]))
            expected = []
            for table in before.get("table_contexts", []):
                bbox = tables[int(table["table_id"].split("GRID")[1]) - 1].bbox
                left, top, right, bottom = page.bbox
                if left <= bbox[0] < bbox[2] <= right and top <= bbox[1] < bbox[3] <= bottom:
                    expected.append(table)
            # 页面内原有表格的行列、文字、ID 和字符坐标必须逐项保持一致。
            assert after == expected
            geometry.append({"page_number": page.page_number,
                             "before": len(before.get("table_contexts", [])), "after": len(after)})

    prepared = semantics.prepare_source_semantics(context, {
        "requirement": "\n".join(assets.document_page_text(document_id, p["page_number"]) for p in manifest["pages"]),
        "evidence_source": source["source"], "evidence_catalog": source["evidence_catalog"],
    })
    projected_segments = 0
    for item in [*prepared["text_items"], *prepared["vision_items"]]:
        for page in item.get("pages", [item]):
            validate(page, SOURCE_SEMANTICS_INPUT_SCHEMA)
            for table in page.get("table_contexts", []):
                for row in table["rows"]:
                    for cell in row["cells"]:
                        for segment in cell["segments"]:
                            if "source_span" in segment:
                                span = segment["source_span"]
                                assert page["page_text"][span["start"]:span["end"]] == segment["text"]
                                projected_segments += 1
    assert projected_segments > 0

    checks = []
    for failed in saved["failed_candidates"]:
        old_item = saved["source_inputs"][failed["item_index"]]
        hydrated = semantics._hydrate_source_semantics_item(context, old_item)
        candidate = failed["repair_context"]["candidate_output"]
        for fact in candidate["authoritative_facts"]:
            anchor = fact["source_anchor"]
            record = {"item_index": failed["item_index"], "fact_id": fact["fact_id"]}
            if fact["governed_value_spans"]:
                try:
                    semantics._validate_model_table_value_spans(old_item, hydrated, {"authoritative_facts": [fact]})
                except ValueError as error:
                    message = str(error)
                    assert "当前输入坐标" in message and "允许范围" in message and "当前行单元格" in message
                    record["rejected_with_local_feedback"] = message
                else:
                    raise AssertionError("历史跨列坐标必须拒绝，不能静默删除或扩大来源")
            elif anchor.get("table_cell") is None:
                result = semantics.postprocess_source_semantics_item(context, {
                    "item_input": old_item, "item_output": {"authoritative_facts": [deepcopy(fact)]},
                })
                actual = result["authoritative_facts"][0]["source_anchor"]
                assert "table_cell" not in actual and "source_segments" not in actual
                record["non_table_anchor_accepted"] = actual
            checks.append(record)

    return {"status": "passed", "document_id": document_id, "source_sha256": manifest["source_sha256"],
            "online_database_writes": 0, "model_requests": 0, "geometry": geometry,
            "projected_segments_checked": projected_segments,
            "prepared_counts": {k: prepared[k] for k in ("text_item_count", "vision_item_count")},
            "historical_candidate_checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    report = verify(json.loads(args.source.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "historical_candidate_checks"}, ensure_ascii=False))
