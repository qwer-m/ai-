"""
智能测试生成引擎 (Intelligent Test Generation Engine)

此模块负责将用户需求（文本、图片OCR结果等）转化为结构化的测试用例。
核心功能：
1. 上下文压缩：针对超长需求文档或知识库，利用 LLM 进行智能摘要。
2. 批量生成：支持按批次生成大量用例，自动管理 ID 序列。
3. 流式生成：支持 Server-Sent Events (SSE) 风格的流式输出，提供实时进度反馈。
4. 格式清洗：强大的 JSON 修复能力，处理 LLM 返回的不规范 JSON。
5. 自动去重：在追加模式下，通过历史记录防止用例重复。

依赖：
- core.ai_client: 模型调用。
- modules.knowledge_base: RAG 检索支持。
"""

from typing import Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import uuid

from core.ai_client import get_client_for_user
from core.database import SessionLocal
from sqlalchemy.orm import Session
from core.models import TestGeneration, LogEntry
from modules.knowledge_base import knowledge_base
import json
import re
from modules.test_generation_components.excel_export import (
    convert_json_to_excel as _convert_json_to_excel_impl,
)
from modules.test_generation_components.json_processing import (
    clean_and_parse_json as _clean_and_parse_json_impl,
)
from modules.test_generation_components.json_processing import (
    normalize_json_structure as _normalize_json_structure_impl,
)
from modules.test_generation_components.json_processing import (
    deduplicate_test_cases as _deduplicate_test_cases_impl,
)
from modules.test_generation_components.json_processing import (
    count_unique_test_cases as _count_unique_test_cases_impl,
)
from modules.test_generation_components.json_processing import (
    infer_case_kind as _infer_case_kind_impl,
)
from modules.test_generation_components.json_processing import (
    reorder_cases_by_closed_loop as _reorder_cases_by_closed_loop_impl,
)
from modules.test_generation_components.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
)
from modules.test_generation_components.prompt_orchestration import (
    build_closed_loop_base_prompt,
)
from modules.test_generation_components.prompt_orchestration import (
    build_supplement_closed_loop_instruction,
)
from modules.test_generation_components.result_postprocess import (
    finalize_generated_cases,
    merge_cases_for_append,
    prepare_append_existing_cases,
    stream_postprocess_cases,
)
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
from modules.test_generation_components.snapshot_wait_gate import wait_snapshot_ready_gate
from modules.stage25_switches import STAGE25_SWITCHES
from modules.test_generation_components.generation_diagnostics import (
    build_context_source_log,
    build_coverage_diagnostics,
    build_final_context_trace,
    build_gate_reason_chain,
)


def clean_and_parse_json(response_text: str) -> Any:
    """
    兼容旧调用入口：对外函数名保持不变，内部转调组件实现。

    这样做可以在不改变外部引用路径的前提下，将解析逻辑从主流程文件中解耦。
    """
    return _clean_and_parse_json_impl(response_text)


def normalize_json_structure(data: Any) -> Any:
    """
    兼容旧调用入口：对外函数名保持不变，内部转调组件实现。
    """
    return _normalize_json_structure_impl(data)


def deduplicate_test_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    兼容旧调用入口：统一复用组件层的去重实现。
    """
    return _deduplicate_test_cases_impl(cases)


def count_unique_test_cases(cases: list[dict[str, Any]]) -> int:
    """
    兼容旧调用入口：统一复用组件层的唯一计数实现。
    """
    return _count_unique_test_cases_impl(cases)


def infer_case_kind(case: dict[str, Any]) -> str:
    """
    Backward-compatible wrapper for heuristic case categorization.
    """
    return _infer_case_kind_impl(case)


def reorder_cases_by_closed_loop(
    cases: list[dict[str, Any]],
    *,
    start_id: int = 1,
    renumber_ids: bool = True,
    module_order_hint: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Backward-compatible wrapper for workflow-first closed-loop reordering.
    """
    return _reorder_cases_by_closed_loop_impl(
        cases,
        start_id=start_id,
        renumber_ids=renumber_ids,
        module_order_hint=module_order_hint,
    )

