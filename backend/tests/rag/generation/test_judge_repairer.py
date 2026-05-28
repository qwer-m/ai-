from __future__ import annotations

from modules.testing.test_generation_components.judge.test_case_judge import judge_cases
from modules.testing.test_generation_components.judge.test_case_repairer import repair_cases
from modules.testing.test_generation_components.postprocess.json_repair import deduplicate_test_cases


def test_judge_repairer_does_not_append_untyped_batch_gap_cases() -> None:
    semantics = {
        "hard_flow_constraints": [
            "\u4e8c\u8f6e\u590d\u4e60\u8bfe\u7a0b\u8be6\u60c5\u9875\u4ec5\u4fdd\u7559\u5b66\u0026\u7ec3\u6d41\u7a0b"
        ],
        "reuse_risks": [
            "\u6253\u5370\u5f39\u7a97\u4fdd\u7559\u6559\u6750\u548c\u7b54\u6848\u53cc\u9009\u9879"
        ],
    }

    judged = judge_cases([], semantics)
    repaired = repair_cases(judged, semantics)

    assert repaired.appended_case_count == 0
    assert repaired.repaired_case_count == 0
    assert repaired.repairable_count == 2
    for item in repaired.cases:
        if item.status != "REPAIRABLE":
            continue
        assert item.repaired_pass is False
        assert item.after_case == {}
        assert item.reject_reason == "requires_typed_requirement_unit"
        assert "untyped_batch_gap_not_auto_repaired" in item.signals.notes


def test_judge_repairer_does_not_append_when_missing_payload_is_empty() -> None:
    judged = judge_cases([], {"hard_flow_constraints": []})
    repaired = repair_cases(judged, {"hard_flow_constraints": []})

    assert repaired.appended_case_count == 0
    assert all(not item.after_case for item in repaired.cases if item.status == "REPAIRABLE")


def test_judge_rejects_exact_duplicate_cases_at_batch_level() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "习题本标题格式“小学数学X周X习题本”验证",
            "test_module": "习题本",
            "preconditions": ["当前系统教学周为第3周"],
            "steps": ["1. 登录学生端", "2. 进入习题本周视图", "3. 查看页面标题"],
            "test_input": "无",
            "expected_result": "标题显示为“小学数学3周习题本”，周次与当前周一致",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "验证习题本标题格式为'小学数学X周X习题本'，其中X为当前教学周数",
            "test_module": "习题本",
            "preconditions": ["当前教学周为第5周"],
            "steps": ["1. 学生端进入习题本页面", "2. 观察页面标题/导航栏标题"],
            "test_input": "无",
            "expected_result": "标题显示为'小学数学5周习题本'",
            "priority": "P0",
        },
    ]
    cases[1]["description"] = cases[0]["description"]

    judged = judge_cases(cases, {})
    statuses = {item.case_id: item.status for item in judged.cases}
    duplicate = next(item for item in judged.cases if item.case_id == "TC-002")

    assert statuses["TC-001"] == "PASS"
    assert statuses["TC-002"] == "REJECT"
    assert duplicate.reject_reason == "semantic_duplicate:TC-001"
    assert duplicate.signals.is_semantic_duplicate is True
    assert duplicate.signals.duplicate_of_case_id == "TC-001"


def test_judge_rejects_near_duplicate_same_scenario_cases_at_batch_level() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "验证习题本中只看错题开关默认关闭，开启后隐藏正确题，无错题时显示指定空状态文案",
            "test_module": "习题本",
            "steps": ["1. 打开习题本", "2. 开启只看错题"],
            "test_input": "开关切换操作",
            "expected_result": "默认关闭显示全部题目；开启后只显示错误题；全部正确时显示本次作业全部作答正确，暂无错题",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "验证本周全部答对0错题时习题本只看错题后显示本次作业全部作答正确，暂无错题",
            "test_module": "习题本",
            "steps": ["1. 打开习题本", "2. 点击只看错题"],
            "test_input": "无",
            "expected_result": "开启只看错题后列表为空，并显示本次作业全部作答正确，暂无错题",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "验证习题本只看错题开关开启后正确隐藏正确题",
            "test_module": "习题本",
            "steps": ["1. 打开习题本", "2. 开启只看错题"],
            "test_input": "无",
            "expected_result": "只显示错题，正确题隐藏",
            "priority": "P0",
        },
    ]

    judged = judge_cases(cases, {})

    assert judged.reject_count == 2
    duplicate_rows = [item for item in judged.cases if item.signals.is_semantic_duplicate]
    assert {item.case_id for item in duplicate_rows} == {"TC-002", "TC-003"}
    assert all(item.reject_reason == "semantic_duplicate:TC-001" for item in duplicate_rows)


