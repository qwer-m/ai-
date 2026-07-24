from __future__ import annotations

from copy import deepcopy
from typing import Any

from modules.test_generation_components.postprocess.execution_plan_case_state import (
    main_chain_precondition_conflict_reason,
    typed_state_contract_conflicts,
)
from modules.test_generation_components.postprocess.streaming_execution_plan_metadata import (
    _structured_candidate_contract_conflicts,
    apply_execution_plan_metadata,
    evaluate_required_stage_candidate_coverage,
    retain_required_stage_assignment,
)
from modules.test_generation_components.postprocess.streaming_execution_plan_metadata_helpers import (
    annotate_execution_plan_cases,
)


def _verified_state(
    entity: str,
    state: str,
    *,
    source: str,
    scope: str,
    temporal: str,
) -> dict[str, Any]:
    return {
        "entity": entity,
        "state": state,
        "source": source,
        "scope": scope,
        "polarity": "positive",
        "temporal": temporal,
        "confidence": 0.9,
        "evidence": [f"{entity}:{state}"],
        "evidence_verified": True,
    }


def _strict_primary_blueprint(
    blueprint: dict[str, Any],
    *,
    preserve_missing_declarations: bool = False,
) -> dict[str, Any]:
    """把测试蓝图补成当前需求编译器实际下发的结构化主工作流契约。"""
    normalized = deepcopy(blueprint)
    workflow_id = str(normalized.get("workflow_id") or normalized.get("id") or "workflow")
    normalized["primary"] = True
    steps = [dict(item) for item in (normalized.get("steps") or []) if isinstance(item, dict)]
    if not preserve_missing_declarations:
        normalized.setdefault("initial_state", str((steps[0] if steps else {}).get("state_in") or "initial"))
        normalized.setdefault(
            "required_stage_ids",
            [str(step.get("id") or "") for step in steps if str(step.get("id") or "")],
        )
        normalized.setdefault(
            "terminal_states",
            [str((steps[-1] if steps else {}).get("state_out") or "completed")],
        )
    required_ids = {
        str(item or "") for item in (normalized.get("required_stage_ids") or []) if str(item or "")
    }
    terminal_states = {
        str(item or "") for item in (normalized.get("terminal_states") or []) if str(item or "")
    }
    normalized_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(steps, start=1):
        step = dict(raw_step)
        stage_id = str(step.get("id") or f"step_{index:03d}")
        module_key = f"{workflow_id}_{stage_id}_module"
        step.setdefault("path_type", "positive")
        step.setdefault("required", stage_id in required_ids if required_ids else True)
        step.setdefault("terminal", str(step.get("state_out") or "") in terminal_states)
        step.setdefault("critical", False)
        step.setdefault("blocking", False)
        step.setdefault("destructive", False)
        step.setdefault("can_advance_main_flow", True)
        step.setdefault("module_candidates", [{"module_key": module_key}])
        step.setdefault("interaction_ids", [])
        step.setdefault("required_states", [])
        step.setdefault("produced_states", [])
        normalized_steps.append(step)
    normalized["steps"] = normalized_steps
    return normalized


