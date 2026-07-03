"""Build workflow-blueprint learning samples from human-final cases."""

from __future__ import annotations

import re
from typing import Any

from ..postprocess.case_access import case_priority, case_text_parts
from .final_case_parsing import _text


def _case_text(case: dict[str, Any]) -> str:
    return " ".join(
        case_text_parts(
            case,
            ("description", "test_module", "preconditions", "steps", "test_input", "expected_result"),
            dedupe=False,
        )
    ).strip()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _priority(case: dict[str, Any]) -> str:
    value = case_priority(case) or str(case.get("model_priority") or "P2").strip().upper()
    return value if value in {"P0", "P1", "P2"} else "P2"


def _workflow_actor(case: dict[str, Any]) -> str:
    explicit = str(case.get("role") or case.get("actor") or "").strip().lower()
    if explicit == "teacher":
        return "supervisor"
    if explicit in {"admin", "supervisor", "student", "member", "student_free"}:
        return explicit
    text = _case_text(case).lower()
    if any(token in text for token in ("admin", "后台", "管理员", "审核")):
        return "admin"
    if any(token in text for token in ("supervisor", "督导", "老师", "教师")):
        return "supervisor"
    return "student"


def _workflow_step_keywords(case: dict[str, Any]) -> list[str]:
    raw = " ".join(
        [
            _text(case.get("test_module")),
            _text(case.get("description")),
            _text(case.get("expected_result")),
        ]
    )
    tokens = [
        token.strip()
        for token in re.split(r"[\s,，。；;、|/]+", raw)
        if len(token.strip()) >= 2
    ]
    return list(dict.fromkeys(tokens))[:8]


