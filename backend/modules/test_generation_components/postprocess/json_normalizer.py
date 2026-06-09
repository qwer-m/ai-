"""JSON normalization helpers for test generation postprocessing."""

from __future__ import annotations

import re
from typing import Any


def normalize_json_structure(data: Any) -> Any:
    """Normalize generated case structures without changing non-list inputs."""
    if not isinstance(data, list):
        return data

    normalized = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue

        def pick(keys: list[str], default=None):
            for k in keys:
                if k in item and item.get(k) is not None:
                    return item.get(k)
            return default

        def normalize_list(v: Any) -> list[str]:
            if v is None:
                return []
            if isinstance(v, list):
                out: list[str] = []
                for x in v:
                    if isinstance(x, dict):
                        val = (
                            x.get("text")
                            or x.get("desc")
                            or x.get("step")
                            or x.get("name")
                            or x.get("内容")
                            or x.get("描述")
                            or x.get("步骤")
                        )
                        if val is not None:
                            out.append(str(val).strip())
                        else:
                            out.append(str(x).strip())
                    else:
                        out.append(str(x).strip())
                return [s for s in out if s]
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return []
                if "\n" in s:
                    return [line.strip() for line in s.splitlines() if line.strip()]
                if "，" in s:
                    return [seg.strip() for seg in s.split("，") if seg.strip()]
                if ";" in s:
                    return [seg.strip() for seg in s.split(";") if seg.strip()]
                return [s]
            return [str(v).strip()] if str(v).strip() else []

        raw_id = pick(
            ["id", "ID", "case_id", "caseId", "用例编号", "编号", "test_case_id", "testcase_id"],
            None,
        )
        raw_id_s = str(raw_id).strip() if raw_id is not None else ""
        if re.fullmatch(r"TC-\d{3,}", raw_id_s):
            final_id = raw_id_s
        elif re.fullmatch(r"\d+", raw_id_s):
            final_id = f"TC-{int(raw_id_s):03d}"
        else:
            final_id = f"TC-{i + 1:03d}"

        description = str(
            pick(["description", "desc", "用例描述", "描述", "name", "title", "标题"], "") or ""
        ).strip()
        test_module = str(
            pick(["test_module", "module", "testModule", "模块", "功能模块", "所属模块"], "") or ""
        ).strip()
        preconditions = normalize_list(
            pick(["preconditions", "precondition", "前置条件", "前提条件", "conditions"], [])
        )
        steps = normalize_list(pick(["steps", "step", "操作步骤", "步骤", "test_steps", "testSteps"], []))
        test_input = str(pick(["test_input", "input", "testInput", "输入", "测试输入", "入参"], "") or "").strip()
        expected_result = str(
            pick(["expected_result", "expected", "expectedResult", "预期结果", "期望结果", "断言"], "")
            or ""
        ).strip()
        priority = str(pick(["priority", "Priority", "prio", "优先级", "级别"], "") or "").strip()

        p = priority.upper()
        if p not in ["P0", "P1", "P2"]:
            if p in ["高", "HIGH"]:
                p = "P0"
            elif p in ["中", "MEDIUM"]:
                p = "P1"
            elif p in ["低", "LOW"]:
                p = "P2"
            else:
                p = ""

        normalized.append(
            {
                "id": final_id,
                "description": description,
                "test_module": test_module,
                "preconditions": preconditions,
                "steps": steps,
                "test_input": test_input,
                "expected_result": expected_result,
                "priority": p,
            }
        )

    return normalized


_CASE_KIND_ORDER = {
    "ui_verification": 0,
    "happy_path": 1,
    "validation_boundary": 2,
    "exception_error": 3,
    "permission_security": 4,
    "performance_stability_compat": 5,
    "integration_cross_module": 6,
    "other": 7,
}


def _safe_text_join(value: Any) -> str:
    """Convert nested field values to a plain string for keyword heuristics."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_safe_text_join(x) for x in value)
    if isinstance(value, dict):
        return " ".join(_safe_text_join(v) for v in value.values())
    return str(value)


def infer_case_kind(case: dict[str, Any]) -> str:
    """
    Heuristic case type inference for closed-loop ordering.

    Priority of classification:
    UI -> Integration -> Security/Permission -> Performance/Stability -> Exception ->
    Validation/Boundary -> Happy -> Other
    """
    text = " ".join(
        [
            _safe_text_join(case.get("description")),
            _safe_text_join(case.get("test_module")),
            _safe_text_join(case.get("preconditions")),
            _safe_text_join(case.get("steps")),
            _safe_text_join(case.get("test_input")),
            _safe_text_join(case.get("expected_result")),
        ]
    ).lower()

    def has_any(keywords: list[str]) -> bool:
        return any(k in text for k in keywords)

    if has_any(
        [
            "ui verification",
            "visual",
            "layout",
            "样式",
            "界面",
            "页面展示",
            "视觉",
            "交互样式",
        ]
    ):
        return "ui_verification"

    if has_any(
        [
            "integration",
            "cross-module",
            "跨模块",
            "端到端",
            "end-to-end",
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
    if str(case.get("priority") or "").upper() == "P0":
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
        module = str(case.get("test_module") or "").strip() or "General"
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
        module = str(case.get("test_module") or "").strip() or "General"
        kind = infer_case_kind(case)
        kind_rank = _CASE_KIND_ORDER.get(kind, _CASE_KIND_ORDER["other"])
        pri = str(case.get("priority") or "P1").upper()
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
