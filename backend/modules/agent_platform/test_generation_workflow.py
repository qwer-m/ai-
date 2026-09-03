from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
import unicodedata
from typing import Any, TYPE_CHECKING

from core.db.model_defs import KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    load_document_manifest,
)
from .test_generation_batching import (
    build_planning_evidence_catalog,
    merge_grounded_generation_batches,
    postprocess_generation_batch_item,
    prepare_execution_chain_context,
    prepare_test_case_batches,
    select_execution_chain,
    validate_execution_chain,
)
from .context_compression import (
    compress_evidence_catalog,
    context_compression_enabled,
    context_compression_max_tokens,
)
from .test_generation_semantics import (
    GOVERNANCE_RELATION_ALIASES,
    merge_authority_reconciliation,
    merge_source_semantics,
    postprocess_authority_reconciliation_item,
    postprocess_source_semantics_item,
    prepare_authority_reconciliation,
    prepare_source_semantics,
)
from .test_generation_review import (
    merge_final_review_batches,
    merge_final_review_recheck_records,
    merge_final_review_repairs,
    postprocess_final_review_batch_item,
    postprocess_final_review_repair_item,
    postprocess_global_final_review_output,
    prepare_final_review_batches,
    prepare_final_review_rechecks,
    prepare_final_review_repairs,
    prepare_global_final_review,
    prepare_terminal_final_review_repairs,
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
        "test_design_item_ids": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$"},
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
        "test_design_item_ids",
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

RISK_DETAIL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "risk_id": {"type": "string", "minLength": 1},
        "description": {"type": "string", "minLength": 1},
        "severity": {"type": "string", "minLength": 1},
        "related_fact_ids": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["description"],
    "additionalProperties": False,
}

RISK_OR_TEXTS_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "minLength": 1},
        RISK_DETAIL_SCHEMA,
        {
            "type": "array",
            "items": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    RISK_DETAIL_SCHEMA,
                ]
            },
        },
    ]
}

ACTORS_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "string", "minLength": 1},
        {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
    ]
}


TEST_DESIGN_TECHNIQUES = [
    "场景法",
    "等价类",
    "边界值",
    "状态迁移",
    "判定表",
    "错误推测",
]


PLANNER_TEST_DESIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "technique": {"type": "string", "enum": TEST_DESIGN_TECHNIQUES},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 160},
        "coverage_items": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 160},
        },
    },
    "required": ["technique", "rationale", "coverage_items"],
    "additionalProperties": False,
}


PLANNER_TEST_POINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "objective": {"type": "string", "minLength": 1, "maxLength": 160},
        "test_designs": {
            "type": "array",
            "minItems": 1,
            "items": PLANNER_TEST_DESIGN_SCHEMA,
        },
    },
    "required": ["name", "objective", "test_designs"],
    "additionalProperties": False,
}


TEST_DESIGN_CATALOG_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_design_item_id": {
            "type": "string",
            "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$",
        },
        "module_index": {"type": "integer", "minimum": 0},
        "module_name": {"type": "string", "minLength": 1},
        "test_point": {"type": "string", "minLength": 1},
        "technique": {"type": "string", "enum": TEST_DESIGN_TECHNIQUES},
        "rationale": {"type": "string", "minLength": 1},
        "coverage_intent": {"type": "string", "minLength": 1},
    },
    "required": [
        "test_design_item_id",
        "module_index",
        "module_name",
        "test_point",
        "technique",
        "rationale",
        "coverage_intent",
    ],
    "additionalProperties": False,
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
                    "actors": ACTORS_SCHEMA,
                    "lifecycle": {
                        "type": ["string", "null"],
                    },
                    "test_points": {
                        "type": "array",
                        "minItems": 1,
                        "items": PLANNER_TEST_POINT_SCHEMA,
                    },
                },
                "required": [
                    "name",
                    "objective",
                    "actors",
                    "lifecycle",
                    "test_points",
                ],
                "additionalProperties": False,
            },
        },
        "coverage_focus": TEXT_OR_TEXTS_SCHEMA,
        "risks": RISK_OR_TEXTS_SCHEMA,
    },
    "required": [
        "requirement_summary",
        "business_modules",
        "coverage_focus",
        "risks",
    ],
    "additionalProperties": False,
}


# 全局规划 Agent 只负责需要跨批次推理的模块结构；摘要字段由平台从已校验草案编译。
PLANNER_AGENT_OUTPUT_SCHEMA: dict[str, Any] = deepcopy(PLANNER_OUTPUT_SCHEMA)
for _compiled_field in ("coverage_focus", "risks"):
    PLANNER_AGENT_OUTPUT_SCHEMA["properties"].pop(_compiled_field)
    PLANNER_AGENT_OUTPUT_SCHEMA["required"].remove(_compiled_field)

# 全局汇总只返回候选语义组 ID，平台再恢复组内已校验的原子覆盖文本。
# 一个候选组是分批 Planner 已经形成的最小业务语义单元，避免全局模型
# 枚举数百个原子 ID 时整组漏项，也避免重复改写自然语言导致超时。
PLANNER_AGENT_SUBMISSION_SCHEMA: dict[str, Any] = deepcopy(
    PLANNER_AGENT_OUTPUT_SCHEMA
)
PLANNER_AGENT_SUBMISSION_SCHEMA["properties"]["business_modules"]["items"][
    "properties"
]["test_points"]["items"]["properties"]["test_designs"]["items"][
    "properties"
]["coverage_items"]["items"] = {
    "type": "string",
    "pattern": "^CG-[0-9]{4,}$",
}


BUSINESS_PLAN_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "batch_summary": {"type": "string", "minLength": 1, "maxLength": 240},
        "module_candidates": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "objective": {"type": "string", "minLength": 1, "maxLength": 160},
                    "actors": ACTORS_SCHEMA,
                    "lifecycle": {"type": ["string", "null"]},
                    "coverage_topics": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1, "maxLength": 80},
                                "objective": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 160,
                                },
                            },
                            "required": ["name", "objective"],
                            "additionalProperties": False,
                        },
                    },
                    "fact_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                "required": [
                    "name",
                    "objective",
                    "actors",
                    "lifecycle",
                    "coverage_topics",
                    "fact_ids",
                ],
                "additionalProperties": False,
            },
        },
        "coverage_focus": TEXT_OR_TEXTS_SCHEMA,
        "risks": RISK_OR_TEXTS_SCHEMA,
    },
    "required": ["batch_summary", "module_candidates", "coverage_focus", "risks"],
    "additionalProperties": False,
}


PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scope_id": {"type": "string", "pattern": "^EV-[0-9]{4,}$"},
        "assignments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "string", "minLength": 1},
                    "module_routes": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "type": "object",
                            "properties": {
                                "module_index": {"type": "integer", "minimum": 0},
                                "relation": {
                                    "type": "string",
                                    "enum": ["primary", "shared"],
                                },
                                "test_design_item_indexes": {
                                    "type": "array",
                                    "uniqueItems": True,
                                    "items": {"type": "integer", "minimum": 0},
                                },
                            },
                            "required": [
                                "module_index",
                                "relation",
                                "test_design_item_indexes",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "fact_id",
                    "module_routes",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scope_id", "assignments"],
    "additionalProperties": False,
}

BUSINESS_PLANNING_BATCH_MAX_FACTS = 20
BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS = 4500
PLANNING_SCOPE_ROUTE_BATCH_SIZE = 2
# 路由输出会为每条事实生成模块映射；按模型视图大小限流，避免两个大 scope
# 被强行拼成一个超长响应，导致事实遗漏或结构化输出退化。
PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS = 16000
PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "routes": {
            "type": "array",
            "minItems": 1,
            "maxItems": PLANNING_SCOPE_ROUTE_BATCH_SIZE,
            "items": PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA,
        }
    },
    "required": ["routes"],
    "additionalProperties": False,
}

PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA = deepcopy(
    PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA
)
_planning_route_agent_assignment_schema = (
    PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA["properties"]["routes"]["items"]
    ["properties"]["assignments"]["items"]
)
_planning_route_agent_assignment_schema["properties"].pop("fact_id")
_planning_route_agent_assignment_schema["properties"]["fact_ref"] = {
    "type": "string",
    "pattern": "^RF-[0-9]{3,}$",
}
_planning_route_agent_assignment_schema["required"] = [
    "fact_ref",
    "module_routes",
]


PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "module_index": {"type": "integer", "minimum": 0},
        "decisions": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "test_design_item_index": {"type": "integer", "minimum": 0},
                    "disposition": {
                        "type": "string",
                        "enum": ["supported", "unsupported"],
                    },
                    "fact_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": [
                    "test_design_item_index",
                    "disposition",
                    "fact_ids",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["module_index", "decisions"],
    "additionalProperties": False,
}


PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA = deepcopy(
    PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA
)


BATCH_FINAL_REVIEW_DIFFERENCE_CATEGORIES = [
    "business_semantics",
    "executability",
    "state_coherence",
    "unsupported_business_rule",
    "semantic_duplicate",
    "deterministic_audit",
]

GLOBAL_FINAL_REVIEW_DIFFERENCE_CATEGORIES = [
    *BATCH_FINAL_REVIEW_DIFFERENCE_CATEGORIES,
    "coverage_imbalance",
    "priority_conflict",
]


FINAL_REVIEW_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phase": {"type": "string", "const": "final_review"},
        "approved": {"type": "boolean"},
        "summary": {"type": "string", "minLength": 1},
        "differences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": ["string", "null"]},
                    "category": {
                        "type": "string",
                        "enum": GLOBAL_FINAL_REVIEW_DIFFERENCE_CATEGORIES,
                    },
                    "field_path": {"type": ["string", "null"]},
                    "detail": {"type": "string", "minLength": 1},
                    "related_fact_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "repair_scope": {
                        "type": "string",
                        "enum": ["case", "cohort"],
                    },
                    "repair_instruction": {"type": "string", "minLength": 1},
                },
                "required": [
                    "case_id",
                    "category",
                    "field_path",
                    "detail",
                    "related_fact_ids",
                    "repair_instruction",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "phase",
        "approved",
        "summary",
        "differences",
    ],
    "additionalProperties": False,
}

# phase 由工作流节点确定，summary 缺失时可由审查结论确定性生成；
# 模型边界只强制要求不可由平台推导的业务判断字段。
BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA: dict[str, Any] = deepcopy(
    FINAL_REVIEW_OUTPUT_SCHEMA
)
BATCH_FINAL_REVIEW_DIFFERENCE_SCHEMA = BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA[
    "properties"
]["differences"]["items"]
BATCH_FINAL_REVIEW_DIFFERENCE_SCHEMA["properties"]["category"][
    "enum"
] = BATCH_FINAL_REVIEW_DIFFERENCE_CATEGORIES
BATCH_FINAL_REVIEW_DIFFERENCE_SCHEMA["properties"].pop("related_fact_ids")
BATCH_FINAL_REVIEW_DIFFERENCE_SCHEMA["required"].remove("related_fact_ids")
BATCH_FINAL_REVIEW_DIFFERENCE_SCHEMA["required"].append("repair_scope")
BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA["required"] = [
    "approved",
    "differences",
]

GLOBAL_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA: dict[str, Any] = deepcopy(
    FINAL_REVIEW_OUTPUT_SCHEMA
)
GLOBAL_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA["properties"]["differences"]["items"][
    "properties"
]["case_id"] = {"type": "string", "minLength": 1}
GLOBAL_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA["required"] = [
    "approved",
    "differences",
]


SCENARIO_DESIGN_GUIDANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommended_case_count": {"type": "integer", "minimum": 1, "maximum": 20},
        "scenario_groups": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "scenario_key": {"type": "string", "minLength": 1, "maxLength": 80},
                    "scenario_type": {
                        "type": "string",
                        "enum": [
                            "main",
                            "exception",
                            "boundary",
                            "permission",
                            "lifecycle",
                        ],
                    },
                    "precondition_fact_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "action_fact_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "expected_fact_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "test_design_item_ids": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$",
                        },
                    },
                },
                "required": [
                    "scenario_key",
                    "scenario_type",
                    "precondition_fact_ids",
                    "action_fact_ids",
                    "expected_fact_ids",
                    "test_design_item_ids",
                ],
                "additionalProperties": False,
            },
        },
        "warnings": {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "string",
                "enum": [
                    "cross_lifecycle",
                    "cross_role",
                    "missing_observable_assertion",
                    "fact_overload",
                ],
            },
        },
    },
    "required": ["recommended_case_count", "scenario_groups", "warnings"],
    "additionalProperties": False,
}


FACT_DESIGN_ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fact_id": {"type": "string", "minLength": 1},
        "test_design_item_indexes": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": 0},
        },
    },
    "required": ["fact_id", "test_design_item_indexes"],
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
                    "actors": ACTORS_SCHEMA,
                    "lifecycle": {
                        "type": ["string", "null"],
                    },
                    "test_points": {
                        "type": "array",
                        "minItems": 1,
                        "items": PLANNER_TEST_POINT_SCHEMA,
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
                    "fact_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "fact_design_routes": {
                        "type": "array",
                        "minItems": 1,
                        "items": FACT_DESIGN_ROUTE_SCHEMA,
                    },
                },
                "required": [
                    "name",
                    "objective",
                    "actors",
                    "lifecycle",
                    "test_points",
                    "evidence_ids",
                    "fact_ids",
                    "fact_design_routes",
                ],
                "additionalProperties": False,
            },
        },
        "coverage_focus": TEXT_OR_TEXTS_SCHEMA,
        "risks": RISK_OR_TEXTS_SCHEMA,
    },
    "required": [
        "requirement_summary",
        "business_modules",
        "coverage_focus",
        "risks",
    ],
    "additionalProperties": False,
}


FACT_ID_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "uniqueItems": True,
    "items": {"type": "string", "minLength": 1},
}

OPTIONAL_FACT_ID_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "uniqueItems": True,
    "items": {"type": "string", "minLength": 1},
}


MODEL_GROUNDED_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "fact_ids": FACT_ID_LIST_SCHEMA,
    },
    "required": ["text", "fact_ids"],
    "additionalProperties": False,
}


MODEL_STEP_FACT_BINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": OPTIONAL_FACT_ID_LIST_SCHEMA,
        "expected": FACT_ID_LIST_SCHEMA,
    },
    "required": ["action", "expected"],
    "additionalProperties": False,
}


MODEL_INLINE_CASE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
        "preconditions": {
            "type": "array",
            "items": MODEL_GROUNDED_TEXT_SCHEMA,
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "minLength": 1},
                    "expected": {"type": "string", "minLength": 1},
                    "fact_bindings": MODEL_STEP_FACT_BINDINGS_SCHEMA,
                },
                "required": ["action", "expected", "fact_bindings"],
                "additionalProperties": False,
            },
        },
        "tags": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
        "test_design_item_ids": {
            "type": "array",
            "uniqueItems": True,
            # 该字段由平台按事实路由派生，不属于模型的生成责任。
            "x-platform-derived": True,
            "items": {
                "type": "string",
                "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$",
            },
        },
    },
    "required": [
        "title",
        "priority",
        "preconditions",
        "steps",
        "test_design_item_ids",
    ],
    "additionalProperties": False,
}


# 生成阶段只要求模型输出可执行内容和逐字段事实绑定。
# test_design_item_ids 可由平台依据事实路由确定性派生，避免把稳定编号生成
# 交给模型而造成结构化输出失败；修复阶段仍使用上面的严格内联契约。
MODEL_GENERATION_CASE_SCHEMA: dict[str, Any] = deepcopy(MODEL_INLINE_CASE_SCHEMA)
# 保留属性以兼容旧缓存/旧模型，但从 required 移除，模型可以完全不生成它。
MODEL_GENERATION_CASE_SCHEMA["required"] = [
    field
    for field in MODEL_GENERATION_CASE_SCHEMA["required"]
    if field != "test_design_item_ids"
]


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
                    "action_fact_ids": OPTIONAL_FACT_ID_LIST_SCHEMA,
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


FINAL_REVIEW_REPAIR_RESULT_SCHEMA: dict[str, Any] = deepcopy(GROUNDING_SCHEMA)
FINAL_REVIEW_REPAIR_RESULT_SCHEMA["properties"] = {
    **GROUNDING_SCHEMA["properties"],
    "review_noop": {"type": "boolean"},
}

MODEL_GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_cases": {
            "type": "array",
            "items": MODEL_GENERATION_CASE_SCHEMA,
            "description": (
                "必须直接传递 JSON 数组；禁止将整个数组再序列化成字符串。"
            ),
        },
    },
    "required": ["test_cases"],
    "additionalProperties": False,
}


# 修复输入保留只读身份字段，便于模型准确定位目标用例。
MODEL_REPAIR_CASE_SCHEMA: dict[str, Any] = deepcopy(MODEL_INLINE_CASE_SCHEMA)
MODEL_REPAIR_CASE_SCHEMA["properties"] = {
    "case_id": {"type": "string", "minLength": 1},
    "module": {"type": "string", "minLength": 1},
    **MODEL_REPAIR_CASE_SCHEMA["properties"],
}
MODEL_REPAIR_CASE_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case_id": {"type": "string", "minLength": 1},
        **deepcopy(MODEL_INLINE_CASE_SCHEMA["properties"]),
    },
    "required": ["case_id"],
    "minProperties": 2,
    "additionalProperties": False,
}
MODEL_REPAIR_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case_patches": {
            "type": "array",
            "minItems": 1,
            "items": MODEL_REPAIR_CASE_PATCH_SCHEMA,
        },
    },
    "required": ["case_patches"],
    "additionalProperties": False,
}


MERGED_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_cases": GROUNDING_SCHEMA["properties"]["test_cases"],
        "case_fact_bindings": GROUNDING_SCHEMA["properties"]["case_fact_bindings"],
        "batch_count": {"type": "integer", "minimum": 1},
        "case_count": {"type": "integer", "minimum": 1},
    },
    "required": ["test_cases", "case_fact_bindings", "batch_count", "case_count"],
    "additionalProperties": False,
}


GENERATION_AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "case_count": {"type": "integer", "minimum": 0},
        "effective_fact_count": {"type": "integer", "minimum": 0},
        "covered_fact_count": {"type": "integer", "minimum": 0},
        "uncovered_fact_ids": {"type": "array", "items": {"type": "string"}},
        "invalid_fact_ids": {"type": "array", "items": {"type": "string"}},
        "duplicate_case_ids": {"type": "array", "items": {"type": "string"}},
        "test_design_item_count": {"type": "integer", "minimum": 0},
        "covered_test_design_item_count": {"type": "integer", "minimum": 0},
        "uncovered_test_design_item_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "invalid_test_design_item_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "summary": {"type": "string", "minLength": 1},
        "differences": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": [
        "approved",
        "case_count",
        "effective_fact_count",
        "covered_fact_count",
        "uncovered_fact_ids",
        "invalid_fact_ids",
        "duplicate_case_ids",
        "test_design_item_count",
        "covered_test_design_item_count",
        "uncovered_test_design_item_ids",
        "invalid_test_design_item_ids",
        "summary",
        "differences",
    ],
    "additionalProperties": False,
}


SYNTHESIS_APPROVAL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "test_cases": GROUNDING_SCHEMA["properties"]["test_cases"],
        "case_fact_bindings": GROUNDING_SCHEMA["properties"]["case_fact_bindings"],
        "final_review": FINAL_REVIEW_OUTPUT_SCHEMA,
    },
    "required": ["test_cases", "case_fact_bindings", "final_review"],
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
                "block_id": {
                    "oneOf": [
                        {
                            "type": "string",
                            "minLength": 1,
                            "description": "单块事实时逐字复制 blocks 中的一个 block_id 字符串。",
                        },
                        {
                            "type": "array",
                            "minItems": 2,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1},
                            "description": (
                                "跨连续布局块的事实时，按页面顺序填写 quote 实际覆盖的最小 block_id 数组。"
                            ),
                        },
                    ],
                    "description": (
                        "只能复制 blocks 中真实存在的单个 block_id 字符串，或 quote 实际覆盖的连续 block_id 数组；"
                        "禁止把 source_scopes.allowed_block_ids 整组复制。"
                    ),
                },
                "source_span": {
                    **SOURCE_SPAN_SCHEMA,
                    "description": "必须精确覆盖 quote，并只命中所选 block_id（或 block_id 数组）范围。",
                },
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


REVIEW_FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fact_id": {"type": "string", "minLength": 1},
        "assertion": {"type": "string", "minLength": 1},
        "value_policy": {"type": "string", "enum": ["exact", "runtime_configured"]},
        "governed_values": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["fact_id", "assertion", "value_policy", "governed_values"],
    "additionalProperties": False,
}


REPAIR_SOURCE_ANCHOR_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "source_kind": {"type": "string", "const": "document"},
                "document_id": {"type": "integer", "minimum": 1},
                "page_number": {"type": "integer", "minimum": 1},
            },
            "required": ["source_kind", "document_id", "page_number"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "source_kind": {"type": "string", "const": "inline"},
                "requirement_sha256": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                },
            },
            "required": ["source_kind", "requirement_sha256"],
            "additionalProperties": False,
        },
    ]
}

REPAIR_AUTHORITATIVE_FACT_SCHEMA: dict[str, Any] = deepcopy(
    AUTHORITATIVE_FACT_SCHEMA
)
REPAIR_AUTHORITATIVE_FACT_SCHEMA["properties"]["source_anchor"] = (
    REPAIR_SOURCE_ANCHOR_SCHEMA
)


FINAL_REVIEW_BATCH_META_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "batch_id": {"type": "string", "minLength": 1},
        "batch_number": {"type": "integer", "minimum": 1},
        "batch_count": {"type": "integer", "minimum": 1},
        "module_name": {"type": "string", "minLength": 1},
        "generation_batch_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "case_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": [
        "batch_id",
        "batch_number",
        "batch_count",
        "module_name",
        "generation_batch_ids",
        "case_ids",
    ],
    "additionalProperties": False,
}


FINAL_REVIEW_BATCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "review_batch": FINAL_REVIEW_BATCH_META_SCHEMA,
        "test_cases": {
            "type": "array",
            "minItems": 1,
            "items": CASE_SCHEMA,
        },
        "case_fact_bindings": {
            "type": "array",
            "minItems": 1,
            "items": CASE_FACT_BINDING_SCHEMA,
        },
        "review_facts": {
            "type": "array",
            "minItems": 1,
            "items": REVIEW_FACT_SCHEMA,
        },
        "test_design_items": {
            "type": "array",
            "items": TEST_DESIGN_CATALOG_ITEM_SCHEMA,
        },
        "audit_summary": GENERATION_AUDIT_SCHEMA,
    },
    "required": [
        "review_batch",
        "test_cases",
        "case_fact_bindings",
        "review_facts",
        "test_design_items",
        "audit_summary",
    ],
    "additionalProperties": False,
}


FINAL_REVIEW_REPAIR_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "review_batch": FINAL_REVIEW_BATCH_META_SCHEMA,
        "test_cases": {
            "type": "array",
            "minItems": 1,
            "items": MODEL_REPAIR_CASE_SCHEMA,
        },
        "authoritative_facts": {
            "type": "array",
            "minItems": 1,
            "items": REPAIR_AUTHORITATIVE_FACT_SCHEMA,
        },
        "test_design_items": {
            "type": "array",
            "items": TEST_DESIGN_CATALOG_ITEM_SCHEMA,
        },
        "review_result": FINAL_REVIEW_OUTPUT_SCHEMA,
        "repair_requirements": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "required_fact_ids": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "target_case_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "target_case_count": {"type": "integer", "minimum": 1},
        "repair_cycle": {"type": "integer", "minimum": 1},
    },
    "required": [
        "review_batch",
        "test_cases",
        "authoritative_facts",
        "test_design_items",
        "review_result",
        "repair_requirements",
        "required_fact_ids",
        "target_case_ids",
        "target_case_count",
    ],
    "additionalProperties": False,
}


