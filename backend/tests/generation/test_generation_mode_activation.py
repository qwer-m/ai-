from __future__ import annotations

from modules.test_generation_components.control.generation_mode_activation import (
    build_generation_mode_control_state,
    infer_generation_coverage_profile,
)
from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import stream_postprocess_cases
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    normalize_final_case_priorities,
    strip_case_meta_fields,
)
from modules.test_generation_components.prompting.structured_context import build_structured_prompt_context


class _NoopClient:
    def generate_response(self, requirement: str, prompt: str, db=None, **kwargs):  # noqa: ANN001, ARG002
        return "[]"

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
        yield "[]"


class _SupplementClient(_NoopClient):
    def __init__(self, cases):
        self.cases = cases
        self.prompts: list[str] = []

    def generate_response(self, requirement: str, prompt: str, db=None, **kwargs):  # noqa: ANN001, ARG002
        self.prompts.append(prompt)
        return __import__("json").dumps(self.cases, ensure_ascii=False)


def _drain_with_return(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def test_full_functional_regression_profile_activates_from_expected_count() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="近期课程和排课功能调整",
        expected_count=100,
    )

    assert profile["coverage_mode"] == "full_functional_regression"
    assert profile["target_case_range"] == {"min": 80, "max": 120}
    assert len(profile["coverage_layers"]) >= 5


def test_standard_regression_profile_activates_from_document_intent() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="本次改版需要做标准回归，确保原有模块不受影响",
        expected_count=20,
    )

    assert profile["coverage_mode"] == "standard_regression"
    assert profile["target_case_range"] == {"min": 30, "max": 50}


def test_expanded_regression_profile_activates_from_mid_sized_expected_count() -> None:
    profile = infer_generation_coverage_profile(
        requirement_text="writing workflow regression",
        expected_count=70,
    )

    assert profile["coverage_mode"] == "expanded_regression"
    assert profile["case_density"] == "medium_high"
    assert profile["target_case_range"] == {"min": 60, "max": 80}


def test_generation_mode_control_reaches_structured_prompt_context() -> None:
    state = build_generation_mode_control_state(
        requirement_text="全功能测试：覆盖入口、核心流程、异常和跨模块回归",
        expected_count=100,
    )
    prompt_context = build_structured_prompt_context(
        requirement="全功能测试：覆盖入口、核心流程、异常和跨模块回归",
        feedback_control_state=state,
    )

    summary = dict(prompt_context.get("control_summary") or {})
    control_text = str(prompt_context.get("control_context") or "")
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    assert summary["generation_target_case_range"] == {"min": 80, "max": 120}
    assert "GENERATION COVERAGE MODE" in control_text
    assert "full_functional_regression" in control_text
    assert "not a quota" in control_text


def test_full_functional_mode_prevents_review_gate_compressing_dense_case_set() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for a management workflow",
        expected_count=100,
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate management workflow checkpoint {idx}",
            "test_module": f"module-{idx:03d}",
            "preconditions": ["User is logged in", f"Dataset {idx} exists"],
            "steps": [
                f"1. Open workflow area {idx}",
                f"2. Execute action for checkpoint {idx}",
                f"3. Refresh and re-open checkpoint {idx}",
            ],
            "test_input": f"checkpoint-{idx}",
            "expected_result": (
                f"Checkpoint {idx} shows saved state {idx} after refresh, "
                f"and module {idx} displays the updated record."
            ),
            "priority": "P1" if idx % 3 else "P2",
        }
        for idx in range(1, 41)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="full functional regression for a management workflow",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    assert isinstance(result, dict)
    assert len(result.get("cases") or []) >= 30
    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    assert summary["recommended_range"] == "80-120"
    debug = dict(result.get("feedback_control_debug") or {})
    assert debug["generation_coverage_mode"] == "full_functional_regression"