def test_judge_rejects_registered_registry_duplicate_scenarios_across_modules() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "\u9a8c\u8bc1\u5b66\u5458\u56de\u7b54\u7b54\u975e\u6240\u95ee\u65f6\uff0c\u51c6\u786e\u6027\u76f4\u63a5\u8bb0\u4e3a0\u5206",
            "test_module": "\u5b66\u5458\u7aefAI\u8bc4\u5206",
            "steps": [
                "1. AI\u63d0\u95ee\u5177\u4f53\u6570\u5b66\u9898",
                "2. \u5b66\u5458\u8f93\u5165\u4e0e\u9898\u76ee\u65e0\u5173\u7684\u5185\u5bb9",
                "3. \u67e5\u770b\u51c6\u786e\u6027\u5206\u6570",
            ],
            "test_input": "\u4eca\u5929\u5929\u6c14\u5f88\u597d",
            "expected_result": "\u51c6\u786e\u6027\u5206\u6570\u4e3a0\u5206\uff0c\u5176\u4ed6\u7ef4\u5ea6\u6b63\u5e38\u8bc4\u5206",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "\u9a8c\u8bc1\u5b66\u5458\u7aef\u56de\u7b54\u7b54\u975e\u6240\u95ee\u65f6\u51c6\u786e\u6027\u81ea\u52a80\u5206\uff0c\u4e14\u603b\u5206\u6309\u89c4\u5219\u8ba1\u7b97",
            "test_module": "\u5b66\u5458\u7aefAI\u8bb2\u9519\u9898\u8bc4\u5206",
            "steps": [
                "1. \u5728AI\u8ffd\u95ee\u540e\u8f93\u5165\u4e0e\u95ee\u9898\u65e0\u5173\u7684\u56de\u7b54",
                "2. \u5b8c\u6210\u4ea4\u4e92\u540e\u67e5\u770b\u8bc4\u5206\u660e\u7ec6",
            ],
            "test_input": "\u4eca\u5929\u5929\u6c14\u4e0d\u9519",
            "expected_result": "\u51c6\u786e\u6027\u7ef4\u5ea6\u5f970\u5206\uff0c\u7cfb\u7edf\u6807\u6ce8\u7b54\u975e\u6240\u95ee",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate = next(item for item in judged.cases if item.case_id == "TC-002")

    assert duplicate.status == "REJECT"
    assert duplicate.reject_reason == "semantic_duplicate:TC-001"
    assert duplicate.signals.is_semantic_duplicate is True
    assert duplicate.signals.duplicate_of_case_id == "TC-001"


