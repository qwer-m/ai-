from modules.test_generation_components.control.functional_architecture import extract_functional_architecture
from modules.test_generation_components.control.project_profile_activation import build_project_profile
from modules.test_generation_components.legacy.stream.batch_flow_control import (
    select_complete_generated_cases,
)
from modules.test_generation_components.postprocess.streaming_case_normalization import is_placeholder_expected_result
from modules.test_generation_components.postprocess.json_normalizer import normalize_json_structure
from modules.test_generation_components.postprocess.json_validator import infer_case_kind, reorder_cases_by_closed_loop
from modules.test_generation_components.postprocess.module_contract import (
    apply_functional_module_phase,
    case_matches_functional_phase,
    enforce_functional_module_contract,
    summarize_functional_phase_coverage,
)
from modules.test_generation_components.postprocess.streaming_ui_like import is_ui_like_case
from core.processing.business_chunking import RequirementChunker
from modules.test_generation_components.postprocess.priority_anchor_rules import (
    enforce_entry_path_p0,
    enforce_pure_ui_p2,
)


STRUCTURED_PARTITION_REQUIREMENT = """
## 内容分区
1. 官方区：只有官方账户有发帖权限，普通用户查看时隐藏发布按钮
2. 反馈区：从视频页点击反馈后进入该区域
3. 交流区：用户可以发布内容、回复和删除自己的帖子
4. 作文区：二期开放，当前暂不实现
5. 消息：新内容到达后显示红点，点击进入消息详情
"""


NUMBERED_PRODUCT_REQUIREMENT = """
1. 需求背景
本次更新用于丰富产品内容。
2. 课程内容
列出三至六年级的作文题目。
3. 进入课程
用户点击首页入口进入课程列表，选择年级、册次和单元。
普通用户点击锁定课程时跳转会员中心。
4. 课程环节
用户进入学习页面，完成答题后返回课程页并更新完成状态。
5. 作文批改
用户上传图片并提交批改，页面显示处理中、成功或失败状态。
6. 写作素材
用户查看素材列表，切换分类并下载资料。
7. 写作秘籍
用户进入秘籍列表，点击已获得或未获得的秘籍查看详情。
8. 课程购买
课程购买成功后，进入课程模块刷新页面显示已解锁。
"""


MARKDOWN_PRODUCT_REQUIREMENT = """
# Background
This release improves the existing product.
# Account Management
Users create an account, sign in, edit profile data, and view account status.
# Order Processing
Users select products, submit an order, pay, and view success or failure status.
# Reporting
Users open the report page, filter records, and download the result.
# Appendix
This section only contains terminology.
"""


NESTED_BUSINESS_PARTITION_REQUIREMENT = """
1. 需求背景
本次迭代调整现有产品内部功能。
2. 产品首页
2.1 页面样式
用户打开首页，查看列表和页面状态。
2.2 业务分区
1. 内容中心：运营角色可以发布内容，普通用户可查看
2. 服务反馈：用户从服务页进入并提交反馈
3. 用户交流：用户可创建话题、回复或删除自己的内容
4. 作品展示：该区域二期开放，当前暂不实现
5. 系统提醒：有新内容时显示红点，点击进入详情
3. 详情页
用户点击列表内容进入详情，可以返回产品首页。
4. 管理后台
管理员进入审核页，查看、审核或删除用户内容。
"""


PARENT_SCOPE_REQUIREMENT = """
## 当前功能
1. 订单：用户创建订单并查看成功状态
2. 报表：用户进入报表页并下载结果
3. 通知：新状态到达后显示通知，点击进入详情
## 扩展功能（二期）
1. 智能分析：用户进入分析页查看趋势
2. 自动化：用户创建自动化任务并查看状态
3. 异常预警：异常发生后同步通知给管理员
"""


PARTIAL_FUTURE_FEATURE_REQUIREMENT = """
1. 订单管理
用户进入订单页，创建订单并查看成功或失败状态。
1.1 批量导出（二期）
用户后续可以下载批量导出结果。
2. 报表中心
用户打开报表页，筛选记录并下载结果，页面显示下载成功状态。
"""