def test_expanded_regression_mode_keeps_broad_case_set_after_review_gate() -> None:
    state = build_generation_mode_control_state(
        requirement_text="expanded regression for a writing workflow",
        expected_count=70,
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate writing workflow scenario {idx}",
            "test_module": f"module-{idx:03d}",
            "preconditions": ["User is logged in", f"Writing asset {idx} exists"],
            "steps": [
                f"1. Open writing area {idx}",
                f"2. Execute business action for scenario {idx}",
                f"3. Verify state, message, and linked list update for scenario {idx}",
            ],
            "test_input": f"scenario-{idx}",
            "expected_result": (
                f"Scenario {idx} keeps the expected business state, "
                f"updates module {idx}, and does not expose stale requirement behavior."
            ),
            "priority": "P1" if idx % 4 else "P2",
        }
        for idx in range(1, 69)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="expanded regression for a writing workflow",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=70,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    assert isinstance(result, dict)
    assert len(result.get("cases") or []) >= 56
    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "expanded_regression"
    assert summary["recommended_range"] == "60-80"
    assert summary["target_final_count"] == 70
    assert summary["soft_min_count"] == 56
    assert summary["hard_min_count"] == 49
    assert summary["underfilled"] is False


def test_explicit_expected_count_marks_underfilled_when_final_is_over_pruned() -> None:
    state = build_generation_mode_control_state(
        requirement_text="expanded regression for a writing workflow",
        expected_count=70,
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Validate writing workflow scenario {idx}",
            "test_module": f"module-{idx % 10}",
            "preconditions": ["User is logged in", f"Writing asset {idx} exists"],
            "steps": [
                f"1. Open writing area {idx % 10}",
                f"2. Execute business action for scenario {idx}",
                f"3. Verify state, message, and linked list update for scenario {idx}",
            ],
            "test_input": f"scenario-{idx}",
            "expected_result": (
                f"Scenario {idx} keeps the expected business state, "
                f"updates module {idx % 10}, and does not expose stale requirement behavior."
            ),
            "priority": "P1",
        }
        for idx in range(1, 69)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="expanded regression for a writing workflow",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=70,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    debug = dict(result.get("convergence_debug") or {})
    assert summary["generation_coverage_mode"] == "expanded_regression"
    assert summary["target_final_count"] == 70
    assert summary["underfilled"] is True
    assert summary["status"] == "completed_underfilled"
    assert summary["underfill_reason"] in {
        "duplicate_pruned_under_target",
        "quality_rejected_under_target",
        "review_selector_over_pruned",
    }
    assert debug["valid_unique_candidate_count"] >= 63
    assert debug["target_satisfaction_ratio"] < 0.8


def test_full_mode_uses_mode_aware_final_duplicate_caps_for_generic_scenarios() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for dashboard and course workflow",
        expected_count=120,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Dashboard statistics show total submitted count",
            "test_module": "Dashboard",
            "preconditions": ["User is logged in"],
            "steps": ["Open Dashboard"],
            "test_input": "submitted records exist",
            "expected_result": "Dashboard total submitted count matches submitted records.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Dashboard statistics show failed review count",
            "test_module": "Dashboard",
            "preconditions": ["User is logged in"],
            "steps": ["Open Dashboard"],
            "test_input": "failed records exist",
            "expected_result": "Dashboard failed review count matches failed records.",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "Course workflow step navigation opens the first lesson",
            "test_module": "Course",
            "preconditions": ["User is logged in"],
            "steps": ["Open Course", "Click next step to first lesson"],
            "test_input": "first lesson",
            "expected_result": "First lesson page opens and shows lesson content.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Course workflow step navigation opens the second lesson",
            "test_module": "Course",
            "preconditions": ["User is logged in"],
            "steps": ["Open Course", "Click next step to second lesson"],
            "test_input": "second lesson",
            "expected_result": "Second lesson page opens and shows lesson content.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement=(
                "1. Dashboard: users view statistics.\n"
                "2. Course: users navigate lesson workflow."
            ),
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=120,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    assert isinstance(result, dict)
    final_cases = result.get("cases") or []
    final_descriptions = " ".join(str(item.get("description") or "") for item in final_cases)
    assert len(final_cases) >= 3
    assert "total submitted count" in final_descriptions or "failed review count" in final_descriptions
    assert "first lesson" in final_descriptions or "second lesson" in final_descriptions
    review_summary = dict(result.get("review_decision_summary") or {})
    assert review_summary["scenario_duplicate_pruned_count"] <= 1
    assert review_summary["flow_governance_applied"] is True


