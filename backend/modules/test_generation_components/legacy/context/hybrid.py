from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge_base import knowledge_base
from modules.stage25_switches import STAGE25_SWITCHES
from modules.test_generation_components.hybrid_context_builder import (
    HYBRID_CONFIG,
    build_hybrid_context,
    should_use_rag,
)
from modules.test_generation_components.hybrid_guard import (
    HYBRID_EMPTY_GUARD_CONFIG,
    detect_hybrid_empty_context,
    parse_snapshot_queue_info,
)


class LegacyGenerationContextHybridMixin:

    def _resolve_kb_context_with_hybrid(
        self,
        requirement: str,
        project_id: int,
        db: Session,
        user_id: int = None,
        compress: bool = False,
        status_messages: list[str] | None = None,
        precision_mode: bool = False,
    ) -> dict:
        """统一编排 snapshot + RAG 融合上下文，并处理空上下文兜底。"""
        query_text = requirement[:1000] if requirement else ""
        reason_chain: list[str] = []
        self._append_reason_chain(reason_chain, "entry:resolve_hybrid_context")
        if not db:
            self._append_reason_chain(reason_chain, "guard:db_unavailable")
            return {
                "kb_context": "",
                "context_source": "none",
                "fallback_reason": "db_unavailable",
                "abort_generation": False,
                "abort_error": "",
                "fusion_debug": {
                    "snapshot_used": False,
                    "rag_used": False,
                    "rag_chunk_count": 0,
                    "rag_top_scores": [],
                    "fusion_mode": "empty",
                    "final_context_tokens": 0,
                    "hybrid_empty_context": True,
                    "hybrid_empty_reason": "db_unavailable",
                    "snapshot_ready": False,
                    "snapshot_usable_for_generation": False,
                    "snapshot_readiness_reason": "db_unavailable",
                    "needs_rebuild": True,
                    "snapshot_queue_status": "none",
                    "snapshot_queue_reason": "none",
                    "snapshot_queue_error": "",
                    "sync_snapshot_retry_attempted": False,
                    "sync_snapshot_retry_success": False,
                    "sync_snapshot_retry_error": "",
                    "final_decision": "proceed_with_generation",
                    "reason_chain": reason_chain,
                },
            }

        if status_messages is not None:
            status_messages.append("正在准备项目级上下文快照，这可能需要几秒钟...")

        snapshot_text = ""
        try:
            snapshot_result = knowledge_base.get_or_build_context_snapshot(
                project_id=project_id,
                db=db,
                user_id=user_id,
                force_rebuild=False,
                prefer_async_rebuild=True,
            )
        except Exception as e:
            snapshot_result = {"success": False, "fallback_reason": f"snapshot_exception:{e}"}

        queue_status, queue_reason, queue_error = parse_snapshot_queue_info(snapshot_result)
        snapshot_status = str(snapshot_result.get("snapshot_status") or "unknown")
        snapshot_ready = bool(snapshot_result.get("is_ready", False))
        snapshot_usable = bool(snapshot_result.get("usable_for_generation", False))
        snapshot_needs_rebuild = bool(snapshot_result.get("needs_rebuild", False))
        snapshot_readiness_reason = str(snapshot_result.get("readiness_reason") or "")
        self._append_reason_chain(reason_chain, f"snapshot_status:{snapshot_status}")
        if snapshot_readiness_reason:
            self._append_reason_chain(reason_chain, f"snapshot_readiness_reason:{snapshot_readiness_reason}")
        if queue_reason:
            self._append_reason_chain(reason_chain, f"snapshot_queue_reason:{queue_reason}")

        if snapshot_result.get("success") and (snapshot_result.get("snapshot_text") or "").strip():
            snapshot_text = snapshot_result.get("snapshot_text") or ""
            self._append_reason_chain(reason_chain, "snapshot:reuse_or_rebuild_success")
            if status_messages is not None:
                if snapshot_result.get("cache_hit") and snapshot_usable:
                    status_messages.append(
                        f"snapshot ready and reusable -> using snapshot（reason={snapshot_readiness_reason or 'snapshot_success_and_hash_matched'}）。"
                    )
                else:
                    status_messages.append(
                        f"项目级上下文快照已更新（{snapshot_result.get('rebuild_reason', 'unknown')}），本次复用新快照。"
                    )

        fallback_reason = ""
        if not snapshot_text:
            fallback_reason = snapshot_result.get("fallback_reason") or "snapshot_unavailable"
            self._append_reason_chain(reason_chain, f"snapshot_fallback:{fallback_reason}")
            queue_result = snapshot_result.get("queue_result") or {}
            if status_messages is not None:
                if queue_result.get("queued"):
                    status_messages.append(
                        f"snapshot stale -> queued rebuild and using rag_only（task_id={queue_result.get('task_id')}，reason={snapshot_readiness_reason or fallback_reason}）。"
                    )
                elif queue_reason == "already_pending":
                    status_messages.append(
                        "snapshot pending -> waiting skipped, using rag_only（reason=already_pending）。"
                    )
                elif queue_reason == "enqueue_failed":
                    status_messages.append(
                        f"snapshot not ready -> using rag_only（enqueue_failed, err={queue_error or 'unknown'}）。"
                    )
                else:
                    if snapshot_status == "failed":
                        status_messages.append(
                            f"snapshot failed -> fallback to rag（reason={snapshot_readiness_reason or fallback_reason}）。"
                        )
                    elif snapshot_status in {"pending", "building"}:
                        status_messages.append(
                            f"snapshot not ready -> using rag_only（status={snapshot_status}, reason={snapshot_readiness_reason or fallback_reason}）。"
                        )
                    elif snapshot_status == "stale":
                        status_messages.append(
                            f"snapshot stale -> queued rebuild and using rag_only（reason={snapshot_readiness_reason or fallback_reason}）。"
                        )
                    else:
                        status_messages.append(
                            f"snapshot not ready -> using rag_only（reason={snapshot_readiness_reason or fallback_reason}）。"
                        )
                    status_messages.append(
                        f"snapshot_queue_status={queue_status}, snapshot_queue_reason={queue_reason}"
                    )
        snapshot_tokens = max(0, len(snapshot_text) // 4)
        use_rag, precision_reasons = should_use_rag(
            question=query_text,
            mode="test_case_generation",
            snapshot_length=snapshot_tokens,
            precision_mode=bool(precision_mode),
        )
        if use_rag:
            self._append_reason_chain(reason_chain, f"rag_enabled:{','.join(precision_reasons)}")
        else:
            self._append_reason_chain(reason_chain, "rag_disabled:no_precision_trigger")
        if status_messages is not None:
            if use_rag:
                status_messages.append(f"已启用精度增强检索（原因：{','.join(precision_reasons)}）。")
            else:
                status_messages.append("当前问题未触发精度增强检索，仅使用 snapshot 背景。")

        rag_result = None
        rag_error = ""
        if use_rag:
            try:
                rag_result = knowledge_base.get_relevant_context(
                    query=query_text,
                    project_id=project_id,
                    limit=HYBRID_CONFIG.rag_top_k,
                    db=db,
                    user_id=user_id,
                    debug=True,
                    max_tokens=HYBRID_CONFIG.rag_max_tokens,
                )
                self._append_reason_chain(reason_chain, "rag_retrieval:completed")
            except Exception as e:
                rag_error = f"rag_exception:{e}"
                self._append_reason_chain(reason_chain, rag_error)
                if status_messages is not None:
                    status_messages.append(f"RAG 精度检索失败（{e}），将仅使用 snapshot。")

        fusion_result = build_hybrid_context(
            question=query_text,
            snapshot_text=snapshot_text,
            rag_payload=rag_result if isinstance(rag_result, dict) else None,
            mode="test_case_generation",
            precision_mode=bool(precision_mode),
        )
        fusion_debug = fusion_result.get("debug") or {}
        kb_context = fusion_result.get("context") or ""

        empty_info = detect_hybrid_empty_context(
            snapshot_text=snapshot_text,
            kb_context=kb_context,
            fusion_debug=fusion_debug,
            rag_payload=rag_result if isinstance(rag_result, dict) else None,
        )
        fusion_debug.update(
            {
                "snapshot_used": bool(fusion_debug.get("snapshot_used")),
                "rag_used": bool(fusion_debug.get("rag_used")),
                "hybrid_empty_context": bool(empty_info.get("hybrid_empty_context")),
                "hybrid_empty_reason": str(empty_info.get("final_empty_reason") or ""),
                "lane_counts": empty_info.get("lane_counts") or {},
                "lane_reasons": empty_info.get("lane_reasons") or {},
                "snapshot_status": snapshot_status,
                "snapshot_ready": snapshot_ready,
                "snapshot_usable_for_generation": snapshot_usable,
                "snapshot_readiness_reason": snapshot_readiness_reason,
                "snapshot_version": int(snapshot_result.get("snapshot_version") or 0),
                "snapshot_fingerprint": str(snapshot_result.get("snapshot_fingerprint") or ""),
                "snapshot_build_reason": snapshot_result.get("rebuild_reason"),
                "snapshot_build_latency_ms": float(snapshot_result.get("build_latency_ms") or 0.0),
                "needs_rebuild": snapshot_needs_rebuild,
                "snapshot_queue_status": queue_status,
                "snapshot_queue_reason": queue_reason,
                "snapshot_queue_error": queue_error,
                "sync_snapshot_retry_attempted": False,
                "sync_snapshot_retry_success": False,
                "sync_snapshot_retry_error": "",
                "final_decision": "proceed_with_generation",
                "retrieval_profile": ((rag_result or {}).get("debug", {}) or {}).get("retrieval_profile", {})
                if isinstance(rag_result, dict)
                else {},
                "reason_chain": reason_chain,
                "stage25_switches": STAGE25_SWITCHES.to_dict()
                if STAGE25_SWITCHES.include_switches_in_debug
                else {},
            }
        )

        abort_generation = False
        abort_error = ""
        strategy = HYBRID_EMPTY_GUARD_CONFIG.normalized_strategy()
        if bool(empty_info.get("hybrid_empty_context")):
            if strategy == "sync_snapshot_retry_then_fail" and HYBRID_EMPTY_GUARD_CONFIG.sync_snapshot_retry_enabled:
                fusion_debug["sync_snapshot_retry_attempted"] = True
                retry_result = self._try_sync_snapshot_retry_once(
                    project_id=project_id,
                    user_id=user_id,
                    timeout_sec=HYBRID_EMPTY_GUARD_CONFIG.sync_snapshot_retry_timeout_sec,
                )
                if retry_result.get("success"):
                    fusion_debug["sync_snapshot_retry_success"] = True
                    retry_payload = retry_result.get("result") or {}
                    retry_snapshot_text = str(retry_payload.get("snapshot_text") or "").strip()
                    retry_fusion = build_hybrid_context(
                        question=query_text,
                        snapshot_text=retry_snapshot_text,
                        rag_payload=rag_result if isinstance(rag_result, dict) else None,
                        mode="test_case_generation",
                        precision_mode=bool(precision_mode),
                    )
                    retry_debug = retry_fusion.get("debug") or {}
                    retry_context = retry_fusion.get("context") or ""
                    retry_empty = detect_hybrid_empty_context(
                        snapshot_text=retry_snapshot_text,
                        kb_context=retry_context,
                        fusion_debug=retry_debug,
                        rag_payload=rag_result if isinstance(rag_result, dict) else None,
                    )
                    if not retry_empty.get("hybrid_empty_context"):
                        kb_context = retry_context
                        fusion_debug.update(retry_debug)
                        fusion_debug["hybrid_empty_context"] = False
                        fusion_debug["hybrid_empty_reason"] = ""
                        fusion_debug["final_decision"] = "retry_snapshot_then_proceed"
                        self._append_reason_chain(reason_chain, "guard_sync_retry:success")
                        fallback_reason = ""
                    else:
                        abort_generation = True
                        abort_error = "上下文为空：同步补救后仍未获取到可用 snapshot/RAG 结果。"
                        fallback_reason = "hybrid_empty_context_after_sync_retry"
                        fusion_debug["hybrid_empty_context"] = True
                        fusion_debug["hybrid_empty_reason"] = str(
                            retry_empty.get("final_empty_reason") or "hybrid_empty_context_after_sync_retry"
                        )
                        self._append_reason_chain(
                            reason_chain,
                            f"guard_sync_retry:failed_after_retry:{fusion_debug.get('hybrid_empty_reason')}",
                        )
                        # 中文注释：补救已尝试但仍失败，最终降级为可解释错误返回。
                        fusion_debug["final_decision"] = "degraded_to_error"
                else:
                    abort_generation = True
                    sync_err = str(retry_result.get("error") or "sync_snapshot_retry_failed")
                    abort_error = f"上下文为空：同步 snapshot 补救失败（{sync_err}）。"
                    fallback_reason = sync_err
                    fusion_debug["sync_snapshot_retry_error"] = sync_err
                    self._append_reason_chain(reason_chain, f"guard_sync_retry:exception:{sync_err}")
                    # 中文注释：补救过程失败，最终降级为可解释错误返回。
                    fusion_debug["final_decision"] = "degraded_to_error"
            else:
                abort_generation = True
                abort_error = "上下文为空：snapshot 与 RAG 均不可用，已按 fail-fast 策略终止本次生成。"
                fallback_reason = str(empty_info.get("final_empty_reason") or "hybrid_empty_context")
                self._append_reason_chain(reason_chain, f"guard_fail_fast:{fallback_reason}")
                fusion_debug["final_decision"] = "fail_fast"
        else:
            fusion_debug["final_decision"] = "proceed_with_generation"

        if status_messages is not None:
            status_messages.append(
                "融合完成：mode="
                f"{fusion_debug.get('fusion_mode')}，rag_chunks={fusion_debug.get('rag_chunk_count', 0)}，"
                f"tokens≈{fusion_debug.get('final_context_tokens', 0)}"
            )
            if abort_generation:
                status_messages.append(
                    "上下文兜底触发："
                    f"final_decision={fusion_debug.get('final_decision')}，reason={fusion_debug.get('hybrid_empty_reason') or fallback_reason}"
                )

        if not kb_context:
            if rag_error:
                fallback_reason = rag_error
            elif fallback_reason:
                pass
            elif use_rag and (fusion_debug.get("rag_error") or ""):
                fallback_reason = str(fusion_debug.get("rag_error"))
            else:
                fallback_reason = "hybrid_empty_context"
        if fallback_reason:
            self._append_reason_chain(reason_chain, f"fallback:{fallback_reason}")
        self._append_reason_chain(reason_chain, f"final_decision:{fusion_debug.get('final_decision')}")

        return {
            "kb_context": kb_context,
            "context_source": fusion_debug.get("fusion_mode") or "empty",
            "snapshot_result": snapshot_result,
            "rag_result": rag_result if isinstance(rag_result, dict) else None,
            "fusion_debug": fusion_debug,
            "fallback_reason": fallback_reason,
            "abort_generation": abort_generation,
            "abort_error": abort_error,
            "reason_chain": reason_chain,
        }

    def _resolve_kb_context_with_snapshot(
        self,
        requirement: str,
        project_id: int,
        db: Session,
        user_id: int = None,
        compress: bool = False,
        status_messages: list[str] | None = None,
    ) -> dict:
        """兼容旧调用名，内部转调到融合实现。"""
        return self._resolve_kb_context_with_hybrid(
            requirement=requirement,
            project_id=project_id,
            db=db,
            user_id=user_id,
            compress=compress,
            status_messages=status_messages,
            precision_mode=False,
        )
