from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ai.ai_client_impl import get_client_for_user
from core.db.database import SessionLocal
from core.db.models import KnowledgeDocument
from modules.test_generation_components.control.current_requirement_blueprint import (
    extract_current_requirement_blueprints,
)
from modules.test_generation_components.control.model_envelope_call import (
    invoke_model_envelope,
)


def _ordered_ids(values: object, *, object_key: str | None = None) -> list[str]:
    """按契约原顺序提取唯一 ID，不把对象中的其他内容带入验收摘要。"""

    if not isinstance(values, list):
        return []
    output: list[str] = []
    for item in values:
        if object_key:
            if not isinstance(item, dict):
                continue
            value = item.get(object_key)
        else:
            value = item
        if not isinstance(value, str):
            continue
        item_id = value.strip()
        if item_id and item_id not in output:
            output.append(item_id)
    return output


def main() -> None:
    db = SessionLocal()
    try:
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == 237).first()
        if doc is None:
            raise RuntimeError("knowledge_documents.id=237 不存在")
        client = get_client_for_user(1, db)
        if os.getenv("CODEX_REAL_MODEL_CONNECTIVITY_PROBE") == "1":
            started = time.perf_counter()
            result = invoke_model_envelope(
                client=client,
                envelope_id="real-connectivity-probe",
                user_input='Return exactly {"ok":true}.',
                system_prompt="Return minified JSON only.",
                db=db,
                max_tokens=128,
                task_type="generation",
                request_timeout_seconds=60,
                max_transport_replays=0,
            )
            print(
                "REAL_MODEL_PROBE "
                + json.dumps(
                    {
                        "elapsed_sec": round(time.perf_counter() - started, 2),
                        "model": client.select_model("", "generation"),
                        "status": result.status,
                        "raw_preview": result.raw_text[:120],
                        "diagnostic": result.to_diagnostic(),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                flush=True,
            )
            return
        started = time.perf_counter()
        blueprints, diagnostics = extract_current_requirement_blueprints(
            client=client,
            requirement_text=doc.content or "",
            db=db,
            project_id=int(doc.project_id or 0),
            user_id=1,
        )
        contract = diagnostics.get("requirement_semantic_contract") or {}
        graph = contract.get("semantic_graph") or {}
        architecture = contract.get("functional_architecture") or {}
        facts = [
            item
            for item in (contract.get("evidence_facts") or [])
            if isinstance(item, dict)
        ]
        nodes = [
            item for item in graph.get("nodes", []) if isinstance(item, dict)
        ]
        edges = [
            item for item in graph.get("edges", []) if isinstance(item, dict)
        ]
        names = {
            str(item.get("node_id") or ""): str(item.get("name") or "")
            for item in nodes
        }
        summary = {
            "elapsed_sec": round(time.perf_counter() - started, 2),
            "document": {
                "id": doc.id,
                "filename": doc.filename,
                "content_len": len(doc.content or ""),
            },
            "model": client.select_model("", "generation"),
            "success": diagnostics.get("semantic_compile_success"),
            "status": diagnostics.get("current_requirement_blueprint_status"),
            "failed_stage": diagnostics.get("semantic_pipeline_failed_stage"),
            "workflow_status": diagnostics.get("workflow_declaration_status"),
            "fact_compile": {
                "status": diagnostics.get("fact_ledger_compile_status"),
                "global_status": diagnostics.get(
                    "fact_ledger_compile_global_status"
                ),
                "fact_count": diagnostics.get("fact_ledger_fact_count"),
                "chunk_count": diagnostics.get(
                    "fact_ledger_compile_chunk_count"
                ),
                "completed_chunk_count": diagnostics.get(
                    "fact_ledger_compile_completed_chunk_count"
                ),
                "collapsed_duplicate_fact_count": diagnostics.get(
                    "fact_ledger_compile_collapsed_duplicate_fact_count"
                ),
                "chunk_summaries": diagnostics.get(
                    "fact_ledger_compile_chunk_summaries"
                )
                or [],
                "candidate_attempts": diagnostics.get(
                    "fact_ledger_compile_candidate_attempt_count"
                ),
                "physical_calls": diagnostics.get(
                    "fact_ledger_compile_physical_call_count"
                ),
                "transport_failures": diagnostics.get(
                    "fact_ledger_compile_transport_failure_count"
                ),
                "transport_retries": diagnostics.get(
                    "fact_ledger_compile_transport_retry_count"
                ),
                "attempts": diagnostics.get("fact_ledger_compile_attempts")
                or [],
            },
            "scope_compile": {
                "status": diagnostics.get("scope_ledger_compile_status"),
                "mode": diagnostics.get("scope_ledger_compile_mode"),
                "global_status": diagnostics.get(
                    "scope_ledger_compile_global_status"
                ),
                "global_error_codes": diagnostics.get(
                    "scope_ledger_compile_global_error_codes"
                )
                or [],
                "candidate_attempts": diagnostics.get(
                    "scope_ledger_compile_candidate_attempt_count"
                ),
                "physical_calls": diagnostics.get(
                    "scope_ledger_compile_physical_call_count"
                ),
                "transport_failures": diagnostics.get(
                    "scope_ledger_compile_transport_failure_count"
                ),
                "transport_retries": diagnostics.get(
                    "scope_ledger_compile_transport_retry_count"
                ),
                "boundary_manifest_status": diagnostics.get(
                    "scope_ledger_boundary_manifest_status"
                ),
                "boundary_selection_status": diagnostics.get(
                    "scope_ledger_boundary_selection_status"
                ),
                "boundary_selection_fingerprint": diagnostics.get(
                    "scope_ledger_boundary_selection_fingerprint"
                ),
                "boundary_selection_count": diagnostics.get(
                    "scope_ledger_boundary_selection_count"
                ),
                "membership_assignment_status": diagnostics.get(
                    "scope_ledger_membership_assignment_status"
                ),
                "membership_assignment_fingerprint": diagnostics.get(
                    "scope_ledger_membership_assignment_fingerprint"
                ),
                "membership_assignment_count": diagnostics.get(
                    "scope_ledger_membership_assignment_count"
                ),
                "membership_none_count": diagnostics.get(
                    "scope_ledger_membership_none_count"
                ),
                "boundary_count": diagnostics.get(
                    "scope_ledger_boundary_count"
                ),
                "binding_shard_count": diagnostics.get(
                    "scope_ledger_binding_shard_count"
                ),
                "completed_binding_shards": diagnostics.get(
                    "scope_ledger_binding_completed_shard_count"
                ),
                "failed_binding_shard_index": diagnostics.get(
                    "scope_ledger_binding_failed_shard_index"
                ),
                "binding_count": diagnostics.get(
                    "scope_ledger_fact_binding_count"
                ),
                "source_topology": diagnostics.get(
                    "scope_ledger_source_topology"
                )
                or {},
                "binding_role_counts": diagnostics.get(
                    "scope_ledger_binding_role_counts"
                )
                or {},
                "membership_relation_count": diagnostics.get(
                    "scope_ledger_membership_relation_count"
                ),
                "explicit_fact_membership_count": diagnostics.get(
                    "scope_ledger_explicit_fact_membership_count"
                ),
                "boundary_topology_summary": diagnostics.get(
                    "scope_ledger_boundary_topology_summary"
                )
                or [],
                "binding_shard_summaries": diagnostics.get(
                    "scope_ledger_binding_shard_summaries"
                )
                or [],
                "attempts": diagnostics.get("scope_ledger_compile_attempts")
                or [],
            },
            "attempt_count": diagnostics.get("semantic_compile_attempt_count"),
            "independent_recompile_used": diagnostics.get(
                "semantic_compile_independent_recompile_used"
            ),
            "independent_recompile_trigger_codes": diagnostics.get(
                "semantic_compile_independent_recompile_trigger_codes"
            ),
            "stop_reason": diagnostics.get("semantic_compile_stop_reason"),
            "attempts": [
                {
                    "attempt": item.get("semantic_attempt", item.get("attempt")),
                    "mode": item.get("compilation_mode"),
                    "status": item.get("status"),
                    "raw_chars": item.get("raw_chars"),
                    "structural_codes": item.get(
                        "structural_recompile_error_codes"
                    )
                    or [],
                    "topology_errors": item.get("workflow_topology_error_codes")
                    or [],
                    "scheduled": item.get(
                        "independent_recompile_scheduled", False
                    ),
                }
                for item in diagnostics.get("semantic_compile_attempts", [])
                if isinstance(item, dict)
            ],
            "graph": {
                "facts": len(facts),
                "fact_summaries": [
                    {
                        "id": item.get("fact_id"),
                        "kind": item.get("fact_kind"),
                        "statement": item.get("statement"),
                        "priority": item.get("priority"),
                        "testability": item.get("testability"),
                        "evidence_count": (
                            len(item.get("evidence"))
                            if isinstance(item.get("evidence"), list)
                            else 0
                        ),
                    }
                    for item in facts
                ],
                "nodes": len(nodes),
                "node_summaries": {
                    kind: [
                        {
                            "id": item.get("node_id"),
                            "name": item.get("name"),
                            "fact_ids": _ordered_ids(item.get("fact_ids")),
                        }
                        for item in nodes
                        if item.get("kind") == kind
                    ]
                    for kind in ("scope", "capability", "constraint")
                },
                "edges": len(edges),
                "edge_summaries": [
                    {
                        "id": item.get("edge_id"),
                        "type": item.get("type"),
                        "source": item.get("source_node_id"),
                        "target": item.get("target_node_id"),
                        "fact_ids": _ordered_ids(item.get("fact_ids")),
                    }
                    for item in edges
                ],
                "scopes": [
                    {"id": item.get("node_id"), "name": item.get("name")}
                    for item in nodes
                    if item.get("kind") == "scope"
                ],
                "capability_count": sum(
                    item.get("kind") == "capability" for item in nodes
                ),
                "constraint_count": sum(
                    item.get("kind") == "constraint" for item in nodes
                ),
                "owns": [
                    {
                        "source": names.get(
                            str(item.get("source_node_id") or ""),
                            item.get("source_node_id"),
                        ),
                        "target": names.get(
                            str(item.get("target_node_id") or ""),
                            item.get("target_node_id"),
                        ),
                    }
                    for item in edges
                    if item.get("type") == "owns"
                ],
                "interactions": [
                    {
                        "source": names.get(
                            str(item.get("source_node_id") or ""),
                            item.get("source_node_id"),
                        ),
                        "target": names.get(
                            str(item.get("target_node_id") or ""),
                            item.get("target_node_id"),
                        ),
                        "trigger": item.get("trigger"),
                        "result_state": item.get("result_state"),
                    }
                    for item in edges
                    if item.get("type") == "interacts_with"
                ],
                "primary_flow": graph.get("primary_flow") or {},
            },
            "functional_modules": [
                item.get("module_name")
                for item in architecture.get("functional_modules", [])
                if isinstance(item, dict)
            ],
            "module_interaction_count": len(
                architecture.get("module_interactions") or []
            ),
            "workflow_count": len(blueprints),
            "workflow_step_counts": [
                len(item.get("steps") or []) for item in blueprints
            ],
            "workflow_summaries": [
                {
                    "id": workflow.get("workflow_id"),
                    "name": workflow.get("name"),
                    "steps": [
                        {
                            "id": step.get("id"),
                            "label": step.get("label"),
                            "action": step.get("action"),
                            "stage_kind": step.get("stage_kind"),
                            "scope_ids": _ordered_ids(
                                step.get("scope_candidates"),
                                object_key="scope_id",
                            ),
                            "relation_ids": _ordered_ids(
                                step.get("relation_ids")
                            ),
                            "fact_ids": _ordered_ids(step.get("fact_ids")),
                            "required": step.get("required"),
                            "terminal": step.get("terminal"),
                        }
                        for step in (workflow.get("steps") or [])
                        if isinstance(step, dict)
                    ],
                }
                for workflow in blueprints
                if isinstance(workflow, dict)
            ],
            "semantic_errors": diagnostics.get("semantic_graph_rejections") or [],
        }
        print(
            "REAL_SEMANTIC_RESULT "
            + json.dumps(summary, ensure_ascii=False, default=str),
            flush=True,
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