def test_numbered_top_level_sections_build_generic_module_catalog() -> None:
    architecture = extract_functional_architecture(NUMBERED_PRODUCT_REQUIREMENT)

    assert architecture["source"] == "document_top_level_sections"
    assert [item["module_name"] for item in architecture["functional_modules"]] == [
        "进入课程",
        "课程环节",
        "作文批改",
        "写作素材",
        "写作秘籍",
        "课程购买",
    ]


def test_markdown_sections_use_the_same_structure_and_evidence_rules() -> None:
    architecture = extract_functional_architecture(MARKDOWN_PRODUCT_REQUIREMENT)

    assert architecture["source"] == "document_top_level_sections"
    assert [item["module_name"] for item in architecture["functional_modules"]] == [
        "Account Management",
        "Order Processing",
        "Reporting",
    ]


def test_structured_sibling_declarations_do_not_require_module_name_suffixes() -> None:
    architecture = extract_functional_architecture(STRUCTURED_PARTITION_REQUIREMENT)

    assert [item["module_name"] for item in architecture["functional_modules"]] == [
        "官方区",
        "反馈区",
        "交流区",
        "消息",
    ]
    assert [(item["module_name"], item["scope_reason"]) for item in architecture["excluded_modules"]] == [
        ("作文区", "暂不实现")
    ]


def test_nested_business_partition_can_outrank_page_level_sections() -> None:
    architecture = extract_functional_architecture(NESTED_BUSINESS_PARTITION_REQUIREMENT)

    assert architecture["source"] == "structured_declaration_group"
    assert [item["module_name"] for item in architecture["functional_modules"]] == [
        "内容中心",
        "服务反馈",
        "用户交流",
        "系统提醒",
    ]
    assert [item["module_name"] for item in architecture["excluded_modules"]] == ["作品展示"]


def test_flow_outline_is_scoped_by_selected_functional_architecture() -> None:
    profile = build_project_profile(requirement_text=NESTED_BUSINESS_PARTITION_REQUIREMENT)
    outline = profile["flow_outline"]
    labels = [outline["flow_labels"][key] for key in outline["flow_order"]]

    assert outline["scope_source"] == "functional_architecture_candidates"
    assert outline["scope_module_count"] == 4
    assert set(labels) <= {"内容中心", "服务反馈", "用户交流", "系统提醒"}
    assert not ({"产品首页", "详情页", "管理后台", "作品展示"} & set(labels))


def test_parent_scope_marker_is_inherited_by_declared_children() -> None:
    architecture = extract_functional_architecture(PARENT_SCOPE_REQUIREMENT)

    assert [item["module_name"] for item in architecture["functional_modules"]] == ["订单", "报表", "通知"]
    assert architecture["excluded_modules"] == []

    future_only = extract_functional_architecture(
        PARENT_SCOPE_REQUIREMENT[PARENT_SCOPE_REQUIREMENT.index("## 扩展功能") :]
    )
    assert future_only["functional_modules"] == []


def test_future_child_feature_does_not_exclude_parent_module() -> None:
    architecture = extract_functional_architecture(PARTIAL_FUTURE_FEATURE_REQUIREMENT)

    assert [item["module_name"] for item in architecture["functional_modules"]] == ["订单管理", "报表中心"]
    assert architecture["excluded_modules"] == []


def test_requirement_chunker_does_not_promote_numbered_items_to_modules() -> None:
    chunks = RequirementChunker().chunk(STRUCTURED_PARTITION_REQUIREMENT)

    assert chunks
    assert all(item.module is None for item in chunks)


def test_document_structure_candidates_do_not_infer_interaction_direction_from_cooccurrence() -> None:
    single_module = extract_functional_architecture(STRUCTURED_PARTITION_REQUIREMENT)
    explicit_relation = extract_functional_architecture(NUMBERED_PRODUCT_REQUIREMENT)

    assert single_module["module_interactions"] == []
    assert explicit_relation["module_interactions"] == []


