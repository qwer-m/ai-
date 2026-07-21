"""JSON validation and ordering helpers for test generation postprocessing."""

from __future__ import annotations

from typing import Any

from .case_access import case_flat_text, case_text_field, case_text_value

_CASE_KIND_ORDER = {
    "workflow_entry": 0,
    "happy_path": 1,
    "validation_boundary": 2,
    "exception_error": 3,
    "permission_security": 4,
    "performance_stability_compat": 5,
    "integration_cross_module": 6,
    "ui_verification": 7,
    "other": 8,
}


def _safe_text_join(value: Any) -> str:
    """Compatibility wrapper for legacy JSON helper imports."""
    return case_text_value(value)


def infer_case_kind(case: dict[str, Any]) -> str:
    """
    Heuristic case type inference for closed-loop ordering.

    Priority of classification distinguishes workflow-bearing UI from presentation-only UI.
    """
    text = case_flat_text(
        case,
        fields=("description", "test_module", "preconditions", "steps", "test_input", "expected_result"),
        separator=" ",
        lower=True,
    )

    def has_any(keywords: list[str]) -> bool:
        return any(k in text for k in keywords)

    presentation_only = has_any(
        [
            "ui verification",
            "visual",
            "layout",
            "样式",
            "颜色",
            "字体",
            "间距",
            "视觉",
            "纯展示",
            "页面展示",
        ]
    )
    workflow_interaction = has_any(
        [
            "workflow entry",
            "entry point",
            "入口",
            "点击进入",
            "点击后进入",
            "点击后跳转",
            "不可点击",
            "点击无效",
            "无法进入",
            "cannot click",
            "not clickable",
            "navigate to",
        ]
    )
    if workflow_interaction:
        return "workflow_entry"
    if presentation_only:
        return "ui_verification"

    if has_any(
        [
            "integration",
            "cross-module",
            "cross module",
            "end-to-end",
            "e2e",
            "跨模块",
            "端到端",
            "联调",
            "跨系统",
            "全链路",
        ]
    ):
        return "integration_cross_module"

    if has_any(
        [
            "permission",
            "auth",
            "authorize",
            "unauthorized",
            "forbidden",
            "security",
            "xss",
            "csrf",
            "sql injection",
            "权限",
            "越权",
            "安全",
            "脱敏",
            "风控",
        ]
    ):
        return "permission_security"

    if has_any(
        [
            "performance",
            "perf",
            "latency",
            "throughput",
            "stability",
            "compatibility",
            "concurrent",
            "load",
            "stress",
            "timeout",
            "memory",
            "cpu",
            "性能",
            "并发",
            "稳定性",
            "兼容",
            "压测",
            "响应时间",
        ]
    ):
        return "performance_stability_compat"

    if has_any(
        [
            "error",
            "exception",
            "failed",
            "fail",
            "invalid",
            "denied",
            "拒绝",
            "失败",
            "异常",
            "报错",
            "错误",
            "不可用",
            "超时",
        ]
    ):
        return "exception_error"

    if has_any(
        [
            "boundary",
            "equivalence",
            "validation",
            "required",
            "max",
            "min",
            "range",
            "limit",
            "null",
            "empty",
            "格式",
            "必填",
            "边界",
            "校验",
            "最小",
            "最大",
            "长度",
            "取值",
        ]
    ):
        return "validation_boundary"

    if has_any(
        [
            "happy path",
            "success",
            "正常",
            "主流程",
            "成功",
            "可提交",
            "可保存",
        ]
    ):
        return "happy_path"

    # Fallback: treat high-priority unlabeled cases as happy-path first.
    if case_text_field(case, "priority").upper() == "P0":
        return "happy_path"
    return "other"


def extract_module_order_from_cases(
    cases: list[dict[str, Any]],
    module_order_hint: list[str] | None = None,
) -> list[str]:
    """
    Build module order for final display.

    Rule:
    1) keep optional hint order first
    2) fill remaining modules by first appearance in the generated list
    """
    ordered: list[str] = []
    seen: set[str] = set()

    for module in (module_order_hint or []):
        name = str(module or "").strip()
        if not name or name in seen:
            continue
        ordered.append(name)
        seen.add(name)

    for case in cases:
        if not isinstance(case, dict):
            continue
        module = case_text_field(case, "test_module") or "General"
        if module in seen:
            continue
        ordered.append(module)
        seen.add(module)
    return ordered


def reorder_cases_by_closed_loop(
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    module_order_hint: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Reorder cases into workflow-first closed-loop structure.

    Sort order:
    - Module order (hint first, then first appearance)
    - Case kind in module:
      UI -> Happy -> Validation -> Exception -> Security -> Performance -> Integration -> Other
    - Priority: P0 > P1 > P2
    - Original index for stable ordering
    """
    if not isinstance(cases, list):
        return []

    normalized_cases = [x for x in cases if isinstance(x, dict)]
    if not normalized_cases:
        return []

    module_order = extract_module_order_from_cases(normalized_cases, module_order_hint)
    module_rank = {name: idx for idx, name in enumerate(module_order)}
    priority_rank = {"P0": 0, "P1": 1, "P2": 2}

    annotated: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for idx, case in enumerate(normalized_cases):
        module = case_text_field(case, "test_module") or "General"
        kind = infer_case_kind(case)
        kind_rank = _CASE_KIND_ORDER.get(kind, _CASE_KIND_ORDER["other"])
        pri = (case_text_field(case, "priority") or "P1").upper()
        pri_rank = priority_rank.get(pri, 1)
        key = (module_rank.get(module, len(module_rank)), kind_rank, pri_rank, idx)
        annotated.append((key, dict(case)))

    annotated.sort(key=lambda x: x[0])
    reordered = [row for _, row in annotated]

    if not renumber_ids:
        return reordered

    safe_start = max(1, int(start_id or 1))
    for i, case in enumerate(reordered):
        case["id"] = f"TC-{safe_start + i:03d}"
    return reordered
