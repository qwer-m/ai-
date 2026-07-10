from __future__ import annotations

from modules.testing.test_generation_components.judge.test_case_judge import judge_case, judge_cases
from modules.testing.test_generation_components.judge.test_case_repairer import repair_cases
from modules.testing.test_generation_components.postprocess.json_repair import deduplicate_test_cases


def test_judge_keeps_distinct_general_quota_limit_subflows() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Post quota - extra posting chance after learning more than 15 minutes",
            "test_module": "Post quota",
            "steps": [
                "Use all daily base quota",
                "Learn in app more than 15 minutes",
                "Tap create post",
            ],
            "expected_result": "User can enter editor and publish by consuming the extra posting chance",
            "priority": "P2",
        },
        {
            "id": "TC-002",
            "description": "Post quota - daily base quota exhausted shows toast when creating post or reply",
            "test_module": "Post quota",
            "steps": [
                "Use all 5 daily post and reply chances",
                "Tap create post",
                "Tap reply",
            ],
            "expected_result": "System shows quota exhausted toast and blocks editor entry",
            "priority": "P2",
        },
        {
            "id": "TC-003",
            "description": "Post quota - forum browsing time does not count as learning time",
            "test_module": "Post quota",
            "steps": [
                "Use all daily quota",
                "Browse forum more than 15 minutes",
                "Tap create post",
            ],
            "expected_result": "Forum browsing time is not counted as learning time and quota exhausted toast still appears",
            "priority": "P2",
        },
    ]

    judged = judge_cases(cases, {})

    assert judged.reject_count == 0
    assert {item.case_id: item.status for item in judged.cases} == {
        "TC-001": "PASS",
        "TC-002": "PASS",
        "TC-003": "PASS",
    }


def test_judge_still_rejects_exact_general_quota_duplicate() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Post quota - daily base quota exhausted shows toast when creating post or reply",
            "test_module": "Post quota",
            "steps": ["Use all daily quota", "Tap create post"],
            "expected_result": "System shows quota exhausted toast and blocks editor entry",
            "priority": "P2",
        },
        {
            "id": "TC-002",
            "description": "Post quota - daily base quota exhausted shows toast when creating post or reply",
            "test_module": "Post quota",
            "steps": ["Use all daily quota", "Tap create post"],
            "expected_result": "System shows quota exhausted toast and blocks editor entry",
            "priority": "P2",
        },
    ]

    judged = judge_cases(cases, {})

    duplicate = next(item for item in judged.cases if item.case_id == "TC-002")
    assert judged.reject_count == 1
    assert duplicate.reject_reason == "semantic_duplicate:TC-001"
    assert duplicate.signals.is_semantic_duplicate is True


def test_judge_repairer_does_not_append_untyped_batch_gap_cases() -> None:
    semantics = {
        "hard_flow_constraints": [
            "二轮复习课程详情页仅保留学&练流程"
        ],
        "reuse_risks": [
            "打印弹窗保留教材和答案双选项"
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


def test_judge_rejects_duplicate_case_when_fields_use_aliases() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "错题推荐列表按知识点筛选后展示推荐题",
            "test_module": "错题推荐",
            "steps": ["进入错题推荐页", "选择知识点筛选条件", "查看推荐题列表"],
            "test_input": "学生存在同一知识点下的错题记录",
            "expected_result": "列表只展示该知识点关联的推荐题，题目数量与推荐结果一致",
            "priority": "P0",
        },
        {
            "caseId": "TC-002",
            "title": "错题推荐列表按知识点筛选后展示推荐题",
            "testModule": "错题推荐",
            "testSteps": ["打开错题推荐页面", "按知识点执行筛选", "检查推荐题列表"],
            "testInput": "学生存在同一知识点下的错题记录",
            "expectedResult": "列表只展示该知识点关联的推荐题，题目数量与推荐结果一致",
            "priority": "P0",
        },
    ]

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
            "description": "验证学员回答答非所问时，准确性直接记为0分",
            "test_module": "学员端AI评分",
            "steps": [
                "1. AI提问具体数学题",
                "2. 学员输入与题目无关的内容",
                "3. 查看准确性分数",
            ],
            "test_input": "今天天气很好",
            "expected_result": "准确性分数为0分，其他维度正常评分",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "验证学员端回答答非所问时准确性自动0分，且总分按规则计算",
            "test_module": "学员端AI讲错题评分",
            "steps": [
                "1. 在AI追问后输入与问题无关的回答",
                "2. 完成交互后查看评分明细",
            ],
            "test_input": "今天天气不错",
            "expected_result": "准确性维度得0分，系统标注答非所问",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})
    duplicate = next(item for item in judged.cases if item.case_id == "TC-002")

    assert duplicate.status == "REJECT"
    assert duplicate.reject_reason == "semantic_duplicate:TC-001"
    assert duplicate.signals.is_semantic_duplicate is True
    assert duplicate.signals.duplicate_of_case_id == "TC-001"