def test_full_mode_promotes_main_path_p0_anchor_when_model_omits_p0() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submit, review, and permission workflow",
        expected_count=100,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Upload file and submit task generates the final result",
            "test_module": "Submission Workflow",
            "preconditions": ["User is logged in", "A valid file exists"],
            "steps": ["Open submission page", "Upload file", "Submit task"],
            "test_input": "valid file",
            "expected_result": "The task is submitted and the generated result is displayed.",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Result page copy button shows success toast",
            "test_module": "Result Page",
            "preconditions": ["A generated result exists"],
            "steps": ["Open result page", "Click copy"],
            "test_input": "copy",
            "expected_result": "A success toast is shown.",
            "priority": "P2",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload files, submit tasks, and view generated results.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities = [str(item.get("priority") or "").upper() for item in (result.get("cases") or [])]
    assert "P0" in priorities


def test_full_mode_maintains_p0_anchor_floor_when_one_p0_exists() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submit, approval, permission, and community workflow",
        expected_count=100,
    )
    payload = state.to_dict()
    payload.setdefault("source_meta", {})["generation_coverage_profile"] = {
        "coverage_mode": "standard_regression",
        "target_case_range": {"min": 30, "max": 50},
    }
    seed_cases = [
        {
            "id": "TC-001",
            "description": "Existing P0 first lesson permission anchor",
            "test_module": "Permission",
            "preconditions": ["A normal user is logged in"],
            "steps": ["Open first lesson"],
            "test_input": "normal user",
            "expected_result": "The first lesson is enterable.",
            "priority": "P0",
        }
    ]
    generated_cases = [
        {
            "id": f"TC-{index + 2:03d}",
            "description": f"{family} main path case {index}",
            "test_module": f"{family.title()} Module {index}",
            "preconditions": ["User is logged in"],
            "steps": [f"Open {family}", f"Run {family} action {index}"],
            "test_input": family,
            "expected_result": f"The {family} flow completes and result {index} is displayed.",
            "priority": "P1",
        }
        for index, family in enumerate(
            [
                "upload submit",
                "generate result",
                "review approved",
                "member permission",
                "community comment like",
                "publish workflow",
                "approval passed",
                "all courses member",
                "upload image",
                "submit draft",
                "generated report",
                "locked paywall",
                "comment reply",
                "like cancel",
                "publish approved",
                "permission vip",
                "result detail",
                "upload retry",
                "submit success",
                "review pass notice",
                "member all courses",
                "community detail",
                "approval state",
                "generate final result",
                "upload attachment",
                "submit form",
                "publish article",
                "review approved list",
                "permission locked course",
                "comment audit",
                "like article",
                "all courses access",
                "upload camera",
                "submit review",
                "generated feedback",
                "approval notification",
                "member course access",
                "community work detail",
                "publish circle",
                "review pass reward",
                "permission first lesson",
                "upload album",
                "submit audit",
                "generate correction",
                "community comment entry",
            ]
        )
    ]
    cases = [*seed_cases, *generated_cases]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload content, submit it, receive approval, pass permission gates, and use community features.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=payload,
        )
    )

    priorities = [str(item.get("priority") or "").upper() for item in (result.get("cases") or [])]
    assert priorities.count("P0") >= 8
    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    persisted_cases = strip_case_meta_fields(
        normalize_final_case_priorities(
            result.get("cases") or [],
            requirement_text="Users upload content, submit it, receive approval, pass permission gates, and use community features.",
        )
    )
    persisted_priorities = [str(item.get("priority") or "").upper() for item in persisted_cases]
    assert persisted_priorities.count("P0") >= 8


