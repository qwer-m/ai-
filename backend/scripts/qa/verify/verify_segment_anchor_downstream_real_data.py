"""以新模型的真实单元格候选复验多段来源在规划、生成、终审和修复中的流通。"""

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
from core.db.model_defs import AgentDefinition, AgentRun
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.result_cache import payload_hash
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform.test_generation_batching import _fact_source_order
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from modules.agent_platform.test_generation_facts import (
    fact_source_context_fields, fact_source_texts, index_effective_facts,
    source_anchor_spans, source_anchor_texts,
)
from modules.agent_platform.test_generation_planning import prepare_business_plan_batches
from modules.agent_platform.test_generation_review import _fact_coverage_key, _repair_facts, _review_facts
from modules.agent_platform.test_generation_schemas import AUTHORITATIVE_FACT_SCHEMA, REPAIR_AUTHORITATIVE_FACT_SCHEMA, REVIEW_FACT_SCHEMA
from modules.agent_platform.test_generation_semantics import _planning_scopes, postprocess_source_semantics_item
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition


def verify(source_path):
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    assert source["status"] == "passed"
    inputs_path = Path(source["input_path"])
    inputs_bytes = inputs_path.read_bytes()
    inputs = json.loads(inputs_bytes)
    fragments = {item["source_item_index"]: item["input"] for item in inputs["source_fragments"]}
    pages = inputs["source_artifacts"]["source_semantics_source_pages"]
    facts, fragments_checked = [], []
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, source["source_run_id"])
            run_hash = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id,
                run_id=run.id, node_key="segment-anchor-downstream", run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(inputs["source_artifacts"]))
            for record in source["fragments"]:
                assert record["status"] == "passed"
                item = fragments[record["source_item_index"]]
                assert action_state_hash(item) == record["input_sha256"]
                normalized = postprocess_source_semantics_item(context,
                    {"item_input": deepcopy(item), "item_output": deepcopy(record["candidate"])})
                assert normalized == record["normalized_output"]
                # 使用实际canonical输出再次验收，确认不会把绝对多段坐标重复平移。
                renormalized = postprocess_source_semantics_item(context,
                    {"item_input": deepcopy(item), "item_output": deepcopy(normalized)})
                assert renormalized == normalized
                facts.extend(normalized["authoritative_facts"])
                fragments_checked.append({"source_item_index": record["source_item_index"],
                    "candidate_sha256": action_state_hash(record["candidate"]),
                    "canonical_idempotent": True, "fact_count": len(normalized["authoritative_facts"])})
            table_checks, old_path_checks = [], []
            for index, fact in enumerate(facts):
                validate(fact, AUTHORITATIVE_FACT_SCHEMA)
                anchor = fact["source_anchor"]
                if "source_segments" not in anchor:
                    if "quote" in anchor:
                        assert source_anchor_texts(anchor) == [anchor["quote"]]
                    elif "image_region" in anchor:
                        assert source_anchor_texts(anchor) == source_anchor_spans(anchor) == []
                    old_path_checks.append({"fact_id": fact["fact_id"], "anchor_fields": sorted(anchor)})
                    continue
                assert "source_span" not in anchor and "quote" not in anchor
                segments = anchor["source_segments"]
                page = pages[f'{anchor["document_id"]}:{anchor["page_number"]}']
                for segment in segments:
                    span = segment["source_span"]
                    assert page["page_text"][span["start"]:span["end"]] == segment["text"]
                    assert segment["text"] in source_anchor_texts(anchor)
                    assert segment["text"] in fact_source_texts(fact)
                assert source_anchor_spans(anchor) == [segment["source_span"] for segment in segments]
                order = _fact_source_order(fact, fallback_index=index)
                assert order[3] == min(segment["source_span"]["start"] for segment in segments)
                cell = anchor["table_cell"]
                assert _fact_coverage_key(fact)[0][-1] == (cell["table_id"], cell["row_index"], cell["column_index"])
                # 只删字段检查身份敏感性，不把删字段后的对象当作事实或模型响应。
                missing_segments = deepcopy(fact)
                missing_segments["source_anchor"].pop("source_segments")
                assert payload_hash(fact) != payload_hash(missing_segments)
                assert action_state_hash(fact) != action_state_hash(missing_segments)
                table_checks.append({"fact_id": fact["fact_id"], "table_cell": cell,
                    "segment_count": len(segments), "first_source_position": order[3],
                    "exact_source_slices": True, "continuous_quote_not_invented": True,
                    "segments_in_cache_and_action_identity": True})
            assert table_checks
            effective = [fact for fact in facts if fact["status"] == "effective"]
            assert index_effective_facts(effective)
            projected_plan = prepare_business_plan_batches(context,
                {"planning_scopes": _planning_scopes(effective), "case_budget": run.input_payload["case_budget"]})
            plan_facts = {fact["fact_id"]: fact for item in projected_plan["items"]
                for scope in item["planning_scopes"] for fact in scope["facts"]}
            spec = next(spec for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == "test_case_generator")
            definition = builtin_definition(AgentDefinition, spec, user_id=run.user_id, project_id=run.project_id)
            generation, _ = _project_agent_map_input(definition=definition,
                raw_item={"authoritative_facts": deepcopy(effective)})
            generator_facts = {fact["fact_id"]: fact for fact in generation["authoritative_facts"]}
            reviewed, repaired = _review_facts(effective), _repair_facts(effective)
            for fact in reviewed:
                validate(fact, REVIEW_FACT_SCHEMA)
            for fact in repaired:
                validate(fact, REPAIR_AUTHORITATIVE_FACT_SCHEMA)
            reviewed_by_id = {fact["fact_id"]: fact for fact in reviewed}
            repaired_by_id = {fact["fact_id"]: fact for fact in repaired}
            for fact in effective:
                for view in (plan_facts, generator_facts, reviewed_by_id, repaired_by_id):
                    assert fact_source_context_fields(view[fact["fact_id"]]) == fact_source_context_fields(fact)
                if "source_segments" in fact["source_anchor"]:
                    compact_anchor = repaired_by_id[fact["fact_id"]]["source_anchor"]
                    for field in ("table_cell", "source_segments"):
                        assert compact_anchor[field] == fact["source_anchor"][field]
            assert not db.new and not db.dirty and not db.deleted
            assert run_hash == action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
        finally:
            db.rollback()
    assert source_path.read_bytes() == source_bytes and inputs_path.read_bytes() == inputs_bytes
    return {"status": "passed", "source_report": str(source_path.resolve()),
        "source_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_inputs_sha256": hashlib.sha256(inputs_bytes).hexdigest(),
        "fragments": fragments_checked, "table_facts": table_checks, "other_anchor_paths": old_path_checks,
        "effective_facts_checked": len(effective), "model_requests_sent": 0, "online_database_writes": 0,
        "scope": "新真实模型候选的canonical幂等及下游纯投影/来源排序/哈希验证。没有新生成或终审模型响应，没有正式Run、实际用例导出或缓存命中实验；不把结构及原文流通通过等同业务语义通过。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError(options.output)
    result = verify(options.source_report)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "table_facts": len(result["table_facts"]),
        "effective_facts_checked": result["effective_facts_checked"], "model_requests_sent": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