class TestGenerationModule:
    """
    测试生成模块核心类 (Test Generation Logic)
    封装了用例数量估算、JSON 生成、流式生成等核心业务逻辑。

    === AGENT PERSONA: QA ARCHITECT & DECOUPLING SPECIALIST ===
    This module acts as an autonomous QA Agent enforced by the "5 Pillars" protocol:
    1. Comprehensive Coverage: P0/P1/P2 + Non-Functional.
    2. Clear Purpose: One goal per test case.
    3. Minimal Workload (MECE): No redundancy.
    4. Clear Classification: Accurate module/priority tagging.
    5. Independence (Zero Coupling): ATOMIC test cases only.
    
    The Agent performs "Mental Sandbox Simulation" (Decoupling Check) before generation.
    """
    def __init__(self):
        pass

    def _is_active_db_session(self, db: Session | None) -> bool:
        """仅对真实 SQLAlchemy Session 执行 DB 读写或门禁逻辑。"""
        return isinstance(db, Session)

    def estimate_test_count(self, requirement: str, project_id: int, db: Session, user_id: int = None) -> int:
        """
        估算测试用例数量 (Estimate Test Case Count)
        利用 LLM 根据需求长度和复杂度，快速估算合理的用例数量，用于前端进度条或默认值设置。
        """
        try:
            client = get_client_for_user(user_id, db)
            
            # Simple RAG retrieval for context
            query_text = requirement[:500] if requirement else ""
            kb_context = ""
            try:
                kb_context = knowledge_base.get_relevant_context(query=query_text, project_id=project_id, limit=2, db=db, user_id=user_id)
            except Exception:
                pass
            
            doc_len = len(requirement) if requirement else 0
            
            system_prompt = f"""
            You are an expert QA lead.
            Based on the requirement scale and project context provided by the user, ESTIMATE the reasonable number of test cases needed to cover the ESSENTIAL functionality.
            
            Project Context (Reference):
            {kb_context}
            
            Document Statistics:
            - Total Length: {doc_len} characters
            
            Rules:
            1. Return ONLY a single integer number (e.g. 15).
            2. Do not return a range (e.g. 10-20).
            3. Do not return any text explanation.
            4. Be EFFICIENT but COMPREHENSIVE. 
               - Cover Critical and Major paths thoroughly.
               - Include necessary edge cases and negative tests.
               - Avoid redundant permutations, but ensure full logic coverage.
            5. Scaling Guide:
               - Simple Login/Reset Password: 5-8 cases.
               - CRUD Management Page: 10-15 cases.
               - Complex Form/Process: 20-30 cases.
            6. The goal is a Standard Regression Suite.
            7. **Assume Atomic Test Cases (The 5 Pillars)**: 
               - Each case covers exactly ONE checkable point (Zero Coupling).
               - Do not count "End-to-End" flows as single cases if they cover multiple distinct features.
               - Do not count redundant "Water Injection" cases (e.g. 10 valid inputs for same field).
               - Enforce MECE (Mutually Exclusive, Collectively Exhaustive).
            """
            
            user_msg = f"Requirement Content (first 2000 chars):\n{requirement[:2000]}"
            
            response = client.generate_response(user_msg, system_prompt, db=db)
            
            # Parse integer
            text_resp = str(response).strip()
            match = re.search(r'\d+', text_resp)
            if match:
                val = int(match.group(0))
                # Apply a mild damping factor (approx -10%) to prevent slight inflation
                val = int(val * 0.9)
                # Safety bounds - moderate max cap
                return max(5, min(val, 100))
            return 20
        except Exception as e:
            print(f"Estimation failed ({type(e).__name__}): {e}")
            raise e  # Propagate error to let frontend handle it, no fallback guessing

    def analyze_requirement_context(self, requirement: str, kb_context: str, client, db: Session) -> dict:
        """
        Meta-Analysis Agent: Analyzes the requirement and knowledge base to determine test strategy.
        Returns a dictionary with system_type, impact_scope, test_ratios, and focus_areas.
        """
        try:
            analysis_prompt = f"""
            You are a QA Architect. Analyze the following Requirement and Reference Context.
            Determine the System Type, Impact Scope, and optimal Testing Strategy.
            
            Requirement Preview: {requirement[:1000]}...
            Reference Context Preview: {kb_context[:1000]}...
            
            ANALYSIS GUIDELINES (System Type Detection):
            - **Web**: Keywords like "Browser", "URL", "Page", "H5", "网页", "后台".
            - **Mobile App**: Keywords like "iOS", "Android", "APK", "Touch", "Swipe", "手机", "APP".
            - **Tablet/Pad**: Keywords like "iPad", "Tablet", "Landscape", "Split Screen", "平板", "HD".
            - **Desktop App**: Keywords like "Windows", "Mac", "Client", "Exe", "Install", "PC客户端", "电脑版".
            - **Combination**: If multiple platforms are detected, combine them (e.g., "Mobile + Web", "Tablet + Desktop + Web").

            TEST CASE DESIGN PRINCIPLES (Strategy Level):
            1. **Comprehensive Coverage**: Ensure all functional and non-functional aspects are covered.
            2. **Clear Purpose**: Each test area must have a specific, identifiable goal.
            3. **Minimal Workload (MECE)**: Avoid redundancy. Strategy must be efficient.
            4. **Clear Classification**: Organize by modules logically.
            5. **Independence (Zero Coupling)**: Plan for atomic test points. Avoid overlapping scope between areas.

            Output STRICT JSON:
            {{
                "system_type": "String describing the system type (e.g., 'Web', 'Mobile App', 'Tablet + Web', 'Mobile + Web + Desktop')",
                "impact_scope": "New Feature" | "Regression" | "Hotfix" | "Refactor",
                "complexity": "High" | "Medium" | "Low",
                "suggested_ratios": {{
                    "functional": 0.6,
                    "regression": 0.2,
                    "non_functional": 0.2
                }},
                "focus_areas": ["Login", "Payment", "API", "UI", "Security", "Performance", "Responsiveness", "Cross-Platform"],
                "device_scenarios": ["Weak Network", "Landscape", "Battery Drain", "Browser Compatibility", "Mouse/Keyboard", "Touch"]
            }}
            """
            response = client.generate_response(requirement[:2000], analysis_prompt, db=db) # Use limited req for speed
            plan = clean_and_parse_json(response)
            if isinstance(plan, dict):
                return plan
        except Exception as e:
            print(f"Meta-analysis failed: {e}")
            # Re-raise the exception to let the user know something went wrong
            # instead of silently downgrading to a default plan.
            raise e

    def _try_sync_snapshot_retry_once(
        self,
        project_id: int,
        user_id: int | None,
        timeout_sec: int,
    ) -> dict:
        """
        触发一次“限时同步 snapshot 重建”。

        说明：
        1. 只尝试一次，避免无限重试。
        2. 使用独立数据库会话，避免跨线程复用 Session。
        3. 用 timeout 控制等待时长，超时后立即返回失败。
        """

        def _worker() -> dict:
            retry_db = SessionLocal()
            try:
                return knowledge_base.get_or_build_context_snapshot(
                    project_id=project_id,
                    db=retry_db,
                    user_id=user_id,
                    force_rebuild=True,
                    prefer_async_rebuild=False,
                )
            finally:
                retry_db.close()

        safe_timeout = max(2, int(timeout_sec or 0))
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-sync-retry")
        future = executor.submit(_worker)
        try:
            result = future.result(timeout=safe_timeout)
            ok = bool(result.get("success") and (result.get("snapshot_text") or "").strip())
            return {
                "success": ok,
                "result": result,
                "error": "" if ok else str(result.get("fallback_reason") or "sync_retry_empty_snapshot"),
            }
        except FutureTimeoutError:
            future.cancel()
            return {
                "success": False,
                "result": None,
                "error": f"sync_snapshot_retry_timeout:{safe_timeout}s",
            }
        except Exception as e:
            return {"success": False, "result": None, "error": f"sync_snapshot_retry_exception:{e}"}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _run_snapshot_readiness_gate(
        self,
        project_id: int,
        user_id: int | None,
        status_messages: list[str] | None = None,
    ) -> dict:
        """
        生成前 snapshot 门禁：
        - 仅做“是否可开始生成”的判定与等待，不改动现有 snapshot 构建主逻辑；
        - 通过短轮询等待 snapshot ready，避免“本次请求吃不到刚入队快照”。
        """

        def _get_status() -> dict:
            # 中文注释：每次轮询使用新会话，避免长事务下读取不到最新状态。
            gate_db = SessionLocal()
            try:
                return knowledge_base.get_context_snapshot_status(project_id=project_id, db=gate_db) or {}
            finally:
                gate_db.close()

        def _enqueue() -> dict:
            gate_db = SessionLocal()
            try:
                return knowledge_base.enqueue_context_snapshot_rebuild(
                    project_id=project_id,
                    db=gate_db,
                    user_id=user_id,
                    force_rebuild=False,
                ) or {}
            finally:
                gate_db.close()

        gate_result = wait_snapshot_ready_gate(
            get_status_fn=_get_status,
            enqueue_rebuild_fn=_enqueue,
            status_messages=status_messages,
        )
        gate_debug = gate_result.get("gate_debug") or {}
        print(
            "snapshot readiness gate: "
            f"enabled={gate_debug.get('snapshot_gate_enabled')} "
            f"before={gate_debug.get('snapshot_status_before_generation')} "
            f"after={gate_debug.get('snapshot_status_after_wait')} "
            f"poll={gate_debug.get('snapshot_wait_poll_count')} "
            f"elapsed_ms={gate_debug.get('snapshot_wait_elapsed_ms')} "
            f"result={gate_debug.get('snapshot_wait_result')} "
            f"queue={gate_debug.get('snapshot_wait_queue_status')}/{gate_debug.get('snapshot_wait_queue_reason')}"
        )
        if status_messages is not None:
            status_messages.append(
                "snapshot gate result: "
                f"{gate_debug.get('snapshot_wait_result')} "
                f"(poll={gate_debug.get('snapshot_wait_poll_count')}, "
                f"elapsed_ms={gate_debug.get('snapshot_wait_elapsed_ms')})"
            )
        return gate_result

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

    def generate_test_cases_json(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_size: int = 20, batch_index: int = 0, user_id: int = None) -> dict:
        """
        生成测试用例 - JSON 格式 (Generate Test Cases JSON)
        
        Args:
            requirement: 需求文本。
            project_id: 项目ID，用于 RAG 检索上下文。
            db: 数据库会话。
            doc_type: 文档类型 (requirement, prototype, incomplete)。不同的类型会触发不同的 Prompt 策略。
            compress: 是否启用上下文压缩（针对超长文本）。
            expected_count: 预期总数量。
            batch_size: 当前批次大小。
            batch_index: 当前批次索引 (用于计算 ID 起始值)。
            user_id: 用户ID，用于获取特定的 AI 模型配置。
            
        Returns:
            dict: 包含生成的用例列表，或者错误信息。
        """
        # Get client for user
        client = get_client_for_user(user_id, db)
        request_id = uuid.uuid4().hex

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        final_trace_emitted = False
        gate_debug: dict[str, Any] = {}
        context_result: dict[str, Any] | None = None
        gate_debug: dict[str, Any] = {}
        context_result: dict[str, Any] | None = None
        gate_result: dict[str, Any] | None = None
        gate_debug: dict[str, Any] = {}
        context_result: dict[str, Any] | None = None
        if db:
            # 中文注释：生成前先执行 snapshot readiness gate，避免 stale 场景直接 rag_only。
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
                status_messages=None,
            )
            gate_debug = gate_result.get("gate_debug") or {}
            if not gate_result.get("proceed"):
                gate_reason_chain = build_gate_reason_chain(gate_debug)
                wait_result = str(gate_debug.get("snapshot_wait_result") or "").strip().lower()
                if "timeout" in wait_result:
                    gate_reason_chain.append("snapshot_gate_timeout")
                gate_reason_chain.append("hybrid_context_not_built")
                gate_reason_chain.append("generation_aborted_before_model_call")
                print(
                    "snapshot gate abort(json): "
                    f"status_before={gate_debug.get('snapshot_status_before_generation')}, "
                    f"status_after={gate_debug.get('snapshot_status_after_wait')}, "
                    f"result={gate_debug.get('snapshot_wait_result')}"
                )
                error_payload = {
                    "error": gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT",
                    "abort_code": gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT",
                    "message": gate_result.get("error_message")
                    or "snapshot 未就绪，已按 fail-fast 终止本次生成。",
                    "fallback_reason": "snapshot_wait_gate_abort",
                    "context_source": "none",
                    "reason_chain": gate_reason_chain,
                    "fusion_debug": {
                        **gate_debug,
                        "final_decision": "timeout_fail_fast",
                        "final_generation_context_mode": "none",
                        "reason_chain": gate_reason_chain,
                    },
                }
                self._emit_context_source_log(
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    context_result={
                        "context_source": "none",
                        "fusion_debug": error_payload.get("fusion_debug") or {},
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
                        "fusion_debug": error_payload.get("fusion_debug") or {},
                        "snapshot_result": {},
                        "rag_result": {},
                    },
                    gate_debug=gate_debug,
                    fallback_reason="snapshot_wait_gate_abort",
                    abort_code=error_payload.get("abort_code") or "",
                    compressed_chars=0,
                )
                return error_payload

            context_result = self._resolve_kb_context_with_hybrid(
                requirement=requirement,
                project_id=project_id,
                db=db,
                user_id=user_id,
                compress=compress,
                precision_mode=True,
            )
            fusion_debug = context_result.get("fusion_debug") or {}
            if gate_debug:
                # 中文注释：把 gate 调试字段合并进融合调试，方便统一观察。
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
            # 中文注释：命中“上下文全空兜底”时，直接终止，避免模型在无知识上下文下裸跑。
            if context_result.get("abort_generation"):
                fusion_debug = context_result.get("fusion_debug") or {}
                abort_error = context_result.get("abort_error") or "上下文为空，已按兜底策略终止生成。"
                reason_chain = list(fusion_debug.get("reason_chain") or [])
                if not reason_chain or reason_chain[-1] != "hybrid_context_not_built":
                    reason_chain.append("hybrid_context_not_built")
                if reason_chain[-1] != "generation_aborted_before_model_call":
                    reason_chain.append("generation_aborted_before_model_call")
                fusion_debug["reason_chain"] = reason_chain
                print(
                    "Hybrid guard abort(json): "
                    f"snapshot_status={fusion_debug.get('snapshot_status')}, "
                    f"snapshot_queue_status={fusion_debug.get('snapshot_queue_status')}, "
                    f"snapshot_queue_reason={fusion_debug.get('snapshot_queue_reason')}, "
                    f"lane_counts={fusion_debug.get('lane_counts')}, "
                    f"lane_reasons={fusion_debug.get('lane_reasons')}, "
                    f"final_empty_reason={fusion_debug.get('hybrid_empty_reason')}"
                )
                error_payload = {
                    "error": "HYBRID_EMPTY_CONTEXT_ABORT",
                    "abort_code": "HYBRID_EMPTY_CONTEXT_ABORT",
                    "message": abort_error,
                    "fallback_reason": context_result.get("fallback_reason") or "",
                    "context_source": context_result.get("context_source") or "empty",
                    "reason_chain": reason_chain,
                    "fusion_debug": fusion_debug,
                }
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
                return error_payload

            if compress:
                # 需求文本压缩与知识快照是两条并行优化链路，任何一条失败都不阻断生成。
                try:
                    compressed_req = client.compress_context(
                        requirement,
                        prompt="请将以下需求压缩为适合测试用例生成的精炼版本，保留技术细节、字段/ID、约束、边界与异常规则，去除冗余。输出纯文本。",
                        db=db,
                    )
                    if compressed_req and not compressed_req.startswith("Error") and not compressed_req.startswith("Exception"):
                        requirement = compressed_req
                except Exception:
                    pass

        # Meta-Analysis Step (Dynamic Strategy Planning)
        analysis_result = self.analyze_requirement_context(requirement, kb_context, client, db)
        strategy_plan = analysis_result or {}
        system_type = analysis_result.get("system_type", "Unknown")
        ratios = analysis_result.get("suggested_ratios", {})
        focus_areas = analysis_result.get("focus_areas", [])
        
        # Helper to safely format percentage
        def safe_pct(val):
            try:
                return f"{float(val):.0%}"
            except:
                return "0%"

        strategy_instruction = f"""
        STRATEGIC PLANNING (Meta-Analysis Result):
        - Detected System Type: {system_type}
        - Focus Areas: {', '.join(focus_areas)}
        - Target Ratios: Functional {safe_pct(ratios.get('functional', 0.6))}, Regression {safe_pct(ratios.get('regression', 0.2))}, Non-Functional {safe_pct(ratios.get('non_functional', 0.2))}.
        
        INSTRUCTION:
        Adjust your test case generation to align with these ratios and focus areas.
        """
        base_prompt = build_closed_loop_base_prompt(
            strategy_plan,
            doc_type=doc_type,
            pretty_json=False,
        )

        # Calculate start number for IDs based on batch index
        start_id = batch_index * batch_size + 1
        
        system_prompt = f"""
        {base_prompt}
        
        Reference Knowledge (Use this style/info if relevant):
        {kb_context}
        
        The JSON should be a list of objects with keys: id, description, test_module, preconditions, steps, test_input, expected_result, priority.
        - test_module: Explain which area/module this test case belongs to (e.g., Login, User Management, Payment).
        - test_input: Describe the input actions or data changes in the steps. Explicitly mention if a value is a Boundary Value or Invalid Equivalence Class.
        - description: Include the specific scenario being tested (e.g., "Verify login with empty password" or "Verify age input at boundary 18").
        
        BATCH GENERATION INSTRUCTION (workflow-first):
        This is batch #{batch_index + 1}.
        Start the Test Case IDs from {start_id} (e.g., TC-{start_id:03d}).
        Target this batch size: about {batch_size} cases.
        Prioritize completing the current module closed-loop first; do not cross-module jump for count balancing.
        Return ONLY the JSON array.
        """
        self._emit_final_context_trace(
            db=db,
            project_id=project_id,
            user_id=user_id,
            request_id=request_id,
            context_result=context_result,
            gate_debug=gate_debug,
            fallback_reason=(context_result or {}).get("fallback_reason") if isinstance(context_result, dict) else "",
            abort_code="",
            compressed_chars=len(kb_context or ""),
        )
        response = client.generate_response(requirement, system_prompt, db=db)
        
        # ... rest of function using response ...
        result = finalize_generated_cases(
            response,
            start_id=start_id,
            clean_and_parse_json_fn=clean_and_parse_json,
            normalize_json_structure_fn=normalize_json_structure,
            deduplicate_test_cases_fn=deduplicate_test_cases,
            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
        )
            
        # Save to DB if session provided
        if db:
            try:
                db_entry = TestGeneration(
                    requirement_text=original_requirement,
                    generated_result=json.dumps(result, ensure_ascii=False) if not "error" in result else json.dumps({"error": result, "raw": response}, ensure_ascii=False),
                    project_id=project_id,
                    user_id=user_id
                )
                db.add(db_entry)
                db.commit()
                db.refresh(db_entry)
                # Add db id to result for reference
                if isinstance(result, list):
                     pass # Can't add to list easily, maybe wrap? keeping as is.
                elif isinstance(result, dict):
                    result['db_id'] = db_entry.id

                if isinstance(result, list) and STAGE25_SWITCHES.coverage_diagnostics_enabled:
                    coverage_diag = build_coverage_diagnostics(
                        requirement=requirement,
                        generated_cases=[x for x in result if isinstance(x, dict)],
                        kb_context=kb_context,
                        fusion_debug=(context_result or {}).get("fusion_debug") or {},
                        expected_count=int(expected_count or 0),
                    )
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}",
                        )
                    )
                    db.commit()
            except Exception as e:
                print(f"Failed to save to DB: {e}")

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
                 
        return result
    
    def generate_test_cases_stream(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_size: int = 10, overwrite: bool = False, append: bool = False, user_id: int = None):
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
                return

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
                return

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

        # --- STEP 1: META-ANALYSIS (Dynamic Strategy Planning) ---
        yield "@@STATUS@@:正在进行需求元分析 (Meta-Analysis)，识别系统类型与测试策略...\n"
        strategy_plan = self.analyze_requirement_context(requirement, kb_context, client, db)
        yield f"@@STATUS@@:分析完成 - 系统类型: {strategy_plan.get('system_type')}, 复杂度: {strategy_plan.get('complexity')}, 策略: {json.dumps(strategy_plan.get('suggested_ratios'))}...\n"
        base_prompt = build_closed_loop_base_prompt(
            strategy_plan,
            doc_type=doc_type,
            pretty_json=True,
        )

        full_content = ""
        
        # Calculate batches
        import math

        # Dynamic Batch Size Adjustment based on User Request
        existing_unique_count = (
            count_unique_test_cases(existing_cases)
            if isinstance(existing_cases, list)
            else 0
        )
        current_existing_count = existing_unique_count
        
        if append:
            needed_to_append = expected_count - current_existing_count
            if needed_to_append > 25:
                batch_size = 25
            else:
                # If needed is small (e.g. 5), we generate all in one batch
                batch_size = max(1, needed_to_append)
        else:
            # For fresh generation, user requested 25 per batch
            batch_size = 25

        # Ensure batch_size is at least 1 to avoid infinite loop
        batch_size = max(1, batch_size)
        
        # Handle Append Mode: If expected_count is met, auto-increment
        current_count = existing_unique_count
        if append and expected_count <= current_count:
            yield f"@@STATUS@@:当前用例数({current_count})已达预期({expected_count})，自动增加 {batch_size} 条用例...\n"
            expected_count = current_count + batch_size

        total_batches = math.ceil((expected_count - (start_id - 1)) / batch_size)
        # Ensure at least 1 batch if needed
        if total_batches < 1 and expected_count > (start_id - 1):
            total_batches = 1
        
        current_id = start_id
        
        # History tracking for de-duplication
        history_summaries = []
        if append and isinstance(existing_cases, list):
            for c in existing_cases:
                if isinstance(c, dict):
                    history_summaries.append(f"{c.get('id', '')}: {c.get('description', '')}")

        def _merged_unique_total(new_cases: Any) -> int:
            """中文注释：统一计算“历史+新增”的唯一总数，避免补齐逻辑口径不一致。"""
            merged: list[dict[str, Any]] = []
            if append and isinstance(existing_cases, list):
                merged.extend(existing_cases)
            if isinstance(new_cases, list):
                merged.extend(new_cases)
            return count_unique_test_cases(merged)

        for i in range(total_batches):
            remaining = expected_count - (current_id - start_id)
            current_batch_count = min(batch_size, remaining)
            
            if current_batch_count <= 0:
                break

            generated_in_batch = 0
            attempt = 0
            batch_content = ""

            while generated_in_batch < current_batch_count and attempt < 3:
                need = current_batch_count - generated_in_batch
                attempt += 1
                yield f"@@STATUS@@:正在生成第 {i+1}/{total_batches} 批次 ({current_batch_count} 条) - 第 {attempt} 次尝试...\n"

                # Build history context (last 50 items to save tokens)
                history_context_str = ""
                if history_summaries:
                    recent_history = history_summaries[-50:]
                    history_list_str = "\n".join([f"- {h}" for h in recent_history])
                    history_context_str = f"""
                    IMPORTANT - DE-DUPLICATION INSTRUCTION:
                    The following test scenarios have ALREADY been generated. 
                    DO NOT generate duplicates or very similar cases to these:
                    {history_list_str}
                    
                    Focus on NEW scenarios in the current module closed loop first.
                    """
                # --- COVERAGE & GAP ANALYSIS (CRITICAL) ---
                coverage_instruction = ""
                if append and existing_cases:
                    coverage_instruction = build_append_closed_loop_coverage_instruction(
                        existing_cases=[c for c in existing_cases if isinstance(c, dict)],
                        requirement=requirement,
                        expected_count=expected_count,
                        infer_case_kind_fn=infer_case_kind,
                    )

                system_prompt = f"""
                {base_prompt}
                
                {coverage_instruction}
                
                # --- REFERENCE KNOWLEDGE (RAG) ---
                The following content is retrieved from the knowledge base (Historical Test Cases / Docs).
                USAGE RULES:
                1. Use this ONLY for understanding the project's terminology, style, and format.
                2. DO NOT copy these test cases unless they are strictly relevant to the current requirement.
                3. If the Reference Knowledge conflicts with the current Requirement, FOLLOW THE CURRENT REQUIREMENT.
                4. IGNORE the order of test cases in the Reference Knowledge. You MUST follow the order of the *Current Requirement*.
                
                [START REFERENCE]
                {kb_context}
                [END REFERENCE]
                
                {history_context_str}
                
                # --- GENERATION STRATEGY ---
                1. ANALYZE the User's Requirement (provided in the next message) step-by-step.
                2. IDENTIFY the specific functionality, logic, and constraints in the User's Requirement.
                3. APPLY Testing Techniques:
                   - Equivalence Partitioning: Identify valid/invalid inputs.
                   - Boundary Value Analysis: Test edges (min, max, null, overflow).
                   - Scenario Testing: Cover happy paths and error paths.
                4. GENERATE new test cases that target the User's Requirement. 
                   - Do NOT generate generic cases unrelated to the specific logic.
                   - Do NOT repeat test cases found in Reference Knowledge unless necessary.
                5. FINAL CHECK: Ensure the first test case corresponds to the *first step* of the User's Requirement (e.g., Entry Point).
                
                # --- VISUAL/LAYOUT TESTING RULE ---
                If the Requirement mentions UI layout, styles, or specific visual elements (e.g., "入口是什么样式", "图2"):
                - You MUST generate a "UI Verification" test case as the VERY FIRST case for that module.
                - Verify the visual appearance matches the description/image.
                - Do NOT skip visual details just because they are not "functional actions".
                
                BATCH GENERATION INSTRUCTION (workflow-first):
                This is batch {i+1} of {total_batches}.
                Start the Test Case IDs from {int(current_id) + int(generated_in_batch)} (e.g., TC-{(int(current_id) + int(generated_in_batch)):03d}).
                Target this batch size: about {need} cases.
                Keep closed-loop continuity in current module first; do not jump modules just to match count.
                
                Return ONLY the JSON array.
                """

                if not final_trace_emitted:
                    self._emit_final_context_trace(
                        db=db,
                        project_id=project_id,
                        user_id=user_id,
                        request_id=request_id,
                        context_result=context_result,
                        gate_debug=gate_debug,
                        fallback_reason=(context_result or {}).get("fallback_reason") if isinstance(context_result, dict) else "",
                        abort_code="",
                        compressed_chars=len(kb_context or ""),
                    )
                    final_trace_emitted = True
                stream = client.generate_response_stream(requirement, system_prompt)
                chunk_acc = ""
                provider_error = None
                for chunk in stream:
                    chunk_acc += chunk
                    full_content += chunk
                    batch_content += chunk
                    yield chunk # Stream chunk directly for better performance
                    if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                        provider_error = chunk
                        break

                if not provider_error and not chunk_acc.strip():
                    if attempt < 3:
                        yield "\n@@STATUS@@:模型未返回内容，正在重试...\n"
                        continue
                    yield "\n@@STATUS@@:生成失败\n"
                    yield "Error: 模型未返回内容（可能是模型配置/额度/网络/内容安全导致），请检查后重试\n"
                    attempt = 3
                    break

                if provider_error:
                    yield "\n@@STATUS@@:生成失败\n"
                    yield f"{provider_error}\n"
                    attempt = 3
                    break

                full_content += "\n"
                batch_content += "\n"
                yield "\n"

                try:
                    parsed_batch = clean_and_parse_json(batch_content)
                    parsed_batch = normalize_json_structure(parsed_batch)
                    if isinstance(parsed_batch, list):
                        generated_in_batch = len(parsed_batch)
                        # Update history for next batch/retry
                        for case in parsed_batch:
                            if isinstance(case, dict):
                                history_summaries.append(f"{case.get('id', '')}: {case.get('description', '')}")
                except Exception:
                    pass

            current_id += current_batch_count

        # Post-processing and saving to DB after stream finishes
        try:
            # Try to clean and parse the full content to ensure it's valid JSON before saving
            parsed_result = yield from stream_postprocess_cases(
                client=client,
                requirement=requirement,
                base_prompt=base_prompt,
                kb_context=kb_context,
                full_content=full_content,
                expected_count=expected_count,
                append=append,
                existing_cases=existing_cases,
                existing_unique_count=existing_unique_count,
                start_id=start_id,
                db=db,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                count_unique_test_cases_fn=count_unique_test_cases,
                infer_case_kind_fn=infer_case_kind,
                build_supplement_closed_loop_instruction_fn=build_supplement_closed_loop_instruction,
            )
            if isinstance(parsed_result, dict) and parsed_result.get("error"):
                yield "\n@@STATUS@@:生成失败\n"
                yield f"Error: {parsed_result.get('error')}\n"
            elif isinstance(parsed_result, list) and len(parsed_result) == 0:
                yield "\n@@STATUS@@:生成失败\n"
                yield "Error: 模型返回空数组或解析不到有效用例，请检查模型配置/提示词/网络后重试\n"

            cleaned_response = json.dumps(parsed_result, ensure_ascii=False)
            
            if db:
                if overwrite:
                    from sqlalchemy import desc
                    query = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == original_requirement
                    )
                    if user_id:
                        query = query.filter(TestGeneration.user_id == user_id)
                    existing_entry_overwrite = query.order_by(desc(TestGeneration.created_at)).first()
                    
                    if existing_entry_overwrite:
                        existing_entry_overwrite.generated_result = cleaned_response
                        db.commit()
                        db.refresh(existing_entry_overwrite)
                    else:
                         db_entry = TestGeneration(
                            requirement_text=original_requirement,
                            generated_result=cleaned_response, # Save the cleaned text which should be JSON
                            project_id=project_id,
                            user_id=user_id
                        )
                         db.add(db_entry)
                         db.commit()
                elif append and existing_entry:
                    # Merge with existing cases
                    if isinstance(parsed_result, list):
                        merged_result = merge_cases_for_append(
                            existing_cases,
                            parsed_result,
                            deduplicate_test_cases_fn=deduplicate_test_cases,
                            reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                        )
                        existing_entry.generated_result = json.dumps(merged_result, ensure_ascii=False)
                        db.commit()
                        db.refresh(existing_entry)
                else:
                    db_entry = TestGeneration(
                        requirement_text=original_requirement,
                        generated_result=cleaned_response, # Save the cleaned text which should be JSON
                        project_id=project_id,
                        user_id=user_id
                    )
                    db.add(db_entry)
                    db.commit()

                # --- Log GEN_DIAG and GEN_QM ---
                try:
                    # 中文注释：诊断日志也采用唯一用例计数，避免与界面显示口径不一致。
                    count = count_unique_test_cases(parsed_result) if isinstance(parsed_result, list) else 0
                    
                    # Calculate actual model for accurate logging
                    # system_prompt is defined above in this function
                    full_input = (system_prompt or "") + requirement
                    actual_model = client.select_model(full_input)
                    
                    # GEN_DIAG
                    diag = {
                        "kind": "gen_diag",
                        "mode": "stream",
                        "doc_type": doc_type,
                        "compress": compress,
                        "expected_count": expected_count,
                        "generated_count": count,
                        "content_length": len(requirement),
                        "kb_length": len(kb_context or ""),
                        "prototype_included": "[Prototype Analysis]" in requirement,
                        "model": actual_model,  # Use actual selected model
                        "max_tokens": client.max_tokens
                    }
                    
                    db.add(LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}",
                        user_id=user_id
                    ))
                    
                    # GEN_QM
                    positive = 0
                    negative = 0
                    edge = 0
                    functional_count = 0
                    non_functional_count = 0
                    avg_steps = 0.0
                    pending = 0
                    steps_count = 0
                    steps_items = 0
                    kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时", "Invalid", "Fail", "Error", "Exception", "Timeout", "Deny"]
                    kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符", "溢出", "Boundary", "Edge", "Max", "Min", "Limit", "Critical", "Null", "Empty", "Overflow"]
                    # 中文注释：非功能关键词用于补充统计“非功能测试用例条数”。
                    kw_non_functional = [
                        "性能", "perf", "performance", "并发", "concurrent", "throughput",
                        "延迟", "latency", "响应时间", "timeout", "压测", "stress", "load",
                        "安全", "security", "鉴权", "auth", "权限", "permission",
                        "xss", "sql注入", "sql injection", "csrf",
                        "可用性", "usability", "易用性", "可访问性", "accessibility",
                        "稳定性", "reliability", "容错", "fault tolerance",
                        "兼容", "compatibility", "browser", "跨端", "资源占用", "memory", "cpu"
                    ]
                    
                    if isinstance(parsed_result, list):
                        for item in parsed_result:
                            # Combine fields for keyword search
                            desc = (item.get("description") or "") + " " + \
                                   (item.get("expected_result") or "") + " " + \
                                   (item.get("test_input") or "")
                            
                            # Add steps to search text
                            steps_text = ""
                            steps = item.get("steps")
                            if isinstance(steps, list):
                                steps_text = " ".join(str(s) for s in steps)
                            elif isinstance(steps, str):
                                steps_text = steps
                            
                            search_text = (desc + " " + steps_text).lower() # Use lowercase for case-insensitive search
                            
                            # Check keywords (case-insensitive)
                            is_neg = any(k.lower() in search_text for k in kw_neg)
                            is_edge = any(k.lower() in search_text for k in kw_edge)
                            
                            # Priority: Edge > Negative > Positive
                            # (Or as per user request to "re-plan", we ensure mutually exclusive or correct classification)
                            # Current Logic:
                            # If Edge keywords found -> Edge
                            # Else if Negative keywords found -> Negative
                            # Else -> Positive
                            
                            if is_edge:
                                edge += 1
                            elif is_neg:
                                negative += 1
                            else:
                                positive += 1

                            # 中文注释：功能/非功能统计与正负边界分类解耦，仅用于质量看板展示。
                            is_non_functional = any(k.lower() in search_text for k in kw_non_functional)
                            if is_non_functional:
                                non_functional_count += 1
                            else:
                                functional_count += 1
                                
                            if isinstance(steps, list):
                                steps_count += len(steps)
                                steps_items += 1
                            elif isinstance(steps, str):
                                lines = [s for s in steps.splitlines() if s.strip()]
                                steps_count += len(lines)
                                steps_items += 1
                                
                            if isinstance(item.get("description"), str) and "[Pending Confirmation]" in item.get("description"):
                                pending += 1
                                
                    avg_steps = steps_count / steps_items if steps_items else 0.0
                    qm = {
                        "positive": positive,
                        "negative": negative,
                        "edge": edge,
                        "functional_count": functional_count,
                        "non_functional_count": non_functional_count,
                        "avg_steps": avg_steps,
                        "pending": pending,
                        # 中文注释：generated_count 改为唯一计数，和补齐/前端显示保持一致口径。
                        "generated_count": count_unique_test_cases(parsed_result) if isinstance(parsed_result, list) else 0
                    }
                    
                    db.add(LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}",
                        user_id=user_id
                    ))
                    # Also yield to stream for real-time frontend update
                    yield f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}\n"

                    if (
                        STAGE25_SWITCHES.coverage_diagnostics_enabled
                        and isinstance(parsed_result, list)
                    ):
                        coverage_diag = build_coverage_diagnostics(
                            requirement=requirement,
                            generated_cases=[x for x in parsed_result if isinstance(x, dict)],
                            kb_context=kb_context,
                            fusion_debug=(context_result or {}).get("fusion_debug") or {},
                            expected_count=int(expected_count or 0),
                        )
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                log_type="system",
                                message=f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}",
                                user_id=user_id,
                            )
                        )
                        yield f"GEN_COVERAGE_DIAG:{json.dumps(coverage_diag, ensure_ascii=False)}\n"
                    
                    db.commit()
                except Exception as log_e:
                    print(f"Failed to log metrics: {log_e}")

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

        except Exception as e:
            print(f"Failed to save streamed result to DB: {e}")

    def generate_test_cases_excel(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, user_id: int = None) -> bytes:
        # Generate test cases in JSON format
        json_result = self.generate_test_cases_json(requirement, project_id, db, doc_type, compress, user_id=user_id)
        
        return self.convert_json_to_excel(json_result)

    def convert_json_to_excel(self, json_data: list | dict) -> bytes:
        """
        导出入口保持原方法签名，内部委托给组件实现。

        这样可避免路由层调用路径变化，同时让导出逻辑与生成编排逻辑解耦。
        """
        return _convert_json_to_excel_impl(json_data)

test_generator = TestGenerationModule()