def test_public_normalization_preserves_full_regression_main_path_floor_with_ui_words() -> None:
    anchor_cases = [
        {
            "id": "TC-001",
            "description": "批改反馈-完整四部分生成：校验综合点评、分句点评、提升思路、全文润色全部正确显示",
            "test_module": "作文批改-批改结果页",
            "expected_result": "四部分内容均正确生成，综合点评、分句点评、提升思路、全文润色完整展示",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "投稿页-提交投稿：校验提交后显示投稿成功弹窗，点击我知道了返回批改详情页，按钮状态变为审核中",
            "test_module": "作文批改-投稿页",
            "expected_result": "提交后弹出投稿成功弹窗；点击后返回批改结果页，投稿按钮文案变为审核中且不可点击",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "后台审核通过投稿作品",
            "test_module": "作文审核后台",
            "expected_result": "后台审核通过成功，作品状态变为已发布",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "普通用户第一课免费试学，其余课程锁住并点击跳转会员中心",
            "test_module": "课程列表页 - 权限",
            "expected_result": "第一课可免费进入学习；第二课及以后显示锁图标，点击后跳转至会员中心页面",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "会员用户进入同步作文课程列表页可看到所有课程均可学习",
            "test_module": "课程列表页 - 权限",
            "expected_result": "所有课程均显示为可学习状态，无锁图标或可点击进入",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "删除已发布作品后恢复未投稿",
            "test_module": "我的作文",
            "expected_result": "作品从作文圈列表移除，我的作文中该作品恢复为未投稿状态",
            "priority": "P1",
        },
        {
            "id": "TC-007",
            "description": "上传图片成功后点击去批改生成批改结果",
            "test_module": "作文批改",
            "expected_result": "上传成功后点击去批改，系统生成批改结果并进入结果页",
            "priority": "P1",
        },
        {
            "id": "TC-008",
            "description": "审核通过后作文圈可见作品详情",
            "test_module": "作文圈",
            "expected_result": "审核通过后作品在作文圈可见，打开作品详情展示正文",
            "priority": "P1",
        },
    ]
    filler_cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"展示型补充用例 {idx}",
            "test_module": "展示",
            "expected_result": "页面展示正确",
            "priority": "P2",
        }
        for idx in range(9, 90)
    ]

    normalized = strip_case_meta_fields(
        normalize_final_case_priorities(
            [*anchor_cases, *filler_cases],
            requirement_text="小学同步作文 full functional regression",
        )
    )

    assert sum(1 for item in normalized if str(item.get("priority") or "").upper() == "P0") >= 8


def test_public_normalization_does_not_promote_essay_cases_for_schedule_requirement() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "上传作文图片成功后点击去批改生成批改结果",
            "test_module": "作文批改",
            "expected_result": "上传成功后点击去批改，系统生成批改结果并进入结果页",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "提交投稿后进入审核中",
            "test_module": "作文投稿",
            "expected_result": "投稿提交成功，作品状态变为审核中",
            "priority": "P1",
        },
    ]

    normalized = strip_case_meta_fields(
        normalize_final_case_priorities(
            cases,
            requirement_text="近期课程+排课：本周课程、学习计划、课程时间冲突和顺延规则",
        )
    )

    assert all(str(item.get("priority") or "").upper() != "P0" for item in normalized)


def test_full_mode_does_not_promote_non_blocking_display_cases_to_p0() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submit, approval, permission, sorting, and sharing workflow",
        expected_count=100,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Category list keeps the promoted course on top",
            "test_module": "Course Category",
            "preconditions": ["User is logged in"],
            "steps": ["Open category list"],
            "test_input": "category",
            "expected_result": "The course is sorted at the top of the category list.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Share article to H5 page and verify card content",
            "test_module": "Community Share",
            "preconditions": ["Article exists"],
            "steps": ["Click share"],
            "test_input": "share",
            "expected_result": "The H5 share page displays title, subtitle, and open app button.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Upload image and submit task successfully generates correction result",
            "test_module": "Correction",
            "preconditions": ["User is logged in"],
            "steps": ["Upload image", "Submit task"],
            "test_input": "valid image",
            "expected_result": "The correction result is successfully generated and displayed.",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "Submit article successfully enters pending review state",
            "test_module": "Submission",
            "preconditions": ["Draft is valid"],
            "steps": ["Submit article"],
            "test_input": "valid draft",
            "expected_result": "Submission succeeds and the article enters pending review.",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "Normal user opens locked lesson and is redirected to membership paywall",
            "test_module": "Permission",
            "preconditions": ["Normal user is logged in"],
            "steps": ["Open locked lesson"],
            "test_input": "locked lesson",
            "expected_result": "The user cannot enter the lesson and is redirected to membership paywall.",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "Approved work appears in community detail page",
            "test_module": "Community",
            "preconditions": ["Work is approved"],
            "steps": ["Open community detail"],
            "test_input": "approved work",
            "expected_result": "The approved work detail page displays the work content.",
            "priority": "P1",
        },
    ] + [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Core generated result checkpoint {idx}",
            "test_module": f"Generated Result {idx}",
            "preconditions": ["User is logged in"],
            "steps": ["Upload input", "Submit task"],
            "test_input": f"valid input {idx}",
            "expected_result": f"The generated result {idx} is displayed after submit success.",
            "priority": "P1",
        }
        for idx in range(7, 47)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload and submit content, pass approval, handle permissions, sorting, and sharing.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities_by_description = {
        str(item.get("description") or ""): str(item.get("priority") or "").upper()
        for item in (result.get("cases") or [])
    }
    assert priorities_by_description["Category list keeps the promoted course on top"] != "P0"
    assert priorities_by_description["Share article to H5 page and verify card content"] != "P0"
    true_anchor_p0_count = sum(
        1
        for description, priority in priorities_by_description.items()
        if priority == "P0"
        and any(token in description.lower() for token in ("upload", "submit", "approved", "permission", "generated result"))
    )
    assert true_anchor_p0_count >= 3


