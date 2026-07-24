from __future__ import annotations

import re
from typing import Any

from .case_access import case_flat_text
from .json_repair import deterministic_case_dedup_key

try:
    from ..prompting.structured_context import (
        _normalize_priority as _normalize_existing_priority,
    )
except Exception:  # pragma: no cover
    def _normalize_existing_priority(value: Any) -> str:
        priority = str(value or "P2").strip().upper()
        return priority if priority in {"P0", "P1", "P2"} else "P2"


def _extract_case_text(case: dict[str, Any]) -> str:
    return case_flat_text(
        case,
        fields=(
            "title",
            "module",
            "test_module",
            "description",
            "test_input",
            "expected_result",
            "expected_results",
            "risk",
            "case_type",
            "case_kind",
            "validation_kind",
            "preconditions",
            "steps",
            "tags",
        ),
        separator=" ",
        lower=True,
    )


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_case_level_release_blocking(text: str) -> bool:
    lowered = str(text or "").lower()
    explicit = (
        "release blocking",
        "release_blocking",
        "block release",
        "release blocked",
        "阻断发布",
        "发布阻断",
        "发布被阻断",
        "无法发布",
    )
    if _contains_any(lowered, explicit):
        return True
    release_hint = ("release", "发布", "上线")
    block_hint = ("block", "blocked", "blocking", "阻断", "中断", "无法发布", "不能发布")
    return _contains_any(lowered, release_hint) and _contains_any(lowered, block_hint)


def _priority_case_signature(case: dict[str, Any]) -> str:
    return deterministic_case_dedup_key(case, include_priority=False)


def _rule_hit_by_light_match(rule: dict[str, Any], case_text: str) -> bool:
    lowered_case = str(case_text or "").lower()
    rule_id = str(rule.get("rule_id") or "").strip().lower().replace(" ", "")
    rule_text = str(rule.get("rule_text") or "").strip().lower()
    if rule_id and rule_id in lowered_case.replace(" ", ""):
        return True
    if rule_text and rule_text in lowered_case:
        return True
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}", rule_text)
    if not tokens:
        return False
    hits = sum(1 for token in tokens if token and token.lower() in lowered_case)
    return (hits / max(1, len(tokens))) >= 0.35


def _rule_status_from_diag(diag: dict[str, Any]) -> str:
    covered = bool(diag.get("covered"))
    missing_types = [str(x).strip() for x in (diag.get("missing_types") or []) if str(x).strip()]
    if not covered:
        return "missing"
    if missing_types:
        return "weakly_covered"
    return "covered"


def _infer_rule_signals(diag: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        [
            str(diag.get("rule_text") or ""),
            str(diag.get("rule_id") or ""),
        ]
    ).lower()

    core = _contains_any(
        text,
        (
            "核心",
            "主流程",
            "登录",
            "下单",
            "支付",
            "提交",
            "保存",
            "发布",
            "审批",
            "查询",
            "core",
            "main flow",
            "workflow",
            "login",
            "order",
            "payment",
            "submit",
            "save",
            "publish",
            "approve",
        ),
    )
    security = _contains_any(
        text,
        (
            "安全",
            "权限",
            "鉴权",
            "认证",
            "越权",
            "security",
            "permission",
            "auth",
            "authorization",
            "authentication",
        ),
    )
    data_critical = _contains_any(
        text,
        (
            "数据",
            "账务",
            "金额",
            "状态",
            "数据丢失",
            "data",
            "amount",
            "ledger",
            "state",
            "corruption",
            "loss",
        ),
    )
    release_blocking = _contains_any(
        text,
        (
            "阻断发布",
            "发布阻断",
            "critical",
            "blocker",
            "release blocking",
            "release_blocking",
            "sev0",
            "sev1",
        ),
    )
    status = _rule_status_from_diag(diag)

    if release_blocking or security or data_critical:
        risk_level = "high"
    elif core or status == "weakly_covered":
        risk_level = "medium"
    else:
        risk_level = "low"

    return {
        "rule_id": str(diag.get("rule_id") or "").strip(),
        "biz_key": str(diag.get("biz_key") or "unknown").strip() or "unknown",
        "rule_text": str(diag.get("rule_text") or "").strip(),
        "rule_is_core_workflow": bool(core),
        "rule_is_security_sensitive": bool(security),
        "rule_is_data_critical": bool(data_critical),
        "rule_is_release_blocking": bool(release_blocking),
        "rule_risk_level": risk_level,
        "rule_coverage_status": status,
    }