def test_judge_rejects_popup_card_share_quota_and_refresh_duplicate_scenarios() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "批改结果复核页面首次进入时弹出批改完成弹窗",
            "test_module": "批改结果复核",
            "steps": ["AI批改完成后自动跳转至复核页面"],
            "expected_result": "页面弹出弹窗，文案包含已智能完成全页批改",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "验证批改结果复核页面首次进入时弹出已智能完成全页批改弹窗",
            "test_module": "批改结果复核",
            "steps": ["观察页面是否弹出弹窗"],
            "expected_result": "页面加载完成后弹出批改完成弹窗，弹窗可关闭",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "验证习题本题目卡片包含状态角标、知识点标签、元信息、题目图片、查看详解按钮",
            "test_module": "习题本",
            "steps": ["查看习题本题目卡片"],
            "expected_result": "卡片显示状态角标、知识点标签、元信息、图片和查看详解按钮",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "题目卡片元素验证：状态角标、知识点标签、元信息、图片、查看详解按钮",
            "test_module": "习题本-题目卡片",
            "steps": ["检查题目卡片元素"],
            "expected_result": "状态角标、知识点标签、元信息、图片、查看详解按钮均展示",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "验证学习成长报告分享至微信好友和朋友圈",
            "test_module": "学习成长报告",
            "steps": ["点击分享按钮"],
            "expected_result": "微信好友生成H5链接和二维码，朋友圈生成信息长图",
            "priority": "P0",
        },
        {
            "id": "TC-006",
            "description": "验证学习成长报告分享到朋友圈自动生成长图",
            "test_module": "学习成长报告",
            "steps": ["点击分享到朋友圈"],
            "expected_result": "长图包含学生姓名、教学周和核心数据",
            "priority": "P0",
        },
        {
            "id": "TC-007",
            "description": "验证体验额度耗尽后所有学习入口均被拦截",
            "test_module": "体验额度",
            "steps": ["点击学习入口"],
            "expected_result": "弹出体验次数已用完的拦截文案",
            "priority": "P1",
        },
        {
            "id": "TC-008",
            "description": "体验额度耗尽后拦截所有学习入口并显示指定文案",
            "test_module": "体验额度",
            "steps": ["尝试进入任何学习入口"],
            "expected_result": "每个操作均弹出体验次数已用完的拦截提示",
            "priority": "P1",
        },
        {
            "id": "TC-009",
            "description": "验证督导新增错题后学生端习题本静默刷新且无弹窗",
            "test_module": "全局异常",
            "steps": ["督导新增错题", "学生端等待刷新"],
            "expected_result": "学生端无弹窗，新增错题出现，知识点掌握度重算",
            "priority": "P0",
        },
        {
            "id": "TC-010",
            "description": "督导重新批改新增错题后学生端数据静默刷新",
            "test_module": "全局异常-静默刷新",
            "steps": ["触发后端数据变更"],
            "expected_result": "页面数据静默刷新，无任何弹窗提示，提升计划同步更新",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-004"}) in duplicate_pairs
    assert frozenset({"TC-007", "TC-008"}) in duplicate_pairs
    assert frozenset({"TC-009", "TC-010"}) in duplicate_pairs


def test_judge_rejects_nested_plan_report_quota_duplicate_scenarios() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "批改结果复核温馨提示显示条件验证（需关注题和特殊题型）",
            "test_module": "批改结果复核",
            "steps": ["查看需关注题和特殊题型详情"],
            "expected_result": "需关注题显示书写差异建议人工复核；特殊题型显示特殊题型建议重点核对",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "批改结果复核温馨提示书写差异建议人工复核显示条件验证",
            "test_module": "批改结果复核",
            "steps": ["查看需关注题详情"],
            "expected_result": "温馨提示显示书写差异建议人工复核",
            "priority": "P2",
        },
        {
            "id": "TC-003",
            "description": "周末提升计划第一步数据范围验证，仅当前教学周已完成精准学习题本",
            "test_module": "周末提升计划第一步",
            "steps": ["进入看看我的弱点", "查看错题来源"],
            "expected_result": "只统计当前教学周已完成精准学习题本，不包含牛刀小试和未完成题本",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "周末提升计划第一步数据范围与错题率计算，验证仅纳入当前教学周已完成精准学习题本",
            "test_module": "周末提升计划-第一步",
            "steps": ["进入看看我的弱点"],
            "expected_result": "仅展示当前教学周已完成精准学习题本中的错题，不包含牛刀课内练习",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "周末提升计划第三步学习流程，完成一个切片自动进入下一个",
            "test_module": "周末提升计划-第三步",
            "steps": ["完成第一个切片"],
            "expected_result": "完成当前切片后自动跳转至下一个切片",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "周末提升计划第三步切片自动进入下一个验证",
            "test_module": "周末提升计划第三步",
            "steps": ["完成第一个切片全部内容"],
            "expected_result": "系统自动切换到下一个切片，无需手动点击",
            "priority": "P2",
        },
        {
            "id": "TC-007",
            "description": "学习成长报告分享微信好友群功能验证",
            "test_module": "学习成长报告",
            "steps": ["点击分享微信好友"],
            "expected_result": "生成H5链接和二维码，分享卡片包含学生姓名、教学周、摘要、封面图",
            "priority": "P0",
        },
        {
            "id": "TC-008",
            "description": "学习成长报告分享微信好友卡片内容验证",
            "test_module": "学习成长报告（督导端）",
            "steps": ["查看分享卡片预览"],
            "expected_result": "分享卡片包含学生姓名、教学周、摘要文本、封面图片",
            "priority": "P0",
        },
        {
            "id": "TC-009",
            "description": "学习成长报告分享到朋友圈功能验证信息长图生成",
            "test_module": "学习成长报告",
            "steps": ["选择分享到朋友圈"],
            "expected_result": "自动生成信息长图，包含学生姓名、教学周、核心数据",
            "priority": "P0",
        },
        {
            "id": "TC-010",
            "description": "体验额度正常消耗，学生首次批改1道题，额度从50减少至49",
            "test_module": "体验额度",
            "steps": ["批改1道题", "查看额度"],
            "expected_result": "体验额度显示为49",
            "priority": "P1",
        },
        {
            "id": "TC-011",
            "description": "体验额度初始额度与每次识别批改扣减",
            "test_module": "体验额度",
            "steps": ["确认初始额度50", "批改1道题", "查看剩余额度"],
            "expected_result": "初始额度50，完成1次识别或批改后显示49",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-004"}) in duplicate_pairs
    assert frozenset({"TC-005", "TC-006"}) in duplicate_pairs
    assert frozenset({"TC-007", "TC-008"}) in duplicate_pairs
    assert frozenset({"TC-010", "TC-011"}) in duplicate_pairs
    assert frozenset({"TC-007", "TC-009"}) not in duplicate_pairs
    assert frozenset({"TC-008", "TC-009"}) not in duplicate_pairs


def test_judge_rejects_latest_residual_duplicates_without_collapsing_distinct_comment_checks() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "验证习题本只包含拍照搜题进错题本的题目，不包含课内练习（快问快答、牛刀小试）的记录",
            "test_module": "习题本",
            "steps": ["进入当前教学周习题本页面", "查看题目列表"],
            "expected_result": "习题本中只显示拍照搜题进错题本的题目，课内练习的记录不会出现在列表中",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "验证习题本只收录拍照搜题进错题本的题目，不包含课内练习",
            "test_module": "习题本",
            "steps": ["进入习题本"],
            "expected_result": "习题本列表中只显示拍照搜题的那1道题，快问快答和牛刀小试的题目不出现",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "验证学习成长报告本周学习概览四个数据卡数值来源正确",
            "test_module": "学习成长报告（督导端）",
            "steps": ["查看本周学习概览区域的四个数值", "核对每个数据卡下方的来源标注"],
            "expected_result": "四个数据卡分别显示视频数、练习题数、错题数、知识点数，且每个卡下方标注来源",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "学习成长报告本周学习概览四个数据卡有来源标注",
            "test_module": "学习成长报告（督导端）",
            "steps": ["查看本周学习概览区域的四个数据卡", "检查每个数据卡下方的来源标注"],
            "expected_result": "四个数据卡各自显示来源说明，例如来自周末提升计划或课内练习",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "验证学习成长报告中督导评语的语音输入转文字功能可用",
            "test_module": "学习成长报告",
            "steps": ["点击语音输入按钮", "录音并查看评语编辑框"],
            "expected_result": "语音录制结束后，语音自动转换为文字并填入评语编辑框",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "验证学习成长报告中督导评语AI初稿结构包含肯定、表扬、指出、鼓励四个部分",
            "test_module": "学习成长报告",
            "steps": ["查看督导评语区域", "按段落分析内容"],
            "expected_result": "AI初稿包含肯定近期努力、表扬具体进步、指出待加强知识点、鼓励继续加油",
            "priority": "P2",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-004"}) in duplicate_pairs
    assert frozenset({"TC-005", "TC-006"}) not in duplicate_pairs


def test_judge_marks_generic_automation_template_expected_result_as_pending() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "题目详情页图片加载失败时显示图片无法展示文案",
            "test_module": "题目详情页",
            "steps": ["进入题目详情页", "观察图片区域"],
            "expected_result": "执行观察题目图片区域后，应跳转到目标页面，且页面路径与标题均与题目详情页图片加载失败时显示'图片无法展示'文案一致",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "体验额度耗尽后历史课程无法查看",
            "test_module": "体验额度",
            "steps": ["进入历史课程", "查看题目解析"],
            "expected_result": "执行尝试查看对应的题目解析后，响应状态码正确，且用户仅可访问体验额度耗尽后授权范围内页面或模块",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})

    assert judged.pending_count == 2
    assert all(item.pending_reason == "contains_pending_logic" for item in judged.cases)
    assert all(item.signals.vague_or_unconfirmed_hits for item in judged.cases)