def test_full_mode_demotes_popup_status_and_limit_cases_from_p0() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for submission, status, and record management",
        expected_count=100,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Submission rule popup appears on first entry",
            "test_module": "Submission Popup",
            "preconditions": ["User is logged in"],
            "steps": ["Open submission page"],
            "test_input": "first entry",
            "expected_result": "The rule popup is displayed.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "My works list shows status badge for failed review",
            "test_module": "My Works",
            "preconditions": ["A failed review work exists"],
            "steps": ["Open my works"],
            "test_input": "failed work",
            "expected_result": "The failed review status badge is displayed.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "My works list keeps maximum records limit at 20",
            "test_module": "My Works",
            "preconditions": ["More than 20 records exist"],
            "steps": ["Open my works"],
            "test_input": "21 records",
            "expected_result": "Only 20 records are retained.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "Submit work successfully enters pending review",
            "test_module": "Submission",
            "preconditions": ["Valid work exists"],
            "steps": ["Submit work"],
            "test_input": "valid work",
            "expected_result": "Submit succeeds and the work enters pending review.",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "Upload image and successfully generate review result",
            "test_module": "Review",
            "preconditions": ["Valid image exists"],
            "steps": ["Upload image", "Submit review"],
            "test_input": "valid image",
            "expected_result": "The generated review result is displayed.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users submit work, view status, and manage records.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities_by_description = {
        str(item.get("description") or ""): str(item.get("priority") or "").upper()
        for item in (result.get("cases") or [])
    }
    assert priorities_by_description["Submission rule popup appears on first entry"] != "P0"
    assert priorities_by_description["My works list shows status badge for failed review"] != "P0"
    assert priorities_by_description["My works list keeps maximum records limit at 20"] != "P0"


def test_template_polluted_expected_result_is_removed_before_final() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for image visibility and correction result",
        expected_count=100,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Image original visibility toggle",
            "test_module": "Correction Upload",
            "preconditions": ["User is logged in"],
            "steps": ["Open uploaded image", "Click toggle original image"],
            "test_input": "uploaded image",
            "expected_result": "执行再次点击按钮后，应跳转到目标页面，且页面路径与标题均与作文批改上传图片显隐原图功能一致",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Upload image and generate correction result",
            "test_module": "Correction Upload",
            "preconditions": ["User is logged in"],
            "steps": ["Upload image", "Submit correction"],
            "test_input": "valid image",
            "expected_result": "The correction result is generated and displays the review details.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload images, toggle original image visibility, and generate correction results.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    final_text = __import__("json").dumps(result.get("cases") or [], ensure_ascii=False)
    assert "页面路径与标题" not in final_text
    assert "显隐原图功能一致" not in final_text


