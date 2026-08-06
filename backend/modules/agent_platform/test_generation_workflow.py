from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, TYPE_CHECKING

from core.db.model_defs import KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    load_document_manifest,
)
from .test_generation_batching import (
    MAX_EVIDENCE_ACCOUNTING_BATCHES,
    MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH,
    MAX_EVIDENCE_ACCOUNTING_NEIGHBOR_CHARS,
    build_planning_evidence_catalog,
    merge_evidence_accounting_batches,
    merge_grounded_generation_batches,
    merge_plan_evidence_routing,
    prepare_evidence_accounting_batches,
    prepare_execution_chain_context,
    prepare_test_case_batches,
    validate_execution_chain,
)
from .test_generation_continuity import (
    MAX_CONTINUITY_AUDIT_ITEMS,
    merge_continuity_audit,
    prepare_continuity_audit_items,
)
from .test_generation_semantics import (
    merge_authority_reconciliation,
    merge_source_semantics,
    prepare_authority_reconciliation,
    prepare_source_semantics,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext, ToolRegistry


CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "module": {"type": "string", "minLength": 1},
        "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
        "preconditions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "minLength": 1},
                    "expected": {"type": "string", "minLength": 1},
                },
                "required": ["action", "expected"],
                "additionalProperties": False,
            },
        },
        "tags": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": [
        "case_id",
        "title",
        "module",
        "priority",
        "preconditions",
        "steps",
        "tags",
    ],
    "additionalProperties": False,
}


TEXT_OR_TEXTS_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "minLength": 1},
        {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    ]
}


PLANNER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement_summary": {"type": "string", "minLength": 1},
        "business_modules": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "actors": TEXT_OR_TEXTS_SCHEMA,
                    "lifecycle": {
                        "type": ["string", "null"],
                    },
                },
                "required": ["name", "objective", "actors", "lifecycle"],
                "additionalProperties": False,
            },
        },
        "coverage_focus": TEXT_OR_TEXTS_SCHEMA,
        "risks": TEXT_OR_TEXTS_SCHEMA,
    },
    "required": [
        "requirement_summary",
        "business_modules",
        "coverage_focus",
        "risks",
    ],
    "additionalProperties": False,
}


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement_summary": {"type": "string", "minLength": 1},
        "business_modules": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "actors": TEXT_OR_TEXTS_SCHEMA,
                    "lifecycle": {
                        "type": ["string", "null"],
                    },
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "pattern": "^EV-[0-9]{4,}$",
                        },
                    },
                },
                "required": [
                    "name",
                    "objective",
                    "actors",
                    "lifecycle",
                    "evidence_ids",
                ],
                "additionalProperties": False,
            },
        },
        "coverage_focus": TEXT_OR_TEXTS_SCHEMA,
        "risks": TEXT_OR_TEXTS_SCHEMA,
    },
    "required": [
        "requirement_summary",
        "business_modules",
        "coverage_focus",
        "risks",
    ],
    "additionalProperties": False,
}


EVIDENCE_ACCOUNTING_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence_id": {
            "type": "string",
            "pattern": "^EV-[0-9]{4,}$",
        },
        "module_indexes": {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "integer",
                "minimum": 0,
            },
        },
        "disposition": {
            "type": "string",
            "enum": ["assigned", "context_only", "plan_gap"],
        },
        "reason": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
        },
    },
    "required": [
        "evidence_id",
        "module_indexes",
        "disposition",
        "reason",
    ],
    "additionalProperties": False,
}


EVIDENCE_ACCOUNTING_BATCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence_accounting": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH,
            "items": EVIDENCE_ACCOUNTING_ITEM_SCHEMA,
        },
    },
    "required": ["evidence_accounting"],
    "additionalProperties": False,
}


REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence_accounting": {
            "type": "array",
            "minItems": 1,
            "items": EVIDENCE_ACCOUNTING_ITEM_SCHEMA,
        },
    },
    "required": ["evidence_accounting"],
    "additionalProperties": False,
}




FACT_ID_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "uniqueItems": True,
    "items": {"type": "string", "minLength": 1},
}


CASE_FACT_BINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string", "minLength": 1},
        "precondition_bindings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "precondition_index": {"type": "integer", "minimum": 0},
                    "fact_ids": FACT_ID_LIST_SCHEMA,
                },
                "required": ["precondition_index", "fact_ids"],
                "additionalProperties": False,
            },
        },
        "step_bindings": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "step_index": {"type": "integer", "minimum": 0},
                    "action_fact_ids": FACT_ID_LIST_SCHEMA,
                    "expected_fact_ids": FACT_ID_LIST_SCHEMA,
                },
                "required": ["step_index", "action_fact_ids", "expected_fact_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["case_id", "precondition_bindings", "step_bindings"],
    "additionalProperties": False,
}


GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_cases": {
            "type": "array",
            "items": CASE_SCHEMA,
        },
        "case_fact_bindings": {
            "type": "array",
            "items": CASE_FACT_BINDING_SCHEMA,
        },
    },
    "required": ["test_cases", "case_fact_bindings"],
    "additionalProperties": False,
}




EXECUTION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "main_chain_suite_id": {"type": "string"},
        "suites": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "suite_id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "goal": {"type": "string", "minLength": 1},
                    "suite_type": {
                        "type": "string",
                        "enum": ["chain", "collection"],
                    },
                    "case_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "transitions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "case_id": {"type": "string", "minLength": 1},
                                "from_state": {"type": "string", "minLength": 1},
                                "to_state": {"type": "string", "minLength": 1},
                            },
                            "required": ["case_id", "from_state", "to_state"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "suite_id",
                    "name",
                    "goal",
                    "suite_type",
                    "case_ids",
                    "transitions",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["main_chain_suite_id", "suites"],
    "additionalProperties": False,
}


EXECUTION_CHAIN_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "goal": {"type": "string"},
        "case_ids": {
            "type": "array",
            "maxItems": 12,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["name", "goal", "case_ids"],
    "additionalProperties": False,
}


EVIDENCE_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["inline", "knowledge_document"]},
        "document_id": {"type": ["integer", "null"], "minimum": 1},
        "filename": {"type": "string"},
        "doc_type": {"type": "string"},
        "content_hash": {"type": "string", "minLength": 64, "maxLength": 64},
        "asset_available": {"type": "boolean"},
        "page_count": {"type": "integer", "minimum": 0},
    },
    "required": [
        "kind",
        "document_id",
        "filename",
        "doc_type",
        "content_hash",
        "asset_available",
        "page_count",
    ],
    "additionalProperties": False,
}


ORDERED_MARKER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["arabic", "latin_upper", "latin_lower"],
        },
        "ordinal": {"type": "integer", "minimum": 1},
        "raw": {"type": "string", "minLength": 1},
        "suffix": {"type": "string", "minLength": 1},
        "block_id": {"type": "string", "minLength": 1},
        "line_text": {"type": "string", "minLength": 1},
    },
    "required": ["kind", "ordinal", "raw", "suffix", "block_id", "line_text"],
    "additionalProperties": False,
}


PLANNING_EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence_id": {"type": "string", "pattern": "^EV-[0-9]{4,}$"},
        "document_id": {"type": ["integer", "null"], "minimum": 1},
        "chunk_index": {"type": "integer", "minimum": 0},
        "biz_key": {"type": "string"},
        "text": {"type": "string", "minLength": 1},
        "page_number": {"type": ["integer", "null"], "minimum": 1},
        "block_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "source_offset_start": {"type": "integer", "minimum": 0},
        "source_offset_end": {"type": "integer", "minimum": 0},
        "asset_source_sha256": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
        },
        "continuation": {
            "type": ["object", "null"],
            "properties": {
                "confidence": {"type": "string", "const": "high"},
                "previous_evidence_id": {
                    "type": "string",
                    "pattern": "^EV-[0-9]{4,}$",
                },
                "left_tail_span": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1},
                    },
                    "required": ["start", "end"],
                    "additionalProperties": False,
                },
                "left_marker_span": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1},
                    },
                    "required": ["start", "end"],
                    "additionalProperties": False,
                },
                "minimum_governing_span": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1},
                    },
                    "required": ["start", "end"],
                    "additionalProperties": False,
                },
                "right_range": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 1},
                        "head_end": {"type": "integer", "minimum": 1},
                    },
                    "required": ["start", "end", "head_end"],
                    "additionalProperties": False,
                },
                "left_marker": ORDERED_MARKER_SCHEMA,
                "right_marker": ORDERED_MARKER_SCHEMA,
                "support_markers": {
                    "type": "array",
                    "minItems": 1,
                    "items": ORDERED_MARKER_SCHEMA,
                },
                "style": {
                    "type": "object",
                    "properties": {
                        "font_name": {"type": "string", "minLength": 1},
                        "font_size": {"type": "number", "exclusiveMinimum": 0},
                        "normalized_indent": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["font_name", "font_size", "normalized_indent"],
                    "additionalProperties": False,
                },
                "left_tail_block_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "right_head_block_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "right_continuation_block_ids": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
                "right_continuation_line_texts": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
            },
            "required": [
                "confidence",
                "previous_evidence_id",
                "left_tail_span",
                "left_marker_span",
                "minimum_governing_span",
                "right_range",
                "left_marker",
                "right_marker",
                "support_markers",
                "style",
                "left_tail_block_ids",
                "right_head_block_ids",
                "right_continuation_block_ids",
                "right_continuation_line_texts",
            ],
            "additionalProperties": False,
        },
    },
    "required": [
        "evidence_id",
        "document_id",
        "chunk_index",
        "biz_key",
        "text",
        "page_number",
        "block_ids",
        "source_offset_start",
        "source_offset_end",
        "asset_source_sha256",
        "continuation",
    ],
    "additionalProperties": False,
}