def test_cross_module_phase_rejects_case_without_interaction_evidence() -> None:
    phase = {
        "phase": "cross_module",
        "interactions": [
            {
                "interaction_id": "reply_notice",
                "source_module": "交流区",
                "target_module": "消息",
                "trigger": "交流区回复后消息模块显示提醒",
            }
        ],
    }
    valid_case = {
        "test_module": "消息",
        "description": "交流区回复后消息模块显示提醒",
        "_semantic": {"interaction_ids": ["reply_notice"]},
    }
    unrelated_case = {
        "test_module": "消息",
        "description": "消息列表滑动时自动收起键盘",
    }

    assert case_matches_functional_phase(valid_case, phase) is True
    assert case_matches_functional_phase(unrelated_case, phase) is False
    assert apply_functional_module_phase([valid_case, unrelated_case], phase) == [
        {
            **valid_case,
            "functional_phase": "cross_module",
            "functional_interaction_modules": ["交流区", "消息"],
            "functional_interaction_ids": ["reply_notice"],
        }
    ]


def test_module_internal_phase_records_target_without_overwriting_model_module() -> None:
    cases = apply_functional_module_phase(
        [
            {"id": "TC-001", "test_module": "投稿-提交流程", "description": "提交投稿"},
            {"id": "TC-002", "module": "临时模块", "description": "投稿成功"},
        ],
        {"phase": "module_internal", "module_name": "作文批改"},
    )

    assert cases[0]["test_module"] == "投稿-提交流程"
    assert cases[1]["module"] == "临时模块"
    assert all(item["functional_module_anchor"] == "作文批改" for item in cases)


def test_batch_accepts_only_complete_model_cases_and_renumbers_them() -> None:
    complete = {
        "id": "model-id",
        "test_module": "通用模块",
        "description": "保存当前配置",
        "preconditions": ["用户已进入配置页"],
        "steps": ["填写配置", "点击保存"],
        "test_input": "配置值=A",
        "expected_result": "保存成功提示出现，重新进入后配置值为A",
        "priority": "P1",
    }
    accepted, rejected = select_complete_generated_cases(
        [
            {**complete, "id": "bad-1", "expected_result": "符合预期"},
            complete,
            {**complete, "id": "bad-2", "preconditions": []},
            {**complete, "id": "bad-3", "priority": ""},
        ],
        limit=2,
        start_id=7,
        is_placeholder_expected_result_fn=is_placeholder_expected_result,
    )

    assert [item["id"] for item in accepted] == ["TC-007"]
    assert rejected == [
        {"case_id": "bad-1", "missing_fields": ["expected_result"]},
        {"case_id": "bad-2", "missing_fields": ["preconditions"]},
        {"case_id": "bad-3", "missing_fields": ["priority"]},
    ]


def test_module_contract_accepts_exact_module_and_rejects_prefix_or_unknown_module() -> None:
    profile = build_project_profile(requirement_text=STRUCTURED_PARTITION_REQUIREMENT)
    cases, summary = enforce_functional_module_contract(
        [
            {
                "id": "TC-010",
                "description": "帖子审核不通过消息点击弹出提示",
                "test_module": "消息",
                "steps": ["进入消息页并点击审核消息"],
                "expected_result": "展示审核不通过提示",
                "priority": "P1",
            },
            {
                "id": "TC-003",
                "description": "编辑帖子内容",
                "test_module": "论坛发帖主流程",
                "steps": ["填写内容并选择版块"],
                "expected_result": "内容保存完成",
                "priority": "P0",
            },
        ],
        project_profile=profile,
    )

    assert [item["test_module"] for item in cases] == ["消息"]
    assert summary["normalized_count"] == 0
    assert summary["rejected_modules"] == ["论坛发帖主流程"]


def test_unmapped_workflow_step_does_not_inherit_neighbor_module() -> None:
    profile = build_project_profile(requirement_text=STRUCTURED_PARTITION_REQUIREMENT)
    cases, summary = enforce_functional_module_contract(
        [
            {"id": "TC-001", "test_module": "官方区", "description": "进入官方区"},
            {
                "id": "TC-002",
                "test_module": "论坛发帖主流程",
                "description": "编辑帖子内容",
            },
            {"id": "TC-003", "test_module": "反馈区", "description": "进入反馈区"},
        ],
        project_profile=profile,
        inherit_execution_context=True,
    )

    assert [item["test_module"] for item in cases] == ["官方区", "反馈区"]
    assert summary["normalized_count"] == 0
    assert summary["rejected_count"] == 1
    assert summary["execution_context_inheritance_applied"] is False