def test_judge_does_not_use_broad_schedule_registry_families_as_duplicate_rules() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "首页本周进度-有课程时个人进度完整展示",
            "test_module": "首页-本周进度模块",
            "steps": ["进入学员首页", "查看本周进度模块个人进度区域"],
            "test_input": "本周5节课，已完成3节",
            "expected_result": "显示已学60%、已完成课程数3/5、文案为继续学习2节课完成本周任务",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "首页本周进度-学习时长排行榜Top5展示及称号图标",
            "test_module": "首页-本周进度模块",
            "steps": ["进入学员首页", "查看学习时长排行榜区域"],
            "test_input": "门店本周8名学员有学习记录",
            "expected_result": "仅展示Top5学员，第1名显示学习恒星图标，第2-3名显示勤勉彗星图标，第4-5名显示奋进新星图标",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "首页本周进度-无课程时空状态展示",
            "test_module": "首页-本周进度模块",
            "steps": ["进入学员首页", "查看本周进度模块"],
            "test_input": "学员本周无任何课程",
            "expected_result": "展示本周暂无学习计划空状态，不显示进度百分比和排行榜数据",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})

    assert judged.reject_count == 0
    assert {item.case_id: item.status for item in judged.cases} == {
        "TC-001": "PASS",
        "TC-002": "PASS",
        "TC-003": "PASS",
    }


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


def test_judge_does_not_apply_temporal_shutdown_fact_as_global_enter_ban() -> None:
    control_state = {
        "source_meta": {
            "fact_profile": {
                "confirmed_facts": ["时间：6月19日-7月10日23:59，结束后入口关闭且课程不可进入"],
                "hard_flow_constraints": ["时间：6月19日-7月10日23:59，结束后入口关闭且课程不可进入"],
            }
        }
    }
    cases = [
        {
            "id": "TC-001",
            "description": "提交后5秒进度条进入测评结果页",
            "test_module": "测评提交",
            "steps": ["完成测评并点击提交"],
            "expected_result": "5秒后进度条完成并跳转至测评结果页",
            "priority": "P0",
        },
        {
            "id": "TC-002",
            "description": "会员用户点击课程可直接学习",
            "test_module": "课程列表页",
            "steps": ["会员用户点击课程卡片"],
            "expected_result": "直接进入课程学习页面，不跳转至5天SVIP购买页",
            "priority": "P0",
        },
        {
            "id": "TC-003",
            "description": "活动结束后课程不可进入",
            "test_module": "活动上下线",
            "steps": ["将系统时间切到7月10日23:59后", "访问活动课程入口"],
            "expected_result": "入口关闭，课程不可进入，用户无法访问原复习课程内容",
            "priority": "P0",
        },
    ]

    judged = judge_cases(cases, {}, control_state=control_state)

    assert judged.reject_count == 0
    assert all(not item.signals.confirmed_fact_violations for item in judged.cases)


def test_judge_rejects_positive_access_inside_temporal_shutdown_scope() -> None:
    control_state = {
        "source_meta": {
            "fact_profile": {
                "confirmed_facts": ["时间：6月19日-7月10日23:59，结束后入口关闭且课程不可进入"],
            }
        }
    }
    case = {
        "id": "TC-001",
        "description": "活动结束后入口仍可点击进入课程",
        "test_module": "活动上下线",
        "steps": ["将系统时间切到7月10日23:59后", "点击活动入口"],
        "expected_result": "活动结束后仍可进入课程学习页面",
        "priority": "P0",
    }

    judged = judge_case(case, {}, control_state=control_state)

    assert judged.status == "REJECT"
    assert judged.reject_reason == "violates_confirmed_fact"
    assert judged.signals.confirmed_fact_violations


def test_judge_marks_vague_requirement_defined_copy_as_pending() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Workbook pending copy follows actual product design",
            "test_module": "Workbook",
            "steps": ["Open workbook"],
            "expected_result": "页面显示如\"暂无待批改题目\"或需求定义的文案",
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
            "expected_result": "左右滑动或点击日历查看是否支持切换周（如果设计如此）；若无周切换功能则仅显示本周",
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
            "description": "投稿失败后展示失败原因",
            "test_module": "作文批改",
            "steps": ["打开投稿详情", "查看审核结果"],
            "expected_result": "页面展示未通过的失败原因",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "作品审核驳回时可查看驳回原因",
            "test_module": "作文批改-投稿",
            "steps": ["进入我的作文", "点击未通过作品"],
            "expected_result": "未通过作品显示审核失败原因",
            "priority": "P2",
        },
        {
            "id": "TC-003",
            "description": "普通用户第一课可试学",
            "test_module": "课程列表",
            "steps": ["普通用户进入课程列表", "点击第一课"],
            "expected_result": "第一课可进入，不跳会员中心",
            "priority": "P0",
        },
        {
            "id": "TC-004",
            "description": "普通用户只有第一课可免费进入",
            "test_module": "课程权限",
            "steps": ["普通用户打开课程", "分别点击第一课和第二课"],
            "expected_result": "第一课进入试学，其他课程跳转会员中心",
            "priority": "P0",
        },
        {
            "id": "TC-005",
            "description": "下载PDF后内容与批改结果一致",
            "test_module": "资料下载",
            "steps": ["点击PDF下载", "打开文件检查内容"],
            "expected_result": "PDF文件包含批改结果主要内容",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "资料PDF下载内容校验",
            "test_module": "下载资料",
            "steps": ["在批改结果页下载PDF", "核对PDF内容"],
            "expected_result": "下载PDF内容与页面批改结果保持一致",
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


def test_postprocess_deduplicate_accepts_alias_fields_for_structural_key() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "Course schedule save flow",
            "test_module": "Schedule",
            "steps": ["open schedule page", "save plan"],
            "test_input": "valid course and time",
            "expected_result": "plan is saved",
        },
        {
            "caseId": "TC-002",
            "title": "Course schedule save flow",
            "testModule": "Schedule",
            "testSteps": ["open schedule page", "save plan"],
            "testInput": "valid course and time",
            "expectedResult": "plan is saved",
        },
    ]

    deduped = deduplicate_test_cases(cases)

    assert [item.get("id") or item.get("caseId") for item in deduped] == ["TC-001"]