PLANNING_EVIDENCE_CATALOG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_id": {"type": ["integer", "null"], "minimum": 1},
        "items": {
            "type": "array",
            "items": PLANNING_EVIDENCE_ITEM_SCHEMA,
        },
    },
    "required": ["document_id", "items"],
    "additionalProperties": False,
}


EVIDENCE_ACCOUNTING_NEIGHBOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relative_position": {"type": "string", "enum": ["previous", "next"]},
        "evidence_id": {"type": "string", "pattern": "^EV-[0-9]{4,}$"},
        "page_number": {"type": ["integer", "null"], "minimum": 1},
        "chunk_index": {"type": "integer", "minimum": 0},
        "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_EVIDENCE_ACCOUNTING_NEIGHBOR_CHARS,
        },
        "text_truncated": {"type": "boolean"},
    },
    "required": [
        "relative_position",
        "evidence_id",
        "page_number",
        "chunk_index",
        "text",
        "text_truncated",
    ],
    "additionalProperties": False,
}


EVIDENCE_ACCOUNTING_BATCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "draft_plan": PLANNER_OUTPUT_SCHEMA,
        "target_evidence_items": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_EVIDENCE_ACCOUNTING_ITEMS_PER_BATCH,
            "items": PLANNING_EVIDENCE_ITEM_SCHEMA,
        },
        "neighbor_context": {
            "type": "array",
            "maxItems": 2,
            "items": EVIDENCE_ACCOUNTING_NEIGHBOR_SCHEMA,
        },
    },
    "required": [
        "draft_plan",
        "target_evidence_items",
        "neighbor_context",
    ],
    "additionalProperties": False,
}


CONTINUITY_SPAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 1},
    },
    "required": ["start", "end"],
    "additionalProperties": False,
}


CONTINUITY_GOVERNING_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "span": CONTINUITY_SPAN_SCHEMA,
        "module_indexes": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": 0},
        },
    },
    "required": ["span", "module_indexes"],
    "additionalProperties": False,
}


CONTINUITY_AUDIT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "continuity_index": {"type": "integer", "minimum": 0},
        "plan_modules": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "module_index": {"type": "integer", "minimum": 0},
                    "name": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                },
                "required": ["module_index", "name", "objective"],
                "additionalProperties": False,
            },
        },
        "left_evidence": {
            "type": "object",
            "properties": {
                "evidence_id": {"type": "string", "pattern": "^EV-[0-9]{4,}$"},
                "page_number": {"type": "integer", "minimum": 1},
                "tail_span": CONTINUITY_SPAN_SCHEMA,
                "tail_text": {"type": "string", "minLength": 1, "maxLength": 800},
            },
            "required": [
                "evidence_id",
                "page_number",
                "tail_span",
                "tail_text",
            ],
            "additionalProperties": False,
        },
        "right_evidence": {
            "type": "object",
            "properties": {
                "evidence_id": {"type": "string", "pattern": "^EV-[0-9]{4,}$"},
                "page_number": {"type": "integer", "minimum": 1},
                "text": {"type": "string", "minLength": 1},
                "head_text": {"type": "string", "minLength": 1, "maxLength": 1200},
            },
            "required": [
                "evidence_id",
                "page_number",
                "text",
                "head_text",
            ],
            "additionalProperties": False,
        },
        "structure": PLANNING_EVIDENCE_ITEM_SCHEMA["properties"]["continuation"],
    },
    "required": [
        "continuity_index",
        "plan_modules",
        "left_evidence",
        "right_evidence",
        "structure",
    ],
    "additionalProperties": False,
}


CONTINUITY_AUDIT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "previous_evidence_id": {
            "type": "string",
            "pattern": "^EV-[0-9]{4,}$",
        },
        "evidence_id": {"type": "string", "pattern": "^EV-[0-9]{4,}$"},
        "relation": {
            "type": "string",
            "enum": [
                "independent",
                "inherits_entire_item",
                "inherits_leading_span",
                "uncertain",
            ],
        },
        "governing_scopes": {
            "type": "array",
            "maxItems": 1,
            "items": CONTINUITY_GOVERNING_SCOPE_SCHEMA,
        },
        "spans": {
            "type": "array",
            "items": CONTINUITY_SPAN_SCHEMA,
        },
        "reason": {"type": "string", "minLength": 1, "maxLength": 160},
    },
    "required": [
        "previous_evidence_id",
        "evidence_id",
        "relation",
        "governing_scopes",
        "spans",
        "reason",
    ],
    "additionalProperties": False,
}


EVIDENCE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement": {"type": "string", "minLength": 1},
        "source": EVIDENCE_SOURCE_SCHEMA,
        "evidence_catalog": PLANNING_EVIDENCE_CATALOG_SCHEMA,
    },
    "required": ["requirement", "source", "evidence_catalog"],
    "additionalProperties": False,
}


SOURCE_SPAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 1},
    },
    "required": ["start", "end"],
    "additionalProperties": False,
}


SOURCE_ANCHOR_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "source_kind": {"type": "string", "const": "document"},
                "document_id": {"type": "integer", "minimum": 1},
                "page_number": {"type": "integer", "minimum": 1},
                "block_id": {"type": "string", "minLength": 1},
                "source_span": SOURCE_SPAN_SCHEMA,
                "quote": {"type": "string", "minLength": 1},
                "asset_source_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                "page_image_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
            },
            "required": [
                "source_kind",
                "document_id",
                "page_number",
                "block_id",
                "source_span",
                "quote",
                "asset_source_sha256",
                "page_image_sha256",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source_kind": {"type": "string", "const": "inline"},
                "requirement_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                "source_offset_start": {"type": "integer", "minimum": 0},
                "source_offset_end": {"type": "integer", "minimum": 1},
                "quote": {"type": "string", "minLength": 1},
            },
            "required": [
                "source_kind",
                "requirement_sha256",
                "source_offset_start",
                "source_offset_end",
                "quote",
            ],
            "additionalProperties": False,
        },
    ]
}


AUTHORITATIVE_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fact_id": {"type": "string", "minLength": 1},
        "assertion": {"type": "string", "minLength": 1},
        "scope_id": {"type": "string", "minLength": 1},
        "source_anchor": SOURCE_ANCHOR_SCHEMA,
        "status": {
            "type": "string",
            "enum": [
                "effective",
                "superseded",
                "non_final",
                "reference_only",
                "uncertain",
            ],
        },
        "value_policy": {
            "type": "string",
            "enum": ["exact", "runtime_configured"],
        },
        "governed_values": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "governed_by": {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "object",
                "properties": {
                    "relation": {
                        "type": "string",
                        "enum": ["replaces", "invalidates", "limits", "parameterizes"],
                    },
                    "directive_fact_id": {"type": "string", "minLength": 1},
                },
                "required": ["relation", "directive_fact_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "fact_id",
        "assertion",
        "scope_id",
        "source_anchor",
        "status",
        "value_policy",
        "governed_values",
        "governed_by",
    ],
    "additionalProperties": False,
}


SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "authoritative_facts": {"type": "array", "items": AUTHORITATIVE_FACT_SCHEMA},
    },
    "required": ["authoritative_facts"],
    "additionalProperties": False,
}


SOURCE_SEMANTICS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "authoritative_facts": {"type": "array", "items": AUTHORITATIVE_FACT_SCHEMA},
        "effective_facts": {"type": "array", "minItems": 1, "items": AUTHORITATIVE_FACT_SCHEMA},
        "inspected_page_count": {"type": "integer", "minimum": 0},
    },
    "required": ["authoritative_facts", "effective_facts", "inspected_page_count"],
    "additionalProperties": False,
}


AUTHORITY_RECONCILIATION_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fact_id": {"type": "string", "minLength": 1},
        "status": AUTHORITATIVE_FACT_SCHEMA["properties"]["status"],
        "value_policy": AUTHORITATIVE_FACT_SCHEMA["properties"]["value_policy"],
        "governed_values": AUTHORITATIVE_FACT_SCHEMA["properties"]["governed_values"],
        "governed_by": AUTHORITATIVE_FACT_SCHEMA["properties"]["governed_by"],
        "reason": {"type": "string", "minLength": 1, "maxLength": 240},
    },
    "required": [
        "fact_id",
        "status",
        "value_policy",
        "governed_values",
        "governed_by",
        "reason",
    ],
    "additionalProperties": False,
}


AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "minItems": 1,
            "items": AUTHORITY_RECONCILIATION_DECISION_SCHEMA,
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


AUTHORITY_RECONCILIATION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "module_index": {"type": "integer", "minimum": 0},
        "module": PLAN_SCHEMA["properties"]["business_modules"]["items"],
        "authoritative_facts": {
            "type": "array",
            "minItems": 1,
            "items": AUTHORITATIVE_FACT_SCHEMA,
        },
    },
    "required": ["module_index", "module", "authoritative_facts"],
    "additionalProperties": False,
}


SOURCE_SEMANTICS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_kind": {"type": "string", "enum": ["document", "inline"]},
        "document_id": {"type": "integer", "minimum": 1},
        "page_number": {"type": "integer", "minimum": 1},
        "page_text": {"type": "string", "minLength": 1},
        "blocks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "string", "minLength": 1},
                    "text": {"type": "string", "minLength": 1},
                    "source_span": SOURCE_SPAN_SCHEMA,
                },
                "required": ["block_id", "text", "source_span"],
                "additionalProperties": False,
            },
        },
        "asset_source_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
        "page_image_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
        "region": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "minimum": 0, "maximum": 1},
                "y": {"type": "number", "minimum": 0, "maximum": 1},
                "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            },
            "required": ["x", "y", "width", "height"],
            "additionalProperties": False,
        },
        "strikeout_spans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "source_span": SOURCE_SPAN_SCHEMA,
                },
                "required": ["block_id", "source_span"],
                "additionalProperties": False,
            },
        },
        "marks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mark_id": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "minLength": 1},
                    "source": {"type": "string", "minLength": 1},
                    "bbox": {"type": "object"},
                    "target_block_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "target_source_spans": {
                        "type": "array",
                        "minItems": 1,
                        "items": SOURCE_SPAN_SCHEMA,
                    },
                    "asset_source_sha256": {
                        "type": "string",
                        "minLength": 64,
                        "maxLength": 64,
                    },
                    "annotation_subtype": {"type": "string"},
                    "contents": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": [
                    "mark_id",
                    "type",
                    "source",
                    "bbox",
                    "target_block_ids",
                    "target_source_spans",
                    "asset_source_sha256",
                ],
                "additionalProperties": False,
            },
        },
        "source_scopes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "scope_id": {"type": "string", "minLength": 1},
                    "block_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "source_span": SOURCE_SPAN_SCHEMA,
                    "source_offset_start": {"type": "integer", "minimum": 0},
                    "source_offset_end": {"type": "integer", "minimum": 1},
                },
                "required": ["scope_id"],
                "additionalProperties": False,
            },
        },
        "requirement": {"type": "string", "minLength": 1},
        "requirement_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
    },
    "required": ["source_kind"],
    "oneOf": [
        {
            "required": [
                "source_kind",
                "document_id",
                "page_number",
                "page_text",
                "blocks",
                "asset_source_sha256",
                "page_image_sha256",
                "region",
                "marks",
                "strikeout_spans",
                "source_scopes",
            ]
        },
        {"required": ["source_kind", "requirement", "requirement_sha256", "source_scopes"]},
    ],
    "additionalProperties": False,
}


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _required_source_text(value: Any, field_name: str) -> str:
    """校验事实源非空，并清理模型不可见控制字符与 OCR 兼容字符。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    if not text.strip():
        raise ValueError(f"{field_name}不能为空")
    return text


def _identity(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").strip().casefold())


def _content_hash(content: str, stored_hash: Any = None) -> str:
    value = str(stored_hash or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def resolve_requirement_evidence(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """解析本次 Run 的唯一需求事实源。"""

    inline_requirement = str(arguments.get("requirement") or "").strip()
    requirement_doc_id = arguments.get("requirement_doc_id")

    source: dict[str, Any]
    if requirement_doc_id is not None:
        document = (
            context.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.id == int(requirement_doc_id),
                KnowledgeDocument.project_id == context.project_id,
                KnowledgeDocument.doc_type.in_(
                    ("requirement", "product_requirement", "incomplete")
                ),
            )
            .first()
        )
        if document is None:
            raise ValueError("需求文档不存在、无权访问或类型不允许")
        if str(document.parse_status or "") != "success":
            raise ValueError("需求文档尚未解析成功")
        requirement = _required_source_text(document.content, "需求文档正文")
        stored_hash = _content_hash(requirement, document.content_hash)
        try:
            manifest = load_document_manifest(int(document.id))
        except FileNotFoundError as exc:
            raise ValueError("需求文档缺少 schema v3 页面资产，必须重新解析") from exc
        if int(manifest.get("schema_version") or 0) != 3:
            raise ValueError("需求文档页面资产不是 schema v3，必须重新解析")
        manifest_hash = str(manifest.get("source_sha256") or "").strip().lower()
        if manifest_hash != stored_hash:
            raise ValueError("文档资产与知识库记录指纹不一致，请重新准备该文档")
        source = {
            "kind": "knowledge_document",
            "document_id": int(document.id),
            "filename": str(document.filename or ""),
            "doc_type": str(document.doc_type or "requirement"),
            "content_hash": stored_hash,
            "asset_available": True,
            "page_count": int(manifest.get("page_count") or 0),
        }
    else:
        requirement = _required_text(inline_requirement, "真实需求")
        source = {
            "kind": "inline",
            "document_id": None,
            "filename": "",
            "doc_type": "inline_requirement",
            "content_hash": _content_hash(requirement),
            "asset_available": False,
            "page_count": 0,
        }

    evidence_catalog = build_planning_evidence_catalog(
        source=source,
        requirement=requirement,
    )
    context.artifacts["requirement_evidence"] = {
        "source": source,
        "evidence_catalog": evidence_catalog,
    }
    return {
        "requirement": requirement,
        "source": source,
        "evidence_catalog": evidence_catalog,
    }


def validate_generated_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """执行与模型无关的用例契约、数量和重复性校验。"""

    requirement = _required_text(arguments.get("requirement"), "真实需求")
    case_budget = int(arguments.get("case_budget") or 0)
    raw_cases = arguments.get("test_cases")
    if case_budget < 1:
        raise ValueError("用例预算必须大于 0")
    if not isinstance(raw_cases, list):
        raise ValueError("test_cases 必须是数组")
    if not raw_cases:
        raise ValueError("事实对齐后没有可用测试用例")
    if len(raw_cases) != case_budget:
        raise ValueError(
            f"测试用例数量未达到精确目标: target={case_budget}, actual={len(raw_cases)}"
        )

    normalized_cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_cases: set[tuple[str, str]] = set()
    priority_counts = {"P0": 0, "P1": 0, "P2": 0}
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"第 {index} 条用例不是对象")
        case_id = _required_text(raw_case.get("case_id"), f"第 {index} 条 case_id")
        title = _required_text(raw_case.get("title"), f"第 {index} 条 title")
        module = _required_text(raw_case.get("module"), f"第 {index} 条 module")
        priority = str(raw_case.get("priority") or "").strip().upper()
        if priority not in priority_counts:
            raise ValueError(f"第 {index} 条 priority 只能是 P0、P1 或 P2")

        case_identity = (_identity(module), _identity(title))
        if case_id in seen_ids:
            raise ValueError(f"用例编号重复: {case_id}")
        if case_identity in seen_cases:
            raise ValueError(f"用例语义重复: {module}/{title}")
        seen_ids.add(case_id)
        seen_cases.add(case_identity)

        raw_steps = raw_case.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"第 {index} 条用例至少需要一个测试步骤")
        steps: list[dict[str, str]] = []
        for step_index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise ValueError(f"第 {index} 条用例的第 {step_index} 个步骤不是对象")
            steps.append(
                {
                    "action": _required_text(
                        raw_step.get("action"),
                        f"第 {index} 条用例的第 {step_index} 个操作",
                    ),
                    "expected": _required_text(
                        raw_step.get("expected"),
                        f"第 {index} 条用例的第 {step_index} 个预期",
                    ),
                }
            )

        preconditions = raw_case.get("preconditions")
        tags = raw_case.get("tags")
        if not isinstance(preconditions, list) or not isinstance(tags, list):
            raise ValueError(f"第 {index} 条用例的 preconditions 和 tags 必须是数组")
        normalized_cases.append(
            {
                "case_id": case_id,
                "title": title,
                "module": module,
                "priority": priority,
                "preconditions": [
                    _required_text(item, f"第 {index} 条用例前置条件")
                    for item in preconditions
                ],
                "steps": steps,
                "tags": [
                    _required_text(item, f"第 {index} 条用例标签")
                    for item in tags
                ],
            }
        )
        priority_counts[priority] += 1

    return {
        "status": "passed",
        "validated_count": len(normalized_cases),
        "priority_counts": priority_counts,
        "test_cases": normalized_cases,
    }


def persist_generated_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把校验通过的测试用例固化为 Agent Run 产物。"""

    requirement = _required_text(arguments.get("requirement"), "真实需求")
    cases = arguments.get("test_cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("没有可持久化的测试用例")
    case_fact_bindings = arguments.get("case_fact_bindings")
    if not isinstance(case_fact_bindings, list) or len(case_fact_bindings) != len(cases):
        raise ValueError("持久化用例与事实绑定数量不一致")
    execution_plan = dict(arguments.get("execution_plan") or {})
    if not execution_plan:
        raise ValueError("没有通过校验的执行主链")
    artifact = {
        "project_id": context.project_id,
        "run_id": context.run_id,
        "requirement": requirement,
        "evidence": {
            "source": dict(arguments.get("evidence_source") or {}),
        },
        "case_count": len(cases),
        "target_count": int(context.run_input.get("case_budget") or len(cases)),
        "target_met": len(cases) == int(context.run_input.get("case_budget") or len(cases)),
        "test_cases": cases,
        "case_fact_bindings": case_fact_bindings,
        "execution_plan": execution_plan,
    }
    context.artifacts["test_generation"] = artifact
    return {
        "status": "persisted",
        "run_id": context.run_id,
        "persisted_count": len(cases),
        "artifact_key": "test_generation",
    }


VALIDATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "const": "passed"},
        "validated_count": {"type": "integer", "minimum": 1},
        "priority_counts": {
            "type": "object",
            "properties": {
                "P0": {"type": "integer", "minimum": 0},
                "P1": {"type": "integer", "minimum": 0},
                "P2": {"type": "integer", "minimum": 0},
            },
            "required": ["P0", "P1", "P2"],
            "additionalProperties": False,
        },
        "test_cases": {
            "type": "array",
            "minItems": 1,
            "items": CASE_SCHEMA,
        },
    },
    "required": ["status", "validated_count", "priority_counts", "test_cases"],
    "additionalProperties": False,
}