GLOBAL_FINAL_REVIEW_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case_index": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "module": {"type": "string", "minLength": 1},
                    "priority": {"type": "string", "minLength": 1},
                    "first_action": {"type": "string"},
                    "last_expected": {"type": "string"},
                },
                "required": [
                    "case_id",
                    "title",
                    "module",
                    "priority",
                    "first_action",
                    "last_expected",
                ],
                "additionalProperties": False,
            },
        },
        "batch_review": FINAL_REVIEW_OUTPUT_SCHEMA,
        "audit_summary": GENERATION_AUDIT_SCHEMA,
    },
    "required": ["case_index", "batch_review", "audit_summary"],
    "additionalProperties": False,
}


# 来源分析是模型原始输出边界：页面来源只选择一个真实块，
# quote、坐标和作用域全部由平台根据真实页面确定性生成。
SOURCE_SEMANTICS_AGENT_ANCHOR_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "document_id": {"type": "integer", "minimum": 1},
                "page_number": {"type": "integer", "minimum": 1},
                "block_id": {"type": "string", "minLength": 1},
            },
            "required": ["document_id", "page_number", "block_id"],
            "additionalProperties": False,
            "description": "只选择一个与原子事实最直接相关的真实 block_id，平台补齐 quote、source_span 和 scope_id。",
        },
        {
            "type": "object",
            "properties": {
                "source_offset_start": {"type": "integer", "minimum": 0},
                "source_offset_end": {"type": "integer", "minimum": 1},
            },
            "required": ["source_offset_start", "source_offset_end"],
            "additionalProperties": False,
        },
    ]
}

def _source_semantics_agent_fact_schema() -> dict[str, Any]:
    """只约束模型输出字段形状，跨字段语义交由后处理统一校验。"""

    schema = deepcopy(AUTHORITATIVE_FACT_SCHEMA)
    schema["properties"]["source_anchor"] = SOURCE_SEMANTICS_AGENT_ANCHOR_SCHEMA
    # 模型偶尔会把 status/value_policy 枚举错填到 relation；先接收字符串，
    # 再由来源后处理基于当前事实目录规范化，避免工具层反复重吐整页事实。
    schema["properties"]["governed_by"]["items"]["properties"]["relation"] = {
        "type": "string",
        "minLength": 1,
    }
    schema["properties"].pop("scope_id")
    schema["properties"].pop("governed_values")
    schema["properties"]["governed_value_spans"] = {
        "type": "array",
        "uniqueItems": True,
        "items": SOURCE_SPAN_SCHEMA,
        "description": (
            "只填写 runtime_configured 事实在当前输入正文中的具体示例值坐标；"
            "压缩页面使用当前 page_text 的局部坐标，平台会确定性转换为原页绝对坐标；"
            "exact 事实必须为空，且不得定位动态策略声明。"
        ),
    }
    schema["required"] = [
        "governed_value_spans" if field == "governed_values" else field
        for field in schema["required"]
        if field != "scope_id"
    ]
    return schema


SOURCE_SEMANTICS_AGENT_FACT_SCHEMA: dict[str, Any] = (
    _source_semantics_agent_fact_schema()
)


SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "authoritative_facts": {
            "type": "array",
            "items": SOURCE_SEMANTICS_AGENT_FACT_SCHEMA,
        },
    },
    "required": ["authoritative_facts"],
    "additionalProperties": False,
}


SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "authoritative_facts": {
            "type": "array",
            "items": AUTHORITATIVE_FACT_SCHEMA,
        },
    },
    "required": ["authoritative_facts"],
    "additionalProperties": False,
}


SOURCE_SEMANTICS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "authoritative_facts": {"type": "array", "items": AUTHORITATIVE_FACT_SCHEMA},
        "effective_facts": {"type": "array", "minItems": 1, "items": AUTHORITATIVE_FACT_SCHEMA},
        "planning_scopes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "scope_id": {"type": "string", "minLength": 1},
                    "facts": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "fact_id": {"type": "string", "minLength": 1},
                                "assertion": {"type": "string", "minLength": 1},
                                "value_policy": {
                                    "type": "string",
                                    "enum": ["exact", "runtime_configured"],
                                },
                                "governed_values": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "governed_by": AUTHORITATIVE_FACT_SCHEMA["properties"]["governed_by"],
                            },
                            "required": [
                                "fact_id",
                                "assertion",
                                "value_policy",
                                "governed_values",
                                "governed_by",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["scope_id", "facts"],
                "additionalProperties": False,
            },
        },
        "inspected_page_count": {"type": "integer", "minimum": 0},
    },
    "required": [
        "authoritative_facts",
        "effective_facts",
        "planning_scopes",
        "inspected_page_count",
    ],
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
        "reason",
    ],
    "anyOf": [
        {"required": ["status"]},
        {"required": ["value_policy"]},
        {"required": ["governed_values"]},
        {"required": ["governed_by"]},
    ],
    "additionalProperties": False,
}


AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "minItems": 0,
            "items": AUTHORITY_RECONCILIATION_DECISION_SCHEMA,
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA = deepcopy(
    AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA
)
_authority_agent_governed_by_item_schema = (
    AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA["properties"]["decisions"]["items"]
    ["properties"]["governed_by"]["items"]
)
_authority_agent_governed_by_item_schema["properties"].pop("directive_fact_id")
_authority_agent_governed_by_item_schema["properties"]["fact_id"] = {
    "type": "string",
    "minLength": 1,
}
_authority_agent_governed_by_item_schema["required"] = ["relation", "fact_id"]
_authority_agent_relation_schema = _authority_agent_governed_by_item_schema[
    "properties"
]["relation"]
_authority_agent_relation_schema["enum"] = [
    *_authority_agent_relation_schema["enum"],
    *GOVERNANCE_RELATION_ALIASES,
]


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
                    "allowed_block_ids": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
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
        {
            "required": [
                "source_kind",
                "requirement",
                "requirement_sha256",
                "source_scopes",
            ]
        },
    ],
    "additionalProperties": False,
}

# 文本通道把连续 3～4 页合并为一个任务；批次内仍保留每页完整真实坐标。
SOURCE_SEMANTICS_DOCUMENT_PAGE_SCHEMA = deepcopy(SOURCE_SEMANTICS_INPUT_SCHEMA)
SOURCE_SEMANTICS_DOCUMENT_PAGE_SCHEMA["properties"]["source_kind"] = {
    "type": "string",
    "const": "document",
}
SOURCE_SEMANTICS_DOCUMENT_PAGE_SCHEMA["oneOf"] = [
    deepcopy(SOURCE_SEMANTICS_DOCUMENT_PAGE_SCHEMA["oneOf"][0])
]
SOURCE_SEMANTICS_INPUT_SCHEMA["properties"]["source_kind"]["enum"].append(
    "document_batch"
)
SOURCE_SEMANTICS_INPUT_SCHEMA["properties"]["pages"] = {
    "type": "array",
    "minItems": 1,
    "maxItems": 4,
    "items": SOURCE_SEMANTICS_DOCUMENT_PAGE_SCHEMA,
}
SOURCE_SEMANTICS_INPUT_SCHEMA["oneOf"].append(
    {
        "required": [
            "source_kind",
            "document_id",
            "pages",
        ]
    }
)


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


