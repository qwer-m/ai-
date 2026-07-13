from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from .actor_roles import normalize_actor_role
from .feedback_control_state import FeedbackControlState
from .workflow_blueprint_repository import is_trusted_workflow_contract
from ..postprocess.json_processing import clean_and_parse_json


CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE = "current_requirement_blueprint"
CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE = "current_requirement_extracted"

_MAX_REQUIREMENT_CHARS = 24000
_MAX_BLUEPRINTS = 1
_MAX_STEPS = 10
_MAX_KEYWORDS_PER_STEP = 8
_DEFAULT_BLUEPRINT_MAX_TOKENS = 1600
_MIN_BLUEPRINT_MAX_TOKENS = 600
_BLUEPRINT_MAX_TOKENS_ENV = "GENERATION_CURRENT_REQUIREMENT_BLUEPRINT_MAX_TOKENS"

_ALLOWED_STAGE_KINDS = {
    "entry",
    "configure",
    "edit",
    "preview",
    "commit",
    "downstream_visibility",
    "consume",
    "completion_sync",
}

_STAGE_KIND_ALIASES = {
    "start": "entry",
    "open": "entry",
    "list": "entry",
    "select": "configure",
    "config": "configure",
    "form": "configure",
    "modify": "edit",
    "save": "commit",
    "submit": "commit",
    "publish": "commit",
    "share": "downstream_visibility",
    "display": "downstream_visibility",
    "visible": "downstream_visibility",
    "sync": "completion_sync",
    "complete": "completion_sync",
}

_FALLBACK_STAGE_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "stage_kind": "entry",
        "id": "entry",
        "label": "进入目标功能",
        "action": "打开需求涉及的目标功能入口",
        "tokens": ("进入", "入口", "打开", "访问", "查看", "浏览", "列表", "首页", "tab", "page", "open", "view", "list"),
    },
    {
        "stage_kind": "configure",
        "id": "configure",
        "label": "配置或编辑业务内容",
        "action": "按需求配置、编辑或选择业务内容",
        "tokens": ("选择", "填写", "输入", "编辑", "配置", "设置", "筛选", "排序", "上传", "切换", "新增", "修改", "select", "edit", "input", "config", "upload"),
    },
    {
        "stage_kind": "preview",
        "id": "preview",
        "label": "检查页面反馈",
        "action": "检查提交前或操作后的页面展示与反馈",
        "tokens": ("预览", "展示", "显示", "校验", "检查", "确认", "提示", "弹窗", "状态", "preview", "display", "check", "toast"),
    },
    {
        "stage_kind": "commit",
        "id": "commit",
        "label": "提交或保存变更",
        "action": "提交、保存或发布本次业务操作",
        "tokens": ("提交", "保存", "发布", "确认", "完成", "发送", "删除", "更新", "创建", "submit", "save", "publish", "send", "delete"),
    },
    {
        "stage_kind": "downstream_visibility",
        "id": "downstream_visibility",
        "label": "验证下游可见",
        "action": "验证操作结果在列表、详情或关联页面可见",
        "tokens": ("可见", "同步", "刷新", "列表", "详情", "展示", "显示", "通知", "分享", "统计", "visibility", "sync", "detail", "share"),
    },
    {
        "stage_kind": "completion_sync",
        "id": "completion_sync",
        "label": "确认流程完成",
        "action": "确认流程完成并保持最终状态一致",
        "tokens": ("成功", "完成", "结果", "记录", "状态", "生效", "一致", "success", "complete", "result", "record"),
    },
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        value = int(str(raw).strip())
    except ValueError:
        return int(default)
    return max(int(minimum), int(value))


def current_requirement_blueprint_max_tokens() -> int:
    return _env_int(
        _BLUEPRINT_MAX_TOKENS_ENV,
        _DEFAULT_BLUEPRINT_MAX_TOKENS,
        minimum=_MIN_BLUEPRINT_MAX_TOKENS,
    )


