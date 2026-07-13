from typing import Any
import json

from sqlalchemy.orm import Session

from core.db.models import LogEntry
from modules.domain.stage25_switches import STAGE25_SWITCHES
from modules.testing.test_generation_components.prompting.generation_diagnostics import (
    build_context_source_log,
    build_final_context_trace,
)


class LegacyGenerationContextTraceMixin:

    def _append_reason_chain(self, reason_chain: list[str], reason: str) -> None:
        """统一维护 reason_chain（去空、去重、保持顺序）。"""
        if not STAGE25_SWITCHES.guard_reason_chain_enabled:
            return
        item = str(reason or "").strip()
        if not item:
            return
        if reason_chain and reason_chain[-1] == item:
            return
        reason_chain.append(item)

    def _emit_context_source_log(
        self,
        *,
        db: Session | None,
        project_id: int,
        user_id: int | None,
        context_result: dict[str, Any] | None,
        gate_debug: dict[str, Any] | None,
        doc_type: str,
        compress: bool,
        requirement_length: int,
    ) -> None:
        """输出最终生成上下文来源日志（阶段2.5证据化）。"""
        if (not self._is_active_db_session(db)) or (not STAGE25_SWITCHES.final_context_source_log_enabled):
            return
        try:
            payload = build_context_source_log(
                context_result=context_result,
                gate_debug=gate_debug,
                doc_type=doc_type,
                compress=compress,
                requirement_length=requirement_length,
            )
            db.add(
                LogEntry(
                    project_id=project_id,
                    user_id=user_id,
                    log_type="system",
                    message=f"GEN_CONTEXT_SOURCE:{json.dumps(payload, ensure_ascii=False)}",
                )
            )
            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"Failed to emit context source log: {e}")

    def _emit_final_context_trace(
        self,
        *,
        db: Session | None,
        project_id: int,
        user_id: int | None,
        request_id: str,
        context_result: dict[str, Any] | None,
        gate_debug: dict[str, Any] | None,
        fallback_reason: str = "",
        abort_code: str = "",
        compressed_chars: int = 0,
    ) -> None:
        """
        正式模型调用前输出最终上下文来源证据链。
        """
        if not self._is_active_db_session(db):
            return
        try:
            payload = build_final_context_trace(
                project_id=project_id,
                request_id=request_id,
                context_result=context_result,
                gate_debug=gate_debug,
                fallback_reason=fallback_reason,
                abort_code=abort_code,
                compressed_chars=compressed_chars,
            )
            db.add(
                LogEntry(
                    project_id=project_id,
                    user_id=user_id,
                    log_type="system",
                    message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                )
            )
            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"Failed to emit final context trace: {e}")
