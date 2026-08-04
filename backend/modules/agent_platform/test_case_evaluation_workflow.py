from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .artifacts import stage_run_artifact

if TYPE_CHECKING:
    from .registry import ToolExecutionContext, ToolRegistry


TEXT_LIST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "maxItems": 30,
    "items": {"type": "string", "minLength": 1, "maxLength": 300},
}

REQUIREMENT_POINT_LIST_SCHEMA: dict[str, Any] = {
    **TEXT_LIST_SCHEMA,
    "minItems": 1,
}

METRICS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "precision": {"type": "number", "minimum": 0, "maximum": 1},
        "recall": {"type": "number", "minimum": 0, "maximum": 1},
        "f1_score": {"type": "number", "minimum": 0, "maximum": 1},
        "semantic_similarity": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["precision", "recall", "f1_score", "semantic_similarity"],
    "additionalProperties": False,
}

DEFECT_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "missing_points": TEXT_LIST_SCHEMA,
        "hallucinations": TEXT_LIST_SCHEMA,
        "modifications": TEXT_LIST_SCHEMA,
    },
    "required": ["missing_points", "hallucinations", "modifications"],
    "additionalProperties": False,
}

REQUIREMENT_BASELINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement_points": TEXT_LIST_SCHEMA,
        "ai_requirement_gaps": TEXT_LIST_SCHEMA,
        "human_requirement_gaps": TEXT_LIST_SCHEMA,
        "ai_unanchored_points": TEXT_LIST_SCHEMA,
        "human_added_value": TEXT_LIST_SCHEMA,
        "both_missing_points": TEXT_LIST_SCHEMA,
        "covered_by_both": TEXT_LIST_SCHEMA,
        "generated_coverage_count": {"type": "integer", "minimum": 0},
        "modified_coverage_count": {"type": "integer", "minimum": 0},
        "generated_coverage_rate": {"type": "number", "minimum": 0, "maximum": 1},
        "modified_coverage_rate": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
    },
    "required": [
        "requirement_points",
        "ai_requirement_gaps",
        "human_requirement_gaps",
        "ai_unanchored_points",
        "human_added_value",
        "both_missing_points",
        "covered_by_both",
        "generated_coverage_count",
        "modified_coverage_count",
        "generated_coverage_rate",
        "modified_coverage_rate",
        "summary",
    ],
    "additionalProperties": False,
}

TEST_CASE_EVALUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "metrics": METRICS_SCHEMA,
        "defect_analysis": DEFECT_ANALYSIS_SCHEMA,
        "requirement_baseline": REQUIREMENT_BASELINE_SCHEMA,
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
    "required": [
        "metrics",
        "defect_analysis",
        "requirement_baseline",
        "summary",
    ],
    "additionalProperties": False,
}

EVALUATION_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirement_points": REQUIREMENT_POINT_LIST_SCHEMA,
        "agent_coverage": TEXT_LIST_SCHEMA,
        "manual_coverage": TEXT_LIST_SCHEMA,
        "missing_points": TEXT_LIST_SCHEMA,
        "hallucinations": TEXT_LIST_SCHEMA,
        "modifications": TEXT_LIST_SCHEMA,
        "precision": {"type": "number", "minimum": 0, "maximum": 1},
        "recall": {"type": "number", "minimum": 0, "maximum": 1},
        "f1_score": {"type": "number", "minimum": 0, "maximum": 1},
        "semantic_similarity": {"type": "number", "minimum": 0, "maximum": 1},
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
    "required": [
        "requirement_points",
        "agent_coverage",
        "manual_coverage",
        "missing_points",
        "hallucinations",
        "modifications",
        "precision",
        "recall",
        "f1_score",
        "semantic_similarity",
        "summary",
    ],
    "additionalProperties": False,
}


def _ordered_membership(
    requirement_points: list[str],
    covered_points: set[str],
) -> list[str]:
    return [point for point in requirement_points if point in covered_points]


