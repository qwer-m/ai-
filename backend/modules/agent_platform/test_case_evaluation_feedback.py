"""把真实两版用例评测差异转换成项目内待确认的反馈候选。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from core.db.model_defs import AgentContextRecord, AgentRun, AgentWorkflowDefinition
from .results import persisted_test_generation_result
from .run_contract import input_payload_hash
from .sources import SourceSnapshot, persisted_source_snapshot


EVALUATION_DIFFERENCE_KIND = "test_case_evaluation_difference"
DIFFERENCE_CATEGORIES = ("missing_points", "hallucinations", "modifications")


def evaluation_source_inputs(run: AgentRun) -> tuple[str, list[dict[str, Any]], SourceSnapshot]:
    """从同一个已交付的生成 Run 读取需求、用例和来源版本。"""

    if run.status != "success":
        raise ValueError("只有已成功交付用例的运行可以进行人工对比评测")
    artifact = persisted_test_generation_result(run) or {}
    cases = artifact.get("test_cases")
    if not isinstance(cases, list) or not cases or not all(isinstance(case, dict) for case in cases):
        raise ValueError("生成运行缺少可对比的用例产物")
    requirement = str(artifact.get("requirement") or "")
    snapshot = persisted_source_snapshot(run)
    if not requirement.strip() or snapshot is None:
        raise ValueError("生成运行缺少可追溯的需求正文或来源版本")
    supplement = (run.input_payload or {}).get("requirement") if snapshot.document_id else None
    sections = [requirement]
    if supplement and supplement.strip():
        sections.append("【本轮补充说明】\n" + supplement)
    for decision in artifact.get("approved_decisions", []):
        sections.append(f"【已确认口径 {decision['evidence_id']}】\n{decision['text']}")
    # 评测只传执行业务字段，来源原文已在需求基线中，无需逐用例重复发送。
    fields = ("case_id", "title", "module", "preconditions", "test_input", "steps", "priority")
    return "\n\n".join(sections), [{k: case[k] for k in fields} for case in cases], snapshot


def evaluation_input_identity(
    db: Session, *, evaluation_run: AgentRun, source_run_id: int,
) -> dict[str, Any]:
    """回读真实运行，验证三份输入与源生成产物一致，不使用请求方自报的身份。"""

    workflow = db.get(AgentWorkflowDefinition, evaluation_run.workflow_definition_id)
    if workflow is None or workflow.workflow_key != "test_case_evaluation":
        raise ValueError("反馈必须来自正式用例评测运行")
    source_run = db.get(AgentRun, int(source_run_id))
    if source_run is None:
        raise LookupError("反馈关联的源生成运行不存在")
    if source_run.project_id != evaluation_run.project_id or source_run.user_id != evaluation_run.user_id:
        raise ValueError("评测反馈不能跨项目或用户关联运行")
    source_requirement, source_cases, source_snapshot = evaluation_source_inputs(source_run)
    inputs = dict(evaluation_run.input_payload or {})
    if inputs.get("source_run_id") != int(source_run.id):
        raise ValueError("评测输入与源生成运行身份不一致")
    requirement = str(inputs.get("requirement") or "")
    if requirement != source_requirement:
        raise ValueError("评测需求与源生成运行的需求版本不一致")
    generated_cases = inputs.get("generated_cases")
    if not isinstance(generated_cases, list) or input_payload_hash(generated_cases) != input_payload_hash(source_cases):
        raise ValueError("评测生成用例与源运行已交付版本不一致")
    reference_content = str(inputs.get("reference_content") or "")
    if not reference_content.strip():
        raise ValueError("评测反馈缺少人工最终用例")
    return {
        "source_run_id": int(source_run.id),
        "evaluation_run_id": int(evaluation_run.id),
        "project_id": int(evaluation_run.project_id),
        "input_hashes": {
            "requirement": input_payload_hash(requirement),
            "generated_cases": input_payload_hash(generated_cases),
            "reference_content": input_payload_hash(reference_content),
            "baseline_groups": input_payload_hash(inputs["baseline_groups"]),
        },
        "source_snapshot": source_snapshot.to_dict(),
        "source_execution_contract_hash": str(
            ((source_run.run_context or {}).get("execution_contract") or {}).get("hash") or ""
        ),
        "evaluation_execution_contract_hash": str(
            ((evaluation_run.run_context or {}).get("execution_contract") or {}).get("hash") or ""
        ),
    }


def difference_candidate_contents(identity: dict[str, Any], evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    """仅转换已记录的差异，不用分数、成功状态或摘要推导新的业务规则。"""

    analysis = evaluation.get("defect_analysis")
    if not isinstance(analysis, dict):
        raise ValueError("评测结果缺少结构化差异")
    evaluation_hash = input_payload_hash(evaluation)
    result: list[dict[str, Any]] = []
    for category in DIFFERENCE_CATEGORIES:
        differences = analysis.get(category)
        if not isinstance(differences, list):
            raise ValueError(f"评测差异 {category} 必须是数组")
        for index, difference in enumerate(differences):
            if not isinstance(difference, str) or not difference.strip():
                raise ValueError("评测差异必须是非空文本")
            result.append({
                **deepcopy(identity),
                "kind": EVALUATION_DIFFERENCE_KIND,
                "category": category,
                "text": difference,
                "difference_text": difference,
                "difference_path": f"evaluation.defect_analysis.{category}[{index}]",
                "difference_hash": input_payload_hash(difference),
                "evaluation_hash": evaluation_hash,
                "evaluator_type": "model",
            })
    return result


def stage_evaluation_feedback(
    context: Any, *, source_run_id: int, evaluation: dict[str, Any],
) -> list[int]:
    """与评测产物共用事务；锁定评测运行，使工具重放不会重复建立反馈候选。"""

    from modules.knowledge_base_components.context.context_memory_repository import ContextMemoryRepository

    evaluation_run = context.db.query(AgentRun).filter(AgentRun.id == context.run_id).with_for_update().first()
    if evaluation_run is None or evaluation_run.project_id != context.project_id or evaluation_run.user_id != context.user_id:
        raise LookupError("当前评测运行不存在或不属于当前用户和项目")
    identity = evaluation_input_identity(context.db, evaluation_run=evaluation_run, source_run_id=source_run_id)
    repository = ContextMemoryRepository(context.db)
    record_ids: list[int] = []
    for content in difference_candidate_contents(identity, evaluation):
        revision = input_payload_hash(content)
        existing = context.db.query(AgentContextRecord).filter(
            AgentContextRecord.source_kind == "validated_feedback",
            AgentContextRecord.scope == "project",
            AgentContextRecord.scope_id == str(context.project_id),
            AgentContextRecord.revision == revision,
        ).first()
        if existing is not None:
            record_ids.append(int(existing.id))
            continue
        record = repository.create_pending(
            user_id=int(context.user_id), project_id=int(context.project_id),
            source_kind="validated_feedback", scope="project", scope_id=str(context.project_id),
            revision=revision, content=content,
            evidence_refs=[{
                "kind": EVALUATION_DIFFERENCE_KIND,
                **deepcopy(identity),
                "difference_path": content["difference_path"],
                "difference_hash": content["difference_hash"],
                "evaluation_hash": content["evaluation_hash"],
            }],
            origin_run_id=int(source_run_id),
        )
        record_ids.append(int(record.id))
    return record_ids


def validate_evaluation_feedback_record(db: Session, record: AgentContextRecord) -> None:
    """确认和检索均回读被评版本；不能把变更、删除或失败的评测当成当前依据。"""

    content = dict(record.content or {})
    if content.get("kind") != EVALUATION_DIFFERENCE_KIND:
        return
    evaluation_run_id = content.get("evaluation_run_id")
    source_run_id = content.get("source_run_id")
    if type(evaluation_run_id) is not int or type(source_run_id) is not int:
        raise ValueError("评测反馈缺少真实运行身份")
    evaluation_run = db.get(AgentRun, evaluation_run_id)
    if evaluation_run is None or evaluation_run.status != "success":
        raise ValueError("反馈关联的评测运行不存在或尚未成功")
    if evaluation_run.user_id != record.user_id or evaluation_run.project_id != record.project_id:
        raise ValueError("评测反馈与运行的用户或项目不一致")
    if (record.source_kind != "validated_feedback" or record.origin_run_id != source_run_id
        or record.scope != "project" or record.scope_id != str(record.project_id)):
        raise ValueError("评测反馈的项目作用域或源运行身份不一致")
    identity = evaluation_input_identity(db, evaluation_run=evaluation_run, source_run_id=source_run_id)
    artifact = ((evaluation_run.run_context or {}).get("artifacts") or {}).get("test_evaluation")
    if (not isinstance(artifact, dict) or artifact.get("source_run_id") != source_run_id
        or artifact.get("evaluation_run_id") != evaluation_run_id
        or record.id not in (artifact.get("feedback_record_ids") or [])):
        raise ValueError("反馈关联的真实评测产物不可用")
    evaluation = artifact.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("反馈关联的真实评测差异不可用")
    candidates = difference_candidate_contents(identity, evaluation)
    expected = next((item for item in candidates if input_payload_hash(item) == record.revision), None)
    if expected is None or any(content.get(key) != value for key, value in expected.items() if key != "text"):
        raise ValueError("评测反馈与原始三份输入或被评差异版本不一致")


__all__ = [
    "EVALUATION_DIFFERENCE_KIND", "DIFFERENCE_CATEGORIES", "evaluation_source_inputs", "evaluation_input_identity",
    "difference_candidate_contents", "stage_evaluation_feedback", "validate_evaluation_feedback_record",
]
