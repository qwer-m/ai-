from __future__ import annotations

import json
from typing import Any, Callable


def persist_generation_diag(
    *,
    db: Any,
    active_db_session: bool,
    log_entry_model: Any,
    project_id: int,
    user_id: int,
    payload: dict[str, Any],
) -> bool:
    if not active_db_session:
        return False
    try:
        db.add(
            log_entry_model(
                project_id=project_id,
                user_id=user_id,
                log_type="system",
                message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
            )
        )
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def emit_json_biz_key_diag(
    *,
    prompt_context: dict[str, Any],
    db: Any,
    active_db_session: bool,
    log_entry_model: Any,
    project_id: int,
    user_id: int,
    request_id: str,
) -> bool:
    if not active_db_session:
        return False
    try:
        isolation_payload = dict(prompt_context.get("biz_key_isolation_log") or {})
        if not isolation_payload:
            return False
        isolation_payload.update(
            {
                "project_id": int(project_id),
                "request_id": request_id,
                "source": "generate_test_cases_json",
            }
        )
        return persist_generation_diag(
            db=db,
            active_db_session=active_db_session,
            log_entry_model=log_entry_model,
            project_id=project_id,
            user_id=user_id,
            payload=isolation_payload,
        )
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"Failed to emit biz key isolation log(json): {exc}")
        return False


def emit_json_prompt_context_intake_diag(
    *,
    client: Any,
    prompt_context: dict[str, Any],
    context_result: dict[str, Any],
    requirement: str,
    kb_context: str,
    base_prompt: str,
    system_prompt: str,
    doc_type: str,
    compress: bool,
    project_id: int,
    user_id: int,
    request_id: str,
    batch_index: int,
    expected_count: int,
    multi_pass: bool,
    generation_mode: str,
    db: Any,
    active_db_session: bool,
    log_entry_model: Any,
    build_prompt_context_intake_diagnostics_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if not active_db_session:
        return None
    try:
        intake_model = str(client.select_model(f"{system_prompt or ''}{requirement or ''}", task_type="generation"))
    except Exception:
        intake_model = str(getattr(client, "model", "") or "")
    try:
        payload = build_prompt_context_intake_diagnostics_fn(
            prompt_context=prompt_context,
            context_result=context_result if isinstance(context_result, dict) else {},
            requirement=requirement or "",
            kb_context=kb_context or "",
            base_prompt=base_prompt or "",
            system_prompt=system_prompt or "",
            mode="json",
            doc_type=doc_type,
            compress=bool(compress),
            project_id=project_id,
            request_id=request_id,
            batch_index=batch_index,
            total_batches=None,
            attempt=1,
            expected_count=int(expected_count or 0),
            multi_pass=bool(multi_pass),
            generation_mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
            model=intake_model,
            max_output_tokens=getattr(client, "max_tokens", None),
        )
        persist_generation_diag(
            db=db,
            active_db_session=active_db_session,
            log_entry_model=log_entry_model,
            project_id=project_id,
            user_id=user_id,
            payload=payload,
        )
        return payload
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"Failed to emit prompt context intake log(json): {exc}")
        return None