def test_judge_rejects_review_status_color_duplicate_and_marks_vague_format_copy() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "序号栏颜色根据批改状态正确显示：正确绿色、待复核橙色、错误红色、需关注其他颜色",
            "test_module": "批改结果复核页面",
            "steps": ["查看题目列表中的序号方块颜色"],
            "expected_result": "正确显示绿色，待复核显示橙色，错误显示红色，需关注显示其他颜色",
            "priority": "P2",
        },
        {
            "id": "TC-002",
            "description": "批改结果复核序号栏颜色验证：正确为绿色、待复核为橙色、错误为红色、其他需关注为灰色",
            "test_module": "批改结果复核页面",
            "steps": ["进入复核页面", "观察全部标签下各题目的序号栏颜色"],
            "expected_result": "正确题目序号栏背景为绿色，待复核题目为橙色，错误题目为红色，需关注题目为灰色",
            "priority": "P2",
        },
        {
            "id": "TC-003",
            "description": "验证习题本标题格式为小学数学X周X习题本",
            "test_module": "习题本标题",
            "steps": ["打开习题本页面", "查看标题"],
            "expected_result": "习题本标题显示为小学数学3周3习题本或类似格式，其中X对应周数，符合描述格式",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "验证每周对比变化率显示",
            "test_module": "学习成长报告",
            "steps": ["查看每周对比区域"],
            "expected_result": "相等时显示持平或增加0%",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    pending_ids = {item.case_id for item in judged.cases if item.pending_reason == "contains_pending_logic"}
    assert {"TC-003", "TC-004"}.issubset(pending_ids)


def test_judge_rejects_bad_image_duplicate_and_keeps_manual_correction_directions() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "作业拍照批改：图片模糊全黑不完整时允许上传，批改后对应题目标记为待复核",
            "test_module": "作业拍照批改",
            "steps": ["拍摄模糊图片", "点击开始智能批改"],
            "expected_result": "图片成功上传并完成批改；复核页面中对应题目的序号栏颜色为橙色，待复核中显示该题",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "作业拍照批改上传模糊全黑不完整图片，允许上传且批改后标记待复核",
            "test_module": "作业拍照批改（督导端）",
            "steps": ["拍摄或导入异常图片", "点击开始智能批改", "检查复核状态"],
            "expected_result": "允许上传并执行批改；对应题目被标记为待复核橙色标签",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "批改结果复核单题判定正确后触发弹窗并同步学情",
            "test_module": "批改结果复核",
            "steps": ["点击判定正确按钮", "确认弹窗"],
            "expected_result": "该题状态变为正确，错题本移除该题，正确数增加，模型优化上报接口被调用",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "批改结果复核单题判定错误后触发弹窗并更正历史误判数据",
            "test_module": "批改结果复核",
            "steps": ["点击判定错误按钮", "确认弹窗"],
            "expected_result": "该题学情数据更正为错误，错题本新增该题，错误数增加，模型优化请求已上报",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "批改结果复核已判定错误的题目手动修正为正确后错题本自动移除该题",
            "test_module": "批改结果复核页面",
            "steps": ["点击判定正确并确认", "查看错题本"],
            "expected_result": "错题本中该题被移除，学情数据重新计算，历史误判记录被更正",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-005"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-004"}) not in duplicate_pairs
    assert frozenset({"TC-004", "TC-005"}) not in duplicate_pairs