def test_workflow_entry_ui_precedes_presentation_only_ui() -> None:
    entry = {
        "id": "TC-001",
        "description": "论坛首页交流区入口点击后进入交流区",
        "test_module": "交流区",
        "steps": ["点击交流区入口"],
        "expected_result": "进入交流区帖子列表",
        "priority": "P0",
    }
    presentation = {
        "id": "TC-002",
        "description": "论坛首页背景主色调和按钮间距符合设计稿",
        "test_module": "交流区",
        "steps": ["查看页面样式"],
        "expected_result": "背景颜色和按钮间距与设计稿一致",
        "priority": "P2",
    }

    assert infer_case_kind(entry) == "workflow_entry"
    assert infer_case_kind(presentation) == "ui_verification"
    assert is_ui_like_case(entry, {}) is False
    ordered = reorder_cases_by_closed_loop([presentation, entry], renumber_ids=False)
    assert [item["id"] for item in ordered] == ["TC-001", "TC-002"]


def _generic_phase_profile() -> dict:
    return {
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "account",
                    "module_name": "Account",
                    "scope_status": "in_scope",
                    "features": ["Create account", "Sign in", "Edit profile", "Deactivate account"],
                    "evidence_verified": True,
                },
                {
                    "module_key": "notice",
                    "module_name": "Notice",
                    "scope_status": "in_scope",
                    "features": ["Open notice"],
                    "evidence_verified": True,
                },
            ],
            "module_interactions": [
                {
                    "interaction_id": "account_notice",
                    "source_module_key": "account",
                    "target_module_key": "notice",
                    "source_module": "Account",
                    "target_module": "Notice",
                    "trigger": "Profile update sends a notice",
                    "evidence_verified": True,
                }
            ],
        }
    }


def _account_notice_semantic() -> dict:
    return {
        "module_candidates": [
            {
                "module_key": "notice",
                "module_name": "Notice",
                "role": "primary",
                "confidence": 0.9,
                "evidence": ["Notice"],
            }
        ],
        "interaction_ids": ["account_notice"],
    }


def test_functional_phase_metadata_survives_internal_and_cross_batches() -> None:
    internal = apply_functional_module_phase(
        [{"id": "TC-001", "test_module": "temporary", "description": "Edit profile"}],
        {"phase": "module_internal", "module_name": "Account"},
    )
    cross = apply_functional_module_phase(
        [{"id": "TC-002", "test_module": "Notice", "description": "Account profile update creates Notice", "_semantic": _account_notice_semantic()}],
        {
            "phase": "cross_module",
            "interactions": [
                {
                    "interaction_id": "account_notice",
                    "source_module": "Account",
                    "target_module": "Notice",
                    "trigger": "Profile update sends a notice",
                    "evidence_terms": ["profile update"],
                }
            ],
        },
    )

    assert internal[0]["functional_phase"] == "module_internal"
    assert internal[0]["functional_module_anchor"] == "Account"
    assert internal[0]["test_module"] == "temporary"
    assert cross[0]["functional_phase"] == "cross_module"
    assert cross[0]["functional_interaction_modules"] == ["Account", "Notice"]
    assert cross[0]["functional_interaction_ids"] == ["account_notice"]


def test_functional_phase_metadata_survives_json_normalization() -> None:
    cross = apply_functional_module_phase(
        [
            {
                "id": "TC-001",
                "test_module": "Notice",
                "description": "Account profile update creates Notice",
                "preconditions": ["Profile exists"],
                "steps": ["Update profile"],
                "test_input": "New profile name",
                "expected_result": "Notice is created",
                "priority": "P1",
                "_semantic": _account_notice_semantic(),
            }
        ],
        {
            "phase": "cross_module",
            "interactions": [
                {
                    "interaction_id": "account_notice",
                    "source_module": "Account",
                    "target_module": "Notice",
                    "trigger": "Profile update sends a notice",
                    "evidence_terms": ["profile update"],
                }
            ],
        },
    )

    normalized = normalize_json_structure(cross)

    assert normalized[0]["functional_phase"] == "cross_module"
    assert normalized[0]["functional_interaction_modules"] == ["Account", "Notice"]
    assert normalized[0]["functional_interaction_ids"] == ["account_notice"]
    assert normalized[0]["_semantic"]["interaction_ids"] == ["account_notice"]


