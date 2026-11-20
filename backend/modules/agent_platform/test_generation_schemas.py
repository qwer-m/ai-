"""单主 Agent 生成的唯一工具契约；不再经过事实归属和多层草案转换。"""

from copy import deepcopy


def object_schema(properties, *, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1}
TEXTS = {"type": "array", "items": TEXT, "uniqueItems": True}
COVERAGE_ID_PATTERN = r"^COV-[0-9]{4,}$"
COVERAGE_ID_SCHEMA = {**TEXT, "pattern": COVERAGE_ID_PATTERN}
MAX_COVERAGE_ITEMS = 200
SOURCE_REFS_SCHEMA = {"type": "array", "minItems": 1, "items": TEXT}
COVERAGE_ITEM_SCHEMA = object_schema({
    "coverage_id": COVERAGE_ID_SCHEMA, "objective": TEXT,
    "conditions": TEXTS, "exceptions": TEXTS, "source_refs": SOURCE_REFS_SCHEMA,
})
COVERAGE_UPDATES_SCHEMA = {"type": "array", "maxItems": MAX_COVERAGE_ITEMS, "items": COVERAGE_ITEM_SCHEMA}
COVERAGE_REMOVALS_SCHEMA = {"type": "array", "maxItems": MAX_COVERAGE_ITEMS,
                            "uniqueItems": True, "items": COVERAGE_ID_SCHEMA}
CASE_CONTENT_SCHEMA = object_schema({
    "title": TEXT, "module": TEXT, "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
    "preconditions": {"type": "array", "items": TEXT}, "test_input": {"type": "string"},
    "steps": {"type": "array", "minItems": 1, "items": object_schema({"action": TEXT, "expected": TEXT})},
    "tags": TEXTS, "coverage_ids": {"type": "array", "items": COVERAGE_ID_SCHEMA, "uniqueItems": True,
        "description": "可选辅助覆盖索引，只能引用已存在的COV编号；省略表示空数组"},
    "source_refs": SOURCE_REFS_SCHEMA,
}, required=["title", "module", "priority", "preconditions", "test_input", "steps", "tags", "source_refs"])
ISSUE_SCHEMA = object_schema({
    "kind": {"type": "string", "enum": ["source_conflict", "missing_rule", "case_error", "coverage_gap"]},
    "target_case_ids": TEXTS, "coverage_ids": TEXTS, "reason": TEXT, "source_refs": SOURCE_REFS_SCHEMA,
})
STEP_REFS = {"type": "array", "minItems": 1, "uniqueItems": True,
             "items": object_schema({"case_id": TEXT, "step_number": {"type": "integer", "minimum": 1}})}
_FULL_REVIEW_SCHEMA = object_schema({
    "source_checks": {"type": "array", "minItems": 1, "items": object_schema({
        "evidence_id": TEXT,
        "branches": {"type": "array", "minItems": 1, "items": object_schema({
            "rule": TEXT, "case_ids": TEXTS,
            "disposition": {"type": "string", "enum": ["covered", "transition_covered", "effect_covered", "gap", "superseded", "reference"],
                "description": "状态切换有实际操作及到达状态时用transition_covered，缺任一项用gap。effect_covered用于实际值或其他功能的验证；covered用于其余已验证行为。"},
            "transition_steps": {**STEP_REFS,
                "description": "仅transition_covered提供；定位从所需起始状态执行切换并验证结果的步骤，按用例中顺序从1计数。"},
            "related_evidence_ids": TEXTS,
            "verification_steps": {**STEP_REFS,
                "description": "仅effect_covered提供，定位验证实际取值或受保护功能的操作和可观察预期。"},
        }, required=["rule", "case_ids", "disposition", "related_evidence_ids"])},
    })},
    "case_checks": {"type": "array", "minItems": 1, "items": object_schema({
        "case_id": TEXT, "additional_evidence_ids": TEXTS,
        "scope_reason": {**TEXT, "description": "用中文说明当前用例的前提、输入和步骤如何处理引用来源的适用条件与例外。"},
        "valid": {"type": "boolean"},
    })},
    "business_questions": {"type": "array", "items": object_schema({
        **{key: value for key, value in ISSUE_SCHEMA["properties"].items() if key != "coverage_ids"},
        "kind": {"type": "string", "enum": ["source_conflict", "missing_rule"]},
    })},
    "issue_resolutions": {"type": "array", "items": object_schema({
        "issue_id": TEXT, "resolution": {"type": "string", "enum": ["resolved", "open", "needs_human"]},
        "reason": TEXT, "source_refs": SOURCE_REFS_SCHEMA,
        "target_case_ids": {**TEXTS, "minItems": 1,
            "description": "仅open coverage_gap可显式更正候选修复对象；省略保留当前归属。"},
    }, required=["issue_id", "resolution", "reason", "source_refs"])},
})
# 同一独审会话入口按本次输入分别验收全面审查和步骤核对，两类回执不能混交。
MAX_WITNESS_CLAIMS = 32
_WITNESS_SCHEMA = object_schema({"witness_checks": {"type": "array", "minItems": 1, "maxItems": MAX_WITNESS_CLAIMS,
    "items": object_schema({
        "claim_id": TEXT,
        "verified": {"type": "array", "items": object_schema({
            "source_quote": TEXT, "case_id": TEXT,
            "action_step": {"type": "integer", "minimum": 1},
            "expected_step": {"type": "integer", "minimum": 1},
        })},
        "gaps": {"type": "array", "items": object_schema({"source_quote": TEXT, "missing": TEXT})},
    })}})
REVIEW_SCHEMA = object_schema({**_FULL_REVIEW_SCHEMA["properties"], **_WITNESS_SCHEMA["properties"]}, required=[])
REVIEW_SCHEMA["oneOf"] = [{"required": schema["required"], "maxProperties": len(schema["properties"])}
                          for schema in (_FULL_REVIEW_SCHEMA, _WITNESS_SCHEMA)]
GENERATION_RESOLUTION_SCHEMA = object_schema({"completed": {"type": "boolean", "const": True}})
GENERATION_INPUT_SCHEMA = object_schema({
    "requirement": {"type": "string"}, "requirement_doc_id": {"type": ["integer", "null"], "minimum": 1},
    "case_budget": {"type": "integer", "minimum": 1, "maximum": 200},
    "batch_case_limit": {"type": "integer", "minimum": 1, "maximum": 20},
    "disable_result_cache": {"type": "boolean"},
}, required=["requirement", "requirement_doc_id", "case_budget", "batch_case_limit"])
GENERATION_INPUT_SCHEMA["anyOf"] = [
    {"properties": {"requirement": {"type": "string", "minLength": 1}}},
    {"properties": {"requirement_doc_id": {"type": "integer", "minimum": 1}}},
]

# 正常任务的验收预算显式冻结到Run，恢复和重规划不清零。
GENERATION_EXECUTION_LIMITS = {"max_requests": 32, "max_input_tokens": 300000,
                               "max_output_tokens": 100000, "max_total_tokens": 300000}


def copy_schema(value):
    return deepcopy(value)
