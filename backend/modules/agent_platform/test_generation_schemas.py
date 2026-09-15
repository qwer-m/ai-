"""测试生成工作流使用的契约 Schema 与静态编译约束。

这些定义独立于业务准备、校验和持久化函数，避免大模块同时承担数据契约与流程编排。
"""

from copy import deepcopy
from typing import Any

from .test_generation_review import GLOBAL_REVIEW_CASE_INDEX_SCHEMA
from .test_generation_semantics import GOVERNANCE_RELATION_ALIASES

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
        "test_input": {"type": "string"},
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
        "test_input",
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
        "test_input": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "fact_ids": OPTIONAL_FACT_ID_LIST_SCHEMA,
            },
            "required": ["text", "fact_ids"],
            "additionalProperties": False,
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
        "test_input",
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
        "test_input_fact_ids": OPTIONAL_FACT_ID_LIST_SCHEMA,
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
    "required": [
        "case_id",
        "precondition_bindings",
        "test_input_fact_ids",
        "step_bindings",
    ],
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


IMAGE_REGION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {"type": "number", "minimum": 0, "maximum": 1},
        "y": {"type": "number", "minimum": 0, "maximum": 1},
        "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
    },
    "required": ["x", "y", "width", "height"],
    "additionalProperties": False,
}


PLANNING_EVIDENCE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "evidence_id": {"type": "string", "pattern": "^EV-[0-9]{4,}$"},
        "document_id": {"type": ["integer", "null"], "minimum": 1},
        "chunk_index": {"type": "integer", "minimum": 0},
        "biz_key": {"type": "string"},
        "text": {"type": "string"},
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
        "image_region": IMAGE_REGION_SCHEMA,
        "page_image_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
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
    # 图片证据使用真实页面区域；空文本与零偏移仅表示没有正文坐标。
    "oneOf": [
        {
            "properties": {"text": {"type": "string", "minLength": 1}},
            "not": {
                "anyOf": [
                    {"required": ["image_region"]},
                    {"required": ["page_image_sha256"]},
                ]
            },
        },
        {
            "properties": {
                "document_id": {"type": "integer", "minimum": 1},
                "page_number": {"type": "integer", "minimum": 1},
                "block_ids": {"minItems": 1},
                "text": {"const": ""},
                "source_offset_start": {"const": 0},
                "source_offset_end": {"const": 0},
                "continuation": {"type": "null"},
            },
            "required": ["image_region", "page_image_sha256"],
        },
    ],
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
                "source_kind": {"type": "string", "const": "document"},
                "document_id": {"type": "integer", "minimum": 1},
                "page_number": {"type": "integer", "minimum": 1},
                "block_id": {"type": "string", "minLength": 1},
                "image_region": IMAGE_REGION_SCHEMA,
                "asset_source_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                "page_image_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
            },
            "required": [
                "source_kind",
                "document_id",
                "page_number",
                "block_id",
                "image_region",
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
            "items": GLOBAL_REVIEW_CASE_INDEX_SCHEMA,
        },
        "batch_review": FINAL_REVIEW_OUTPUT_SCHEMA,
        "audit_summary": GENERATION_AUDIT_SCHEMA,
    },
    "required": ["case_index", "batch_review", "audit_summary"],
    "additionalProperties": False,
}


# 来源分析是模型原始输出边界：页面来源只选择一个真实块，
# 正文引用、图片区域和作用域全部由平台根据真实页面确定性生成。
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
            "description": "只选择一个与原子事实最直接相关的真实 block_id；平台为正文块补齐 quote/source_span，为图片块补齐 image_region，并派生 scope_id。",
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
        "page_text": {"type": "string"},
        "blocks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "block_id": {"type": "string", "minLength": 1},
                            "text": {"type": "string", "minLength": 1},
                            "source_span": SOURCE_SPAN_SCHEMA,
                        },
                        "required": ["block_id", "text", "source_span"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "block_id": {"type": "string", "minLength": 1},
                            "type": {"type": "string", "const": "image"},
                            "bbox": IMAGE_REGION_SCHEMA,
                        },
                        "required": ["block_id", "type", "bbox"],
                        "additionalProperties": False,
                    },
                ],
            },
        },
        "asset_source_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
        "page_image_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
        "region": IMAGE_REGION_SCHEMA,
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
                    "image_region": IMAGE_REGION_SCHEMA,
                },
                "required": ["scope_id"],
                "additionalProperties": False,
                "oneOf": [
                    {"not": {"required": ["image_region"]}},
                    {
                        "required": ["image_region", "allowed_block_ids"],
                        "not": {
                            "anyOf": [
                                {"required": ["source_span"]},
                                {"required": ["source_offset_start"]},
                                {"required": ["source_offset_end"]},
                            ]
                        },
                    },
                ],
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



