"""重验 Run41 两条真实事实与 Doc278 网格，验证完整同行上下文及精确锚点边界。"""

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
from core.db.model_defs import AgentDefinition, AgentNodeRun, AgentRun
from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from modules.agent_platform.test_generation_facts import fact_source_texts
from modules.agent_platform.test_generation_planning import prepare_business_plan_batches
from modules.agent_platform.test_generation_review import _repair_facts, _review_facts
from modules.agent_platform.test_generation_schemas import AUTHORITATIVE_FACT_SCHEMA, FACT_SOURCE_CONTEXT_SCHEMA
from modules.knowledge_base_components.document.document_asset_service import document_page_text, load_document_manifest
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition
from scripts.qa.verify.verify_visual_source_real_data import _replay_source_tools


def measured(value):
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return {"characters": len(serialized), "utf8_bytes": len(serialized.encode("utf-8"))}


def rejected(operation):
    try:
        operation()
    except ValueError as error:
        return str(error)
    raise AssertionError("越过单元格来源边界必须被拒绝")


def checked_context(page, anchor):
    table, row, cell = semantics._table_cell_for_reference(page, anchor["table_cell"])
    context = semantics._source_context_for_anchor(page, anchor)
    validate(context, FACT_SOURCE_CONTEXT_SCHEMA)
    columns = {value["column_index"] for value in row["cells"]}
    expected_rows = [{"row_index": value["row_index"], "cells": [
        {"column_index": part["column_index"], "text": "\n".join(segment["text"] for segment in part["segments"])}
        for part in value["cells"] if part["column_index"] in columns
    ]} for value in table["rows"] if value["row_index"] in {0, row["row_index"]}]
    assert context == {"table_contexts": [{"table_id": table["table_id"], "rows": expected_rows}]}
    original_text = document_page_text(page["document_id"], page["page_number"])
    for source_row in table["rows"]:
        if source_row["row_index"] not in {0, row["row_index"]}:
            continue
        for source_cell in source_row["cells"]:
            if source_cell["column_index"] not in columns:
                continue
            for segment in source_cell["segments"]:
                span = segment["original_source_span"]
                assert original_text[span["start"]:span["end"]] == segment["text"]
    assert anchor["source_segments"] == [{"block_id": segment["block_id"], "text": segment["text"],
        "source_span": segment["original_source_span"]} for segment in cell["segments"]]
    assert "quote" not in anchor and "source_span" not in anchor
    return context, table, row, cell


def projected_facts(context, facts):
    plans = prepare_business_plan_batches(context, {
        "planning_scopes": semantics._planning_scopes(facts), "case_budget": context.run_input["case_budget"],
    })["items"]
    plan = {fact["fact_id"]: fact for batch in plans for scope in batch["planning_scopes"] for fact in scope["facts"]}
    views = {"planning": plan, "review": {fact["fact_id"]: fact for fact in _review_facts(facts)},
             "repair": {fact["fact_id"]: fact for fact in _repair_facts(facts)}}
    diagnostics = {}
    for key, field in (("test_case_generator", "authoritative_facts"), ("test_planning_route_gap_reviewer", "candidate_facts")):
        spec = next(value for value in BUILTIN_AGENT_SPECS if value["agent_key"] == key)
        definition = builtin_definition(AgentDefinition, spec, user_id=context.user_id, project_id=context.project_id)
        projected, diagnostic = _project_agent_map_input(definition=definition, raw_item={field: deepcopy(facts)})
        views[key] = {fact["fact_id"]: fact for fact in projected[field]}
        diagnostics[key] = {"projection": diagnostic, "model_facts_size": measured(projected[field])}
    for fact in facts:
        for view in views.values():
            assert view[fact["fact_id"]]["source_context"] == fact["source_context"]
        assert views["repair"][fact["fact_id"]]["source_anchor"]["source_segments"] == fact["source_anchor"]["source_segments"]
    return diagnostics


