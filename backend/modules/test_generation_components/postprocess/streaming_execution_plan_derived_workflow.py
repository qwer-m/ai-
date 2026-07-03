from __future__ import annotations

from typing import Any, Callable

from ..control.actor_roles import normalize_actor_role as normalize_actor_role_value
from .case_access import case_step_lines, case_text_field
from .streaming_execution_plan_grouping import (
    execution_case_text,
    priority_rank,
)
from .streaming_execution_plan_stage_inference import (
    contains_any_token,
    infer_workflow_phase,
    infer_workflow_stage_kind,
)
from .streaming_postprocess_utils import _clip_text

DERIVED_WORKFLOW_ACTION_TOKENS = (
    "新增",
    "创建",
    "添加",
    "选择",
    "设置",
    "编辑",
    "修改",
    "准备",
    "准备好",
    "预览",
    "保存",
    "提交",
    "提交成功",
    "提交后",
    "确认",
    "发布",
    "下架",
    "删除",
    "同步",
    "生效",
    "进入",
    "进入页面",
    "入口",
    "跳转",
    "点击",
    "学习",
    "查看",
    "打开",
    "create",
    "add",
    "select",
    "set",
    "edit",
    "preview",
    "save",
    "submit",
    "commit",
    "committed",
    "confirm",
    "sync",
    "navigate",
    "click",
    "view",
    "learn",
    "open",
    "entry",
    "prepare",
    "prepared",
    "reflect",
    "reflects",
    "downstream",
    "触发打分",
    "开始打分",
    "自动打分",
    "评分计算",
    "生成评分",
    "给出评分",
    "trigger score",
    "score calculation",
)

DERIVED_WORKFLOW_STATE_TOKENS = (
    "成功",
    "完成",
    "正确",
    "一致",
    "保存",
    "已保存",
    "保存成功",
    "加入",
    "回到",
    "跳转",
    "更新",
    "展示",
    "显示",
    "进入",
    "准备好",
    "准备完成",
    "生效",
    "已生效",
    "success",
    "completed",
    "successfully",
    "updated",
    "saved",
    "visible",
    "ready",
    "prepared",
    "reflected",
    "shown",
    "评分结果",
    "打分结果",
    "综合评分",
    "score result",
    "scoring result",
)

DERIVED_WORKFLOW_BOUNDARY_TOKENS = (
    "边界",
    "上限",
    "下限",
    "空状态",
    "无数据",
    "boundary",
    "limit",
    "empty",
)

DERIVED_WORKFLOW_DISPLAY_ONLY_PENALTY_TOKENS = (
    "按钮展示",
    "文案",
    "样式",
    "排序",
    "筛选",
    "列表",
    "display only",
)


def derived_workflow_candidate_buckets(
    cases_for_plan: list[dict[str, Any]],
    *,
    exclusion_reason_fn: Callable[[dict[str, Any]], str],
    record_exclusion_fn: Callable[[dict[str, Any], str], None],
) -> tuple[list[tuple[int, int, int, dict[str, Any]]], list[tuple[int, int, int, dict[str, Any]]]]:
    primary_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    fallback_candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, item in enumerate(cases_for_plan):
        text = execution_case_text(item)
        exclusion_reason = exclusion_reason_fn(item)
        if exclusion_reason:
            record_exclusion_fn(item, exclusion_reason)
            continue
        action_score = sum(1 for token in DERIVED_WORKFLOW_ACTION_TOKENS if token.lower() in text)
        state_score = sum(1 for token in DERIVED_WORKFLOW_STATE_TOKENS if token.lower() in text)
        if action_score <= 0 or state_score <= 0:
            continue
        score = priority_rank(item) + action_score * 10 + state_score * 5
        if contains_any_token(text, DERIVED_WORKFLOW_BOUNDARY_TOKENS):
            score -= 20
        if contains_any_token(text, DERIVED_WORKFLOW_DISPLAY_ONLY_PENALTY_TOKENS):
            score -= 10
        if score < 15:
            continue
        bucket = primary_candidates if priority_rank(item) > 0 else fallback_candidates
        bucket.append((score, infer_workflow_phase(text), index, item))
    return primary_candidates, fallback_candidates


def select_derived_workflow_candidates(
    primary_candidates: list[tuple[int, int, int, dict[str, Any]]],
    fallback_candidates: list[tuple[int, int, int, dict[str, Any]]],
    *,
    limit: int = 10,
) -> list[tuple[int, int, int, dict[str, Any]]]:
    scored = list(primary_candidates if len(primary_candidates) >= 2 else fallback_candidates)
    if primary_candidates and fallback_candidates:
        scored.extend(fallback_candidates)
    if len(scored) < 2:
        return []
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    return sorted(scored[: max(1, int(limit or 0))], key=lambda row: (row[1], row[2]))