def _slug(value: Any, *, fallback: str = "workflow") -> str:
    text = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", _text(value)).strip("_")
    if not text:
        text = fallback
    return text[:80]


def _fingerprint(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", _text(value))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _compact_requirement(value: str) -> str:
    text = _text(value)
    if len(text) <= _MAX_REQUIREMENT_CHARS:
        return text
    head = text[: int(_MAX_REQUIREMENT_CHARS * 0.75)]
    tail = text[-int(_MAX_REQUIREMENT_CHARS * 0.25) :]
    return f"{head}\n\n...[requirement truncated for blueprint extraction]...\n\n{tail}"


def _requirement_units(value: str) -> list[str]:
    text = re.sub(r"\r\n?", "\n", _text(value))
    rough_units = re.split(r"[\n。；;！？!?]+", text)
    units: list[str] = []
    seen: set[str] = set()
    for raw in rough_units:
        unit = re.sub(r"\s+", " ", raw).strip(" -•*#\t")
        if len(unit) < 4:
            continue
        if len(unit) > 180:
            unit = unit[:180]
        key = unit.lower()
        if key in seen:
            continue
        seen.add(key)
        units.append(unit)
        if len(units) >= 80:
            break
    return units


def _unit_token_score(unit: str, tokens: tuple[str, ...]) -> int:
    lowered = unit.lower()
    score = 0
    for token in tokens:
        needle = _text(token).lower()
        if needle and needle in lowered:
            score += 1
    return score


def _select_fallback_evidence(
    units: list[str],
    *,
    tokens: tuple[str, ...],
    used_indices: set[int],
    fallback_index: int,
) -> tuple[str, int | None]:
    scored: list[tuple[int, int, str]] = []
    for index, unit in enumerate(units):
        score = _unit_token_score(unit, tokens)
        if index in used_indices:
            score -= 1
        if score > 0:
            scored.append((score, -index, unit))
    if scored:
        score, negative_index, unit = max(scored)
        return unit, -negative_index
    if not units:
        return "", None
    index = min(max(0, fallback_index), len(units) - 1)
    return units[index], index


def build_fallback_current_requirement_blueprint(
    requirement_text: str,
    *,
    project_id: int | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    units = _requirement_units(requirement_text)
    if len(_text(requirement_text)) < 120 and len(units) < 2:
        return []
    used_indices: set[int] = set()
    steps: list[dict[str, Any]] = []
    for index, profile in enumerate(_FALLBACK_STAGE_PROFILES):
        evidence, unit_index = _select_fallback_evidence(
            units,
            tokens=tuple(profile.get("tokens") or ()),
            used_indices=used_indices,
            fallback_index=index,
        )
        if unit_index is not None:
            used_indices.add(unit_index)
        stage_kind = str(profile["stage_kind"])
        action = evidence or str(profile["action"])
        label = str(profile["label"])
        steps.append(
            {
                "id": str(profile["id"]),
                "label": label,
                "action": action[:160],
                "stage_kind": stage_kind,
                "actor": "business_user",
                "state_out": f"{stage_kind}_done",
                "match_keywords": _normalize_keywords(
                    {
                        "match_keywords": list(profile.get("tokens") or ())[:4],
                    },
                    label,
                    action,
                ),
                "evidence": [evidence] if evidence else [],
            }
        )
    if len(steps) < 3:
        return []
    return normalize_current_requirement_blueprint_payload(
        {
            "workflow_blueprints": [
                {
                    "workflow_id": "current_requirement_fallback_main_flow",
                    "name": "当前需求主流程兜底蓝图",
                    "confidence": 0.52,
                    "steps": steps,
                }
            ]
        },
        requirement_text=requirement_text,
        project_id=project_id,
        user_id=user_id,
    )


def _normalize_stage_kind(value: Any, *, index: int, total: int) -> str:
    raw = re.sub(r"\s+", "_", _text(value).lower()).strip("_")
    if raw in _ALLOWED_STAGE_KINDS:
        return raw
    if raw in _STAGE_KIND_ALIASES:
        return _STAGE_KIND_ALIASES[raw]
    if index == 1:
        return "entry"
    if index == total:
        return "downstream_visibility" if total >= 4 else "commit"
    if index == total - 1 and total >= 4:
        return "commit"
    return "configure"


_STAGE_KIND_SUPPORT_TOKENS: dict[str, tuple[str, ...]] = {
    "entry": (
        "entry",
        "open",
        "enter",
        "navigate",
        "home",
        "list",
        "入口",
        "进入",
        "打开",
        "跳转",
        "首页",
        "列表",
    ),
    "configure": (
        "configure",
        "select",
        "choose",
        "switch",
        "setting",
        "配置",
        "设置",
        "选择",
        "切换",
        "分区",
    ),
    "edit": (
        "edit",
        "input",
        "fill",
        "compose",
        "upload",
        "编辑",
        "填写",
        "输入",
        "上传",
    ),
    "preview": (
        "preview",
        "check",
        "confirm",
        "预览",
        "检查",
        "确认",
        "待提交",
    ),
    "commit": (
        "commit",
        "submit",
        "publish",
        "save",
        "send",
        "提交",
        "发布",
        "保存",
        "发送",
        "完成操作",
    ),
    "downstream_visibility": (
        "visible",
        "visibility",
        "display",
        "show",
        "sync",
        "message",
        "notification",
        "updated",
        "latest",
        "可见",
        "展示",
        "显示",
        "同步",
        "刷新",
        "消息",
        "通知",
        "红点",
        "更新",
        "最新",
    ),
    "consume": (
        "consume",
        "open",
        "view",
        "click",
        "detail",
        "interact",
        "comment",
        "reply",
        "查看",
        "打开",
        "点击",
        "详情",
        "阅读",
        "互动",
        "评论",
        "回复",
    ),
    "completion_sync": (
        "complete",
        "completion",
        "closed",
        "result",
        "record",
        "status",
        "完成",
        "闭环",
        "结果",
        "记录",
        "状态",
        "生效",
    ),
}


_EXPLICIT_STAGE_KIND_SUPPORT_TOKENS: dict[str, tuple[str, ...]] = {
    "edit": _STAGE_KIND_SUPPORT_TOKENS["edit"],
    "preview": (
        *_STAGE_KIND_SUPPORT_TOKENS["preview"],
        "view",
        "detail",
        "content",
        "查看",
        "详情",
        "内容",
        "一致",
    ),
    "downstream_visibility": (
        *_STAGE_KIND_SUPPORT_TOKENS["downstream_visibility"],
        "列表",
        "结果",
        "变更",
        "传播",
        "list",
        "result",
        "propagate",
    ),
}


def _contains_support_token(text: str, tokens: tuple[str, ...]) -> bool:
    haystack = _text(text).lower()
    if not haystack:
        return False
    for token in tokens:
        needle = _text(token).lower()
        if needle and needle in haystack:
            return True
    return False


def _stage_kind_support_text(
    raw_step: dict[str, Any],
    *,
    label: str,
    action: str,
    evidence: list[str],
) -> str:
    values: list[Any] = [
        label,
        action,
        raw_step.get("description"),
        raw_step.get("assertion"),
        raw_step.get("module"),
        raw_step.get("feature"),
        raw_step.get("expected_result"),
        raw_step.get("target_surface"),
        raw_step.get("visible_surface"),
        raw_step.get("match_keywords"),
        raw_step.get("keywords"),
        raw_step.get("aliases"),
        evidence,
    ]
    return " ".join(part for part in (_flatten_text(value) for value in values) if part)


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(part for part in (_flatten_text(item) for item in value.values()) if part)
    if isinstance(value, (list, tuple, set)):
        return " ".join(part for part in (_flatten_text(item) for item in value) if part)
    return _text(value)


def _infer_stage_kind_from_step_text(text: str, *, fallback: str) -> str:
    normalized_fallback = fallback if fallback in _ALLOWED_STAGE_KINDS else "configure"
    ordered_kinds = (
        "commit",
        "downstream_visibility",
        "completion_sync",
        "preview",
        "edit",
        "configure",
        "consume",
        "entry",
    )
    for kind in ordered_kinds:
        if _contains_support_token(text, _STAGE_KIND_SUPPORT_TOKENS[kind]):
            return kind
    return normalized_fallback


def _infer_post_commit_closure_stage_kind(text: str, *, fallback: str) -> str:
    normalized_fallback = fallback if fallback in _ALLOWED_STAGE_KINDS else "downstream_visibility"
    for kind in ("completion_sync", "downstream_visibility", "consume"):
        if _contains_support_token(text, _STAGE_KIND_SUPPORT_TOKENS[kind]):
            return kind
    return normalized_fallback if normalized_fallback in {"downstream_visibility", "consume", "completion_sync"} else "downstream_visibility"


def _stage_kind_supported_by_step_text(stage_kind: str, text: str) -> bool:
    tokens = _EXPLICIT_STAGE_KIND_SUPPORT_TOKENS.get(stage_kind) or _STAGE_KIND_SUPPORT_TOKENS.get(stage_kind)
    if not tokens:
        return True
    return _contains_support_token(text, tokens)


def _stage_kind_from_step_identifier(value: Any) -> str:
    identifier = _text(value).lower()
    if not identifier:
        return ""
    if identifier in _ALLOWED_STAGE_KINDS:
        return identifier
    if identifier in _STAGE_KIND_ALIASES:
        return _STAGE_KIND_ALIASES[identifier]
    compact = re.sub(r"[^a-z0-9_]+", "_", identifier)
    parts = {part for part in compact.split("_") if part}
    if {"downstream", "visibility"} <= parts or "downstream_visibility" in compact:
        return "downstream_visibility"
    if "completion" in parts or "sync" in parts:
        return "completion_sync"
    for kind in ("preview", "commit", "consume", "configure", "edit", "entry"):
        if kind in parts:
            return kind
    return ""


def _list_text(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    elif _text(value):
        values = [value]
    else:
        values = []
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = re.sub(r"\s+", " ", _text(raw)).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _normalize_keywords(step: dict[str, Any], label: str, action: str) -> list[str]:
    values: list[Any] = []
    for key in ("match_keywords", "keywords", "aliases"):
        raw = step.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    values.extend([label, action, step.get("module"), step.get("feature")])
    keywords: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = re.sub(r"\s+", " ", _text(raw)).strip()
        if not text or len(text) < 2:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(text[:80])
        if len(keywords) >= _MAX_KEYWORDS_PER_STEP:
            break
    return keywords


def _payload_to_blueprint_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("workflow_blueprints", "blueprints", "workflows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if isinstance(payload.get("steps"), list):
            return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _repair_post_commit_closure_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commit_indices = [
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict) and _text(step.get("stage_kind")).lower() == "commit"
    ]
    if not commit_indices:
        return steps
    first_commit_index = commit_indices[0]
    closure_kinds = {"downstream_visibility", "consume", "completion_sync"}
    if any(
        _text(step.get("stage_kind")).lower() in closure_kinds
        for step in steps[first_commit_index + 1 :]
        if isinstance(step, dict)
    ):
        return steps
    post_commit_indices = [
        index for index in range(first_commit_index + 1, len(steps)) if isinstance(steps[index], dict)
    ]
    if not post_commit_indices:
        return steps
    target_index = post_commit_indices[-1]
    target = dict(steps[target_index])
    support_text = _stage_kind_support_text(
        target,
        label=_text(target.get("label")),
        action=_text(target.get("action")),
        evidence=_list_text(target.get("evidence") or target.get("source_evidence") or target.get("requirement_evidence")),
    )
    repaired_stage_kind = _infer_post_commit_closure_stage_kind(
        support_text,
        fallback=_text(target.get("stage_kind")).lower() or "downstream_visibility",
    )
    if repaired_stage_kind not in closure_kinds:
        return steps
    original_stage_kind = _text(target.get("stage_kind")).lower()
    if repaired_stage_kind == original_stage_kind:
        return steps
    updated_steps = [dict(step) for step in steps]
    target["stage_kind"] = repaired_stage_kind
    target["stage_kind_original"] = _text(target.get("stage_kind_original")) or original_stage_kind
    target["stage_kind_adjusted"] = True
    target["stage_kind_adjustment_reason"] = "post_commit_closure_repair"
    updated_steps[target_index] = target
    return updated_steps


def normalize_current_requirement_blueprint_payload(
    payload: Any,
    *,
    requirement_text: str,
    project_id: int | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    blueprints: list[dict[str, Any]] = []
    requirement_hash = _fingerprint(requirement_text)
    for bp_index, raw_blueprint in enumerate(_payload_to_blueprint_candidates(payload), start=1):
        raw_steps = raw_blueprint.get("steps") or raw_blueprint.get("edges")
        if not isinstance(raw_steps, list):
            continue
        candidate_steps = [step for step in raw_steps if isinstance(step, dict)]
        if len(candidate_steps) < 2:
            continue
        workflow_name = _text(raw_blueprint.get("name") or raw_blueprint.get("title") or "current requirement workflow")
        workflow_id = _slug(
            raw_blueprint.get("workflow_id") or raw_blueprint.get("id") or workflow_name,
            fallback=f"current_requirement_workflow_{bp_index}",
        )
        normalized_steps: list[dict[str, Any]] = []
        previous_state = "initial"
        total = min(len(candidate_steps), _MAX_STEPS)
        for step_index, raw_step in enumerate(candidate_steps[:_MAX_STEPS], start=1):
            original_stage_kind = _normalize_stage_kind(
                raw_step.get("stage_kind") or raw_step.get("kind"),
                index=step_index,
                total=total,
            )
            label = _text(raw_step.get("label") or raw_step.get("title") or raw_step.get("action"))
            action = _text(raw_step.get("action") or raw_step.get("description") or label)
            if not label and action:
                label = action
            if not label:
                continue
            explicit_state_out = _text(raw_step.get("state_out") or raw_step.get("target_state"))
            evidence = _list_text(raw_step.get("evidence") or raw_step.get("source_evidence") or raw_step.get("requirement_evidence"))
            support_text = _stage_kind_support_text(
                raw_step,
                label=label,
                action=action,
                evidence=evidence,
            )
            stage_kind = original_stage_kind
            stage_kind_adjusted = False
            identifier_stage_kind = _stage_kind_from_step_identifier(raw_step.get("id"))
            if (
                identifier_stage_kind
                and identifier_stage_kind != stage_kind
                and _stage_kind_supported_by_step_text(identifier_stage_kind, support_text)
            ):
                stage_kind = identifier_stage_kind
                stage_kind_adjusted = True
            if not _stage_kind_supported_by_step_text(stage_kind, support_text):
                inferred_stage_kind = _infer_stage_kind_from_step_text(support_text, fallback=stage_kind)
                if inferred_stage_kind != stage_kind:
                    stage_kind = inferred_stage_kind
                    stage_kind_adjusted = True
            step_id = _slug(raw_step.get("id") or f"{stage_kind}_{step_index:02d}", fallback=f"step_{step_index:02d}")
            state_out = _slug(explicit_state_out or f"{step_id}_done", fallback=f"step_{step_index:02d}_done")
            source_actor_role = _text(raw_step.get("actor") or raw_step.get("role"))
            role_context = " ".join(
                str(part or "")
                for part in (
                    source_actor_role,
                    label,
                    action,
                    raw_step.get("module"),
                    raw_step.get("feature"),
                )
                if str(part or "").strip()
            )
            normalized_steps.append(
                {
                    **raw_step,
                    "id": step_id,
                    "workflow_id": workflow_id,
                    "label": label[:120],
                    "action": action[:160] if action else label[:160],
                    "state_in": previous_state,
                    "state_out": state_out,
                    "source_state": previous_state,
                    "target_state": state_out,
                    "stage_kind": stage_kind,
                    "actor": normalize_actor_role(source_actor_role, fallback_text=role_context),
                    "source_actor_role": source_actor_role or "business_user",
                    "path_type": "positive",
                    "blocking": False,
                    "destructive": False,
                    "can_advance_main_flow": True,
                    "allow_bridge": True,
                    "match_keywords": _normalize_keywords(raw_step, label, action),
                    "evidence": evidence[:3],
                    "source": CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE,
                    "stage_kind_original": original_stage_kind if stage_kind_adjusted else "",
                    "stage_kind_adjusted": bool(stage_kind_adjusted),
                }
            )
            previous_state = state_out
        if len(normalized_steps) < 2:
            continue
        normalized_steps = _repair_post_commit_closure_steps(normalized_steps)
        confidence = raw_blueprint.get("confidence")
        try:
            confidence_value = max(0.0, min(1.0, float(confidence)))
        except Exception:
            confidence_value = 0.72
        blueprints.append(
            {
                **raw_blueprint,
                "id": workflow_id,
                "workflow_id": workflow_id,
                "name": workflow_name[:120],
                "project_id": int(project_id or 0),
                "user_id": int(user_id or 0),
                "source_type": CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE,
                "repository_source": CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE,
                "trusted": False,
                "confidence": round(confidence_value, 4),
                "source_content_hash": requirement_hash,
                "steps": normalized_steps,
                "edges": normalized_steps,
            }
        )
        if len(blueprints) >= _MAX_BLUEPRINTS:
            break
    return blueprints


def _build_blueprint_prompt() -> str:
    return """
Extract ONE executable main flow from the CURRENT requirement document only.
Do not generate test cases. Do not use historical examples, RAG matches, or external product knowledge.
Return compact minified JSON only. No markdown. No explanation.

Schema:
{"workflow_blueprints":[{"workflow_id":"short_id","name":"short name","confidence":0.8,"steps":[{"id":"entry","label":"<=18 chars","action":"<=40 chars","stage_kind":"entry|configure|edit|preview|commit|downstream_visibility|consume|completion_sync","actor":"admin|supervisor|student|member|student_free|business_user","state_out":"short_state","match_keywords":["<=8 chars"],"evidence":["<=30 chars"]}]}]}

Rules:
- Use 6 positive main-flow steps when possible; use 4 to 5 only when the document truly has fewer executable stages.
- Prefer a real user action chain, ordered as entry -> configure -> edit -> commit -> downstream_visibility -> consume/completion_sync.
- If the input contains [Requirement Understanding], use its visual_facts only to identify screens, controls, visible states, and interactions that support the CURRENT text.
- Do not turn visual-only display facts, OCR diagnostics, evidence alignment scores, or parser metadata into main-flow steps unless the requirement text also describes an executable user action.
- Use preview/check only when the requirement has an explicit non-blocking happy-path content, form, image, or result preview before commit.
- For content, community, workflow, learning, commerce, or operational features, list/detail/create/edit/comment/react/audit/message/result visibility can be a main flow when supported by the requirement.
- Exclude boundary, exception, permission denial, isolated sorting/display checks, and pure visual style checks.
- Do not use quota/limit/exceeded/count cap/permission failure/error/empty-state checks as main-flow preview or validation steps.
- Never mention quota, limit, count cap, exceeded, permission failure, error, empty-state, or rejection evidence in main-flow steps.
- Preview/check steps must confirm that the happy-path content, selection, image, form, or result is ready to submit; they must not block the flow.
- Normalize persona labels into the closest actor enum. Use business_user for generic users/customers/requesters/operators.
- If the requirement is Chinese, write name/label/action/match_keywords/evidence in concise Chinese.
- Every step must be supported by the current requirement text.
- If no executable flow is present, return {"workflow_blueprints":[]}.
""".strip()


def _extract_requirement_understanding_stats(requirement: str) -> dict[str, Any]:
    marker = "[Requirement Understanding]"
    text = _text(requirement)
    marker_index = text.find(marker)
    if marker_index < 0:
        return {"requirement_understanding_used": False}
    section = text[marker_index + len(marker) :].strip()
    next_section = section.find("\n\n[")
    if next_section >= 0:
        section = section[:next_section].strip()
    stats: dict[str, Any] = {"requirement_understanding_used": True}
    try:
        payload = json.loads(section)
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        stats.update(
            {
                "requirement_understanding_visual_fact_count": int(payload.get("visual_fact_count") or 0),
                "requirement_understanding_invalid_visual_block_count": int(
                    payload.get("invalid_visual_block_count") or 0
                ),
            }
        )
    return stats


def _fallback_blueprint_result(
    *,
    requirement: str,
    diagnostics: dict[str, Any],
    reason: str,
    project_id: int | None,
    user_id: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    blueprints = build_fallback_current_requirement_blueprint(
        requirement,
        project_id=project_id,
        user_id=user_id,
    )
    if not blueprints:
        return [], diagnostics
    for blueprint in blueprints:
        blueprint["fallback"] = True
        blueprint["allow_final_materialization"] = False
        for step in blueprint.get("steps") or []:
            if isinstance(step, dict):
                step["allow_bridge"] = False
    diagnostics.update(
        {
            "current_requirement_blueprint_status": f"fallback_applied_after_{reason}",
            "current_requirement_blueprint_fallback_reason": reason,
            "current_requirement_blueprint_count": int(len(blueprints)),
            "current_requirement_blueprint_step_count": int(
                sum(len(item.get("steps") or []) for item in blueprints if isinstance(item, dict))
            ),
            "current_requirement_blueprint_fallback": True,
        }
    )
    diagnostics["current_requirement_blueprint_ids"] = [
        str(item.get("id") or "") for item in blueprints if str(item.get("id") or "").strip()
    ][:5]
    return blueprints, diagnostics


def extract_current_requirement_blueprints(
    *,
    client: Any,
    requirement_text: str,
    db: Any = None,
    project_id: int | None = None,
    user_id: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requirement = _text(requirement_text)
    diagnostics: dict[str, Any] = {
        "current_requirement_blueprint_status": "skipped_empty_requirement",
        "current_requirement_blueprint_count": 0,
        "current_requirement_blueprint_step_count": 0,
        "current_requirement_blueprint_source": CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE,
    }
    max_tokens = current_requirement_blueprint_max_tokens()
    diagnostics["current_requirement_blueprint_max_tokens"] = int(max_tokens)
    diagnostics.update(_extract_requirement_understanding_stats(requirement))
    if not requirement:
        return [], diagnostics
    if client is None or not hasattr(client, "generate_response"):
        diagnostics["current_requirement_blueprint_status"] = "skipped_no_client"
        return [], diagnostics
    try:
        raw = client.generate_response(
            _compact_requirement(requirement),
            _build_blueprint_prompt(),
            db=db,
            max_tokens=max_tokens,
            task_type="generation",
        )
    except Exception as exc:
        diagnostics.update(
            {
                "current_requirement_blueprint_status": "model_call_failed",
                "current_requirement_blueprint_error": str(exc)[:240],
            }
        )
        return _fallback_blueprint_result(
            requirement=requirement,
            diagnostics=diagnostics,
            reason="model_call_failed",
            project_id=project_id,
            user_id=user_id,
        )
    raw_text = _text(raw)
    diagnostics["current_requirement_blueprint_raw_chars"] = int(len(raw_text))
    if not raw_text or raw_text.startswith("Error:") or raw_text.startswith("Exception"):
        diagnostics.update(
            {
                "current_requirement_blueprint_status": "model_error",
                "current_requirement_blueprint_error": raw_text[:240],
            }
        )
        return _fallback_blueprint_result(
            requirement=requirement,
            diagnostics=diagnostics,
            reason="model_error",
            project_id=project_id,
            user_id=user_id,
        )
    try:
        parsed = clean_and_parse_json(raw_text)
    except Exception:
        try:
            parsed = json.loads(raw_text)
        except Exception as exc:
            diagnostics.update(
                {
                    "current_requirement_blueprint_status": "parse_failed",
                    "current_requirement_blueprint_error": str(exc)[:240],
                }
            )
            return _fallback_blueprint_result(
                requirement=requirement,
                diagnostics=diagnostics,
                reason="parse_failed",
                project_id=project_id,
                user_id=user_id,
            )
    blueprints = normalize_current_requirement_blueprint_payload(
        parsed,
        requirement_text=requirement,
        project_id=project_id,
        user_id=user_id,
    )
    diagnostics.update(
        {
            "current_requirement_blueprint_status": "applied" if blueprints else "no_candidate",
            "current_requirement_blueprint_count": int(len(blueprints)),
            "current_requirement_blueprint_step_count": int(
                sum(len(item.get("steps") or []) for item in blueprints if isinstance(item, dict))
            ),
        }
    )
    if blueprints:
        diagnostics["current_requirement_blueprint_ids"] = [
            str(item.get("id") or "") for item in blueprints if str(item.get("id") or "").strip()
        ][:5]
        return blueprints, diagnostics
    return _fallback_blueprint_result(
        requirement=requirement,
        diagnostics=diagnostics,
        reason="no_candidate",
        project_id=project_id,
        user_id=user_id,
    )


def _has_authoritative_existing_blueprint(state: FeedbackControlState) -> bool:
    for blueprint in state.workflow_blueprints or []:
        if not isinstance(blueprint, dict):
            continue
        repository_source = _text(blueprint.get("repository_source") or blueprint.get("source"))
        source_type = _text(blueprint.get("source_type"))
        if repository_source == CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE:
            return True
        if source_type == CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE:
            return True
        if is_trusted_workflow_contract(blueprint):
            return True
    return False


def merge_current_requirement_blueprint_control_state(
    control_state: FeedbackControlState | dict[str, Any] | None,
    *,
    client: Any,
    requirement_text: str,
    db: Any = None,
    project_id: int | None = None,
    user_id: int | None = None,
) -> FeedbackControlState:
    state = FeedbackControlState.from_any(control_state)
    if _has_authoritative_existing_blueprint(state):
        return state.merge(
            FeedbackControlState(
                source_meta={
                    "sources": ["current_requirement_blueprint"],
                    "current_requirement_blueprint_status": "skipped_authoritative_workflow_blueprint",
                    "current_requirement_blueprint_count": 0,
                    "current_requirement_blueprint_existing_count": int(len(state.workflow_blueprints)),
                }
            )
        )
    blueprints, diagnostics = extract_current_requirement_blueprints(
        client=client,
        requirement_text=requirement_text,
        db=db,
        project_id=project_id,
        user_id=user_id,
    )
    current_state = FeedbackControlState(
        workflow_blueprints=blueprints,
        source_meta={
            "sources": ["current_requirement_blueprint"],
            **diagnostics,
        },
    )
    if blueprints:
        return current_state.merge(state)
    return state.merge(current_state)


__all__ = [
    "CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE",
    "CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE",
    "build_fallback_current_requirement_blueprint",
    "extract_current_requirement_blueprints",
    "merge_current_requirement_blueprint_control_state",
    "normalize_current_requirement_blueprint_payload",
]
