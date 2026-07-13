from __future__ import annotations

import json
from typing import Any, Callable


def append_stream_gen_diag(payload: dict[str, Any], stream_batch_diags: list[str]) -> None:
    try:
        stream_batch_diags.append(f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}\n")
    except Exception:
        pass


def persist_gen_diag(
    *,
    db: Any,
    is_active_db_session: bool,
    log_entry_model: Any,
    project_id: int,
    user_id: int,
    payload: dict[str, Any],
) -> None:
    if not is_active_db_session:
        return
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
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def emit_stream_gen_diag(
    *,
    db: Any,
    is_active_db_session: bool,
    log_entry_model: Any,
    project_id: int,
    user_id: int,
    payload: dict[str, Any],
    stream_batch_diags: list[str],
) -> None:
    persist_gen_diag(
        db=db,
        is_active_db_session=is_active_db_session,
        log_entry_model=log_entry_model,
        project_id=project_id,
        user_id=user_id,
        payload=payload,
    )
    append_stream_gen_diag(payload, stream_batch_diags)


def emit_biz_key_diag(
    *,
    prompt_context: dict[str, Any],
    already_emitted: bool,
    db: Any,
    is_active_db_session: bool,
    log_entry_model: Any,
    project_id: int,
    user_id: int,
    request_id: str,
) -> bool:
    if already_emitted or not is_active_db_session:
        return already_emitted
    try:
        payload = dict(prompt_context.get("biz_key_isolation_log") or {})
        if not payload:
            return already_emitted
        payload.update(
            {
                "project_id": int(project_id),
                "request_id": request_id,
                "source": "generate_test_cases_stream",
            }
        )
        persist_gen_diag(
            db=db,
            is_active_db_session=is_active_db_session,
            log_entry_model=log_entry_model,
            project_id=project_id,
            user_id=user_id,
            payload=payload,
        )
        return True
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"Failed to emit biz key isolation log(stream): {exc}")
        return already_emitted


def emit_stream_batch_quality_diag(
    *,
    batch_metric: dict[str, Any],
    db: Any,
    is_active_db_session: bool,
    log_entry_model: Any,
    project_id: int,
    user_id: int,
    request_id: str,
    current_biz_key: str,
    multi_pass: bool,
    generation_mode: str,
    stream_batch_diags: list[str],
) -> dict[str, Any]:
    payload = {
        "kind": "stream_batch_quality",
        "project_id": int(project_id),
        "request_id": str(request_id or ""),
        "current_biz_key": str(current_biz_key or "unknown"),
        "multi_pass": bool(multi_pass),
        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
        **dict(batch_metric or {}),
    }
    emit_stream_gen_diag(
        db=db,
        is_active_db_session=is_active_db_session,
        log_entry_model=log_entry_model,
        project_id=project_id,
        user_id=user_id,
        payload=payload,
        stream_batch_diags=stream_batch_diags,
    )
    return payload


def emit_prompt_context_intake_diag(
    *,
    prompt_context: dict[str, Any],
    base_prompt_text: str,
    system_prompt_text: str,
    batch_index_value: int,
    total_batches_value: int,
    attempt_value: int,
    requested_count: int,
    client: Any,
    requirement: str,
    context_result: dict[str, Any],
    kb_context: str,
    doc_type: str,
    compress: bool,
    project_id: int,
    user_id: int,
    request_id: str,
    multi_pass: bool,
    generation_mode: str,
    db: Any,
    is_active_db_session: bool,
    log_entry_model: Any,
    build_prompt_context_intake_diagnostics_fn: Callable[..., dict[str, Any]],
    stream_batch_diags: list[str],
) -> dict[str, Any]:
    actual_model = ""
    try:
        actual_model = str(
            client.select_model(f"{system_prompt_text or ''}{requirement or ''}", task_type="generation")
        )
    except Exception:
        actual_model = str(getattr(client, "model", "") or "")
    payload = build_prompt_context_intake_diagnostics_fn(
        prompt_context=prompt_context,
        context_result=context_result if isinstance(context_result, dict) else {},
        requirement=requirement or "",
        kb_context=kb_context or "",
        base_prompt=base_prompt_text or "",
        system_prompt=system_prompt_text or "",
        mode="stream",
        doc_type=doc_type,
        compress=compress,
        project_id=project_id,
        request_id=request_id,
        batch_index=batch_index_value,
        total_batches=total_batches_value,
        attempt=attempt_value,
        expected_count=requested_count,
        multi_pass=bool(multi_pass),
        generation_mode=generation_mode or ("multi_pass" if multi_pass else "single_pass"),
        model=actual_model,
        max_output_tokens=getattr(client, "max_tokens", None),
    )
    emit_stream_gen_diag(
        db=db,
        is_active_db_session=is_active_db_session,
        log_entry_model=log_entry_model,
        project_id=project_id,
        user_id=user_id,
        payload=payload,
        stream_batch_diags=stream_batch_diags,
    )
    return payload


def emit_stream_batch_token_usage_diag(
    *,
    payload: dict[str, Any],
    db: Any,
    is_active_db_session: bool,
    log_entry_model: Any,
    project_id: int,
    user_id: int,
    stream_batch_diags: list[str],
) -> None:
    emit_stream_gen_diag(
        db=db,
        is_active_db_session=is_active_db_session,
        log_entry_model=log_entry_model,
        project_id=project_id,
        user_id=user_id,
        payload=payload,
        stream_batch_diags=stream_batch_diags,
    )
