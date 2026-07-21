from __future__ import annotations

from typing import Any

from core.processing.document_structure import extract_document_structure, normalize_document_text

from .feedback_control_state import FeedbackControlState
from .functional_architecture import extract_functional_architecture, functional_module_names
from ..coverage.coverage_analyzer import extract_flow_outline


_DATA_FLOW_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("entry_capture", ("进入", "上传", "导入", "新建", "采集", "入口", "capture", "upload", "import", "entry", "create")),
    ("review_confirm", ("复核", "审核", "审批", "确认", "修正", "review", "approve", "confirm", "correct")),
    ("artifact_list", ("列表", "工作台", "dashboard", "list")),
    ("artifact_detail", ("详情", "detail")),
    ("planning", ("计划", "方案", "配置", "plan", "schedule", "config")),
    ("completion_summary", ("完成", "复盘", "成果", "汇总", "summary", "complete", "completion")),
    ("report", ("报告", "分享", "report", "share")),
    ("access_limit", ("额度", "权限", "拦截", "次数", "quota", "permission", "limit", "gate")),
    ("global_exception", ("全局", "异常", "空状态", "无数据", "exception", "global", "empty")),
    ("history_recovery", ("历史", "恢复", "重试", "history", "recover", "retry")),
)

_CROSS_CUTTING_PHASES = {"access_limit", "global_exception", "history_recovery"}
_MIN_PROJECT_PROFILE_CONFIDENCE = 0.2


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


