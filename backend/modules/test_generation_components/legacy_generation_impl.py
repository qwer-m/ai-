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
        if not db:
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

        if snapshot_result.get("success") and (snapshot_result.get("snapshot_text") or "").strip():
            snapshot_text = snapshot_result.get("snapshot_text") or ""
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
            except Exception as e:
                rag_error = f"rag_exception:{e}"
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
                "needs_rebuild": snapshot_needs_rebuild,
                "snapshot_queue_status": queue_status,
                "snapshot_queue_reason": queue_reason,
                "snapshot_queue_error": queue_error,
                "sync_snapshot_retry_attempted": False,
                "sync_snapshot_retry_success": False,
                "sync_snapshot_retry_error": "",
                "final_decision": "proceed_with_generation",
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
                        fallback_reason = ""
                    else:
                        abort_generation = True
                        abort_error = "上下文为空：同步补救后仍未获取到可用 snapshot/RAG 结果。"
                        fallback_reason = "hybrid_empty_context_after_sync_retry"
                        fusion_debug["hybrid_empty_context"] = True
                        fusion_debug["hybrid_empty_reason"] = str(
                            retry_empty.get("final_empty_reason") or "hybrid_empty_context_after_sync_retry"
                        )
                        # 中文注释：补救已尝试但仍失败，最终降级为可解释错误返回。
                        fusion_debug["final_decision"] = "degraded_to_error"
                else:
                    abort_generation = True
                    sync_err = str(retry_result.get("error") or "sync_snapshot_retry_failed")
                    abort_error = f"上下文为空：同步 snapshot 补救失败（{sync_err}）。"
                    fallback_reason = sync_err
                    fusion_debug["sync_snapshot_retry_error"] = sync_err
                    # 中文注释：补救过程失败，最终降级为可解释错误返回。
                    fusion_debug["final_decision"] = "degraded_to_error"
            else:
                abort_generation = True
                abort_error = "上下文为空：snapshot 与 RAG 均不可用，已按 fail-fast 策略终止本次生成。"
                fallback_reason = str(empty_info.get("final_empty_reason") or "hybrid_empty_context")
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

        return {
            "kb_context": kb_context,
            "context_source": fusion_debug.get("fusion_mode") or "empty",
            "snapshot_result": snapshot_result,
            "rag_result": rag_result if isinstance(rag_result, dict) else None,
            "fusion_debug": fusion_debug,
            "fallback_reason": fallback_reason,
            "abort_generation": abort_generation,
            "abort_error": abort_error,
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

        # Retrieve context from Knowledge Base if DB is available
        original_requirement = requirement
        kb_context = ""
        gate_result: dict[str, Any] | None = None
        gate_debug: dict[str, Any] = {}
        if db:
            # 中文注释：生成前先执行 snapshot readiness gate，避免 stale 场景直接 rag_only。
            gate_result = self._run_snapshot_readiness_gate(
                project_id=project_id,
                user_id=user_id,
                status_messages=None,
            )
            gate_debug = gate_result.get("gate_debug") or {}
            if not gate_result.get("proceed"):
                print(
                    "snapshot gate abort(json): "
                    f"status_before={gate_debug.get('snapshot_status_before_generation')}, "
                    f"status_after={gate_debug.get('snapshot_status_after_wait')}, "
                    f"result={gate_debug.get('snapshot_wait_result')}"
                )
                return {
                    "error": gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT",
                    "message": gate_result.get("error_message")
                    or "snapshot 未就绪，已按 fail-fast 终止本次生成。",
                    "fallback_reason": "snapshot_wait_gate_abort",
                    "context_source": "none",
                    "fusion_debug": {
                        **gate_debug,
                        "final_decision": "timeout_fail_fast",
                        "final_generation_context_mode": "none",
                    },
                }

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
                print(
                    "Hybrid guard abort(json): "
                    f"snapshot_status={fusion_debug.get('snapshot_status')}, "
                    f"snapshot_queue_status={fusion_debug.get('snapshot_queue_status')}, "
                    f"snapshot_queue_reason={fusion_debug.get('snapshot_queue_reason')}, "
                    f"lane_counts={fusion_debug.get('lane_counts')}, "
                    f"lane_reasons={fusion_debug.get('lane_reasons')}, "
                    f"final_empty_reason={fusion_debug.get('hybrid_empty_reason')}"
                )
                return {
                    "error": "HYBRID_EMPTY_CONTEXT_ABORT",
                    "message": abort_error,
                    "fallback_reason": context_result.get("fallback_reason") or "",
                    "context_source": context_result.get("context_source") or "empty",
                    "fusion_debug": fusion_debug,
                }

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

        base_prompt = f"""You are the QA Architect Agent.
        You must strictly follow the "5 Pillars" protocol to generate test cases.
        Generate test cases in STRICT JSON format.

        MANDATORY TEST CASE DESIGN PRINCIPLES (The 5 Pillars):
        1. **Comprehensive Coverage (覆盖全面)**: Cover Critical (P0), Major (P1), and Edge/Non-Functional (P2) scenarios.
        2. **Clear Purpose (目的明确)**: Each test case must have ONE clear, specific goal. The Description must reflect this goal.
        3. **Minimal Workload (工作量最小化 - MECE)**: Eliminate redundancy. If Case A covers logic X, Case B must NOT cover logic X again.
        4. **Clear Classification (分类清晰)**: Correctly assign `test_module` and `priority`.
        5. **Independence (相互独立 - Zero Coupling)**:
           - **CRITICAL**: Each test case must be ATOMIC.
           - **FORBIDDEN**: Case A covers (Feature X + Feature Y), Case B covers (Feature X). This is Coupling.
           - **REQUIRED**: Case A covers (Feature X), Case B covers (Feature Y). This is Independence.
           - Do not chain dependent tests (e.g., "Step 1: Run Case A"). Each case must be self-contained with preconditions.

        You MUST apply the following testing techniques:
        1. Equivalence Partitioning (等价类划分): Cover both valid and invalid equivalence classes.
        2. Boundary Value Analysis (边界值分析): Test boundaries (min, max, just below min, just above max) for numeric or range-based inputs.

        STRATEGIC THINKING (Context Awareness):
        - Focus entirely on the specific logic described in the Requirement.
        - Do not hallucinate features not mentioned (unless standard for the domain, e.g., Login needs Password).
        - Avoid "Lazy Copying": Do not just copy old test cases. Adapt them or create new ones.
        - SYSTEM INTERACTION: If "Reference Knowledge" is provided, analyze how the new requirement impacts existing modules.
        
        # --- DYNAMIC STRATEGY PLAN (EXECUTED BY META-AGENT) ---
        The Meta-Analysis Agent has determined the following strategy. YOU MUST FOLLOW IT:
        1. SYSTEM TYPE: {strategy_plan.get('system_type')}
           - Focus Scenarios: {', '.join(strategy_plan.get('device_scenarios', []))}
        2. IMPACT SCOPE: {strategy_plan.get('impact_scope')}
        3. TEST RATIOS (Target Composition): 
           - Functional: {int(float(strategy_plan.get('suggested_ratios', {}).get('functional', 0.6)) * 100)}%
           - Regression/Integration: {int(float(strategy_plan.get('suggested_ratios', {}).get('regression', 0.2)) * 100)}%
           - Non-Functional (Security/Perf): {int(float(strategy_plan.get('suggested_ratios', {}).get('non_functional', 0.2)) * 100)}%
        4. PRIORITY FOCUS AREAS: {', '.join(strategy_plan.get('focus_areas', []))} (NOTE: Cover these thoroughly, but reach them via the Natural Flow. Do not jump to them immediately.)

        LOGICAL ORDER INSTRUCTION (CRITICAL):
        - **Module-Based Organization (Vertical Slicing)**: Organize test cases by Functional Module/Page.
        - **Structure within Module**: For EACH module, follow this order: Happy Path -> Edge Cases/Validation -> Non-Functional (Security/Perf).
        - **Flow**: Iterate through modules in the natural user journey order (e.g., Login -> Home -> Details), but finish ALL aspects of one module before moving to the next.
        - Do NOT jump to complex logic (like "Location Config") before covering basic entry points (like "Home Page Entry").

        MENTAL CHAIN-OF-THOUGHT (CoT) INSTRUCTION:
        Before generating the JSON, perform the following "Mental Sandbox Simulation":
        1. **Walkthrough**: Mentally trace the user's journey through the requirement from start to finish.
        2. **Attack Surface**: As you trace each step, pause and ask "What if I do X here?".
        3. **Decoupling Check**: Ask "Does this new case overlap with any previous case?". If yes, refine it to be distinct.
        4. **Synthesis**: Combine the "Happy Path" (Flow) with these "Attacks" (Divergence) to form your test cases.
        *Do not output this mental process, but let it guide your JSON generation.*

        ADAPTIVE BUDGET & MECE STRATEGY (CRITICAL):
        You must use the MECE (Mutually Exclusive, Collectively Exhaustive) principle to manage the Test Case Count.
        1. **DECOMPOSITION**: Break the requirement into atomic "Checkable Points".
        2. **STRATEGY ROUTING**:
           - **Case A: Request < Necessary (Deficit)** -> [SURVIVAL MODE] (Prioritize P0 > P1, Drop P2).
           - **Case B: Request > Necessary (Surplus)** -> [CIRCUIT BREAKER MODE] (Cover P0-P2 -> High Value Non-Func -> STOP).
           - **DO NOT** generate "Water Injection" cases.

        ANTI-BLOAT RULE (COUPLING PREVENTION):
        - Every test case must have a distinct *Verification Point*.
        - **Rule of Thumb**: If you can merge two cases without losing coverage, they are likely coupled/redundant. Keep them separate ONLY if they test different *logic branches*.

        IMPORTANT LANGUAGE REQUIREMENT:
        All content (description, steps, test_input, expected_result, preconditions, test_module) MUST be in Chinese (Simplified).
        Do not output English unless it is a specific technical term or variable name from the requirement.

        STRICT OUTPUT REQUIREMENTS (MANDATORY):
        - Output MUST be a single valid JSON array (no extra text before/after).
        - Do NOT output Markdown, code fences, explanations, or batch headers.
        - Each array item MUST be a JSON object with EXACT keys:
          id, description, test_module, preconditions, steps, test_input, expected_result, priority
        - No additional keys are allowed.
        - Types:
          - id: string like "TC-001"
          - description: string
          - test_module: string
          - preconditions: array of strings (can be empty [])
          - steps: array of strings (must be non-empty)
          - test_input: string
          - expected_result: string
          - priority: one of "P0","P1","P2"
"""
        
        if doc_type == "prototype":
            base_prompt += """
            The input provided is a description of a UI prototype (derived from an image).
            Focus on testing the UI elements, layout, user interactions, and visual states described.
            Infer expected behaviors for buttons, inputs, and navigation based on standard UI patterns.
            """
        elif doc_type == "incomplete":
            base_prompt += """
            The input provided is an incomplete requirement document.
            You should:
            1. Generate test cases for the parts that are clearly defined.
            2. For missing or ambiguous information, infer reasonable expected results based on common software standards.
            3. Add a tag "[Pending Confirmation]" to the description of test cases that rely on inferred information.
            """
        
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
        
        BATCH GENERATION INSTRUCTION:
        This is batch #{batch_index + 1}. 
        Generate exactly {batch_size} test cases.
        Start the Test Case IDs from {start_id} (e.g., TC-{start_id:03d}).
        Ensure these test cases cover different aspects or scenarios than previous batches if possible, or just proceed sequentially through the requirement logic.
        
        Return ONLY the JSON list.
        """
        response = client.generate_response(requirement, system_prompt, db=db)
        
        # ... rest of function using response ...
        if isinstance(response, (list, dict)):
            result = response
        else:
            result = clean_and_parse_json(response)
            
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
            except Exception as e:
                print(f"Failed to save to DB: {e}")
                
        return result
    
    def generate_test_cases_stream(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, expected_count: int = 20, batch_size: int = 10, overwrite: bool = False, append: bool = False, user_id: int = None):
        # Get client for user
        client = get_client_for_user(user_id, db)

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
                 try:
                     existing_cases = json.loads(existing_entry.generated_result)
                     if isinstance(existing_cases, list):
                         start_id = len(existing_cases) + 1
                 except Exception:
                     pass

        if db:
            status_messages: list[str] = []
            # 中文注释：流式链路同样走 snapshot readiness gate，保证两条入口行为一致。
            gate_result = self._run_snapshot_readiness_gate(
                project_id=project_id,
                user_id=user_id,
                status_messages=status_messages,
            )
            gate_debug = gate_result.get("gate_debug") or {}
            if not gate_result.get("proceed"):
                for status_message in status_messages:
                    yield f"@@STATUS@@:{status_message}\n"
                yield (
                    "@@STATUS@@:snapshot readiness gate 未通过，终止本次生成。"
                    f"(result={gate_debug.get('snapshot_wait_result')})\n"
                )
                gate_error_code = gate_result.get("error_code") or "SNAPSHOT_NOT_READY_TIMEOUT"
                gate_error_message = gate_result.get("error_message") or "snapshot 未就绪，终止生成。"
                yield f"Error: {gate_error_code}: {gate_error_message}\n"
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

        base_prompt = f"""You are the QA Architect Agent.
        You must strictly follow the "5 Pillars" protocol to generate test cases.
        Generate test cases in STRICT JSON format.

        MANDATORY TEST CASE DESIGN PRINCIPLES (The 5 Pillars):
        1. **Comprehensive Coverage (覆盖全面)**: Cover Critical (P0), Major (P1), and Edge/Non-Functional (P2) scenarios.
        2. **Clear Purpose (目的明确)**: Each test case must have ONE clear, specific goal. The Description must reflect this goal.
        3. **Minimal Workload (工作量最小化 - MECE)**: Eliminate redundancy. If Case A covers logic X, Case B must NOT cover logic X again.
        4. **Clear Classification (分类清晰)**: Correctly assign `test_module` and `priority`.
        5. **Independence (相互独立 - Zero Coupling)**:
           - **CRITICAL**: Each test case must be ATOMIC.
           - **FORBIDDEN**: Case A covers (Feature X + Feature Y), Case B covers (Feature X). This is Coupling.
           - **REQUIRED**: Case A covers (Feature X), Case B covers (Feature Y). This is Independence.
           - Do not chain dependent tests (e.g., "Step 1: Run Case A"). Each case must be self-contained with preconditions.

        You MUST apply the following testing techniques:
        1. Equivalence Partitioning (等价类划分): Cover both valid and invalid equivalence classes.
        2. Boundary Value Analysis (边界值分析): Test boundaries (min, max, just below min, just above max) for numeric or range-based inputs.

        STRATEGIC THINKING (Context Awareness):
- Focus entirely on the specific logic described in the Requirement.
- Do not hallucinate features not mentioned (unless standard for the domain, e.g., Login needs Password).
- Avoid "Lazy Copying": Do not just copy old test cases. Adapt them or create new ones.
- SYSTEM INTERACTION: If "Reference Knowledge" is provided, analyze how the new requirement impacts existing modules.

# --- DYNAMIC STRATEGY PLAN (EXECUTED BY META-AGENT) ---
The Meta-Analysis Agent has determined the following strategy. YOU MUST FOLLOW IT:
1. SYSTEM TYPE: {strategy_plan.get('system_type')}
   - Focus Scenarios: {', '.join(strategy_plan.get('device_scenarios', []))}
2. IMPACT SCOPE: {strategy_plan.get('impact_scope')}
3. TEST RATIOS (Target Composition): 
   - Functional: {int(float(strategy_plan.get('suggested_ratios', {}).get('functional', 0.6)) * 100)}%
   - Regression/Integration: {int(float(strategy_plan.get('suggested_ratios', {}).get('regression', 0.2)) * 100)}%
   - Non-Functional (Security/Perf): {int(float(strategy_plan.get('suggested_ratios', {}).get('non_functional', 0.2)) * 100)}%
4. PRIORITY FOCUS AREAS: {', '.join(strategy_plan.get('focus_areas', []))} (NOTE: Cover these thoroughly, but reach them via the Natural Flow. Do not jump to them immediately.)

LOGICAL ORDER INSTRUCTION (CRITICAL):
- **Module-Based Organization (Vertical Slicing)**: Organize test cases by Functional Module/Page.
- **Structure within Module**: For EACH module, follow this order: Happy Path -> Edge Cases/Validation -> Non-Functional (Security/Perf).
- **Flow**: Iterate through modules in the natural user journey order (e.g., Login -> Home -> Details), but finish ALL aspects of one module before moving to the next.
- Do NOT jump to complex logic (like "Location Config") before covering basic entry points (like "Home Page Entry").

MENTAL CHAIN-OF-THOUGHT (CoT) INSTRUCTION:
        Before generating the JSON, perform the following "Mental Sandbox Simulation":
        1. **Walkthrough**: Mentally trace the user's journey through the requirement from start to finish.
        2. **Attack Surface**: As you trace each step, pause and ask "What if I do X here?".
        3. **Decoupling Check**: Ask "Does this new case overlap with any previous case?". If yes, refine it to be distinct.
        4. **Synthesis**: Combine the "Happy Path" (Flow) with these "Attacks" (Divergence) to form your test cases.
        *Do not output this mental process, but let it guide your JSON generation.*

ADAPTIVE BUDGET & MECE STRATEGY (CRITICAL):
You must use the MECE (Mutually Exclusive, Collectively Exhaustive) principle to manage the Test Case Count.

1. **DECOMPOSITION**: Break the requirement into atomic "Checkable Points".
2. **DENSITY CALCULATION**:
   - Simple Feature (e.g., Button): ~1-2 cases.
   - Logic Flow (e.g., Form): ~3-5 cases.
   - Complex Process (e.g., Payment): ~5-10 cases.

3. **STRATEGY ROUTING**:
   - **Case A: Request < Necessary (Deficit)** -> [SURVIVAL MODE]
     - PRIORITY: P0 (Happy Path) > P1 (Critical Edge).
     - DROP: P2 (Minor UI/Text), P3 (Exploratory).
     - GOAL: Ensure basic usability first.

   - **Case B: Request > Necessary (Surplus)** -> [CIRCUIT BREAKER MODE]
     - STEP 1: Cover all P0/P1/P2 cases fully.
     - STEP 2: If quota remains, add HIGH-VALUE Non-Functional (Security/Performance) cases.
     - STEP 3: **STOP GENERATING** if you run out of distinct, valuable scenarios.
     - **DO NOT** generate "Water Injection" cases (e.g., testing same field with 10 different random strings).
     - **DO NOT** split one logical step into 5 tiny steps just to fill count.
     - It is BETTER to return fewer, high-quality cases than to hit the target with garbage.

4. **ANTI-BLOAT RULE (COUPLING PREVENTION)**:
           - Every test case must have a distinct *Verification Point*.
           - **Rule of Thumb**: If you can merge two cases without losing coverage, they are likely coupled/redundant. Keep them separate ONLY if they test different *logic branches*.

IMPORTANT LANGUAGE REQUIREMENT:
All content (description, steps, test_input, expected_result, preconditions, test_module) MUST be in Chinese (Simplified).
Do not output English unless it is a specific technical term or variable name from the requirement.

STRICT OUTPUT REQUIREMENTS (MANDATORY):
- Output MUST be a single valid JSON array (no extra text before/after).
- Format the JSON with indentation (2 spaces) and newlines for readability.
- Do NOT output Markdown, code fences, explanations, or batch headers.
- Each array item MUST be a JSON object with EXACT keys:
  id, description, test_module, preconditions, steps, test_input, expected_result, priority
- No additional keys are allowed.
- Do NOT wrap the JSON array in an object (e.g., {{"test_cases": [...]}}).
- preconditions and steps MUST be arrays of strings (not single strings).

JSON STRUCTURE EXAMPLE (Follow this structure exactly):
[
   {{ 
     "id": "TC-001",  
     "description": "验证内部试用机注册时未获取GPS经纬度，应禁止保存", 
     "test_module": "内部试用机申请", 
     "preconditions": [ 
       "系统已登录具备试用机申请权限的销售账号", 
       "设备GPS功能被禁用或模拟无定位" 
     ], 
     "steps": [ 
       "进入内部试用机申请页", 
       "不填写任何经纬度信息", 
       "点击提交按钮" 
     ], 
     "test_input": "经度为空，纬度为空", 
     "expected_result": "提示'请成功获取设备位置信息后提交'，表单无法提交", 
     "priority": "P0" 
   }}
]

Types:
  - id: string like "TC-001"
  - description: string
  - test_module: string
  - preconditions: array of strings (can be empty [])
  - steps: array of strings (must be non-empty)
  - test_input: string
  - expected_result: string
  - priority: one of "P0","P1","P2"
"""
        
        if doc_type == "prototype":
            base_prompt += """
            The input provided is a description of a UI prototype (derived from an image).
            Focus on testing the UI elements, layout, user interactions, and visual states described.
            Infer expected behaviors for buttons, inputs, and navigation based on standard UI patterns.
            """
        elif doc_type == "incomplete":
            base_prompt += """
            The input provided is an incomplete requirement document.
            You should:
            1. Generate test cases for the parts that are clearly defined.
            2. For missing or ambiguous information, infer reasonable expected results based on common software standards.
            3. Add a tag "[Pending Confirmation]" to the description of test cases that rely on inferred information.
            """
        
        full_content = ""
        
        # Calculate batches
        import math

        # Dynamic Batch Size Adjustment based on User Request
        current_existing_count = len(existing_cases) if isinstance(existing_cases, list) else 0
        
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
        current_count = len(existing_cases)
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
                    
                    Focus on NEW scenarios, different edge cases, or other modules.
                    """

                # --- COVERAGE & GAP ANALYSIS (CRITICAL) ---
                coverage_instruction = ""
                if append and existing_cases:
                     # 1. Analyze existing cases by Module
                     module_stats = {}
                     for c in existing_cases:
                         if isinstance(c, dict):
                             m = c.get("test_module", "General")
                             p = c.get("priority", "P1")
                             if m not in module_stats:
                                 module_stats[m] = {"total": 0, "P0": 0, "P1": 0, "Non-Func": 0}
                             module_stats[m]["total"] += 1
                             if p == "P0": module_stats[m]["P0"] += 1
                             if p == "P1": module_stats[m]["P1"] += 1
                             # Heuristic for Non-Func
                             desc = c.get("description", "").lower()
                             if any(x in desc for x in ["性能", "安全", "并发", "perf", "sec"]):
                                 module_stats[m]["Non-Func"] += 1
                     
                     stats_str = "\n".join([f"   - {k}: {v['total']} cases (P0:{v['P0']}, Non-Func:{v['Non-Func']})" for k, v in module_stats.items()])

                     coverage_instruction = f"""
                     # --- COVERAGE & GAP ANALYSIS (CRITICAL) ---
                     You are in 'APPEND MODE'. 
                     Current Case Count: {len(existing_cases)}
                     Target Total: {expected_count}
                     
                     EXISTING COVERAGE MATRIX (Module-Level):
                     {stats_str}
                     
                     INSTRUCTION:
                     1. **GAP ANALYSIS**: Look at the Matrix above.
                        - Which modules have high 'Total' but 0 'Non-Func'? -> TARGET: Add Security/Performance cases for them.
                        - Which modules are missing completely compared to Requirement? -> TARGET: Create 'Happy Path' for missing modules.
                        - Which modules have only 'P0'? -> TARGET: Add Edge cases (P1/P2).
                     
                     2. **SMART INSERTION & REFACTORING**:
                        - You are allowed to generate cases that "logically insert" between existing ones.
                        - If a generated case is a "better version" or "deeper variant" of an existing one, explicitly mention it in the description.
                     
                     3. **CIRCUIT BREAKER (APPEND SPECIFIC)**:
                        - If the 'Target Total' seems excessive (e.g., >100 for a simple form) and you believe the requirement is FULLY COVERED (MECE):
                        - STOP generating low-value permutations.
                        - Instead, generate complex **Integration Scenarios** (cross-module workflows) which are likely missing.
                     """

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
                
                BATCH GENERATION INSTRUCTION:
                This is batch {i+1} of {total_batches}.
                Generate exactly {need} test cases.
                Start the Test Case IDs from {int(current_id) + int(generated_in_batch)} (e.g., TC-{(int(current_id) + int(generated_in_batch)):03d}).
                Ensure the list length is exactly {need}.
                
                Return ONLY the JSON array.
                """

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
            parsed_result = clean_and_parse_json(full_content)
            # Enforce standard structure
            parsed_result = normalize_json_structure(parsed_result)

            # Calculate total count including existing cases if in append mode
            current_total = len(parsed_result) if isinstance(parsed_result, list) else 0
            if append and isinstance(existing_cases, list):
                current_total += len(existing_cases)

            if isinstance(parsed_result, list) and expected_count:
                # Truncate if we have too many (to respect "exact" count and avoid confusion)
                # But only if it's significantly more (e.g. > 5 extra) to avoid cutting off a good case slightly?
                # Actually, user wants exact control. Let's truncate to expected_count if we are sure.
                # But wait, expected_count is the target.
                
                # Logic to supplement
                current_total = len(parsed_result)
                if append and isinstance(existing_cases, list):
                    current_total += len(existing_cases)

                if current_total < expected_count:
                    supplement_history = []
                    if isinstance(parsed_result, list) and len(parsed_result) > 0:
                        for c in parsed_result[-50:]:
                            if isinstance(c, dict):
                                supplement_history.append(f"- {c.get('id', '')}: {c.get('description', '')}")
                    if append and isinstance(existing_cases, list) and len(existing_cases) > 0:
                        for c in existing_cases[-50:]:
                            if isinstance(c, dict):
                                supplement_history.append(f"- {c.get('id', '')}: {c.get('description', '')}")
                    supplement_history_str = ""
                    if supplement_history:
                        supplement_history_str = f"""
                        EXISTING CASES (Do NOT overlap or duplicate):
                        {chr(10).join(supplement_history)}
                        """
                    missing = expected_count - current_total
                    supplement_attempt = 0
                    while missing > 0 and supplement_attempt < 3:
                        supplement_attempt += 1
                        yield f"@@STATUS@@:检测到缺少 {missing} 条用例，正在补齐(第 {supplement_attempt} 次)...\n"
                        system_prompt = f"""
                        {base_prompt}

                        Reference Knowledge (Use this style/info if relevant):
                        {kb_context}

                        {supplement_history_str}

                        SUPPLEMENT INSTRUCTION:
                        Generate exactly {missing} additional test cases.
                        Start the Test Case IDs from {current_total + 1} (e.g., TC-{(current_total + 1):03d}).
                        Each new case must have a DISTINCT verification point and must NOT overlap with existing cases.
                        Do not repeat the same test_input + expected_result + test_module combination.
                        Return ONLY the JSON array.
                        """
                        extra_content = ""
                        extra_stream = client.generate_response_stream(requirement, system_prompt)
                        provider_error = None
                        for chunk in extra_stream:
                            extra_content += chunk
                            full_content += chunk
                            yield chunk
                            if chunk.startswith("Error:") or chunk.startswith("[额度耗尽]") or chunk.startswith("Exception occurred:"):
                                provider_error = chunk
                                break
                        if provider_error:
                            yield "\n@@STATUS@@:生成失败\n"
                            yield f"{provider_error}\n"
                            break
                        full_content += "\n"
                        yield "\n"
                        try:
                            extra_parsed = clean_and_parse_json(extra_content)
                            extra_parsed = normalize_json_structure(extra_parsed)
                            if isinstance(extra_parsed, list) and extra_parsed:
                                parsed_result.extend(extra_parsed)
                                parsed_result = normalize_json_structure(parsed_result)
                                # Update current total
                                current_total = len(parsed_result)
                                if append and isinstance(existing_cases, list):
                                    current_total += len(existing_cases)
                        except Exception:
                            pass
                        missing = expected_count - current_total

                # Final Truncation if exceeded (Intelligent Pruning)
                if current_total > expected_count:
                    target_new_count = expected_count
                    if append and isinstance(existing_cases, list):
                        target_new_count = expected_count - len(existing_cases)
                    
                    if target_new_count < 0: target_new_count = 0
                    
                    if len(parsed_result) > target_new_count:
                        excess = len(parsed_result) - target_new_count
                        yield f"@@STATUS@@:已生成 {len(parsed_result)} 条新用例，需保留 {target_new_count} 条。正在呼叫 QA Review Agent 进行过拟合审查...\n"
                        
                        # --- QA REVIEW AGENT (Anti-Overfitting & Pruning) ---
                        # Use LLM to intelligently select the best cases, filtering out redundancy/bloat.
                        try:
                            candidates_json = json.dumps(parsed_result, ensure_ascii=False)
                            
                            # Prepare Existing Cases Context for Append Mode
                            existing_context = ""
                            if append and isinstance(existing_cases, list) and len(existing_cases) > 0:
                                existing_summaries = []
                                # Limit to last 50 cases to avoid token overflow, assuming redundancy is mostly with recent or same-module cases
                                # Or better: filter existing cases by modules present in candidates? 
                                # For now, simple tail context.
                                for ec in existing_cases[-50:]: 
                                    mod = ec.get("test_module", "General")
                                    desc = str(ec.get("description", ec.get("test_step", "")))[:100].replace("\n", " ")
                                    existing_summaries.append(f"- [{mod}] {desc}")
                                
                                existing_context = f"""
                                EXISTING TEST CASES (Reference Only - Do NOT Duplicate):
                                {chr(10).join(existing_summaries)}
                                {len(existing_cases) > 50 and f"... (and {len(existing_cases)-50} more earlier cases)" or ""}
                                """

                            review_prompt = f"""
                            You are a Senior QA Lead performing a Test Case Review.
                            
                            CONTEXT:
                            We have generated a list of test cases, but we have exceeded the budget.
                            - Candidates Count: {len(parsed_result)}
                            - Max Allowed (Target): {target_new_count}
                            {existing_context}
                            
                            TASK:
                            Select exactly {target_new_count} BEST test cases from the Candidates list below.
                            
                            CRITERIA FOR SELECTION (Anti-Overfitting):
                            1. **Independence (Zero Coupling)**: Each case must be ATOMIC and test exactly one verification point.
                            2. **Eliminate Redundancy**: If multiple cases test the exact same logic path (just with different data), KEEP ONLY ONE.
                            3. **Check against Existing**: If a candidate duplicates logic already covered in "EXISTING TEST CASES", DISCARD IT.
                            4. **Clear Purpose**: Prefer cases with a single, specific goal over multi-goal cases.
                            5. **Remove Bloat**: Remove trivial cases (e.g., checking UI color) if core functionality is at risk.
                            6. **Prioritize Value**:
                               - MUST KEEP: P0 (Critical Path).
                               - MUST KEEP: Security / Performance / Data Integrity cases.
                               - MUST KEEP: Complex Boundary cases.
                               - DISCARD: P2/P3 cases that are low value or "water injection" (凑数).
                            
                            CANDIDATES LIST:
                            {candidates_json}
                            
                            OUTPUT:
                            Return a STRICT JSON array containing ONLY the selected {target_new_count} test case objects.
                            Do not modify the content of the test cases, just select them.
                            """
                            
                            review_response = client.generate_response(review_prompt, "You are a QA Auditor.", db=db)
                            reviewed_cases = clean_and_parse_json(review_response)
                            reviewed_cases = normalize_json_structure(reviewed_cases)
                            
                            if isinstance(reviewed_cases, list) and len(reviewed_cases) > 0:
                                # Ensure we don't exceed target even if LLM ignores instruction
                                if len(reviewed_cases) > target_new_count:
                                    reviewed_cases = reviewed_cases[:target_new_count]
                                parsed_result = reviewed_cases
                                yield f"@@STATUS@@:QA Agent 审查完成，已剔除 {excess} 条冗余/低价值用例。\n"
                            else:
                                raise ValueError("QA Agent returned invalid result")

                        except Exception as e:
                            # Fallback to Rule-Based Pruning if LLM fails
                            yield f"@@STATUS@@:QA Agent 审查异常 ({str(e)})，转为使用规则评分算法进行筛选...\n"
                            
                            def calculate_value(case):
                                score = 0
                                p = case.get("priority", "P1").upper()
                                if p == "P0": score += 100
                                elif p == "P1": score += 50
                                else: score += 10 # P2 or unknown
                                
                                # Keywords bonus
                                desc = str(case.get("description", "")).lower()
                                if any(k in desc for k in ["安全", "xss", "sql", "security", "性能", "perf", "并发"]):
                                    score += 20
                                return score

                            # Annotate with score and original index
                            annotated = []
                            for idx, case in enumerate(parsed_result):
                                annotated.append({
                                    "case": case,
                                    "score": calculate_value(case),
                                    "index": idx
                                })
                            
                            # Sort by Score ASC, then Index DESC (to remove lowest value, latest generated)
                            annotated.sort(key=lambda x: (x["score"], -x["index"]))
                            
                            # Mark indices to keep
                            indices_to_remove = set()
                            for i in range(excess):
                                indices_to_remove.add(annotated[i]["index"])
                            
                            # Reconstruct
                            final_result = []
                            for idx, case in enumerate(parsed_result):
                                if idx not in indices_to_remove:
                                    final_result.append(case)
                            
                            parsed_result = final_result
                            yield f"@@STATUS@@:规则筛选完成，保留了高优先级及关键用例。\n"


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
                        merged_result = existing_cases + parsed_result
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
                    count = len(parsed_result) if isinstance(parsed_result, list) else 0
                    
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
                    avg_steps = 0.0
                    pending = 0
                    steps_count = 0
                    steps_items = 0
                    kw_neg = ["失败", "错误", "异常", "不可用", "拒绝", "超时", "Invalid", "Fail", "Error", "Exception", "Timeout", "Deny"]
                    kw_edge = ["边界", "最大值", "最小值", "极限", "临界", "空值", "重复", "特殊字符", "溢出", "Boundary", "Edge", "Max", "Min", "Limit", "Critical", "Null", "Empty", "Overflow"]
                    
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
                        "avg_steps": avg_steps,
                        "pending": pending,
                        "generated_count": len(parsed_result) if isinstance(parsed_result, list) else 0
                    }
                    
                    db.add(LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}",
                        user_id=user_id
                    ))
                    # Also yield to stream for real-time frontend update
                    yield f"GEN_QM:{json.dumps(qm, ensure_ascii=False)}\n"
                    
                    db.commit()
                except Exception as log_e:
                    print(f"Failed to log metrics: {log_e}")

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

