from typing import Any
import json
import uuid

from sqlalchemy.orm import Session

from core.ai_client import get_client_for_user
from core.models import TestGeneration, LogEntry
from modules.stage25_switches import STAGE25_SWITCHES
from modules.test_generation_components.generation_diagnostics import (
    build_coverage_diagnostics,
    build_gate_reason_chain,
)
from modules.test_generation_components.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
    build_closed_loop_base_prompt,
    build_supplement_closed_loop_instruction,
)
from modules.test_generation_components.result_postprocess import (
    finalize_generated_cases,
    merge_cases_for_append,
    prepare_append_existing_cases,
    stream_postprocess_cases,
)
from modules.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    normalize_json_structure,
    deduplicate_test_cases,
    count_unique_test_cases,
    infer_case_kind,
    reorder_cases_by_closed_loop,
    convert_json_to_excel as _convert_json_to_excel_adapter,
)


class LegacyGenerationJsonMixin:

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

    def generate_test_cases_excel(self, requirement: str, project_id: int, db: Session = None, doc_type: str = "requirement", compress: bool = False, user_id: int = None) -> bytes:
        # Generate test cases in JSON format
        json_result = self.generate_test_cases_json(requirement, project_id, db, doc_type, compress, user_id=user_id)
        
        return self.convert_json_to_excel(json_result)

    def convert_json_to_excel(self, json_data: list | dict) -> bytes:
        """
        导出入口保持原方法签名，内部委托给组件实现。

        这样可避免路由层调用路径变化，同时让导出逻辑与生成编排逻辑解耦。
        """
        return _convert_json_to_excel_adapter(json_data)