def submit_source_semantics(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收模型提交的来源事实，参数与返回值共享同一严格契约。"""

    return deepcopy(arguments)


def submit_generation_batch(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收当前真实批次生成结果，参数与返回值共享严格用例契约。"""

    return deepcopy(arguments)


def submit_scenario_design_guidance(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收场景拆分建议，参数与返回值共享严格结构契约。"""

    return deepcopy(arguments)


def submit_business_plan(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收全局业务规划，参数与返回值共享严格规划契约。"""

    return deepcopy(arguments)


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
    # 中文注释：保留完整 evidence_catalog 作为审计和锚点事实源，压缩只生成
    # 供 source semantics 使用的 evidence ID 视图，避免把摘要或裁剪文本写成来源。
    run_input = getattr(context, "run_input", {})
    if not isinstance(run_input, dict):
        run_input = {}
    compression_enabled = context_compression_enabled(run_input)
    compression_max_tokens = context_compression_max_tokens(run_input)
    compressed_catalog, compression_stats = compress_evidence_catalog(
        evidence_catalog,
        enabled=compression_enabled,
        max_tokens=compression_max_tokens,
    )
    context.artifacts["context_compression"] = {
        **compression_stats,
        "source_kind": str(source.get("kind") or ""),
        "document_id": source.get("document_id"),
        "selected_evidence_ids": [
            str(item.get("evidence_id") or "")
            for item in (compressed_catalog.get("items") or [])
        ],
        "candidate_selected_evidence_ids": [
            str(item.get("evidence_id") or "")
            for item in (compressed_catalog.get("candidate_items") or [])
        ],
        "candidate_catalog_chars": sum(
            len(str(item.get("text") or ""))
            for item in (compressed_catalog.get("candidate_items") or [])
        ),
    }
    context.artifacts["requirement_evidence"] = {
        "source": source,
        "evidence_catalog": evidence_catalog,
    }
    return {
        "requirement": requirement,
        "source": source,
        "evidence_catalog": evidence_catalog,
    }


def _summary_item_text(value: Any) -> str:
    """把摘要字段中的文本或结构化风险统一为可读且稳定的文本。"""

    if not isinstance(value, dict):
        return str(value or "").strip()
    description = str(value.get("description") or "").strip()
    if not description:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    prefix = ""
    risk_id = str(value.get("risk_id") or "").strip()
    severity = str(value.get("severity") or "").strip()
    if risk_id:
        prefix = risk_id
    if severity:
        prefix = f"{prefix}（级别：{severity}）" if prefix else f"级别：{severity}"
    related_fact_ids = [
        str(fact_id).strip()
        for fact_id in list(value.get("related_fact_ids") or [])
        if str(fact_id).strip()
    ]
    suffix = f"（关联事实：{'、'.join(related_fact_ids)}）" if related_fact_ids else ""
    body = f"{prefix}：{description}" if prefix else description
    return f"{body}{suffix}"


def validate_business_plan_output(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验业务模块、测试点和测试方法的唯一性及完整性。"""

    output = arguments.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("business_modules"), list):
        raise ValueError("业务规划缺少 business_modules")
    input_payload = dict(arguments.get("input_payload") or {})
    partial_plans = list(input_payload.get("partial_plans") or [])
    if not partial_plans:
        raise ValueError("业务规划缺少已校验的分批规划草案")
    normalized = deepcopy(output)
    raw_group_catalog = input_payload.get("coverage_group_catalog")
    if raw_group_catalog is not None:
        group_catalog = list(raw_group_catalog or [])
        coverage_items_by_group_id: dict[str, list[str]] = {}
        for raw_group in group_catalog:
            group = dict(raw_group or {})
            group_id = _required_text(
                group.get("coverage_group_id"),
                "coverage_group_id",
            )
            if group_id in coverage_items_by_group_id:
                raise ValueError(f"覆盖语义组目录包含重复ID: {group_id}")
            coverage_items = [
                _required_text(value, "coverage_group.coverage_item")
                for value in list(group.get("coverage_items") or [])
            ]
            if not coverage_items:
                raise ValueError(f"覆盖语义组缺少原子覆盖项: {group_id}")
            coverage_items_by_group_id[group_id] = coverage_items
        if not coverage_items_by_group_id:
            raise ValueError("业务规划缺少覆盖语义组目录")
        used_group_ids: list[str] = []
        for module in list(normalized.get("business_modules") or []):
            for point in list(dict(module).get("test_points") or []):
                for design in list(dict(point).get("test_designs") or []):
                    coverage_group_ids = [
                        _required_text(value, "coverage_group_id")
                        for value in list(dict(design).get("coverage_items") or [])
                    ]
                    unknown_group_ids = set(coverage_group_ids) - set(
                        coverage_items_by_group_id
                    )
                    if unknown_group_ids:
                        raise ValueError(
                            "业务规划引用未知覆盖语义组: "
                            f"{sorted(unknown_group_ids)}"
                        )
                    used_group_ids.extend(coverage_group_ids)
                    design["coverage_items"] = [
                        coverage_item
                        for group_id in coverage_group_ids
                        for coverage_item in coverage_items_by_group_id[group_id]
                    ]
        duplicate_group_ids = sorted(
            group_id
            for group_id, count in Counter(used_group_ids).items()
            if count > 1
        )
        missing_group_ids = sorted(
            set(coverage_items_by_group_id) - set(used_group_ids)
        )
        if duplicate_group_ids or missing_group_ids:
            raise ValueError(
                "业务规划必须且只能承接一次全部覆盖语义组: "
                f"missing={missing_group_ids[:20]}, duplicate={duplicate_group_ids[:20]}"
            )
    planning_metadata = dict(input_payload.get("planning_metadata") or {})
    for field in ("coverage_focus", "risks"):
        values: list[str] = []
        metadata_values = planning_metadata.get(field)
        if metadata_values is not None:
            candidates = (
                [metadata_values]
                if isinstance(metadata_values, str)
                else list(metadata_values or [])
            )
            for candidate in candidates:
                value = _summary_item_text(candidate)
                if value and value not in values:
                    values.append(value)
        else:
            for raw_partial in partial_plans:
                draft = dict(dict(raw_partial or {}).get("draft") or {})
                raw_value = draft.get(field)
                candidates = [raw_value] if isinstance(raw_value, str) else list(raw_value or [])
                for candidate in candidates:
                    value = _summary_item_text(candidate)
                    if value and value not in values:
                        values.append(value)
        if not values and field == "coverage_focus":
            raise ValueError(f"分批规划草案缺少可编译字段: {field}")
        normalized[field] = values
    planning_limits = dict(input_payload.get("planning_limits") or {})
    required_limit_keys = {
        "max_business_modules",
        "max_test_points",
        "max_test_designs",
        "max_coverage_items",
    }
    if set(planning_limits) != required_limit_keys or any(
        not isinstance(planning_limits[key], int) or planning_limits[key] < 1
        for key in required_limit_keys
    ):
        raise ValueError("业务规划缺少平台计算的 planning_limits")
    module_names: set[str] = set()
    test_point_count = 0
    test_design_count = 0
    design_item_count = 0
    for module_index, raw_module in enumerate(normalized["business_modules"]):
        if not isinstance(raw_module, dict):
            raise ValueError(f"业务规划模块必须是对象: index={module_index}")
        module = dict(raw_module)
        module_name = _required_text(module.get("name"), "business_module.name")
        if module_name in module_names:
            raise ValueError(f"业务规划包含重复模块名称: {module_name}")
        module_names.add(module_name)
        test_points = list(module.get("test_points") or [])
        point_names: set[str] = set()
        for point_index, raw_point in enumerate(test_points):
            test_point_count += 1
            if not isinstance(raw_point, dict):
                raise ValueError(
                    f"业务规划测试点必须是对象: module={module_name}, index={point_index}"
                )
            point = dict(raw_point)
            point_name = _required_text(point.get("name"), "test_point.name")
            if point_name in point_names:
                raise ValueError(f"业务模块包含重复测试点: module={module_name}, point={point_name}")
            point_names.add(point_name)
            for design_index, raw_design in enumerate(list(point.get("test_designs") or [])):
                test_design_count += 1
                if not isinstance(raw_design, dict):
                    raise ValueError(
                        "测试点的 test_designs 每项必须是对象: "
                        f"module={module_name}, point={point_name}, index={design_index}"
                    )
                design = dict(raw_design)
                _required_text(design.get("technique"), "test_design.technique")
                _required_text(design.get("rationale"), "test_design.rationale")
                for coverage_intent in list(design.get("coverage_items") or []):
                    _required_text(coverage_intent, "test_design.coverage_item")
                    design_item_count += 1
    if design_item_count < 1:
        raise ValueError("业务规划没有形成任何测试设计覆盖项")
    actual_counts = {
        "max_business_modules": len(normalized["business_modules"]),
        "max_test_points": test_point_count,
        "max_test_designs": test_design_count,
        "max_coverage_items": design_item_count,
    }
    exceeded = [
        f"{key}={actual_counts[key]}/{planning_limits[key]}"
        for key in required_limit_keys
        if actual_counts[key] > planning_limits[key]
    ]
    if exceeded:
        raise ValueError(f"业务规划超过动态容量: {', '.join(sorted(exceeded))}")
    return normalized


def _serialized_json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _business_planning_limits(
    *,
    module_candidate_count: int,
    coverage_topic_count: int,
    covered_fact_count: int,
) -> dict[str, int]:
    """按已校验草案和事实目录计算规划容量，不假设覆盖项与用例一一对应。"""

    module_capacity = max(1, int(module_candidate_count))
    topic_capacity = max(module_capacity, int(coverage_topic_count))
    fact_capacity = max(topic_capacity, int(covered_fact_count))
    return {
        "max_business_modules": module_capacity,
        "max_test_points": topic_capacity,
        "max_test_designs": topic_capacity,
        "max_coverage_items": fact_capacity,
    }


def _planning_scope_fragments(planning_scopes: list[Any]) -> list[dict[str, Any]]:
    """按事实数和真实 JSON 体积拆分来源范围，保留事实原始顺序。"""

    fragments: list[dict[str, Any]] = []
    seen_scope_ids: set[str] = set()
    seen_fact_ids: set[str] = set()
    for raw_scope in planning_scopes:
        if not isinstance(raw_scope, dict):
            raise ValueError("业务规划输入的 planning_scopes 每项必须是对象")
        scope = dict(raw_scope)
        scope_id = _required_text(scope.get("scope_id"), "planning_scope.scope_id")
        if scope_id in seen_scope_ids:
            raise ValueError(f"业务规划输入包含重复 scope_id: {scope_id}")
        seen_scope_ids.add(scope_id)
        facts = list(scope.get("facts") or [])
        if not facts:
            raise ValueError(f"业务规划输入包含空事实范围: scope_id={scope_id}")
        fragment_facts: list[dict[str, Any]] = []
        for raw_fact in facts:
            if not isinstance(raw_fact, dict):
                raise ValueError(f"业务规划事实必须是对象: scope_id={scope_id}")
            fact = deepcopy(raw_fact)
            fact_id = _required_text(fact.get("fact_id"), "planning_scope.fact_id")
            if fact_id in seen_fact_ids:
                raise ValueError(f"业务规划输入包含重复 fact_id: {fact_id}")
            seen_fact_ids.add(fact_id)
            candidate = {
                "scope_id": scope_id,
                "facts": [*fragment_facts, fact],
            }
            if fragment_facts and (
                len(fragment_facts) >= BUSINESS_PLANNING_BATCH_MAX_FACTS
                or _serialized_json_chars(candidate) > BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS
            ):
                fragments.append({"scope_id": scope_id, "facts": fragment_facts})
                fragment_facts = []
            fragment_facts.append(fact)
        if fragment_facts:
            fragments.append({"scope_id": scope_id, "facts": fragment_facts})
    if not fragments:
        raise ValueError("业务规划缺少有效 planning_scopes")
    return fragments


def _planning_fact_model_view(fact: dict[str, Any]) -> dict[str, Any]:
    """生成规划/路由模型真正需要的事实投影。

    来源锚点、治理关系和状态字段仍由 source_semantics 及审计链路保留；
    规划模型只需要稳定 ID 与可读断言，重复传输其余字段会放大长请求。
    """

    fact_id = _required_text(fact.get("fact_id"), "planning_scope.fact_id")
    assertion = str(fact.get("assertion") or "").strip()
    return {"fact_id": fact_id, "assertion": assertion}


def prepare_business_plan_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把完整事实目录切成有界规划批次，避免单次模型请求跨越网关时限。"""

    planning_scopes = list(arguments.get("planning_scopes") or [])
    case_budget = int(arguments.get("case_budget") or 0)
    if case_budget < 1:
        raise ValueError("业务规划缺少有效 case_budget")
    if not planning_scopes or not all(isinstance(scope, dict) for scope in planning_scopes):
        raise ValueError("业务规划输入的 planning_scopes 每项必须是对象")
    raw_payload_chars = _serialized_json_chars({"planning_scopes": planning_scopes})
    model_scopes = [
        {
            "scope_id": _required_text(scope.get("scope_id"), "planning_scope.scope_id"),
            "facts": [
                _planning_fact_model_view(dict(fact))
                for fact in list(scope.get("facts") or [])
            ],
        }
        for scope in planning_scopes
    ]
    model_payload_chars = _serialized_json_chars({"planning_scopes": model_scopes})
    fragments = _planning_scope_fragments(model_scopes)
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_fact_count = 0
    for fragment in fragments:
        fragment_fact_count = len(list(fragment.get("facts") or []))
        candidate = [*current, fragment]
        if current and (
            current_fact_count + fragment_fact_count > BUSINESS_PLANNING_BATCH_MAX_FACTS
            or _serialized_json_chars({"planning_scopes": candidate})
            > BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS
        ):
            batches.append(current)
            current = []
            current_fact_count = 0
        current.append(fragment)
        current_fact_count += fragment_fact_count
    if current:
        batches.append(current)

    fact_count = sum(
        len(list(fragment.get("facts") or []))
        for batch in batches
        for fragment in batch
    )
    items = [
        {
            "planning_scopes": deepcopy(batch),
            "case_budget": case_budget,
            "planning_batch": {
                "batch_number": index + 1,
                "batch_count": len(batches),
                "scope_fragment_count": len(batch),
                "fact_count": sum(len(list(item.get("facts") or [])) for item in batch),
            },
        }
        for index, batch in enumerate(batches)
    ]
    context.artifacts["business_planning_batch_plan"] = {
        "batch_count": len(items),
        "scope_count": len(planning_scopes),
        "scope_fragment_count": len(fragments),
        "fact_count": fact_count,
        "max_facts_per_batch": BUSINESS_PLANNING_BATCH_MAX_FACTS,
        "max_json_chars_per_batch": BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS,
        "raw_model_input_chars": raw_payload_chars,
        "projected_model_input_chars": model_payload_chars,
        "model_input_reduction_ratio": round(
            (raw_payload_chars - model_payload_chars) / raw_payload_chars,
            6,
        ) if raw_payload_chars else 0.0,
        "model_fact_fields": ["fact_id", "assertion"],
        "removed_fact_fields": sorted(
            {
                str(key)
                for scope in planning_scopes
                if isinstance(scope, dict)
                for raw_fact in list(scope.get("facts") or [])
                if isinstance(raw_fact, dict)
                for key in raw_fact
                if key not in {"fact_id", "assertion"}
            }
        ),
    }
    return {
        "items": items,
        "batch_count": len(items),
        "scope_count": len(planning_scopes),
        "fact_count": fact_count,
    }


def validate_business_plan_draft_output(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """确保每个规划草案完整承接当前批次的全部真实事实。"""

    del context
    input_payload = dict(arguments.get("input_payload") or {})
    output = arguments.get("output")
    if not isinstance(output, dict):
        raise ValueError("业务规划批次未返回对象")
    expected_fact_ids = [
        _required_text(dict(fact).get("fact_id"), "planning_scope.fact_id")
        for raw_scope in list(input_payload.get("planning_scopes") or [])
        for fact in list(dict(raw_scope).get("facts") or [])
    ]
    if not expected_fact_ids or len(expected_fact_ids) != len(set(expected_fact_ids)):
        raise ValueError("业务规划批次事实为空或 fact_id 重复")
    candidates = output.get("module_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("业务规划批次缺少 module_candidates")
    candidate_names: set[str] = set()
    routed_fact_ids: list[str] = []
    for raw_candidate in candidates:
        if not isinstance(raw_candidate, dict):
            raise ValueError("业务规划批次 module_candidates 每项必须是对象")
        candidate = dict(raw_candidate)
        name = _required_text(candidate.get("name"), "module_candidate.name")
        if name in candidate_names:
            raise ValueError(f"业务规划批次包含重复模块候选: {name}")
        candidate_names.add(name)
        fact_ids = list(candidate.get("fact_ids") or [])
        if not fact_ids or len(fact_ids) != len(set(fact_ids)):
            raise ValueError(f"模块候选 fact_ids 为空或重复: module={name}")
        routed_fact_ids.extend(str(value) for value in fact_ids)
    expected_set = set(expected_fact_ids)
    routed_set = set(routed_fact_ids)
    if routed_set != expected_set:
        missing = sorted(expected_set - routed_set)
        unknown = sorted(routed_set - expected_set)
        raise ValueError(
            "业务规划批次没有完整承接真实事实: "
            f"missing={missing[:20]}, unknown={unknown[:20]}"
        )
    return deepcopy(output)


def prepare_business_plan_consolidation(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验批次草案并移除仅用于完整性审计的 fact_id，压缩全局汇总输入。"""

    prepared_items = list(arguments.get("prepared_items") or [])
    plan_records = list(arguments.get("plan_records") or [])
    case_budget = int(arguments.get("case_budget") or 0)
    if case_budget < 1:
        raise ValueError("业务规划汇总缺少有效 case_budget")
    if not prepared_items or len(prepared_items) != len(plan_records):
        raise ValueError("业务规划批次输入与结果数量不一致")
    partial_plans: list[dict[str, Any]] = []
    covered_fact_ids: set[str] = set()
    module_candidate_count = 0
    coverage_group_catalog: list[dict[str, Any]] = []
    assigned_topic_objectives: set[str] = set()
    coverage_topic_count = 0
    planning_metadata = {"coverage_focus": [], "risks": []}
    for index, (raw_prepared, raw_record) in enumerate(
        zip(prepared_items, plan_records, strict=True)
    ):
        prepared = dict(raw_prepared or {})
        record = dict(raw_record or {})
        if int(record.get("item_index", index)) != index:
            raise ValueError(f"业务规划批次记录顺序不一致: index={index}")
        output = validate_business_plan_draft_output(
            context,
            {"input_payload": prepared, "output": dict(record.get("output") or {})},
        )
        compact = deepcopy(output)
        for field in planning_metadata:
            raw_values = compact.pop(field, [])
            candidates = [raw_values] if isinstance(raw_values, str) else list(raw_values or [])
            for candidate in candidates:
                value = _summary_item_text(candidate)
                if value and value not in planning_metadata[field]:
                    planning_metadata[field].append(value)
        compact.pop("batch_summary", None)
        compact_candidates: list[dict[str, Any]] = []
        for candidate in list(compact.get("module_candidates") or []):
            coverage_items: list[str] = []
            for raw_topic in list(candidate.get("coverage_topics") or []):
                topic = dict(raw_topic or {})
                _required_text(topic.get("name"), "coverage_topic.name")
                objective = _required_text(
                    topic.get("objective"),
                    "coverage_topic.objective",
                )
                # 同一原子意图只保留第一次出现的候选归属。
                if objective not in assigned_topic_objectives:
                    coverage_items.append(objective)
                    assigned_topic_objectives.add(objective)
            covered_fact_ids.update(str(value) for value in list(candidate.get("fact_ids") or []))
            candidate.pop("fact_ids", None)
            if coverage_items:
                group_id = f"CG-{len(coverage_group_catalog) + 1:04d}"
                coverage_group_catalog.append(
                    {
                        "coverage_group_id": group_id,
                        "name": _required_text(candidate.get("name"), "module_candidate.name"),
                        "objective": _required_text(
                            candidate.get("objective"),
                            "module_candidate.objective",
                        ),
                        "coverage_items": coverage_items,
                    }
                )
                candidate["coverage_topics"] = [group_id]
                compact_candidates.append(candidate)
                module_candidate_count += 1
                coverage_topic_count += len(coverage_items)
        compact["module_candidates"] = compact_candidates
        partial_plans.append(
            {
                "batch_number": index + 1,
                "batch_count": len(plan_records),
                "draft": compact,
            }
        )
    context.artifacts["business_planning_batch_results"] = {
        "batch_count": len(partial_plans),
        "covered_fact_count": len(covered_fact_ids),
    }
    planning_limits = _business_planning_limits(
        module_candidate_count=module_candidate_count,
        coverage_topic_count=coverage_topic_count,
        covered_fact_count=len(covered_fact_ids),
    )
    return {
        "partial_plans": partial_plans,
        "planning_metadata": planning_metadata,
        "coverage_group_catalog": coverage_group_catalog,
        "batch_count": len(partial_plans),
        "covered_fact_count": len(covered_fact_ids),
        "case_budget": case_budget,
        "planning_limits": planning_limits,
    }


def _planning_module_design_catalog(module: dict[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for raw_point in list(module.get("test_points") or []):
        point = dict(raw_point or {})
        for raw_design in list(point.get("test_designs") or []):
            design = dict(raw_design or {})
            for coverage_intent in list(design.get("coverage_items") or []):
                catalog.append(
                    {
                        "test_design_item_index": len(catalog),
                        "test_point": str(point.get("name") or ""),
                        "technique": str(design.get("technique") or ""),
                        "coverage_intent": str(coverage_intent or ""),
                    }
                )
    if not catalog:
        raise ValueError(f"业务模块缺少测试设计项: module={module.get('name')}")
    return catalog


def prepare_planning_scope_routes(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把每个有效 scope 变成模块与测试设计联合路由任务。"""

    plan = dict(arguments.get("plan") or {})
    modules = list(plan.get("business_modules") or [])
    scopes = list(arguments.get("planning_scopes") or [])
    if not modules or not all(isinstance(module, dict) for module in modules):
        raise ValueError("规划路由缺少有效 business_modules")
    if not scopes or not all(isinstance(scope, dict) for scope in scopes):
        raise ValueError("规划路由缺少有效 planning_scopes")
    artifacts = getattr(context, "artifacts", None)
    if not isinstance(artifacts, dict):
        artifacts = {}
        try:
            context.artifacts = artifacts
        except AttributeError:
            pass
    module_catalog = [
        {
            "module_index": index,
            "name": str(module.get("name") or ""),
            "objective": str(module.get("objective") or ""),
            "test_design_items": _planning_module_design_catalog(module),
        }
        for index, module in enumerate(modules)
    ]
    items = [
        {
            "scope_id": str(scope.get("scope_id") or ""),
            "facts": deepcopy(list(scope.get("facts") or [])),
            "business_modules": deepcopy(module_catalog),
        }
        for scope in scopes
    ]
    if any(not item["scope_id"] or not item["facts"] for item in items):
        raise ValueError("planning_scopes 包含空 scope_id 或空事实集")
    if len({item["scope_id"] for item in items}) != len(items):
        raise ValueError("planning_scopes 包含重复 scope_id")
    model_items = [
        {
            "scope_id": item["scope_id"],
            "facts": [
                {
                    **_planning_fact_model_view(dict(fact)),
                    "fact_ref": f"RF-{fact_index + 1:03d}",
                }
                for fact_index, fact in enumerate(item["facts"])
            ],
        }
        for item in items
    ]

    batch_indexes: list[list[int]] = []
    current_indexes: list[int] = []
    for item_index, model_item in enumerate(model_items):
        candidate_indexes = [*current_indexes, item_index]
        candidate_chars = _serialized_json_chars(
            {
                "scopes": [model_items[index] for index in candidate_indexes],
                "business_modules": module_catalog,
            }
        )
        if current_indexes and (
            len(candidate_indexes) > PLANNING_SCOPE_ROUTE_BATCH_SIZE
            or candidate_chars > PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS
        ):
            batch_indexes.append(current_indexes)
            current_indexes = [item_index]
            continue
        current_indexes = candidate_indexes
    if current_indexes:
        batch_indexes.append(current_indexes)

    raw_batch_payload_chars = sum(
        _serialized_json_chars(
            {
                "scopes": [items[index] for index in indexes],
                "business_modules": module_catalog,
            }
        )
        for indexes in batch_indexes
    )
    model_batch_payload_chars = sum(
        _serialized_json_chars(
            {
                "scopes": [model_items[index] for index in indexes],
                "business_modules": module_catalog,
            }
        )
        for indexes in batch_indexes
    )
    batch_items = [
        {
            "scopes": [
                {
                    "scope_id": model_items[index]["scope_id"],
                    "facts": deepcopy(model_items[index]["facts"]),
                }
                for index in indexes
            ],
            "business_modules": deepcopy(module_catalog),
        }
        for indexes in batch_indexes
    ]
    artifacts["planning_scope_route_plan"] = {
        "scope_count": len(items),
        "batch_count": len(batch_items),
        "module_count": len(modules),
        "max_model_input_chars": PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS,
        "max_scopes_per_batch": PLANNING_SCOPE_ROUTE_BATCH_SIZE,
        "oversized_single_scope_count": sum(
            len(indexes) == 1
            and _serialized_json_chars(
                {
                    "scopes": [model_items[indexes[0]]],
                    "business_modules": module_catalog,
                }
            ) > PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS
            for indexes in batch_indexes
        ),
        "raw_batch_model_input_chars": raw_batch_payload_chars,
        "projected_batch_model_input_chars": model_batch_payload_chars,
        "model_input_reduction_ratio": round(
            (raw_batch_payload_chars - model_batch_payload_chars) / raw_batch_payload_chars,
            6,
        ) if raw_batch_payload_chars else 0.0,
        "removed_module_fields": ["test_points"],
        "model_fact_fields": ["fact_ref", "fact_id", "assertion"],
    }
    return {
        "items": items,
        "batch_items": batch_items,
        "scope_count": len(items),
        "batch_count": len(batch_items),
        "module_count": len(modules),
    }


def _normalize_planning_scope_route(
    *,
    prepared: dict[str, Any],
    raw_output: dict[str, Any],
) -> dict[str, Any]:
    """依据单项真实输入规范化路由，并完成可在当前项修复的确定性校验。"""

    output = deepcopy(raw_output)
    expected_scope_id = str(prepared.get("scope_id") or "")
    if str(output.get("scope_id") or "") != expected_scope_id:
        raise ValueError(f"规划路由结果篡改 scope_id: scope_id={expected_scope_id}")
    module_catalog = prepared.get("business_modules")
    if not isinstance(module_catalog, list) or not module_catalog:
        raise ValueError(f"规划路由输入缺少模块目录: scope_id={expected_scope_id}")
    module_indexes = [
        module.get("module_index") if isinstance(module, dict) else None
        for module in module_catalog
    ]
    if module_indexes != list(range(len(module_catalog))):
        raise ValueError(f"规划路由输入模块目录下标不连续: scope_id={expected_scope_id}")
    prepared_facts = [dict(fact) for fact in list(prepared.get("facts") or [])]
    prepared_fact_ids = [
        str(fact.get("fact_id") or "").strip() for fact in prepared_facts
    ]
    if not all(prepared_fact_ids) or len(prepared_fact_ids) != len(set(prepared_fact_ids)):
        raise ValueError(f"规划路由输入 fact_id 为空或重复: scope_id={expected_scope_id}")
    prepared_fact_refs = [
        str(fact.get("fact_ref") or "").strip() for fact in prepared_facts
    ]
    uses_fact_refs = all(prepared_fact_refs)
    if uses_fact_refs and len(prepared_fact_refs) != len(set(prepared_fact_refs)):
        raise ValueError(f"规划路由输入 fact_ref 重复: scope_id={expected_scope_id}")
    fact_id_by_ref = dict(zip(prepared_fact_refs, prepared_fact_ids, strict=True))
    assignments = output.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("规划路由 assignments 必须是数组")
    assignments_by_fact_id: dict[str, dict[str, Any]] = {}
    for assignment_index, raw_assignment in enumerate(assignments):
        if not isinstance(raw_assignment, dict):
            raise ValueError(
                f"规划路由 assignment 必须是对象: index={assignment_index}"
            )
        assignment = dict(raw_assignment)
        fact_ref = str(assignment.get("fact_ref") or "").strip()
        submitted_fact_id = str(assignment.get("fact_id") or "").strip()
        if uses_fact_refs and fact_ref:
            fact_id = fact_id_by_ref.get(fact_ref, "")
        elif uses_fact_refs and submitted_fact_id in prepared_fact_ids:
            # agent_map 后处理结果会在后续合并工具中再次经过本函数；
            # 已恢复且仍属于当前 scope 的真实 fact_id 应保持幂等。
            fact_id = submitted_fact_id
        else:
            fact_id = submitted_fact_id
        if not fact_id or fact_id in assignments_by_fact_id:
            raise ValueError(
                "规划路由必须逐条且仅路由当前 scope 的全部 fact_id: "
                f"scope_id={expected_scope_id}; fact_ref={fact_ref or None}"
            )
        assignment.pop("fact_ref", None)
        assignment["fact_id"] = fact_id
        raw_module_routes = assignment.get("module_routes")
        if not isinstance(raw_module_routes, list) or not raw_module_routes:
            raise ValueError(f"规划路由缺少模块与测试设计映射: fact_id={fact_id}")
        module_routes_by_index: dict[int, dict[str, Any]] = {}
        primary_count = 0
        for raw_module_route in raw_module_routes:
            if not isinstance(raw_module_route, dict):
                raise ValueError(f"规划路由模块映射必须是对象: fact_id={fact_id}")
            module_route = dict(raw_module_route)
            module_index = module_route.get("module_index")
            relation = str(module_route.get("relation") or "")
            design_indexes = module_route.get("test_design_item_indexes")
            if (
                not isinstance(module_index, int)
                or isinstance(module_index, bool)
                or not 0 <= module_index < len(module_catalog)
                or relation not in {"primary", "shared"}
                or not isinstance(design_indexes, list)
                or len(design_indexes) != len(set(design_indexes))
            ):
                raise ValueError(f"规划路由模块或测试设计映射无效: fact_id={fact_id}")
            design_catalog = list(
                dict(module_catalog[module_index]).get("test_design_items") or []
            )
            if any(
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(design_catalog)
                for index in design_indexes
            ):
                raise ValueError(
                    f"规划路由测试设计项下标越界: fact_id={fact_id}, module_index={module_index}"
                )
            primary_count += int(relation == "primary")
            existing_route = module_routes_by_index.get(module_index)
            if existing_route is None:
                module_routes_by_index[module_index] = {
                    "module_index": module_index,
                    "relation": relation,
                    "test_design_item_indexes": list(design_indexes),
                }
                continue
            existing_route["relation"] = (
                "primary"
                if "primary" in {existing_route["relation"], relation}
                else "shared"
            )
            existing_route["test_design_item_indexes"] = sorted(
                set(existing_route["test_design_item_indexes"]) | set(design_indexes)
            )
        if primary_count != 1:
            raise ValueError(f"规划路由必须且只能包含一个主模块: fact_id={fact_id}")
        module_routes = list(module_routes_by_index.values())
        assignment["module_routes"] = sorted(
            module_routes,
            key=lambda item: (item["relation"] != "primary", item["module_index"]),
        )
        assignments_by_fact_id[fact_id] = assignment
    missing_fact_ids = sorted(set(prepared_fact_ids) - set(assignments_by_fact_id))
    unknown_fact_ids = sorted(set(assignments_by_fact_id) - set(prepared_fact_ids))
    if missing_fact_ids or unknown_fact_ids:
        raise ValueError(
            "规划路由必须逐条且仅路由当前 scope 的全部 fact_id: "
            f"scope_id={expected_scope_id}; missing={missing_fact_ids}; "
            f"unknown={unknown_fact_ids}"
        )
    output["assignments"] = [
        assignments_by_fact_id[fact_id] for fact_id in prepared_fact_ids
    ]
    return output


def _normalize_planning_scope_route_batch(
    *,
    prepared: dict[str, Any],
    raw_output: dict[str, Any],
) -> dict[str, Any]:
    """按输入顺序拆解批量路由，并复用逐 scope 的严格校验。"""

    scopes = list(prepared.get("scopes") or [])
    module_catalog = list(prepared.get("business_modules") or [])
    routes = raw_output.get("routes")
    if (
        not scopes
        or len(scopes) > PLANNING_SCOPE_ROUTE_BATCH_SIZE
        or not isinstance(routes, list)
        or len(routes) != len(scopes)
    ):
        raise ValueError("规划路由批次必须逐项返回全部 scope")
    routes_by_scope_id: dict[str, dict[str, Any]] = {}
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("规划路由批次 routes 只能包含对象")
        scope_id = str(route.get("scope_id") or "")
        if not scope_id or scope_id in routes_by_scope_id:
            raise ValueError("规划路由批次包含空或重复 scope_id")
        routes_by_scope_id[scope_id] = route

    normalized_routes: list[dict[str, Any]] = []
    for raw_scope in scopes:
        scope = dict(raw_scope or {})
        scope_id = str(scope.get("scope_id") or "")
        route = routes_by_scope_id.get(scope_id)
        if route is None:
            raise ValueError(f"规划路由批次遗漏 scope_id: {scope_id}")
        normalized_routes.append(
            _normalize_planning_scope_route(
                prepared={
                    "scope_id": scope_id,
                    "facts": deepcopy(list(scope.get("facts") or [])),
                    "business_modules": deepcopy(module_catalog),
                },
                raw_output=route,
            )
        )
    if set(routes_by_scope_id) != {
        str(dict(scope).get("scope_id") or "") for scope in scopes
    }:
        raise ValueError("规划路由批次引用输入外 scope_id")
    return {"routes": normalized_routes}


def postprocess_planning_scope_routing_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """在单个路由实例完成时规范化输出并校验其真实输入边界。"""

    del context
    prepared = dict(arguments.get("item_input") or {})
    raw_output = dict(arguments.get("item_output") or {})
    if "scopes" in prepared:
        return _normalize_planning_scope_route_batch(
            prepared=prepared,
            raw_output=raw_output,
        )
    return _normalize_planning_scope_route(
        prepared=prepared,
        raw_output=raw_output,
    )


def _expand_planning_scope_route_batches(
    *,
    prepared_items: list[Any],
    route_records: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把模型批次恢复为既有的逐 scope 路由数据流。"""

    if not prepared_items or "scopes" not in dict(prepared_items[0] or {}):
        return (
            [dict(item) for item in prepared_items],
            [dict(record) for record in route_records],
        )
    if len(prepared_items) != len(route_records):
        raise ValueError("规划路由批次结果数量不一致")

    expanded_items: list[dict[str, Any]] = []
    expanded_records: list[dict[str, Any]] = []
    for batch_index, (raw_prepared, raw_record) in enumerate(
        zip(prepared_items, route_records, strict=True)
    ):
        prepared = dict(raw_prepared or {})
        record = dict(raw_record or {})
        if int(record.get("item_index", batch_index)) != batch_index:
            raise ValueError(f"规划路由批次记录顺序不一致: index={batch_index}")
        normalized = _normalize_planning_scope_route_batch(
            prepared=prepared,
            raw_output=dict(record.get("output") or {}),
        )
        module_catalog = list(prepared.get("business_modules") or [])
        for raw_scope, route in zip(
            list(prepared.get("scopes") or []),
            normalized["routes"],
            strict=True,
        ):
            scope = dict(raw_scope or {})
            expanded_items.append(
                {
                    "scope_id": str(scope.get("scope_id") or ""),
                    "facts": deepcopy(list(scope.get("facts") or [])),
                    "business_modules": deepcopy(module_catalog),
                }
            )
            expanded_records.append(
                {
                    "item_index": len(expanded_records),
                    "output": deepcopy(route),
                }
            )
    return expanded_items, expanded_records


def _collect_planning_scope_route_state(
    *,
    plan: dict[str, Any],
    prepared_items: list[Any],
    route_records: list[Any],
) -> dict[str, Any]:
    """统一解析初始路由，供缺口审计和最终合并复用。"""

    prepared_items, route_records = _expand_planning_scope_route_batches(
        prepared_items=prepared_items,
        route_records=route_records,
    )
    draft_plan = deepcopy(plan)
    modules = [dict(module) for module in list(draft_plan.get("business_modules") or [])]
    if len(prepared_items) != len(route_records):
        raise ValueError("规划路由结果数量与有效 scope 数量不一致")

    facts_by_scope: dict[str, list[dict[str, Any]]] = {}
    fact_scope_by_id: dict[str, str] = {}
    for item_index, prepared in enumerate(prepared_items):
        if not isinstance(prepared, dict):
            raise ValueError(f"规划路由输入必须是对象: index={item_index}")
        scope_id = str(prepared.get("scope_id") or "").strip()
        facts = list(prepared.get("facts") or [])
        if not scope_id or not facts:
            raise ValueError(f"规划路由输入缺少 scope 或事实: index={item_index}")
        normalized_facts: list[dict[str, Any]] = []
        for raw_fact in facts:
            if not isinstance(raw_fact, dict):
                raise ValueError(f"规划路由事实必须是对象: scope_id={scope_id}")
            fact = deepcopy(raw_fact)
            fact_id = str(fact.get("fact_id") or "").strip()
            if not fact_id or fact_id in fact_scope_by_id:
                raise ValueError(f"规划路由事实 ID 为空或重复: fact_id={fact_id}")
            fact_scope_by_id[fact_id] = scope_id
            normalized_facts.append(fact)
        facts_by_scope[scope_id] = normalized_facts

    evidence_by_module: list[list[str]] = [[] for _ in modules]
    facts_by_module: list[list[str]] = [[] for _ in modules]
    fact_design_routes_by_module: list[list[dict[str, Any]]] = [
        [] for _ in modules
    ]
    for item_index, (prepared, record) in enumerate(zip(prepared_items, route_records)):
        if not isinstance(record, dict):
            raise ValueError(f"规划路由记录必须是对象: index={item_index}")
        if int(record.get("item_index", item_index)) != item_index:
            raise ValueError(f"规划路由记录顺序不一致: index={item_index}")
        output = _normalize_planning_scope_route(
            prepared=dict(prepared),
            raw_output=dict(record.get("output") or {}),
        )
        scope_id = str(output["scope_id"])
        for assignment in output["assignments"]:
            fact_id = str(assignment["fact_id"])
            for module_route in list(assignment["module_routes"]):
                module_index = int(module_route["module_index"])
                if scope_id not in evidence_by_module[module_index]:
                    evidence_by_module[module_index].append(scope_id)
                if fact_id not in facts_by_module[module_index]:
                    facts_by_module[module_index].append(fact_id)
                fact_design_routes_by_module[module_index].append(
                    {
                        "fact_id": fact_id,
                        "test_design_item_indexes": list(
                            module_route["test_design_item_indexes"]
                        ),
                    }
                )

    return {
        "draft_plan": draft_plan,
        "modules": modules,
        "facts_by_scope": facts_by_scope,
        "fact_scope_by_id": fact_scope_by_id,
        "evidence_by_module": evidence_by_module,
        "facts_by_module": facts_by_module,
        "fact_design_routes_by_module": fact_design_routes_by_module,
    }


def _missing_planning_design_indexes(state: dict[str, Any]) -> dict[int, list[int]]:
    modules = list(state["modules"])
    facts_by_module = list(state["facts_by_module"])
    routes_by_module = list(state["fact_design_routes_by_module"])
    missing_by_module: dict[int, list[int]] = {}
    for module_index, module_fact_ids in enumerate(facts_by_module):
        if not module_fact_ids:
            continue
        expected = set(range(len(_planning_module_design_catalog(modules[module_index]))))
        routed = {
            int(design_index)
            for route in routes_by_module[module_index]
            for design_index in list(route["test_design_item_indexes"])
        }
        missing = sorted(expected - routed)
        if missing:
            missing_by_module[module_index] = missing
    return missing_by_module


def prepare_planning_route_repairs(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把全局路由缺口收敛为按模块复核的最小真实事实集。"""

    del context
    state = _collect_planning_scope_route_state(
        plan=dict(arguments.get("plan") or {}),
        prepared_items=list(arguments.get("prepared_items") or []),
        route_records=list(arguments.get("route_records") or []),
    )
    modules = list(state["modules"])
    facts_by_scope = dict(state["facts_by_scope"])
    evidence_by_module = list(state["evidence_by_module"])
    routes_by_module = list(state["fact_design_routes_by_module"])
    missing_by_module = _missing_planning_design_indexes(state)
    items: list[dict[str, Any]] = []
    for module_index, missing_indexes in missing_by_module.items():
        design_catalog = _planning_module_design_catalog(modules[module_index])
        routed_indexes_by_fact = {
            str(route["fact_id"]): list(route["test_design_item_indexes"])
            for route in routes_by_module[module_index]
        }
        candidate_facts: list[dict[str, Any]] = []
        for scope_id in evidence_by_module[module_index]:
            for fact in facts_by_scope[scope_id]:
                fact_id = str(fact["fact_id"])
                candidate_facts.append(
                    {
                        "scope_id": scope_id,
                        "fact_id": fact_id,
                        "assertion": str(fact.get("assertion") or ""),
                        "current_test_design_item_indexes": routed_indexes_by_fact.get(
                            fact_id, []
                        ),
                    }
                )
        if not candidate_facts:
            raise ValueError(
                "规划路由缺口没有可复核的真实事实: "
                f"module={modules[module_index].get('name')}"
            )
        items.append(
            {
                "module_index": module_index,
                "module_name": str(modules[module_index].get("name") or ""),
                "module_objective": str(modules[module_index].get("objective") or ""),
                "missing_test_design_items": [
                    deepcopy(design_catalog[index]) for index in missing_indexes
                ],
                "candidate_facts": candidate_facts,
            }
        )
    return {
        "items": items,
        "gap_module_count": len(items),
        "gap_design_item_count": sum(len(indexes) for indexes in missing_by_module.values()),
    }


def _normalize_planning_route_repair(
    *,
    prepared: dict[str, Any],
    raw_output: dict[str, Any],
) -> dict[str, Any]:
    output = deepcopy(raw_output)
    expected_module_index = int(prepared.get("module_index", -1))
    if output.get("module_index") != expected_module_index:
        raise ValueError(
            f"规划路由缺口复核篡改 module_index: module_index={expected_module_index}"
        )
    expected_design_indexes = {
        int(item["test_design_item_index"])
        for item in list(prepared.get("missing_test_design_items") or [])
        if isinstance(item, dict)
    }
    candidate_fact_ids = {
        str(fact.get("fact_id") or "")
        for fact in list(prepared.get("candidate_facts") or [])
        if isinstance(fact, dict)
    }
    decisions = output.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("规划路由缺口复核 decisions 必须是非空数组")
    normalized_by_index: dict[int, dict[str, Any]] = {}
    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            raise ValueError("规划路由缺口复核 decision 必须是对象")
        decision = dict(raw_decision)
        design_index = decision.get("test_design_item_index")
        if (
            not isinstance(design_index, int)
            or isinstance(design_index, bool)
            or design_index not in expected_design_indexes
            or design_index in normalized_by_index
        ):
            raise ValueError("规划路由缺口复核包含未知或重复测试设计项")
        disposition = str(decision.get("disposition") or "")
        fact_ids = decision.get("fact_ids")
        reason = str(decision.get("reason") or "").strip()
        if (
            disposition not in {"supported", "unsupported"}
            or not isinstance(fact_ids, list)
            or len(fact_ids) != len(set(fact_ids))
            or any(str(fact_id) not in candidate_fact_ids for fact_id in fact_ids)
            or not reason
        ):
            raise ValueError(
                f"规划路由缺口复核结论无效: test_design_item_index={design_index}"
            )
        if disposition == "supported" and not fact_ids:
            raise ValueError("规划路由缺口复核支持结论必须引用真实 fact_id")
        if disposition == "unsupported" and fact_ids:
            raise ValueError("规划路由缺口复核不支持结论不能引用 fact_id")
        normalized_by_index[design_index] = {
            "test_design_item_index": design_index,
            "disposition": disposition,
            "fact_ids": [str(fact_id) for fact_id in fact_ids],
            "reason": reason,
        }
    if set(normalized_by_index) != expected_design_indexes:
        raise ValueError("规划路由缺口复核未逐项审查全部缺失测试设计项")
    output["decisions"] = [
        normalized_by_index[index] for index in sorted(normalized_by_index)
    ]
    return output


def postprocess_planning_route_repair_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验缺口复核只能引用当前输入中的模块、设计项和真实事实。"""

    del context
    return _normalize_planning_route_repair(
        prepared=dict(arguments.get("item_input") or {}),
        raw_output=dict(arguments.get("item_output") or {}),
    )


def _prune_unsupported_planning_design_items(
    *,
    modules: list[dict[str, Any]],
    routes_by_module: list[list[dict[str, Any]]],
    unsupported_by_module: dict[int, set[int]],
) -> int:
    """删除无事实支持的原子覆盖项，并重排同模块内的确定性索引。"""

    removed_count = 0
    for module_index, unsupported_indexes in unsupported_by_module.items():
        module = modules[module_index]
        old_index = 0
        new_index = 0
        index_mapping: dict[int, int] = {}
        retained_points: list[dict[str, Any]] = []
        for raw_point in list(module.get("test_points") or []):
            point = deepcopy(dict(raw_point or {}))
            retained_designs: list[dict[str, Any]] = []
            for raw_design in list(point.get("test_designs") or []):
                design = deepcopy(dict(raw_design or {}))
                retained_coverage: list[Any] = []
                for coverage_intent in list(design.get("coverage_items") or []):
                    if old_index in unsupported_indexes:
                        removed_count += 1
                    else:
                        index_mapping[old_index] = new_index
                        new_index += 1
                        retained_coverage.append(coverage_intent)
                    old_index += 1
                if retained_coverage:
                    design["coverage_items"] = retained_coverage
                    retained_designs.append(design)
            if retained_designs:
                point["test_designs"] = retained_designs
                retained_points.append(point)
        if not retained_points:
            raise ValueError(
                "业务模块的全部测试设计项均缺少真实事实支持: "
                f"module={module.get('name')}"
            )
        module["test_points"] = retained_points
        for route in routes_by_module[module_index]:
            route["test_design_item_indexes"] = sorted(
                {
                    index_mapping[index]
                    for index in list(route.get("test_design_item_indexes") or [])
                    if index in index_mapping
                }
            )
    return removed_count


def merge_planning_scope_routes(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """严格合并逐事实路由，并为业务模块写入确定性的事实与证据 ID。"""

    prepared_items = list(arguments.get("prepared_items") or [])
    route_records = list(arguments.get("route_records") or [])
    state = _collect_planning_scope_route_state(
        plan=dict(arguments.get("plan") or {}),
        prepared_items=prepared_items,
        route_records=route_records,
    )
    draft_plan = state["draft_plan"]
    modules = state["modules"]
    fact_scope_by_id = state["fact_scope_by_id"]
    evidence_by_module = state["evidence_by_module"]
    facts_by_module = state["facts_by_module"]
    fact_design_routes_by_module = state["fact_design_routes_by_module"]
    repair_input = prepare_planning_route_repairs(
        context,
        {
            "plan": arguments.get("plan"),
            "prepared_items": prepared_items,
            "route_records": route_records,
        },
    )
    repair_items = list(repair_input["items"])
    if repair_items and "repair_records" not in arguments:
        first_gap = repair_items[0]
        raise ValueError(
            "规划路由没有承接模块的全部测试设计项: "
            f"module={first_gap.get('module_name')}, "
            "missing="
            f"{[item['test_design_item_index'] for item in first_gap['missing_test_design_items']]}"
        )
    repair_records = list(arguments.get("repair_records") or [])
    if len(repair_items) != len(repair_records):
        raise ValueError("规划路由缺口复核结果数量不一致")
    repaired_design_item_count = 0
    unsupported_by_module: dict[int, set[int]] = {}
    unsupported_decisions: list[dict[str, Any]] = []
    for item_index, (prepared, record) in enumerate(zip(repair_items, repair_records)):
        if not isinstance(record, dict) or int(record.get("item_index", item_index)) != item_index:
            raise ValueError(f"规划路由缺口复核记录顺序不一致: index={item_index}")
        output = _normalize_planning_route_repair(
            prepared=prepared,
            raw_output=dict(record.get("output") or {}),
        )
        module_index = int(output["module_index"])
        for decision in output["decisions"]:
            design_index = int(decision["test_design_item_index"])
            if decision["disposition"] == "unsupported":
                unsupported_by_module.setdefault(module_index, set()).add(design_index)
                unsupported_decisions.append(
                    {
                        "module": str(modules[module_index].get("name") or ""),
                        "design_index": design_index,
                        "reason": str(decision["reason"]),
                    }
                )
                continue
            repaired_design_item_count += 1
            for fact_id in decision["fact_ids"]:
                scope_id = str(fact_scope_by_id[fact_id])
                if scope_id not in evidence_by_module[module_index]:
                    evidence_by_module[module_index].append(scope_id)
                if fact_id not in facts_by_module[module_index]:
                    facts_by_module[module_index].append(fact_id)
                route = next(
                    (
                        item for item in fact_design_routes_by_module[module_index]
                        if item["fact_id"] == fact_id
                    ),
                    None,
                )
                if route is None:
                    route = {"fact_id": fact_id, "test_design_item_indexes": []}
                    fact_design_routes_by_module[module_index].append(route)
                if design_index not in route["test_design_item_indexes"]:
                    route["test_design_item_indexes"].append(design_index)
                    route["test_design_item_indexes"].sort()
    removed_unsupported_design_item_count = _prune_unsupported_planning_design_items(
        modules=modules,
        routes_by_module=fact_design_routes_by_module,
        unsupported_by_module=unsupported_by_module,
    )
    remaining_gaps = _missing_planning_design_indexes(state)
    if remaining_gaps:
        module_index = next(iter(remaining_gaps))
        raise ValueError(
            "规划路由没有承接模块的全部测试设计项: "
            f"module={modules[module_index].get('name')}, "
            f"missing={remaining_gaps[module_index]}"
        )
    active_module_indexes = [
        index for index, module_fact_ids in enumerate(facts_by_module) if module_fact_ids
    ]
    routed_modules = [
        {
            **modules[index],
            "evidence_ids": evidence_by_module[index],
            "fact_ids": facts_by_module[index],
            "fact_design_routes": fact_design_routes_by_module[index],
        }
        for index in active_module_indexes
    ]
    if not routed_modules:
        raise ValueError("规划路由没有形成任何受真实证据支持的业务模块")
    draft_plan["business_modules"] = routed_modules
    assignment_counts = {
        fact_id: sum(fact_id in module_fact_ids for module_fact_ids in facts_by_module)
        for fact_id in {
            fact_id for module_fact_ids in facts_by_module for fact_id in module_fact_ids
        }
    }
    facts_with_design_routes = {
        str(route["fact_id"])
        for routes in fact_design_routes_by_module
        for route in routes
        if route["test_design_item_indexes"]
    }
    unmatched_test_design_fact_ids = [
        fact_id
        for fact_id in fact_scope_by_id
        if fact_id in assignment_counts and fact_id not in facts_with_design_routes
    ]
    context.artifacts["planning_fact_routes"] = {
        "input_module_count": len(modules),
        "routed_module_count": len(routed_modules),
        "fact_count": len(assignment_counts),
        "fact_assignment_count": sum(assignment_counts.values()),
        "shared_fact_count": sum(count > 1 for count in assignment_counts.values()),
        "max_fact_reuse": max(assignment_counts.values(), default=0),
        "fact_design_assignment_count": sum(
            len(route["test_design_item_indexes"])
            for routes in fact_design_routes_by_module
            for route in routes
        ),
        "multi_design_fact_route_count": sum(
            len(route["test_design_item_indexes"]) > 1
            for routes in fact_design_routes_by_module
            for route in routes
        ),
        "initial_gap_module_count": int(repair_input["gap_module_count"]),
        "initial_gap_design_item_count": int(repair_input["gap_design_item_count"]),
        "route_repair_record_count": len(repair_records),
        "repaired_design_item_count": repaired_design_item_count,
        "removed_unsupported_design_item_count": removed_unsupported_design_item_count,
        "unsupported_design_items": unsupported_decisions,
        "unmatched_test_design_fact_count": len(unmatched_test_design_fact_ids),
        "unmatched_test_design_fact_ids": unmatched_test_design_fact_ids,
    }
    return draft_plan


def validate_scenario_design_guidance(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验场景拆分只引用当前批次的真实事实和测试设计项。"""

    input_payload = dict(arguments.get("input_payload") or {})
    output = arguments.get("output")
    if not isinstance(output, dict):
        raise ValueError("场景拆分专业 Agent 输出必须是对象")
    case_budget = int(input_payload.get("case_budget") or 0)
    recommended_case_count = int(output.get("recommended_case_count") or 0)
    scenario_groups = [
        dict(item)
        for item in list(output.get("scenario_groups") or [])
        if isinstance(item, dict)
    ]
    if case_budget < 1 or recommended_case_count > case_budget:
        raise ValueError(
            "场景拆分建议数量超过当前批次用例额度: "
            f"recommended={recommended_case_count}, case_budget={case_budget}"
        )
    if len(scenario_groups) > case_budget:
        raise ValueError(
            "场景拆分分组数量超过当前批次用例额度: "
            f"groups={len(scenario_groups)}, case_budget={case_budget}"
        )

    allowed_fact_ids = {
        str(fact.get("fact_id") or "").strip()
        for fact in list(input_payload.get("authoritative_facts") or [])
        if isinstance(fact, dict) and str(fact.get("fact_id") or "").strip()
    }
    plan = dict(input_payload.get("plan") or {})
    allowed_design_item_ids = {
        str(item.get("test_design_item_id") or "").strip()
        for item in list(plan.get("test_design_items") or [])
        if isinstance(item, dict) and str(item.get("test_design_item_id") or "").strip()
    }
    scenario_keys: set[str] = set()
    for group in scenario_groups:
        scenario_key = str(group.get("scenario_key") or "").strip()
        if scenario_key in scenario_keys:
            raise ValueError(f"场景拆分编号重复: {scenario_key}")
        scenario_keys.add(scenario_key)
        referenced_fact_ids = {
            str(fact_id)
            for field in (
                "precondition_fact_ids",
                "action_fact_ids",
                "expected_fact_ids",
            )
            for fact_id in list(group.get(field) or [])
        }
        unknown_fact_ids = sorted(referenced_fact_ids - allowed_fact_ids)
        if unknown_fact_ids:
            raise ValueError(
                "场景拆分引用了当前批次之外的事实: "
                + ", ".join(unknown_fact_ids)
            )
        design_item_ids = {
            str(item_id) for item_id in list(group.get("test_design_item_ids") or [])
        }
        unknown_design_item_ids = sorted(design_item_ids - allowed_design_item_ids)
        if unknown_design_item_ids:
            raise ValueError(
                "场景拆分引用了当前批次之外的测试设计项: "
                + ", ".join(unknown_design_item_ids)
            )
    return dict(output)


def build_generation_audit_summary(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """用确定性事实绑定检查代替重新输出全部用例的合成模型。"""

    authoritative_facts = [dict(item) for item in list(arguments.get("authoritative_facts") or [])]
    generation = dict(arguments.get("generation") or {})
    generation_inputs = list(arguments.get("generation_inputs") or [])
    case_budget = int(arguments.get("case_budget") or 0)
    test_cases = [dict(item) for item in list(generation.get("test_cases") or [])]
    bindings = [dict(item) for item in list(generation.get("case_fact_bindings") or [])]
    effective_fact_ids = {
        _required_text(fact.get("fact_id"), "fact_id")
        for fact in authoritative_facts
        if str(fact.get("status") or "") == "effective"
    }
    covered_fact_ids: set[str] = set()
    for binding in bindings:
        for item in list(binding.get("precondition_bindings") or []):
            covered_fact_ids.update(str(value) for value in list(dict(item).get("fact_ids") or []))
        for item in list(binding.get("step_bindings") or []):
            detail = dict(item)
            covered_fact_ids.update(str(value) for value in list(detail.get("action_fact_ids") or []))
            covered_fact_ids.update(str(value) for value in list(detail.get("expected_fact_ids") or []))
    invalid_fact_ids = sorted(covered_fact_ids - effective_fact_ids)
    uncovered_fact_ids = sorted(effective_fact_ids - covered_fact_ids)
    case_ids = [str(item.get("case_id") or "") for item in test_cases]
    duplicate_case_ids = sorted(
        {case_id for case_id in case_ids if case_id and case_ids.count(case_id) > 1}
    )
    expected_design_item_ids = {
        str(design_item_id)
        for raw_input in generation_inputs
        if isinstance(raw_input, dict)
        for design_item_id in list(
            dict(raw_input.get("case_fact_contract") or {}).get(
                "required_test_design_item_ids"
            )
            or []
        )
    }
    covered_design_item_ids = {
        str(design_item_id)
        for test_case in test_cases
        for design_item_id in list(test_case.get("test_design_item_ids") or [])
    }
    uncovered_design_item_ids = sorted(
        expected_design_item_ids - covered_design_item_ids
    )
    invalid_design_item_ids = sorted(
        covered_design_item_ids - expected_design_item_ids
    )
    differences: list[str] = []
    if len(test_cases) != case_budget:
        differences.append(f"用例数量不符: target={case_budget}, actual={len(test_cases)}")
    if len(bindings) != len(test_cases):
        differences.append("用例与事实绑定数量不一致")
    if uncovered_fact_ids:
        differences.append(f"存在未覆盖事实: {uncovered_fact_ids[:20]}")
    if invalid_fact_ids:
        differences.append(f"存在无效事实引用: {invalid_fact_ids[:20]}")
    if duplicate_case_ids:
        differences.append(f"存在重复 case_id: {duplicate_case_ids[:20]}")
    if uncovered_design_item_ids:
        differences.append(f"存在未覆盖测试设计项: {uncovered_design_item_ids[:20]}")
    if invalid_design_item_ids:
        differences.append(f"存在无效测试设计项引用: {invalid_design_item_ids[:20]}")
    approved = not differences
    result = {
        "approved": approved,
        "case_count": len(test_cases),
        "effective_fact_count": len(effective_fact_ids),
        "covered_fact_count": len(covered_fact_ids & effective_fact_ids),
        "uncovered_fact_ids": uncovered_fact_ids,
        "invalid_fact_ids": invalid_fact_ids,
        "duplicate_case_ids": duplicate_case_ids,
        "test_design_item_count": len(expected_design_item_ids),
        "covered_test_design_item_count": len(
            covered_design_item_ids & expected_design_item_ids
        ),
        "uncovered_test_design_item_ids": uncovered_design_item_ids,
        "invalid_test_design_item_ids": invalid_design_item_ids,
        "summary": (
            f"确定性审计{'通过' if approved else '未通过'}：{len(test_cases)} 条用例，"
            f"覆盖 {len(covered_fact_ids & effective_fact_ids)}/{len(effective_fact_ids)} 个有效事实，"
            f"覆盖 {len(covered_design_item_ids & expected_design_item_ids)}/"
            f"{len(expected_design_item_ids)} 个测试设计项。"
        ),
        "differences": differences,
    }
    context.artifacts["test_generation_audit"] = result
    return result


def approve_synthesized_test_cases(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """只允许通过主智能体终审的合成用例进入确定性校验。"""

    generation = dict(arguments.get("generation") or {})
    audit_summary = dict(arguments.get("audit_summary") or {})
    final_review = dict(arguments.get("final_review") or {})
    case_budget = int(arguments.get("case_budget") or 0)
    if final_review.get("phase") != "final_review":
        raise ValueError("主智能体终审阶段标识无效")
    if audit_summary.get("approved") is not True:
        raise ValueError(
            "确定性用例审计未通过: "
            + "；".join(str(item) for item in list(audit_summary.get("differences") or [])[:5])
        )
    if final_review.get("approved") is not True:
        differences = [str(item) for item in list(final_review.get("differences") or [])]
        detail = "；".join(differences[:5]) or str(final_review.get("summary") or "未通过")
        raise ValueError(f"主智能体终审未通过: {detail}")
    test_cases = list(generation.get("test_cases") or [])
    case_fact_bindings = list(generation.get("case_fact_bindings") or [])
    if case_budget < 1 or len(test_cases) != case_budget:
        raise ValueError(
            f"生成结果数量与任务目标不一致: target={case_budget}, actual={len(test_cases)}"
        )
    if len(case_fact_bindings) != len(test_cases):
        raise ValueError("生成结果的用例与事实绑定数量不一致")
    context.artifacts["test_generation_final_review"] = final_review
    return {
        "test_cases": test_cases,
        "case_fact_bindings": case_fact_bindings,
        "final_review": final_review,
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
        test_design_item_ids = raw_case.get("test_design_item_ids")
        if (
            not isinstance(preconditions, list)
            or not isinstance(tags, list)
            or not isinstance(test_design_item_ids, list)
        ):
            raise ValueError(
                f"第 {index} 条用例的 preconditions、tags 和 test_design_item_ids 必须是数组"
            )
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
                "test_design_item_ids": list(
                    dict.fromkeys(
                        _required_text(item, f"第 {index} 条用例测试设计项")
                        for item in test_design_item_ids
                    )
                ),
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
    final_review = dict(arguments.get("final_review") or {})
    if final_review.get("phase") != "final_review":
        raise ValueError("持久化前缺少独立终审结果")
    if final_review.get("approved") is not True:
        raise ValueError("独立终审未通过，禁止持久化测试用例")
    artifact = {
        "project_id": context.project_id,
        "run_id": context.run_id,
        "requirement": requirement,
        "evidence": {
            "source": dict(arguments.get("evidence_source") or {}),
            # 将压缩决策与最终用例一起落盘，便于后续历史 RAG 和评测追溯。
            "context_compression": deepcopy(
                context.artifacts.get("context_compression") or {}
            ),
        },
        "case_count": len(cases),
        "target_count": int(context.run_input.get("case_budget") or len(cases)),
        "target_met": len(cases) == int(context.run_input.get("case_budget") or len(cases)),
        "test_cases": cases,
        "case_fact_bindings": case_fact_bindings,
        "execution_plan": execution_plan,
        "final_review": final_review,
    }
    context.artifacts["test_generation"] = artifact
    return {
        "status": "completed",
        "summary": f"已持久化 {len(cases)} 条测试用例",
        "persisted_artifact_key": "test_generation",
        "final_review": final_review,
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
        "batch_id": {"type": "string", "pattern": "^M[0-9]{3}-B[0-9]{3}$"},
        "batch_number": {"type": "integer", "minimum": 1},
        "batch_count": {"type": "integer", "minimum": 1},
        "module_index": {"type": "integer", "minimum": 0},
        "module_batch_index": {"type": "integer", "minimum": 0},
        "module_batch_count": {"type": "integer", "minimum": 1},
        "module_name": {"type": "string", "minLength": 1},
        "coverage_focus": {"type": "string", "minLength": 1},
        "source_document_ids": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": 1},
        },
        "source_page_numbers": {
            "type": "array",
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": 1},
        },
        "source_scope_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "semantic_summary": {"type": "string", "minLength": 1},
        "semantic_keywords": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "fact_count": {"type": "integer", "minimum": 1},
        "fact_json_chars": {"type": "integer", "minimum": 1},
        "required_test_design_item_ids": {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "string",
                "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$",
            },
        },
    },
    "required": [
        "batch_id",
        "batch_number",
        "batch_count",
        "module_index",
        "module_batch_index",
        "module_batch_count",
        "module_name",
        "coverage_focus",
        "source_document_ids",
        "source_page_numbers",
        "source_scope_ids",
        "semantic_summary",
        "semantic_keywords",
        "fact_count",
        "fact_json_chars",
        "required_test_design_item_ids",
    ],
    "additionalProperties": False,
}


BATCH_BUSINESS_MODULE_SCHEMA = deepcopy(
    PLAN_SCHEMA["properties"]["business_modules"]["items"]
)
for internal_field in ("fact_design_routes", "test_points"):
    BATCH_BUSINESS_MODULE_SCHEMA["properties"].pop(internal_field)
    BATCH_BUSINESS_MODULE_SCHEMA["required"].remove(internal_field)


BATCH_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement_summary": {"type": "string"},
        "business_module": BATCH_BUSINESS_MODULE_SCHEMA,
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
        "test_design_items": {
            "type": "array",
            "items": TEST_DESIGN_CATALOG_ITEM_SCHEMA,
        },
    },
    "required": [
        "requirement_summary",
        "business_module",
        "coverage_focus",
        "risks",
        "test_design_items",
    ],
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
        "case_fact_contract": {
            "type": "object",
            "properties": {
                "target_case_ids": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "pattern": "^TC-[0-9]{3,}$"},
                },
                "required_fact_ids": FACT_ID_LIST_SCHEMA,
                "required_test_design_item_ids": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$",
                    },
                },
                # 仅供平台后处理确定性派生设计项编号，模型不会被要求回填该表。
                "fact_design_item_ids": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$",
                        },
                    },
                },
                "coverage_slots": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "case_id": {
                                "type": "string",
                                "pattern": "^TC-[0-9]{3,}$",
                            },
                            "required_fact_ids": FACT_ID_LIST_SCHEMA,
                            "required_test_design_item_ids": {
                                "type": "array",
                                "uniqueItems": True,
                                "items": {
                                    "type": "string",
                                    "pattern": "^TD-[0-9]{3}-[0-9]{3}-[0-9]{3}$",
                                },
                            },
                        },
                        "required": [
                            "case_id",
                            "required_fact_ids",
                            "required_test_design_item_ids",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "target_case_ids",
                "required_fact_ids",
                "required_test_design_item_ids",
                "coverage_slots",
            ],
            "additionalProperties": False,
        },
    },
    "required": [
        "requirement",
        "plan",
        "case_budget",
        "batch",
        "authoritative_facts",
        "case_fact_contract",
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
        "required": ["item_index", "output"],
        "additionalProperties": False,
    }


BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "submit_source_semantics",
        "name": "提交来源语义事实",
        "description": "提交当前来源范围内提取的权威事实，并由平台按严格契约校验。",
        "handler_key": "testing.submit_source_semantics",
        "input_schema": SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
        "output_schema": SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "submit_business_plan",
        "name": "提交全局业务规划",
        "description": "提交全局业务模块和测试设计目录，并由平台按严格契约校验。",
        "handler_key": "testing.submit_business_plan",
        "input_schema": PLANNER_AGENT_SUBMISSION_SCHEMA,
        "output_schema": PLANNER_AGENT_SUBMISSION_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "submit_generation_batch",
        "name": "提交测试用例生成批次",
        "description": "提交当前真实批次生成的测试用例，并由平台按严格契约校验。",
        "handler_key": "testing.submit_generation_batch",
        "input_schema": MODEL_GROUNDING_SCHEMA,
        "output_schema": MODEL_GROUNDING_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "submit_scenario_design_guidance",
        "name": "提交场景拆分建议",
        "description": "提交当前真实批次的场景拆分建议，并由平台按严格契约校验。",
        "handler_key": "testing.submit_scenario_design_guidance",
        "input_schema": SCENARIO_DESIGN_GUIDANCE_SCHEMA,
        "output_schema": SCENARIO_DESIGN_GUIDANCE_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
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
                "text_items": {"type": "array", "items": SOURCE_SEMANTICS_INPUT_SCHEMA},
                "vision_items": {"type": "array", "items": SOURCE_SEMANTICS_INPUT_SCHEMA},
                "item_count": {"type": "integer", "minimum": 1},
                "text_item_count": {"type": "integer", "minimum": 0},
                "vision_item_count": {"type": "integer", "minimum": 0},
                "source_kind": {"type": "string", "enum": ["inline", "knowledge_document"]},
            },
            "required": [
                "text_items",
                "vision_items",
                "item_count",
                "text_item_count",
                "vision_item_count",
                "source_kind",
            ],
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
                "text_inputs": {"type": "array", "items": SOURCE_SEMANTICS_INPUT_SCHEMA},
                "text_records": {
                    "type": "array",
                    "items": _map_record_schema(SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA),
                },
                "vision_inputs": {"type": "array", "items": SOURCE_SEMANTICS_INPUT_SCHEMA},
                "vision_records": {
                    "type": "array",
                    "items": _map_record_schema(SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA),
                },
            },
            "required": ["text_inputs", "text_records", "vision_inputs", "vision_records"],
            "additionalProperties": False,
        },
        "output_schema": SOURCE_SEMANTICS_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_business_plan_batches",
        "name": "准备业务规划批次",
        "description": "按事实数量和真实 JSON 体积切分业务规划输入，保留全部事实顺序。",
        "handler_key": "testing.prepare_business_plan_batches",
        "input_schema": {
            "type": "object",
            "properties": {
                "planning_scopes": SOURCE_SEMANTICS_OUTPUT_SCHEMA["properties"]["planning_scopes"],
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["planning_scopes", "case_budget"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "batch_count": {"type": "integer", "minimum": 1},
                "scope_count": {"type": "integer", "minimum": 1},
                "fact_count": {"type": "integer", "minimum": 1},
            },
            "required": ["items", "batch_count", "scope_count", "fact_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_business_plan_consolidation",
        "name": "准备业务规划汇总",
        "description": "校验各批事实完整性并压缩草案，生成全局规划的最小输入。",
        "handler_key": "testing.prepare_business_plan_consolidation",
        "input_schema": {
            "type": "object",
            "properties": {
                "prepared_items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "object"},
                },
                "plan_records": {
                    "type": "array",
                    "minItems": 1,
                    "items": _map_record_schema(BUSINESS_PLAN_DRAFT_SCHEMA),
                },
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["prepared_items", "plan_records", "case_budget"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "partial_plans": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "object"},
                },
                "planning_metadata": {
                    "type": "object",
                    "properties": {
                        "coverage_focus": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "risks": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                    "required": ["coverage_focus", "risks"],
                    "additionalProperties": False,
                },
                "coverage_group_catalog": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "coverage_group_id": {
                                "type": "string",
                                "pattern": "^CG-[0-9]{4,}$",
                            },
                            "name": {"type": "string", "minLength": 1, "maxLength": 80},
                            "objective": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 160,
                            },
                            "coverage_items": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 160,
                                },
                            },
                        },
                        "required": [
                            "coverage_group_id",
                            "name",
                            "objective",
                            "coverage_items",
                        ],
                        "additionalProperties": False,
                    },
                },
                "batch_count": {"type": "integer", "minimum": 1},
                "covered_fact_count": {"type": "integer", "minimum": 1},
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
                "planning_limits": {
                    "type": "object",
                    "properties": {
                        "max_business_modules": {"type": "integer", "minimum": 1},
                        "max_test_points": {"type": "integer", "minimum": 1},
                        "max_test_designs": {"type": "integer", "minimum": 1},
                        "max_coverage_items": {"type": "integer", "minimum": 1},
                    },
                    "required": [
                        "max_business_modules",
                        "max_test_points",
                        "max_test_designs",
                        "max_coverage_items",
                    ],
                    "additionalProperties": False,
                },
            },
            "required": [
                "partial_plans",
                "planning_metadata",
                "coverage_group_catalog",
                "batch_count",
                "covered_fact_count",
                "case_budget",
                "planning_limits",
            ],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_planning_scope_routes",
        "name": "准备业务规划与测试设计路由",
        "description": "把每个治理后的有效来源范围准备为模块与测试设计联合路由任务。",
        "handler_key": "testing.prepare_planning_scope_routes",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": PLANNER_OUTPUT_SCHEMA,
                "planning_scopes": SOURCE_SEMANTICS_OUTPUT_SCHEMA["properties"]["planning_scopes"],
            },
            "required": ["plan", "planning_scopes"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "batch_items": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "scope_count": {"type": "integer", "minimum": 1},
                "batch_count": {"type": "integer", "minimum": 1},
                "module_count": {"type": "integer", "minimum": 1},
            },
            "required": ["items", "batch_items", "scope_count", "batch_count", "module_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_planning_route_repairs",
        "name": "审计业务规划路由缺口",
        "description": "汇总全部初始路由，只为未承接的测试设计项准备真实事实复核任务。",
        "handler_key": "testing.prepare_planning_route_repairs",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": PLANNER_OUTPUT_SCHEMA,
                "prepared_items": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "route_records": {
                    "type": "array",
                    "minItems": 1,
                    "items": _map_record_schema(PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA),
                },
            },
            "required": ["plan", "prepared_items", "route_records"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "object"}},
                "gap_module_count": {"type": "integer", "minimum": 0},
                "gap_design_item_count": {"type": "integer", "minimum": 0},
            },
            "required": ["items", "gap_module_count", "gap_design_item_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_planning_scope_routes",
        "name": "合并业务规划与测试设计路由",
        "description": "合并初始路由与缺口复核结果，严格绑定每条事实和测试设计项。",
        "handler_key": "testing.merge_planning_scope_routes",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": PLANNER_OUTPUT_SCHEMA,
                "prepared_items": {"type": "array", "minItems": 1, "items": {"type": "object"}},
                "route_records": {
                    "type": "array",
                    "minItems": 1,
                    "items": _map_record_schema(PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA),
                },
                "repair_records": {
                    "type": "array",
                    "items": _map_record_schema(PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA),
                },
            },
            "required": ["plan", "prepared_items", "route_records", "repair_records"],
            "additionalProperties": False,
        },
        "output_schema": PLAN_SCHEMA,
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
        "description": "按事实 ID 校验并应用模块协调补丁，拒绝跨模块引用、失效事实复活和动态配置降级。",
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
        "output_schema": MERGED_GENERATION_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "build_generation_audit_summary",
        "name": "构建生成结果审计摘要",
        "description": "确定性检查用例数量、事实覆盖、无效引用与重复编号，不重写完整用例。",
        "handler_key": "testing.build_generation_audit_summary",
        "input_schema": {
            "type": "object",
            "properties": {
                "authoritative_facts": {
                    "type": "array",
                    "items": AUTHORITATIVE_FACT_SCHEMA,
                },
                "generation": MERGED_GENERATION_SCHEMA,
                "generation_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": GENERATION_BATCH_ITEM_SCHEMA,
                },
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": [
                "authoritative_facts",
                "generation",
                "generation_inputs",
                "case_budget",
            ],
            "additionalProperties": False,
        },
        "output_schema": GENERATION_AUDIT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_final_review_batches",
        "name": "准备分批终审任务",
        "description": "复用生成包的模块和事实边界，按用例数量与上下文体积生成终审批次。",
        "handler_key": "testing.prepare_final_review_batches",
        "input_schema": {
            "type": "object",
            "properties": {
                "generation_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": GENERATION_BATCH_ITEM_SCHEMA,
                },
                "generation": MERGED_GENERATION_SCHEMA,
                "batch_case_limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["generation_inputs", "generation", "batch_case_limit"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "batch_count": {"type": "integer", "minimum": 1},
                "case_count": {"type": "integer", "minimum": 1},
            },
            "required": ["items", "batch_count", "case_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_final_review_repairs",
        "name": "准备终审增量修复任务",
        "description": "只为未通过或存在事实覆盖缺口的批次生成增量修复任务。",
        "handler_key": "testing.prepare_final_review_repairs",
        "input_schema": {
            "type": "object",
            "properties": {
                "review_inputs": {
                    "type": "array",
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "review_records": {
                    "type": "array",
                    "items": _map_record_schema(FINAL_REVIEW_OUTPUT_SCHEMA),
                },
                "generation_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": GENERATION_BATCH_ITEM_SCHEMA,
                },
            },
            "required": ["review_inputs", "review_records", "generation_inputs"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
                },
                "repair_batch_count": {"type": "integer", "minimum": 0},
            },
            "required": ["items", "repair_batch_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_final_review_repairs",
        "name": "合并终审增量修复",
        "description": "按稳定case_id覆盖失败批次，未进入修复的用例保持原值与顺序。",
        "handler_key": "testing.merge_final_review_repairs",
        "input_schema": {
            "type": "object",
            "properties": {
                "generation": MERGED_GENERATION_SCHEMA,
                "repair_inputs": {
                    "type": "array",
                    "items": FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
                },
                "repair_records": {
                    "type": "array",
                    "items": _map_record_schema(FINAL_REVIEW_REPAIR_RESULT_SCHEMA),
                },
            },
            "required": ["generation", "repair_inputs", "repair_records"],
            "additionalProperties": False,
        },
        "output_schema": MERGED_GENERATION_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_final_review_rechecks",
        "name": "准备终审变更复审",
        "description": "从修复后的完整结果中提取发生变更的批次，已通过批次不重复执行。",
        "handler_key": "testing.prepare_final_review_rechecks",
        "input_schema": {
            "type": "object",
            "properties": {
                "repair_inputs": {
                    "type": "array",
                    "items": FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
                },
                "generation": MERGED_GENERATION_SCHEMA,
                "generation_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": GENERATION_BATCH_ITEM_SCHEMA,
                },
            },
            "required": ["repair_inputs", "generation", "generation_inputs"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "recheck_batch_count": {"type": "integer", "minimum": 0},
            },
            "required": ["items", "recheck_batch_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_final_review_batches",
        "name": "合并分批终审结果",
        "description": "保留通过批次的初审结论，并以失败批次的复审结论增量覆盖。",
        "handler_key": "testing.merge_final_review_batches",
        "input_schema": {
            "type": "object",
            "properties": {
                "review_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "review_records": {
                    "type": "array",
                    "minItems": 1,
                    "items": _map_record_schema(FINAL_REVIEW_OUTPUT_SCHEMA),
                },
                "repair_inputs": {
                    "type": "array",
                    "items": FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
                },
                "recheck_inputs": {
                    "type": "array",
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "recheck_records": {
                    "type": "array",
                    "items": _map_record_schema(FINAL_REVIEW_OUTPUT_SCHEMA),
                },
                "audit_summary": GENERATION_AUDIT_SCHEMA,
            },
            "required": [
                "review_inputs",
                "review_records",
                "repair_inputs",
                "recheck_inputs",
                "recheck_records",
                "audit_summary",
            ],
            "additionalProperties": False,
        },
        "output_schema": FINAL_REVIEW_OUTPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "merge_final_review_recheck_records",
        "name": "合并多轮终审复审结果",
        "description": "按终审批次ID用后续复审结果覆盖基线结果，未再修改批次保持原结论。",
        "handler_key": "testing.merge_final_review_recheck_records",
        "input_schema": {
            "type": "object",
            "properties": {
                "baseline_inputs": {
                    "type": "array",
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "baseline_records": {
                    "type": "array",
                    "items": _map_record_schema(FINAL_REVIEW_OUTPUT_SCHEMA),
                },
                "replacement_inputs": {
                    "type": "array",
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "replacement_records": {
                    "type": "array",
                    "items": _map_record_schema(FINAL_REVIEW_OUTPUT_SCHEMA),
                },
            },
            "required": [
                "baseline_inputs",
                "baseline_records",
                "replacement_inputs",
                "replacement_records",
            ],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": _map_record_schema(FINAL_REVIEW_OUTPUT_SCHEMA),
                },
                "baseline_count": {"type": "integer", "minimum": 0},
                "replaced_count": {"type": "integer", "minimum": 0},
            },
            "required": ["items", "baseline_count", "replaced_count"],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_global_final_review",
        "name": "准备全局终审摘要",
        "description": "只输出用例索引、批审结论和审计摘要，避免全局Agent重复读取完整事实正文。",
        "handler_key": "testing.prepare_global_final_review",
        "input_schema": {
            "type": "object",
            "properties": {
                "generation": MERGED_GENERATION_SCHEMA,
                "batch_review": FINAL_REVIEW_OUTPUT_SCHEMA,
                "audit_summary": GENERATION_AUDIT_SCHEMA,
            },
            "required": ["generation", "batch_review", "audit_summary"],
            "additionalProperties": False,
        },
        "output_schema": GLOBAL_FINAL_REVIEW_INPUT_SCHEMA,
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "prepare_terminal_final_review_repairs",
        "name": "准备统一终末修复任务",
        "description": "刷新当前用例批次，并把批次复审与全局审查差异统一路由到最后一轮增量修复。",
        "handler_key": "testing.prepare_terminal_final_review_repairs",
        "input_schema": {
            "type": "object",
            "properties": {
                "generation_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": GENERATION_BATCH_ITEM_SCHEMA,
                },
                "generation": MERGED_GENERATION_SCHEMA,
                "batch_case_limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "batch_review": FINAL_REVIEW_OUTPUT_SCHEMA,
                "global_review": FINAL_REVIEW_OUTPUT_SCHEMA,
            },
            "required": [
                "generation_inputs",
                "generation",
                "batch_case_limit",
                "batch_review",
                "global_review",
            ],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
                },
                "repair_batch_count": {"type": "integer", "minimum": 0},
                "review_inputs": {
                    "type": "array",
                    "minItems": 1,
                    "items": FINAL_REVIEW_BATCH_INPUT_SCHEMA,
                },
                "review_records": {
                    "type": "array",
                    "minItems": 1,
                    "items": _map_record_schema(FINAL_REVIEW_OUTPUT_SCHEMA),
                },
            },
            "required": [
                "items",
                "repair_batch_count",
                "review_inputs",
                "review_records",
            ],
            "additionalProperties": False,
        },
        "risk_level": "low",
        "requires_approval": False,
    },
    {
        "tool_key": "approve_synthesized_test_cases",
        "name": "确认主智能体终审结果",
        "description": "只允许经过合成校验且由主智能体明确通过的测试用例进入最终校验。",
        "handler_key": "testing.approve_synthesized_test_cases",
        "input_schema": {
            "type": "object",
            "properties": {
                "generation": MERGED_GENERATION_SCHEMA,
                "audit_summary": GENERATION_AUDIT_SCHEMA,
                "final_review": FINAL_REVIEW_OUTPUT_SCHEMA,
                "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["generation", "audit_summary", "final_review", "case_budget"],
            "additionalProperties": False,
        },
        "output_schema": SYNTHESIS_APPROVAL_OUTPUT_SCHEMA,
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
        "tool_key": "select_execution_chain",
        "name": "选择确定性执行主链",
        "description": "按严格可达候选顺序选择主链，不调用模型重写或拼接用例。",
        "handler_key": "testing.select_execution_chain",
        "input_schema": CHAIN_CONTEXT_SCHEMA,
        "output_schema": EXECUTION_CHAIN_SELECTION_SCHEMA,
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
                "final_review": FINAL_REVIEW_OUTPUT_SCHEMA,
            },
            "required": [
                "requirement",
                "evidence_source",
                "test_cases",
                "case_fact_bindings",
                "execution_plan",
                "final_review",
            ],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "const": "completed"},
                "summary": {"type": "string", "minLength": 1},
                "persisted_artifact_key": {
                    "type": "string",
                    "const": "test_generation",
                },
                "final_review": FINAL_REVIEW_OUTPUT_SCHEMA,
            },
            "required": [
                "status",
                "summary",
                "persisted_artifact_key",
                "final_review",
            ],
            "additionalProperties": False,
        },
        "risk_level": "medium",
        "requires_approval": False,
    },
)