BATCH_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "batch_number": {"type": "integer", "minimum": 1},
        "batch_count": {"type": "integer", "minimum": 1},
        "module_index": {"type": "integer", "minimum": 0},
        "module_batch_index": {"type": "integer", "minimum": 0},
        "module_batch_count": {"type": "integer", "minimum": 1},
        "module_name": {"type": "string", "minLength": 1},
        "coverage_focus": {"type": "string", "minLength": 1},
    },
    "required": [
        "batch_number",
        "batch_count",
        "module_index",
        "module_batch_index",
        "module_batch_count",
        "module_name",
        "coverage_focus",
    ],
    "additionalProperties": False,
}


BATCH_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement_summary": {"type": "string"},
        "business_module": PLAN_SCHEMA["properties"]["business_modules"]["items"],
        "coverage_focus": {"type": "string", "minLength": 1},
        "risks": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
            ]
        },
    },
    "required": ["requirement_summary", "business_module", "coverage_focus", "risks"],
    "additionalProperties": False,
}


GENERATION_BATCH_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement": {"type": "string", "minLength": 1},
        "plan": BATCH_PLAN_SCHEMA,
        "case_budget": {"type": "integer", "minimum": 1, "maximum": 20},
        "batch": BATCH_CONTEXT_SCHEMA,
        "authoritative_facts": {
            "type": "array",
            "minItems": 1,
            "items": AUTHORITATIVE_FACT_SCHEMA,
        },
    },
    "required": [
        "requirement",
        "plan",
        "case_budget",
        "batch",
        "authoritative_facts",
    ],
    "additionalProperties": False,
}