def test_judge_uses_fact_profile_from_control_state() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Archived records appear in the active workbook",
            "test_module": "Workbook",
            "steps": ["Open workbook"],
            "expected_result": "Archived records are displayed",
            "priority": "P0",
        }
    ]
    control_state = {
        "source_meta": {
            "fact_profile": {
                "confirmed_facts": ["Archived records must not appear in the active workbook"],
                "forbidden_facts": ["Archived records must not appear in the active workbook"],
                "pending_items": [],
                "hard_flow_constraints": [],
                "reuse_risks": [],
            }
        }
    }

    judged = judge_cases(cases, {}, control_state=control_state)

    assert judged.reject_count == 1
    assert judged.cases[0].reject_reason == "violates_confirmed_fact"
    assert judged.cases[0].signals.confirmed_fact_violations


def test_judge_marks_vague_requirement_defined_copy_as_pending() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Workbook pending copy follows actual product design",
            "test_module": "Workbook",
            "steps": ["Open workbook"],
            "expected_result": "\u9875\u9762\u663e\u793a\u5982\"\u6682\u65e0\u5f85\u6279\u6539\u9898\u76ee\"\u6216\u9700\u6c42\u5b9a\u4e49\u7684\u6587\u6848",
            "priority": "P1",
        }
    ]

    judged = judge_cases(cases, {})

    assert judged.pending_count == 1
    assert judged.cases[0].pending_reason == "contains_pending_logic"
    assert judged.cases[0].signals.vague_or_unconfirmed_hits
    assert judged.cases[0].signals.pending_hits