def _build_evaluation_result(evidence: dict[str, Any]) -> dict[str, Any]:
    requirement_points = [str(item) for item in evidence["requirement_points"]]
    agent_coverage = {str(item) for item in evidence["agent_coverage"]}
    manual_coverage = {str(item) for item in evidence["manual_coverage"]}
    covered_by_agent = _ordered_membership(requirement_points, agent_coverage)
    covered_by_manual = _ordered_membership(requirement_points, manual_coverage)
    covered_by_both = _ordered_membership(
        requirement_points,
        agent_coverage & manual_coverage,
    )
    ai_gaps = _ordered_membership(requirement_points, set(requirement_points) - agent_coverage)
    human_gaps = _ordered_membership(requirement_points, set(requirement_points) - manual_coverage)
    both_missing = _ordered_membership(
        requirement_points,
        set(requirement_points) - agent_coverage - manual_coverage,
    )
    total = len(requirement_points)

    return {
        "metrics": {
            "precision": float(evidence["precision"]),
            "recall": float(evidence["recall"]),
            "f1_score": float(evidence["f1_score"]),
            "semantic_similarity": float(evidence["semantic_similarity"]),
        },
        "defect_analysis": {
            "missing_points": list(evidence["missing_points"]),
            "hallucinations": list(evidence["hallucinations"]),
            "modifications": list(evidence["modifications"]),
        },
        "requirement_baseline": {
            "requirement_points": requirement_points,
            "ai_requirement_gaps": ai_gaps,
            "human_requirement_gaps": human_gaps,
            "ai_unanchored_points": list(evidence["hallucinations"]),
            "human_added_value": list(evidence["missing_points"]),
            "both_missing_points": both_missing,
            "covered_by_both": covered_by_both,
            "generated_coverage_count": len(covered_by_agent),
            "modified_coverage_count": len(covered_by_manual),
            "generated_coverage_rate": round(len(covered_by_agent) / total, 4),
            "modified_coverage_rate": round(len(covered_by_manual) / total, 4),
            "summary": str(evidence["summary"]),
        },
        "summary": str(evidence["summary"]),
    }


def persist_test_case_evaluation(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """将评审结果同时固化到评测 Run 和源生成 Run。"""

    source_run_id = int(arguments["source_run_id"])
    evaluation = _build_evaluation_result(dict(arguments["evaluation"]))
    upload = dict(arguments.get("upload") or {})
    artifact = {
        "source_run_id": source_run_id,
        "evaluation_run_id": context.run_id,
        "project_id": context.project_id,
        "requirement": str(arguments["requirement"]),
        "reference_content": str(arguments["reference_content"]),
        "evaluation": evaluation,
        "upload": {
            "filename": str(upload.get("filename") or ""),
            "content_type": str(upload.get("content_type") or ""),
            "size": int(upload.get("size") or 0),
            "ocr": {
                "source": str(upload.get("ocr_source") or "not_image"),
                "ok": bool(upload.get("ocr_ok", False)),
                "cloud_fallback": bool(upload.get("cloud_fallback", False)),
                "error": str(upload.get("ocr_error") or ""),
            },
        },
    }
    context.artifacts["test_evaluation"] = artifact
    stage_run_artifact(
        db=context.db,
        project_id=context.project_id,
        user_id=context.user_id,
        run_id=source_run_id,
        artifact_key="test_evaluation",
        payload=artifact,
    )
    return {
        "status": "persisted",
        "run_id": context.run_id,
        "source_run_id": source_run_id,
        "artifact_key": "test_evaluation",
    }


PERSIST_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "const": "persisted"},
        "run_id": {"type": "integer", "minimum": 1},
        "source_run_id": {"type": "integer", "minimum": 1},
        "artifact_key": {"type": "string", "const": "test_evaluation"},
    },
    "required": ["status", "run_id", "source_run_id", "artifact_key"],
    "additionalProperties": False,
}

UPLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filename": {"type": "string"},
        "content_type": {"type": "string"},
        "size": {"type": "integer", "minimum": 0},
        "ocr_source": {"type": "string"},
        "ocr_ok": {"type": "boolean"},
        "cloud_fallback": {"type": "boolean"},
        "ocr_error": {"type": "string"},
    },
    "required": [
        "filename",
        "content_type",
        "size",
        "ocr_source",
        "ocr_ok",
        "cloud_fallback",
        "ocr_error",
    ],
    "additionalProperties": False,
}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_run_id": {"type": "integer", "minimum": 1},
        "requirement": {"type": "string", "minLength": 1},
        "generated_cases": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "object"},
        },
        "reference_content": {"type": "string", "minLength": 1},
        "project_context": {"type": "string"},
        "upload": UPLOAD_SCHEMA,
    },
    "required": [
        "source_run_id",
        "requirement",
        "generated_cases",
        "reference_content",
        "project_context",
        "upload",
    ],
    "additionalProperties": False,
}