def test_full_mode_demotes_media_management_and_time_status_from_p0() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for upload, submission, approval, permission, and media management",
        expected_count=100,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Image thumbnails can be deleted and drag sorted",
            "test_module": "Upload Media",
            "preconditions": ["Uploaded images exist"],
            "steps": ["Delete one thumbnail", "Drag sort another thumbnail"],
            "test_input": "uploaded images",
            "expected_result": "The image list updates and keeps the new order.",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "Force close app keeps uploaded image list",
            "test_module": "Upload Recovery",
            "preconditions": ["Images have been uploaded"],
            "steps": ["Force close app", "Reopen the page"],
            "test_input": "uploaded images",
            "expected_result": "The uploaded image list is retained.",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "Review remains pending for 48 hours",
            "test_module": "Review Status",
            "preconditions": ["A review item is pending"],
            "steps": ["Wait 48 hours", "Open review status"],
            "test_input": "pending item",
            "expected_result": "The pending status remains visible.",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "My works list keeps maximum records limit at 20",
            "test_module": "My Works",
            "preconditions": ["More than 20 records exist"],
            "steps": ["Open my works"],
            "test_input": "21 records",
            "expected_result": "Only 20 records are retained.",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "Upload valid image and successfully generate correction result",
            "test_module": "Correction",
            "preconditions": ["User is logged in"],
            "steps": ["Upload valid image", "Submit correction"],
            "test_input": "valid image",
            "expected_result": "The correction result is generated and the result details are displayed.",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "Correction feedback displays four modules completely",
            "test_module": "Correction Result",
            "preconditions": ["Correction result exists"],
            "steps": ["Open correction result"],
            "test_input": "result id",
            "expected_result": "The four feedback modules are displayed completely.",
            "priority": "P1",
        },
        {
            "id": "TC-007",
            "description": "Submit work successfully enters pending review",
            "test_module": "Submission",
            "preconditions": ["Valid work exists"],
            "steps": ["Submit work"],
            "test_input": "valid work",
            "expected_result": "Submit succeeds and the work enters pending review.",
            "priority": "P1",
        },
        {
            "id": "TC-008",
            "description": "Approved work becomes visible in community detail",
            "test_module": "Approval",
            "preconditions": ["The submitted work has been approved"],
            "steps": ["Open community list", "Open the work detail"],
            "test_input": "approved work",
            "expected_result": "The approved work is visible in the community detail page.",
            "priority": "P1",
        },
        {
            "id": "TC-009",
            "description": "Member user can access all courses",
            "test_module": "Permission",
            "preconditions": ["User is a member"],
            "steps": ["Open all courses"],
            "test_input": "member account",
            "expected_result": "All courses are accessible.",
            "priority": "P1",
        },
        {
            "id": "TC-010",
            "description": "Normal user first lesson is available and other lessons are locked",
            "test_module": "Permission",
            "preconditions": ["User is not a member"],
            "steps": ["Open first lesson", "Open another lesson"],
            "test_input": "normal account",
            "expected_result": "The first lesson is available and other lessons are locked by the paywall.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Users upload images, generate correction results, submit work, pass approval, and access courses by permission.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    priorities_by_description = {
        str(item.get("description") or ""): str(item.get("priority") or "").upper()
        for item in (result.get("cases") or [])
    }
    assert priorities_by_description["Image thumbnails can be deleted and drag sorted"] != "P0"
    assert priorities_by_description["Force close app keeps uploaded image list"] != "P0"
    assert priorities_by_description["Review remains pending for 48 hours"] != "P0"
    assert priorities_by_description["My works list keeps maximum records limit at 20"] != "P0"
    assert priorities_by_description["Upload valid image and successfully generate correction result"] == "P0"
    assert priorities_by_description["Correction feedback displays four modules completely"] == "P0"