def test_judge_marks_optional_design_copy_as_pending() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Calendar optionally supports week switching",
            "test_module": "Learning Plan",
            "steps": ["Open calendar"],
            "expected_result": "\u5de6\u53f3\u6ed1\u52a8\u6216\u70b9\u51fb\u65e5\u5386\u67e5\u770b\u662f\u5426\u652f\u6301\u5207\u6362\u5468\uff08\u5982\u679c\u8bbe\u8ba1\u5982\u6b64\uff09\uff1b\u82e5\u65e0\u5468\u5207\u6362\u529f\u80fd\u5219\u4ec5\u663e\u793a\u672c\u5468",
            "priority": "P1",
        }
    ]

    judged = judge_cases(cases, {})

    assert judged.pending_count == 1
    assert judged.cases[0].signals.vague_or_unconfirmed_hits


def test_judge_rejects_latest_residual_duplicate_scenarios() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "批改结果复核-筛选标签切换时题目列表正确更新",
            "test_module": "批改结果复核",
            "steps": ["点击全部标签", "点击待复核标签", "点击需关注标签"],
            "expected_result": "点击全部显示所有题目；点击待复核仅显示待复核题目；点击需关注仅显示需关注题目；右侧题量数字对应显示数量",
            "priority": "P2",
        },
        {
            "id": "TC-002",
            "description": "批改结果复核页面-筛选标签切换：点击'待复核'后列表只显示被标记为待复核的题目",
            "test_module": "批改结果复核页面",
            "steps": ["在顶部筛选标签中点击'待复核'", "查看题目列表"],
            "expected_result": "列表只显示标记为'待复核'的那道题，右侧题量显示为1，已隐藏正确和错误的题目",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "周末提升计划第三步-系统重新生成薄弱知识点后课程切片同步重新生成",
            "test_module": "周末提升计划",
            "steps": ["后端触发薄弱知识点重新生成", "学生端返回第三步查看切片列表"],
            "expected_result": "课程切片列表更新为与新薄弱知识点对应的切片，旧切片被替换或删除",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "周末提升计划-第三步：系统重新生成薄弱知识点后，左侧导航树和右侧切片同步更新",
            "test_module": "周末提升计划",
            "steps": ["模拟督导重新批改错题，触发薄弱知识点变化", "观察左侧导航树和右侧切片内容"],
            "expected_result": "左侧导航树更新为新课程切片列表，右侧内容自动切换到第一个切片，提示用户课程已更新",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "学习成长报告-H5页面为只读模式，隐藏编辑和管理操作",
            "test_module": "学习成长报告",
            "steps": ["使用手机浏览器打开H5链接", "观察页面是否有编辑、删除、修改督导评语等管理操作按钮"],
            "expected_result": "H5页面无任何编辑或管理入口，所有数据均为只读展示",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "学习成长报告-H5页面只读且隐藏编辑管理操作",
            "test_module": "学习成长报告",
            "steps": ["使用手机浏览器或微信打开H5链接", "检查页面是否包含编辑按钮、管理入口"],
            "expected_result": "页面仅展示报告内容，无编辑按钮、无管理入口，所有操作按钮均不可见或不可交互，为纯只读模式",
            "priority": "P2",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-004"}) in duplicate_pairs
    assert frozenset({"TC-005", "TC-006"}) in duplicate_pairs


def test_judge_rejects_student_essay_residual_duplicate_intents() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "\u6295\u7a3f\u5931\u8d25\u540e\u5c55\u793a\u5931\u8d25\u539f\u56e0",
            "test_module": "\u4f5c\u6587\u6279\u6539",
            "steps": ["\u6253\u5f00\u6295\u7a3f\u8be6\u60c5", "\u67e5\u770b\u5ba1\u6838\u7ed3\u679c"],
            "expected_result": "\u9875\u9762\u5c55\u793a\u672a\u901a\u8fc7\u7684\u5931\u8d25\u539f\u56e0",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "\u4f5c\u54c1\u5ba1\u6838\u9a73\u56de\u65f6\u53ef\u67e5\u770b\u9a73\u56de\u539f\u56e0",
            "test_module": "\u4f5c\u6587\u6279\u6539-\u6295\u7a3f",
            "steps": ["\u8fdb\u5165\u6211\u7684\u4f5c\u6587", "\u70b9\u51fb\u672a\u901a\u8fc7\u4f5c\u54c1"],
            "expected_result": "\u672a\u901a\u8fc7\u4f5c\u54c1\u663e\u793a\u5ba1\u6838\u5931\u8d25\u539f\u56e0",
            "priority": "P2",
        },
        {
            "id": "TC-003",
            "description": "\u666e\u901a\u7528\u6237\u7b2c\u4e00\u8bfe\u53ef\u8bd5\u5b66",
            "test_module": "\u8bfe\u7a0b\u5217\u8868",
            "steps": ["\u666e\u901a\u7528\u6237\u8fdb\u5165\u8bfe\u7a0b\u5217\u8868", "\u70b9\u51fb\u7b2c\u4e00\u8bfe"],
            "expected_result": "\u7b2c\u4e00\u8bfe\u53ef\u8fdb\u5165\uff0c\u4e0d\u8df3\u4f1a\u5458\u4e2d\u5fc3",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "\u666e\u901a\u7528\u6237\u53ea\u6709\u7b2c\u4e00\u8bfe\u53ef\u514d\u8d39\u8fdb\u5165",
            "test_module": "\u8bfe\u7a0b\u6743\u9650",
            "steps": ["\u666e\u901a\u7528\u6237\u6253\u5f00\u8bfe\u7a0b", "\u5206\u522b\u70b9\u51fb\u7b2c\u4e00\u8bfe\u548c\u7b2c\u4e8c\u8bfe"],
            "expected_result": "\u7b2c\u4e00\u8bfe\u8fdb\u5165\u8bd5\u5b66\uff0c\u5176\u4ed6\u8bfe\u7a0b\u8df3\u8f6c\u4f1a\u5458\u4e2d\u5fc3",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "\u4e0b\u8f7dPDF\u540e\u5185\u5bb9\u4e0e\u6279\u6539\u7ed3\u679c\u4e00\u81f4",
            "test_module": "\u8d44\u6599\u4e0b\u8f7d",
            "steps": ["\u70b9\u51fbPDF\u4e0b\u8f7d", "\u6253\u5f00\u6587\u4ef6\u68c0\u67e5\u5185\u5bb9"],
            "expected_result": "PDF\u6587\u4ef6\u5305\u542b\u6279\u6539\u7ed3\u679c\u4e3b\u8981\u5185\u5bb9",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "\u8d44\u6599PDF\u4e0b\u8f7d\u5185\u5bb9\u6821\u9a8c",
            "test_module": "\u4e0b\u8f7d\u8d44\u6599",
            "steps": ["\u5728\u6279\u6539\u7ed3\u679c\u9875\u4e0b\u8f7dPDF", "\u6838\u5bf9PDF\u5185\u5bb9"],
            "expected_result": "\u4e0b\u8f7dPDF\u5185\u5bb9\u4e0e\u9875\u9762\u6279\u6539\u7ed3\u679c\u4fdd\u6301\u4e00\u81f4",
            "priority": "P2",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-004"}) in duplicate_pairs
    assert frozenset({"TC-005", "TC-006"}) in duplicate_pairs


def test_judge_rejects_student_essay_latest_duplicate_clusters() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "同一主题第5次批改后拦截继续批改",
            "test_module": "作文批改",
            "steps": ["连续批改同一主题5次", "再次点击去批改"],
            "expected_result": "系统提示同一主题批改次数已达上限",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "同一作文主题5次批改上限校验",
            "test_module": "作文批改",
            "steps": ["同一主题已完成5次批改", "上传图片并点击去批改"],
            "expected_result": "不再发起批改请求并展示次数上限提示",
            "priority": "P2",
        },
        {
            "id": "TC-003",
            "description": "作文圈精选作品按精选排序展示",
            "test_module": "作文圈",
            "steps": ["后台设置精选作品", "打开作文圈精选列表"],
            "expected_result": "精选作品进入作文圈精选列表并按排序规则展示",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "作文圈精选列表权重排序校验",
            "test_module": "作文圈",
            "steps": ["设置多个精选作品", "查看精选列表顺序"],
            "expected_result": "精选列表顺序与排序规则一致",
            "priority": "P2",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate_pairs = {
        frozenset({item.case_id, item.signals.duplicate_of_case_id})
        for item in judged.cases
        if item.signals.is_semantic_duplicate
    }

    assert frozenset({"TC-001", "TC-002"}) in duplicate_pairs
    assert frozenset({"TC-003", "TC-004"}) in duplicate_pairs


def test_judge_marks_generic_template_expected_result_as_pending() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "学习成长报告-周一到周五作业摘片无图片时显示文案",
            "test_module": "学习成长报告（督导端）",
            "steps": ["查看'周一到周五作业摘片'区块", "观察显示内容"],
            "expected_result": "执行观察显示内容后，应完整显示学习成长报告-周一到周五作业摘片无图片时显示文案关键字段，且字段值与输入/后端数据一致",
            "priority": "P2",
        }
    ]

    judged = judge_cases(cases, {})

    assert judged.pending_count == 1
    assert judged.cases[0].pending_reason == "contains_pending_logic"
    assert judged.cases[0].signals.vague_or_unconfirmed_hits


def test_postprocess_deduplicate_removes_near_duplicate_validation_targets() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "习题本标题格式“小学数学X周X习题本”验证",
            "test_module": "习题本",
            "steps": ["1. 进入习题本", "2. 查看标题"],
            "test_input": "无",
            "expected_result": "标题显示为“小学数学3周习题本”",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "验证习题本标题格式为'小学数学X周X习题本'，其中X为当前教学周数",
            "test_module": "习题本",
            "steps": ["1. 进入习题本页面", "2. 观察导航栏标题"],
            "test_input": "无",
            "expected_result": "标题显示为'小学数学5周习题本'",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "题目详情页无解析时展示空文案",
            "test_module": "题目详情页",
            "steps": ["1. 进入题目详情页", "2. 查看解析模块"],
            "test_input": "无",
            "expected_result": "题目解析模块显示文案'暂无解析内容'",
            "priority": "P2",
        },
    ]

    deduped = deduplicate_test_cases(cases)

    assert [item["id"] for item in deduped] == ["TC-001", "TC-002", "TC-003"]