def test_module_contract_derives_cross_module_metadata_from_structured_semantics() -> None:
    result, summary = enforce_functional_module_contract(
        [
            {
                "id": "TC-001",
                "test_module": "Notice",
                "description": "Account profile update creates Notice",
                "_semantic": _account_notice_semantic(),
            }
        ],
        project_profile=_generic_phase_profile(),
    )

    assert summary["rejected_count"] == 0
    assert result[0]["functional_phase"] == "cross_module"
    assert result[0]["functional_interaction_modules"] == ["Account", "Notice"]
    assert result[0]["functional_interaction_ids"] == ["account_notice"]


def test_global_architecture_coverage_preserves_uneven_complex_and_simple_modules() -> None:
    cases = [
        {
            "id": f"A-{index}",
            "test_module": "Account",
            "description": f"Account workflow rule {index}",
        }
        for index in range(1, 6)
    ]
    cases.append(
        {
            "id": "N-1",
            "test_module": "Notice",
            "description": "Open one notice",
        }
    )

    summary = summarize_functional_phase_coverage(
        cases,
        project_profile=_generic_phase_profile(),
        target_count=6,
    )

    assert summary["module_counts"] == {"account": 5, "notice": 1}
    assert summary["interaction_counts"] == {"account_notice": 0}
    assert summary["uncovered_modules"] == []
    assert summary["uncovered_interactions"] == ["account_notice"]
    assert "phase_targets" not in summary
    assert "remaining_deficits" not in summary


def test_functional_phase_summary_reports_cross_module_before_public_projection() -> None:
    account = apply_functional_module_phase(
        [{"id": "A-1", "test_module": "Account", "description": "Edit profile"}],
        {"phase": "module_internal", "module_name": "Account"},
    )
    cross = apply_functional_module_phase(
        [{"id": "X-1", "test_module": "Notice", "description": "Account profile update creates Notice", "_semantic": _account_notice_semantic()}],
        {
            "phase": "cross_module",
            "interactions": [
                {
                    "interaction_id": "account_notice",
                    "source_module": "Account",
                    "target_module": "Notice",
                    "trigger": "Profile update sends a notice",
                    "evidence_terms": ["profile update"],
                }
            ],
        },
    )

    summary = summarize_functional_phase_coverage(
        [*account, *cross],
        project_profile=_generic_phase_profile(),
        target_count=2,
    )

    assert summary["phase_counts"] == {
        "module_internal:Account": 1,
        "cross_module": 1,
    }


def test_entry_path_availability_is_p0_but_visual_style_is_not() -> None:
    cases = [
        {
            "id": "TC-ENTRY",
            "description": "Click the feedback button to enter the feedback page",
            "steps": ["Click the feedback button"],
            "expected_result": "The target page opens and shows the feedback list",
            "priority": "P2",
            "priority_final": "P2",
            "main_chain_stage_kind": "entry",
            "workflow_transition": {
                "workflow_id": "feedback-flow",
                "stage_kind": "entry",
                "critical": True,
                "blocking": True,
            },
        },
        {
            "id": "TC-STYLE",
            "description": "Check the return button color and spacing",
            "steps": ["View the return button style"],
            "expected_result": "The button color matches the design",
            "priority": "P2",
            "priority_final": "P2",
        },
    ]

    result = enforce_entry_path_p0(cases)
    result = enforce_pure_ui_p2(result)

    assert result[0]["priority_final"] == "P0"
    assert result[0]["priority_decision_source"] == "entry_path_availability_p0"
    assert result[1]["priority_final"] == "P2"


def test_return_button_style_is_not_misclassified_as_entry_navigation() -> None:
    cases = [
        {
            "id": "TC-UI-RETURN",
            "description": "检查返回按钮文案、颜色和悬浮样式",
            "preconditions": ["用户已进入目标页面"],
            "steps": ["观察返回按钮样式", "对比设计稿"],
            "test_input": "页面视觉设计",
            "expected_result": "返回按钮颜色、文案和间距符合设计",
            "priority": "P0",
            "priority_final": "P0",
        }
    ]

    result = enforce_entry_path_p0(cases)
    result = enforce_pure_ui_p2(result)

    assert result[0]["priority_final"] == "P2"
    assert result[0]["priority_decision_source"] == "pure_ui_non_blocking_p2"
