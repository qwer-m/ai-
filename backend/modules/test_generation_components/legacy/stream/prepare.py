from typing import Any, Iterator

from sqlalchemy.orm import Session

from core.db.models import TestGeneration
from modules.testing.test_generation_components.prompting.generation_diagnostics import build_gate_reason_chain
from modules.testing.test_generation_components.legacy.adapters import (
    count_unique_test_cases,
    deduplicate_test_cases,
    normalize_json_structure,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import prepare_append_existing_cases


class LegacyGenerationStreamPrepareMixin:

    def _stream_prepare_phase(
        self,
        *,
        client,
        request_id: str,
        requirement: str,
        project_id: int,
        db: Session | None = None,
        doc_type: str = "requirement",
        compress: bool = False,
        expected_count: int = 20,
        batch_size: int = 10,
        overwrite: bool = False,
        append: bool = False,
        user_id: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        # Get client for user
        client = get_client_for_user(user_id, db)
        request_id = uuid.uuid4().hex

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        
        # Determine start_id if appending
        start_id = 1
        existing_cases = []
        existing_entry = None
        
        if db and append:
             from sqlalchemy import desc
             query = db.query(TestGeneration).filter(
                 TestGeneration.project_id == project_id,
                 TestGeneration.requirement_text == original_requirement
             )
             if user_id:
                 query = query.filter(TestGeneration.user_id == user_id)
             existing_entry = query.order_by(desc(TestGeneration.created_at)).first()
             
             if existing_entry and existing_entry.generated_result:
                 existing_cases, existing_unique_count, start_id = prepare_append_existing_cases(
                     existing_entry.generated_result,
                     normalize_json_structure_fn=normalize_json_structure,
                     deduplicate_test_cases_fn=deduplicate_test_cases,
                     count_unique_test_cases_fn=count_unique_test_cases,
                 )

        if db:
            status_messages: list[str] = []
            # 中文注释：流式链路同样走 snapshot readiness gate，保证两条入口行为一致。
            gate_result = {
                "proceed": True,
                "gate_debug": {
                    "snapshot_gate_enabled": False,
                    "snapshot_wait_result": "skipped_non_session_db",
                },
            }
            if self._is_active_db_session(db):
                gate_result = self._run_snapshot_readiness_gate(
                project_id=project_id,
                user_id=user_id,
                status_messages=status_messages,
            )
            gate_debug = gate_result.get("gate_debug") or {}
            if not gate_result.get("proceed"):
                gate_reason_chain = build_gate_reason_chain(gate_debug)
                wait_result = str(gate_debug.get("snapshot_wait_result") or "").strip().lower()
                if "timeout" in wait_result:
                    gate_reason_chain.append("snapshot_gate_timeout")
                gate_reason_chain.append("hybrid_context_not_built")
                gate_reason_chain.append("generation_aborted_before_model_call")
                for status_message in status_messages:
                    yield f"@@STATUS@@:{status_message}\n"
                yield (
                    "@@STATUS@@:snapshot readiness gate 未通过，终止本次生成。"
                    f"(result={gate_debug.get('snapshot_wait_result')})\n"
                )
                gate_error_code = gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT"
                gate_error_message = gate_result.get("error_message") or "snapshot 未就绪，终止生成。"
                yield f"Error: {gate_error_code}: {gate_error_message}\n"
                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result={
                        "context_source": "none",
                        "fusion_debug": {
                            **gate_debug,
                            "final_decision": "timeout_fail_fast",
                            "reason_chain": gate_reason_chain,
                        },
                        "snapshot_result": {},
                        "rag_result": {},
                    },
                    gate_debug=gate_debug,
                    doc_type=doc_type,
                    compress=compress,
                    requirement_length=len(requirement or ""),
                )
                self._emit_final_context_trace(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    request_id=request_id,
                    context_result={
                        "context_source": "none",
                        "fusion_debug": {
                            **gate_debug,
                            "reason_chain": gate_reason_chain,
                            "final_decision": "timeout_fail_fast",
                        },
                        "snapshot_result": {},
                        "rag_result": {},
                    },
                    gate_debug=gate_debug,
                    fallback_reason="snapshot_wait_gate_abort",
                    abort_code=gate_error_code,
                    compressed_chars=0,
                )
                return {"abort": True}

            context_result = self._resolve_kb_context_with_hybrid(
                requirement=requirement,
                project_id=project_id,
                db=db,
                user_id=user_id,
                compress=compress,
                status_messages=status_messages,
                precision_mode=True,
            )
            fusion_debug = context_result.get("fusion_debug") or {}
            if gate_debug:
                fusion_debug.update(gate_debug)
                reason_chain = list(fusion_debug.get("reason_chain") or [])
                for reason in build_gate_reason_chain(gate_debug):
                    if reason and (not reason_chain or reason_chain[-1] != reason):
                        reason_chain.append(reason)
                fusion_debug["reason_chain"] = reason_chain
                if (
                    gate_debug.get("snapshot_wait_result") == "timeout_fallback_rag"
                    and fusion_debug.get("final_decision") == "proceed_with_generation"
                ):
                    fusion_debug["final_decision"] = "timeout_fallback_rag_then_proceed"
            fusion_debug["final_generation_context_mode"] = context_result.get("context_source") or "empty"
            context_result["fusion_debug"] = fusion_debug
            kb_context = context_result.get("kb_context") or ""

            for status_message in status_messages:
                yield f"@@STATUS@@:{status_message}\n"

            # 中文注释：流式链路同样在“上下文全空”时快速失败，避免继续裸跑生成。
            if context_result.get("abort_generation"):
                fusion_debug = context_result.get("fusion_debug") or {}
                abort_error = context_result.get("abort_error") or "上下文为空，已按兜底策略终止生成。"
                reason_chain = list(fusion_debug.get("reason_chain") or [])
                if not reason_chain or reason_chain[-1] != "hybrid_context_not_built":
                    reason_chain.append("hybrid_context_not_built")
                if reason_chain[-1] != "generation_aborted_before_model_call":
                    reason_chain.append("generation_aborted_before_model_call")
                fusion_debug["reason_chain"] = reason_chain
                final_decision = fusion_debug.get("final_decision") or "fail_fast"
                print(
                    "Hybrid guard abort(stream): "
                    f"snapshot_status={fusion_debug.get('snapshot_status')}, "
                    f"snapshot_queue_status={fusion_debug.get('snapshot_queue_status')}, "
                    f"snapshot_queue_reason={fusion_debug.get('snapshot_queue_reason')}, "
                    f"lane_counts={fusion_debug.get('lane_counts')}, "
                    f"lane_reasons={fusion_debug.get('lane_reasons')}, "
                    f"final_empty_reason={fusion_debug.get('hybrid_empty_reason')}"
                )
                yield (
                    "@@STATUS@@:上下文兜底触发，终止本次生成。"
                    f"(decision={final_decision},queue={fusion_debug.get('snapshot_queue_status')}/{fusion_debug.get('snapshot_queue_reason')})\n"
                )
                yield f"Error: {abort_error}\n"
                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result=context_result,
                    gate_debug=gate_debug,
                    doc_type=doc_type,
                    compress=compress,
                    requirement_length=len(requirement or ""),
                )
                self._emit_final_context_trace(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    request_id=request_id,
                    context_result=context_result,
                    gate_debug=gate_debug,
                    fallback_reason=context_result.get("fallback_reason") or "",
                    abort_code="HYBRID_EMPTY_CONTEXT_ABORT",
                    compressed_chars=len(kb_context or ""),
                )
                return {"abort": True}

            if compress:
                try:
                    req_len_before = len(requirement)
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db,
                    )
                    if (
                        compressed_req
                        and not compressed_req.startswith("Error")
                        and not compressed_req.startswith("Exception")
                    ):
                        requirement = compressed_req
                        yield f"@@STATUS@@:需求压缩完成 ({req_len_before} -> {len(requirement)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:需求压缩返回异常，使用原始文本: {(compressed_req or '')[:50]}...\n"
                except Exception as e:
                    yield f"@@STATUS@@:需求压缩失败 ({str(e)})，将使用原始文本...\n"

        if db and not compress:
            if requirement and len(requirement) > 120000:
                yield "@@STATUS@@:输入内容较长，正在进行智能压缩以适配模型上下文...\n"
                try:
                    req_len_before = len(requirement)
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db
                    )
                    if compressed_req and not compressed_req.startswith("Error"):
                        requirement = compressed_req
                        yield f"@@STATUS@@:长文本压缩完成 ({req_len_before} -> {len(requirement)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:长文本压缩异常，使用原始文本...\n"
                except Exception:
                    pass
            if kb_context and len(kb_context) > 120000:
                yield "@@STATUS@@:知识库上下文较长，正在进行智能压缩以适配模型上下文...\n"
                try:
                    kb_len_before = len(kb_context)
                    compressed_kb = client.compress_context(
                        kb_context,
                        prompt="请将以下检索到的知识库上下文压缩为适合测试用例生成的精炼摘要，保留关键实体、流程、约束、字段、边界与异常规则。输出纯文本。",
                        db=db
                    )
                    if compressed_kb and not compressed_kb.startswith("Error"):
                        kb_context = compressed_kb
                        yield f"@@STATUS@@:知识库压缩完成 ({kb_len_before} -> {len(kb_context)} 字符)...\n"
                    else:
                        yield f"@@STATUS@@:知识库压缩异常，使用原始文本...\n"
                except Exception:
                    pass


        return {
            "abort": False,
            "client": client,
            "request_id": request_id,
            "requirement": requirement,
            "project_id": project_id,
            "db": db,
            "doc_type": doc_type,
            "compress": compress,
            "expected_count": expected_count,
            "batch_size": batch_size,
            "overwrite": overwrite,
            "append": append,
            "user_id": user_id,
            "original_requirement": original_requirement,
            "kb_context": kb_context,
            "start_id": start_id,
            "existing_cases": existing_cases,
            "existing_entry": existing_entry,
            "context_result": context_result if isinstance(context_result, dict) else {},
            "gate_debug": gate_debug if isinstance(gate_debug, dict) else {},
        }

