"""只读回放真实 Run 来源链，核对分片表格上下文、预算及坐标恢复，不调用模型。"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import Draft202012Validator
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.test_generation_schemas import SOURCE_SEMANTICS_INPUT_SCHEMA
from modules.knowledge_base_components.document.document_asset_service import (
    document_table_layouts, load_document_manifest, document_page_text,
)
from scripts.qa.verify.verify_visual_source_real_data import _replay_source_tools


def _rejected(operation):
    try:
        operation()
    except ValueError as error:
        return str(error)
    raise AssertionError("伪造表格文字或坐标必须被拒绝")


def verify(run_id: int):
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, run_id)
            assert run is not None
            before = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            evidence, prepared, context = _replay_source_tools(db, run)
            document_id = int(evidence["source"]["document_id"])
            manifest = load_document_manifest(document_id)
            tables = document_table_layouts(document_id)
            raw_pages = {int(page["page_number"]): page for page in manifest["pages"]}
            covered = Counter()
            page_reports = []
            negative_checks = {}
            validator = Draft202012Validator(SOURCE_SEMANTICS_INPUT_SCHEMA)
            model_inputs = [*prepared["text_items"], *prepared["vision_items"]]
            for item in model_inputs:
                validator.validate(item)
                hydrated_item = semantics._hydrate_source_semantics_item(context, deepcopy(item))
                semantics._validate_source_semantics_input(context, hydrated_item)
                hydrated_pages = {page["page_number"]: page for page in hydrated_item.get("pages") or [hydrated_item]}
                for page in item.get("pages") or [item]:
                    number = int(page["page_number"])
                    hydrated = hydrated_pages[number]
                    selected_ids = {block["block_id"] for block in page["blocks"]}
                    expected = semantics._project_table_contexts(tables[number], selected_ids)
                    assert page.get("table_contexts", []) == expected
                    source_text = document_page_text(document_id, number)
                    assert hydrated["page_text"] == source_text
                    for block in page["blocks"]:
                        if block.get("type") == "image":
                            continue
                        span = block["source_span"]
                        assert page["page_text"][span["start"]:span["end"]] == block["text"]
                        covered[(number, block["block_id"])] += 1
                    allowed = {value for scope in page["source_scopes"] for value in scope["allowed_block_ids"]}
                    assert allowed <= selected_ids
                    for block_id in allowed:
                        anchor, _, scopes = semantics._validated_document_anchor(
                            {"document_id": document_id, "page_number": number, "block_id": block_id}, hydrated,
                        )
                        assert len(scopes) == 1
                        if "source_span" in anchor:
                            span = anchor["source_span"]
                            assert source_text[span["start"]:span["end"]] == anchor["quote"]
                    context_only = set()
                    for table in expected:
                        assert table["rows"][0]["row_index"] == 0
                        for row in table["rows"]:
                            for cell in row["cells"]:
                                for segment in cell["segments"]:
                                    span = segment["original_source_span"]
                                    assert source_text[span["start"]:span["end"]] == segment["text"]
                                    if segment["block_id"] not in selected_ids:
                                        context_only.add(segment["block_id"])
                    assert not context_only.intersection(allowed)
                    if context_only and "context_only_anchor" not in negative_checks:
                        negative_checks["context_only_anchor"] = _rejected(lambda: semantics._validated_document_anchor(
                            {"document_id": document_id, "page_number": number, "block_id": next(iter(context_only))}, hydrated,
                        ))
                    chars = semantics._page_model_text_chars(page)
                    if item["source_kind"] == "document_batch":
                        assert len(page["blocks"]) <= semantics._TEXT_PAGE_FRAGMENT_MAX_BLOCKS
                        assert chars <= semantics._TEXT_PAGE_BATCH_CHAR_LIMIT or len(page["blocks"]) == 1
                    page_reports.append({
                        "page_number": number, "block_ids": sorted(selected_ids),
                        "body_chars": len(page["page_text"]), "body_and_context_chars": chars,
                        "serialized_json_chars": semantics._serialized_json_chars(page),
                        "context_only_block_ids": sorted(context_only),
                        "table_contexts": expected,
                        "input_sha256": action_state_hash(page),
                    })
                    if expected and "tampered_original_span" not in negative_checks:
                        changed = deepcopy(hydrated)
                        segment = changed["table_contexts"][0]["rows"][0]["cells"][0]["segments"][0]
                        segment["original_source_span"]["start"] += 1
                        negative_checks["tampered_original_span"] = _rejected(lambda: semantics._validate_table_contexts(changed))
                        changed = deepcopy(hydrated)
                        segment = changed["table_contexts"][0]["rows"][0]["cells"][0]["segments"][0]
                        segment["text"] = segment["text"][:-1]
                        negative_checks["tampered_text"] = _rejected(lambda: semantics._validate_table_contexts(changed))
            expected_blocks = {
                (int(item["page_number"]), block_id)
                for item in evidence["evidence_catalog"]["items"]
                for block_id in item["block_ids"]
                if any(block["block_id"] == block_id and block.get("type") == "text_line"
                       for block in raw_pages[int(item["page_number"])] ["blocks"])
            }
            assert set(covered) == expected_blocks
            assert set(covered.values()) == {1}, "正文块必须只提取一次，表格首行只作为额外上下文复用"
            assert len(negative_checks) == 3
            assert not db.new and not db.dirty and not db.deleted
            assert before == action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            return {
                "status": "passed", "verified_at": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id, "document_id": document_id,
                "database_transaction": "READ ONLY", "database_writes": False, "model_requests_sent": 0,
                "source_sha256": manifest["source_sha256"],
                "source_table_count": sum(len(value) for value in tables.values()),
                "source_page_count": len(raw_pages), "text_items": len(prepared["text_items"]),
                "vision_items": len(prepared["vision_items"]), "validated_body_blocks": len(covered),
                "max_body_and_context_chars": max(page["body_and_context_chars"] for page in page_reports),
                "negative_checks": negative_checks,
                "model_inputs_sha256": action_state_hash(model_inputs),
                "pages": page_reports,
                "scope_limit": "验证真实来源分片、网格关系、引用边界和预算；没有模型调用，不宣称五条错误事实已完成语义重提取。",
            }
        finally:
            db.rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.run_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "pages"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
