from __future__ import annotations

from typing import Any

from .feedback_control_state import FeedbackControlState
from ..coverage.coverage_analyzer import extract_flow_outline


_DATA_FLOW_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entry_capture", ("拍照", "拍摄", "上传", "采集", "识别", "批改", "入口", "capture", "upload", "import", "entry")),
    ("review_confirm", ("复核", "审核", "审批", "确认", "修正", "review", "approve", "confirm", "correct")),
    ("artifact_list", ("习题本", "错题本", "题本", "列表", "workbook", "notebook", "dashboard", "list")),
    ("artifact_detail", ("详情", "解析", "答案", "detail", "answer", "analysis")),
    ("learning_plan", ("提升计划", "学习计划", "方案", "课程", "看视频", "切片", "plan", "course", "lesson", "slice")),
    ("completion_summary", ("完成", "复盘", "成果", "汇总", "summary", "complete", "completion")),
    ("report", ("报告", "成长报告", "周报", "分享", "report", "share")),
    ("access_limit", ("额度", "权限", "拦截", "次数", "quota", "permission", "limit", "gate")),
    ("global_exception", ("全局", "异常", "空状态", "无数据", "exception", "global", "empty")),
    ("history_makeup", ("历史", "补做", "补学", "history", "makeup")),
)

_CROSS_CUTTING_PHASES = {"access_limit", "global_exception", "history_makeup"}


def _dedupe_texts(values: Any, *, limit: int = 80) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        values = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = " ".join(text.lower().split())
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _phase_for_label(label: str) -> str:
    lowered = str(label or "").strip().lower()
    if not lowered:
        return ""
    priority = {
        "review_confirm": 0,
        "entry_capture": 1,
        "artifact_list": 2,
        "artifact_detail": 3,
        "learning_plan": 4,
        "completion_summary": 5,
        "report": 6,
        "access_limit": 7,
        "global_exception": 8,
        "history_makeup": 9,
    }
    matches: list[tuple[int, int, str]] = []
    for phase, tokens in _DATA_FLOW_PHASES:
        score = sum(len(str(token)) for token in tokens if str(token).lower() in lowered)
        if score > 0:
            matches.append((int(score), -int(priority.get(phase, 99)), phase))
    if matches:
        matches.sort(reverse=True)
        return matches[0][2]
    return ""


def _apply_data_flow_order(outline: dict[str, Any]) -> dict[str, Any]:
    flow_order = [str(item) for item in (outline.get("flow_order") or []) if str(item).strip()]
    cross_cutting = [str(item) for item in (outline.get("cross_cutting") or []) if str(item).strip()]
    flow_labels = dict(outline.get("flow_labels") or {})
    cross_labels = dict(outline.get("cross_cutting_labels") or {})
    phase_rank = {phase: index for index, (phase, _tokens) in enumerate(_DATA_FLOW_PHASES)}
    stage_phase: dict[str, str] = {}
    moved_to_cross: list[str] = []
    retained_flow: list[str] = []
    for key in flow_order:
        label = str(flow_labels.get(key) or key)
        phase = _phase_for_label(label)
        if phase:
            stage_phase[key] = phase
        if phase in _CROSS_CUTTING_PHASES:
            moved_to_cross.append(key)
            cross_labels.setdefault(key, label)
            continue
        retained_flow.append(key)

    matched = [key for key in retained_flow if stage_phase.get(key)]
    if len(matched) >= 2:
        sorted_flow = sorted(
            enumerate(retained_flow),
            key=lambda item: (
                phase_rank.get(stage_phase.get(item[1]) or "", 10_000),
                item[0],
            ),
        )
        new_flow_order = [key for _index, key in sorted_flow]
    else:
        new_flow_order = retained_flow

    new_cross = _dedupe_texts([*cross_cutting, *moved_to_cross])
    edges: list[dict[str, str]] = []
    for left, right in zip(new_flow_order, new_flow_order[1:]):
        edges.append(
            {
                "from": left,
                "to": right,
                "from_label": str(flow_labels.get(left) or left),
                "to_label": str(flow_labels.get(right) or right),
            }
        )

    return {
        **outline,
        "flow_order": new_flow_order,
        "cross_cutting": new_cross,
        "cross_cutting_labels": cross_labels,
        "data_flow_edges": edges,
        "data_flow_phase_rank": stage_phase,
        "data_flow_order_applied": bool(new_flow_order != flow_order or moved_to_cross),
    }