def _case_for_workflow_stage(
    case: dict[str, Any],
    *,
    blueprint: dict[str, Any],
    stage_id: str,
    confidence: float = 0.95,
    precondition_states: list[dict[str, Any]] | None = None,
    produced_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造语义编译与证据校验后，执行计划层真正接收到的用例。"""
    normalized = dict(case)
    workflow_id = str(blueprint.get("workflow_id") or blueprint.get("id") or "")
    step = next(
        dict(item)
        for item in (blueprint.get("steps") or [])
        if isinstance(item, dict) and str(item.get("id") or "") == stage_id
    )
    evidence = str(
        normalized.get("description")
        or next(iter(normalized.get("steps") or []), "")
        or stage_id
    )
    module_candidates = [
        {
            "module_key": str(item.get("module_key") or ""),
            "module_name": str(item.get("module_name") or item.get("module_key") or ""),
            "role": "primary",
            "confidence": confidence,
            "evidence": [evidence],
            "evidence_verified": True,
        }
        for item in (step.get("module_candidates") or [])
        if isinstance(item, dict) and str(item.get("module_key") or "")
    ]

    def _verified_states(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                **dict(item),
                "confidence": float(item.get("confidence") or confidence),
                "evidence": list(item.get("evidence") or [evidence]),
                "evidence_verified": True,
            }
            for item in values
            if isinstance(item, dict)
        ]

    normalized["_semantic"] = {
        "version": "case-semantic-v1",
        "module_candidates": module_candidates,
        "interaction_ids": list(step.get("interaction_ids") or []),
        "workflow_stage_candidates": [
            {
                "workflow_id": workflow_id,
                "stage_id": stage_id,
                "stage_kind": str(step.get("stage_kind") or ""),
                "confidence": confidence,
                "evidence": [evidence],
                "evidence_verified": True,
            }
        ],
        "precondition_states": _verified_states(
            list(step.get("required_states") or [])
            if precondition_states is None
            else precondition_states
        ),
        "produced_states": _verified_states(
            list(step.get("produced_states") or [])
            if produced_states is None
            else produced_states
        ),
    }
    return normalized


def test_typed_state_contract_rejects_stage_only_claim_without_case_states() -> None:
    step_meta = {
        "required_states": [
            {
                "entity": "order",
                "state": "created",
                "source": "previous_stage",
                "scope": "checkout_flow",
                "temporal": "before_action",
            }
        ],
        "produced_states": [
            {
                "entity": "order",
                "state": "paid",
                "source": "current_stage",
                "scope": "checkout_flow",
                "temporal": "after_action",
            }
        ],
    }

    conflicts = typed_state_contract_conflicts({"_semantic": {}}, step_meta=step_meta)

    assert {item["reason"] for item in conflicts} == {
        "case_precondition_states_missing_workflow_contract_state",
        "case_produced_states_missing_workflow_contract_state",
    }


def test_typed_state_contract_requires_source_scope_and_temporal_alignment() -> None:
    step_meta = {
        "required_states": [
            {
                "entity": "order",
                "state": "created",
                "source": "previous_stage",
                "scope": "checkout_flow",
                "temporal": "before_action",
            }
        ]
    }
    case = {
        "_semantic": {
            "precondition_states": [
                _verified_state(
                    "order",
                    "created",
                    source="external_fixture",
                    scope="other_flow",
                    temporal="after_action",
                )
            ]
        }
    }

    reasons = {item["reason"] for item in typed_state_contract_conflicts(case, step_meta=step_meta)}

    assert "case_precondition_states_missing_workflow_contract_state" in reasons
    assert "case_precondition_states_not_in_workflow_contract" not in reasons


def test_typed_state_contract_allows_additional_verified_case_state() -> None:
    declared = _verified_state(
        "record",
        "ready",
        source="previous_stage",
        scope="workflow",
        temporal="after_previous_stage",
    )
    case = {
        "_semantic": {
            "precondition_states": [
                declared,
                _verified_state(
                    "permission",
                    "granted",
                    source="external_fixture",
                    scope="case",
                    temporal="before_case",
                ),
            ]
        }
    }

    assert typed_state_contract_conflicts(
        case,
        step_meta={"required_states": [declared]},
    ) == []


def test_typed_state_temporal_compatibility_is_directional() -> None:
    expected_during = _verified_state(
        "record",
        "visible",
        source="current_stage",
        scope="workflow",
        temporal="during_case",
    )
    actual_after = _verified_state(
        "record",
        "visible",
        source="current_stage",
        scope="workflow",
        temporal="after_case",
    )
    assert typed_state_contract_conflicts(
        {"_semantic": {"produced_states": [actual_after]}},
        step_meta={"produced_states": [expected_during]},
    ) == []

    reverse = typed_state_contract_conflicts(
        {"_semantic": {"produced_states": [expected_during]}},
        step_meta={"produced_states": [actual_after]},
    )
    assert [item["reason"] for item in reverse] == [
        "case_produced_states_missing_workflow_contract_state"
    ]


def test_stage_interaction_contract_must_be_covered_by_case_semantics() -> None:
    step_meta = {
        "stage_kind": "downstream_visibility",
        "interaction_ids": ["publish_notice"],
    }
    candidate = {"stage_kind": "downstream_visibility"}

    missing = _structured_candidate_contract_conflicts(
        {"_semantic": {"interaction_ids": []}},
        step_meta=step_meta,
        candidate=candidate,
    )
    aligned = _structured_candidate_contract_conflicts(
        {"_semantic": {"interaction_ids": ["publish_notice"]}},
        step_meta=step_meta,
        candidate=candidate,
    )

    assert "workflow_stage_interaction_contract_missing" in missing
    assert aligned == []


def test_blocking_critical_entry_still_advances_positive_main_chain() -> None:
    workflow_id = "blocking_entry_flow"
    blueprint = {
        "id": workflow_id,
        "primary": True,
        "initial_state": "initial",
        "required_stage_ids": ["open", "submit"],
        "terminal_states": ["submitted"],
        "steps": [
            {
                "id": "open",
                "label": "Open form",
                "action": "Open form",
                "state_in": "initial",
                "state_out": "opened",
                "stage_kind": "entry",
                "actor": "student",
                "path_type": "positive",
                "required": True,
                "terminal": False,
                "critical": True,
                "blocking": True,
                "destructive": False,
                "can_advance_main_flow": True,
                "module_candidates": [{"module_key": "forms"}],
                "interaction_ids": [],
                "required_states": [],
                "produced_states": [],
            },
            {
                "id": "submit",
                "label": "Submit form",
                "action": "Submit form",
                "state_in": "opened",
                "state_out": "submitted",
                "stage_kind": "commit",
                "actor": "student",
                "path_type": "positive",
                "required": True,
                "terminal": True,
                "critical": False,
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "module_candidates": [{"module_key": "forms"}],
                "interaction_ids": [],
                "required_states": [],
                "produced_states": [],
            },
        ],
    }
    cases = []
    for stage_id, stage_kind, description in (
        ("open", "entry", "Open form"),
        ("submit", "commit", "Submit form"),
    ):
        cases.append(
            {
                "id": f"{stage_id}-case",
                "test_module": "forms",
                "description": description,
                "steps": [description],
                "expected_result": f"{stage_id} succeeds",
                "priority": "P1",
                "_semantic": {
                    "module_candidates": [
                        {
                            "module_key": "forms",
                            "confidence": 0.9,
                            "evidence": [description],
                            "evidence_verified": True,
                        }
                    ],
                    "interaction_ids": [],
                    "workflow_stage_candidates": [
                        {
                            "workflow_id": workflow_id,
                            "stage_id": stage_id,
                            "stage_kind": stage_kind,
                            "confidence": 0.9,
                            "evidence": [description],
                            "evidence_verified": True,
                        }
                    ],
                    "precondition_states": [],
                    "produced_states": [],
                },
            }
        )

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=[blueprint])
    main_cases = [item for item in annotated if item.get("execution_group") == "main_smoke"]

    assert summary["linear_executable"] is True, summary
    assert summary["publishable_main_chain"] is True
    assert main_cases[0]["blocking"] is True
    assert main_cases[0]["can_advance_main_flow"] is True


def test_annotation_marks_only_the_physically_selected_candidate() -> None:
    first = {
        "id": "TC-A",
        "test_module": "forms",
        "description": "same case",
        "preconditions": ["prepared"],
        "steps": ["open form"],
        "test_input": "form",
        "expected_result": "form opens",
        "priority": "P1",
    }
    second = {**first, "id": "TC-B"}
    annotated = annotate_execution_plan_cases(
        [first, second],
        selected_by_stage=[("open", "Open form", first)],
        workflow_stage_meta_by_key={
            "open": {
                "workflow_id": "form_flow",
                "actor": "student",
                "stage_kind": "entry",
                "state_in": "initial",
                "state_out": "opened",
                "path_type": "positive",
                "critical": False,
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
            }
        },
        workflow_blueprints=[{"id": "form_flow"}],
    )

    assert annotated[0]["execution_group"] == "main_smoke"
    assert annotated[1]["execution_group"] != "main_smoke"


def _essay_review_workflow_blueprints() -> list[dict[str, Any]]:
    return [
        _strict_primary_blueprint({
            "id": "essay_review_publish",
            "source": "current_requirement_blueprint",
            "initial_state": "prepared",
            "required_stage_ids": ["open_entry", "submit_result", "student_visible"],
            "terminal_states": ["student_result_visible"],
            "steps": [
                {
                    "id": "open_entry",
                    "label": "open correction entry",
                    "action": "open correction entry",
                    "actor": "supervisor",
                    "state_in": "prepared",
                    "state_out": "entry_opened",
                    "stage_kind": "entry",
                    "keywords": ["open correction entry"],
                },
                {
                    "id": "submit_result",
                    "label": "submit correction result",
                    "action": "submit correction result",
                    "actor": "supervisor",
                    "state_in": "entry_opened",
                    "state_out": "correction_published",
                    "stage_kind": "commit",
                    "keywords": ["submit correction result"],
                },
                {
                    "id": "student_visible",
                    "label": "latest correction result visible",
                    "action": "latest correction result visible",
                    "actor": "student",
                    "state_in": "correction_published",
                    "state_out": "student_result_visible",
                    "stage_kind": "downstream_visibility",
                    "keywords": ["latest correction result visible"],
                },
            ],
        })
    ]


def _essay_review_cases() -> list[dict[str, Any]]:
    cases = [
        {
            "id": "raw-1",
            "test_module": "作文批改",
            "description": "Supervisor opens the correction entry for a submitted essay",
            "preconditions": ["supervisor account has a submitted essay"],
            "steps": ["open correction entry"],
            "test_input": "submitted essay",
            "expected_result": "correction entry is ready",
            "priority": "P1",
            "role": "supervisor",
        },
        {
            "id": "raw-2",
            "test_module": "作文批改",
            "description": "Supervisor submit correction result after review",
            "preconditions": ["correction entry is ready"],
            "steps": ["submit correction result"],
            "test_input": "real rubric feedback",
            "expected_result": "submit success and correction result saved",
            "priority": "P1",
            "role": "supervisor",
        },
        {
            "id": "raw-3",
            "test_module": "作文批改",
            "description": "Student sees latest correction result visible in the essay detail",
            "preconditions": ["correction result has been submitted"],
            "steps": ["open essay detail", "verify latest correction result visible"],
            "test_input": "student essay",
            "expected_result": "latest correction result visible and synced to student",
            "priority": "P1",
            "role": "student",
        },
        {
            "id": "raw-4",
            "test_module": "作文批改",
            "description": "Student checks result list tooltip display text",
            "preconditions": ["student has correction result"],
            "steps": ["open result list", "hover tooltip"],
            "test_input": "student essay",
            "expected_result": "tooltip display text is readable",
            "priority": "P0",
            "role": "student",
        },
    ]
    blueprint = _essay_review_workflow_blueprints()[0]
    stage_by_case_id = {
        "raw-1": "open_entry",
        "raw-2": "submit_result",
        "raw-3": "student_visible",
    }
    return [
        _case_for_workflow_stage(
            item,
            blueprint=blueprint,
            stage_id=stage_by_case_id[str(item.get("id") or "")],
        )
        if str(item.get("id") or "") in stage_by_case_id
        else item
        for item in cases
    ]


def test_apply_execution_plan_metadata_materializes_chain_annotations() -> None:
    annotated, summary = apply_execution_plan_metadata(
        _essay_review_cases(),
        workflow_blueprints=_essay_review_workflow_blueprints(),
    )

    main_cases = [item for item in annotated if item.get("execution_group") == "main_smoke"]
    display_cases = [item for item in annotated if item.get("execution_group") == "display"]

    assert summary["workflow_blueprint_source"] == "current_requirement_blueprint"
    assert summary["linear_executable"] is True
    assert summary["main_chain_case_count"] == 3
    assert summary["main_chain_stage_order"] == ["open_entry", "submit_result", "student_visible"]
    assert summary["state_conflict_count"] == 0
    assert summary["semantic_conflict_count"] == 0
    assert [item["id"] for item in main_cases] == ["TC-001", "TC-002", "TC-003"]
    assert [item.get("depends_on") for item in main_cases] == [[], ["TC-001"], ["TC-002"]]
    assert [item.get("main_chain_stage_kind") for item in main_cases] == [
        "entry",
        "commit",
        "downstream_visibility",
    ]
    assert [(item.get("source_state"), item.get("target_state")) for item in main_cases] == [
        ("prepared", "entry_opened"),
        ("entry_opened", "correction_published"),
        ("correction_published", "student_result_visible"),
    ]
    assert [item.get("role") for item in main_cases] == ["supervisor", "supervisor", "student"]
    assert [item.get("priority") for item in main_cases] == ["P1", "P1", "P1"]

    assert len(display_cases) == 1
    assert display_cases[0]["id"] == "TC-004"
    assert display_cases[0]["priority"] == "P1"
    assert display_cases[0]["priority_decision_source"] == "execution_plan_non_main_p0_demoted"


def test_apply_execution_plan_metadata_uses_verified_stage_confidence_for_global_selection() -> None:
    workflow_blueprints = _essay_review_workflow_blueprints()
    workflow_blueprints[0]["steps"][2]["keywords"] = ["latest correction result"]
    cases = _essay_review_cases() + [
        {
            "id": "raw-weak-detail",
            "test_module": "Essay detail",
            "description": "Student opens latest correction result detail",
            "preconditions": ["student has a correction result"],
            "steps": ["open latest correction result detail"],
            "test_input": "student essay",
            "expected_result": "correction result detail page opens",
            "priority": "P0",
            "role": "student",
        }
    ]
    cases[-1] = _case_for_workflow_stage(
        cases[-1],
        blueprint=workflow_blueprints[0],
        stage_id="student_visible",
        confidence=0.35,
    )

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)

    main_descriptions = [
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    ]
    assert "Student sees latest correction result visible in the essay detail" in main_descriptions
    assert "Student opens latest correction result detail" not in main_descriptions
    selected_by_stage = {
        item.get("stage_key"): item.get("case_id")
        for item in summary["global_stage_assignment"]["selected"]
    }
    assert selected_by_stage["student_visible"] == "raw-3"


def test_apply_execution_plan_metadata_rejects_display_case_with_mismatched_blueprint_object() -> None:
    workflow_blueprints = [
        {
            "id": "forum_post_reply_flow",
            "source": "current_requirement_blueprint",
            "initial_state": "initial",
            "required_stage_ids": [
                "entry",
                "configure",
                "edit",
                "preview",
                "commit",
                "downstream_visibility",
            ],
            "terminal_states": ["message_viewed"],
            "steps": [
                {
                    "id": "entry",
                    "label": "进入论坛首页",
                    "action": "进入论坛首页",
                    "module": "论坛首页",
                    "actor": "student",
                    "state_in": "initial",
                    "state_out": "forum_home",
                    "stage_kind": "entry",
                    "match_keywords": ["论坛首页"],
                },
                {
                    "id": "configure",
                    "label": "选择分区与版块",
                    "action": "选择内容分区与版块",
                    "module": "论坛发帖与互动",
                    "actor": "student",
                    "state_in": "forum_home",
                    "state_out": "tab_selected",
                    "stage_kind": "configure",
                    "match_keywords": ["选择分区", "版块"],
                },
                {
                    "id": "edit",
                    "label": "编辑帖子正文",
                    "action": "编辑帖子标题正文和图片",
                    "module": "发帖页",
                    "actor": "student",
                    "state_in": "tab_selected",
                    "state_out": "editing_post",
                    "stage_kind": "edit",
                    "match_keywords": ["编辑帖子"],
                },
                {
                    "id": "preview",
                    "label": "查看帖子详情预览",
                    "action": "查看帖子详情预览内容",
                    "module": "帖子详情",
                    "actor": "student",
                    "state_in": "editing_post",
                    "state_out": "post_detail",
                    "stage_kind": "preview",
                    "match_keywords": ["详情"],
                },
                {
                    "id": "commit",
                    "label": "提交回帖回复",
                    "action": "提交回帖回复",
                    "module": "帖子详情-回复",
                    "actor": "student",
                    "state_in": "post_detail",
                    "state_out": "reply_submitted",
                    "stage_kind": "commit",
                    "match_keywords": ["提交回复", "回帖"],
                },
                {
                    "id": "downstream_visibility",
                    "label": "回复消息展示",
                    "action": "查看回复消息展示",
                    "module": "消息-回复消息",
                    "actor": "student",
                    "state_in": "reply_submitted",
                    "state_out": "message_viewed",
                    "stage_kind": "downstream_visibility",
                    "match_keywords": ["回复消息"],
                },
            ],
        }
    ]
    workflow_blueprints = [_strict_primary_blueprint(workflow_blueprints[0])]
    cases = [
        {
            "id": "raw-display-entry",
            "test_module": "作文区",
            "description": "作文区精选TAB展示-仅展示后台标记精选标签的作品并按热门排序",
            "steps": ["进入作文区论坛首页", "点击精选TAB", "检查列表内容与排序"],
            "expected_result": "精选作品按热门排序展示",
            "priority": "P0",
        },
        {
            "id": "raw-entry",
            "test_module": "论坛首页-内容列表",
            "description": "进入论坛首页内容列表",
            "steps": ["进入论坛首页", "查看内容列表加载完成"],
            "expected_result": "论坛首页内容列表可操作",
            "priority": "P0",
        },
        {
            "id": "raw-configure",
            "test_module": "论坛发帖与互动",
            "description": "选择分区与版块",
            "steps": ["选择内容分区", "选择发帖版块"],
            "expected_result": "分区与版块选择成功",
            "priority": "P0",
        },
        {
            "id": "raw-edit",
            "test_module": "发帖页",
            "description": "编辑帖子标题正文和图片",
            "steps": ["输入标题", "填写帖子正文", "上传图片"],
            "expected_result": "帖子内容进入可预览状态",
            "priority": "P0",
        },
        {
            "id": "raw-display-preview",
            "test_module": "作文区",
            "description": "作文详情页-展示图片时点击缩略图放大查看及左右滑动",
            "steps": ["进入勾选展示图片的作文详情页", "点击缩略图放大查看"],
            "expected_result": "图片可放大并左右滑动展示",
            "priority": "P0",
        },
        {
            "id": "raw-preview",
            "test_module": "帖子详情",
            "description": "帖子详情页预览发帖内容",
            "steps": ["进入帖子详情页", "检查标题、正文和图片内容"],
            "expected_result": "帖子详情内容与编辑内容一致",
            "priority": "P0",
        },
        {
            "id": "raw-commit",
            "test_module": "帖子详情-回复",
            "description": "提交回帖回复",
            "steps": ["点击回复", "输入评论内容", "提交回复"],
            "expected_result": "回复提交成功",
            "priority": "P0",
        },
        {
            "id": "raw-downstream",
            "test_module": "消息-回复消息",
            "description": "二级评论消息展示：回复评论的消息也需要在消息列表中展示",
            "steps": ["进入消息页面的回复TAB", "查看回复消息列表"],
            "expected_result": "新提交的回复消息在回复消息列表中展示",
            "priority": "P0",
        },
    ]
    stage_by_case_id = {
        "raw-entry": "entry",
        "raw-configure": "configure",
        "raw-edit": "edit",
        "raw-preview": "preview",
        "raw-commit": "commit",
        "raw-downstream": "downstream_visibility",
    }
    cases = [
        _case_for_workflow_stage(
            item,
            blueprint=workflow_blueprints[0],
            stage_id=stage_by_case_id[str(item.get("id") or "")],
        )
        if str(item.get("id") or "") in stage_by_case_id
        else item
        for item in cases
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)

    main_descriptions = [
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    ]
    assert summary["linear_executable"] is True
    assert summary["main_chain_case_count"] == 6
    assert not any("作文区精选TAB展示" in description for description in main_descriptions)
    assert not any("作文详情页-展示图片" in description for description in main_descriptions)
    assert any("进入论坛首页内容列表" in description for description in main_descriptions)
    assert any("帖子详情页预览发帖内容" in description for description in main_descriptions)

    excluded = summary.get("main_chain_excluded_candidates") or []
    excluded_reasons = {
        item.get("reason")
        for item in excluded
        if "作文区精选TAB展示" in str(item.get("description") or "")
        or "作文详情页-展示图片" in str(item.get("description") or "")
    }
    assert not excluded_reasons
    selected_case_ids = {
        item.get("case_id") for item in summary["global_stage_assignment"]["selected"]
    }
    assert not {"raw-display-entry", "raw-display-preview"} & selected_case_ids


def test_apply_execution_plan_metadata_prefers_semantic_stage_match_over_weak_p0_overlap() -> None:
    reply = "回复"
    message = "消息"
    like = "点赞"
    audit = "审核"
    backend = "后台"
    approve = "通过"

    workflow_blueprints = [
        {
            "id": "forum_opt",
            "source": "current_requirement_blueprint",
            "initial_state": "post_previewed",
            "required_stage_ids": ["commit", "downstream_visibility", "consume"],
            "terminal_states": ["post_reviewed"],
            "steps": [
                {
                    "id": "commit",
                    "label": f"提交{reply}评论",
                    "action": f"点击{reply}按钮提交评论",
                    "state_in": "post_previewed",
                    "state_out": "reply_committed",
                    "stage_kind": "commit",
                    "match_keywords": [f"提交{reply}", f"{reply}评论"],
                },
                {
                    "id": "downstream_visibility",
                    "label": f"{reply}{message}展示",
                    "action": f"进入{message}页查看{reply}与{like}",
                    "state_in": "reply_committed",
                    "state_out": "msg_viewed",
                    "stage_kind": "downstream_visibility",
                    "match_keywords": [reply, like, message],
                },
                {
                    "id": "consume",
                    "label": f"{backend}{audit}帖子与{reply}内容",
                    "action": f"{backend}{audit}帖子与{reply}内容",
                    "state_in": "msg_viewed",
                    "state_out": "post_reviewed",
                    "stage_kind": "consume",
                    "match_keywords": [f"{backend}{audit}", f"{audit}{backend}", approve],
                },
            ],
        }
    ]
    workflow_blueprints = [_strict_primary_blueprint(workflow_blueprints[0])]
    weak_message_description = f"置顶帖显示元素-不显示{reply}量/{like}量"
    strong_message_description = f"{reply}帖子后在{reply}{message}Tab中可见"
    weak_audit_description = f"{audit}{backend}-按发帖/{reply}内容模糊搜索"
    strong_audit_description = f"{audit}{backend}-帖子列表页{approve}审核操作"
    cases = [
        {
            "id": "weak-message-overlap",
            "test_module": "论坛首页",
            "description": weak_message_description,
            "steps": ["进入官方区", "查看置顶帖"],
            "expected_result": f"不显示浏览量/{reply}量/{like}量",
            "priority": "P0",
        },
        {
            "id": "strong-message",
            "test_module": f"跨模块{message}",
            "description": strong_message_description,
            "steps": [
                f"用户B{reply}用户A的帖子",
                f"用户A进入{message}Tab",
                f"切换至{reply}{message}子Tab",
            ],
            "expected_result": f"用户A的{reply}{message}Tab中显示用户B的{reply}内容",
            "priority": "P1",
        },
        {
            "id": "weak-audit-module-only",
            "test_module": f"{audit}{backend}",
            "description": weak_audit_description,
            "steps": [f"在{backend}搜索框输入关键词", "点击搜索"],
            "expected_result": "搜索结果支持模糊匹配",
            "priority": "P0",
        },
        {
            "id": "strong-audit",
            "test_module": f"{audit}{backend}",
            "description": strong_audit_description,
            "steps": [
                f"进入{audit}{backend}帖子列表页",
                f"点击【{approve}】按钮{audit}一条帖子",
            ],
            "expected_result": f"点击【{approve}】后帖子{audit}状态变为{approve}",
            "priority": "P1",
        },
        {
            "id": "commit",
            "test_module": "论坛发帖与互动",
            "description": f"提交{reply}评论",
            "steps": [f"点击{reply}按钮提交评论"],
            "expected_result": f"{reply}提交成功",
            "priority": "P0",
        },
    ]

    stage_by_case_id = {
        "weak-message-overlap": ("downstream_visibility", 0.35),
        "strong-message": ("downstream_visibility", 0.95),
        "weak-audit-module-only": ("consume", 0.35),
        "strong-audit": ("consume", 0.95),
        "commit": ("commit", 0.95),
    }
    cases = [
        _case_for_workflow_stage(
            item,
            blueprint=workflow_blueprints[0],
            stage_id=stage_by_case_id[str(item.get("id") or "")][0],
            confidence=stage_by_case_id[str(item.get("id") or "")][1],
        )
        for item in cases
    ]
    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)

    main_descriptions = {
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    }
    assert strong_message_description in main_descriptions
    assert strong_audit_description in main_descriptions
    assert weak_message_description not in main_descriptions
    assert weak_audit_description not in main_descriptions
    excluded = summary.get("main_chain_excluded_candidates") or []
    excluded_by_original_id = {
        item.get("case_id"): item.get("reason")
        for item in excluded
        if item.get("case_id") in {"weak-message-overlap", "weak-audit-module-only"}
    }
    assert excluded_by_original_id == {}
    selected_by_stage = {
        item.get("stage_key"): item.get("case_id")
        for item in summary["global_stage_assignment"]["selected"]
    }
    assert selected_by_stage["downstream_visibility"] == "strong-message"
    assert selected_by_stage["consume"] == "strong-audit"


def test_main_chain_globally_prefers_atomic_stage_cases_without_flat_precondition_words() -> None:
    workflow_blueprints = [
        _strict_primary_blueprint({
            "id": "forum_publish_flow",
            "source": "current_requirement_blueprint",
            "initial_state": "initial",
            "required_stage_ids": ["entry", "configure", "edit", "preview", "commit", "consume"],
            "terminal_states": ["post_visible"],
            "steps": [
                {
                    "id": "entry",
                    "label": "Open forum home",
                    "action": "Open forum home through entry button",
                    "state_in": "initial",
                    "state_out": "forum_home",
                    "stage_kind": "entry",
                    "keywords": ["forum home"],
                },
                {
                    "id": "configure",
                    "label": "Select forum zone",
                    "action": "Select forum zone",
                    "state_in": "forum_home",
                    "state_out": "zone_selected",
                    "stage_kind": "configure",
                    "keywords": ["select forum zone"],
                },
                {
                    "id": "edit",
                    "label": "Edit post body",
                    "action": "Edit post title and body",
                    "state_in": "zone_selected",
                    "state_out": "post_editing",
                    "stage_kind": "edit",
                    "keywords": ["edit post"],
                },
                {
                    "id": "preview",
                    "label": "Preview edited post",
                    "action": "Preview edited post content",
                    "state_in": "post_editing",
                    "state_out": "post_ready",
                    "stage_kind": "preview",
                    "keywords": ["preview edited post"],
                },
                {
                    "id": "commit",
                    "label": "Submit post",
                    "action": "Submit post",
                    "state_in": "post_ready",
                    "state_out": "post_submitted",
                    "stage_kind": "commit",
                    "keywords": ["submit post"],
                },
                {
                    "id": "consume",
                    "label": "Open submitted post detail",
                    "action": "Open submitted post detail",
                    "state_in": "post_submitted",
                    "state_out": "post_visible",
                    "stage_kind": "consume",
                    "keywords": ["submitted post detail"],
                },
            ],
        })
    ]
    cases = [
        {
            "id": "entry",
            "test_module": "Forum",
            "description": "Open forum home through entry button",
            "preconditions": ["User is logged in"],
            "steps": ["Click forum entry button"],
            "expected_result": "Forum home opens",
            "priority": "P1",
        },
        {
            "id": "configure",
            "test_module": "Forum",
            "description": "Select forum zone",
            "preconditions": ["Forum home is open"],
            "steps": ["Select official zone"],
            "expected_result": "Selected zone is active",
            "priority": "P1",
        },
        {
            "id": "wrong-edit-completed",
            "test_module": "Forum",
            "description": "Edit post title and body then submit post",
            "preconditions": ["A forum zone is selected"],
            "steps": ["Edit post", "Submit post"],
            "expected_result": "Post submitted successfully",
            "priority": "P0",
        },
        {
            "id": "edit",
            "test_module": "Forum",
            "description": "Edit post title and body",
            "preconditions": ["A forum zone is selected"],
            "steps": ["Edit post title", "Edit post body"],
            "expected_result": "Edited content is ready for preview",
            "priority": "P1",
        },
        {
            "id": "preview",
            "test_module": "Forum",
            "description": "Preview edited post content",
            "preconditions": ["Post content is being edited"],
            "steps": ["Open preview"],
            "expected_result": "Edited title and body are shown in preview",
            "priority": "P1",
        },
        {
            "id": "commit",
            "test_module": "Forum",
            "description": "Submit post",
            "preconditions": ["Post preview is ready"],
            "_semantic": {
                "workflow_stage_candidates": [
                    {
                        "workflow_id": "forum_publish_flow",
                        "stage_id": "commit",
                        "confidence": 0.95,
                    }
                ]
            },
            "steps": ["Click submit"],
            "expected_result": "Post submitted successfully",
            "priority": "P1",
        },
        {
            "id": "wrong-consume-message",
            "test_module": "Message",
            "description": "Open submitted post detail from approval message",
            "preconditions": ["User has an approved system message"],
            "_semantic": {
                "precondition_states": [
                    {
                        "entity": "approval_message",
                        "state": "approved",
                        "source": "external_fixture",
                        "scope": "message_module",
                        "polarity": "positive",
                    }
                ]
            },
            "steps": ["Click approval message"],
            "expected_result": "Submitted post detail opens",
            "priority": "P0",
        },
        {
            "id": "consume",
            "test_module": "Forum",
            "description": "Open submitted post detail",
            "preconditions": ["Post is submitted"],
            "_semantic": {
                "workflow_stage_candidates": [
                    {
                        "workflow_id": "forum_publish_flow",
                        "stage_id": "consume",
                        "confidence": 0.95,
                    }
                ]
            },
            "steps": ["Click submitted post card"],
            "expected_result": "Submitted post detail opens",
            "priority": "P1",
        },
    ]
    stage_by_case_id = {
        "entry": ("entry", 0.95),
        "configure": ("configure", 0.95),
        "wrong-edit-completed": ("edit", 0.35),
        "edit": ("edit", 0.95),
        "preview": ("preview", 0.95),
        "commit": ("commit", 0.95),
        "wrong-consume-message": ("consume", 0.35),
        "consume": ("consume", 0.95),
    }
    cases = [
        _case_for_workflow_stage(
            item,
            blueprint=workflow_blueprints[0],
            stage_id=stage_by_case_id[str(item.get("id") or "")][0],
            confidence=stage_by_case_id[str(item.get("id") or "")][1],
            precondition_states=[],
            produced_states=[],
        )
        for item in cases
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=workflow_blueprints)
    main_descriptions = [
        str(item.get("description") or "")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    ]
    assert summary["linear_executable"] is True
    assert "Edit post title and body" in main_descriptions
    assert "Edit post title and body then submit post" not in main_descriptions
    assert "Open submitted post detail" in main_descriptions
    assert "Open submitted post detail from approval message" not in main_descriptions
    excluded = {
        (item.get("case_id"), item.get("stage_key")): item.get("reason")
        for item in (summary.get("main_chain_excluded_candidates") or [])
    }
    assert excluded.get(("wrong-edit-completed", "edit")) != "case_goal_spans_commit_stage"
    assert excluded.get(("wrong-consume-message", "consume")) != "precondition_state_not_produced_by_previous_stage"
    assert summary["global_stage_assignment"]["algorithm"] == "maximum_weight_bipartite_min_cost_flow_v1"
    assert summary["publishable_main_chain"] is True


def test_plain_precondition_text_does_not_trigger_state_continuity_rejection() -> None:
    current_case = {"preconditions": ["流程已完成"]}

    assert main_chain_precondition_conflict_reason(
        {"expected_result": "完成学习"},
        current_case,
    ) == ""


def test_typed_previous_stage_state_requires_matching_entity_and_state() -> None:
    current_step_meta = {
        "required_states": [
            {
                "entity": "payment_callback",
                "state": "completed",
                "source": "previous_stage",
                "scope": "checkout_flow",
                "polarity": "positive",
            }
        ]
    }

    assert main_chain_precondition_conflict_reason(
        {},
        {},
        previous_step_meta={
            "produced_states": [
                {
                    "entity": "order",
                    "state": "created",
                    "source": "current_stage",
                    "scope": "checkout_flow",
                    "polarity": "positive",
                }
            ]
        },
        current_step_meta=current_step_meta,
    ) == "precondition_state_not_produced_by_previous_stage"
    assert main_chain_precondition_conflict_reason(
        {},
        {},
        previous_step_meta={
            "produced_states": [
                {
                    "entity": "payment_callback",
                    "state": "completed",
                    "source": "current_stage",
                    "scope": "checkout_flow",
                    "polarity": "positive",
                }
            ]
        },
        current_step_meta=current_step_meta,
    ) == ""


def test_external_fixture_and_unknown_state_sources_are_not_hard_previous_stage_constraints() -> None:
    previous_case = {"_semantic": {"produced_states": []}}
    for source in ("external_fixture", "unknown", ""):
        current_case = {
            "_semantic": {
                "precondition_states": [
                    {
                        "entity": "payment_callback",
                        "state": "completed",
                        "source": source,
                        "scope": "checkout_flow",
                        "polarity": "positive",
                    }
                ]
            }
        }
        assert main_chain_precondition_conflict_reason(previous_case, current_case) == ""


def test_state_conflict_keeps_best_assignment_diagnostic_without_publishing_partial_chain() -> None:
    blueprint = {
        "id": "typed_state_flow",
        "source": "current_requirement_blueprint",
        "initial_state": "initial",
        "required_stage_ids": ["create", "view"],
        "terminal_states": ["visible"],
        "steps": [
            {
                "id": "create",
                "label": "Create record",
                "action": "Create record",
                "state_in": "initial",
                "state_out": "created",
                "stage_kind": "commit",
                "keywords": ["create record"],
                "produced_states": [
                    _verified_state(
                        "record",
                        "created",
                        source="same_case_setup",
                        scope="workflow",
                        temporal="after_case",
                    )
                ],
            },
            {
                "id": "view",
                "label": "View record",
                "action": "View record",
                "state_in": "created",
                "state_out": "visible",
                "stage_kind": "consume",
                "keywords": ["view record"],
                "required_states": [
                    _verified_state(
                        "payment_callback",
                        "completed",
                        source="previous_stage",
                        scope="workflow",
                        temporal="after_previous_stage",
                    )
                ],
            },
        ],
    }
    blueprint = _strict_primary_blueprint(blueprint)
    cases = [
        {
            "id": "create-case",
            "test_module": "records",
            "description": "Create record",
            "steps": ["Create record"],
            "expected_result": "Record is created",
            "priority": "P1",
            "_semantic": {
                "produced_states": [
                    {
                        "entity": "record",
                        "state": "created",
                        "source": "current_stage",
                        "scope": "record_flow",
                        "polarity": "positive",
                    }
                ]
            },
        },
        {
            "id": "view-case",
            "test_module": "records",
            "description": "View record",
            "steps": ["View record"],
            "expected_result": "Record is visible",
            "priority": "P1",
            "_semantic": {
                "precondition_states": [
                    {
                        "entity": "payment_callback",
                        "state": "completed",
                        "source": "previous_stage",
                        "scope": "record_flow",
                        "polarity": "positive",
                    }
                ]
            },
        },
    ]
    cases = [
        _case_for_workflow_stage(
            item,
            blueprint=blueprint,
            stage_id="create" if item.get("id") == "create-case" else "view",
        )
        for item in cases
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=[blueprint])
    coverage = evaluate_required_stage_candidate_coverage(
        cases,
        workflow_blueprints=[blueprint],
    )

    assert len(summary["best_assignment"]) == 2
    assert summary["best_assignment_state_conflicts"][0]["reason"] == (
        "precondition_state_not_produced_by_previous_stage"
    )
    assert summary["publishable_main_chain"] is False
    assert summary["main_chain_incomplete_reason"] == "state_chain_conflict"
    assert not any(item.get("execution_group") == "main_smoke" for item in annotated)
    assert coverage["assignment_required_stage_coverage_complete"] is True
    assert coverage["required_stage_coverage_complete"] is False
    assert coverage["publishable_main_chain"] is False
    assert coverage["failure_reason"] == "state_chain_conflict"
    assert coverage["actionable_stage_ids"] == ["view"]


def test_optional_stage_gap_does_not_block_declared_required_chain() -> None:
    blueprint = {
        "id": "optional_stage_flow",
        "source": "current_requirement_blueprint",
        "initial_state": "initial",
        "required_stage_ids": ["open", "submit"],
        "terminal_states": ["submitted"],
        "steps": [
            {
                "id": "open",
                "label": "Open form",
                "action": "Open form",
                "state_in": "initial",
                "state_out": "opened",
                "stage_kind": "entry",
                "keywords": ["open form"],
            },
            {
                "id": "preview",
                "label": "Preview form",
                "action": "Preview form",
                "state_in": "opened",
                "state_out": "previewed",
                "stage_kind": "preview",
                "keywords": ["preview form"],
                "optional": True,
            },
            {
                "id": "submit",
                "label": "Submit form",
                "action": "Submit form",
                "state_in": "opened",
                "state_out": "submitted",
                "stage_kind": "commit",
                "keywords": ["submit form"],
            },
        ],
    }
    blueprint = _strict_primary_blueprint(blueprint)
    cases = [
        {
            "id": "open-case",
            "test_module": "forms",
            "description": "Open form",
            "steps": ["Open form"],
            "expected_result": "Form is opened",
            "priority": "P1",
        },
        {
            "id": "submit-case",
            "test_module": "forms",
            "description": "Submit form",
            "steps": ["Submit form"],
            "expected_result": "Form is submitted",
            "priority": "P1",
        },
        {
            "id": "preview-case",
            "test_module": "forms",
            "description": "Preview form",
            "steps": ["Preview form"],
            "expected_result": "Form preview is visible",
            "priority": "P2",
        },
    ]
    stage_by_case_id = {
        "open-case": "open",
        "submit-case": "submit",
        "preview-case": "preview",
    }
    cases = [
        _case_for_workflow_stage(
            item,
            blueprint=blueprint,
            stage_id=stage_by_case_id[str(item.get("id") or "")],
        )
        for item in cases
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=[blueprint])
    coverage = evaluate_required_stage_candidate_coverage(
        cases,
        workflow_blueprints=[blueprint],
    )

    assert summary["global_stage_assignment"]["required_gap_count"] == 0
    assert summary["global_stage_assignment"]["optional_gap_count"] == 0
    assert summary["publishable_main_chain"] is True
    assert [
        item.get("main_chain_stage")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    ] == ["open", "submit"]
    assert next(
        item for item in annotated if item.get("description") == "Preview form"
    ).get("execution_group") != "main_smoke"
    assert coverage["source_generation_allowed"] is True
    assert coverage["missing_required_stages"] == []
    assert coverage["optional_branch_state_conflicts"] == []


def test_global_assignment_resolves_shared_stage_candidates_in_full_execution_plan() -> None:
    for workflow_id, module_key in (
        ("checkout_flow", "checkout"),
        ("forum_flow", "discussion"),
        ("composition_flow", "writing"),
    ):
        blueprint = _strict_primary_blueprint(
            {
                "id": workflow_id,
                "workflow_id": workflow_id,
                "source": "current_requirement_blueprint",
                "initial_state": "initial",
                "required_stage_ids": ["prepare", "complete"],
                "terminal_states": ["completed"],
                "steps": [
                    {
                        "id": "prepare",
                        "action": "Prepare operation",
                        "state_in": "initial",
                        "state_out": "prepared",
                        "stage_kind": "configure",
                        "module_candidates": [{"module_key": module_key}],
                    },
                    {
                        "id": "complete",
                        "action": "Complete operation",
                        "state_in": "prepared",
                        "state_out": "completed",
                        "stage_kind": "commit",
                        "module_candidates": [{"module_key": module_key}],
                    },
                ],
            }
        )

        def _candidate(
            case_id: str,
            stage_confidences: list[tuple[str, float]],
        ) -> dict[str, Any]:
            return {
                "id": case_id,
                "test_module": module_key,
                "description": f"Execute {case_id}",
                "steps": [f"Execute {case_id}"],
                "test_input": case_id,
                "expected_result": f"{case_id} result is observable",
                "priority": "P1",
                "_semantic": {
                    "module_candidates": [
                        {
                            "module_key": module_key,
                            "role": "primary",
                            "confidence": 0.9,
                            "evidence_verified": True,
                        }
                    ],
                    "interaction_ids": [],
                    "workflow_stage_candidates": [
                        {
                            "workflow_id": workflow_id,
                            "stage_id": stage_id,
                            "stage_kind": str(
                                next(
                                    step
                                    for step in blueprint["steps"]
                                    if step["id"] == stage_id
                                )["stage_kind"]
                            ),
                            "confidence": confidence,
                            "evidence_verified": True,
                        }
                        for stage_id, confidence in stage_confidences
                    ],
                    "precondition_states": [],
                    "produced_states": [],
                },
            }

        shared = _candidate(
            "shared-candidate",
            [("prepare", 0.99), ("complete", 0.90)],
        )
        dedicated = _candidate("prepare-only", [("prepare", 0.75)])
        annotated, summary = apply_execution_plan_metadata(
            [shared, dedicated],
            workflow_blueprints=[blueprint],
        )

        selected = {
            str(item.get("main_chain_stage") or ""): str(
                item.get("description") or ""
            )
            for item in annotated
            if item.get("execution_group") == "main_smoke"
        }
        assert selected == {
            "prepare": "Execute prepare-only",
            "complete": "Execute shared-candidate",
        }
        assert summary["global_stage_assignment"]["required_gap_count"] == 0
        assert summary["publishable_main_chain"] is True


def test_declared_stage_kind_scoring_is_generic_for_configure_stage() -> None:
    blueprint = {
        "id": "settings_flow",
        "source": "current_requirement_blueprint",
        "initial_state": "initial",
        "required_stage_ids": ["open", "configure"],
        "terminal_states": ["configured"],
        "steps": [
            {
                "id": "open",
                "label": "Open settings entry",
                "action": "Open settings entry",
                "state_in": "initial",
                "state_out": "opened",
                "stage_kind": "entry",
                "keywords": ["open settings entry"],
            },
            {
                "id": "configure",
                "label": "Select delivery method",
                "action": "Select delivery method",
                "state_in": "opened",
                "state_out": "configured",
                "stage_kind": "configure",
                "keywords": ["delivery method"],
            },
        ],
    }
    blueprint = _strict_primary_blueprint(blueprint)
    cases = [
        {
            "id": "open-case",
            "test_module": "settings",
            "description": "Open settings entry",
            "steps": ["Open settings entry"],
            "expected_result": "Settings entry is open",
            "priority": "P1",
        },
        {
            "id": "weak-view-case",
            "test_module": "settings",
            "description": "View delivery method label",
            "steps": ["View delivery method label"],
            "expected_result": "Delivery method label is visible",
            "priority": "P0",
        },
        {
            "id": "configure-case",
            "test_module": "settings",
            "description": "Select delivery method",
            "steps": ["Select delivery method"],
            "expected_result": "Delivery method is configured",
            "priority": "P1",
        },
    ]
    stage_confidence_by_case_id = {
        "open-case": ("open", 0.95),
        "weak-view-case": ("configure", 0.35),
        "configure-case": ("configure", 0.95),
    }
    cases = [
        _case_for_workflow_stage(
            item,
            blueprint=blueprint,
            stage_id=stage_confidence_by_case_id[str(item.get("id") or "")][0],
            confidence=stage_confidence_by_case_id[str(item.get("id") or "")][1],
        )
        for item in cases
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=[blueprint])

    selected_by_stage = {
        item.get("main_chain_stage"): item.get("description")
        for item in annotated
        if item.get("execution_group") == "main_smoke"
    }
    assert summary["publishable_main_chain"] is True
    assert selected_by_stage["configure"] == "Select delivery method"


def test_incomplete_blueprint_keeps_best_assignment_but_fails_closed() -> None:
    incomplete_blueprint = {
        "id": "incomplete_flow",
        "source": "current_requirement_blueprint",
        "steps": [
            {
                "id": "open",
                "label": "Open form",
                "action": "Open form",
                "state_in": "initial",
                "state_out": "opened",
                "stage_kind": "entry",
                "keywords": ["open form"],
            },
            {
                "id": "submit",
                "label": "Submit form",
                "action": "Submit form",
                "state_in": "opened",
                "state_out": "submitted",
                "stage_kind": "commit",
                "keywords": ["submit form"],
            },
        ],
    }
    incomplete_blueprint = _strict_primary_blueprint(
        incomplete_blueprint,
        preserve_missing_declarations=True,
    )
    cases = [
        {
            "id": "open-case",
            "test_module": "forms",
            "description": "Open form",
            "steps": ["Open form"],
            "expected_result": "Form is opened",
            "priority": "P1",
        },
        {
            "id": "submit-case",
            "test_module": "forms",
            "description": "Submit form",
            "steps": ["Submit form"],
            "expected_result": "Form is submitted",
            "priority": "P1",
        },
    ]
    cases = [
        _case_for_workflow_stage(
            item,
            blueprint=incomplete_blueprint,
            stage_id="open" if item.get("id") == "open-case" else "submit",
        )
        for item in cases
    ]

    annotated, summary = apply_execution_plan_metadata(cases, workflow_blueprints=[incomplete_blueprint])

    assert len(summary["best_assignment"]) == 2
    assert summary["workflow_blueprint_contract_complete"] is False
    assert summary["publishable_main_chain"] is False
    assert summary["main_chain_incomplete_reason"] == "workflow_blueprint_incomplete"
    assert not any(item.get("execution_group") == "main_smoke" for item in annotated)


def test_declared_workflow_absence_publishes_nonempty_independent_suite() -> None:
    cases = [
        {
            "id": "independent-forum-filter",
            "test_module": "forum filter",
            "description": "Filter forum posts by selected category",
            "preconditions": ["Forum contains posts in multiple categories"],
            "steps": ["Select a category", "Verify the filtered post list"],
            "test_input": "category product-feedback",
            "expected_result": "Only posts in the selected category remain visible",
            "priority": "P1",
        }
    ]

    annotated, summary = apply_execution_plan_metadata(
        cases,
        workflow_absence_declared=True,
    )

    assert annotated
    assert not any(item.get("execution_group") == "main_smoke" for item in annotated)
    assert summary["workflow_absence_declared"] is True
    assert summary["independent_suite_executable"] is True
    assert summary["main_chain_incomplete_reason"] == "workflow_absence_declared"
    assert summary["workflow_blueprint_source"] == "none"


def test_declared_workflow_absence_does_not_make_empty_suite_executable() -> None:
    annotated, summary = apply_execution_plan_metadata(
        [],
        workflow_absence_declared=True,
    )

    assert annotated == []
    assert summary["workflow_absence_declared"] is True
    assert summary["independent_suite_executable"] is False
    assert summary["main_chain_incomplete_reason"] == "workflow_absence_declared"


def _required_stage_coverage_fixture() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    produced_state = {
        "entity": "post",
        "state": "visible",
        "source": "current_stage",
        "scope": "publish_flow",
        "polarity": "positive",
        "temporal": "after_action",
    }
    blueprint = _strict_primary_blueprint(
        {
            "id": "publish_flow",
            "workflow_id": "publish_flow",
            "source": "current_requirement_blueprint",
            "initial_state": "draft",
            "required_stage_ids": ["entry", "submit", "visible"],
            "terminal_states": ["visible"],
            "steps": [
                {
                    "id": "entry",
                    "action": "Open publish entry",
                    "state_in": "draft",
                    "state_out": "editing",
                    "stage_kind": "entry",
                },
                {
                    "id": "submit",
                    "action": "Submit post",
                    "state_in": "editing",
                    "state_out": "submitted",
                    "stage_kind": "commit",
                },
                {
                    "id": "visible",
                    "action": "View published post",
                    "state_in": "submitted",
                    "state_out": "visible",
                    "stage_kind": "downstream_visibility",
                    "produced_states": [produced_state],
                },
            ],
        }
    )
    cases = [
        _case_for_workflow_stage(
            {
                "id": f"case-{stage_id}",
                "test_module": "forum",
                "description": f"Execute {stage_id}",
                "steps": [f"Execute {stage_id}"],
                "expected_result": f"{stage_id} state is observable",
                "priority": "P1",
            },
            blueprint=blueprint,
            stage_id=stage_id,
        )
        for stage_id in ("entry", "submit", "visible")
    ]
    return blueprint, cases


def test_required_stage_candidate_coverage_uses_execution_plan_contract_conflicts() -> None:
    blueprint, cases = _required_stage_coverage_fixture()
    cases[-1]["_semantic"]["produced_states"] = []

    coverage = evaluate_required_stage_candidate_coverage(
        cases,
        workflow_blueprints=[blueprint],
    )

    assert coverage["covered_required_stage_ids"] == ["entry", "submit"]
    assert coverage["missing_required_stage_ids"] == ["visible"]
    assert coverage["required_stage_coverage_complete"] is False

    retained, diagnostics = retain_required_stage_assignment(
        cases,
        cases[:2],
        workflow_blueprints=[blueprint],
        target_max_count=3,
        require_complete_source=True,
    )
    assert [item["id"] for item in retained] == ["case-entry", "case-submit"]
    assert diagnostics["applied"] is False
    assert diagnostics["reason"] == "source_required_stage_assignment_incomplete"


def test_required_stage_coverage_does_not_generate_for_incomplete_blueprint() -> None:
    blueprint, cases = _required_stage_coverage_fixture()
    blueprint.pop("terminal_states")

    coverage = evaluate_required_stage_candidate_coverage(
        cases,
        workflow_blueprints=[blueprint],
    )

    assert coverage["assignment_required_stage_coverage_complete"] is True
    assert coverage["workflow_blueprint_contract_complete"] is False
    assert coverage["required_stage_coverage_complete"] is False
    assert coverage["source_generation_allowed"] is False
    assert coverage["actionable_stage_ids"] == []
    assert coverage["missing_required_stages"] == []
    assert coverage["failure_reason"] == "workflow_blueprint_incomplete"


def test_required_stage_coverage_does_not_generate_for_invalid_blueprint_closure() -> None:
    blueprint, cases = _required_stage_coverage_fixture()
    blueprint["terminal_states"] = ["unreachable_terminal"]
    blueprint["steps"][-1]["terminal"] = False

    coverage = evaluate_required_stage_candidate_coverage(
        cases,
        workflow_blueprints=[blueprint],
    )

    assert coverage["workflow_blueprint_contract_complete"] is True
    assert coverage["workflow_blueprint_closure"]["closure_satisfied"] is False
    assert coverage["required_stage_coverage_complete"] is False
    assert coverage["source_generation_allowed"] is False
    assert coverage["actionable_stage_ids"] == []
    assert coverage["failure_reason"] == "workflow_blueprint_closure_invalid"


def test_explicit_terminal_state_is_not_replaced_by_terminal_step_output() -> None:
    blueprint, cases = _required_stage_coverage_fixture()
    blueprint["terminal_states"] = ["unreachable_terminal"]

    coverage = evaluate_required_stage_candidate_coverage(
        cases,
        workflow_blueprints=[blueprint],
    )

    assert coverage["workflow_blueprint_closure"]["terminal_state_reachable"] is False
    assert "workflow_terminal_state_not_reachable" in coverage[
        "workflow_blueprint_closure"
    ]["failure_reasons"]
    assert coverage["source_generation_allowed"] is False
    assert coverage["failure_reason"] == "workflow_blueprint_closure_invalid"


def test_required_stage_review_retention_replaces_nonprotected_case_within_target() -> None:
    blueprint, stage_cases = _required_stage_coverage_fixture()
    independent_cases = [
        {
            "id": f"independent-{index}",
            "test_module": "forum",
            "description": f"Independent forum check {index}",
            "steps": [f"Run independent check {index}"],
            "expected_result": f"Independent result {index} is observable",
            "priority": "P2",
        }
        for index in (1, 2)
    ]
    full_pool = [*stage_cases, *independent_cases]
    llm_selection = [
        stage_cases[0],
        stage_cases[1],
        independent_cases[0],
        independent_cases[1],
    ]

    retained, diagnostics = retain_required_stage_assignment(
        full_pool,
        llm_selection,
        workflow_blueprints=[blueprint],
        target_max_count=4,
        require_complete_source=True,
    )
    final_coverage = evaluate_required_stage_candidate_coverage(
        retained,
        workflow_blueprints=[blueprint],
    )

    assert len(retained) == 4
    assert final_coverage["required_stage_coverage_complete"] is True
    assert any(item.get("id") == "case-visible" for item in retained)
    assert diagnostics["restored_candidate_keys"]
    assert diagnostics["replaced_candidate_keys"]
    assert diagnostics["within_target_max"] is True


def test_required_stage_review_retention_keeps_complete_selected_assignment() -> None:
    blueprint, stage_cases = _required_stage_coverage_fixture()
    source_pool = [
        *stage_cases,
        {
            "id": "independent",
            "test_module": "forum",
            "description": "Independent forum check",
            "steps": ["Run independent forum check"],
            "expected_result": "Independent result is observable",
            "priority": "P2",
        },
    ]

    retained, diagnostics = retain_required_stage_assignment(
        source_pool,
        stage_cases,
        workflow_blueprints=[blueprint],
        target_max_count=3,
        require_complete_source=True,
    )

    assert retained == stage_cases
    assert diagnostics["applied"] is False
    assert diagnostics["reason"] == (
        "selected_required_stage_assignment_already_complete"
    )
    assert diagnostics["final_coverage"]["publishable_main_chain"] is True


def test_required_stage_coverage_is_noop_without_declared_workflow() -> None:
    cases = [
        {
            "id": "TC-001",
            "test_module": "forum",
            "description": "View independent forum list",
            "steps": ["Open forum list"],
            "expected_result": "The forum list contains visible posts",
            "priority": "P1",
        }
    ]

    coverage = evaluate_required_stage_candidate_coverage(
        cases,
        workflow_blueprints=[],
    )
    retained, diagnostics = retain_required_stage_assignment(
        cases,
        [],
        workflow_blueprints=[],
        target_max_count=1,
    )

    assert coverage["active"] is False
    assert coverage["missing_required_stage_ids"] == []
    assert coverage["required_stage_coverage_complete"] is True
    assert retained == []
    assert diagnostics["applied"] is False
    assert diagnostics["reason"] == "source_required_stage_assignment_absent"
