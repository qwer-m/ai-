"""测试生成内置工具、智能体和工作流规格。"""

from typing import Any

from .test_generation_escalation import (
    GENERATION_SCOPE_FEEDBACK_SCHEMA,
    GENERATION_SCOPE_FEEDBACK_OUTPUT_SCHEMA,
)

from .test_generation_schemas import (
    AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA,
    BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA,
    BUSINESS_PLAN_DRAFT_SCHEMA,
    EVIDENCE_OUTPUT_SCHEMA,
    EVIDENCE_SOURCE_SCHEMA,
    EXECUTION_CHAIN_SELECTION_SCHEMA,
    EXECUTION_PLAN_SCHEMA,
    FINAL_REVIEW_OUTPUT_SCHEMA,
    GENERATION_AUDIT_SCHEMA,
    GLOBAL_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA,
    GROUNDING_SCHEMA,
    MERGED_GENERATION_SCHEMA,
    MODEL_GROUNDING_SCHEMA,
    MODEL_REPAIR_PATCH_SCHEMA,
    PLANNER_AGENT_SUBMISSION_SCHEMA,
    PLANNING_EVIDENCE_CATALOG_SCHEMA,
    PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA,
    PLANNING_SCOPE_ROUTING_AGENT_OUTPUT_SCHEMA,
    PLAN_SCHEMA,
    SCENARIO_DESIGN_GUIDANCE_SCHEMA,
    SOURCE_SEMANTICS_AGENT_OUTPUT_SCHEMA,
    SOURCE_SEMANTICS_INPUT_SCHEMA,
    SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA,
    SOURCE_SEMANTICS_OUTPUT_SCHEMA,
    SYNTHESIS_APPROVAL_OUTPUT_SCHEMA,
    CHAIN_CONTEXT_SCHEMA,
    VALIDATION_OUTPUT_SCHEMA,
    map_record_schema,
)

# 文本与视觉提取提交相同的完整事实契约，共用输出容量，避免密集页面被旧视觉上限截断。
_SOURCE_SEMANTICS_MAX_OUTPUT_TOKENS = 15000

_FACT_SOURCE_CONTEXT_GUIDANCE = (
    "事实中的 assertion 是模型提取的候选摘要，可能漏条件或误读，不能预设它已经正确；"
    "source_context 若存在，是平台从同一原表保留的单元格、本列表头及同行标识原文，"
    "按 row_index、column_index 核对表头、行标签与单元格的完整限定。"
    "摘要遗漏的适用前提不能被视为没有前提；来源存在未决冲突时不能自行选定一种确定规则。"
    "上下文用于核实当前 fact_id，不是额外事实 ID 或新增覆盖义务，不能把同一行其他列的行为混入当前意图。"
)

_DOCUMENT_METADATA_GUIDANCE = (
    "先区分被测产品的业务要求与文档自身的编写、修订、评审和确认信息。"
    "文档的版本、作者、审批状态、确认勾选和修订标记是来源元信息，应保留为 reference_only，"
    "可用于理解来源效力，但不能升级为产品必须提供的界面、操作、状态或覆盖义务。"
    "只有原文明确要求被测产品实现相应功能时，才能将其作为业务事实；不得从文档标记反推产品功能。"
)


_DOCUMENT_SOURCE_ANCHOR_GUIDANCE = (
    "document source_anchor 只能输出 document_id、事实所在 page_number、一个真实 block_id 和 table_cell；"
    "禁止输出 quote、block_id 数组、source_span 或 source_segments。"
    "table_contexts 中的真实单元格按 table_id、row_index、column_index 定位。"
    "所选文本 block_id 命中表格时，table_cell 必须逐字引用承载本条规则的那一个真实单元格的三个坐标；"
    "该单元格的 segments 必须包含所报 block_id，且该块属于当前 source_scopes 的提取范围。"
    "同一 block_id 可能横跨多列，不能只凭 block_id 选择第一列；应选择实际承载规则正文的单元格，行标签由平台保留为上下文。"
    "segments.source_span 是当前 page_text 中可直接引用的片段范围；governed_value_spans 只能取所选单元格内具体示例值的子区间，不能引用整行规则。"
    "不能仅引用整行，也不能选相邻业务列代替当前规则；非表格文本和图片的 table_cell 必须为 null。"
    "平台按原页恢复单元格的各段文字和坐标，并关联本列表头、同行标识；不得用模型摘要改写原文。"
)


BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "read_planning_evidence", "name": "读取本次规划的原始证据",
        "description": "按真实 evidence_id 分页读取本次来源原文；evidence_id=null 查看目录。读取不会新增事实。",
        "handler_key": "testing.generation.read_planning_evidence",
        "input_schema": {
            "type": "object", "properties": {
                "evidence_id": {"type": ["string", "null"], "minLength": 1},
                "offset": {"type": "integer", "minimum": 0},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 4000},
            },
            "required": ["evidence_id", "offset", "max_chars"], "additionalProperties": False,
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "reinspect_planning_source", "name": "重新核对选定来源",
        "description": "由原文本或视觉专家重新分析本次已绑定的来源项；完整验证后替换该项事实并使过期计划失效。",
        "handler_key": "testing.generation.reinspect_planning_source",
        "input_schema": {
            "type": "object", "properties": {
                "source_kind": {"type": "string", "enum": ["text", "vision"]},
                "item_index": {"type": "integer", "minimum": 0},
                "question": {"type": "string", "minLength": 1},
            },
            "required": ["source_kind", "item_index", "question"], "additionalProperties": False,
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "revise_planning_draft", "name": "修正选定规划草案",
        "description": "依据当前真实事实修正目标、覆盖意图和风险；跨阶段问题须引用当前 problem_ids，初始规划可传空数组。",
        "handler_key": "testing.generation.revise_planning_draft",
        "input_schema": {
            "type": "object", "properties": {
                "item_index": {"type": "integer", "minimum": 0},
                "question": {"type": "string", "minLength": 1},
                "problem_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
            },
            "required": ["item_index", "question", "problem_ids"], "additionalProperties": False,
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "rebuild_business_plan", "name": "生成或修订业务规划",
        "description": "依据当前事实和草案初次建立规划，或结合指定问题修订已有规划；初始阶段 problem_ids 可为空，旧结果按完整输入失效。",
        "handler_key": "testing.generation.rebuild_business_plan",
        "input_schema": {
            "type": "object", "properties": {
                "problem_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
            },
            "required": ["problem_ids"], "additionalProperties": False,
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "route_planning_scopes", "name": "为当前规划分配事实依据",
        "description": "让路由专家处理当前缺失或已失效的输入，完整校验后返回未承接的覆盖意图。",
        "handler_key": "testing.generation.route_planning_scopes",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "review_planning_route_gaps", "name": "核查规划意图的完整支持",
        "description": "独立核查当前待审或未决意图；design_selections 为空时由平台选取当前待办，非空只能选当前未决目标。权威缺口及已失效事实由平台自动传递。相同证据已复核的意图须修订草案或来源，不能再次抽取裁决。",
        "handler_key": "testing.generation.review_planning_route_gaps",
        "input_schema": {"type": "object", "properties": {
            "design_selections": {"type": "array", "items": {"type": "object", "properties": {
                "module_index": {"type": "integer", "minimum": 0},
                "test_design_item_indexes": {"type": "array", "minItems": 1, "uniqueItems": True,
                    "items": {"type": "integer", "minimum": 0}},
            }, "required": ["module_index", "test_design_item_indexes"], "additionalProperties": False}},
            "question": {"type": "string"},
            "additional_scope_ids": {"type": "array", "uniqueItems": True,
                "items": {"type": "string", "minLength": 1}},
            "problem_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
        }, "required": ["design_selections", "question", "additional_scope_ids", "problem_ids"], "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "read_review_evidence", "name": "按需读取终审事实证据",
        "description": "仅在需要核对来源时，分页读取当前运行已绑定的事实或目录；fact_id=null 读取目录。读取不改变用例或审查结论。",
        "handler_key": "testing.generation.read_review_evidence",
        "input_schema": {
            "type": "object", "properties": {
                "fact_id": {"type": ["string", "null"], "minLength": 1},
                "offset": {"type": "integer", "minimum": 0},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 4000},
            },
            "required": ["fact_id", "offset", "max_chars"], "additionalProperties": False,
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "repair_review_batch", "name": "修复当前待办批次",
        "description": "平台派发当前全部可修复批次，独立范围并发处理；返回最新状态与待复审批次。",
        "handler_key": "testing.generation.repair_review_batch",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "recheck_review_batch", "name": "复审已修改的终审批次",
        "description": "平台派发当前全部待复审批次，由独立评审并发检查修改后的用例版本。",
        "handler_key": "testing.generation.recheck_review_batch",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "review_all_cases", "name": "全局审查当前用例",
        "description": "在 review.can_review_all_cases=true 时独立检查当前用例索引，返回差异供协调者判断修复范围或上游调整。",
        "handler_key": "testing.generation.review_all_cases",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "accept_generation_plan", "name": "接受当前有效规划",
        "description": "完整验收当前规划与事实路由，准备权威判断；接受规划不表示生成目标已经完成。",
        "handler_key": "testing.generation.accept_generation_plan",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "reconcile_generation_authority", "name": "协调待处理模块的权威事实",
        "description": "平台从当前 authority.pending_indexes 获取全部独立待处理项，按配置并发调用专家，保留已有有效结果，无需手填索引。",
        "handler_key": "testing.generation.reconcile_generation_authority",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "generate_cases", "name": "生成待处理范围的测试用例",
        "description": "平台从当前状态获取全部待生成批次并按配置并发，已验收批次不重跑。guidance 仅定位实际问题，不能改变来源事实；无需手填索引。局部额度失败保留有效结果并返回问题。",
        "handler_key": "testing.generation.generate_cases",
        "input_schema": {
            "type": "object", "properties": {
                "guidance": {"type": "string"},
            },
            "required": [], "additionalProperties": False,
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "review_generated_cases", "name": "独立审查待处理用例范围",
        "description": "平台从当前状态获取全部待初审输入并按配置并发，审查差异可交回上游规划；无需手填索引。",
        "handler_key": "testing.generation.review_generated_cases",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "revise_generation_layout", "name": "调整事实分组与用例分配",
        "description": "依据当前 problem_ids 修改指定模块的完整分组；模块事实必须完整且仅分配一次，总用例预算不变。",
        "handler_key": "testing.generation.revise_generation_layout",
        "input_schema": {
            "type": "object", "properties": {
                "problem_ids": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                "changes": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object", "properties": {
                            "module_index": {"type": "integer", "minimum": 0},
                            "groups": {
                                "type": "array", "minItems": 1,
                                "items": {
                                    "type": "object", "properties": {
                                        "fact_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                                        "test_design_item_ids": {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}},
                                        "case_budget": {"type": "integer", "minimum": 1, "maximum": 20},
                                    },
                                    "required": ["fact_ids", "test_design_item_ids", "case_budget"], "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["module_index", "groups"], "additionalProperties": False,
                    },
                },
            },
            "required": ["problem_ids", "changes"], "additionalProperties": False,
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "inspect_generation_scope", "name": "读取当前生成范围或问题",
        "description": "生成阶段按 item_index 读取生成输入和候选；终审阶段按 review_batch_id 读取唯一的当前审查范围；或按 problem_id 读取完整问题。三个参数只选择一个，其余为 null。",
        "handler_key": "testing.generation.inspect_generation_scope",
        "input_schema": {
            "type": "object", "properties": {
                "item_index": {"type": ["integer", "null"], "minimum": 0},
                "problem_id": {"type": ["string", "null"], "minLength": 1},
                "review_batch_id": {"type": ["string", "null"], "minLength": 1},
            },
            "required": ["item_index", "problem_id", "review_batch_id"], "additionalProperties": False,
            "anyOf": [
                {"properties": {"item_index": {"type": "integer", "minimum": 0}, "problem_id": {"type": "null"}, "review_batch_id": {"type": "null"}}},
                {"properties": {"item_index": {"type": "null"}, "problem_id": {"type": "string", "minLength": 1}, "review_batch_id": {"type": "null"}}},
                {"properties": {"item_index": {"type": "null"}, "problem_id": {"type": "null"}, "review_batch_id": {"type": "string", "minLength": 1}}},
            ],
        },
        "output_schema": {"type": "object"}, "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "finish_generation", "name": "提交当前完整生成目标",
        "description": "仅在当前规划、事实、生成范围、独立批次审查和全局审查均验收通过时提交交付结果。",
        "handler_key": "testing.generation.finish_generation",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {"completed": {"type": "boolean"}}, "required": ["completed"], "additionalProperties": False},
        "risk_level": "low", "requires_approval": False,
    },
    {
        "tool_key": "report_generation_scope_problem", "name": "报告生成范围问题",
        "description": "引用当前真实事实、设计项和原文条件，报告无法直接修正的范围矛盾；该报告不是用例、已批准结论或额外预算。",
        "handler_key": "testing.report_generation_scope_problem",
        "input_schema": GENERATION_SCOPE_FEEDBACK_SCHEMA,
        "output_schema": GENERATION_SCOPE_FEEDBACK_OUTPUT_SCHEMA,
        "risk_level": "low", "requires_approval": False,
    },
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
                    "items": map_record_schema(SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA),
                },
                "vision_inputs": {"type": "array", "items": SOURCE_SEMANTICS_INPUT_SCHEMA},
                "vision_records": {
                    "type": "array",
                    "items": map_record_schema(SOURCE_SEMANTICS_NORMALIZED_OUTPUT_SCHEMA),
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
        "tool_key": "approve_synthesized_test_cases",
        "name": "确认当前已验收用例",
        "description": "只允许当前完整版本通过确定性审计和独立审查后进入交付校验。",
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
        "agent_key": "test_generation_coordinator",
        "name": "测试生成自主协调智能体",
        "description": "围绕同一交付目标，自主选择取证、规划、生成、范围调整和独立复审，保持来源、预算与版本一致。",
        "instructions": (
            "你负责依据本次真实来源交付可执行、可断言的完整测试用例。"
            "工具返回的是当前状态，按具体问题自主选择有价值的行动；接受规划或生成某批次不等于整个目标完成。"
            "available_actions 列出当前合法行动及其真实目标，仅从该目录选择工具和索引；它不规定唯一下一步。"
            "recent_action_errors 保留失败工具、原参数及已保存子调用数；网络失败不是业务内容错误，"
            "不要仅因恢复会话而重建已有规划。结合当前待处理范围继续必要行动，完整输入相同时成功子调用会复验复用。"
            "state_matches_failure=false 表示领域状态已变化，旧错误不能直接当作当前范围的问题。"
            "每次行动后按新目录判断剩余范围，已完成索引不可重复生成，但仍可读取并主动修订规划或布局。"
            "规划行动的 changed=false 表示内容没有变化，不应当作修复进展，也不应向无关草案重复传播同一问题。"
            "goal 固定来源版本、用户总用例预算和完整验收条件；不能自报新事实、修改来源身份或增加总预算。"
            "planning 包含 source_items、planning_batches、route_gaps 和 unrouted_modules；"
            "authority、generation、initial_review_batches、review 分别说明当前仍需处理的真实范围。"
            "initial_review_batches 通过 generation_batch_ids 和 case_ids 对应生成范围，初审 item_index 与生成 item_index 各自独立。"
            "problems 记录生成或审查发现的问题及其规划批次来源；问题是待判断主张，不是事实或自动批准的重规划命令。"
            "authority_support_gap 表示已完成的权威判断使部分设计支持失效；design_support_gaps 给出准确的规划模块与意图索引，"
            "应结合 authority_facts 和 authority_decisions 判断需要复核支持还是修订草案，不能从 TD 编号猜索引。"
            "针对这些缺口调用支持复核时，平台自动传入当前权威问题与失效事实，不能重新作为支持依据。"
            "需要核对原文时用 read_planning_evidence，scope_ids 就是 evidence_id，未知时可用 null 读取目录。"
            "原文与当前提取事实不一致时，可选对应原来源项调用 reinspect_planning_source 完整重审。"
            "有效变更会按来源版本使旧规划、生成和审查产物失效；必须重新验收，不能叠加新旧事实。"
            "来源重审保留已发生的请求历史及预算消耗，changed=false 不会清理下游或重置额度。"
            "草案推导了来源外行为时用 revise_planning_draft，必须同步修正目标、覆盖意图和相关风险。"
            "模块划分重复或覆盖分组不合理时可用 rebuild_business_plan；事实路由缺失时可用 route_planning_scopes，"
            "每个意图均需用 review_planning_route_gaps 独立核查完整支持集合，初审 design_selections 传空数组。"
            "复核范围以 available_actions 中的 unresolved_design_selections 为准；"
            "design_selections 为空时平台处理当前全部未决目标，也可选择其中部分并提供具体 question。"
            "已通过且没有新缺口的意图不重复复核；相同证据已定向复核的未决项应修订错误草案或补充真实来源。"
            "requires_additional_evidence 中的目标必须同时选择新的真实 additional_scope_ids，不能只改变问题措辞重复裁决。"
            "支持版本变化只使下游产物失效，其他意图已有支持裁决仍由当前状态保留。"
            "unresolved_design_selections 精确列出未决目标；support_gaps 给出原意图片段、缺口类型和真实证据。"
            "只存在支持缺口时，重分组不能改动意图，rebuild_blocker 会说明为什么该动作无效；"
            "可自主取证、复核来源、修订真正有误的草案，或定向核实原审查，不能通过改模块名重新抽取支持结论。"
            "定向复核保留 previous_decisions 供追溯，专家根据当前问题和来源独立核对；question 应描述待核实事项，不复制旧裁决。"
            "若候选事实范围不足，可从真实证据目录选择 additional_scope_ids 补充，"
            "初审或无需补充时传空数组；不得把审查者的判断当作新事实。"
            "复核标签与理由矛盾时，应核实该结论；只有草案实际引入了错误意图，才需要修订草案。"
            "supported 或 unsupported 都要结合支持事实判断，"
            "不能把结果状态倒推成来源未提及的操作，也不能删除必需覆盖组来规避验收。"
            "初始规划的 problem_ids 传空数组；由生成或审查反馈触发的草案修订和重规划必须引用对应当前 problem_ids。"
            "planning.plan_available 表示业务规划是否已建立，rebuild_business_plan 同样用于初次建立规划；"
            "planning.can_finish=true 且 plan_accepted=false 时可用 accept_generation_plan 验收并取得权威协调输入；"
            "reconcile_generation_authority、generate_cases、review_generated_cases 自动处理各自全部独立待处理项，"
            "平台从当前状态获取索引并按配置限流并发，已验收项不重跑；不要传 item_indexes 或逐批做协调决策。"
            "生成或审查发现范围问题后，可用 inspect_generation_scope 读取完整问题和对应原始输入；"
            "specialist_failure 表示专业会话局部请求额度耗尽，查看其中的真实工具反馈及咨询建议后决定如何继续；"
            "这不证明来源或规划有误，也不表示整个请求的所有批次均已生成，按当前 pending_indexes 判断剩余范围。"
            "同一完整生成请求不能原样重复；需要继续时应提供针对失败原因的新 guidance 或修订实际范围，不能重置共享额度。"
            "终审按 review_batch_id 读取唯一 review_scope，不再按生成索引重复读取同一终审批次。"
            "review_scope 提供修复后的当前用例，awaiting_recheck 表示新候选尚未复审，旧结论不能归到新候选上。"
            "若现有事实和数量内可以明确建立执行前置，向 generate_cases 提供具体 guidance 后继续原范围；"
            "若事实分组或数量分配不合理，可用 revise_generation_layout 调整；若规划目标或覆盖归属错误，应修订草案或规划。"
            "不要因一次超量候选就增加用例预算或认定无法完成；不得把互斥前置或不连续生命周期硬塞进同一用例。"
            "generation.layout 提供当前完整事实分组及数量；revise_generation_layout 的 changes 必须给出每个被修改模块的完整 groups，"
            "每个 group 指定真实 fact_ids、test_design_item_ids 和 case_budget；模块内事实与意图各恰好分配一次，总预算保持不变。"
            "fact_ids 表示本组独占的事实覆盖义务；平台另外按意图补齐完整支持上下文，共享事实不重复分配覆盖义务。"
            "需要跨模块重新分配数量时一并提交相关模块，不能删除事实、突破批次容量或用相同输入重复抽取。"
            "平台只复用完整输入一致的已验收结果；任何相关上游变化都会使对应下游产物和批准失效。"
            "review 提供 repairable_batches、recheck_batch_ids 和独立审查结论；repairable 表示存在未解决差异，"
            "不保证当前范围内能够修好，应结合 repair_scopes、review_summary、当前候选和事实条件判断承接方式。"
            "候选自身有局部错误可选 repair_review_batch，修改后必须用 recheck_review_batch 独立复审；"
            "两种行动均无需批次参数，平台从当前待办派发所有独立范围，不要逐批重复调用。"
            "局部差异不会自动证明规划有误；发现来源元信息被当成产品功能时，应先核对来源效力，不能反复重建规划来满足虚构功能。"
            "修复专家会自动收到该 batch_id 的完整当前候选、事实和差异；能从 review_summary 明确问题时可直接修复，"
            "不需要先遍历所有候选。只有需判断具体范围或质疑审查时才读取对应 review_batch_id。"
            "review_all_cases 检查当前完整版本，发现上游范围问题时同样可以回到规划或分组。"
            "需要核对审查中的具体事实才使用 read_review_evidence，不能自行改写评审结论或声明差异已解决。"
            "最外层 can_finish=true 后平台直接调用 finish_generation 并重新验收当前完整版本，无需再次决策。"
            "父子调用共用请求和时间预算；避免无新问题的重复取证、重规划、生成和复审，不能重置额度。"
            "所有说明使用中文，通过工具完成任务，不能用自由正文宣告成功。"
        ),
        "model": "",
        "output_schema": {"type": "object", "properties": {"completed": {"type": "boolean"}}, "required": ["completed"], "additionalProperties": False},
        "runtime_config": {
            # 整个目标的父子调用共享上限，规划和审查的阶段完成不会重置预算。
            "model_route": "main", "transient_fallback_model_route": "turbo",
            "transient_fallback_after_failures": 2,
            "max_turns": 160, "request_timeout_seconds": 3600,
            "managed_agent_max_concurrency": 6,
            "max_retries": 0, "max_output_tokens": 4000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "disable_server_output_schema": True, "validate_before_submit": True,
            "force_terminal_tool_choice": False,
            "tool_keys": [
                "read_planning_evidence", "reinspect_planning_source", "revise_planning_draft",
                "rebuild_business_plan", "route_planning_scopes", "review_planning_route_gaps",
                "accept_generation_plan", "reconcile_generation_authority", "generate_cases",
                "review_generated_cases", "revise_generation_layout", "inspect_generation_scope",
                "read_review_evidence", "repair_review_batch", "recheck_review_batch",
                "review_all_cases", "finish_generation",
            ],
            "stop_at_tool_keys": ["finish_generation"],
            "automatic_terminal_tool": "finish_generation",
            "managed_agent_keys": [
                "test_source_text_semantics_analyst", "test_source_semantics_analyst",
                "test_business_plan_batcher", "test_business_planner",
                "test_planning_scope_router", "test_planning_route_gap_reviewer",
                "test_authority_reconciliation_reviewer", "test_case_generator",
                "test_generation_batch_repairer", "test_generation_final_reviewer", "test_generation_global_reviewer",
            ],
            "state_initializer": "testing.initialize_generation_state",
            "state_observer": "testing.observe_generation_state",
            # 原始证据来自固定输入，阅读记录变化不能使原文过期；候选详情跟随领域版本。
            "snapshot_tools": {"inspect_generation_scope": "state", "read_planning_evidence": "input",
                               "read_review_evidence": "state.review.input"},
            "state_action_catalog": "available_actions",
            "output_postprocessor": "testing.validate_generation_resolution",
        },
    },
    {
        "agent_key": "test_business_plan_batcher",
        "version": 1,
        "name": "测试业务规划分批智能体",
        "description": "在有界事实批次内提炼业务模块候选，并逐条承接真实事实。",
        "instructions": (
            _FACT_SOURCE_CONTEXT_GUIDANCE +
            "你是测试业务规划分批智能体。planning_scopes 是平台从完整有效事实目录切出的当前批次，"
            "planning_batch 只说明批次位置，case_budget 是全局目标用例数。"
            "若提供 _planning_feedback，其中的问题、旧草案及路由差异仅用于定位修正，不能作为新的事实依据；"
            "应依据 planning_scopes 同时修正目标、覆盖主题与相关风险，不能保留旧草案中缺少依据的推断。"
            "只能根据当前 planning_scopes 提炼粗粒度业务模块候选，不得补造来源外系统、角色、状态或规则。"
            "覆盖主题中的动作、对象、条件和预期必须能由事实直接推出；描述可见状态的事实不证明未提及的上游操作或实现过程。"
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
            "来源中的问题、建议和答复要分开理解：覆盖主题只断言原文已经明确回答的业务行为；"
            "不能把未答复的问题改写成肯定规则，也不能把答复扩展到同一句中未被回答的其他行为。"
        ),
        "model": "",
        "output_schema": BUSINESS_PLAN_DRAFT_SCHEMA,
        "runtime_config": {
            "model_route": "turbo",
            "transient_fallback_model_route": "main",
            "transient_fallback_after_failures": 2,
            "result_cache": {
                "enabled": True,
            },
            "disable_server_output_schema": True,
            "max_turns": 6,
            "validate_before_submit": True,
            "stop_at_tool_keys": ["submit_result"],
            "force_terminal_tool_choice": False,
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
        "version": 4,
        "name": "测试业务规划智能体",
        "description": "汇总已校验的分批规划草案，形成全局业务模块和测试设计目录。",
        "instructions": (
            "你是测试业务规划智能体。partial_plans 是平台分批读取全部有效事实、逐批校验 fact_id 无遗漏后形成的精简草案，"
            "若提供 _planning_feedback，旧计划与路由差异仅用于定位问题；重新规划必须使用当前草案及当前覆盖组目录，"
            "不得延续旧目标中已经失去依据的业务行为，也不能沿用过期编号。"
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
            "唯一性覆盖整个 business_modules 的所有测试点和测试方法；同一模块内的不同测试点也不能重复引用同一个组。"
            "组是不可拆的最小语义单元，不得挑选或重排组内原子项。平台会在严格校验后展开组内全部原子覆盖意图；"
            "你只负责把语义组编排到最合适的测试方法下。"
            "输出保持精简：requirement_summary 不超过 120 个汉字，模块和测试点 objective 均不超过 160 个汉字。"
            "一条测试用例可以承载多个语义相关的覆盖项，因此禁止按 case_budget 强行拼接或删除原子覆盖意图。"
            "business_modules、全局 test_points 和全局 test_designs 的数量不得超过 planning_limits 对应上限；"
            "每个测试点只保留最适用的一种测试方法。可以合并语义重复的层级结构，不能合并、拆分或删除覆盖语义组 ID。"
            "完成规划后调用 submit_business_plan 工具提交，不得用正文返回 JSON。"
            "提交会执行完整校验；收到 rejected 时，依据原始语义组目录和反馈的冲突位置修正规划，再次提交。"
            "同一语义组在多个测试点出现时，应依据业务含义选择唯一归属，并复核其他测试点是否仍有独立覆盖内容；"
            "不得随意删除覆盖语义组、改动目录或忽略未列出的校验要求。只有完整验收通过才完成本次规划。"
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
                "enabled": True,
            },
            "disable_server_output_schema": True,
            # 规划候选在同一会话内反馈校验差异，不再靠外层固定重跑修正。
            "max_turns": 6,
            "validate_before_submit": True,
            "request_timeout_seconds": 360,
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
            _FACT_SOURCE_CONTEXT_GUIDANCE +
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
            "transient_fallback_model_route": "main",
            "transient_fallback_after_failures": 2,
            "result_cache": {
                "enabled": True,
            },
            "disable_server_output_schema": True,
            "max_turns": 6,
            "validate_before_submit": True,
            "stop_at_tool_keys": ["submit_result"],
            "force_terminal_tool_choice": False,
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
        "description": "逐项核查测试设计意图的完整事实支持，允许对已路由意图纠正支持关系。",
        "instructions": (
            _FACT_SOURCE_CONTEXT_GUIDANCE +
            "你负责业务规划意图的独立支持审查。输入包含一个业务模块、本次待审查的测试设计项，"
            "以及该模块已关联来源范围中的提取候选。候选摘要和待审意图可能来自同一错误提取，彼此匹配不能证明正确。"
            "每条 candidate_fact 的 source_context 紧邻其摘要保留原表上下文；"
            "source_context 专门承载表格上下文，非表格事实没有该字段是正常情况；"
            "不得仅凭字段缺失判为 missing_support，必须指出覆盖意图与已提供事实之间具体缺失或冲突的业务要求。"
            "须按该事实的原文表头、行标识和对应单元格独立核对适用条件。"
            "必须逐条审查 missing_test_design_items，不能遗漏、增加或改写 test_design_item_index。"
            "在 fact_ids 中引用确实支持覆盖意图的真实事实，support_gaps 列出尚未获支持的实质要求；"
            "不得输出 disposition，平台依据支持事实和缺口派生结论。不能只凭关键词相似、同页或同模块强行绑定。"
            "coverage_intent 是测试覆盖目标，不是完整用例；概括某项规则或引用某项条件，不要求在意图中重复其所有前置条件、步骤和结果。"
            "先从事实及其 source_context 确定规则的适用对象、前提、时限、量词和结果，再判断意图是否要求验证该规则，不能只匹配摘要。"
            "若意图明确断言某个条件足以触发结果，却漏掉来源必要的前提、时限或范围，便扩大了适用范围，属于 missing_support；"
            "仅概括验证某规则、引用已有条件名称或没有重复写出结果去向，不等于扩大适用范围。"
            "coverage_fragment 必须指向意图实际表达但未获支持的要求，不能以意图未逐字抄写来源、未展开已引用条件为由制造缺口。"
            "引用事实自身的 assertion 漏掉 source_context 中的限制时，也不能沿用这个遗漏批准意图。"
            "实体或字段相同不代表业务动作相同；结果或展示状态不能反向证明未提及的上游操作、实现路径或时机。"
            "业务概念的概括名称不要求与界面文案字面相同；仅当意图明确要求校验具体文案时，才检查该字面值是否有来源。"
            "验证提示正确展示不等于断言固定文案；来源只支持展示行为或配置值时，应按该语义核对，"
            "不能要求意图提供固定字面值，也不能把示例文案当成唯一预期。"
            "runtime_configured 表示具体值按已声明的配置或运行态验证，不能仅因缺少固定值拒绝；"
            "但它不能消除适用对象、生效时机或业务行为的真实冲突。"
            "多个事实可以共同支持复合意图，但不能添加把它们串起来的假设；任一实质要求缺少依据，都必须逐项记录 support_gaps。"
            "完全支持时 fact_ids 是最小完整支持集合，support_gaps 为空；存在缺口时仍保留实际支持其他部分的 fact_ids。"
            "每个 gap 的 coverage_fragment 必须逐字取自本项 coverage_intent，reason 说明该片段缺少的依据；"
            "kind=missing_support 表示缺少支持，evidence_fact_ids 可引用用于核对的实际候选事实；"
            "kind=source_conflict 必须在 evidence_fact_ids 中引用承载冲突双方的真实事实，并说明不相容的前提或结果；"
            "同一事实的 source_context 已保留冲突双方时可以引用该事实，不能因它们共用 fact_id 就忽略冲突。"
            "不同指标、展示口径或动作不能仅因名称相近就视为同一规则冲突；当前路由仅是待核实候选。"
            "_planning_feedback 若存在，表示真实下游问题和本次核实问题；它不是正确答案，必须重新核对完整候选事实。"
            "来源间存在相互冲突的行为且没有明确优先级时，不能自行指定文字、图片、表头或单元格谁优先，必须列出 source_conflict 缺口。"
            "支持必须不依赖未声明的例外或优先级解释；需要补入这种假设才能成立时，应保留缺口交由协调者处理。"
            "若候选事实都不能直接支持，fact_ids 为空并列出具体缺口；fact_ids 和 support_gaps 不能同时为空。"
            "reason 必须简洁说明来源的完整条件与覆盖意图如何对应；有 source_context 时引用相关单元格及表头的实际限定，"
            "不匹配时明确指出遗漏、冲突或未获支持的动作及条件，不能用常识补齐。"
            "最终 JSON 只能包含 module_index 和 decisions；每个 decision 只能包含 "
            "test_design_item_index、fact_ids、support_gaps、reason；gap 只能包含 coverage_fragment、kind、evidence_fact_ids、reason。"
        ),
        "model": "",
        "output_schema": PLANNING_ROUTE_REPAIR_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "transient_fallback_model_route": "turbo",
            "transient_fallback_after_failures": 2,
            "result_cache": {
                "enabled": True,
            },
            "disable_server_output_schema": True,
            "max_turns": 6,
            "validate_before_submit": True,
            "stop_at_tool_keys": ["submit_result"],
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 8000,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_choice": "required",
            "input_projection_version": "planning-support-v3-inline-evidence",
            "input_projection": {
                "module_index": True,
                "module_name": True,
                "module_objective": True,
                "missing_test_design_items": True,
                "candidate_facts": [
                    "fact_id", "scope_id", "assertion", "status", "source_context",
                    "value_policy", "governed_values", "governed_by",
                ],
                "additional_scope_ids": True,
                "_planning_feedback": {"question": True, "downstream_problems": True},
            },
            "tool_keys": [],
        },
    },
    {
        "agent_key": "test_source_text_semantics_analyst",
        "version": 1,
        "name": "来源文本语义分析智能体",
        "description": "使用强文本模型按连续页面批次提取带精确来源坐标的原子事实。",
        "instructions": (
            "你是来源文本语义分析智能体。_planning_feedback 只提供需要核对的问题与旧候选，不能作为事实或证据。"
            "重新核对时仍返回当前全部来源范围的完整结果，不能只返回问题相关片段。"
            "source_kind=document_batch 时，pages 是连续真实页面批次；"
            "source_kind=inline 时，requirement 和 source_scopes 共同限定唯一事实源。"
            "必须逐个审查 source_scopes，但不得为了覆盖范围补造来源中不存在的内容。authoritative_facts 只提取直接存在、"
            "可独立验证的原子事实；fact_id 在本批次内唯一，assertion 使用中文。"
            "source_kind=document_batch 时，每条 fact 的 source_anchor 必须使用 document 结构，逐字复制对应 page_number；禁止输出 inline 锚点。"
            + _DOCUMENT_SOURCE_ANCHOR_GUIDANCE +
            "每条事实必须原子化；适用条件与对应结果共同组成一条可验证规则，不能因为跨文本块就拆掉适用前提。"
            "一条规则只选择当前提取范围内与其最直接相关的一个 block_id，不得借用仅供上下文的块作为独立事实来源。"
            "每条规则保留来源明确写出的适用前提、操作和结果；不同条件或不同结果的分支分别提交，不得省略前提扩大适用范围。"
            "平台会根据真实页面确定性补齐来源文字、绝对坐标和 scope_id；模型不得拼接或改写来源引用。"
            "table_contexts 保留真实网格首行及当前片段所在行；先结合行标签、列标题和单元格理解完整规则，同一 row_index 和 column_index 确定格位置。"
            "表头或行标签中明确适用于单元格的对象、条件、时限及量词必须保留在该格的事实中，不能只摘录格内正文。"
            "表头限定与单元格内容不相容且没有明确优先级时，保留两者的冲突内容与来源上下文并将相关事实标为 uncertain，不能自行选择其一。"
            "上下文中的 original_source_span 是原页坐标，不能替代当前 blocks 的引用坐标；仅作上下文的文字不能扩张本片事实提取范围。"
            "不要把不同行或不同列的条件拼接到同一规则。"
            "inline source_anchor 只能输出 requirement 的绝对起止坐标。source_anchor 中禁止输出 source_kind，来源类型由平台按真实输入确定。"
            "禁止输出 scope_id，平台会根据校验后的真实来源锚点派生唯一 scope_id。"
            "status 只能是 effective、superseded、non_final、reference_only 或 uncertain。"
            "只有能够直接形成用户操作、可观察结果、状态规则、权限边界或可验证配置的事实才能标为 effective；"
            "项目背景、营销目标、GMV目标、原因说明和纯叙述性上下文必须标为 reference_only。"
            + _DOCUMENT_METADATA_GUIDANCE +
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
                "enabled": True,
            },
            "input_mode": "text",
            "max_turns": 1,
            "request_timeout_seconds": 90,
            "max_retries": 0,
            "max_output_tokens": _SOURCE_SEMANTICS_MAX_OUTPUT_TOKENS,
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
            "你是来源语义分析智能体。_planning_feedback 只提供需要核对的问题与旧候选，不能作为事实或证据。"
            "重新核对时仍返回当前全部来源范围的完整结果，不能只返回问题相关片段。"
            "source_kind=document 时，输入 JSON 与同一消息中的真实页面图像共同构成事实源；"
            "document_id 和 page_number 是当前输入的不可变身份，必须从输入 JSON 原样复制到每条 source_anchor；"
            "不得根据证据目录、截图文字、图片中的分页标记或其他页面推断、替换或改写这两个字段；"
            "图像包含整页全景及标注真实 block_id 的原文档高清局部图；用全景确认上下文与布局，用局部图核对文字和控件，"
            "同一内容只提取一次，不得因收到多张图而重复生成事实，也不得把局部图当作额外页面。"
            "source_kind=inline 时，requirement 和 source_scopes 共同限定唯一事实源。每个输入只分析当前页或当前纯文本一次，不按业务模块重复解释。"
            "source_scopes 是平台根据真实证据目录确定的完整审查范围；你必须逐个审查，"
            "但只能提取页面中真实存在的事实，不得为了覆盖范围补造内容。"
            "authoritative_facts 只提取来源中直接存在、可独立验证的原子事实，不得补造页面、控件、状态、角色或规则。"
            "fact_id 只需在当前页面或本次来源内唯一；平台会依据真实来源身份合并为全局规范 fact_id，无需模型自行添加前缀。assertion 用中文陈述事实；禁止输出 scope_id，平台会根据校验后的真实来源锚点派生唯一 scope_id。"
            "source_scopes.allowed_block_ids 只是当前证据作用域允许引用的块集合，不是 source_anchor.block_id 的输出值。"
            + _DOCUMENT_SOURCE_ANCHOR_GUIDANCE +
            "每条事实必须原子化；事实涉及多个文本块或图片块时继续拆分，并选择与当前原子事实最直接相关的一个 block_id。"
            "流程图按可独立验证的条件分支拆分，每条事实明确分支前提、操作和结果；禁止把整张流程图串成一个长事实。"
            "同一图片块允许承载多条不同分支事实，各条仍引用该真实 block_id。"
            "blocks 中 type=image 的条目是真实图片块，包含 block_id 和 bbox；它没有正文和字符坐标，也允许作为事实来源。"
            "必须同时审查图文混合页和纯图片页中的可读文字、界面行为及明确业务规则；不得因为 page_text 为空而忽略图片。"
            "图片事实引用对应真实图片块的 block_id，提交 document_id、page_number、block_id 和 table_cell=null；"
            "不得把图片中的文字绑定到附近正文块，也不得虚构 OCR 正文、quote 或 source_span。"
            "禁止自行输出图片像素坐标或块内相对坐标；文本事实由平台从真实正文补齐来源文字和坐标，"
            "图片事实由平台从真实图片块补齐 image_region；两类事实均由平台派生 scope_id、asset_source_sha256 和 page_image_sha256。"
            "table_contexts 保留真实网格首行及当前片段所在行；先结合行标签、列标题和单元格理解完整规则，同一 row_index 和 column_index 确定格位置。"
            "表头或行标签中明确适用于单元格的对象、条件、时限及量词必须保留在该格的事实中，不能只摘录格内正文。"
            "表头限定与单元格内容不相容且没有明确优先级时，保留两者的冲突内容与来源上下文并将相关事实标为 uncertain，不能自行选择其一。"
            "其中 original_source_span 只表示原页上下文坐标，不能替代 blocks 的引用坐标或扩张当前事实提取范围。"
            "不要把不同行或不同列的条件拼接到同一规则。"
            "inline source_anchor 只能输出 requirement 对应的 source_offset_start 和 source_offset_end；"
            "source_anchor 中禁止输出 source_kind，来源类型由平台按真实输入确定；"
            "平台会根据真实 requirement 自动补齐 requirement_sha256 和 quote。"
            "marks 是 manifest v3 的通用来源标记：strikeout 表示命中内容已删除；高亮或批注只在原文明确表达时用于判断"
            "replaces、non_final 或 runtime_configured，不得只凭颜色、位置或批注存在本身推断业务含义。"
            "图文对同一行为存在冲突时，分别保留各自可追溯的事实和适用条件，交由后续跨页治理处理；"
            "不得因图片位置更靠后、画面更完整或某个按钮存在，就覆盖正文规则或自行宣布其已被替代。"
            "status 只能是 effective、superseded、non_final、reference_only 或 uncertain。"
            "明确生效且可作为生成依据时用 effective；已废弃、非终稿、仅参考或无法确认的内容不得标为 effective。"
            "只有能够直接形成用户操作、可观察结果、状态规则、权限边界或可验证配置的事实才能标为 effective；"
            "项目背景、营销目标、GMV目标、原因说明和纯叙述性上下文必须标为 reference_only。"
            + _DOCUMENT_METADATA_GUIDANCE +
            "来源示例中的机构名、当前页码、总页数、时间或金额等单次实例状态应与通用规则分开，实例状态标为 reference_only；"
            "不能仅因出现具体数值就推定固定要求或动态配置，必须按来源明确约束判断。"
            "图片中的规则按同样标准判断，禁止把所有截图事实统一标为 reference_only；"
            "截图中的机构名、当前页码、总页数、时间、金额等单次实例状态，应与控件能力和规则分开；"
            "控件存在、静态标签与明确行为可形成规则事实，单次实例状态只能作为 reference_only 的独立事实，不能升级为固定需求。"
            "截图展示可执行操作时，必须同时保留画面中可读的适用前提、状态、数值关系与结果，"
            "不得把某个达成条件下的按钮推广为全部状态均可操作，也不得添加图中未声明的因果关系。"
            "无法确认通用约束的样例只陈述当前截图展示的内容；不能仅为使样例可生成用例而把它改成 runtime_configured。"
            "value_policy 只能是 exact 或 runtime_configured；来源明确说值由配置或运行态决定时必须使用 runtime_configured。"
            "动态配置的识别和事实归类由你依据来源语义完成，平台不通过关键词要求确定性覆盖。"
            "governed_value_spans 只能填写当前输入 page_text 中的字符坐标；压缩页面使用局部坐标，"
            "平台会从真实原页切片并转换为绝对坐标后生成 governed_values；"
            "平台不判断动态值内容，只校验坐标并原样切片；策略声明本身不是具体示例值；"
            "只有来源明确声明值由配置、后台或运行态决定时才使用 runtime_configured；来源明确要求的固定文案、金额、"
            "次数和时长属于 exact，不能把仅作展示的样例值当作固定要求，也不能因为存在批注、待设计说明或视觉标记而改判。"
            "不得自行复制、改写或概括具体值。来源没有明确示例值时，即使 value_policy=runtime_configured 也必须输出空数组；"
            "value_policy=exact 时 governed_value_spans 必须为空。"
            "引用 type=image 块的事实无论 value_policy 为何，governed_value_spans 都必须为空数组 []，"
            "因为图片没有可切片的正文字符坐标；不得为图片示例值编造坐标。"
            "图片中明确要求固定值时使用 exact，明确由配置或运行态决定时使用 runtime_configured；"
            "是否为图片来源本身不改变 status 或 value_policy 的判断标准。"
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
                "enabled": True,
            },
            "input_mode": "document_page_optional_image",
            "max_turns": 1,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": _SOURCE_SEMANTICS_MAX_OUTPUT_TOKENS,
            "extra_body": {"thinking": {"type": "disabled"}},
            "tool_keys": ["submit_source_semantics"],
            "stop_at_tool_keys": ["submit_source_semantics"],
        },
    },
    {
        "agent_key": "test_authority_reconciliation_reviewer",
        "name": "来源权威事实协调智能体",
        "description": "在同一业务模块内核对事实及其真实上下文的冲突、实例与规则边界、修订及动态配置，只输出事实治理补丁。",
        "instructions": (
            _FACT_SOURCE_CONTEXT_GUIDANCE +
            "你是来源权威事实协调智能体。module 是唯一审查边界，authoritative_facts 已按真实来源顺序排列。"
            "module 仅给出名称及事实、证据归属范围，不包含规划目标、测试设计或支持审查结论；"
            "权威判断只依据原文事实、适用条件与明确治理关系，不能由测试意图或支持边反推原文的效力。"
            "你不得新增、删除、改写 assertion、scope_id 或 source_anchor；decisions 只返回状态、值策略或治理关系确有变化的事实补丁。"
            "每条补丁必须包含 fact_id、reason，以及 status、value_policy、governed_values、governed_by 中至少一个确有变化的字段；"
            "未变化字段不要复制，平台会从原事实继承。"
            "逐项比较同一业务行为在相同适用条件下的操作和结果是否矛盾，并识别跨页修订、明确替代、废弃、非最终说明和动态配置。"
            "冲突也可能存在于同一页或一条事实的真实上下文内；须核对其表头限定、同行标识与单元格原文，"
            "不能因为只有一个 fact_id 或一页来源就跳过。表头与正文条件互相矛盾且没有明确优先关系时，"
            "将该 effective 事实降为 uncertain，在 reason 中引用冲突的真实条件；不得自行规定正文覆盖表头或表头覆盖正文。"
            "适用条件不同的事实不能仅因结果不同就判为冲突；必须依据当前事实明确保留的条件判断，不得补造缺失前提。"
            "不得因为页序较后、文字较新或来源是正文或图片就自动覆盖另一条规则；媒体类型和排列顺序不决定权威性。"
            "规范性条件规则与截图单次实例混淆或截图事实遗漏适用前提时，应保留明确的规范规则，"
            "将不能作为通用要求的实例观察降为 reference_only，无法确认其适用性时降为 uncertain。"
            "若一个 assertion 混合了样例数量、具体记录状态与规则，且不能通过状态补丁分开，"
            "同模块已有独立明确规范规则时可将该混合事实降为 reference_only；不得为保留样例而改写 assertion。"
            "相同适用条件下的事实确实矛盾且无法确定优先权时，将当前仍为 effective 的冲突事实标为 uncertain，"
            "不得让相互矛盾的通用规则同时保持 effective，也不得伪造来源没有表达的替代或修订关系。"
            "原 status 不是 effective 的事实不得重新激活，也不得改成其他状态。原 value_policy=runtime_configured 不得降级为 exact。"
            + _DOCUMENT_METADATA_GUIDANCE +
            "若后续事实明确替代或使旧事实无效，应把旧事实标为 superseded，并在旧事实 governed_by 中引用治理事实。"
            "governed_by 每项只能包含 relation 和 fact_id；其中 fact_id 是施加治理的事实 ID，"
            "relation 描述它对当前 decision.fact_id 的作用，标准值使用 replaces、invalidates、limits 或 parameterizes。"
            "只有该 relation 在当前真实事实之间确实成立时才填写 governed_by；冲突或实例降级不自动等于 replaces，"
            "没有明确治理关系时只提交状态补丁，不得为使补丁看似完整而添加关系。"
            "若事实明确声明具体值以后台、环境或运行时配置为准，应使用 runtime_configured；"
            "只保留平台已经从真实坐标切片得到的 governed_values，来源没有示例值时允许为空。"
            "完成冲突、实例边界和明确治理关系审查后，仅对确实需要变化的 fact_id 提交补丁；"
            "整个模块均无需变化时 decisions 才输出空数组，不能仅因没有显式替代措辞而跳过冲突治理。"
            "governed_by 只能引用当前 authoritative_facts 中的 fact_id，不得引用自身或模块外事实。"
            "reason 用不超过240字的中文引用当前 authoritative_facts 中相关 fact_id 及其实际条件或结果，说明本次裁决的直接依据；"
            "不得引用输入外事实、编造原文或用媒体类型代替理由。最终 JSON 顶层只能包含 decisions。"
        ),
        "model": "",
        "output_schema": AUTHORITY_RECONCILIATION_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "result_cache": {
                "enabled": True,
            },
            "max_turns": 6,
            "validate_before_submit": True,
            "stop_at_tool_keys": ["submit_result"],
            "force_terminal_tool_choice": False,
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
            _FACT_SOURCE_CONTEXT_GUIDANCE +
            "你是测试场景拆分专业 Agent，不生成测试用例正文。"
            "输入的 authoritative_facts 是唯一事实源，plan.test_design_items 是唯一测试设计来源。"
            "consultation_question 仅表示父 Agent 希望解决的问题，不是新的事实、编号或预算来源。"
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
            + _FACT_SOURCE_CONTEXT_GUIDANCE +
            "只能使用 authoritative_facts 中明确存在的事实，不得使用常识或已失效来源补造。"
            "动态值及其展示方式按 authoritative_facts 原样表达，平台不判断或改写其取值。"
            "plan 是当前业务模块规划，batch 是按模块语义、来源页和负载形成的上下文包，"
            "case_budget 是本包必须精确生成的用例数量，可以大于 1。"
            "case_fact_contract 是平台确定的批次级生成契约：必须按 case_budget 精确生成用例，"
            "required_fact_ids 必须在本批全部用例的事实绑定中合计完整覆盖；"
            "每条用例的事实组合必须依据真实条件、角色和生命周期确定，全批必须完整覆盖事实全集。"
            "最终用例的测试设计项编号由平台依据事实路由确定性派生，模型不得输出或猜测编号。"
            "当 _platform_repair.mode=minimal_patch 时，candidate_output 是尚未通过校验的上一版候选，"
            "必须依据原始输入完整复核全部用例，一次性修正 validation_feedback 列出的全部违规项，"
            "并保留已有事实依据且确认正确的内容，不得只处理第一项。"
            "repair_targets 只定位候选实际绑定中的错误，不代表其余位置已获验证；"
            "batch_coverage_gaps 是全批缺口，不指定承载用例，必须按业务语义选择修正位置；"
            "发现其他错误时也必须修正，不得强制恢复旧候选，最终仍返回完整 test_cases。"
            "当 _platform_repair.mode=full_regeneration 时，说明上一版候选结构已损坏且不会提供 candidate_output；"
            "必须重新读取当前原始输入并完整生成本包，不得拼接、补猜或延续上一版残片。"
            "事实应按业务语义分配给最合适的用例，允许在不同用例之间重新组合，不按数组位置机械切分。"
            "batch.semantic_summary、semantic_keywords、source_page_numbers 和 source_scope_ids 只用于说明关联边界，"
            "不得作为新增事实；authoritative_facts 仍是唯一事实源。"
            "当前 authoritative_facts 与 case_fact_contract 共同确定本批唯一必须覆盖的范围。"
            "batch.module_name 表示用例的业务归属，batch.coverage_focus 仅说明当前测试点，"
            "不得因模块名称或范围摘要未提及某条事实而遗漏契约要求；不得补造输入事实之外的内容。"
            "你可以按当前事实和校验反馈自主调用 test_generation_scenario_designer 处理需要拆分的复杂批次。"
            "当当前批次存在跨生命周期、多角色、权限或异常边界组合时，"
            "在工具的 input 字段描述需要解决的问题；平台会自动提供当前批次的真实事实、测试设计和用例预算，"
            "无需复制输入 JSON，也不能通过问题改写来源或预算。单一简单事实可以直接生成。"
            "专业 Agent 只提供事实 ID 和测试设计 ID 的结构建议，"
            "它不是新事实源；你负责判断建议是否适用，可以直接修正候选，或说明当前具体问题再次咨询。"
            "不要在没有新问题或新反馈时重复咨询；父子 Agent 共享请求预算。"
            "如果建议与 case_fact_contract 冲突，以平台契约为准。"
            "当输入包含 gap_contract 时，本批是单个权威事实缺口的修正任务：必须且只能生成一条直接覆盖 "
            "gap_contract.coverage_intent 的用例；不得改为同证据块中的其他测试意图。"
            "必须生成恰好 case_budget 条互不重复的用例，不得多生成或少生成，也不得为了凑数制造低价值变体。"
            "preconditions 只写执行所需且由当前事实支持的条件，可据事实的适用条件选取独立测试样本；"
            "不能虚构业务状态或把预期结果提前作为触发前置；无需额外条件时使用空数组。"
            "需求未声明交互界面时，步骤使用实现无关的业务动作，不得臆造页面、按钮或提示文案。"
            "不要输出 case_id 和 module；平台会按 test_cases 数组顺序生成 case_id，并使用 batch.module_name 写入模块。"
            "每条用例必须可执行，每个步骤的 expected 都必须是当前操作完成后可观察、可验证的断言。"
            "动作所依赖的执行上下文必须由前置条件、前序步骤或明确独立样本建立；"
            "已合法建立的测试起点不要求重复其历史进入流程，测试准备不得引入来源外的产品入口或业务规则。"
            "同一用例可包含条件连续的步骤或明确隔离的子流程；互斥状态必须区分作用样本，"
            "或通过来源支持的操作切换，不能默认前一场景终态满足后一场景前置。"
            "存在生命周期时应明确各子流程的入口状态、触发和可观察结果；异步步骤需有对应的状态衔接。"
            "优先覆盖规划中的主流程、异常、边界、权限和生命周期风险，但不得添加需求外业务规则。"
            "plan.test_design_items 只用于说明规划中的覆盖意图；平台会将其中的原始编号提供给"
            "场景拆分专业 Agent，并从每条用例实际绑定的事实路由派生 test_design_item_ids，"
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
            "available_fact_ids 是本批可引用全集，required_fact_ids 是本批必需事实。"
            "design_support_requirements 直接列出每个设计意图的 support_fact_ids，必须由同一条语义连贯的用例完整承接才计为覆盖。"
            "提交前按该清单核对每个意图及全部支持事实，不要只覆盖同义事实中的一个；多个事实可共同绑定同一字段，"
            "但必须真实支持该字段内容，不能为了满足编号检查挂接不相关事实。"
            "提交用例时必须精确生成 case_budget 条，不得用低价值变体凑数。"
            "只有发现无法在当前事实和数量范围内直接修正的实际范围矛盾时，才调用 report_generation_scope_problem。"
            "报告应具体说明互斥前置、生命周期不连续或分配压力，至少引用两个真实场景组；"
            "发现一个意图的现有支持集合不足时使用 insufficient_design_support，可引用一个真实场景组，不能编造第二组。"
            "fact_ids 和 test_design_item_ids 必须沿 case_fact_contract.design_support_requirements 对应，"
            "condition.quote 必须逐字引用本组事实 assertion 或 source_context 某一个单元格中说明条件的原文，不能跨单元格拼串。"
            "报告不是新增事实、已批准结论或增加预算的请求授权，父协调者会判断继续原范围、重分组或修订规划。"
            "一次候选超量、遗漏事实、绑定错误或结构错误本身不是范围矛盾，能直接修正时继续原提交验收。"
            "若提供 _planning_feedback，其中 instruction 仅定位修正，不改变真实事实与预算。"
            "完成用例时调用 submit_generation_batch，范围报告使用独立报告工具，不得把报告混入 test_cases 或用正文返回 JSON。"
            "平台会在本次会话内完成结构、事实绑定和业务规则验收；若工具返回 status=rejected，"
            "该候选尚未通过验收，必须依据原始事实和 feedback 复核完整候选，修正后重新提交。"
            "可自行决定直接修正、再次咨询专业 Agent 或提交有依据的范围问题；工具验收通过才能结束当前专业会话。"
            "submit_generation_batch 参数顶层只能包含 test_cases，不得输出说明、统计或运行元数据。"
            "test_cases 的值必须直接是 JSON 数组，禁止使用 json.dumps 或其他方式将数组二次序列化为字符串。"
            "test_cases 每项必须且只能包含 title、priority、preconditions、test_input、steps、tags；"
            "tags 必须是字符串数组且可以省略，test_design_item_ids 不由模型输出。"
            "禁止在用例顶层输出 expected_result 或 expected，也禁止使用 step、description、module_name "
            "等别名替代用例字段。"
        ),
        "model": "",
        "output_schema": MODEL_GROUNDING_SCHEMA,
        "runtime_config": {
            # 完整事实绑定与固定数量下的场景综合使用现有高质量模型路由。
            "model_route": "review",
            "transient_fallback_model_route": "turbo",
            "transient_fallback_after_failures": 2,
            # 主 Agent 与专业 Agent 共用六次请求预算，允许会话内修正与再次咨询。
            "max_turns": 6,
            "validate_before_submit": True,
            "request_timeout_seconds": 180,
            "max_retries": 0,
            "max_output_tokens": 12000,
            # 生成模型只接收当前批次的语义字段；原始锚点和全量契约保留在
            # node input，供平台后处理、哈希和审计使用。
            "input_projection_version": "generation-model-v11-design-support",
            "input_projection": {
                "plan": {
                    "business_module": [
                        "name",
                        "objective",
                        "actors",
                        "lifecycle",
                    ],
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
                    "source_document_ids",
                    "source_page_numbers",
                    "source_scope_ids",
                ],
                "authoritative_facts": [
                    "fact_id",
                    "assertion",
                    "source_context",
                    "scope_id",
                    "status",
                    "value_policy",
                    "governed_values",
                    "governed_by",
                ],
                "_planning_feedback": True,
                "case_fact_contract": {
                    "required_fact_ids": True,
                    "available_fact_ids": True,
                    "design_support_requirements": True,
                },
            },
            "extra_body": {"thinking": {"type": "disabled"}},
            "disable_server_output_schema": True,
            "subagent_keys": ["test_generation_scenario_designer"],
            "tool_keys": ["submit_generation_batch", "report_generation_scope_problem"],
            "stop_at_tool_keys": ["submit_generation_batch", "report_generation_scope_problem"],
            # 用例与范围问题使用不同验收通道，只有父协调者授予的报告通道可以接纳问题。
            "force_terminal_tool_choice": False,
        },
    },
    {
        "agent_key": "test_generation_final_reviewer",
        "name": "测试用例独立终审智能体",
        "version": 1,
        "description": "按模块批次独立审查用例的业务语义、可执行性和状态连贯性。",
        "instructions": (
            _FACT_SOURCE_CONTEXT_GUIDANCE +
            "你是独立用例终审 Agent，只审查输入 review_batch 的当前候选。test_cases 是完整当前版本，"
            "case_fact_bindings 给出逐字段依据，review_facts 给出来源规则，test_design_items 给出覆盖意图。"
            "audit_summary 只证明结构和引用完整，你须独立判断业务语义、实际覆盖及可执行性。"
            "逐条完整阅读用例的 test_input、preconditions 和全部 steps，再作判断；这些字段共同建立执行上下文，"
            "已明确提供的样本、配置、角色和状态不必在其他字段重复声明，也不要求追溯预置样本的构造历史。"
            "按动作的完整原文确认是否已经建立所需状态，不能忽略同一句中的进入、创建、查询或触发操作。"
            "前置允许给出满足来源约束的真实可建立状态；在已有状态下直接读取、查询或观察是有效的测试动作，"
            "不要求每个断言都重新触发业务。动作完成后的预期可以观察其业务结果，无需为每个观察强制新增界面或步骤。"
            "区分真实矛盾与未展开的细节：只有来源必需条件缺失、当前样本确实冲突、动作依赖无法建立的状态、"
            "预期没有检验覆盖义务或引入来源外规则时才拒绝。对实现路径、环境权限或样本生成方式的猜测不能作为拒绝依据。"
            "不同属性可以同时成立；多个明确独立的样本可以在同一用例测试，不得仅因共用活动、页面或存在多个子流程就推定状态互斥。"
            "连续步骤沿用前序终态；独立子流程以各自声明的样本为起点。只有实际混用同一对象的互斥状态才报 state_coherence。"
            "事实支持按完整业务含义核对，来源允许的配置值和 runtime_configured 动态值不得因缺少固定常量而拒绝。"
            "输入域约束可以验证违规值不能形成有效配置；不得要求来源未规定的提示文案、返回码或特定拒绝机制。"
            "语义等价的结果可覆盖规则；不得要求逐字照抄事实、重复查询已观察结果或逐个列名才能验证集合为空等集合断言。"
            "每条拒绝结论必须同时说明：当前用例实际写了什么、来源要求什么、两者具体在哪里冲突或缺失。"
            "提交前将 repair_instruction 与当前字段完整原文对照：若它只是要求补写已经存在的动作、样本或断言，应撤回该差异。"
            "修复指令同样必须受来源约束，不得要求虚构产品入口、实现机制或互斥条件，也不能把业务禁止行为的成功执行作为测试前提。"
            "不能只凭数量和编号正确而通过；规则说明或事实编号挂载不能代替实际测试。"
            "semantic_duplicate 必须指向重复检验的同一事实语义；不同对象、输入域、状态、权限、生命周期或结果分支是独立覆盖义务。"
            "当前 case_id 内可修正时 repair_scope=case；确需重新分配场景或事实时 repair_scope=cohort，"
            "并指出当前限制和真实冲突；若需要修改其他用例，修复指令必须明确目标 case_id，不能笼统要求重做同批全部用例。"
            "不能通过删除事实义务消除问题，不能强制增加总用例预算。来源本身有误时指出原文与候选事实的冲突，不编造满足错误事实的功能。"
            "每个 difference 必须包含 case_id、category、field_path、detail、repair_scope、repair_instruction；"
            "field_path 定位当前用例实际存在的一个字段，数组下标从0开始。多个落点分别报告，不拼接多个路径。"
            "category 只能是 business_semantics、executability、state_coherence、unsupported_business_rule、semantic_duplicate。"
            "不要输出 related_fact_ids，平台从当前绑定派生。approved=true 时 differences 必须为空，有未解决差异时为false。"
            "最终 JSON 只能包含 phase、approved、summary、differences；phase 固定为 final_review，summary 用简短中文说明结论。"
            "不得修改或重新输出用例。"
        ),
        "model": "",
        "output_schema": BATCH_FINAL_REVIEW_AGENT_OUTPUT_SCHEMA,
        "runtime_config": {
            "model_route": "review",
            "disable_server_output_schema": True,
            "max_turns": 6,
            "validate_before_submit": True,
            "stop_at_tool_keys": ["submit_result"],
            "force_terminal_tool_choice": False,
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
            _FACT_SOURCE_CONTEXT_GUIDANCE +
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
            "同一用例可包含条件连续的步骤或明确隔离的子流程；互斥状态应分别建立独立样本或按来源支持的操作切换，"
            "标明各自作用对象及状态，不能默认前一场景终态满足后一场景前置，也不能靠规则说明代替实际执行。"
            "满足事实约束的界面和业务状态可以直接作为测试前置；只有当前前置和前序步骤都未建立所需上下文时才补充。"
            "测试准备不得虚构产品入口或业务规则；已有触发后的中性查看、观察或读取可以验证结果。"
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
            "test_design_items 是当前批次的覆盖意图；修复后必须由语义连贯的用例完整承接对应支持事实集合。"
            "设计覆盖编号由平台根据 fact_design_item_ids 和实际绑定重新派生，不能靠移动或填写编号替代覆盖。"
            "最终 JSON 顶层只能包含 case_patches，不得输出 test_cases、说明、统计或修复摘要。"
            "每个补丁只能包含 case_id 以及确实需要替换的 title、priority、preconditions、test_input、steps、tags。"
            "省略字段由平台保持原值。修改步骤或前置条件时必须完整输出该字段的新数组。"
        ),
        "model": "",
        "output_schema": MODEL_REPAIR_PATCH_SCHEMA,
        "runtime_config": {
            # 批次修复需要重组跨生命周期结构，使用主模型提升复杂迁移的稳定性；终审仍保持独立评审路由。
            "model_route": "main",
            "disable_server_output_schema": True,
            "max_turns": 6,
            "validate_before_submit": True,
            "stop_at_tool_keys": ["submit_result"],
            "force_terminal_tool_choice": False,
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
            "max_turns": 6,
            "validate_before_submit": True,
            "stop_at_tool_keys": ["submit_result"],
            "force_terminal_tool_choice": False,
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

# 来源提取与初始草案独立落盘；后续规划、生成和审查共用可恢复的目标状态。
BUILTIN_WORKFLOW_SPECS = (
    {
        "workflow_key": "test_generation",
        "version": 1,
        "name": "多 Agent 测试用例生成",
        "description": "从真实来源建立初始理解，由统一协调者自主调整规划、生成范围与独立审查，完成后验收交付。",
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
                    "stage_key": "source",
                    "label": "来源与初始理解",
                    "description": "提取真实来源事实，形成可追溯的初始规划草案",
                    "node_keys": [
                        "evidence", "prepare_source_semantics", "source_text", "source_vision",
                        "source_semantics", "prepare_plan_batches", "plan_batches",
                    ],
                },
                {
                    "stage_key": "generation",
                    "label": "自主规划、生成与审查",
                    "description": "依据实际问题选择取证、重规划、调整生成范围或独立复审",
                    "node_keys": ["resolve_generation"],
                },
                {
                    "stage_key": "delivery",
                    "label": "验收与交付",
                    "description": "核验当前已批准版本，形成执行链并保存用例",
                    "node_keys": [
                        "approved_cases", "validated_cases", "chain_context",
                        "chain_selection", "execution_chain", "persist",
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
                    "node_key": "resolve_generation",
                    "node_type": "agent_network",
                    "reference_key": "test_generation_coordinator",
                    "depends_on": [
                        "evidence", "prepare_source_semantics", "source_text", "source_vision",
                        "source_semantics", "prepare_plan_batches", "plan_batches",
                    ],
                    "max_attempts": 2,
                    "time_budget_seconds": 7200,
                    "input_mapping": {
                        "evidence_source": "dependencies.evidence.source",
                        "evidence_catalog": "dependencies.evidence.evidence_catalog",
                        "source_text_inputs": "dependencies.prepare_source_semantics.text_items",
                        "source_text_records": "dependencies.source_text.items",
                        "source_vision_inputs": "dependencies.prepare_source_semantics.vision_items",
                        "source_vision_records": "dependencies.source_vision.items",
                        "planning_inputs": "dependencies.prepare_plan_batches.items",
                        "planning_records": "dependencies.plan_batches.items",
                        "case_budget": "input.case_budget",
                        "batch_case_limit": "input.batch_case_limit",
                    },
                },
                {
                    "node_key": "approved_cases",
                    "node_type": "tool",
                    "reference_key": "approve_synthesized_test_cases",
                    "depends_on": ["resolve_generation"],
                    "input_mapping": {
                        "generation": "dependencies.resolve_generation.generation",
                        "audit_summary": "dependencies.resolve_generation.audit_summary",
                        "final_review": "dependencies.resolve_generation.final_review",
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
                    "depends_on": ["resolve_generation", "validated_cases"],
                    "input_mapping": {
                        "plan": "dependencies.resolve_generation.plan",
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