def derived_workflow_steps_from_selected(
    selected: list[tuple[int, int, int, dict[str, Any]]],
    *,
    case_id_fn: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], str]:
    steps: list[dict[str, Any]] = []
    previous_state = "initial"
    for step_index, (_score, _phase, _index, item) in enumerate(selected, start=1):
        description = (
            case_text_field(item, "description")
            or case_text_field(item, "test_module")
            or f"workflow step {step_index}"
        )
        module = case_text_field(item, "test_module")
        expected = case_text_field(item, "expected_result")
        step_texts = case_step_lines(item)
        first_step = next((step for step in step_texts if step), "")
        state_out = f"derived_state_{step_index:03d}"
        stage_kind = infer_workflow_stage_kind(execution_case_text(item))
        match_keywords = [
            _clip_text(value, 120)
            for value in (description, module, expected, first_step)
            if str(value or "").strip()
        ]
        steps.append(
            {
                "id": f"derived_step_{step_index:03d}",
                "label": _clip_text(description, 160),
                "module": _clip_text(module, 80),
                "actor": normalize_actor_role_value("", fallback_text=execution_case_text(item)),
                "action": _clip_text(description, 160),
                "state_in": previous_state,
                "state_out": state_out,
                "stage_kind": stage_kind,
                "assertion": expected[:240],
                "test_steps": step_texts,
                "match_keywords": list(dict.fromkeys(match_keywords))[:6],
                "source_case_id": case_id_fn(item),
                "main_path_step": True,
                "allow_bridge": False,
            }
        )
        previous_state = state_out
    return steps, previous_state


def derived_workflow_selected_for_closure(
    steps: list[dict[str, Any]],
    selected: list[tuple[int, int, int, dict[str, Any]]],
    *,
    case_id_fn: Callable[[dict[str, Any]], str],
) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (
            str(step.get("id") or ""),
            str(step.get("label") or ""),
            next(
                (
                    item
                    for _score, _phase, _index, item in selected
                    if case_id_fn(item) == str(step.get("source_case_id") or "")
                ),
                {},
            ),
        )
        for step in steps
    ]


def derive_workflow_blueprint_from_current_cases(
    cases_for_plan: list[dict[str, Any]],
    *,
    exclusion_reason_fn: Callable[[dict[str, Any]], str],
    record_exclusion_fn: Callable[[dict[str, Any], str], None],
    case_id_fn: Callable[[dict[str, Any]], str],
    stage_meta_by_key: dict[str, dict[str, Any]],
    closure_status_fn: Callable[..., tuple[bool, str, list[str]]],
    closure_source: str = "current_generation_cases",
) -> dict[str, Any]:
    primary_candidates, fallback_candidates = derived_workflow_candidate_buckets(
        cases_for_plan,
        exclusion_reason_fn=exclusion_reason_fn,
        record_exclusion_fn=record_exclusion_fn,
    )
    debug: dict[str, Any] = {
        "candidate_total": int(len(cases_for_plan)),
        "action_state_candidate_count": int(len(primary_candidates) + len(fallback_candidates)),
        "primary_candidate_count": int(len(primary_candidates)),
        "fallback_candidate_count": int(len(fallback_candidates)),
        "selected_candidate_count": 0,
        "closure_reason": "",
    }
    selected = select_derived_workflow_candidates(primary_candidates, fallback_candidates)
    if len(selected) < 2:
        debug["closure_reason"] = "insufficient_action_state_candidates"
        return {
            "blueprint": None,
            "debug": debug,
            "incomplete_reason": "",
            "steps": [],
            "terminal_state": "",
            "stage_kinds": [],
        }
    debug["selected_candidate_count"] = int(len(selected))
    steps, previous_state = derived_workflow_steps_from_selected(
        selected,
        case_id_fn=case_id_fn,
    )
    if len(steps) < 2:
        return {
            "blueprint": None,
            "debug": debug,
            "incomplete_reason": "",
            "steps": steps,
            "terminal_state": previous_state,
            "stage_kinds": [],
        }
    selected_for_closure = derived_workflow_selected_for_closure(
        steps,
        selected,
        case_id_fn=case_id_fn,
    )
    ok, reason, stage_kinds = closure_status_fn(
        selected_for_closure,
        stage_meta_by_key=stage_meta_by_key,
        source=closure_source,
    )
    if not ok:
        debug["closure_reason"] = str(reason or "")
        return {
            "blueprint": None,
            "debug": debug,
            "incomplete_reason": str(reason or ""),
            "steps": steps,
            "terminal_state": previous_state,
            "stage_kinds": list(stage_kinds),
        }
    return {
        "blueprint": {
            "id": "derived_current_generation_workflow",
            "name": "current generation derived workflow",
            "source": "current_generation_cases",
            "steps": steps,
            "terminal_state": previous_state,
        },
        "debug": debug,
        "incomplete_reason": "",
        "steps": steps,
        "terminal_state": previous_state,
        "stage_kinds": list(stage_kinds),
    }