BUILTIN_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "tool_key": "persist_test_case_evaluation",
        "name": "持久化用例评测",
        "description": "将结构化用例评审结果写入评测 Run 并关联源生成 Run。",
        "handler_key": "testing.persist_test_case_evaluation",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_run_id": {"type": "integer", "minimum": 1},
                "requirement": {"type": "string", "minLength": 1},
                "reference_content": {"type": "string", "minLength": 1},
                "upload": UPLOAD_SCHEMA,
                "evaluation": EVALUATION_EVIDENCE_SCHEMA,
            },
            "required": [
                "source_run_id",
                "requirement",
                "reference_content",
                "upload",
                "evaluation",
            ],
            "additionalProperties": False,
        },
        "output_schema": PERSIST_OUTPUT_SCHEMA,
        "risk_level": "medium",
        "requires_approval": False,
    },
)

BUILTIN_AGENT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "agent_key": "test_case_evaluator",
        "name": "测试用例评审智能体",
        "description": "基于当前需求、Agent 生成用例和人工最终用例进行证据化对比。",
        "instructions": (
            "你是测试用例评审智能体。事实源只有输入中的 requirement、generated_cases、"
            "reference_content 和 project_context，不得臆造需求、用例或执行结果。"
            "reference_content 可能是 JSON、CSV、Markdown 或普通文本，应根据内容语义解析，不要针对文件名或某一种模板特调。"
            "先从当前 requirement 提取可核验的需求要点，再分别判定 Agent 用例和人工用例的覆盖情况。"
            "输出必须是单层 JSON，只能包含 requirement_points、agent_coverage、manual_coverage、"
            "missing_points、hallucinations、modifications、precision、recall、f1_score、"
            "semantic_similarity、summary 这 11 个顶层字段，不得嵌套 metrics 或 defect_analysis。"
            "agent_coverage 和 manual_coverage 中的每一项必须原样复制自 requirement_points，"
            "不得改写需求点，以便工具精确计算覆盖关系。"
            "missing_points 只填人工版本具有而 Agent 版本缺失的有价值要点；"
            "hallucinations 只填 Agent 版本中无需求或人工版本支撑的内容；"
            "modifications 填双方相同意图下的实质修改。"
            "precision、recall、f1_score、semantic_similarity 均为 0 到 1，并与缺失、幻觉和覆盖证据一致。"
            "需求本身没有足够证据时，应降低评分并在 summary 说明证据不足，不得用历史画像或静态场景词典补齐。"
            "所有分析文本使用中文，特殊名词、API 和代码标识除外；严格按输出 Schema 返回 JSON。"
        ),
        "model": "",
        "output_schema": EVALUATION_EVIDENCE_SCHEMA,
        "runtime_config": {"model_route": "main", "max_turns": 8, "tool_keys": []},
    },
)

BUILTIN_WORKFLOW_SPECS: tuple[dict[str, Any], ...] = (
    {
        "workflow_key": "test_case_evaluation",
        "name": "测试用例 Agent 评测",
        "description": "使用当前需求和真实的两版用例产出结构化对比产物。",
        "definition": {
            "input_schema": INPUT_SCHEMA,
            "nodes": [
                {
                    "node_key": "evaluate",
                    "node_type": "agent",
                    "reference_key": "test_case_evaluator",
                    "depends_on": [],
                    "max_attempts": 1,
                },
                {
                    "node_key": "persist",
                    "node_type": "tool",
                    "reference_key": "persist_test_case_evaluation",
                    "depends_on": ["evaluate"],
                    "max_attempts": 1,
                    "input_mapping": {
                        "source_run_id": "input.source_run_id",
                        "requirement": "input.requirement",
                        "reference_content": "input.reference_content",
                        "upload": "input.upload",
                        "evaluation": "dependencies.evaluate",
                    },
                },
            ],
            "output_node_key": "persist",
        },
    },
)


def register_test_case_evaluation_tools(registry: ToolRegistry) -> None:
    registry.register(
        "testing.persist_test_case_evaluation",
        persist_test_case_evaluation,
    )