BUILTIN_AGENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "agent_key": "test_business_plan_batcher",
        "version": 1,
        "name": "测试业务规划分批智能体",
        "description": "在有界事实批次内提炼业务模块候选，并逐条承接真实事实。",
        "instructions": (
            "你是测试业务规划分批智能体。planning_scopes 是平台从完整有效事实目录切出的当前批次，"
            "planning_batch 只说明批次位置，case_budget 是全局目标用例数。"
            "只能根据当前 planning_scopes 提炼粗粒度业务模块候选，不得补造来源外系统、角色、状态或规则。"
            "页面、按钮、展示状态和单条规则不能仅因验证路径独立就升级为模块；共享业务目标、核心数据或生命周期的内容应合并。"
            "module_candidates 必须保持精简，每项通过 coverage_topics 列出本批事实直接支持的可测试主题。"
            "每个真实 fact_id 必须至少出现在一个 module_candidate.fact_ids 中；允许确有跨模块约束的事实重复，"
            "即使事实之间互相冲突、前后版本不一致或看似被其他事实替代，也必须全部分类保留；本节点不裁决生效性，"
            "后续权威协调节点会依据来源关系处理冲突。"
            "禁止遗漏、改写或自造 fact_id。batch_summary、coverage_focus 和 risks 只概括当前批次。"
            "risks 没有风险时输出空数组；有风险时可输出字符串，也可输出包含 risk_id、description、severity、related_fact_ids 的结构化风险对象；不得输出其他字段。"
            "最终 JSON 只能包含 batch_summary、module_candidates、coverage_focus、risks；"
            "module_candidates 每项只能包含 name、objective、actors、lifecycle、coverage_topics、fact_ids；"
            "来源没有明确参与角色时 actors 必须为空数组，不得补造用户类型。"
            "coverage_topics 每项只能包含 name、objective。lifecycle 没有明确状态流转时必须为 null。"
        ),
        "model": "",
        "output_schema": BUSINESS_PLAN_DRAFT_SCHEMA,
        "runtime_config": {
            "model_route": "turbo",
            "transient_fallback_model_route": "main",
            "transient_fallback_after_failures": 2,
            "result_cache": {
                "version": "business-plan-batches-v2-projected-facts",
                "accept_legacy": True,
            },
            "disable_server_output_schema": True,
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 5000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "output_postprocessor": "testing.validate_business_plan_draft_output",
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_business_planner",
        "version": 3,
        "name": "测试业务规划智能体",
        "description": "汇总已校验的分批规划草案，形成全局业务模块和测试设计目录。",
        "instructions": (
            "你是测试业务规划智能体。partial_plans 是平台分批读取全部有效事实、逐批校验 fact_id 无遗漏后形成的精简草案，"
            "planning_metadata 是平台从草案确定性汇总的覆盖重点和风险；"
            "coverage_group_catalog 是已校验覆盖语义组目录，partial_plans 中的 coverage_topics 只引用其中的 coverage_group_id。"
            "case_budget 是最终目标用例数；planning_limits 由已校验分批草案中的模块候选、覆盖主题和真实事实数量计算。"
            "只能从 partial_plans 汇总全局规划，不得臆造草案外的系统、角色或规则。"
            "必须逐批审查 draft.module_candidates 和 coverage_topics；语义相同或共享业务目标、核心数据、生命周期的候选应合并，"
            "名称不同但业务含义重叠时也不能重复建模块；独有的业务主题不得因合并而遗漏。"
            "business_module 是共享同一业务目标、核心数据或生命周期的粗粒度能力域；"
            "页面、按钮、展示状态、单个操作或单条规则不能仅因验证路径独立就升级为业务模块。"
            "同一能力域下可独立验证的入口、状态、规则和交互必须逐项写入 test_points，"
            "不得只放进 requirement_summary、coverage_focus 或 risks 后遗漏。"
            "只有业务目标、核心对象或生命周期确实不同才拆成新模块；模块数量由需求语义决定，不设固定数量。"
            "草案明确列出的可选范围、内容矩阵、配置枚举和数量边界也是可测试能力的一部分；"
            "即使没有动作词，也必须由相关模块的 test_point 明确承接。"
            "按业务目标拆分模块，识别参与角色；仅在需求确实包含状态变化时填写生命周期。"
            "每个 test_point 必须选择真正适用的测试方法并输出 test_designs；technique 只能是场景法、等价类、"
            "边界值、状态迁移、判定表或错误推测，不要求每个测试点机械使用全部方法。"
            "coverage_items 只能填写 coverage_group_catalog 中的 coverage_group_id；"
            "每个 coverage_group_id 必须且只能出现一次，不得改写、复制、遗漏或创造新 ID。"
            "组是不可拆的最小语义单元，不得挑选或重排组内原子项。平台会在严格校验后展开组内全部原子覆盖意图；"
            "你只负责把语义组编排到最合适的测试方法下。"
            "输出保持精简：requirement_summary 不超过 120 个汉字，模块和测试点 objective 均不超过 160 个汉字。"
            "一条测试用例可以承载多个语义相关的覆盖项，因此禁止按 case_budget 强行拼接或删除原子覆盖意图。"
            "business_modules、全局 test_points 和全局 test_designs 的数量不得超过 planning_limits 对应上限；"
            "每个测试点只保留最适用的一种测试方法。可以合并语义重复的层级结构，不能合并、拆分或删除覆盖语义组 ID。"
            "完成规划后必须且只能调用一次 submit_business_plan 工具提交，"
            "不得用正文返回 JSON。"
            "本节点只负责识别业务模块，不负责输出证据路由；每个模块的真实 scope 归属由后续独立路由 Agent 逐项判定。"
            "coverage_focus 和 risks 已由平台从分批草案确定性汇总，不得重复输出。"
            "最终 JSON 顶层只能包含 requirement_summary、business_modules；"
            "business_modules 的每项只能包含 name、objective、actors、lifecycle、test_points；"
            "test_points 每项只能包含 name、objective、test_designs；test_designs 每项只能包含 technique、rationale、coverage_items。"
            "actors 可使用单个字符串或字符串数组，草案没有明确角色时保持空数组；"
            "lifecycle 有状态流转时填写单个状态链字符串，没有状态流转时必须为 null。"
            "不得输出 business_goal、modules、roles、partial_plans、planning_limits、case_budget、run_id 或 project_id。"
        ),
        "model": "",
        "output_schema": PLANNER_AGENT_SUBMISSION_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "result_cache": {
                "version": "business-plan-v5-coverage-groups",
                "accept_legacy": False,
            },
            "disable_server_output_schema": True,
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            # 原子覆盖主题只返回短 ID，完整原文由平台校验后恢复。
            "max_output_tokens": 12000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "output_postprocessor": "testing.validate_business_plan_output",
            "tool_keys": ["submit_business_plan"],
            "stop_at_tool_keys": ["submit_business_plan"],
        },
    },
    {
        "agent_key": "test_planning_scope_router",
        "version": 2,
        "name": "业务规划与测试设计路由智能体",
        "description": "按小批次为每个有效来源范围中的事实确定业务模块及其直接支持的测试设计项。",
        "instructions": (
            "你是业务规划证据路由智能体。scopes 是当前小批次的真实来源范围，business_modules 是 Planner 给出的候选业务模块目录。"
            "必须按 scopes 输入顺序逐个处理；每个 scope_id 和其 facts 都是独立事实边界，不得跨 scope 合并或交换事实。"
            "routes 必须与 scopes 一一对应。每条 route 必须逐条处理对应 facts 中的每个 fact_ref，"
            "并通过 module_routes 选择一个主模块及其直接相关的测试设计项。"
            "module_routes 每项包含 module_index、relation 和 test_design_item_indexes；relation 只能是 primary 或 shared，"
            "每条事实必须且只能有一个 primary，只有事实确实同时约束多个独立模块时才能增加 shared。"
            "同一条事实的同一个 module_index 只能出现一次，已经作为 primary 的模块不得再次作为 shared 输出。"
            "test_design_item_indexes 是对应模块 test_design_items 中的零基下标，必须选择事实能够直接支持的一个或多个覆盖意图；"
            "如果目录中没有任何测试设计项受到该事实直接支持，必须保留正确的模块路由并输出空数组；"
            "空数组表示规划目录未覆盖该事实，不得只凭词面相似或为了补齐编号把事实挂到不受其支持的测试设计项。"
            "不得因为事实同处一个 scope 就把整组事实分给同一批模块。"
            "输出 assignment 只能填写短 fact_ref，不能输出或改写长 fact_id；fact_ref 必须与输入逐条一致，不得遗漏、重复或自造。"
            "平台会在严格校验后把 fact_ref 映射回原始 fact_id；模块下标只能使用 business_modules 中的零基下标。"
            "提交前必须按输入 scopes 顺序逐项自检：每个 scope 的 assignments 数量必须等于该 scope facts 数量，"
            "并逐字复制输入中的全部 fact_ref；先完成数量与集合核对，再输出 JSON。"
            "最终 JSON 只能包含 routes；routes 每项只能包含 scope_id 和 assignments。"
        ),
        "model": "",
        "output_schema": PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "turbo",
            "result_cache": {
                "version": "planning-scope-routes-v4-fact-refs",
                "accept_legacy": False,
            },
            "disable_server_output_schema": True,
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 6000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_planning_route_gap_reviewer",
        "version": 1,
        "name": "业务规划路由缺口复核智能体",
        "description": "只复核初始路由未承接的测试设计项，并绑定可直接支持它们的真实事实。",
        "instructions": (
            "你是业务规划路由缺口复核智能体。输入只包含一个业务模块、初始路由未承接的测试设计项，"
            "以及该模块已关联来源范围中的真实候选事实。"
            "必须逐条审查 missing_test_design_items，不能遗漏、增加或改写 test_design_item_index。"
            "只有 candidate_facts 中 assertion 能直接支持该测试设计覆盖意图时，disposition 才能是 supported，"
            "并在 fact_ids 中引用一个或多个对应的真实 fact_id；不能只凭关键词相似、同页或同模块强行绑定。"
            "若候选事实都不能直接支持，disposition 必须是 unsupported，fact_ids 必须为空，并在 reason 中说明规划与事实的缺口。"
            "reason 必须简洁说明事实与覆盖意图为何匹配或不匹配。"
            "最终 JSON 只能包含 module_index 和 decisions；每个 decision 只能包含 "
            "test_design_item_index、disposition、fact_ids、reason。"
        ),
        "model": "",
        "output_schema": PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "turbo",
            "result_cache": {
                "version": "planning-route-gap-review-v1",
                "accept_legacy": False,
            },
            "disable_server_output_schema": True,
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 4000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_source_text_semantics_analyst",
        "version": 1,
        "name": "来源文本语义分析智能体",
        "description": "使用强文本模型按连续页面批次提取带精确来源坐标的原子事实。",
        "instructions": (
            "你是来源文本语义分析智能体。source_kind=document_batch 时，pages 是连续真实页面批次；"
            "source_kind=inline 时，requirement 和 source_scopes 共同限定唯一事实源。"
            "必须逐个审查 source_scopes，但不得为了覆盖范围补造来源中不存在的内容。authoritative_facts 只提取直接存在、"
            "可独立验证的原子事实；fact_id 在本批次内唯一，assertion 使用中文。"
            "source_kind=document_batch 时，每条 fact 的 source_anchor 必须使用 document 结构，逐字复制对应 page_number；禁止输出 inline 锚点。"
            "document source_anchor 只能输出 document_id、事实所在 page_number 和一个真实 block_id；禁止输出 quote、block_id 数组或 source_span。"
            "每条事实必须原子化；事实涉及多个文本块时继续拆分，并选择与当前原子事实最直接相关的一个 block_id。"
            "平台会根据真实页面确定性补齐 quote、绝对坐标和 scope_id；模型不得拼接或改写来源引用。"
            "inline source_anchor 只能输出 requirement 的绝对起止坐标。source_anchor 中禁止输出 source_kind，来源类型由平台按真实输入确定。"
            "禁止输出 scope_id，平台会根据校验后的真实来源锚点派生唯一 scope_id。"
            "status 只能是 effective、superseded、non_final、reference_only 或 uncertain。"
            "只有能够直接形成用户操作、可观察结果、状态规则、权限边界或可验证配置的事实才能标为 effective；"
            "项目背景、营销目标、GMV目标、原因说明和纯叙述性上下文必须标为 reference_only。"
            "value_policy 只能是 exact 或 runtime_configured。governed_value_spans 只输出当前输入正文中的字符坐标；"
            "压缩页面使用局部坐标，平台会从真实原页转换为绝对坐标，"
            "动态配置的识别和事实归类由你依据来源语义完成，平台不通过关键词要求确定性覆盖。"
            "平台只按 governed_value_spans 从真实来源切片生成具体示例值，不判断动态值内容；"
            "来源未出现明确示例时输出空数组，exact 时也必须为空。"
            "提交前必须逐条复核全部事实并清空每条 exact 事实的 governed_value_spans，不得只修正其中第一条。"
            "governed_by 只表达来源中明确存在的 replaces、invalidates、limits、parameterizes 关系，"
            "不得跨当前批次引用 fact_id；不得回显 source_scopes 或任何输入范围字段。"
            "分析完成后必须且只能调用一次 submit_source_semantics 工具提交结果，不得用正文返回 JSON。"
            "工具参数顶层只能包含 authoritative_facts；每条事实只能包含 fact_id、assertion、source_anchor、"
            "status、value_policy、governed_value_spans、governed_by。"
            "governed_value_spans 和 governed_by 没有内容时必须提交空数组 []，禁止使用空对象 {}。"
            "即使当前批次没有可提取的事实，也必须调用工具并明确提交 {\"authoritative_facts\":[]}。"
        ),
        "model": "",
        "output_schema": SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "turbo",
            "transient_fallback_model_route": "main",
            "transient_fallback_after_failures": 2,
            "result_cache": {
                "version": "source-text-semantics-v2-terminal-tool",
                "accept_legacy": False,
            },
            "input_mode": "text",
            "max_turns": 1,
            "request_timeout_seconds": 90,
            "max_retries": 0,
            "max_output_tokens": 15000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_keys": ["submit_source_semantics"],
            "stop_at_tool_keys": ["submit_source_semantics"],
        },
    },
    {
        "agent_key": "test_source_semantics_analyst",
        "version": 1,
        "name": "来源语义分析智能体",
        "description": "逐页或按纯文本来源提取带精确锚点、状态和治理关系的原子事实。",
        "instructions": (
            "你是来源语义分析智能体。source_kind=document 时，输入 JSON 与同一消息中的真实页面图像共同构成事实源；"
            "source_kind=inline 时，requirement 和 source_scopes 共同限定唯一事实源。每个输入只分析当前页或当前纯文本一次，不按业务模块重复解释。"
            "source_scopes 是平台根据真实证据目录确定的完整审查范围；你必须逐个审查，"
            "但只能提取页面中真实存在的事实，不得为了覆盖范围补造内容。"
            "authoritative_facts 只提取来源中直接存在、可独立验证的原子事实，不得补造页面、控件、状态、角色或规则。"
            "fact_id 只需在当前页面或本次来源内唯一；平台会依据真实来源身份合并为全局规范 fact_id，无需模型自行添加前缀。assertion 用中文陈述事实；禁止输出 scope_id，平台会根据校验后的真实来源锚点派生唯一 scope_id。"
            "source_scopes.allowed_block_ids 只是当前证据作用域允许引用的块集合，不是 source_anchor.block_id 的输出值。"
            "document source_anchor 只能输出 document_id、事实所在 page_number 和一个真实 block_id；禁止输出 quote、block_id 数组或 source_span。"
            "每条事实必须原子化；事实涉及多个文本块时继续拆分，并选择与当前原子事实最直接相关的一个 block_id。"
            "禁止使用图片像素坐标或块内相对坐标；平台会根据页面正文和布局块自动补齐 quote、source_span、scope_id、asset_source_sha256 和 page_image_sha256。"
            "inline source_anchor 只能输出 requirement 对应的 source_offset_start 和 source_offset_end；"
            "source_anchor 中禁止输出 source_kind，来源类型由平台按真实输入确定；"
            "平台会根据真实 requirement 自动补齐 requirement_sha256 和 quote。"
            "marks 是 manifest v3 的通用来源标记：strikeout 表示命中内容已删除；高亮或批注只在原文明确表达时用于判断"
            "replaces、non_final 或 runtime_configured，不得只凭颜色、位置或批注存在本身推断业务含义。"
            "status 只能是 effective、superseded、non_final、reference_only 或 uncertain。"
            "明确生效且可作为生成依据时用 effective；已废弃、非终稿、仅参考或无法确认的内容不得标为 effective。"
            "只有能够直接形成用户操作、可观察结果、状态规则、权限边界或可验证配置的事实才能标为 effective；"
            "项目背景、营销目标、GMV目标、原因说明和纯叙述性上下文必须标为 reference_only。"
            "value_policy 只能是 exact 或 runtime_configured；来源明确说值由配置或运行态决定时必须使用 runtime_configured。"
            "动态配置的识别和事实归类由你依据来源语义完成，平台不通过关键词要求确定性覆盖。"
            "governed_value_spans 只能填写当前输入 page_text 中的字符坐标；压缩页面使用局部坐标，"
            "平台会从真实原页切片并转换为绝对坐标后生成 governed_values；"
            "平台不判断动态值内容，只校验坐标并原样切片；策略声明本身不是具体示例值；"
            "只有来源明确声明值由配置、后台或运行态决定时才使用 runtime_configured；来源直接给出的固定文案、金额、"
            "次数和时长都属于 exact，不能因为存在批注、待设计说明或视觉标记而改判。"
            "不得自行复制、改写或概括具体值。来源没有明确示例值时，即使 value_policy=runtime_configured 也必须输出空数组；"
            "value_policy=exact 时 governed_value_spans 必须为空。"
            "提交前必须逐条复核全部事实并清空每条 exact 事实的 governed_value_spans，不得只修正其中第一条。"
            "governed_by 每项只能包含 relation 和 directive_fact_id；relation 只能是 replaces、invalidates、limits、parameterizes。"
            "只有来源中存在明确治理关系时才填写，不得推测；不得引用自身或输入外事实。"
            "分析完成后必须且只能调用一次 submit_source_semantics 工具提交结果，不得用正文返回 JSON。"
            "工具参数顶层只能包含 authoritative_facts，每条事实只能包含 fact_id、assertion、source_anchor、"
            "status、value_policy、governed_value_spans、governed_by。"
            "即使当前页面没有可提取的事实，也必须调用工具并明确提交 {\"authoritative_facts\":[]}，"
            "禁止提交空对象或省略 authoritative_facts。"
        ),
        "model": "",
        "output_schema": SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "vision",
            "result_cache": {
                "version": "source-vision-semantics-v1",
                "accept_legacy": True,
            },
            "input_mode": "document_page_optional_image",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 5000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_keys": ["submit_source_semantics"],
            "stop_at_tool_keys": ["submit_source_semantics"],
        },
    },
    {
        "agent_key": "test_authority_reconciliation_reviewer",
        "name": "跨页权威事实协调智能体",
        "description": "在同一业务模块内识别远距离修订、替代、失效和动态配置关系，只输出事实状态补丁。",
        "instructions": (
            "你是跨页权威事实协调智能体。module 是唯一审查边界，authoritative_facts 已按真实来源顺序排列。"
            "你不得新增、删除、改写 assertion、scope_id 或 source_anchor；decisions 只返回状态、值策略或治理关系确有变化的事实补丁。"
            "每条补丁必须包含 fact_id、reason，以及 status、value_policy、governed_values、governed_by 中至少一个确有变化的字段；"
            "未变化字段不要复制，平台会从原事实继承。"
            "重点识别同一业务行为在不同页面或远距离章节中的后续修订、明确替代、废弃、非最终说明和以运行时配置为准的关系。"
            "不得因为文字较新就自动覆盖旧规则；只有来源明确表达替代、修订、作废、暂不采用、非最终或配置治理时才能改变状态。"
            "原 status 不是 effective 的事实不得重新激活，也不得改成其他状态。原 value_policy=runtime_configured 不得降级为 exact。"
            "若后续事实明确替代或使旧事实无效，应把旧事实标为 superseded，并在旧事实 governed_by 中引用治理事实。"
            "governed_by 每项只能包含 relation 和 fact_id；其中 fact_id 是施加治理的事实 ID，"
            "relation 描述它对当前 decision.fact_id 的作用，标准值使用 replaces、invalidates、limits 或 parameterizes。"
            "若事实明确声明具体值以后台、环境或运行时配置为准，应使用 runtime_configured；"
            "只保留平台已经从真实坐标切片得到的 governed_values，来源没有示例值时允许为空。"
            "没有跨来源治理关系或事实无需变化时不要输出该 fact_id；整个模块均无需变化时 decisions 输出空数组，平台会确定性保留原事实。"
            "governed_by 只能引用当前 authoritative_facts 中的 fact_id，不得引用自身或模块外事实。"
            "reason 用不超过240字的中文说明当前裁决的直接来源依据。最终 JSON 顶层只能包含 decisions。"
        ),
        "model": "",
        "output_schema": AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "result_cache": {
                "version": "authority-reconciliation-v1",
                "accept_legacy": True,
            },
            "max_turns": 1,
            "request_timeout_seconds": 90,
            "max_retries": 0,
            "max_output_tokens": 8192,
            "extra_body": {"thinking": {"type": "disabled"}},
            "disable_server_output_schema": True,
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_generation_scenario_designer",
        "name": "测试场景拆分专业智能体",
        "version": 1,
        "description": "只依据当前批次事实和测试设计项，为复杂生命周期、角色和边界组合提供结构化场景拆分。",
        "instructions": (
            "你是测试场景拆分专业 Agent，不生成测试用例正文。"
            "输入的 authoritative_facts 是唯一事实源，plan.test_design_items 是唯一测试设计来源。"
            "你只能返回输入中已有的 fact_id 和 test_design_item_id，不得输出需求文本、"
            "操作步骤、界面、按钮、具体值或新业务规则。"
            "根据事实的状态、角色、生命周期、异常、边界和权限关系拆分 scenario_groups，"
            "仅通过 precondition_fact_ids、action_fact_ids 和 expected_fact_ids 表达结构。"
            "每个分组必须至少引用一条当前批次事实，expected_fact_ids 应对应可观察结果。"
            "recommended_case_count 和 scenario_groups 数量不得超过 case_budget。"
            "简单事实不得为凑数拆成低价值变体。"
            "完成拆分后必须且只能调用一次 submit_scenario_design_guidance 工具提交，"
            "不得用正文返回 JSON。"
        ),
        "model": "",
        "output_schema": SCENARIO_DESIGN_GUIDANCE_SCHEMA,
        "runtime_config": {
            "model_route": "main",
            "max_turns": 1,
            "request_timeout_seconds": 120,
            "max_retries": 0,
            "max_output_tokens": 3000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "disable_server_output_schema": True,
            "output_postprocessor": "testing.validate_scenario_design_guidance",
            "tool_keys": ["submit_scenario_design_guidance"],
            "stop_at_tool_keys": ["submit_scenario_design_guidance"],
        },
    },
    {
        "agent_key": "test_case_generator",
        "name": "测试用例生成智能体",
        "description": "依据真实需求和业务规划生成结构化、可执行、可断言的测试用例。",
        "instructions": (
            "你是测试用例生成智能体。authoritative_facts 是当前模块经过来源语义验证且 status=effective 的唯一事实源。"
            "authoritative_facts.assertion 是当前批次唯一事实文本，不能扩展其含义。"
            "只能使用 authoritative_facts 中明确存在的事实，不得使用常识或已失效来源补造。"
            "动态值及其展示方式按 authoritative_facts 原样表达，平台不判断或改写其取值。"
            "plan 是当前业务模块规划，batch 是按模块语义、来源页和负载形成的上下文包，"
            "case_budget 是本包必须精确生成的用例数量，可以大于 1。"
            "case_fact_contract 是平台确定的批次级生成契约：必须按 case_budget 精确生成用例，"
            "required_fact_ids 必须在本批全部用例的事实绑定中合计完整覆盖；"
            "coverage_slots 按数组顺序提供每条用例的初始事实负载参考，允许按业务语义在用例之间重新分配，"
            "但全批必须完整覆盖事实全集。测试设计项编号由平台依据事实路由确定性派生，模型不需要看到、生成或猜测编号。"
            "当 _platform_repair.mode=minimal_patch 时，必须把 candidate_output 作为上一版基线，"
            "一次性修正 validation_feedback 列出的全部违规项并保留其余已覆盖事实，"
            "不得只处理第一项，也禁止整包改写。"
            "若同时提供 repair_targets 和 protected_case_ids，只能修改 repair_targets 指定的 test_cases 数组位置；"
            "protected_case_ids 对应位置必须逐字段保持 candidate_output 原值，但最终仍返回完整 test_cases。"
            "当 _platform_repair.mode=full_regeneration 时，说明上一版候选结构已损坏且不会提供 candidate_output；"
            "必须重新读取当前原始输入并完整生成本包，不得拼接、补猜或延续上一版残片。"
            "事实应按业务语义分配给最合适的用例，允许在不同用例之间重新组合，不按数组位置机械切分。"
            "batch.semantic_summary、semantic_keywords、source_page_numbers 和 source_scope_ids 只用于说明关联边界，"
            "不得作为新增事实；authoritative_facts 仍是唯一事实源。"
            "只能覆盖 batch.module_name 和 batch.coverage_focus，不得跨到其他批次补造内容。"
            "你可以自主调用 test_generation_scenario_designer 处理需要拆分的复杂批次，但每个批次最多调用一次。"
            "当当前批次存在跨生命周期、多角色、权限或异常边界组合时，"
            "先把当前原始输入作为 input JSON 传给它；单一简单事实可以直接生成。"
            "专业 Agent 只提供事实 ID 和测试设计 ID 的结构建议，"
            "它不是新事实源；收到建议后必须立即整理用例并调用 submit_generation_batch，禁止再次调用专业 Agent；"
            "如果建议与 case_fact_contract 冲突，以平台契约为准。"
            "当输入包含 gap_contract 时，本批是单个权威事实缺口的修正任务：必须且只能生成一条直接覆盖 "
            "gap_contract.coverage_intent 的用例；不得改为同证据块中的其他测试意图。"
            "必须生成恰好 case_budget 条互不重复的用例，不得多生成或少生成，也不得为了凑数制造低价值变体。"
            "需求未直接声明前置条件时，preconditions 必须为空数组。"
            "需求未声明交互界面时，步骤使用实现无关的业务动作，不得臆造页面、按钮或提示文案。"
            "不要输出 case_id 和 module；平台会按 test_cases 数组顺序生成 case_id，并使用 batch.module_name 写入模块。"
            "每条用例必须可执行，每个步骤的 expected 都必须是当前操作完成后可观察、可验证的断言。"
            "动作若依赖已打开的页面、弹窗或已建立的账号状态，必须在当前动作、同一用例的前序步骤或非互斥前置条件中"
            "明确建立该执行上下文，禁止只写‘在已打开的页面中’而没有进入路径。"
            "游客、已登录用户、不同学段或不同权限等互斥身份不得同时写成整条用例都成立的前置条件；"
            "case_budget 允许时应分配到不同用例，否则每个独立子流程必须先用动作明确切换身份或重置状态。"
            "需求明确存在生命周期时，正常流程用例应把入口业务状态单独写入 preconditions，"
            "最后一步 expected 写成单一终态事实；一个用例不要跨越多个异步生命周期。"
            "优先覆盖规划中的主流程、异常、边界、权限和生命周期风险，但不得添加需求外业务规则。"
            "plan.test_design_items 只用于说明规划中的覆盖意图；其中 test_design_item_id 仅可原样传给"
            "场景拆分专业 Agent。平台会从每条用例实际绑定的事实路由派生 test_design_item_ids，"
            "模型不得在最终测试用例中输出或猜测任何测试设计项编号。"
            "priority 只能使用 P0、P1、P2：P0 仅用于核心主流程不可用、数据或权限安全、资金或不可逆状态错误；"
            "P1 用于重要分支、异常、边界和可恢复的功能错误；P2 用于低频、轻微展示或非阻断体验问题。"
            "不得因为事实来自需求文档就默认标为 P0，应按该用例失败后的真实业务影响逐条判断。"
            "所有文本使用中文，协议名、字段名等专有名词除外。"
            "必须在生成用例的同一次调用中完成逐字段事实绑定，不再依赖后置 Agent 修改或补造事实。"
            "事实引用必须与业务字段内联：preconditions 每项只能包含 text 和 fact_ids；"
            "steps 每项固定为 action、expected、fact_bindings 三个字段，其中 action 和 expected 是非空字符串，"
            "fact_bindings 固定为仅包含 action、expected 两个数组的对象。"
            "示例：{\"action\":\"点击提交\",\"expected\":\"显示提交成功\","
            "\"fact_bindings\":{\"action\":[\"FACT-001\"],\"expected\":[\"FACT-002\"]}}。"
            "不要输出 case_fact_bindings、precondition_index 或 step_index；平台会根据数组位置确定性拆分绑定。"
            "前置条件和 expected 的 fact_ids 均禁止空数组；若找不到至少一个直接支持它们的输入 fact_id，"
            "必须删除或改写该字段，禁止保留无事实绑定的预期，也禁止为绑定而补造事实。"
            "action 仅在需求明确声明该操作时绑定对应事实；查看、观察、读取等为执行测试而引入的中性操作"
            "可以使用空数组，禁止把只支持预期结果的事实机械挂到 action。"
            "绑定不得缺字段、跨用例、跨模块、引用非 effective 事实或输入外 fact_id。"
            "同一条用例可覆盖多个相关事实，同一事实也可支持多条不同用例；不得遗漏 required_fact_ids。"
            "必须精确生成 case_budget 条用例；事实不足时直接失败，不得用低价值变体凑数。"
            "完成分析后必须且只能调用一次 submit_generation_batch 工具提交结果，不得用正文返回 JSON。"
            "工具参数顶层只能包含 test_cases，不得输出说明、统计或运行元数据。"
            "test_cases 的值必须直接是 JSON 数组，禁止使用 json.dumps 或其他方式将数组二次序列化为字符串。"
            "test_cases 每项必须且只能包含 title、priority、preconditions、steps、tags；"
            "tags 必须是字符串数组且可以省略，test_design_item_ids 不由模型输出。"
            "禁止在用例顶层输出 expected_result 或 expected，也禁止使用 step、description、module_name "
            "等别名替代用例字段。"
        ),
        "model": "",
        "output_schema": MODEL_GROUNDING_SCHEMA,
        "runtime_config": {
            "model_route": "main",
            # 直接生成只需一次；复杂批次允许一次主 Agent 调用、一次专业
            # Agent 终止提交和一次主 Agent 最终提交，并保留一个协议轮次余量。
            "max_turns": 4,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 12000,
            # 生成模型只接收当前批次的语义字段；原始锚点和全量契约保留在
            # node input，供平台后处理、哈希和审计使用。
            "input_projection_version": "generation-model-v5-dynamic-scenario-design",
            "input_projection": {
                "plan": {
                    "requirement_summary": True,
                    "business_module": [
                        "name",
                        "objective",
                        "actors",
                        "lifecycle",
                    ],
                    "coverage_focus": True,
                    "risks": True,
                    "test_design_items": [
                        "test_design_item_id",
                        "coverage_intent",
                        "test_point",
                        "technique",
                        "rationale",
                    ],
                },
                "case_budget": True,
                "batch": [
                    "module_name",
                    "coverage_focus",
                    "source_document_ids",
                    "source_page_numbers",
                    "source_scope_ids",
                    "semantic_summary",
                    "semantic_keywords",
                ],
                "authoritative_facts": [
                    "fact_id",
                    "assertion",
                    "scope_id",
                    "status",
                    "value_policy",
                    "governed_values",
                    "governed_by",
                ],
                "case_fact_contract": {
                    "required_fact_ids": True,
                    "coverage_slots": {
                        "required_fact_ids": True,
                    },
                },
            },
            "extra_body": {"thinking": {"type": "disabled"}},
            "disable_server_output_schema": True,
            "subagent_keys": ["test_generation_scenario_designer"],
            "tool_keys": ["submit_generation_batch"],
            "stop_at_tool_keys": ["submit_generation_batch"],
            # 允许先调用场景拆分专业 Agent；最终提交工具仍是唯一运行终点。
            "force_terminal_tool_choice": False,
        },
    },
    {
        "agent_key": "test_generation_final_reviewer",
        "name": "测试用例独立终审智能体",
        "version": 1,
        "description": "按模块批次独立审查用例的业务语义、可执行性和状态连贯性。",
        "instructions": (
            "你是测试用例分批终审 Agent。输入包含 review_batch、test_cases、case_fact_bindings、review_facts、"
            "test_design_items 和 audit_summary；每次只审查当前批次。"
            "平台已确定性完成数量、编号、Schema、事实引用存在性、逐字段绑定完整性和测试设计编号覆盖，"
            "不得重复审查或推翻这些结论。review_facts 只提供标准化 assertion、值策略和动态示例，"
            "不提供 quote、页码和坐标；不得因原文碎片、字符形态、坐标、页码或动态值无法确定而拒绝。"
            "只审查需要业务推理的内容：动作与预期的业务语义是否连贯、用例是否可执行、前置状态是否支撑操作、"
            "是否臆造输入事实之外的业务规则、状态迁移是否合理，以及批次内是否存在低价值语义重复。"
            "判定 semantic_duplicate 时必须比较完整业务规则与可观察结果；不同商品、价格、数量、用户状态、"
            "权限、边界或生命周期分支属于不同覆盖义务，不能仅因操作入口或步骤形式相似就判为重复。"
            "正向要求与对应的禁止、异常或边界规则若来自不同 review_facts，也属于不同覆盖义务，不得判为重复；"
            "semantic_duplicate 必须能指出在两个字段槽位重复承载的同一事实语义。"
            "若不同用例或步骤表达不同业务属性，必须保留各自覆盖，不得建议直接删除其中一项。"
            "review_batch.case_ids 是不可增删的用例槽位；不得提出超过当前槽位数量的拆分要求。"
            "当一个槽位承载多组独立覆盖义务时，应分别判断各子流程是否可执行与可断言；"
            "不得仅因为同一用例含有多个业务主题就要求新增用例。"
            "事实支持按 assertion 的业务含义判断，不要求 expected 与 assertion 逐字相同；runtime_configured 动态值原样放行。"
            "若不同 review_facts 分别直接使用了不同术语，测试步骤沿用各自来源术语不构成业务语义冲突；"
            "不得仅因近义词、对象前缀或来源原词不同而判定不可执行。"
            "approved=true 时 differences 必须为空；发现问题时 approved=false。每个 difference 必须完整包含"
            "case_id、category、field_path、detail、repair_scope、repair_instruction，修复要求直接内嵌在对应问题中。"
            "能在当前 case_id 内完成修正时 repair_scope=case；若问题需要拆分场景、在用例间迁移事实或测试意图、"
            "解决同一用例内的跨角色或跨状态跳变，必须 repair_scope=cohort，以便平台提供同审核批次的其他用例槽位。"
            "repair_scope=cohort 时，如确需修改其他用例，repair_instruction 必须明确写出目标 case_id；"
            "审核批次只是负载边界，禁止笼统要求修改同批全部用例。"
            "不要输出 related_fact_ids，平台会按 case_id 和 field_path 从既有逐字段绑定中确定性派生。"
            "category 只能使用 business_semantics、executability、state_coherence、unsupported_business_rule 或 semantic_duplicate。"
            "最终 JSON 必须且只能包含 phase、approved、summary、differences；"
            "phase 固定为 final_review，summary 必须用非空中文概括当前批次结论。"
            "不得修改、补造或重新输出测试用例，不得因数量正确就默认通过。"
        ),
        "model": "",
        "output_schema": BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "disable_server_output_schema": True,
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 4000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_generation_batch_repairer",
        "name": "测试用例批次修复智能体",
        "version": 1,
        "description": "仅修改未通过终审的用例批次，保持case_id、数量和已通过批次不变。",
        "instructions": (
            "你是测试用例批次修复 Agent。输入包含当前失败批次的 review_batch、test_cases、"
            "authoritative_facts、review_result、repair_requirements 和 required_fact_ids。"
            "输入 test_cases 已使用内联事实绑定结构。输出 case_patches 只包含实际发生变化的用例字段，"
            "平台会把补丁应用到原用例并重新完成全量契约校验；禁止重复输出未修改字段。"
            "target_case_ids 是本次唯一允许修改的编号清单，target_case_count 是目标集合数量。"
            "test_cases 只包含终审明确点名的待修用例，不代表完整审查批次；不得补写 review_batch 中未出现在"
            "target_case_ids 的用例。只修复 repair_requirements 指向的问题，不得修改批次外内容。"
            "case_patches 每项必须输出一个 target_case_ids 中的 case_id，同一 case_id 只能出现一次；允许在原case_id槽位内"
            "重写低价值或错误用例，但不得新增、删除、外延或重编号。"
            "required_fact_ids 和 test_design_items 是本次待修用例集合的覆盖契约，不是原 case 槽位的私有约束；"
            "允许在 target_case_ids 之间重新分配步骤、事实和测试设计项，以解除跨生命周期、状态冲突或语义重复。"
            "review_result.differences 中 related_fact_ids 是平台从原始绑定派生的事实指针；这些事实仍是硬性覆盖义务，"
            "状态重组只能把它们迁移到语义匹配的目标用例、前置条件或步骤，禁止因修复状态冲突而删除。"
            "每条用例只覆盖一个可独立建立前置状态并完成断言的生命周期阶段；异步前后阶段应分配到不同 case 槽位，"
            "不得仅靠改写措辞把提交、审核、奖励、下架等多个阶段继续串在同一用例。"
            "游客、已登录用户、学段和权限等互斥状态不得同时列为全局前置条件；应在 target_case_ids 间重新分配，"
            "或在对应子流程的首个动作中明确切换账号、登录状态或业务状态，使后续断言只受该子流程状态约束。"
            "动作若写成‘在已打开的页面或弹窗中’，必须补充同一动作或前序步骤的真实进入路径，不能把未建立的界面状态"
            "当作隐含前置条件。"
            "若 review_result 要求拆分，但 target_case_count 小于需要的独立场景数，该拆分在当前契约下不可执行；"
            "必须在现有槽位内按子流程重组步骤并保留全部 required_fact_ids，禁止只保留其中一组事实。"
            "repair_cycle 大于1表示上一轮局部修补未通过复审，此时必须根据 differences 重新检查整批 case 边界和"
            "事实分配，优先做结构重组，不得重复上一轮的表面文字修改。"
            "case_id 仅用于补丁定位且必须输出；module 是只读字段，禁止在补丁中输出。"
            "每个前置条件使用 text、fact_ids 内联表达；每一步固定包含 action、expected、fact_bindings，"
            "action 和 expected 是非空字符串，fact_bindings 仅包含 action、expected 两个事实 ID 数组，"
            "禁止在 fact_bindings 中输出 action_fact_ids 或 expected_fact_ids；"
            "所有事实引用都必须来自当前 authoritative_facts 中的生效事实。前置条件和 expected 必须绑定事实；"
            "action 仅在需求明确声明该操作时绑定事实，为执行测试引入的中性查看、观察、读取动作允许为空数组。"
            "不要输出 case_fact_bindings、precondition_index 或 step_index，平台会根据数组位置确定性拆分绑定。"
            "required_fact_ids 是修复前已通过的批次级确定性覆盖义务，必须在修复后的绑定中全部得到覆盖；"
            "输入中的事实保留清单是逐条核对表，提交前必须逐个确认清单中的 fact_id 出现在修复后的绑定中；"
            "收到删除或合并建议时，先判断相关事实是否属于不同商品、价格、数量、状态或边界；若业务属性不同则不得删除，"
            "若确需调整步骤则必须把事实改写到语义匹配的步骤，禁止为通过校验而机械挂载。"
            "动态值按来源事实原样保留，不做取值判断。"
            "当 _platform_repair.mode=minimal_patch 时，以 candidate_output 为上一版基线，只修复 validation_feedback 指出的"
            "覆盖或契约问题并保留其余有效修改，禁止再次整批改写。"
            "当 _platform_repair.mode=full_regeneration 时，说明上一版候选结构已损坏且不会提供 candidate_output；"
            "必须重新读取当前原始输入并完整生成目标批次，不得拼接、补猜或延续上一版残片。"
            "test_design_items 是当前批次允许引用的测试设计覆盖项；修复后所有用例的 test_design_item_ids 合集"
            "必须完整覆盖这些编号，不得引用输入外编号。"
            "最终 JSON 顶层只能包含 case_patches，不得输出 test_cases、说明、统计或修复摘要。"
            "每个补丁只能包含 case_id 以及确实需要替换的 title、priority、preconditions、steps、tags、"
            "test_design_item_ids 字段；省略字段由平台保持原值。修改步骤或前置条件时必须完整输出该字段的新数组。"
        ),
        "model": "",
        "output_schema": MODEL_REPAIR_PATCH_SCHEMA,
        "runtime_config": {
            # 批次修复需要重组跨生命周期结构，使用主模型提升复杂迁移的稳定性；终审仍保持独立评审路由。
            "model_route": "main",
            "disable_server_output_schema": True,
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 10000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_generation_global_reviewer",
        "name": "测试用例全局终审智能体",
        "version": 2,
        "description": "基于精简用例索引生成可路由的跨批重复、优先级与可执行性修复差异。",
        "instructions": (
            "你是测试用例全局终审 Agent。输入只有 case_index、batch_review 和 audit_summary，"
            "不得要求或补造完整需求正文。只检查跨批语义重复、业务目标和风险实质相同用例的优先级明显冲突，"
            "以及 case_index 中首个动作和最终预期无法形成可执行闭环的新增问题。"
            "batch_review 和 audit_summary 只是已有结论背景；不得复制、改写或重新输出其 differences。"
            "approved 只表示全局层是否发现新增问题；即使批次层未通过，全局层无新问题时仍应 approved=true，"
            "平台会在合并层统一决定最终状态。"
            "case_index 不包含完整步骤和事实正文，不得引用中间步骤、事实编号或推断隐藏业务规则。"
            "模块用例数量不相等不代表覆盖失衡；缺少模块规划和风险基准时，不得仅按数量输出 coverage_imbalance。"
            "approved=true 时 differences 必须为空；approved=false 时 differences 必须非空。"
            "differences 中每项都必须用 case_id 指向需要修改的具体用例，且只能引用 case_index 中的编号；"
            "不得使用 null，related_fact_ids 必须为空数组，平台会按 case_id 将差异送入统一修复和复审；"
            "每项必须包含 category、field_path、detail 和 repair_instruction。"
            "跨批语义重复使用 semantic_duplicate，有明确规划基准时的模块覆盖失衡使用 coverage_imbalance，"
            "优先级明显冲突使用 priority_conflict，无法形成可执行闭环使用 executability；"
            "不得为了继承批次结论而重复输出原 category。"
            "最终 JSON 必须且只能包含 phase、approved、summary、differences；"
            "phase 固定为 final_review，summary 必须用非空中文概括全局结论。"
            "不得重新输出测试用例。"
        ),
        "model": "",
        "output_schema": GLOBAL_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 4000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "disable_server_output_schema": True,
            "output_postprocessor": "testing.postprocess_global_final_review_output",
            "tool_keys": [],
        },
    },
)