def normalize_project_profile(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    flow_outline = payload.get("flow_outline") if isinstance(payload.get("flow_outline"), dict) else {}
    flow_order = _dedupe_texts(flow_outline.get("flow_order") or payload.get("flow_order") or [])
    cross_cutting = _dedupe_texts(flow_outline.get("cross_cutting") or payload.get("cross_cutting") or [])
    flow_labels = dict(flow_outline.get("flow_labels") or payload.get("flow_labels") or {})
    cross_labels = dict(flow_outline.get("cross_cutting_labels") or payload.get("cross_cutting_labels") or {})
    normalized_outline = {
        **dict(flow_outline),
        "flow_order": flow_order,
        "cross_cutting": cross_cutting,
        "flow_labels": {str(k): str(v) for k, v in flow_labels.items() if str(k).strip() and str(v).strip()},
        "cross_cutting_labels": {
            str(k): str(v) for k, v in cross_labels.items() if str(k).strip() and str(v).strip()
        },
        "data_flow_edges": [
            dict(item) for item in (flow_outline.get("data_flow_edges") or payload.get("data_flow_edges") or [])
            if isinstance(item, dict)
        ],
        "data_flow_phase_rank": dict(flow_outline.get("data_flow_phase_rank") or payload.get("data_flow_phase_rank") or {}),
        "data_flow_order_applied": bool(
            flow_outline.get("data_flow_order_applied") or payload.get("data_flow_order_applied")
        ),
    }
    has_structure = bool(flow_order or cross_cutting)
    return {
        "profile_version": str(payload.get("profile_version") or "project-profile-v1"),
        "profile_source": str(payload.get("profile_source") or ("document_extracted" if has_structure else "fallback")),
        "confidence": float(payload.get("confidence") or (0.7 if has_structure else 0.0)),
        "flow_outline": normalized_outline,
        "ordering_policy": str(payload.get("ordering_policy") or "flow_first_then_cross_cutting"),
        "scenario_cluster_policy": dict(
            payload.get("scenario_cluster_policy") or {"default_max_per_scenario": 2}
        ),
        "profile_constraints": _dedupe_texts(
            payload.get("profile_constraints") or ["strategy_only_not_fact_source"]
        ),
        "strategy_only": True,
    }


def build_project_profile(
    *,
    requirement_text: str = "",
    cases: list[dict[str, Any]] | None = None,
    module_order_hint: list[str] | None = None,
    module_order_source: str = "",
) -> dict[str, Any]:
    hint_cases: list[dict[str, Any]] = []
    for index, module in enumerate(module_order_hint or [], start=1):
        text = str(module or "").strip()
        if text:
            hint_cases.append({"id": f"PROFILE-MODULE-{index:03d}", "test_module": text})
    candidate_cases = hint_cases or [item for item in (cases or []) if isinstance(item, dict)]
    outline = _apply_data_flow_order(extract_flow_outline(requirement_text, candidate_cases))
    source = "document_extracted"
    if hint_cases:
        source = str(module_order_source or "module_order_hint") or "module_order_hint"
    elif not outline.get("flow_order") and not outline.get("cross_cutting"):
        source = "fallback"
    return normalize_project_profile(
        {
            "profile_source": source,
            "flow_outline": outline,
            "confidence": 0.78 if hint_cases else (0.7 if outline.get("flow_order") else 0.0),
        }
    )


def merge_project_profile_control_state(base_state: Any, project_profile: dict[str, Any] | None) -> FeedbackControlState:
    normalized_base = FeedbackControlState.from_any(base_state)
    profile = normalize_project_profile(project_profile or {})
    outline = dict(profile.get("flow_outline") or {})
    if not profile or not (outline.get("flow_order") or outline.get("cross_cutting")):
        return normalized_base
    return normalized_base.merge(
        {
            "source_meta": {
                "sources": ["project_profile"],
                "project_profile": profile,
            }
        }
    )
