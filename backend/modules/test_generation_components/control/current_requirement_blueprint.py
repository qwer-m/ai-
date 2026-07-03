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
            stage_kind = _normalize_stage_kind(raw_step.get("stage_kind") or raw_step.get("kind"), index=step_index, total=total)
            label = _text(raw_step.get("label") or raw_step.get("title") or raw_step.get("action"))
            action = _text(raw_step.get("action") or raw_step.get("description") or label)
            if not label and action:
                label = action
            if not label:
                continue
            step_id = _slug(raw_step.get("id") or f"{stage_kind}_{step_index:02d}", fallback=f"step_{step_index:02d}")
            explicit_state_out = _text(raw_step.get("state_out") or raw_step.get("target_state"))
            state_out = _slug(explicit_state_out or f"{step_id}_done", fallback=f"step_{step_index:02d}_done")
            evidence = _list_text(raw_step.get("evidence") or raw_step.get("source_evidence") or raw_step.get("requirement_evidence"))
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
                }
            )
            previous_state = state_out
        if len(normalized_steps) < 2:
            continue
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
You are extracting an execution blueprint from the CURRENT requirement document only.

Do not generate test cases.
Do not use historical examples, RAG matches, or external product knowledge.
Return only JSON.

Find the main positive business path a tester should execute first. Prefer a compact chain:
entry -> configure/edit -> preview/check -> commit/save -> downstream visibility/share/sync.

Output schema:
{
  "workflow_blueprints": [
    {
      "workflow_id": "short_stable_id",
      "name": "business workflow name",
      "confidence": 0.0,
      "steps": [
        {
          "id": "entry",
          "label": "human readable step",
          "action": "tester action",
          "stage_kind": "entry|configure|edit|preview|commit|downstream_visibility|consume|completion_sync",
          "actor": "admin|supervisor|student|member|student_free|business_user",
          "state_out": "semantic_state_after_step",
          "match_keywords": ["words from the current requirement"],
          "evidence": ["short requirement phrase that supports this step"]
        }
      ]
    }
  ]
}

Rules:
- Use 3 to 8 steps unless the document truly has fewer.
- Keep only positive main-flow steps. Exclude boundary, exception, permission denial, and pure visual checks.
- Normalize persona labels into the closest actor enum.
- Use business_user for generic users/customers/requesters/operators when the document does not explicitly say student/member/admin/supervisor.
- Every step must be supported by the current requirement text.
- If no executable business flow is present, return {"workflow_blueprints": []}.
""".strip()


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
        return [], diagnostics
    raw_text = _text(raw)
    diagnostics["current_requirement_blueprint_raw_chars"] = int(len(raw_text))
    if not raw_text or raw_text.startswith("Error:") or raw_text.startswith("Exception"):
        diagnostics.update(
            {
                "current_requirement_blueprint_status": "model_error",
                "current_requirement_blueprint_error": raw_text[:240],
            }
        )
        return [], diagnostics
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
            return [], diagnostics
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
    "extract_current_requirement_blueprints",
    "merge_current_requirement_blueprint_control_state",
    "normalize_current_requirement_blueprint_payload",
]
