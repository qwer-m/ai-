"""重验真实来源模型候选，检查表格上下文到规划、生成、终审和修复的完整流通。"""

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
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _project_agent_map_input
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS
from modules.agent_platform.test_generation_facts import fact_source_context_fields, fact_source_texts
from modules.agent_platform.test_generation_planning import prepare_business_plan_batches
from modules.agent_platform.test_generation_review import _repair_facts, _review_facts
from modules.agent_platform.test_generation_schemas import (
    AUTHORITATIVE_FACT_SCHEMA, REPAIR_AUTHORITATIVE_FACT_SCHEMA, REVIEW_FACT_SCHEMA,
)
from modules.agent_platform.test_generation_semantics import _planning_scopes, postprocess_source_semantics_item


def verify(report_path: Path) -> dict:
    report_bytes = report_path.read_bytes()
    source_report = json.loads(report_bytes)
    input_path = Path(source_report["input_path"])
    input_bytes = input_path.read_bytes()
    source_inputs = json.loads(input_bytes)
    fragments = {item["source_item_index"]: item["input"] for item in source_inputs["source_fragments"]}
    facts, checks = [], []
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, source_report["source_run_id"])
            if run is None:
                raise ValueError("真实来源 Run 不存在")
            original_run = action_state_hash([run.input_payload, run.run_context, run.status, run.output_payload])
            context = ToolExecutionContext(db=db, user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                node_key="source_text", run_input=deepcopy(run.input_payload),
                artifacts=deepcopy(source_inputs["source_artifacts"]))
            for record in source_report["fragments"]:
                assert record["status"] == "passed"
                item = fragments[record["source_item_index"]]
                assert action_state_hash(item) == record["input_sha256"]
                assert action_state_hash(record["normalized_output"]) == record["normalized_sha256"]
                normalized = postprocess_source_semantics_item(context, {
                    "item_input": deepcopy(item), "item_output": deepcopy(record["candidate"]),
                })
                prior = {fact["fact_id"]: fact for fact in record["normalized_output"]["authoritative_facts"]}
                for fact in normalized["authoritative_facts"]:
                    validate(fact, AUTHORITATIVE_FACT_SCHEMA)
                    stripped = {key: value for key, value in fact.items() if key != "source_context"}
                    assert stripped == prior[fact["fact_id"]], fact["fact_id"]
                    facts.append(fact)
                checks.append({"source_item_index": record["source_item_index"],
                               "raw_candidate_sha256": action_state_hash(record["candidate"]),
                               "normalized_fact_count": len(normalized["authoritative_facts"]),
                               "summary_status_and_anchor_unchanged": True})

            table_facts = [fact for fact in facts if "source_context" in fact]
            assert table_facts, "当前规范化链尚未保留任何真实表格上下文"
            for fact in table_facts:
                fields = fact_source_context_fields(fact)
                assert fields["source_context"] == fact["source_context"]
                assert fields["source_context"] is not fact["source_context"]
                assert any(text.strip() for text in fact_source_texts(fact)[1:])

            effective = [fact for fact in facts if fact["status"] == "effective"]
            scopes = _planning_scopes(effective)
            projected_scopes = prepare_business_plan_batches(context, {
                "planning_scopes": scopes, "case_budget": run.input_payload["case_budget"],
            })["items"]
            plan_facts = {fact["fact_id"]: fact for batch in projected_scopes
                          for scope in batch["planning_scopes"] for fact in scope["facts"]}

            # 使用当前真实内置定义运行生产输入投影，不构造生成候选、路由结论或用例。
            from core.db.model_defs import AgentDefinition
            from scripts.qa.verify.verify_review_agent_real_model import builtin_definition
            spec = next(spec for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == "test_case_generator")
            definition = builtin_definition(AgentDefinition, spec, user_id=run.user_id, project_id=run.project_id)
            model_input, projection = _project_agent_map_input(definition=definition,
                raw_item={"authoritative_facts": deepcopy(effective)})
            generator_facts = {fact["fact_id"]: fact for fact in model_input["authoritative_facts"]}
            reviewed = _review_facts(effective)
            repaired = _repair_facts(effective)
            for fact in reviewed:
                validate(fact, REVIEW_FACT_SCHEMA)
            for fact in repaired:
                validate(fact, REPAIR_AUTHORITATIVE_FACT_SCHEMA)
            reviewed_by_id = {fact["fact_id"]: fact for fact in reviewed}
            repaired_by_id = {fact["fact_id"]: fact for fact in repaired}
            preserved = []
            for fact in effective:
                for view in (plan_facts, generator_facts, reviewed_by_id, repaired_by_id):
                    assert fact_source_context_fields(view[fact["fact_id"]]) == fact_source_context_fields(fact)
                if "source_context" in fact:
                    preserved.append(fact["fact_id"])

            # 正反引用均直接取自本轮真实表格；反例只拼接不同单元格，不修改来源事实。
            quote_checks = []
            for fact in table_facts:
                cells = [cell["text"] for table in fact["source_context"]["table_contexts"]
                         for row in table["rows"] for cell in row["cells"] if cell["text"].strip()]
                texts = fact_source_texts(fact)
                source_only = next((cell for cell in cells if cell not in fact["assertion"]), None)
                stitched = next((left + right for left in cells for right in cells if left != right
                                 and not any(left + right in text for text in texts)), None)
                if source_only is None or stitched is None:
                    continue
                assert any(source_only in text for text in texts)
                assert not any(stitched in text for text in texts)
                quote_checks.append({"fact_id": fact["fact_id"], "context_quote": source_only,
                                     "not_in_summary": True, "cross_cell_quote_rejected": stitched})
            assert quote_checks
            assert not db.new and not db.dirty and not db.deleted
            assert original_run == action_state_hash([run.input_payload, run.run_context, run.status, run.output_payload])
        finally:
            db.rollback()
    assert report_path.read_bytes() == report_bytes and input_path.read_bytes() == input_bytes
    return {"status": "passed", "model_requests_sent": 0, "database_writes": 0,
            "source_report": str(report_path.resolve()), "source_report_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "source_input_sha256": hashlib.sha256(input_bytes).hexdigest(), "fragments": checks,
            "canonical_table_fact_count": len(table_facts), "preserved_effective_fact_ids": preserved,
            "generation_projection_version": projection["version"], "quote_checks": quote_checks,
            "validation_scope": "原始真实来源候选的确定性重验及现有下游事实投影；原断言不修正，未生成新路由、用例或模型结论。引用正反例验证现有条件引用使用的逐单元格文本边界，不冒充完整报告工具或E2E运行。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = verify(args.source_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "table_facts": result["canonical_table_fact_count"],
                      "flow_preserved_facts": len(result["preserved_effective_fact_ids"]),
                      "quote_checks": len(result["quote_checks"])}, ensure_ascii=False))