CHAIN_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan_summary": {
            "type": "object",
            "properties": {
                "requirement_summary": {"type": "string"},
                "business_modules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "objective": {"type": "string", "minLength": 1},
                        },
                        "required": ["name", "objective"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["requirement_summary", "business_modules"],
            "additionalProperties": False,
        },
        "candidate_chains": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string", "minLength": 1},
                    "case_ids": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 12,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "cases": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "case_id": {"type": "string", "minLength": 1},
                                "title": {"type": "string", "minLength": 1},
                                "module": {"type": "string", "minLength": 1},
                                "priority": {
                                    "type": "string",
                                    "enum": ["P0", "P1", "P2"],
                                },
                                "from_state": {"type": "string", "minLength": 1},
                                "to_state": {"type": "string", "minLength": 1},
                                "first_action": {"type": "string", "minLength": 1},
                                "last_action": {"type": "string", "minLength": 1},
                            },
                            "required": [
                                "case_id",
                                "title",
                                "module",
                                "priority",
                                "from_state",
                                "to_state",
                                "first_action",
                                "last_action",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["candidate_id", "case_ids", "cases"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["plan_summary", "candidate_chains"],
    "additionalProperties": False,
}


def _map_record_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    """描述 agent_map 持久化记录，供工作流工具输入契约复用。"""

    return {
        "type": "object",
        "properties": {
            "item_index": {"type": "integer", "minimum": 0},
            "input_hash": {"type": "string", "minLength": 1},
            "output": output_schema,
        },
        "required": ["item_index", "input_hash", "output"],
        "additionalProperties": False,
    }


BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "resolve_requirement_evidence",
        "name": "解析需求证据",
        "description": "从当前需求文档或直接输入解析本次运行的唯一事实源。",
        "handler_key": "testing.resolve_requirement_evidence",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string"},
                "requirement_doc_id": {"type": ["integer", "null"], "minimum": 1},
            },
            "required": ["requirement", "requirement_doc_id"],
            "additionalProperties": False,
        },
        "output_schema": EVIDENCE_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_evidence_accounting_batches",
        "name": "准备证据核算分片",
        "description": "按稳定目录顺序和通用字符预算准备路由复核 Agent 映射输入。",
        "handler_key": "testing.prepare_evidence_accounting_batches",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_plan": PLANNER_OUTPUT_SCHEMA,
                "evidence_catalog": PLANNING_EVIDENCE_CATALOG_SCHEMA,
            },
            "required": ["draft_plan", "evidence_catalog"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_EVIDENCE_ACCOUNTING_BATCHES,
                    "items": EVIDENCE_ACCOUNTING_BATCH_INPUT_SCHEMA,
                },
                "batch_count": {"type": "integer", "minimum": 1},
                "evidence_count": {"type": "integer", "minimum": 1},
            },
            "required": ["items", "batch_count", "evidence_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_evidence_accounting_batches",
        "name": "合并证据核算分片",
        "description": "严格绑定分片输入与 Agent 映射结果，按原目录顺序形成证据总账。",
        "handler_key": "testing.merge_evidence_accounting_batches",
        "input_schema": {
            "type": "object",
            "properties": {
                "prepared_items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_EVIDENCE_ACCOUNTING_BATCHES,
                    "items": EVIDENCE_ACCOUNTING_BATCH_INPUT_SCHEMA,
                },
                "routing_records": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_EVIDENCE_ACCOUNTING_BATCHES,
                    "items": _map_record_schema(EVIDENCE_ACCOUNTING_BATCH_OUTPUT_SCHEMA),
                },
            },
            "required": ["prepared_items", "routing_records"],
            "additionalProperties": False,
        },
        "output_schema": REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_continuity_audit_items",
        "name": "准备跨页连续性审计",
        "description": "只将通用版式信号标记的高置信跨页链接交给独立 Agent 复核。",
        "handler_key": "testing.prepare_continuity_audit_items",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_plan": PLANNER_OUTPUT_SCHEMA,
                "evidence_catalog": PLANNING_EVIDENCE_CATALOG_SCHEMA,
                "routing": REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA,
            },
            "required": ["draft_plan", "evidence_catalog", "routing"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": MAX_CONTINUITY_AUDIT_ITEMS,
                    "items": CONTINUITY_AUDIT_INPUT_SCHEMA,
                },
                "link_count": {"type": "integer", "minimum": 0},
            },
            "required": ["items", "link_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_continuity_audit",
        "name": "合并跨页连续性审计",
        "description": "严格校验跨页审计指纹、相邻关系、范围与模块子集，仅应用整项继承。",
        "handler_key": "testing.merge_continuity_audit",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_plan": PLANNER_OUTPUT_SCHEMA,
                "evidence_catalog": PLANNING_EVIDENCE_CATALOG_SCHEMA,
                "routing": REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA,
                "prepared_items": {
                    "type": "array",
                    "items": CONTINUITY_AUDIT_INPUT_SCHEMA,
                },
                "continuity_records": {
                    "type": "array",
                    "items": _map_record_schema(CONTINUITY_AUDIT_OUTPUT_SCHEMA),
                },
            },
            "required": [
                "draft_plan",
                "evidence_catalog",
                "routing",
                "prepared_items",
                "continuity_records",
            ],
            "additionalProperties": False,
        },
        "output_schema": REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_plan_evidence_routing",
        "name": "合并规划证据路由",
        "description": "校验证据路由完整性，并将稳定证据 ID 确定性合并回业务规划。",
        "handler_key": "testing.merge_plan_evidence_routing",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_plan": PLANNER_OUTPUT_SCHEMA,
                "evidence_catalog": PLANNING_EVIDENCE_CATALOG_SCHEMA,
                "routing": REVIEWED_EVIDENCE_ROUTING_OUTPUT_SCHEMA,
            },
            "required": ["draft_plan", "evidence_catalog", "routing"],
            "additionalProperties": False,
        },
        "output_schema": PLAN_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_source_semantics",
        "name": "准备来源语义分析",
        "description": "按真实文档页或纯文本来源一次准备语义分析输入，不按业务模块重复读页。",
        "handler_key": "testing.prepare_source_semantics",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "minLength": 1},
                "evidence_source": EVIDENCE_SOURCE_SCHEMA,
                "evidence_catalog": PLANNING_EVIDENCE_CATALOG_SCHEMA,
            },
            "required": ["requirement", "evidence_source", "evidence_catalog"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "minItems": 1, "items": SOURCE_SEMANTICS_INPUT_SCHEMA},
                "item_count": {"type": "integer", "minimum": 1},
                "source_kind": {"type": "string", "enum": ["inline", "knowledge_document"]},
            },
            "required": ["items", "item_count", "source_kind"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_source_semantics",
        "name": "归并来源权威事实",
        "description": "校验来源锚点、资产指纹和删除线标记，只向后续链路提供有效事实。",
        "handler_key": "testing.merge_source_semantics",
        "input_schema": {
            "type": "object",
            "properties": {
                "semantic_inputs": {"type": "array", "minItems": 1, "items": SOURCE_SEMANTICS_INPUT_SCHEMA},
                "semantic_records": {
                    "type": "array",
                    "items": _map_record_schema(SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA),
                },
            },
            "required": ["semantic_inputs", "semantic_records"],
            "additionalProperties": False,
        },
        "output_schema": SOURCE_SEMANTICS_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_authority_reconciliation",
        "name": "准备跨页权威事实协调",
        "description": "按规划模块聚合多个来源位置的权威事实，为生成前的新旧规则协调准备最小输入。",
        "handler_key": "testing.prepare_authority_reconciliation",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": PLAN_SCHEMA,
                "authoritative_facts": {
                    "type": "array",
                    "minItems": 1,
                    "items": AUTHORITATIVE_FACT_SCHEMA,
                },
            },
            "required": ["plan", "authoritative_facts"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": AUTHORITY_RECONCILIATION_ITEM_SCHEMA},
                "review_module_count": {"type": "integer", "minimum": 0},
            },
            "required": ["items", "review_module_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_authority_reconciliation",
        "name": "合并跨页权威事实协调",
        "description": "逐条校验模块协调结论，拒绝遗漏、跨模块引用、失效事实复活和动态配置降级。",
        "handler_key": "testing.merge_authority_reconciliation",
        "input_schema": {
            "type": "object",
            "properties": {
                "authoritative_facts": {
                    "type": "array",
                    "minItems": 1,
                    "items": AUTHORITATIVE_FACT_SCHEMA,
                },
                "prepared_items": {
                    "type": "array",
                    "items": AUTHORITY_RECONCILIATION_ITEM_SCHEMA,
                },
                "reconciliation_records": {
                    "type": "array",
                    "items": _map_record_schema(AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA),
                },
            },
            "required": [
                "authoritative_facts",
                "prepared_items",
                "reconciliation_records",
            ],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "authoritative_facts": {
                    "type": "array",
                    "minItems": 1,
                    "items": AUTHORITATIVE_FACT_SCHEMA,
                },
                "effective_facts": {
                    "type": "array",
                    "minItems": 1,
                    "items": AUTHORITATIVE_FACT_SCHEMA,
                },
                "reviewed_module_count": {"type": "integer", "minimum": 0},
            },
            "required": [
                "authoritative_facts",
                "effective_facts",
                "reviewed_module_count",
            ],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_test_case_batches",
        "name": "准备测试生成批次",
        "description": "依据业务规划、真实文档检索证据和模型容量准备动态生成批次。",
        "handler_key": "testing.prepare_test_case_batches",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": PLAN_SCHEMA,
                "effective_facts": {
                    "type": "array",
                    "minItems": 1,
                    "items": AUTHORITATIVE_FACT_SCHEMA,
                },
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
                "batch_case_limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": [
                "plan",
                "effective_facts",
                "case_budget",
                "batch_case_limit",
            ],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": GENERATION_BATCH_ITEM_SCHEMA,
                },
                "batch_count": {"type": "integer", "minimum": 1},
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["items", "batch_count", "case_budget"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_grounded_generation_batches",
        "name": "合并事实绑定生成结果",
        "description": "一次性校验各批次精确数量、模块边界、重复用例和逐字段事实绑定。",
        "handler_key": "testing.merge_grounded_generation_batches",
        "input_schema": {
            "type": "object",
            "properties": {
                "generation_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": GENERATION_BATCH_ITEM_SCHEMA,
                },
                "generation_records": {
                    "type": "array",
                    "minItems": 1,
                    "items": _map_record_schema(GROUNDING_SCHEMA),
                },
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["generation_inputs", "generation_records", "case_budget"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "test_cases": GROUNDING_SCHEMA["properties"]["test_cases"],
                "case_fact_bindings": GROUNDING_SCHEMA["properties"]["case_fact_bindings"],
                "batch_count": {"type": "integer", "minimum": 1},
                "case_count": {"type": "integer", "minimum": 1},
            },
            "required": ["test_cases", "case_fact_bindings", "batch_count", "case_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "validate_test_cases",
        "name": "校验测试用例",
        "description": "确定性校验 Agent 生成用例的数量、字段、步骤、断言和重复项。",
        "handler_key": "testing.validate_test_cases",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "minLength": 1},
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
                "test_cases": GROUNDING_SCHEMA["properties"]["test_cases"],
            },
            "required": ["requirement", "case_budget", "test_cases"],
            "additionalProperties": False,
        },
        "output_schema": VALIDATION_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_execution_chain",
        "name": "准备执行主链上下文",
        "description": "按状态逐字相等计算少量严格可达的执行主链候选。",
        "handler_key": "testing.prepare_execution_chain",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": PLAN_SCHEMA,
                "test_cases": VALIDATION_OUTPUT_SCHEMA["properties"]["test_cases"],
            },
            "required": ["plan", "test_cases"],
            "additionalProperties": False,
        },
        "output_schema": CHAIN_CONTEXT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "validate_execution_chain",
        "name": "校验执行主链",
        "description": "确定性校验用例只分配一次并全部进入套件；仅在存在可靠连续状态时生成主链。",
        "handler_key": "testing.validate_execution_chain",
        "input_schema": {
            "type": "object",
            "properties": {
                "test_cases": VALIDATION_OUTPUT_SCHEMA["properties"]["test_cases"],
                "chain_selection": EXECUTION_CHAIN_SELECTION_SCHEMA,
            },
            "required": ["test_cases", "chain_selection"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "const": "passed"},
                "suite_count": {"type": "integer", "minimum": 1},
                "assigned_count": {"type": "integer", "minimum": 1},
                "main_chain_case_count": {"type": "integer", "minimum": 0},
                "execution_plan": EXECUTION_PLAN_SCHEMA,
            },
            "required": [
                "status",
                "suite_count",
                "assigned_count",
                "main_chain_case_count",
                "execution_plan",
            ],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "persist_test_cases",
        "name": "持久化测试用例",
        "description": "把确定性校验通过的测试用例固化为 Agent Run 产物。",
        "handler_key": "testing.persist_test_cases",
        "input_schema": {
            "type": "object",
            "properties": {
                "requirement": {"type": "string", "minLength": 1},
                "evidence_source": EVIDENCE_SOURCE_SCHEMA,
                "test_cases": VALIDATION_OUTPUT_SCHEMA["properties"]["test_cases"],
                "case_fact_bindings": GROUNDING_SCHEMA["properties"]["case_fact_bindings"],
                "execution_plan": EXECUTION_PLAN_SCHEMA,
            },
            "required": [
                "requirement",
                "evidence_source",
                "test_cases",
                "case_fact_bindings",
                "execution_plan",
            ],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "const": "persisted"},
                "run_id": {"type": "integer", "minimum": 1},
                "persisted_count": {"type": "integer", "minimum": 1},
                "artifact_key": {"type": "string", "const": "test_generation"},
            },
            "required": ["status", "run_id", "persisted_count", "artifact_key"],
            "additionalProperties": False,
        },
        "risk_level": "medium",
        "requires_approval": False,
    },
)