# 阶段节点是跨 Agent 的持久化恢复边界；高成本重复任务使用 agent_map 逐项落盘。
BUILTIN_WORKFLOW_SPECS = (
    {
        "workflow_key": "test_generation",
        "version": 1,
        "name": "多 Agent 测试用例生成",
        "description": "按来源、规划、权威协调、生成和终审分阶段执行；页级与批次级结果可独立恢复。",
        "definition": {
            "execution_mode": "dag",
            "input_schema": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "requirement_doc_id": {"type": ["integer", "null"], "minimum": 1},
                    "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
                    "batch_case_limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "disable_result_cache": {"type": "boolean"},
                    "enable_context_compression": {"type": "boolean"},
                    "context_compression_max_tokens": {
                        "type": "integer",
                        "minimum": 128,
                        "maximum": 32768,
                    },
                    # 中文注释：兼容旧客户端传入的压缩开关，规范值由服务层解析。
                    "compress": {"type": "boolean"},
                },
                "required": [
                    "requirement",
                    "requirement_doc_id",
                    "case_budget",
                    "batch_case_limit",
                ],
                "anyOf": [
                    {"properties": {"requirement": {"type": "string", "minLength": 1}}},
                    {
                        "properties": {
                            "requirement_doc_id": {"type": "integer", "minimum": 1}
                        }
                    },
                ],
                "additionalProperties": False,
            },
            "display_stages": [
                {
                    "stage_key": "planning",
                    "label": "需求理解与规划",
                    "description": "提取来源证据，完成语义分析、路由与事实协调",
                    "node_keys": [
                        "evidence", "prepare_source_semantics",
                        "source_text", "source_vision", "source_semantics",
                        "prepare_plan_batches", "plan_batches",
                        "prepare_plan_consolidation", "plan",
                        "prepare_plan_routes", "plan_routes",
                        "prepare_plan_route_repairs", "plan_route_repairs", "routed_plan",
                        "prepare_authority", "authority", "effective_facts",
                    ],
                },
                {
                    "stage_key": "generation",
                    "label": "子智能体生成",
                    "description": "按计划分批生成并汇总真实用例",
                    "node_keys": ["prepare_generation", "generation", "generated_cases"],
                },
                {
                    "stage_key": "audit",
                    "label": "确定性审计",
                    "description": "检查事实覆盖、无效引用与编号冲突",
                    "node_keys": ["audit"],
                },
                {
                    "stage_key": "review_delivery",
                    "label": "终审与交付",
                    "description": "分批复核、按需修复，完成全局终审后持久化",
                    "node_keys": [
                        "prepare_final_review", "final_review_batches",
                        "prepare_final_review_repairs", "final_review_repairs",
                        "repaired_cases", "audit_repaired",
                        "prepare_final_review_rechecks", "final_review_rechecks",
                        "prepare_followup_final_review_repairs",
                        "followup_final_review_repairs", "final_repaired_cases",
                        "audit_final_repaired", "prepare_final_review_final_rechecks",
                        "final_review_final_rechecks", "merged_final_review_rechecks",
                        "preterminal_review_summary", "global_review_input", "global_review",
                        "prepare_terminal_final_review_repairs",
                        "terminal_final_review_repairs", "terminal_repaired_cases",
                        "audit_terminal_repaired", "prepare_terminal_final_review_rechecks",
                        "terminal_final_review_rechecks", "batch_review_summary",
                        "approved_cases", "validated_cases",
                        "chain_context", "chain_selection", "execution_chain", "persist",
                    ],
                },
            ],
            "nodes": [
                {
                    "node_key": "evidence",
                    "node_type": "tool",
                    "reference_key": "resolve_requirement_evidence",
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
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "evidence_source": "dependencies.evidence.source",
                        "evidence_catalog": "dependencies.evidence.evidence_catalog",
                    },
                },
                {
                    "node_key": "source_text",
                    "node_type": "agent_map",
                    "reference_key": "test_source_text_semantics_analyst",
                    "depends_on": ["prepare_source_semantics"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1800,
                    "input_mapping": {
                        "items": "dependencies.prepare_source_semantics.text_items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 200,
                        "max_concurrency": 6,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_source_semantics_item",
                    },
                },
                {
                    "node_key": "source_vision",
                    "node_type": "agent_map",
                    "reference_key": "test_source_semantics_analyst",
                    "depends_on": ["prepare_source_semantics"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1800,
                    "input_mapping": {
                        "items": "dependencies.prepare_source_semantics.vision_items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 200,
                        "max_concurrency": 3,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_source_semantics_item",
                    },
                },
                {
                    "node_key": "source_semantics",
                    "node_type": "tool",
                    "reference_key": "merge_source_semantics",
                    "depends_on": ["prepare_source_semantics", "source_text", "source_vision"],
                    "input_mapping": {
                        "text_inputs": "dependencies.prepare_source_semantics.text_items",
                        "text_records": "dependencies.source_text.items",
                        "vision_inputs": "dependencies.prepare_source_semantics.vision_items",
                        "vision_records": "dependencies.source_vision.items",
                    },
                },
                {
                    "node_key": "prepare_plan_batches",
                    "node_type": "tool",
                    "reference_key": "prepare_business_plan_batches",
                    "depends_on": ["source_semantics"],
                    "input_mapping": {
                        "planning_scopes": "dependencies.source_semantics.planning_scopes",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "plan_batches",
                    "node_type": "agent_map",
                    "reference_key": "test_business_plan_batcher",
                    "depends_on": ["prepare_plan_batches"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1200,
                    "input_mapping": {
                        "items": "dependencies.prepare_plan_batches.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 6,
                        "allow_empty": False,
                    },
                },
                {
                    "node_key": "prepare_plan_consolidation",
                    "node_type": "tool",
                    "reference_key": "prepare_business_plan_consolidation",
                    "depends_on": ["prepare_plan_batches", "plan_batches"],
                    "input_mapping": {
                        "prepared_items": "dependencies.prepare_plan_batches.items",
                        "plan_records": "dependencies.plan_batches.items",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "plan",
                    "node_type": "agent",
                    "reference_key": "test_business_planner",
                    "depends_on": ["prepare_plan_consolidation"],
                    "max_attempts": 2,
                    "time_budget_seconds": 360,
                    "input_mapping": {
                        "partial_plans": "dependencies.prepare_plan_consolidation.partial_plans",
                        "planning_metadata": "dependencies.prepare_plan_consolidation.planning_metadata",
                        "coverage_group_catalog": "dependencies.prepare_plan_consolidation.coverage_group_catalog",
                        "case_budget": "dependencies.prepare_plan_consolidation.case_budget",
                        "planning_limits": "dependencies.prepare_plan_consolidation.planning_limits",
                    },
                },
                {
                    "node_key": "prepare_plan_routes",
                    "node_type": "tool",
                    "reference_key": "prepare_planning_scope_routes",
                    "depends_on": ["plan", "source_semantics"],
                    "input_mapping": {
                        "plan": "dependencies.plan",
                        "planning_scopes": "dependencies.source_semantics.planning_scopes",
                    },
                },
                {
                    "node_key": "plan_routes",
                    "node_type": "agent_map",
                    "reference_key": "test_planning_scope_router",
                    "depends_on": ["prepare_plan_routes"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1200,
                    "input_mapping": {
                        "items": "dependencies.prepare_plan_routes.batch_items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 200,
                        "max_concurrency": 4,
                        "allow_empty": False,
                        "item_postprocessor": (
                            "testing.postprocess_planning_scope_routing_item"
                        ),
                    },
                },
                {
                    "node_key": "prepare_plan_route_repairs",
                    "node_type": "tool",
                    "reference_key": "prepare_planning_route_repairs",
                    "depends_on": ["plan", "prepare_plan_routes", "plan_routes"],
                    "input_mapping": {
                        "plan": "dependencies.plan",
                        "prepared_items": "dependencies.prepare_plan_routes.batch_items",
                        "route_records": "dependencies.plan_routes.items",
                    },
                },
                {
                    "node_key": "plan_route_repairs",
                    "node_type": "agent_map",
                    "reference_key": "test_planning_route_gap_reviewer",
                    "depends_on": ["prepare_plan_route_repairs"],
                    "max_attempts": 2,
                    "time_budget_seconds": 360,
                    "input_mapping": {
                        "items": "dependencies.prepare_plan_route_repairs.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 2,
                        "allow_empty": True,
                        "item_postprocessor": (
                            "testing.postprocess_planning_route_repair_item"
                        ),
                    },
                },
                {
                    "node_key": "routed_plan",
                    "node_type": "tool",
                    "reference_key": "merge_planning_scope_routes",
                    "depends_on": [
                        "plan", "prepare_plan_routes", "plan_routes", "plan_route_repairs"
                    ],
                    "input_mapping": {
                        "plan": "dependencies.plan",
                        "prepared_items": "dependencies.prepare_plan_routes.batch_items",
                        "route_records": "dependencies.plan_routes.items",
                        "repair_records": "dependencies.plan_route_repairs.items",
                    },
                },
                {
                    "node_key": "prepare_authority",
                    "node_type": "tool",
                    "reference_key": "prepare_authority_reconciliation",
                    "depends_on": ["routed_plan", "source_semantics"],
                    "input_mapping": {
                        "plan": "dependencies.routed_plan",
                        "authoritative_facts": "dependencies.source_semantics.authoritative_facts",
                    },
                },
                {
                    "node_key": "authority",
                    "node_type": "agent_map",
                    "reference_key": "test_authority_reconciliation_reviewer",
                    "depends_on": ["prepare_authority"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1200,
                    "input_mapping": {
                        "items": "dependencies.prepare_authority.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 2,
                        "allow_empty": True,
                        "item_postprocessor": (
                            "testing.postprocess_authority_reconciliation_item"
                        ),
                    },
                },
                {
                    "node_key": "effective_facts",
                    "node_type": "tool",
                    "reference_key": "merge_authority_reconciliation",
                    "depends_on": ["source_semantics", "prepare_authority", "authority"],
                    "input_mapping": {
                        "authoritative_facts": "dependencies.source_semantics.authoritative_facts",
                        "prepared_items": "dependencies.prepare_authority.items",
                        "reconciliation_records": "dependencies.authority.items",
                    },
                },
                {
                    "node_key": "prepare_generation",
                    "node_type": "tool",
                    "reference_key": "prepare_test_case_batches",
                    "depends_on": ["routed_plan", "effective_facts"],
                    "input_mapping": {
                        "plan": "dependencies.routed_plan",
                        "effective_facts": "dependencies.effective_facts.effective_facts",
                        "case_budget": "input.case_budget",
                        "batch_case_limit": "input.batch_case_limit",
                    },
                },
                {
                    "node_key": "generation",
                    "node_type": "agent_map",
                    "reference_key": "test_case_generator",
                    "depends_on": ["prepare_generation"],
                    "max_attempts": 3,
                    "time_budget_seconds": 3600,
                    "input_mapping": {
                        "items": "dependencies.prepare_generation.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 6,
                        "allow_empty": False,
                        "item_postprocessor": "testing.postprocess_generation_batch_item",
                    },
                },
                {
                    "node_key": "generated_cases",
                    "node_type": "tool",
                    "reference_key": "merge_grounded_generation_batches",
                    "depends_on": ["prepare_generation", "generation"],
                    "input_mapping": {
                        "generation_inputs": "dependencies.prepare_generation.items",
                        "generation_records": "dependencies.generation.items",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "audit",
                    "node_type": "tool",
                    "reference_key": "build_generation_audit_summary",
                    "depends_on": ["effective_facts", "prepare_generation", "generated_cases"],
                    "input_mapping": {
                        "authoritative_facts": "dependencies.effective_facts.authoritative_facts",
                        "generation": "dependencies.generated_cases",
                        "generation_inputs": "dependencies.prepare_generation.items",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "prepare_final_review",
                    "node_type": "tool",
                    "reference_key": "prepare_final_review_batches",
                    "depends_on": ["prepare_generation", "generated_cases"],
                    "input_mapping": {
                        "generation_inputs": "dependencies.prepare_generation.items",
                        "generation": "dependencies.generated_cases",
                        "batch_case_limit": "input.batch_case_limit",
                    },
                },
                {
                    "node_key": "final_review_batches",
                    "node_type": "agent_map",
                    "reference_key": "test_generation_final_reviewer",
                    "depends_on": ["prepare_final_review"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1800,
                    "input_mapping": {
                        "items": "dependencies.prepare_final_review.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 3,
                        "allow_empty": False,
                        "item_postprocessor": "testing.postprocess_final_review_batch_item",
                    },
                },
                {
                    "node_key": "prepare_final_review_repairs",
                    "node_type": "tool",
                    "reference_key": "prepare_final_review_repairs",
                    "depends_on": [
                        "prepare_generation",
                        "prepare_final_review",
                        "final_review_batches",
                    ],
                    "input_mapping": {
                        "review_inputs": "dependencies.prepare_final_review.items",
                        "review_records": "dependencies.final_review_batches.items",
                        "generation_inputs": "dependencies.prepare_generation.items",
                    },
                },
                {
                    "node_key": "final_review_repairs",
                    "node_type": "agent_map",
                    "reference_key": "test_generation_batch_repairer",
                    "depends_on": ["prepare_final_review_repairs"],
                    "max_attempts": 3,
                    "time_budget_seconds": 3600,
                    "input_mapping": {
                        "items": "dependencies.prepare_final_review_repairs.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 3,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_final_review_repair_item",
                    },
                },
                {
                    "node_key": "repaired_cases",
                    "node_type": "tool",
                    "reference_key": "merge_final_review_repairs",
                    "depends_on": [
                        "generated_cases",
                        "prepare_final_review_repairs",
                        "final_review_repairs",
                    ],
                    "input_mapping": {
                        "generation": "dependencies.generated_cases",
                        "repair_inputs": "dependencies.prepare_final_review_repairs.items",
                        "repair_records": "dependencies.final_review_repairs.items",
                    },
                },
                {
                    "node_key": "audit_repaired",
                    "node_type": "tool",
                    "reference_key": "build_generation_audit_summary",
                    "depends_on": [
                        "effective_facts",
                        "prepare_generation",
                        "repaired_cases",
                    ],
                    "input_mapping": {
                        "authoritative_facts": "dependencies.effective_facts.authoritative_facts",
                        "generation": "dependencies.repaired_cases",
                        "generation_inputs": "dependencies.prepare_generation.items",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "prepare_final_review_rechecks",
                    "node_type": "tool",
                    "reference_key": "prepare_final_review_rechecks",
                    "depends_on": [
                        "prepare_generation",
                        "prepare_final_review_repairs",
                        "repaired_cases",
                    ],
                    "input_mapping": {
                        "repair_inputs": "dependencies.prepare_final_review_repairs.items",
                        "generation": "dependencies.repaired_cases",
                        "generation_inputs": "dependencies.prepare_generation.items",
                    },
                },
                {
                    "node_key": "final_review_rechecks",
                    "node_type": "agent_map",
                    "reference_key": "test_generation_final_reviewer",
                    "depends_on": ["prepare_final_review_rechecks"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1800,
                    "input_mapping": {
                        "items": "dependencies.prepare_final_review_rechecks.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 3,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_final_review_batch_item",
                    },
                },
                {
                    "node_key": "prepare_followup_final_review_repairs",
                    "node_type": "tool",
                    "reference_key": "prepare_final_review_repairs",
                    "depends_on": [
                        "prepare_generation",
                        "prepare_final_review_rechecks",
                        "final_review_rechecks",
                    ],
                    "input_mapping": {
                        "review_inputs": "dependencies.prepare_final_review_rechecks.items",
                        "review_records": "dependencies.final_review_rechecks.items",
                        "generation_inputs": "dependencies.prepare_generation.items",
                    },
                },
                {
                    "node_key": "followup_final_review_repairs",
                    "node_type": "agent_map",
                    "reference_key": "test_generation_batch_repairer",
                    "depends_on": ["prepare_followup_final_review_repairs"],
                    "max_attempts": 3,
                    "time_budget_seconds": 3600,
                    "input_mapping": {
                        "items": "dependencies.prepare_followup_final_review_repairs.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 3,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_final_review_repair_item",
                    },
                },
                {
                    "node_key": "final_repaired_cases",
                    "node_type": "tool",
                    "reference_key": "merge_final_review_repairs",
                    "depends_on": [
                        "repaired_cases",
                        "prepare_followup_final_review_repairs",
                        "followup_final_review_repairs",
                    ],
                    "input_mapping": {
                        "generation": "dependencies.repaired_cases",
                        "repair_inputs": "dependencies.prepare_followup_final_review_repairs.items",
                        "repair_records": "dependencies.followup_final_review_repairs.items",
                    },
                },
                {
                    "node_key": "audit_final_repaired",
                    "node_type": "tool",
                    "reference_key": "build_generation_audit_summary",
                    "depends_on": [
                        "effective_facts",
                        "prepare_generation",
                        "final_repaired_cases",
                    ],
                    "input_mapping": {
                        "authoritative_facts": "dependencies.effective_facts.authoritative_facts",
                        "generation": "dependencies.final_repaired_cases",
                        "generation_inputs": "dependencies.prepare_generation.items",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "prepare_final_review_final_rechecks",
                    "node_type": "tool",
                    "reference_key": "prepare_final_review_rechecks",
                    "depends_on": [
                        "prepare_generation",
                        "prepare_followup_final_review_repairs",
                        "final_repaired_cases",
                    ],
                    "input_mapping": {
                        "repair_inputs": "dependencies.prepare_followup_final_review_repairs.items",
                        "generation": "dependencies.final_repaired_cases",
                        "generation_inputs": "dependencies.prepare_generation.items",
                    },
                },
                {
                    "node_key": "final_review_final_rechecks",
                    "node_type": "agent_map",
                    "reference_key": "test_generation_final_reviewer",
                    "depends_on": ["prepare_final_review_final_rechecks"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1800,
                    "input_mapping": {
                        "items": "dependencies.prepare_final_review_final_rechecks.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 3,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_final_review_batch_item",
                    },
                },
                {
                    "node_key": "merged_final_review_rechecks",
                    "node_type": "tool",
                    "reference_key": "merge_final_review_recheck_records",
                    "depends_on": [
                        "prepare_final_review_rechecks",
                        "final_review_rechecks",
                        "prepare_final_review_final_rechecks",
                        "final_review_final_rechecks",
                    ],
                    "input_mapping": {
                        "baseline_inputs": "dependencies.prepare_final_review_rechecks.items",
                        "baseline_records": "dependencies.final_review_rechecks.items",
                        "replacement_inputs": "dependencies.prepare_final_review_final_rechecks.items",
                        "replacement_records": "dependencies.final_review_final_rechecks.items",
                    },
                },
                {
                    "node_key": "prepare_terminal_final_review_repairs",
                    "node_type": "tool",
                    "reference_key": "prepare_terminal_final_review_repairs",
                    "depends_on": [
                        "prepare_generation",
                        "final_repaired_cases",
                        "preterminal_review_summary",
                        "global_review",
                    ],
                    "input_mapping": {
                        "generation_inputs": "dependencies.prepare_generation.items",
                        "generation": "dependencies.final_repaired_cases",
                        "batch_case_limit": "input.batch_case_limit",
                        "batch_review": "dependencies.preterminal_review_summary",
                        "global_review": "dependencies.global_review",
                    },
                },
                {
                    "node_key": "terminal_final_review_repairs",
                    "node_type": "agent_map",
                    "reference_key": "test_generation_batch_repairer",
                    "depends_on": ["prepare_terminal_final_review_repairs"],
                    "max_attempts": 3,
                    "time_budget_seconds": 1800,
                    "input_mapping": {
                        "items": "dependencies.prepare_terminal_final_review_repairs.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 3,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_final_review_repair_item",
                    },
                },
                {
                    "node_key": "terminal_repaired_cases",
                    "node_type": "tool",
                    "reference_key": "merge_final_review_repairs",
                    "depends_on": [
                        "final_repaired_cases",
                        "prepare_terminal_final_review_repairs",
                        "terminal_final_review_repairs",
                    ],
                    "input_mapping": {
                        "generation": "dependencies.final_repaired_cases",
                        "repair_inputs": "dependencies.prepare_terminal_final_review_repairs.items",
                        "repair_records": "dependencies.terminal_final_review_repairs.items",
                    },
                },
                {
                    "node_key": "audit_terminal_repaired",
                    "node_type": "tool",
                    "reference_key": "build_generation_audit_summary",
                    "depends_on": [
                        "effective_facts",
                        "prepare_generation",
                        "terminal_repaired_cases",
                    ],
                    "input_mapping": {
                        "authoritative_facts": "dependencies.effective_facts.authoritative_facts",
                        "generation": "dependencies.terminal_repaired_cases",
                        "generation_inputs": "dependencies.prepare_generation.items",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "prepare_terminal_final_review_rechecks",
                    "node_type": "tool",
                    "reference_key": "prepare_final_review_rechecks",
                    "depends_on": [
                        "prepare_generation",
                        "prepare_terminal_final_review_repairs",
                        "terminal_repaired_cases",
                    ],
                    "input_mapping": {
                        "repair_inputs": "dependencies.prepare_terminal_final_review_repairs.items",
                        "generation": "dependencies.terminal_repaired_cases",
                        "generation_inputs": "dependencies.prepare_generation.items",
                    },
                },
                {
                    "node_key": "terminal_final_review_rechecks",
                    "node_type": "agent_map",
                    "reference_key": "test_generation_final_reviewer",
                    "depends_on": ["prepare_terminal_final_review_rechecks"],
                    "max_attempts": 2,
                    "time_budget_seconds": 1800,
                    "input_mapping": {
                        "items": "dependencies.prepare_terminal_final_review_rechecks.items",
                    },
                    "map_config": {
                        "items_key": "items",
                        "output_key": "items",
                        "max_items": 100,
                        "max_concurrency": 3,
                        "allow_empty": True,
                        "item_postprocessor": "testing.postprocess_final_review_batch_item",
                    },
                },
                {
                    "node_key": "batch_review_summary",
                    "node_type": "tool",
                    "reference_key": "merge_final_review_batches",
                    "depends_on": [
                        "prepare_terminal_final_review_repairs",
                        "prepare_terminal_final_review_rechecks",
                        "terminal_final_review_rechecks",
                        "audit_terminal_repaired",
                    ],
                    "input_mapping": {
                        "review_inputs": "dependencies.prepare_terminal_final_review_repairs.review_inputs",
                        "review_records": "dependencies.prepare_terminal_final_review_repairs.review_records",
                        "repair_inputs": "dependencies.prepare_terminal_final_review_repairs.items",
                        "recheck_inputs": "dependencies.prepare_terminal_final_review_rechecks.items",
                        "recheck_records": "dependencies.terminal_final_review_rechecks.items",
                        "audit_summary": "dependencies.audit_terminal_repaired",
                    },
                },
                {
                    "node_key": "preterminal_review_summary",
                    "node_type": "tool",
                    "reference_key": "merge_final_review_batches",
                    "depends_on": [
                        "prepare_final_review",
                        "final_review_batches",
                        "prepare_final_review_repairs",
                        "prepare_final_review_rechecks",
                        "merged_final_review_rechecks",
                        "audit_final_repaired",
                    ],
                    "input_mapping": {
                        "review_inputs": "dependencies.prepare_final_review.items",
                        "review_records": "dependencies.final_review_batches.items",
                        "repair_inputs": "dependencies.prepare_final_review_repairs.items",
                        "recheck_inputs": "dependencies.prepare_final_review_rechecks.items",
                        "recheck_records": "dependencies.merged_final_review_rechecks.items",
                        "audit_summary": "dependencies.audit_final_repaired",
                    },
                },
                {
                    "node_key": "global_review_input",
                    "node_type": "tool",
                    "reference_key": "prepare_global_final_review",
                    "depends_on": [
                        "final_repaired_cases",
                        "preterminal_review_summary",
                        "audit_final_repaired",
                    ],
                    "input_mapping": {
                        "generation": "dependencies.final_repaired_cases",
                        "batch_review": "dependencies.preterminal_review_summary",
                        "audit_summary": "dependencies.audit_final_repaired",
                    },
                },
                {
                    "node_key": "global_review",
                    "node_type": "agent",
                    "reference_key": "test_generation_global_reviewer",
                    "depends_on": ["global_review_input"],
                    "max_attempts": 2,
                    "time_budget_seconds": 600,
                    "input_mapping": {
                        "case_index": "dependencies.global_review_input.case_index",
                        "batch_review": "dependencies.global_review_input.batch_review",
                        "audit_summary": "dependencies.global_review_input.audit_summary",
                    },
                },
                {
                    "node_key": "approved_cases",
                    "node_type": "tool",
                    "reference_key": "approve_synthesized_test_cases",
                    "depends_on": [
                        "terminal_repaired_cases",
                        "audit_terminal_repaired",
                        "batch_review_summary",
                    ],
                    "input_mapping": {
                        "generation": "dependencies.terminal_repaired_cases",
                        "audit_summary": "dependencies.audit_terminal_repaired",
                        "final_review": "dependencies.batch_review_summary",
                        "case_budget": "input.case_budget",
                    },
                },
                {
                    "node_key": "validated_cases",
                    "node_type": "tool",
                    "reference_key": "validate_test_cases",
                    "depends_on": ["evidence", "approved_cases"],
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "case_budget": "input.case_budget",
                        "test_cases": "dependencies.approved_cases.test_cases",
                    },
                },
                {
                    "node_key": "chain_context",
                    "node_type": "tool",
                    "reference_key": "prepare_execution_chain",
                    "depends_on": ["routed_plan", "validated_cases"],
                    "input_mapping": {
                        "plan": "dependencies.routed_plan",
                        "test_cases": "dependencies.validated_cases.test_cases",
                    },
                },
                {
                    "node_key": "chain_selection",
                    "node_type": "tool",
                    "reference_key": "select_execution_chain",
                    "depends_on": ["chain_context"],
                    "input_mapping": {
                        "plan_summary": "dependencies.chain_context.plan_summary",
                        "candidate_chains": "dependencies.chain_context.candidate_chains",
                    },
                },
                {
                    "node_key": "execution_chain",
                    "node_type": "tool",
                    "reference_key": "validate_execution_chain",
                    "depends_on": ["validated_cases", "chain_selection"],
                    "input_mapping": {
                        "test_cases": "dependencies.validated_cases.test_cases",
                        "chain_selection": "dependencies.chain_selection",
                    },
                },
                {
                    "node_key": "persist",
                    "node_type": "tool",
                    "reference_key": "persist_test_cases",
                    "depends_on": ["evidence", "approved_cases", "validated_cases", "execution_chain"],
                    "input_mapping": {
                        "requirement": "dependencies.evidence.requirement",
                        "evidence_source": "dependencies.evidence.source",
                        "test_cases": "dependencies.validated_cases.test_cases",
                        "case_fact_bindings": "dependencies.approved_cases.case_fact_bindings",
                        "execution_plan": "dependencies.execution_chain.execution_plan",
                        "final_review": "dependencies.approved_cases.final_review",
                    },
                },
            ],
            "output_node_key": "persist",
        },
    },
)


def register_test_generation_tools(registry: ToolRegistry) -> None:
    registry.register(
        "testing.submit_business_plan",
        submit_business_plan,
        parallel_safe=True,
    )
    registry.register(
        "testing.submit_source_semantics",
        submit_source_semantics,
        parallel_safe=True,
    )
    registry.register(
        "testing.submit_generation_batch",
        submit_generation_batch,
        parallel_safe=True,
    )
    registry.register(
        "testing.submit_scenario_design_guidance",
        submit_scenario_design_guidance,
        parallel_safe=True,
    )
    registry.register("testing.resolve_requirement_evidence", resolve_requirement_evidence)
    registry.register(
        "testing.validate_business_plan_output",
        validate_business_plan_output,
    )
    registry.register(
        "testing.validate_business_plan_draft_output",
        validate_business_plan_draft_output,
    )
    registry.register(
        "testing.validate_scenario_design_guidance",
        validate_scenario_design_guidance,
    )
    registry.register("testing.prepare_source_semantics", prepare_source_semantics)
    registry.register(
        "testing.postprocess_source_semantics_item",
        postprocess_source_semantics_item,
    )
    registry.register(
        "testing.postprocess_authority_reconciliation_item",
        postprocess_authority_reconciliation_item,
    )
    registry.register("testing.merge_source_semantics", merge_source_semantics)
    registry.register(
        "testing.prepare_business_plan_batches",
        prepare_business_plan_batches,
    )
    registry.register(
        "testing.prepare_business_plan_consolidation",
        prepare_business_plan_consolidation,
    )
    registry.register(
        "testing.prepare_planning_scope_routes",
        prepare_planning_scope_routes,
    )
    registry.register(
        "testing.postprocess_planning_scope_routing_item",
        postprocess_planning_scope_routing_item,
    )
    registry.register(
        "testing.prepare_planning_route_repairs",
        prepare_planning_route_repairs,
    )
    registry.register(
        "testing.postprocess_planning_route_repair_item",
        postprocess_planning_route_repair_item,
    )
    registry.register(
        "testing.merge_planning_scope_routes",
        merge_planning_scope_routes,
    )
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
        "testing.postprocess_generation_batch_item",
        postprocess_generation_batch_item,
    )
    registry.register(
        "testing.merge_grounded_generation_batches",
        merge_grounded_generation_batches,
    )
    registry.register(
        "testing.build_generation_audit_summary",
        build_generation_audit_summary,
    )
    registry.register(
        "testing.prepare_final_review_batches",
        prepare_final_review_batches,
    )
    registry.register(
        "testing.postprocess_final_review_batch_item",
        postprocess_final_review_batch_item,
    )
    registry.register(
        "testing.prepare_final_review_repairs",
        prepare_final_review_repairs,
    )
    registry.register(
        "testing.postprocess_final_review_repair_item",
        postprocess_final_review_repair_item,
    )
    registry.register(
        "testing.merge_final_review_repairs",
        merge_final_review_repairs,
    )
    registry.register(
        "testing.prepare_final_review_rechecks",
        prepare_final_review_rechecks,
    )
    registry.register(
        "testing.merge_final_review_batches",
        merge_final_review_batches,
    )
    registry.register(
        "testing.merge_final_review_recheck_records",
        merge_final_review_recheck_records,
    )
    registry.register(
        "testing.prepare_global_final_review",
        prepare_global_final_review,
    )
    registry.register(
        "testing.postprocess_global_final_review_output",
        postprocess_global_final_review_output,
    )
    registry.register(
        "testing.prepare_terminal_final_review_repairs",
        prepare_terminal_final_review_repairs,
    )
    registry.register(
        "testing.approve_synthesized_test_cases",
        approve_synthesized_test_cases,
    )
    registry.register("testing.validate_test_cases", validate_generated_test_cases)
    registry.register("testing.prepare_execution_chain", prepare_execution_chain_context)
    registry.register("testing.select_execution_chain", select_execution_chain)
    registry.register("testing.validate_execution_chain", validate_execution_chain)
    registry.register("testing.persist_test_cases", persist_generated_test_cases)