def _normalize_rule_diagnostics(
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []

    if isinstance(coverage_context, dict):
        payload = coverage_context.get("rule_diagnostics")
        if isinstance(payload, list):
            diagnostics.extend([item for item in payload if isinstance(item, dict)])

    if isinstance(rule_diagnostics, dict):
        payload = rule_diagnostics.get("rule_diagnostics")
        if isinstance(payload, list):
            diagnostics.extend([item for item in payload if isinstance(item, dict)])
    elif isinstance(rule_diagnostics, list):
        diagnostics.extend([item for item in rule_diagnostics if isinstance(item, dict)])

    dedup: dict[str, dict[str, Any]] = {}
    for item in diagnostics:
        rule_id = str(item.get("rule_id") or "").strip()
        rule_text = str(item.get("rule_text") or "").strip()
        key = f"{rule_id}|{rule_text}"
        if not key.strip("|"):
            continue
        dedup[key] = item
    return list(dedup.values())


def _build_priority_coverage_context(
    cases: list[dict[str, Any]],
    coverage_context: dict[str, Any] | None = None,
    rule_diagnostics: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    diagnostics = _normalize_rule_diagnostics(coverage_context, rule_diagnostics)
    if not diagnostics:
        return dict(coverage_context or {})

    missing_rules = {
        str(item).strip()
        for item in ((coverage_context or {}).get("missing_rules") or [])
        if str(item).strip()
    }
    covered_rules = {
        str(item).strip()
        for item in ((coverage_context or {}).get("covered_rules") or [])
        if str(item).strip()
    }

    rule_meta: dict[str, dict[str, Any]] = {}
    for diag in diagnostics:
        signals = _infer_rule_signals(diag)
        rule_id = signals.get("rule_id") or ""
        if not rule_id:
            continue
        if rule_id in missing_rules:
            signals["rule_coverage_status"] = "missing"
        elif rule_id in covered_rules and signals.get("rule_coverage_status") == "missing":
            signals["rule_coverage_status"] = "weakly_covered"
        rule_meta[rule_id] = signals

    case_rule_hits: dict[str, list[str]] = {}
    rule_hit_counts: dict[str, int] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        signature = _priority_case_signature(case)
        case_text = _extract_case_text(case)
        hit_ids: list[str] = []
        for rule_id, meta in rule_meta.items():
            rule_payload = {"rule_id": meta.get("rule_id"), "rule_text": meta.get("rule_text")}
            if _rule_hit_by_light_match(rule_payload, case_text):
                hit_ids.append(rule_id)
        dedup_ids = sorted(set(hit_ids))
        case_rule_hits[signature] = dedup_ids
        for rule_id in dedup_ids:
            rule_hit_counts[rule_id] = int(rule_hit_counts.get(rule_id, 0)) + 1

    case_rule_map: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        signature = _priority_case_signature(case)
        covered_rule_ids = list(case_rule_hits.get(signature) or [])
        missing_rule_hits = [
            rid
            for rid in covered_rule_ids
            if str((rule_meta.get(rid) or {}).get("rule_coverage_status")) in {"missing", "weakly_covered"}
        ]
        core_rule_hits = [
            rid for rid in covered_rule_ids if bool((rule_meta.get(rid) or {}).get("rule_is_core_workflow"))
        ]
        unique_coverage_hits = [
            rid for rid in covered_rule_ids if int(rule_hit_counts.get(rid) or 0) == 1
        ]
        rule_risk_reasons = sorted(
            set(
                str((rule_meta.get(rid) or {}).get("rule_risk_level") or "low")
                for rid in covered_rule_ids
            )
        )
        case_rule_map[signature] = {
            "covered_rule_ids": covered_rule_ids,
            "case_covering_rules": covered_rule_ids,
            "case_unique_rule_hits_count": int(len(unique_coverage_hits)),
            "case_missing_rule_hits_count": int(len(missing_rule_hits)),
            "case_core_rule_hits_count": int(len(core_rule_hits)),
            "missing_rule_hits": missing_rule_hits,
            "core_rule_hits": core_rule_hits,
            "unique_coverage_hits": unique_coverage_hits,
            "rule_risk_reasons": rule_risk_reasons,
        }

    enriched = dict(coverage_context or {})
    enriched["rule_diagnostics"] = diagnostics
    enriched["rule_meta"] = rule_meta
    enriched["case_rule_map"] = case_rule_map
    enriched["rule_hit_counts"] = rule_hit_counts
    return enriched