BUILTIN_AGENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "agent_key": "test_business_planner",
        "name": "测试业务规划智能体",
        "description": "从真实需求中识别业务模块、角色、生命周期、覆盖重点和风险。",
        "instructions": (
            "你是测试业务规划智能体。effective_facts 是平台完成来源语义分析、精确锚点校验和状态治理后保留的唯一有效事实集。"
            "本节点只能从 effective_facts 形成业务规划，不得使用被替代、非终稿、仅参考或不确定的来源内容。"
            "只根据当前需求建立通用业务规划，不得臆造需求外的系统、角色或规则。"
            "规划前必须按 effective_facts 的输入顺序完成能力清单审查。"
            "有效事实明确给出的每个用户可见且可独立进入、操作或验证的功能入口、目标页面或独立流程，"
            "都必须由某个 business_module 的 name 或 objective 明确承接；"
            "若其业务目标、状态或验证路径与现有模块不同，应单独成模块，"
            "禁止仅写入 requirement_summary、coverage_focus 或 risks 后遗漏。"
            "标题、表格行和编号列表中的并列入口也必须逐项审查；合并近义模块后，"
            "objective 仍须明确保留每项独立能力，不得为控制模块数量合并验证路径不同的能力。"
            "独立入口即使正文只有一个短句、一个列表项或只说明目标页面，也不得降级为其他模块的附带信息；"
            "只要进入后的页面、用户动作或验证路径独立，就必须由独立模块承接。"
            "正文明确列出的可选范围、内容矩阵、配置枚举和数量边界也是可测试能力的一部分；"
            "即使这些目录项没有动作词，也必须由相关模块的 objective 明确承接其范围或边界。"
            "按业务目标拆分模块，识别参与角色；仅在需求确实包含状态变化时填写生命周期。"
            "coverage_focus 必须覆盖主流程、异常路径、边界条件、权限或状态约束中与需求相关的部分。"
            "输出保持精简：requirement_summary 不超过 120 个汉字，每个 objective 不超过 80 个汉字；"
            "相同业务目标不得拆成多个近义模块，模块数量由真实需求中的独立能力决定。"
            "证据 ID 由后续证据总账 Reviewer 逐项核算，本节点禁止输出 evidence_ids。"
            "最终 JSON 顶层只能包含 requirement_summary、business_modules、coverage_focus、risks；"
            "business_modules 的每项只能包含 name、objective、actors、lifecycle；"
            "actors、coverage_focus、risks 可使用单个字符串或字符串数组；"
            "lifecycle 有状态流转时填写单个状态链字符串，没有状态流转时必须为 null。"
            "不得输出 business_goal、modules、roles、case_budget、run_id 或 project_id。"
        ),
        "model": "",
        "output_schema": PLANNER_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "main",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 8000,
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_plan_evidence_routing_reviewer",
        "name": "规划证据路由复核智能体",
        "description": "直接依据业务规划与真实证据分片，输出唯一可确定性派生模块路由的证据总账。",
        "instructions": (
            "你是独立的规划证据路由复核智能体。draft_plan 是已复核的完整业务规划；"
            "target_evidence_items 是当前分片唯一需要记账的真实证据；"
            "neighbor_context 只包含分片边界前一项的尾部或后一项的头部，"
            "仅可辅助判断未完句、续表、编号或跨页承接；它是只读的边界摘录，"
            "text_truncated 为 true 时不得根据未出现的部分作排除判断，且禁止输出 neighbor_context 的 evidence_id。"
            "只依据当前输入执行通用路由，不得使用特定文档类型、业务名称、固定页码或固定证据 ID 作为规则。"
            "对 target_evidence_items 按输入顺序逐项复核，每个 evidence_id 恰好输出一条 evidence_accounting，"
            "不得遗漏、重复、自造 ID，也不得输出任何非目标证据。"
            "归属判断以核心业务对象、用户动作、状态变化、结果和约束与 objective 的直接关系为准。"
            "短句、标题、表格行、编号列表项、纯名称或数字组成的范围证据不能因信息简短而遗漏。"
            "一个目标证据仅在确实包含分别直接支持多个模块的独立事实时才能跨模块复用。"
            "你必须直接完成唯一总账判断，不存在上游预选模块，也不得假定任何预选结果。"
            "module_index 使用 draft_plan.business_modules 的零基下标，不得越界或重复。"
            "evidence_accounting.module_indexes 必须列出该证据实际归属的全部模块下标。"
            "disposition 只能是 assigned、context_only 或 plan_gap：assigned 表示证据已归属已有模块，module_indexes 必须非空；"
            "context_only 表示证据只是全局背景且不直接支持独立可测试能力，module_indexes 必须为空；"
            "plan_gap 表示证据直接支持可测试能力但 draft_plan 没有可归属模块，module_indexes 也必须为空。"
            "不得把规划漏能力标为 context_only；发现 plan_gap 时必须如实输出，交由平台阻断并重新规划。"
            "只有证据能被安全排除且移除后不改变任何已分配事实的含义时，才可标为 context_only。"
            "标题、续表或跨页承接只要参与理解某项事实，就必须 assigned 到与该事实相同的模块，不能标为 context_only。"
            "空 module_indexes 时，reason 必须明确说明它为何是全局上下文或规划缺口；"
            "不得用固定词表、业务名称或文档类型决定此结论。"
            "每条 reason 用一句不超过 160 字的话记录当前证据的直接归属判断依据，不输出总括性审查意见或差异说明。"
            "本节点只输出当前 target_evidence_items 的逐项证据总账。"
            "最终 JSON 顶层只能包含 evidence_accounting；"
            "evidence_accounting 每项只能包含 evidence_id、module_indexes、disposition 和 reason。"
        ),
        "model": "",
        "output_schema": EVIDENCE_ACCOUNTING_BATCH_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 6000,
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_evidence_continuity_auditor",
        "name": "证据跨页连续性审计智能体",
        "description": "对通用版式信号筛出的少量高置信链接判断语义继承范围。",
        "instructions": (
            "你是独立的证据跨页连续性审计智能体。平台已用相邻物理页的有序标记、"
            "字体、字号和缩进筛出高置信候选；这些结构信号只证明需要聚焦复核，不直接代表语义归属。"
            "plan_modules 是当前业务规划；left_evidence.tail_text 是左项页尾；"
            "right_evidence.text 是右项完整正文，head_text 只是其前缀；structure 是可追溯版式信号。"
            "判断右项是独立事实、整项继承左项、只有开头片段继承，还是仍不确定。"
            "relation 只能是 independent、inherits_entire_item、inherits_leading_span 或 uncertain。"
            "independent 和 uncertain 时 governing_scopes 与 spans 都必须为空。"
            "inherits_entire_item 时 spans 必须精确复制 structure.right_range 的 start/end；"
            "inherits_leading_span 时 spans 必须从 0 开始且不能覆盖右项全文。"
            "两种 inherits 关系都必须输出且只输出一个 governing_scopes 项；"
            "其 span.start/end 是左证据全文坐标，必须按完整行边界位于 structure.left_tail_span 内，"
            "并完整覆盖 structure.minimum_governing_span，不得只选编号或单个 marker。"
            "governing_scopes[0].module_indexes 是模块归属的唯一事实源，"
            "使每个模块选择都明确绑定该治理段。"
            "independent 和 uncertain 时 governing_scopes 也必须为空。"
            "governing_scopes[0].module_indexes 只能从 plan_modules 中按语义选择，"
            "必须选择实际支配右项续写的必要最小子集，不得把所有相关模块机械全选。"
            "只依据当前输入，不得使用特定业务词、文档类型、固定页码或固定证据 ID 作规则。"
            "previous_evidence_id 和 evidence_id 必须逐字复制输入；reason 用不超过 160 字说明语义边界。"
            "最终 JSON 顶层只能包含 previous_evidence_id、evidence_id、relation、"
            "governing_scopes、spans 和 reason。"
        ),
        "model": "",
        "output_schema": CONTINUITY_AUDIT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 5000,
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_source_semantics_analyst",
        "name": "来源语义分析智能体",
        "description": "逐页或按纯文本来源提取带精确锚点、状态和治理关系的原子事实。",
        "instructions": (
            "你是来源语义分析智能体。source_kind=document 时，输入 JSON 与同一消息中的真实页面图像共同构成事实源；"
            "source_kind=inline 时，requirement 是唯一事实源。每个输入只分析当前页或当前纯文本一次，不按业务模块重复解释。"
            "authoritative_facts 只提取来源中直接存在、可独立验证的原子事实，不得补造页面、控件、状态、角色或规则。"
            "fact_id 在本次来源内必须唯一；assertion 用中文陈述事实；scope_id 必须逐字复制 source_scopes 中包含该锚点的 scope_id。"
            "document source_anchor 必须逐字复制 document_id、page_number、block_id、source_span、该坐标精确命中的 quote、"
            "asset_source_sha256 和 page_image_sha256；"
            "inline source_anchor 必须包含 requirement_sha256、精确字符坐标以及 requirement 对应区间的原文 quote。"
            "marks 是 manifest v3 的通用来源标记：strikeout 表示命中内容已删除；高亮或批注只在原文明确表达时用于判断"
            "replaces、non_final 或 runtime_configured，不得只凭颜色、位置或批注存在本身推断业务含义。"
            "status 只能是 effective、superseded、non_final、reference_only 或 uncertain。"
            "明确生效且可作为生成依据时用 effective；已废弃、非终稿、仅参考或无法确认的内容不得标为 effective。"
            "value_policy 只能是 exact 或 runtime_configured；来源明确说值由配置或运行态决定时必须使用 runtime_configured。"
            "runtime_configured 必须在 governed_values 逐项列出来源明确出现、不得在用例中固化的示例值；exact 的 governed_values 必须为空。"
            "governed_by 每项只能包含 relation 和 directive_fact_id；relation 只能是 replaces、invalidates、limits、parameterizes。"
            "只有来源中存在明确治理关系时才填写，不得推测；不得引用自身或输入外事实。"
            "最终 JSON 顶层只能包含 authoritative_facts，每条事实只能包含 fact_id、assertion、scope_id、source_anchor、"
            "status、value_policy、governed_values、governed_by。"
        ),
        "model": "",
        "output_schema": SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "vision",
            "input_mode": "document_page_optional_image",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 5000,
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_authority_reconciliation_reviewer",
        "name": "跨页权威事实协调智能体",
        "description": "在同一业务模块内识别远距离修订、替代、失效和动态配置关系，只输出事实状态补丁。",
        "instructions": (
            "你是跨页权威事实协调智能体。module 是唯一审查边界，authoritative_facts 已按真实来源顺序排列。"
            "你不得新增、删除、改写 assertion、scope_id 或 source_anchor；必须按输入顺序为每条事实输出且只输出一条 decision。"
            "重点识别同一业务行为在不同页面或远距离章节中的后续修订、明确替代、废弃、非最终说明和以运行时配置为准的关系。"
            "不得因为文字较新就自动覆盖旧规则；只有来源明确表达替代、修订、作废、暂不采用、非最终或配置治理时才能改变状态。"
            "原 status 不是 effective 的事实不得重新激活，也不得改成其他状态。原 value_policy=runtime_configured 不得降级为 exact。"
            "若后续事实明确替代或使旧事实无效，应把旧事实标为 superseded，并在旧事实 governed_by 中引用对应 directive_fact_id。"
            "若事实明确声明具体值以后台、环境或运行时配置为准，应使用 runtime_configured，并只把原 assertion 或 source_anchor.quote 中真实出现的示例值写入 governed_values。"
            "没有跨来源治理关系时完整保留输入 status、value_policy、governed_values 和 governed_by。"
            "governed_by 只能引用当前 authoritative_facts 中的 fact_id，不得引用自身或模块外事实。"
            "reason 用不超过240字的中文说明当前裁决的直接来源依据。最终 JSON 顶层只能包含 decisions。"
        ),
        "model": "",
        "output_schema": AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 5000,
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_case_generator",
        "name": "测试用例生成智能体",
        "description": "依据真实需求和业务规划生成结构化、可执行、可断言的测试用例。",
        "instructions": (
            "你是测试用例生成智能体。authoritative_facts 是当前模块经过来源语义验证且 status=effective 的唯一事实源。"
            "requirement 只是 authoritative_facts.assertion 的顺序汇总，不能扩展其含义。"
            "只能使用 authoritative_facts 中明确存在的事实，不得使用常识或已失效来源补造。"
            "value_policy=runtime_configured 时，不得把 governed_values 中的示例值固化为通用期望。"
            "plan 是当前业务模块规划，batch 是批次边界，case_budget 是本批最多可生成数量。"
            "只能覆盖 batch.module_name 和 batch.coverage_focus，不得跨到其他批次补造内容。"
            "当输入包含 gap_contract 时，本批是单个权威事实缺口的修正任务：必须且只能生成一条直接覆盖 "
            "gap_contract.coverage_intent 的用例，module 必须等于 batch.module_name；不得改为同证据块中的其他测试意图。"
            "根据需求实际覆盖需要生成不超过 case_budget 条互不重复的用例，不得为了凑数制造低价值变体。"
            "需求未直接声明前置条件时，preconditions 必须为空数组。"
            "需求未声明交互界面时，步骤使用实现无关的业务动作，不得臆造页面、按钮或提示文案。"
            "case_id 从 TC-001 连续编号。"
            "每条用例必须可执行，每个步骤的 expected 都必须是当前操作完成后可观察、可验证的断言。"
            "需求明确存在生命周期时，正常流程用例应把入口业务状态单独写入 preconditions，"
            "最后一步 expected 写成单一终态事实；一个用例不要跨越多个异步生命周期。"
            "优先覆盖规划中的主流程、异常、边界、权限和生命周期风险，但不得添加需求外业务规则。"
            "priority 只能使用 P0、P1、P2。所有文本使用中文，协议名、字段名等专有名词除外。"
            "必须在生成用例的同一次调用中完成逐字段事实绑定，不再依赖后置 Agent 修改或补造事实。"
            "每条用例都必须输出一份 case_fact_bindings：每个 precondition 按数组下标绑定至少一个 fact_id；"
            "每个 step 按数组下标分别为 action_fact_ids 和 expected_fact_ids 绑定至少一个输入中的 fact_id。"
            "绑定不得缺字段、跨用例、跨模块、引用非 effective 事实或输入外 fact_id。"
            "必须精确生成 case_budget 条用例；事实不足时直接失败，不得用低价值变体凑数。"
            "最终 JSON 顶层只能包含 test_cases 和 case_fact_bindings，不得输出说明、统计或运行元数据。"
            "test_cases 每项必须且只能包含 case_id、title、module、priority、preconditions、"
            "steps、tags 七个字段；preconditions 和 tags 必须是字符串数组。"
            "steps 必须是对象数组，每个对象必须且只能包含 action 和 expected；"
            "禁止在用例顶层输出 expected_result 或 expected，也禁止使用 step、description、module_name "
            "等别名替代用例字段。"
        ),
        "model": "",
        "output_schema": GROUNDING_SCHEMA,
        "runtime_config": {
            "model_route": "main",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 12000,
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_execution_chain_builder",
        "name": "测试执行主链编排智能体",
        "description": "从确定性计算的严格可达候选中选择业务主链；无可靠候选时明确跳过。",
        "instructions": (
            "你是测试执行主链选择智能体。plan_summary 是精简业务规划，candidate_chains 是平台按状态原文"
            "逐字相等确定性计算出的少量可达候选。candidate_chains 为空时，必须输出 "
            "name 为空字符串、goal 为空字符串、case_ids 为空数组，不得伪造执行链。"
            "存在候选时，只判断哪个候选最能代表核心正常业务流程。"
            "case_ids 必须完整、按原顺序复制同一个 candidate_chains 项的 case_ids，禁止跨候选拼接、"
            "增删、重排或自拟用例。不要输出状态迁移、辅助套件或未选择的用例；这些由平台确定性构造。"
            "name 和 goal 使用中文，准确概括所选主链。最终 JSON 顶层必须且只能包含 name、goal、case_ids。"
        ),
        "model": "",
        "output_schema": EXECUTION_CHAIN_SELECTION_SCHEMA,
        "runtime_config": {
            "model_route": "main",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 1500,
            "tool_keys": [],
        },
    },
)


