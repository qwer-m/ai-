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
            "description": "记录列表筛选开关默认关闭，开启后只显示失败记录",
            "test_module": "记录列表",
            "steps": ["打开记录列表", "开启失败记录筛选开关"],
            "test_input": "筛选开关开启",
            "expected_result": "过滤成功记录，仅显示失败记录",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "记录列表过滤开关默认关闭，开启筛选后仅展示失败记录",
            "test_module": "记录列表",
            "steps": ["进入记录列表", "打开失败记录过滤开关"],
            "test_input": "过滤开关开启",
            "expected_result": "筛选成功记录后，列表只展示失败记录",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "记录列表只看失败开关默认关闭，开启过滤后只显示失败记录",
            "test_module": "记录列表",
            "steps": ["打开记录列表", "启用只看失败记录开关"],
            "test_input": "只看失败记录",
            "expected_result": "过滤成功记录，列表仅保留失败记录",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})

    assert judged.reject_count == 2
    duplicate_rows = [item for item in judged.cases if item.signals.is_semantic_duplicate]
    assert len(duplicate_rows) == 2
    assert len([item for item in judged.cases if item.status == "PASS"]) == 1
    assert all(str(item.reject_reason or "").startswith("semantic_duplicate:TC-") for item in duplicate_rows)


def test_judge_does_not_collapse_registered_scenarios_across_modules() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "订单列表筛选开关开启后仅显示失败订单",
            "test_module": "订单列表",
            "steps": ["打开订单列表", "开启失败订单过滤开关"],
            "test_input": "失败订单筛选",
            "expected_result": "列表过滤成功订单，仅显示失败订单",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "用户列表筛选开关开启后仅显示禁用用户",
            "test_module": "用户列表",
            "steps": ["打开用户列表", "开启禁用用户过滤开关"],
            "test_input": "禁用用户筛选",
            "expected_result": "列表过滤启用用户，仅显示禁用用户",
            "priority": "P1",
        },
    ]

    judged = judge_cases(cases, {})
    assert judged.reject_count == 0
    assert all(not item.signals.is_semantic_duplicate for item in judged.cases)


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


def test_judge_rejects_generic_popup_quota_and_refresh_duplicates() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "控制台首次进入时弹出使用说明弹窗",
            "test_module": "控制台",
            "steps": ["首次打开控制台"],
            "expected_result": "页面首次加载后弹出使用说明弹窗",
            "priority": "P1",
        },
        {
            "id": "TC-002",
            "description": "验证控制台首次加载时展示使用说明弹窗",
            "test_module": "控制台",
            "steps": ["首次进入控制台", "观察弹窗"],
            "expected_result": "首次进入时弹出使用说明弹窗且可关闭",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "接口调用额度达到上限后拦截继续请求并提示次数耗尽",
            "test_module": "接口额度",
            "steps": ["耗尽接口调用次数", "再次发起请求"],
            "expected_result": "请求被拦截并提示调用额度已耗尽",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "接口调用次数耗尽后限制后续请求并显示额度上限提示",
            "test_module": "接口额度",
            "steps": ["将调用次数用完", "继续调用接口"],
            "expected_result": "后续请求被拦截，页面提示接口额度耗尽",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "后台更新记录后列表静默刷新且无弹窗",
            "test_module": "记录列表",
            "steps": ["后台更新记录", "等待列表刷新"],
            "expected_result": "列表数据静默刷新，不显示弹窗",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "记录被后台更新后页面无弹窗并静默刷新列表数据",
            "test_module": "记录列表",
            "steps": ["触发后台数据更新", "观察列表"],
            "expected_result": "页面无弹窗，列表数据完成静默刷新",
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


def test_judge_rejects_generic_title_format_duplicate_and_marks_vague_copy() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "验证导出文件标题格式为项目名加日期",
            "test_module": "文件导出",
            "steps": ["导出文件", "查看文件标题"],
            "expected_result": "导出文件标题按项目名加日期的格式显示",
            "priority": "P2",
        },
        {
            "id": "TC-002",
            "description": "验证导出文件标题格式使用项目名加日期",
            "test_module": "文件导出",
            "steps": ["执行文件导出", "检查文件标题"],
            "expected_result": "文件标题使用项目名加日期的格式展示",
            "priority": "P2",
        },
        {
            "id": "TC-003",
            "description": "验证通知标题格式",
            "test_module": "通知中心",
            "steps": ["打开通知中心", "查看通知标题"],
            "expected_result": "通知标题显示为名称加日期或类似格式",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "验证环比变化率显示",
            "test_module": "统计面板",
            "steps": ["查看环比变化区域"],
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


def test_judge_rejects_generic_filter_refresh_and_readonly_duplicates() -> None:
    cases = [
        {
            "id": "TC-001",
            "description": "记录列表筛选开关切换后仅显示失败记录",
            "test_module": "记录列表",
            "steps": ["打开记录列表", "开启失败记录过滤开关"],
            "expected_result": "列表过滤成功记录，只显示失败记录",
            "priority": "P2",
        },
        {
            "id": "TC-002",
            "description": "记录列表过滤开关开启后只展示失败记录",
            "test_module": "记录列表",
            "steps": ["进入记录列表", "启用失败记录筛选开关"],
            "expected_result": "成功记录被过滤，列表仅展示失败记录",
            "priority": "P1",
        },
        {
            "id": "TC-003",
            "description": "后台更新记录后列表静默刷新且无弹窗",
            "test_module": "记录同步",
            "steps": ["后台更新记录", "等待列表刷新"],
            "expected_result": "记录列表静默刷新，页面无弹窗",
            "priority": "P1",
        },
        {
            "id": "TC-004",
            "description": "记录被后台更新后页面无弹窗并静默刷新列表",
            "test_module": "记录同步",
            "steps": ["触发后台记录更新", "观察列表数据"],
            "expected_result": "页面无弹窗，记录列表完成静默刷新",
            "priority": "P1",
        },
        {
            "id": "TC-005",
            "description": "分享页面为只读模式并隐藏编辑入口",
            "test_module": "分享页面",
            "steps": ["打开分享页面", "检查编辑入口"],
            "expected_result": "页面只读展示，编辑入口隐藏且内容不可编辑",
            "priority": "P1",
        },
        {
            "id": "TC-006",
            "description": "分享页面保持只读并隐藏所有编辑操作",
            "test_module": "分享页面",
            "steps": ["进入分享页面", "检查编辑按钮"],
            "expected_result": "页面内容不可编辑，编辑按钮和管理入口均隐藏",
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