def _architecture_scoped_flow_context(
    requirement_text: str,
    functional_architecture: dict[str, Any],
) -> str:
    """将流程识别限定在已选功能模块的结构范围内。

    页面章节、后台章节和已排除模块仍保留在原始需求中供生成模型参考，
    但不再被误当成必须串行覆盖的用户流程阶段。
    """
    modules = [
        dict(item)
        for item in (functional_architecture.get("functional_modules") or [])
        if isinstance(item, dict)
        and str(item.get("module_name") or "").strip()
        and str(item.get("scope_status") or "in_scope") == "in_scope"
    ]
    try:
        confidence = float(functional_architecture.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    if len(modules) < 2 or confidence < 0.7:
        return ""

    normalized_requirement = normalize_document_text(requirement_text)
    structure = extract_document_structure(normalized_requirement)
    nodes_by_path = {
        tuple(int(part) for part in (item.get("path") or [])): item
        for item in (structure.get("nodes") or [])
        if isinstance(item, dict) and item.get("path")
    }

    scoped_lines: list[str] = []
    allowed_aliases: list[str] = []
    for module in modules:
        allowed_aliases.extend(
            str(alias).strip()
            for alias in (module.get("aliases") or [module.get("module_name")])
            if str(alias).strip()
        )
        path = tuple(int(part) for part in (module.get("structure_path") or []))
        node = nodes_by_path.get(path)
        if node:
            scoped_lines.append(str(node.get("raw_heading") or node.get("title") or ""))
            scoped_lines.extend(str(line) for line in (node.get("section_lines") or []) if str(line).strip())
        else:
            scoped_lines.extend(str(line) for line in (module.get("evidence") or []) if str(line).strip())
            scoped_lines.extend(str(line) for line in (module.get("features") or []) if str(line).strip())

    allowed_aliases = _dedupe_texts(allowed_aliases, limit=80)
    if allowed_aliases:
        # 补充文档其他位置对已选模块的同名引用和跨模块交互证据。
        for line in normalized_requirement.splitlines():
            current = str(line or "").strip()
            if current and any(alias in current for alias in allowed_aliases):
                scoped_lines.append(current)

    return "\n".join(_dedupe_texts(scoped_lines, limit=600))


def _phase_for_label(label: str) -> str:
    lowered = str(label or "").strip().lower()
    if not lowered:
        return ""
    priority = {
        "review_confirm": 0,
        "entry_capture": 1,
        "artifact_list": 2,
        "artifact_detail": 3,
        "planning": 4,
        "completion_summary": 5,
        "report": 6,
        "access_limit": 7,
        "global_exception": 8,
        "history_recovery": 9,
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
    functional_architecture = (
        dict(payload.get("functional_architecture"))
        if isinstance(payload.get("functional_architecture"), dict)
        else {}
    )
    functional_modules = [
        dict(item)
        for item in (functional_architecture.get("functional_modules") or [])
        if isinstance(item, dict) and str(item.get("module_name") or "").strip()
    ]
    excluded_modules = [
        dict(item)
        for item in (functional_architecture.get("excluded_modules") or [])
        if isinstance(item, dict) and str(item.get("module_name") or "").strip()
    ]
    normalized_architecture = {
        **functional_architecture,
        "functional_modules": functional_modules,
        "excluded_modules": excluded_modules,
        "module_interactions": [
            dict(item)
            for item in (functional_architecture.get("module_interactions") or [])
            if isinstance(item, dict)
        ],
        "shared_capabilities": _dedupe_texts(functional_architecture.get("shared_capabilities") or []),
    }
    has_structure = bool(flow_order or cross_cutting or functional_modules)
    raw_confidence = payload.get("confidence")
    if raw_confidence is None:
        confidence = 0.7 if has_structure else 0.0
    else:
        try:
            confidence = float(raw_confidence)
        except Exception:
            confidence = 0.0
    return {
        "profile_version": str(payload.get("profile_version") or "project-profile-v2"),
        "profile_source": str(payload.get("profile_source") or ("document_extracted" if has_structure else "fallback")),
        "confidence": float(confidence),
        "flow_outline": normalized_outline,
        "functional_architecture": normalized_architecture,
        "module_order": functional_module_names({"functional_architecture": normalized_architecture}),
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
    flow_context_text: str = "",
    cases: list[dict[str, Any]] | None = None,
    module_order_hint: list[str] | None = None,
    module_order_source: str = "",
) -> dict[str, Any]:
    functional_architecture = extract_functional_architecture(requirement_text)
    architecture_modules = [
        str(item.get("module_name") or "").strip()
        for item in (functional_architecture.get("functional_modules") or [])
        if isinstance(item, dict) and str(item.get("module_name") or "").strip()
    ]
    hint_cases: list[dict[str, Any]] = []
    for index, module in enumerate(module_order_hint or [], start=1):
        text = str(module or "").strip()
        if text:
            hint_cases.append({"id": f"PROFILE-MODULE-{index:03d}", "test_module": text})
    candidate_cases = hint_cases or [item for item in (cases or []) if isinstance(item, dict)]
    architecture_flow_context = _architecture_scoped_flow_context(requirement_text, functional_architecture)
    outline = _apply_data_flow_order(
        extract_flow_outline(
            architecture_flow_context or flow_context_text or requirement_text,
            candidate_cases,
        )
    )
    if architecture_flow_context:
        outline = {
            **outline,
            "scope_source": "functional_architecture",
            "scope_module_count": len(architecture_modules),
        }
    if hint_cases and len(outline.get("flow_order") or []) < 2:
        labels = [str(item.get("test_module") or "").strip() for item in hint_cases]
        keys = [f"module_{index:03d}" for index in range(1, len(labels) + 1)]
        outline = {
            "source": "module_order_hint",
            "flow_order": keys,
            "document_flow_order": keys,
            "flow_labels": dict(zip(keys, labels)),
            "flow_stage_positions": {key: index for index, key in enumerate(keys)},
            "cross_cutting": [],
            "cross_cutting_labels": {},
            "data_flow_edges": [
                {
                    "from": left,
                    "to": right,
                    "from_label": labels[index],
                    "to_label": labels[index + 1],
                }
                for index, (left, right) in enumerate(zip(keys, keys[1:]))
            ],
            "data_flow_phase_rank": {},
            "data_flow_order_applied": False,
        }
    source = "document_extracted"
    if hint_cases:
        source = str(module_order_source or "module_order_hint") or "module_order_hint"
    elif architecture_modules:
        source = str(functional_architecture.get("source") or "document_structure")
    elif not outline.get("flow_order") and not outline.get("cross_cutting"):
        source = "fallback"
    return normalize_project_profile(
        {
            "profile_source": source,
            "flow_outline": outline,
            "functional_architecture": functional_architecture,
            "confidence": (
                0.78
                if hint_cases
                else float(functional_architecture.get("confidence") or (0.7 if outline.get("flow_order") else 0.0))
            ),
        }
    )


def merge_project_profile_control_state(base_state: Any, project_profile: dict[str, Any] | None) -> FeedbackControlState:
    normalized_base = FeedbackControlState.from_any(base_state)
    profile = normalize_project_profile(project_profile or {})
    outline = dict(profile.get("flow_outline") or {})
    if not profile or not (outline.get("flow_order") or outline.get("cross_cutting")):
        return normalized_base
    try:
        confidence = float(profile.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    if confidence < _MIN_PROJECT_PROFILE_CONFIDENCE:
        return normalized_base.merge(
            {
                "source_meta": {
                    "sources": ["project_profile_domain_gate"],
                    "project_profile_gate": {
                        "allowed": False,
                        "reason": "low_project_profile_confidence",
                        "confidence": float(confidence),
                        "min_confidence": float(_MIN_PROJECT_PROFILE_CONFIDENCE),
                    },
                }
            }
        )
    return normalized_base.merge(
        {
            "source_meta": {
                "sources": ["project_profile"],
                "project_profile": profile,
            }
        }
    )