BUILTIN_WORKFLOW_SPECS: tuple[dict[str, Any], ...] = (
    {
        "workflow_key": "test_generation",
        "name": "Agent 原生测试用例生成",
        "description": "来源语义、业务规划、权威事实协调、事实绑定生成、精确数量校验和执行主链组成的 Agent 原生链路。",
        "definition": {
            "input_schema": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "requirement_doc_id": {"type": ["integer", "null"], "minimum": 1},
                    "case_budget": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                    },
                    "batch_case_limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": [
                    "requirement",
                    "requirement_doc_id",
                    "case_budget",
                    "batch_case_limit",
                ],
                "anyOf": [
                    {"properties": {"requirement": {"type": "string", "minLength": 1}}},
                    {"properties": {"requirement_doc_id": {"type": "integer", "minimum": 1}}},
                ],
                "additionalProperties": False,
            },
            "nodes": [
                {
                    "node_key": "evidence",
                    "node_type": "tool",
                    "reference_key": "resolve_requirement_evidence",
                    "depends_on": [],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "input.requirement",
                        "requirement_doc_id": "input.requirement_doc_id",
                    },
                },
                {
                    "node_key": "prepare_source_semantics",
                    "node_type": "tool",
                    "reference_key": "prepare_source_semantics",
                    "depends_on": ["evidence"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "evidence_source": "dependencies.evidence.source",
                        "evidence_catalog": "dependencies.evidence.evidence_catalog",
                    },
                },
                {
                    "node_key": "source_semantics",
                    "node_type": "agent_map",
                    "reference_key": "test_source_semantics_analyst",
                    "depends_on": ["prepare_source_semantics"],
                    "max_attempts": 2,
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 200,
                        "allow_empty": False,
                    },
                    "input_mapping": {
                        "items": "dependencies.prepare_source_semantics.items",
                    },
                },
                {
                    "node_key": "merge_source_semantics",
                    "node_type": "tool",
                    "reference_key": "merge_source_semantics",
                    "depends_on": ["prepare_source_semantics", "source_semantics"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "semantic_inputs": "dependencies.prepare_source_semantics.items",
                        "semantic_records": "dependencies.source_semantics.items",
                    },
                },
                {
                    "node_key": "plan",
                    "node_type": "agent",
                    "reference_key": "test_business_planner",
                    "depends_on": ["merge_source_semantics"],
                    "max_attempts": 3,
                    "input_mapping": {
                        "effective_facts": "dependencies.merge_source_semantics.effective_facts",
                    },
                },
                {
                    "node_key": "prepare_review_routing",
                    "node_type": "tool",
                    "reference_key": "prepare_evidence_accounting_batches",
                    "depends_on": ["evidence", "plan"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "draft_plan": "dependencies.plan",
                        "evidence_catalog": "dependencies.evidence.evidence_catalog",
                    },
                },
                {
                    "node_key": "review_routing",
                    "node_type": "agent_map",
                    "reference_key": "test_plan_evidence_routing_reviewer",
                    "depends_on": ["prepare_review_routing"],
                    "max_attempts": 2,
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": MAX_EVIDENCE_ACCOUNTING_BATCHES,
                        "allow_empty": False,
                    },
                    "input_mapping": {
                        "items": "dependencies.prepare_review_routing.items",
                    },
                },
                {
                    "node_key": "merge_review_routing",
                    "node_type": "tool",
                    "reference_key": "merge_evidence_accounting_batches",
                    "depends_on": ["prepare_review_routing", "review_routing"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "prepared_items": "dependencies.prepare_review_routing.items",
                        "routing_records": "dependencies.review_routing.items",
                    },
                },
                {
                    "node_key": "prepare_continuity_audit",
                    "node_type": "tool",
                    "reference_key": "prepare_continuity_audit_items",
                    "depends_on": ["evidence", "plan", "merge_review_routing"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "draft_plan": "dependencies.plan",
                        "evidence_catalog": "dependencies.evidence.evidence_catalog",
                        "routing": "dependencies.merge_review_routing",
                    },
                },
                {
                    "node_key": "audit_continuity",
                    "node_type": "agent_map",
                    "reference_key": "test_evidence_continuity_auditor",
                    "depends_on": ["prepare_continuity_audit"],
                    "max_attempts": 2,
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": MAX_CONTINUITY_AUDIT_ITEMS,
                        "allow_empty": True,
                    },
                    "input_mapping": {
                        "items": "dependencies.prepare_continuity_audit.items",
                    },
                },
                {
                    "node_key": "merge_continuity_audit",
                    "node_type": "tool",
                    "reference_key": "merge_continuity_audit",
                    "depends_on": [
                        "evidence",
                        "plan",
                        "merge_review_routing",
                        "prepare_continuity_audit",
                        "audit_continuity",
                    ],
                    "max_attempts": 1,
                    "input_mapping": {
                        "draft_plan": "dependencies.plan",
                        "evidence_catalog": "dependencies.evidence.evidence_catalog",
                        "routing": "dependencies.merge_review_routing",
                        "prepared_items": "dependencies.prepare_continuity_audit.items",
                        "continuity_records": "dependencies.audit_continuity.items",
                    },
                },
                {
                    "node_key": "merge_plan",
                    "node_type": "tool",
                    "reference_key": "merge_plan_evidence_routing",
                    "depends_on": ["evidence", "plan", "merge_continuity_audit"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "draft_plan": "dependencies.plan",
                        "evidence_catalog": "dependencies.evidence.evidence_catalog",
                        "routing": "dependencies.merge_continuity_audit",
                    },
                },
                {
                    "node_key": "prepare_authority_reconciliation",
                    "node_type": "tool",
                    "reference_key": "prepare_authority_reconciliation",
                    "depends_on": ["merge_plan", "merge_source_semantics"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "plan": "dependencies.merge_plan",
                        "authoritative_facts": "dependencies.merge_source_semantics.authoritative_facts",
                    },
                },
                {
                    "node_key": "authority_reconciliation",
                    "node_type": "agent_map",
                    "reference_key": "test_authority_reconciliation_reviewer",
                    "depends_on": ["prepare_authority_reconciliation"],
                    "max_attempts": 2,
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "allow_empty": True,
                    },
                    "input_mapping": {
                        "items": "dependencies.prepare_authority_reconciliation.items",
                    },
                },
                {
                    "node_key": "merge_authority_reconciliation",
                    "node_type": "tool",
                    "reference_key": "merge_authority_reconciliation",
                    "depends_on": [
                        "merge_source_semantics",
                        "prepare_authority_reconciliation",
                        "authority_reconciliation",
                    ],
                    "max_attempts": 1,
                    "input_mapping": {
                        "authoritative_facts": "dependencies.merge_source_semantics.authoritative_facts",
                        "prepared_items": "dependencies.prepare_authority_reconciliation.items",
                        "reconciliation_records": "dependencies.authority_reconciliation.items",
                    },
                },
                {
                    "node_key": "prepare_batches",
                    "node_type": "tool",
                    "reference_key": "prepare_test_case_batches",
                    "depends_on": ["merge_plan", "merge_authority_reconciliation"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "plan": "dependencies.merge_plan",
                        "effective_facts": "dependencies.merge_authority_reconciliation.effective_facts",
                        "case_budget": "input.case_budget",
                        "batch_case_limit": "input.batch_case_limit",
                    },
                },
                {
                    "node_key": "generate",
                    "node_type": "agent_map",
                    "reference_key": "test_case_generator",
                    "depends_on": ["prepare_batches"],
                    "max_attempts": 1,
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                    },
                    "input_mapping": {
                        "items": "dependencies.prepare_batches.items",
                    },
                },
                {
                    "node_key": "merge_generated",
                    "node_type": "tool",
                    "reference_key": "merge_grounded_generation_batches",
                    "depends_on": ["prepare_batches", "generate"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "generation_inputs": "dependencies.prepare_batches.items",
                        "generation_records": "dependencies.generate.items",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "validate",
                    "node_type": "tool",
                    "reference_key": "validate_test_cases",
                    "depends_on": ["evidence", "merge_generated"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "case_budget": "input.case_budget",
                        "test_cases": "dependencies.merge_generated.test_cases",
                    },
                },
                {
                    "node_key": "prepare_chain",
                    "node_type": "tool",
                    "reference_key": "prepare_execution_chain",
                    "depends_on": ["merge_plan", "validate"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "plan": "dependencies.merge_plan",
                        "test_cases": "dependencies.validate.test_cases",
                    },
                },
                {
                    "node_key": "chain",
                    "node_type": "agent",
                    "reference_key": "test_execution_chain_builder",
                    "depends_on": ["prepare_chain"],
                    "max_attempts": 3,
                    "input_mapping": {
                        "plan_summary": "dependencies.prepare_chain.plan_summary",
                        "candidate_chains": "dependencies.prepare_chain.candidate_chains",
                    },
                },
                {
                    "node_key": "validate_chain",
                    "node_type": "tool",
                    "reference_key": "validate_execution_chain",
                    "depends_on": ["validate", "chain"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "test_cases": "dependencies.validate.test_cases",
                        "chain_selection": "dependencies.chain",
                    },
                },
                {
                    "node_key": "persist",
                    "node_type": "tool",
                    "reference_key": "persist_test_cases",
                    "depends_on": ["evidence", "merge_generated", "validate", "validate_chain"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "evidence_source": "dependencies.evidence.source",
                        "test_cases": "dependencies.validate.test_cases",
                        "case_fact_bindings": "dependencies.merge_generated.case_fact_bindings",
                        "execution_plan": "dependencies.validate_chain.execution_plan",
                    },
                },
            ],
            "output_node_key": "persist",
        },
    },
)