def verify():
    examples, replayed_facts, originals, generalization = [], [], [], []
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        run = db.get(AgentRun, 41)
        run_hash = action_state_hash([run.run_context, run.output_payload, run.status])
        prepared_node = db.get(AgentNodeRun, 996)
        source_node = db.get(AgentNodeRun, 999)
        source_hash = action_state_hash(source_node.output_payload)
        context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
            run_id=run.id, node_key="table-row-context-replay", run_input=deepcopy(run.input_payload),
            artifacts=deepcopy(run.run_context["artifacts"]))
        original_facts = source_node.output_payload["authoritative_facts"]
        derived_facts = deepcopy(original_facts)
        derived_count = 0
        for original, fact in zip(original_facts, derived_facts):
            anchor = fact["source_anchor"]
            if anchor.get("table_cell") is not None:
                page = context.artifacts["source_semantics_source_pages"][f'{anchor["document_id"]}:{anchor["page_number"]}']
                fact["source_context"], _, _, _ = checked_context(page, anchor)
                derived_count += 1
            assert {k: v for k, v in fact.items() if k != "source_context"} == {
                k: v for k, v in original.items() if k != "source_context"}
            validate(fact, AUTHORITATIVE_FACT_SCHEMA)
        for node_id, item_index, field, fact_id in (
            (997, 0, "text_items", "DOC280-P0005-S0-E396-F0001"),
            (998, 7, "vision_items", "DOC280-P0010-11"),
        ):
            node = db.get(AgentNodeRun, node_id)
            item = prepared_node.output_payload[field][item_index]
            sdk_item = next(value for value in node.sdk_state["items"] if value["item_index"] == item_index)
            receipt = sdk_item["tool_calls"][-1]
            raw = deepcopy(receipt["arguments"])
            candidate_hash = action_state_hash(raw)
            normalized = semantics.postprocess_source_semantics_item(context, {
                "item_input": deepcopy(item), "item_output": raw,
            })
            assert candidate_hash == action_state_hash(receipt["arguments"])
            old_by_id = {fact["fact_id"]: fact for fact in next(value for value in node.output_payload["items"]
                         if value["item_index"] == item_index)["output"]["authoritative_facts"]}
            for fact in normalized["authoritative_facts"]:
                assert {k: v for k, v in fact.items() if k != "source_context"} == {
                    k: v for k, v in old_by_id[fact["fact_id"]].items() if k != "source_context"}
            assert semantics.postprocess_source_semantics_item(context, {
                "item_input": deepcopy(item), "item_output": deepcopy(normalized),
            }) == normalized
            fact = next(value for value in normalized["authoritative_facts"] if value["fact_id"] == fact_id)
            prior = next(value for value in source_node.output_payload["authoritative_facts"] if value["fact_id"] == fact_id)
            validate(fact, AUTHORITATIVE_FACT_SCHEMA)
            page_number = fact["source_anchor"]["page_number"]
            hydrated = semantics._hydrate_source_semantics_item(context, deepcopy(item))
            page = next(value for value in hydrated.get("pages", [hydrated]) if value["page_number"] == page_number)
            new_context, table, row, cell = checked_context(page, fact["source_anchor"])
            assert new_context == fact["source_context"]
            anchor, _, scopes = semantics._validated_document_anchor(fact["source_anchor"], page)
            assert anchor == prior["source_anchor"] and scopes == {prior["scope_id"]}
            source_texts = fact_source_texts(fact)
            cells = [value["text"] for table_context in new_context["table_contexts"]
                     for context_row in table_context["rows"] for value in context_row["cells"]]
            assert all(any(value in source for source in source_texts) for value in cells)
            stitched = next(left + right for left in cells for right in cells if left != right
                            and not any(left + right in source for source in source_texts))
            assert not any(stitched in source for source in source_texts)
            neighbor = next(segment for value in row["cells"] if value["column_index"] != cell["column_index"]
                            for segment in value["segments"])
            invalid = deepcopy(fact)
            # 只构造越界拒绝候选；exact 的治理坐标会按既有合同清空，不能用于测量越界校验。
            invalid["value_policy"] = "runtime_configured"
            invalid["governed_value_spans"] = [neighbor["original_source_span"]]
            boundary = rejected(lambda: semantics._materialize_governed_values(
                fact=invalid, prepared=page, normalized_anchor=anchor, fact_id=fact_id,
                value_policy=invalid["value_policy"], assertion=fact["assertion"]))
            altered = deepcopy(fact)
            altered["source_context"]["table_contexts"][0]["rows"][-1]["cells"][-1]["text"] += "篡改"
            tamper = deepcopy(normalized)
            tamper["authoritative_facts"] = [altered if value["fact_id"] == fact_id else value
                                             for value in tamper["authoritative_facts"]]
            tamper_error = rejected(lambda: semantics.postprocess_source_semantics_item(context, {
                "item_input": deepcopy(item), "item_output": tamper,
            }))
            examples.append({"fact_id": fact_id, "source_node_id": node_id, "source_item_index": item_index,
                "receipt_call_id": receipt["tool_call_id"], "raw_candidate_sha256": candidate_hash,
                "old_fact": prior, "current_fact": fact,
                "source_header_and_row": {"table_id": table["table_id"], "rows": [value for value in table["rows"]
                    if value["row_index"] in (0, row["row_index"])]},
                "raw_candidate_fact": next(value for value in raw["authoritative_facts"] if value["assertion"] == fact["assertion"]),
                "old_context_size": measured(prior["source_context"]), "current_context_size": measured(new_context),
                "exact_anchor_assertion_status_scope_unchanged": True, "canonical_idempotent": True,
                "each_cell_text_available_separately": True, "cross_cell_quote_not_available": stitched,
                "neighbor_governed_span_rejected": boundary, "context_tamper_rejected": tamper_error,
                "full_real_item_normalized_fact_count": len(normalized["authoritative_facts"])})
            replayed_facts.append(fact)
            originals.append(prior)
        projections = projected_facts(context, replayed_facts)
        old_projections = projected_facts(context, originals)
        other_run = db.get(AgentRun, 38)
        other_hash = action_state_hash([other_run.run_context, other_run.output_payload, other_run.status])
        evidence, prepared, other_context = _replay_source_tools(db, other_run)
        assert evidence["source"]["document_id"] == 278
        seen = set()
        for item in [*prepared["text_items"], *prepared["vision_items"]]:
            hydrated = semantics._hydrate_source_semantics_item(other_context, deepcopy(item))
            for page in hydrated.get("pages", [hydrated]):
                allowed = {value for scope in page["source_scopes"] for value in scope["allowed_block_ids"]}
                for table in page.get("table_contexts", []):
                    for row in table["rows"]:
                        for cell in row["cells"]:
                            visible = [segment for segment in cell["segments"] if segment["block_id"] in allowed]
                            identity = (page["page_number"], table["table_id"], row["row_index"], cell["column_index"])
                            if not visible or identity in seen:
                                continue
                            seen.add(identity)
                            raw_anchor = {"document_id": 278, "page_number": page["page_number"],
                                "block_id": visible[0]["block_id"], "table_cell": {"table_id": table["table_id"],
                                "row_index": row["row_index"], "column_index": cell["column_index"]}}
                            anchor, _, _ = semantics._validated_document_anchor(raw_anchor, page)
                            compact, _, _, _ = checked_context(page, anchor)
                            generalization.append({"page_number": page["page_number"], "table_cell": anchor["table_cell"],
                                "context_cell_count": sum(len(value["cells"]) for value in compact["table_contexts"][0]["rows"]),
                                "original_segments": len(anchor["source_segments"]), "complete_row_and_headers": True})
        assert generalization
        assert not db.new and not db.dirty and not db.deleted
        db.refresh(run)
        db.refresh(source_node)
        db.refresh(other_run)
        assert run_hash == action_state_hash([run.run_context, run.output_payload, run.status])
        assert source_hash == action_state_hash(source_node.output_payload)
        assert other_hash == action_state_hash([other_run.run_context, other_run.output_payload, other_run.status])
        db.rollback()
    root = Path(__file__).resolve().parents[3]
    return {"status": "passed", "source_run_id": 41, "source_node_id": 999,
        "source_node_output_sha256": source_hash, "examples": examples,
        "authoritative_facts": derived_facts,
        "offline_derivation": {"only_source_context_updated": True, "canonical_anchor_assertion_status_ids_unchanged": True,
            "table_fact_count": derived_count, "original_facts_sha256": action_state_hash(original_facts),
            "current_facts_sha256": action_state_hash(derived_facts),
            "original_facts_size": measured(original_facts), "current_facts_size": measured(derived_facts),
            "online_checkpoint_updated": False, "not_new_model_facts": True},
        "new_projections": projections, "old_projections": old_projections,
        "doc278_actual_cell_checks": generalization,
        "doc278_table_count": len({(value["page_number"], value["table_cell"]["table_id"]) for value in generalization}),
        "source_sha256": {str(doc): load_document_manifest(doc)["source_sha256"] for doc in (280, 278)},
        "semantics_sha256": hashlib.sha256((root / "modules/agent_platform/test_generation_semantics.py").read_bytes()).hexdigest(),
        "model_requests_sent": 0, "online_database_writes": 0,
        "limits": "仅重验两个真实来源项中的原始模型候选，并核对两条指定事实；Doc278仅真实网格合同，不构造业务事实。验证结构、精确原文、纯投影传播与逐格文本边界，不代表新模型支持判断、完整生成或正式Run通过。旧canonical上下文不迁移；重验使用真实raw候选。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    result = verify()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "examples": len(result["examples"]),
        "doc278_cells": len(result["doc278_actual_cell_checks"]), "doc278_tables": result["doc278_table_count"],
        "model_requests_sent": 0, "online_database_writes": 0}, ensure_ascii=False))