def _workflow_transition_payload(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("workflow_transition")
    return dict(nested) if isinstance(nested, dict) else {}


def _workflow_stage_kind(case: dict[str, Any]) -> str:
    # Use the executed action surface first. Expected results often mention the
    # next page and would otherwise shift a configure step into preview/display.
    text = " ".join(
        _text(case.get(key))
        for key in ("description", "test_module", "steps", "test_input")
        if case.get(key) is not None
    ).lower()
    if _contains_any(text, ("学习完成", "完成学习", "进度更新", "进度同步", "completion", "progress sync")):
        return "completion_sync"
    if _contains_any(
        text,
        (
            "首页",
            "本周任务",
            "学习计划页",
            "下游",
            "同步展示",
            "状态同步",
            "paid status",
            "order status",
            "visible",
            "reflect",
            "sync",
            "学生端",
            "学员端",
            "书房端",
            "用户端",
            "展示一致",
            "同步展示",
            "可见",
            "student side",
            "learner side",
        ),
    ):
        return "downstream_visibility"
    if _contains_any(text, ("保存", "提交", "确认", "发布", "支付", "save", "submit", "commit", "publish", "payment")):
        return "commit"
    if _contains_any(text, ("预览", "确认前", "preview", "review")):
        return "preview"
    if _contains_any(text, ("新增", "创建", "选课", "选择", "设置", "配置", "编辑", "create", "select", "set", "configure", "edit")):
        return "configure"
    if _contains_any(text, ("点击学习", "进入课程", "打开课程", "open course", "start learning", "click course")):
        return "consume"
    if _contains_any(text, ("进入", "访问", "打开", "入口", "enter", "access", "open")):
        return "entry"
    return "unknown"


def _workflow_scope(case: dict[str, Any], stage_kind: str) -> str:
    text = _case_text(case).lower()
    if _contains_any(text, ("排课", "新增计划", "编辑计划", "schedule", "lesson plan")):
        return "schedule_plan"
    if _contains_any(text, ("首页", "本周任务", "homepage", "home page", "weekly task")):
        return "student_home_weekly_task"
    if _contains_any(text, ("学习计划", "learning plan")):
        return "learning_plan"
    if _contains_any(text, ("课程学习", "点击学习", "进入课程", "course learning", "open course")):
        return "course_learning"
    if _contains_any(text, ("checkout", "payment", "支付", "订单", "order")):
        return "checkout_order"
    module = re.sub(r"[^a-z0-9]+", "_", _text(case.get("test_module")).lower()).strip("_")
    return module[:48] or ("workflow" if stage_kind == "unknown" else f"workflow_{stage_kind}")


def _workflow_action(case: dict[str, Any], *, stage_kind: str, scope: str) -> str:
    transition = _workflow_transition_payload(case)
    explicit = _text(case.get("action") or transition.get("action"))
    if explicit:
        return explicit[:160]
    text = _case_text(case).lower()
    if stage_kind == "configure" and _contains_any(text, ("选课", "选择课程", "select course")):
        return "select_courses"
    if stage_kind == "configure" and _contains_any(text, ("日期", "时间", "上课时间", "date", "time")):
        return "configure_schedule_time"
    if stage_kind == "preview":
        return "go_to_preview"
    if stage_kind == "commit" and scope == "schedule_plan":
        return "save_plan"
    if stage_kind == "commit" and _contains_any(text, ("payment", "支付")):
        return "submit_payment"
    if stage_kind == "downstream_visibility" and scope == "student_home_weekly_task":
        return "verify_weekly_task_visible"
    if stage_kind == "consume" and scope == "course_learning":
        return "open_course_learning"
    if stage_kind == "completion_sync":
        return "complete_learning_and_sync_progress"
    return f"{stage_kind}_{scope}"[:160]


def _workflow_target_state(case: dict[str, Any], *, stage_kind: str, scope: str) -> str:
    transition = _workflow_transition_payload(case)
    explicit = _text(case.get("target_state") or case.get("state_out") or transition.get("target_state"))
    if explicit:
        return explicit[:120]
    text = _case_text(case).lower()
    if stage_kind == "configure" and _contains_any(text, ("选课", "选择课程", "select course")):
        return "schedule_courses_selected"
    if stage_kind == "configure" and _contains_any(text, ("日期", "时间", "上课时间", "date", "time")):
        return "schedule_time_configured"
    suffix_by_kind = {
        "entry": "entry_ready",
        "configure": "configured",
        "preview": "preview_ready",
        "commit": "saved",
        "downstream_visibility": "visible",
        "consume": "opened",
        "completion_sync": "progress_synced",
    }
    return f"{scope}_{suffix_by_kind.get(stage_kind, 'ready')}"[:120]


def _workflow_candidate(case: dict[str, Any], *, explicit_main_smoke: bool) -> tuple[bool, str]:
    transition = _workflow_transition_payload(case)
    text = _case_text(case).lower()
    path_type = str(case.get("path_type") or transition.get("path_type") or "").strip().lower()
    if path_type and path_type != "positive":
        return False, "negative_path"
    if case.get("blocking") is True or transition.get("blocking") is True:
        return False, "blocking_path"
    if case.get("destructive") is True or transition.get("destructive") is True:
        return False, "destructive_path"
    if transition.get("can_advance_main_flow") is False:
        return False, "non_advancing_path"
    if _contains_any(text, ("埋点", "pv", "uv", "tracking", "analytics")):
        return False, "analytics"
    if _contains_any(text, ("权限", "无权限", "越权", "permission", "forbidden", "unauthorized")):
        return False, "permission"
    if _contains_any(text, ("删除", "下架", "归档", "作废", "delete", "remove", "archive", "unpublish")):
        return False, "destructive_action"
    if _contains_any(
        text,
        (
            "失败",
            "异常",
            "超时",
            "错误",
            "拒绝",
            "不可",
            "置灰",
            "冲突",
            "上限",
            "下限",
            "空状态",
            "无数据",
            "failed",
            "failure",
            "timeout",
            "error",
            "invalid",
            "blocked",
            "cannot",
            "empty",
            "boundary",
        ),
    ):
        return False, "negative_or_boundary"
    stage_kind = _workflow_stage_kind(case)
    if stage_kind == "unknown":
        return False, "unknown_stage"
    if not explicit_main_smoke and _contains_any(text, ("文案", "样式", "布局", "颜色", "排序", "筛选", "标签", "copy", "layout", "sorting", "filter")):
        return False, "display_only"
    return True, stage_kind


_WORKFLOW_STAGE_ORDER = (
    "entry",
    "configure",
    "preview",
    "commit",
    "downstream_visibility",
    "consume",
    "completion_sync",
)
_WORKFLOW_STAGE_LIMITS = {
    "entry": 1,
    "configure": 3,
    "preview": 1,
    "commit": 1,
    "downstream_visibility": 2,
    "consume": 1,
    "completion_sync": 1,
}


def _workflow_selection_score(
    case: dict[str, Any],
    *,
    stage_kind: str,
    original_index: int,
) -> tuple[int, int, int, int, int]:
    text = _case_text(case).lower()
    priority_score = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(_priority(case), 0)
    transition = _workflow_transition_payload(case)
    explicit_main = int(
        str(case.get("execution_group") or "").strip() == "main_smoke"
        or str(case.get("chain_id") or "").strip() == "main_smoke_chain"
        or transition.get("main_path_step") is True
    )
    assertion_score = 1 if _text(case.get("expected_result")) else 0
    step_score = 1 if _text(case.get("steps")) else 0
    semantic_score = 0
    if stage_kind == "downstream_visibility":
        if _contains_any(
            text,
            (
                "学生端",
                "学员端",
                "书房端",
                "用户端",
                "student side",
                "learner side",
            ),
        ):
            semantic_score += 3
        if _contains_any(
            text,
            (
                "展示一致",
                "同步展示",
                "一致",
                "consistent",
                "sync",
                "reflect",
            ),
        ):
            semantic_score += 2
        if _contains_any(
            text,
            (
                "排行榜",
                "卡片可滑动",
                "样式",
                "布局",
                "ranking",
                "layout",
                "style",
            ),
        ):
            semantic_score -= 2
    if stage_kind == "configure":
        if _contains_any(
            text,
            (
                "新增",
                "选课",
                "选择",
                "选时间",
                "设置",
                "配置",
                "下一步",
                "添加",
                "create",
                "select",
                "configure",
                "next",
                "add",
            ),
        ):
            semantic_score += 2
        if _contains_any(
            text,
            (
                "查看",
                "展示",
                "view",
                "display",
            ),
        ):
            semantic_score -= 1
    stage_score = {
        "commit": 5,
        "downstream_visibility": 4,
        "consume": 4,
        "completion_sync": 4,
        "preview": 3,
        "configure": 2,
        "entry": 1,
    }.get(stage_kind, 0)
    return (
        explicit_main,
        priority_score,
        stage_score,
        semantic_score + assertion_score + step_score,
        -int(original_index),
    )


def _stage_ordered_workflow_cases(
    accepted: list[tuple[int, dict[str, Any], str]]
) -> list[tuple[dict[str, Any], str]]:
    buckets: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    for original_index, case, stage_kind in accepted:
        buckets.setdefault(stage_kind, []).append((original_index, case, stage_kind))

    selected: list[tuple[int, dict[str, Any], str]] = []
    for stage_kind in _WORKFLOW_STAGE_ORDER:
        bucket = buckets.get(stage_kind) or []
        if not bucket:
            continue
        ranked = sorted(
            bucket,
            key=lambda item: _workflow_selection_score(
                item[1],
                stage_kind=item[2],
                original_index=item[0],
            ),
            reverse=True,
        )
        picked = ranked[: int(_WORKFLOW_STAGE_LIMITS.get(stage_kind, 1))]
        selected.extend(sorted(picked, key=lambda item: item[0]))
    return [(case, stage_kind) for _index, case, stage_kind in selected[:10]]


def _select_workflow_cases(cases: list[dict[str, Any]]) -> tuple[list[tuple[dict[str, Any], str]], str]:
    explicit = [
        case
        for case in cases
        if isinstance(case, dict)
        and (
            str(case.get("execution_group") or "").strip() == "main_smoke"
            or str(case.get("chain_id") or "").strip() == "main_smoke_chain"
        )
    ]
    source = "explicit_main_smoke" if explicit else "inferred_positive_main_flow"
    candidates = explicit or [
        case
        for case in cases
        if isinstance(case, dict) and _priority(case) in {"P0", "P1"}
    ]
    accepted: list[tuple[int, dict[str, Any], str]] = []
    for index, case in enumerate(candidates):
        if not (_text(case.get("description")) or _text(case.get("test_module"))):
            continue
        is_accepted, stage_kind = _workflow_candidate(case, explicit_main_smoke=bool(explicit))
        if is_accepted:
            accepted.append((index, case, stage_kind))
    if explicit:
        selected = [(case, stage_kind) for _index, case, stage_kind in accepted[:10]]
        return selected, source
    selected = _stage_ordered_workflow_cases(accepted)
    if _workflow_chain_is_executable(selected):
        return selected, "inferred_stage_ordered_positive_main_flow"
    selected = [(case, stage_kind) for _index, case, stage_kind in accepted[:10]]
    return selected, source


def _workflow_chain_is_executable(selected: list[tuple[dict[str, Any], str]]) -> bool:
    stage_kinds = [stage_kind for _case, stage_kind in selected]
    if len(stage_kinds) < 2 or "commit" not in stage_kinds:
        return False
    commit_index = stage_kinds.index("commit")
    return any(
        index > commit_index and stage_kind in {"downstream_visibility", "consume", "completion_sync"}
        for index, stage_kind in enumerate(stage_kinds)
    )


def _build_workflow_blueprint_sample(
    cases: list[dict[str, Any]],
    *,
    generation_id: int | None,
    linked_doc_ids: list[int],
    quality_ledger: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    selected, selection_source = _select_workflow_cases(cases)
    if not _workflow_chain_is_executable(selected):
        return None
    steps: list[dict[str, Any]] = []
    previous_state = ""
    workflow_id = f"workflow_blueprint_{generation_id or 'manual'}"
    for index, (case, stage_kind) in enumerate(selected, start=1):
        description = _text(case.get("description")) or f"workflow-step-{index}"
        module = _text(case.get("test_module"))
        expected = _text(case.get("expected_result"))
        transition = _workflow_transition_payload(case)
        scope = _workflow_scope(case, stage_kind)
        source_state = previous_state or _text(
            case.get("source_state") or case.get("state_in") or transition.get("source_state")
        ) or f"{scope}_initial"
        state_out = _workflow_target_state(case, stage_kind=stage_kind, scope=scope)
        steps.append(
            {
                "id": f"step_{index:03d}",
                "label": description[:160],
                "module": module[:80],
                "actor": _workflow_actor(case),
                "action": _workflow_action(case, stage_kind=stage_kind, scope=scope),
                "state_in": source_state[:120],
                "state_out": state_out,
                "assertion": expected[:240],
                "test_steps": case.get("steps") if isinstance(case.get("steps"), list) else [],
                "match_keywords": _workflow_step_keywords(case),
                "source_case_id": _text(case.get("id")),
                "allow_bridge": False,
                "workflow_id": workflow_id,
                "stage_kind": stage_kind,
                "path_type": "positive",
                "blocking": False,
                "destructive": False,
                "can_advance_main_flow": True,
                "main_path_step": True,
                "state_transition_reason": selection_source,
            }
        )
        previous_state = state_out
    title = _text(selected[0][0].get("test_module")) or "final_case_workflow"
    return {
        "signal_type": "positive",
        "pattern_usage": "prefer",
        "pattern_category": "main_smoke_flow",
        "reason_category": "main_smoke_flow",
        "expected_priority": "P0",
        "case_id": workflow_id,
        "title": f"Workflow blueprint: {title}"[:120],
        "user_comment": "Derived from ordered human-final cases; use as executable flow structure, not fixed domain copy.",
        "pattern_summary": f"workflow_blueprint | main_smoke_flow | {title}"[:180],
        "pattern_grain": "workflow_blueprint",
        "source": "linked_final_case_workflow_blueprint",
        "source_type": "linked_final_case_workflow_blueprint",
        "source_id": int(generation_id) if generation_id is not None else None,
        "source_case_id": _text(selected[0][0].get("id")) or None,
        "learning_signal_source": "final_case_workflow_blueprint",
        "pattern_scope": "project",
        "pattern_confidence": _pattern_confidence_from_ledger(quality_ledger, positive=True),
        "quality_ledger": dict(quality_ledger or {}),
        "generation_id": generation_id,
        "linked_doc_ids": linked_doc_ids,
        "workflow_blueprint": {
            "id": workflow_id,
            "name": title[:120],
            "source": "linked_final_case_workflow_blueprint",
            "selection_source": selection_source,
            "state_machine_version": "workflow-blueprint-v2",
            "steps": steps,
            "terminal_state": previous_state,
        },
    }


def _pattern_confidence_from_ledger(payload: dict[str, Any] | None, *, positive: bool) -> float:
    if not isinstance(payload, dict) or not payload:
        return 0.72 if positive else 0.65
    coverage_rate = float(payload.get("coverage_rate") or 0.0)
    missing_rules = int(payload.get("missing_rules_count") or 0)
    rejected = int(payload.get("judge_rejected_out_count") or 0) + int(payload.get("judge_pending_out_count") or 0)
    confidence = 0.68
    if coverage_rate >= 0.9:
        confidence += 0.08
    if missing_rules <= 2:
        confidence += 0.06
    if rejected <= 0:
        confidence += 0.04
    if positive:
        confidence += 0.06
    else:
        confidence -= 0.02
    return round(max(0.35, min(0.92, confidence)), 4)