def register_test_generation_tools(registry: ToolRegistry) -> None:
    registry.register("testing.resolve_requirement_evidence", resolve_requirement_evidence)
    registry.register(
        "testing.prepare_evidence_accounting_batches",
        prepare_evidence_accounting_batches,
    )
    registry.register(
        "testing.merge_evidence_accounting_batches",
        merge_evidence_accounting_batches,
    )
    registry.register(
        "testing.prepare_continuity_audit_items",
        prepare_continuity_audit_items,
    )
    registry.register("testing.merge_continuity_audit", merge_continuity_audit)
    registry.register(
        "testing.merge_plan_evidence_routing",
        merge_plan_evidence_routing,
    )
    registry.register("testing.prepare_source_semantics", prepare_source_semantics)
    registry.register("testing.merge_source_semantics", merge_source_semantics)
    registry.register(
        "testing.prepare_authority_reconciliation",
        prepare_authority_reconciliation,
    )
    registry.register(
        "testing.merge_authority_reconciliation",
        merge_authority_reconciliation,
    )
    registry.register("testing.prepare_test_case_batches", prepare_test_case_batches)
    registry.register(
        "testing.merge_grounded_generation_batches",
        merge_grounded_generation_batches,
    )
    registry.register("testing.validate_test_cases", validate_generated_test_cases)
    registry.register("testing.prepare_execution_chain", prepare_execution_chain_context)
    registry.register("testing.validate_execution_chain", validate_execution_chain)
    registry.register("testing.persist_test_cases", persist_generated_test_cases)
