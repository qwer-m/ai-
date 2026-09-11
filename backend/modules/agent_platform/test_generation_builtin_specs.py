"""测试生成内置工具、智能体和工作流规格。"""

from typing import Any

from .test_generation_schemas import (
    ACTORS_SCHEMA,
    AUTHORITATIVE_FACT_SCHEMA,
    AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA,
    AUTHORITY_RECONCILIATION_DECISION_SCHEMA,
    AUTHORITY_RECONCILIATION_ITEM_SCHEMA,
    AUTHORITY_RECONCILIATION_OUTPUT_SCHEMA,
    BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA,
    BATCH_FINAL_REVIEW_DIFFERENCE_CATEGORIES,
    BATCH_FINAL_REVIEW_DIFFERENCE_SCHEMA,
    BUSINESS_PLANNING_BATCH_MAX_FACTS,
    BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS,
    BUSINESS_PLAN_DRAFT_SCHEMA,
    CASE_FACT_BINDING_SCHEMA,
    CASE_SCHEMA,
    EVIDENCE_OUTPUT_SCHEMA,
    EVIDENCE_SOURCE_SCHEMA,
    EXECUTION_CHAIN_SELECTION_SCHEMA,
    EXECUTION_PLAN_SCHEMA,
    FACT_DESIGN_ROUTE_SCHEMA,
    FACT_ID_LIST_SCHEMA,
    FINAL_REVIEW_BATCH_INPUT_SCHEMA,
    FINAL_REVIEW_BATCH_META_SCHEMA,
    FINAL_REVIEW_OUTPUT_SCHEMA,
    FINAL_REVIEW_REPAIR_INPUT_SCHEMA,
    FINAL_REVIEW_REPAIR_RESULT_SCHEMA,
    GENERATION_AUDIT_SCHEMA,
    GLOBAL_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA,
    GLOBAL_FINAL_REVIEW_DIFFERENCE_CATEGORIES,
    GLOBAL_FINAL_REVIEW_INPUT_SCHEMA,
    GROUNDING_SCHEMA,
    MERGED_GENERATION_SCHEMA,
    MODEL_GENERATION_CASE_SCHEMA,
    MODEL_GROUNDED_TEXT_SCHEMA,
    MODEL_GROUNDING_SCHEMA,
    MODEL_INLINE_CASE_SCHEMA,
    MODEL_REPAIR_CASE_PATCH_SCHEMA,
    MODEL_REPAIR_CASE_SCHEMA,
    MODEL_REPAIR_PATCH_SCHEMA,
    MODEL_STEP_FACT_BINDINGS_SCHEMA,
    OPTIONAL_FACT_ID_LIST_SCHEMA,
    ORDERED_MARKER_SCHEMA,
    PLANNER_AGENT_OUTPUT_SCHEMA,
    PLANNER_AGENT_SUBMISSION_SCHEMA,
    PLANNER_OUTPUT_SCHEMA,
    PLANNER_TEST_DESIGN_SCHEMA,
    PLANNER_TEST_POINT_SCHEMA,
    PLANNING_EVIDENCE_CATALOG_SCHEMA,
    PLANNING_EVIDENCE_ITEM_SCHEMA,
    PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA,
    PLANNING_ROUTE_REPAIR_OUTPUT_SCHEMA,
    PLANNING_SCOPE_ROUTE_BATCH_SIZE,
    PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS,
    PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA,
    PLANNING_SCOPE_ROUTING_BATCH_OUTPUT_SCHEMA,
    PLANNING_SCOPE_ROUTING_OUTPUT_SCHEMA,
    PLAN_SCHEMA,
    REPAIR_AUTHORITATIVE_FACT_SCHEMA,
    REPAIR_SOURCE_ANCHOR_SCHEMA,
    REVIEW_FACT_SCHEMA,
    RISK_DETAIL_SCHEMA,
    RISK_OR_TEXTS_SCHEMA,
    SCENARIO_DESIGN_GUIDANCE_SCHEMA,
    SOURCE_ANCHOR_SCHEMA,
    SOURCE_SEMANTICS_AGENT_ANCHOR_SCHEMA,
    SOURCE_SEMANTICS_AGENT_FACT_SCHEMA,
    SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
    SOURCE_SEMANTICS_DOCUMENT_PAGE_SCHEMA,
    SOURCE_SEMANTICS_INPUT_SCHEMA,
    SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA,
    SOURCE_SEMANTICS_OUTPUT_SCHEMA,
    SOURCE_SPAN_SCHEMA,
    SYNTHESIS_APPROVAL_OUTPUT_SCHEMA,
    TEST_DESIGN_CATALOG_ITEM_SCHEMA,
    TEST_DESIGN_TECHNIQUES,
    TEXT_OR_TEXTS_SCHEMA,
)
from .test_generation_workflow import (
    CHAIN_CONTEXT_SCHEMA,
    GENERATION_BATCH_ITEM_SCHEMA,
    VALIDATION_OUTPUT_SCHEMA,
    _map_record_schema,
)

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
            "test_input 固定为仅包含 text 和 fact_ids 的对象，text 应列出执行该用例所需的角色、业务参数、"
            "配置值或边界值，不得把操作步骤或预期结果复制为测试输入；fact_ids 必须直接支持这些输入。"
            "用例无需额外输入时，test_input 必须为 {\"text\":\"\",\"fact_ids\":[]}，不要为填列重复前置条件。"
            "steps 每项固定为 action、expected、fact_bindings 三个字段，其中 action 和 expected 是非空字符串，"
            "fact_bindings 固定为仅包含 action、expected 两个数组的对象。"
            "示例：{\"action\":\"点击提交\",\"expected\":\"显示提交成功\","
            "\"fact_bindings\":{\"action\":[\"FACT-001\"],\"expected\":[\"FACT-002\"]}}。"
            "不要输出 case_fact_bindings、precondition_index 或 step_index；平台会根据数组位置确定性拆分绑定。"
            "前置条件和 expected 的 fact_ids 均禁止空数组；非空 test_input 必须绑定至少一个支持其输入的事实；"
            "若找不到至少一个直接支持业务内容的输入 fact_id，"
            "必须删除或改写该字段，禁止保留无事实绑定的预期，也禁止为绑定而补造事实。"
            "action 仅在需求明确声明该操作时绑定对应事实；查看、观察、读取等为执行测试而引入的中性操作"
            "可以使用空数组，禁止把只支持预期结果的事实机械挂到 action。"
            "绑定不得缺字段、跨用例、跨模块、引用非 effective 事实或输入外 fact_id。"
            "同一条用例可覆盖多个相关事实，同一事实也可支持多条不同用例；不得遗漏 required_fact_ids。"
            "必须精确生成 case_budget 条用例；事实不足时直接失败，不得用低价值变体凑数。"
            "完成分析后必须且只能调用一次 submit_generation_batch 工具提交结果，不得用正文返回 JSON。"
            "工具参数顶层只能包含 test_cases，不得输出说明、统计或运行元数据。"
            "test_cases 的值必须直接是 JSON 数组，禁止使用 json.dumps 或其他方式将数组二次序列化为字符串。"
            "test_cases 每项必须且只能包含 title、priority、preconditions、test_input、steps、tags；"
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
            "input_projection_version": "generation-model-v6-test-input",
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
            "每个前置条件使用 text、fact_ids 内联表达；test_input 使用 text、fact_ids 内联表达，"
            "用于记录执行用例所需的角色、业务参数、配置值或边界值；无需额外输入时 text 为空字符串且 fact_ids 为空数组；"
            "每一步固定包含 action、expected、fact_bindings，"
            "action 和 expected 是非空字符串，fact_bindings 仅包含 action、expected 两个事实 ID 数组，"
            "禁止在 fact_bindings 中输出 action_fact_ids 或 expected_fact_ids；"
            "所有事实引用都必须来自当前 authoritative_facts 中的生效事实。前置条件、非空 test_input 和 expected 必须绑定事实；"
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
            "每个补丁只能包含 case_id 以及确实需要替换的 title、priority、preconditions、test_input、steps、tags、"
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