__all__ = ['ACTORS_SCHEMA', 'AUTHORITATIVE_FACT_SCHEMA', 'AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA', 'AUTHORITY_RECONCILIATION_DECISION_SCHEMA', 'AUTHORITY_RECONCILIATION_ITEM_SCHEMA', 'AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA', 'BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA', 'BATCH_FINAL_REVIEW_DIFFERENCE_CATEGORIES', 'BATCH_FINAL_REVIEW_DIFFERENCE_SCHEMA', 'BUSINESS_PLANNING_BATCH_MAX_FACTS', 'BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS', 'BUSINESS_PLAN_DRAFT_SCHEMA', 'CASE_FACT_BINDING_SCHEMA', 'CASE_SCHEMA', 'EVIDENCE_OUTPUT_SCHEMA', 'EVIDENCE_SOURCE_SCHEMA', 'EXECUTION_CHAIN_SELECTION_SCHEMA', 'EXECUTION_PLAN_SCHEMA', 'FACT_DESIGN_ROUTE_SCHEMA', 'FACT_ID_LIST_SCHEMA', 'FINAL_REVIEW_BATCH_INPUT_SCHEMA', 'FINAL_REVIEW_BATCH_META_SCHEMA', 'FINAL_REVIEW_OUTPUT_SCHEMA', 'FINAL_REVIEW_REPAIR_INPUT_SCHEMA', 'FINAL_REVIEW_REPAIR_RESULT_SCHEMA', 'GENERATION_AUDIT_SCHEMA', 'GLOBAL_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA', 'GLOBAL_FINAL_REVIEW_DIFFERENCE_CATEGORIES', 'GLOBAL_FINAL_REVIEW_INPUT_SCHEMA', 'GROUNDING_SCHEMA', 'IMAGE_REGION_SCHEMA', 'MERGED_GENERATION_SCHEMA', 'MODEL_GENERATION_CASE_SCHEMA', 'MODEL_GROUNDED_TEXT_SCHEMA', 'MODEL_GROUNDING_SCHEMA', 'MODEL_INLINE_CASE_SCHEMA', 'MODEL_REPAIR_CASE_PATCH_SCHEMA', 'MODEL_REPAIR_CASE_SCHEMA', 'MODEL_REPAIR_PATCH_SCHEMA', 'MODEL_STEP_FACT_BINDINGS_SCHEMA', 'OPTIONAL_FACT_ID_LIST_SCHEMA', 'ORDERED_MARKER_SCHEMA', 'PLANNER_AGENT_OUTPUT_SCHEMA', 'PLANNER_AGENT_SUBMISSION_SCHEMA', 'PLANNER_OUTPUT_SCHEMA', 'PLANNER_TEST_DESIGN_SCHEMA', 'PLANNER_TEST_POINT_SCHEMA', 'PLANNING_EVIDENCE_CATALOG_SCHEMA', 'PLANNING_EVIDENCE_ITEM_SCHEMA', 'PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA', 'PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA', 'PLANNING_SCOPE_ROUTE_BATCH_SIZE', 'PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS', 'PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA', 'PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA', 'PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA', 'PLAN_SCHEMA', 'REPAIR_AUTHORITATIVE_FACT_SCHEMA', 'REPAIR_SOURCE_ANCHOR_SCHEMA', 'REVIEW_FACT_SCHEMA', 'RISK_DETAIL_SCHEMA', 'RISK_OR_TEXTS_SCHEMA', 'SCENARIO_DESIGN_GUIDANCE_SCHEMA', 'SOURCE_ANCHOR_SCHEMA', 'SOURCE_SEMANTICS_AGENT_ANCHOR_SCHEMA', 'SOURCE_SEMANTICS_AGENT_FACT_SCHEMA', 'SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA', 'SOURCE_SEMANTICS_DOCUMENT_PAGE_SCHEMA', 'SOURCE_SEMANTICS_INPUT_SCHEMA', 'SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA', 'SOURCE_SEMANTICS_OUTPUT_SCHEMA', 'SOURCE_SPAN_SCHEMA', 'SYNTHESIS_APPROVAL_OUTPUT_SCHEMA', 'TEST_DESIGN_CATALOG_ITEM_SCHEMA', 'TEST_DESIGN_TECHNIQUES', 'TEXT_OR_TEXTS_SCHEMA']
