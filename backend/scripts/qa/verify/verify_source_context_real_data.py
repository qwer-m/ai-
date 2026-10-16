"""用已保存的真实来源响应验证事实上下文派生；可隔离载入暂存模块，不改生产。"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun
from modules.agent_platform import test_generation_semantics as production
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.test_generation_schemas import SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA
from modules.knowledge_base_components.document.document_asset_service import load_document_manifest


def _rejected(operation):
    try:
        operation()
    except (ValueError, ValidationError) as error:
        return str(error)[:500]
    raise AssertionError("被篡改或由模型自行填入的来源上下文必须拒绝")


def verify(source_report: Path, staged_module: Path | None):
    production_path = Path(production.__file__)
    production_hash = hashlib.sha256(production_path.read_bytes()).hexdigest()
    module = production
    if staged_module is not None:
        spec = importlib.util.spec_from_file_location("modules.agent_platform._staged_source_context", staged_module)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    report = json.loads(source_report.read_text("utf-8"))
    saved = json.loads(Path(report["input_path"]).read_text("utf-8"))
    assert report["status"] == "passed"
    assert saved["source_input_sha256"] == report["source_input_sha256"]
    real_inputs = {item["source_item_index"]: item["input"] for item in saved["source_fragments"]}
    assert action_state_hash(list(real_inputs.values())) == report["source_input_sha256"]
    manifest = load_document_manifest(int(report["document_id"]))
    assert manifest["source_sha256"] == report["source_sha256"]
    negatives = {}
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, int(report["source_run_id"]))
            assert run is not None
            before = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            context = ToolExecutionContext(
                db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key="source_text", run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(saved["source_artifacts"]),
            )
            records, inputs = [], []
            actual_original_facts = []
            for fragment in report["fragments"]:
                item = real_inputs[fragment["source_item_index"]]
                assert action_state_hash(item) == fragment["input_sha256"]
                candidate = deepcopy(fragment["candidate"])
                assert all("source_context" not in fact for fact in candidate["authoritative_facts"])
                normalized = module.postprocess_source_semantics_item(context, {
                    "item_input": item, "item_output": candidate,
                })
                stripped = {"authoritative_facts": [
                    {key: value for key, value in fact.items() if key != "source_context"}
                    for fact in normalized["authoritative_facts"]
                ]}
                assert stripped == fragment["normalized_output"], "只允许补齐来源上下文，不得改写已有事实"
                actual_original_facts.extend(fragment["normalized_output"]["authoritative_facts"])
                inputs.append(item)
                records.append({"item_index": len(records), "output": normalized})
            arguments = {"text_inputs": inputs, "text_records": records, "vision_inputs": [], "vision_records": []}
            merged = module.merge_source_semantics(context, deepcopy(arguments))
            assert module.merge_source_semantics(context, deepcopy(arguments)) == merged
            planned = {fact["fact_id"]: fact for scope in merged["planning_scopes"] for fact in scope["facts"]}
            with_context, without_context = [], []
            for fact in merged["authoritative_facts"]:
                original = next(old for old in actual_original_facts if old["fact_id"] == fact["fact_id"])
                if "source_context" not in fact:
                    assert fact == original
                    without_context.append(fact["fact_id"])
                    continue
                with_context.append(fact["fact_id"])
                if fact["status"] == "effective":
                    assert planned[fact["fact_id"]]["source_context"] == fact["source_context"]
                for table in fact["source_context"]["table_contexts"]:
                    assert table["rows"][0]["row_index"] == 0
                    assert all(set(cell) == {"column_index", "text"} for row in table["rows"] for cell in row["cells"])
            assert with_context and without_context
            # 真实响应副本的负向修改只验证边界，不冒充新的模型事实。
            changed = deepcopy(arguments)
            target = next(fact for record in changed["text_records"] for fact in record["output"]["authoritative_facts"]
                          if "source_context" in fact)
            target["source_context"]["table_contexts"][0]["rows"][0]["cells"][0]["text"] += "篡改"
            negatives["canonical_text_tamper"] = _rejected(lambda: module.merge_source_semantics(context, changed))
            changed = deepcopy(arguments)
            target = next(fact for record in changed["text_records"] for fact in record["output"]["authoritative_facts"]
                          if "source_context" in fact)
            target["source_context"]["table_contexts"][0]["rows"][1]["row_index"] += 1
            negatives["canonical_row_tamper"] = _rejected(lambda: module.merge_source_semantics(context, changed))
            changed = deepcopy(arguments)
            raw_facts = [fact for record in changed["text_records"] for fact in record["output"]["authoritative_facts"]]
            target = next(fact for fact in raw_facts if "source_context" not in fact)
            target["source_context"] = deepcopy(next(fact["source_context"] for fact in raw_facts if "source_context" in fact))
            negatives["context_on_non_table_fact"] = _rejected(lambda: module.merge_source_semantics(context, changed))
            candidate = deepcopy(report["fragments"][0]["candidate"])
            candidate["authoritative_facts"][0]["source_context"] = deepcopy(target["source_context"])
            negatives["model_self_reported_context"] = _rejected(lambda: Draft202012Validator(SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA).validate(candidate))
            assert not db.new and not db.dirty and not db.deleted
            assert before == action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            assert production_hash == hashlib.sha256(production_path.read_bytes()).hexdigest()
            return {
                "status": "passed", "verified_at": datetime.now(timezone.utc).isoformat(),
                "mode": "staged_module_only" if staged_module else "current_production_module",
                "source_report": str(source_report.resolve()),
                "source_report_sha256": hashlib.sha256(source_report.read_bytes()).hexdigest(),
                "tested_module": str(Path(module.__file__).resolve()),
                "tested_module_sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
                "production_module_sha256": production_hash, "production_changed": False,
                "run_id": run.id, "document_id": report["document_id"],
                "model_requests_sent": 0, "database_writes": False, "database_transaction": "READ ONLY",
                "source_context_fact_count": len(with_context), "unchanged_non_table_fact_count": len(without_context),
                "assertion_status_anchor_scope_unchanged": True, "canonical_revalidation_idempotent": True,
                "planning_projection_context_preserved": True, "negative_checks": negatives,
                "normalized_payload_chars": len(json.dumps(merged["authoritative_facts"], ensure_ascii=False, separators=(",", ":"))),
                "original_payload_chars": len(json.dumps(actual_original_facts, ensure_ascii=False, separators=(",", ":"))),
                "source_context_fact_ids": with_context, "unchanged_fact_ids": without_context,
                "normalized_output": merged,
                "scope_limit": "仅验证真实响应在平台内的来源关系流通，不重抽模型、不声称原断言遗漏或冲突已修正。",
            }
        finally:
            db.rollback()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--staged-module", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.source_report, args.staged_module)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in (
        "normalized_output", "source_context_fact_ids", "unchanged_fact_ids",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