def test_full_mode_shortfall_supplement_recovers_below_floor_result() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for generated results, audit, permissions, upload failures, and downloads",
        expected_count=120,
    )
    base_cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Existing valid workflow checkpoint {idx}",
            "test_module": f"Module {idx}",
            "preconditions": ["User is logged in"],
            "steps": ["Open the module", "Execute the configured workflow"],
            "test_input": f"valid data {idx}",
            "expected_result": f"Workflow checkpoint {idx} completes and the linked state is updated.",
            "priority": "P1" if idx % 2 else "P2",
        }
        for idx in range(1, 65)
    ]
    supplement_cases = [
        {
            "id": f"S-{idx:03d}",
            "description": f"Supplement high value failure or audit path {idx}",
            "test_module": f"Supplement Module {idx}",
            "preconditions": ["A valid business record exists"],
            "steps": ["Trigger the failure or audit path", "Retry or verify synchronized state"],
            "test_input": f"supplement data {idx}",
            "expected_result": f"The failure or audit path {idx} is handled and the final state remains consistent.",
            "priority": "P1",
        }
        for idx in range(1, 23)
    ]
    client = _SupplementClient(supplement_cases)

    result = _drain_with_return(
        stream_postprocess_cases(
            client=client,
            requirement="Full regression must cover generated results, upload failures, retry, audit rejection, permission, and download failure.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(base_cases, ensure_ascii=False),
            expected_count=120,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    review_summary = dict(result.get("review_decision_summary") or {})
    assert len(result.get("cases") or []) >= 80
    assert review_summary["final_shortfall_supplement_attempted"] is True
    assert review_summary["final_shortfall_supplement_applied"] is True
    assert review_summary["final_shortfall_supplement_count"] > 0
    assert review_summary["final_scenario_duplicate_case_count"] == 0
    assert summary["underfilled"] is False
    assert any("Generate up to 26 additional" in prompt for prompt in client.prompts)


def test_full_mode_marks_below_recommended_floor_underfilled_even_when_candidates_are_few() -> None:
    state = build_generation_mode_control_state(
        requirement_text="full functional regression for a compact candidate set",
        expected_count=100,
    )
    cases = [
        {
            "id": f"TC-{idx:03d}",
            "description": f"Compact full candidate {idx}",
            "test_module": f"Module {idx}",
            "preconditions": ["User is logged in"],
            "steps": ["Open module", "Run action"],
            "test_input": f"input {idx}",
            "expected_result": f"Result {idx} is displayed.",
            "priority": "P1" if idx % 3 else "P2",
        }
        for idx in range(1, 59)
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="full functional regression for a compact candidate set",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    summary = dict(result.get("generation_summary") or {})
    assert summary["generation_coverage_mode"] == "full_functional_regression"
    assert summary["hard_min_count"] >= 80
    assert summary["underfilled"] is True
    assert summary["underfill_level"] in {"moderate", "severe"}
    assert summary["underfill_reason"] == "valid_candidate_insufficient"


def test_confirmed_nonlinear_stage_fact_drops_legacy_locked_stage_case() -> None:
    state = build_generation_mode_control_state(
        requirement_text="Course stages are non-linear and have no prerequisites; any stage can be entered initially.",
        expected_count=100,
    )
    payload = state.to_dict()
    payload.setdefault("source_meta", {})["fact_profile"] = {
        "confirmed_facts": [
            "Course stages are non-linear and have no prerequisites; all stages are enterable initially."
        ],
        "confidence": 0.9,
    }
    cases = [
        {
            "id": "TC-001",
            "description": "Course stage initial state allows all stages to be entered",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click each stage"],
            "test_input": "course stage",
            "expected_result": "All stages are enterable without prerequisite prompts.",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Course stage initial state shows locked toast before previous stage is completed",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click a locked stage"],
            "test_input": "locked stage",
            "expected_result": "The stage cannot enter and a previous stage toast is shown.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Course stages are non-linear and have no prerequisites; any stage can be entered initially.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=payload,
        )
    )

    descriptions = " ".join(str(item.get("description") or "") for item in (result.get("cases") or [])).lower()
    assert "allows all stages" in descriptions
    assert "locked toast" not in descriptions
    summary = dict(result.get("review_decision_summary") or {})
    assert int(summary.get("final_confirmed_conflict_drop_count") or 0) >= 1


def test_final_set_internal_nonlinear_stage_conflict_drops_legacy_locked_case() -> None:
    state = build_generation_mode_control_state(
        requirement_text="Course stage regression with current stage behavior.",
        expected_count=100,
    )
    cases = [
        {
            "id": "TC-001",
            "description": "Course stages are non-linear and all stages are enterable initially",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click all stages"],
            "test_input": "course stage",
            "expected_result": "All stages are enterable initially without prerequisite prompts.",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "Course stage initial locked toast before completing previous stage",
            "test_module": "Course Stage",
            "preconditions": ["User is logged in"],
            "steps": ["Open the course stage page", "Click a locked stage"],
            "test_input": "locked stage",
            "expected_result": "The stage is locked and a previous stage completion toast is shown.",
            "priority": "P1",
        },
    ]

    result = _drain_with_return(
        stream_postprocess_cases(
            client=_NoopClient(),
            requirement="Course stage regression with current stage behavior.",
            base_prompt="BASE",
            kb_context="",
            full_content=__import__("json").dumps(cases, ensure_ascii=False),
            expected_count=100,
            append=False,
            existing_cases=[],
            existing_unique_count=0,
            start_id=1,
            db=None,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            count_unique_test_cases_fn=count_unique_test_cases,
            infer_case_kind_fn=infer_case_kind,
            build_supplement_closed_loop_instruction_fn=lambda **_: "",
            multi_pass=False,
            generation_mode="single_pass",
            feedback_control_state=state.to_dict(),
        )
    )

    descriptions = " ".join(str(item.get("description") or "") for item in (result.get("cases") or [])).lower()
    assert "all stages are enterable" in descriptions
    assert "locked toast" not in descriptions
    summary = dict(result.get("review_decision_summary") or {})
    assert int(summary.get("final_confirmed_conflict_drop_count") or 0) >= 1
