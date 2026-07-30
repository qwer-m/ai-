from copy import deepcopy

from modules.test_generation_components.postprocess.json_normalizer import (
    normalize_json_structure,
)
from modules.test_generation_components.legacy.stream.batch_parallel_shards import (
    build_parallel_shard_instruction,
)


def _requirement_contract() -> dict:
    return {
        "evidence_facts": [
            {
                "fact_id": "FACT-TECHNIQUE-UNACQUIRED",
                "statement": "未获得的秘籍保持置灰并展示未获得提示",
            }
        ],
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "technique_collection",
                    "module_name": "技法收藏",
                }
            ],
            "module_interactions": [],
        },
        "workflow_blueprints": [
            {
                "workflow_id": "WF-TECHNIQUE-COLLECTION",
                "steps": [
                    {
                        "id": "STEP-OPEN-UNACQUIRED",
                        "stage_kind": "preview",
                        "module_candidates": [
                            {"module_key": "technique_collection"}
                        ],
                        "interaction_ids": [],
                        "required_states": [],
                        "produced_states": [],
                    }
                ],
            }
        ],
    }


def _real_structure_case() -> dict:
    """复用真实生成诊断中的字段形态，内容不绑定文件身份或页码。"""

    description = (
        "技法收藏-未获得的秘籍图标置灰，点击后打开未获得提示弹窗且技法图片置灰展示"
    )
    return {
        "id": "TC-042",
        "description": description,
        "test_module": "技法收藏",
        "preconditions": ["用户已登录", "存在尚未获得的秘籍"],
        "steps": ["点击未获得的秘籍图标", "观察弹窗和技法图片状态"],
        "test_input": "未获得的秘籍",
        "expected_result": "打开未获得提示弹窗，技法图片保持置灰",
        "priority": "P1",
        "_semantic": {
            "module_candidates": [
                {
                    "module_key": "technique_collection",
                    "module_name": "技法收藏",
                    "role": "primary",
                    "evidence": [description],
                    "confidence": 0.9,
                }
            ],
            "fact_ids": ["FACT-TECHNIQUE-UNACQUIRED"],
            "interaction_ids": [],
            "workflow_stage_candidates": [
                {
                    "workflow_id": "WF-TECHNIQUE-COLLECTION",
                    "stage_id": "STEP-OPEN-UNACQUIRED",
                    "stage_kind": "preview",
                    "evidence": [description],
                    "confidence": 0.9,
                }
            ],
            "precondition_states": [],
            "produced_states": [
                {
                    "entity": "未获得提示弹窗",
                    "state": "已打开且图片置灰",
                    "source": "current_stage",
                    "scope": "entity",
                    "polarity": "positive",
                    "temporal": "after_case",
                    # 该文本拼接了两个公开字段片段，并非任一字段的原文证据。
                    "evidence": ["点击后打开未获得提示弹窗；弹窗中技法图片置灰展示"],
                    "confidence": 0.9,
                }
            ],
        },
    }


def test_invalid_optional_state_is_pruned_when_authoritative_anchor_exists() -> None:
    semantic_rejections: list[dict] = []

    normalized = normalize_json_structure(
        [_real_structure_case()],
        require_case_semantic_contract=True,
        requirement_semantic_contract=_requirement_contract(),
        semantic_rejections=semantic_rejections,
    )

    assert len(normalized) == 1
    assert semantic_rejections == []
    semantic = normalized[0]["_semantic"]
    assert semantic["fact_ids"] == ["FACT-TECHNIQUE-UNACQUIRED"]
    assert semantic["produced_states"] == []
    assert {
        (item.get("item_type"), item.get("reason"), item.get("disposition"))
        for item in semantic["rejected_semantic_items"]
    } == {("produced_state", "evidence_unverified", "pruned_optional_item")}


def test_invalid_optional_state_without_fact_or_workflow_anchor_still_repairs() -> None:
    case = _real_structure_case()
    case["_semantic"]["fact_ids"] = []
    case["_semantic"]["workflow_stage_candidates"] = []
    semantic_rejections: list[dict] = []

    normalized = normalize_json_structure(
        [case],
        require_case_semantic_contract=True,
        requirement_semantic_contract=_requirement_contract(),
        semantic_rejections=semantic_rejections,
    )

    assert normalized == []
    assert semantic_rejections[0]["rejection_reasons"] == [
        "produced_state:evidence_unverified"
    ]


def test_missing_semantic_object_remains_a_blocking_repair_reason() -> None:
    case = deepcopy(_real_structure_case())
    case.pop("_semantic")
    semantic_rejections: list[dict] = []

    normalized = normalize_json_structure(
        [case],
        require_case_semantic_contract=True,
        requirement_semantic_contract=_requirement_contract(),
        semantic_rejections=semantic_rejections,
    )

    assert normalized == []
    assert semantic_rejections[0]["rejection_reasons"] == [
        "semantic_object_missing"
    ]


def test_shard_prompt_prefers_empty_optional_states_when_goal_is_already_anchored() -> None:
    instruction = build_parallel_shard_instruction(
        {
            "shard_id": "SHARD-GENERIC",
            "shard_index": 1,
            "total_shards": 1,
            "target_count": 1,
            "rule_ids": ["RULE-GENERIC"],
            "rule_texts": ["验证一个活动事实"],
            "facts": [
                {
                    "fact_id": "FACT-GENERIC",
                    "statement": "活动事实发生后结果可观察",
                }
            ],
        }
    )

    assert "Prefer precondition_states=[] and produced_states=[]" in instruction
    assert "never combine or paraphrase fragments as state evidence" in instruction
