from typing import Any
import json
import uuid

from sqlalchemy.orm import Session

from core.ai.ai_client import get_client_for_user
from core.db.models import TestGeneration, LogEntry
from modules.domain.stage25_switches import STAGE25_SWITCHES
from modules.testing.test_generation_components.prompting.generation_diagnostics import (
    build_coverage_diagnostics,
    build_gate_reason_chain,
)
from modules.testing.test_generation_components.coverage.coverage_analyzer import (
    analyze_coverage,
)
from modules.testing.test_generation_components.prompting.prompt_orchestration import (
    build_append_closed_loop_coverage_instruction,
    build_closed_loop_base_prompt,
    build_supplement_closed_loop_instruction,
)
from modules.testing.test_generation_components.prompting.structured_context import (
    build_structured_prompt_context,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    finalize_generated_cases,
    merge_cases_for_append,
    prepare_append_existing_cases,
    stream_postprocess_cases,
)
from modules.testing.test_generation_components.legacy.multi_pass_pipeline import (
    run_multi_pass_generation,
)
from modules.testing.test_generation_components.legacy.adapters import (
    clean_and_parse_json,
    normalize_json_structure,
    deduplicate_test_cases,
    count_unique_test_cases,
    infer_case_kind,
    reorder_cases_by_closed_loop,
    convert_json_to_excel as _convert_json_to_excel_adapter,
)


class LegacyGenerationJsonMixin:

    def generate_test_cases_json(
        self,
        requirement: str,
        project_id: int,
        db: Session = None,
        doc_type: str = "requirement",
        compress: bool = False,
        expected_count: int = 20,
        batch_size: int = 20,
        batch_index: int = 0,
        user_id: int = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
    ) -> dict:
        """
        鐢熸垚娴嬭瘯鐢ㄤ緥 - JSON 鏍煎紡 (Generate Test Cases JSON)
        
        Args:
            requirement: 闇€姹傛枃鏈€?
            project_id: 椤圭洰ID锛岀敤浜?RAG 妫€绱笂涓嬫枃銆?
            db: 鏁版嵁搴撲細璇濄€?
            doc_type: 鏂囨。绫诲瀷 (requirement, prototype, incomplete)銆備笉鍚岀殑绫诲瀷浼氳Е鍙戜笉鍚岀殑 Prompt 绛栫暐銆?
            compress: 鏄惁鍚敤涓婁笅鏂囧帇缂╋紙閽堝瓒呴暱鏂囨湰锛夈€?
            expected_count: 棰勬湡鎬绘暟閲忋€?
            batch_size: 褰撳墠鎵规澶у皬銆?
            batch_index: 褰撳墠鎵规绱㈠紩 (鐢ㄤ簬璁＄畻 ID 璧峰鍊?銆?
            user_id: 鐢ㄦ埛ID锛岀敤浜庤幏鍙栫壒瀹氱殑 AI 妯″瀷閰嶇疆銆?
            
        Returns:
            dict: 鍖呭惈鐢熸垚鐨勭敤渚嬪垪琛紝鎴栬€呴敊璇俊鎭€?
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
            # 涓枃娉ㄩ噴锛氱敓鎴愬墠鍏堟墽琛?snapshot readiness gate锛岄伩鍏?stale 鍦烘櫙鐩存帴 rag_only銆?
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
                # 涓枃娉ㄩ噴锛氭妸 gate 璋冭瘯瀛楁鍚堝苟杩涜瀺鍚堣皟璇曪紝鏂逛究缁熶竴瑙傚療銆?
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
            # 涓枃娉ㄩ噴锛氬懡涓€滀笂涓嬫枃鍏ㄧ┖鍏滃簳鈥濇椂锛岀洿鎺ョ粓姝紝閬垮厤妯″瀷鍦ㄦ棤鐭ヨ瘑涓婁笅鏂囦笅瑁歌窇銆?
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
                # 闇€姹傛枃鏈帇缂╀笌鐭ヨ瘑蹇収鏄袱鏉″苟琛屼紭鍖栭摼璺紝浠讳綍涓€鏉″け璐ラ兘涓嶉樆鏂敓鎴愩€?
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
        if isinstance(analysis_result, dict):
            strategy_plan = analysis_result
        else:
            # 中文注释：元分析异常时使用默认策略，避免中断主链路。
            strategy_plan = self._default_strategy_plan()
        prompt_context = build_structured_prompt_context(
            requirement=requirement or "",
            kb_context=kb_context or "",
            rag_result=(context_result or {}).get("rag_result") if isinstance(context_result, dict) else None,
            existing_cases=[],
            current_biz_key=current_biz_key,
            only_current_biz=bool(only_current_biz),
        )
        resolved_current_biz = str(prompt_context.get("current_biz_key") or "unknown")
        base_prompt = build_closed_loop_base_prompt(
            strategy_plan,
            requirement_context=prompt_context.get("requirement_context") or "",
            testcase_context=prompt_context.get("testcase_context") or "(empty)",
            supplement_context=prompt_context.get("supplement_context") or "(empty)",
            current_biz_key=resolved_current_biz,
            doc_type=doc_type,
            pretty_json=False,
        )
        # 中文注释：在模型调用前输出 biz_key 隔离诊断，便于日志面板观察。
        if self._is_active_db_session(db):
            try:
                isolation_payload = dict(prompt_context.get("biz_key_isolation_log") or {})
                if isolation_payload:
                    isolation_payload.update(
                        {
                            "project_id": int(project_id),
                            "request_id": request_id,
                            "source": "generate_test_cases_json",
                        }
                    )
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            user_id=user_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(isolation_payload, ensure_ascii=False)}",
                        )
                    )
                    db.commit()
            except Exception as e:
                try:
                    db.rollback()
                except Exception:
                    pass
                print(f"Failed to emit biz key isolation log(json): {e}")

        # Calculate start number for IDs based on batch index
        start_id = batch_index * batch_size + 1
        stage_logs: list[dict[str, Any]] = []
        coverage_check_payload: dict[str, Any] | None = None
        raw_response_payload: Any = ""

        system_prompt = f"""
{base_prompt}

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
        normalized_generation_mode = str(generation_mode or "").strip().lower()
        use_pipeline = bool(multi_pass) or normalized_generation_mode in {
            "single_pass",
            "multi_pass",
            "biz_key_multi_pass",
        }
        if use_pipeline:
            # 中文注释：统一由 pipeline 根据 generation_mode 决定生成策略。
            multi_pass_result = run_multi_pass_generation(
                client=client,
                requirement=requirement,
                db=db,
                base_prompt=system_prompt,
                requirement_context=prompt_context.get("requirement_context") or requirement,
                current_biz_key=resolved_current_biz,
                expected_count=int(expected_count or batch_size or 1),
                start_id=start_id,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                multi_pass=bool(multi_pass),
                generation_mode=generation_mode,
                prompt_context=prompt_context,
                build_base_prompt_fn=lambda req_ctx, tc_ctx, sup_ctx, biz_key: build_closed_loop_base_prompt(
                    strategy_plan,
                    requirement_context=req_ctx,
                    testcase_context=tc_ctx,
                    supplement_context=sup_ctx,
                    current_biz_key=biz_key,
                    doc_type=doc_type,
                    pretty_json=False,
                ),
            )
            result = multi_pass_result.get("final_cases") or []
            stage_logs = list(multi_pass_result.get("stage_logs") or [])
            coverage_check_payload = dict(multi_pass_result.get("coverage") or {})
            raw_response_payload = multi_pass_result.get("raw") or {}
        else:
            response = client.generate_response(
                requirement,
                system_prompt,
                db=db,
                task_type="generation",
            )
            raw_response_payload = response
            result = finalize_generated_cases(
                response,
                start_id=start_id,
                clean_and_parse_json_fn=clean_and_parse_json,
                normalize_json_structure_fn=normalize_json_structure,
                deduplicate_test_cases_fn=deduplicate_test_cases,
                reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
            )
            stage_logs = [
                {"kind": "generation_mode", "mode": "single_pass", "biz_keys": [resolved_current_biz], "current_biz_key": resolved_current_biz},
                {"kind": "generation_stage", "stage": "primary", "case_count": len(result) if isinstance(result, list) else 0},
                {"kind": "generation_stage", "stage": "gap", "case_count": 0},
                {"kind": "generation_stage", "stage": "review", "case_count": len(result) if isinstance(result, list) else 0},
            ]
            if isinstance(result, list):
                coverage_check_payload = {
                    "kind": "coverage_check",
                    **analyze_coverage(prompt_context.get("requirement_context") or requirement, result),
                }
            
        # Save to DB if session provided
        if db:
            try:
                if self._is_active_db_session(db):
                    for stage_log in stage_logs:
                        payload = dict(stage_log)
                        payload.update(
                            {
                                "request_id": request_id,
                                "multi_pass": bool(multi_pass),
                                "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                            }
                        )
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                            )
                        )
                    if coverage_check_payload:
                        coverage_payload = dict(coverage_check_payload)
                        coverage_payload.update(
                            {
                                "request_id": request_id,
                                "multi_pass": bool(multi_pass),
                                "generation_mode": normalized_generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                            }
                        )
                        db.add(
                            LogEntry(
                                project_id=project_id,
                                user_id=user_id,
                                log_type="system",
                                message=f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}",
                            )
                        )
                    db.commit()

                db_entry = TestGeneration(
                    requirement_text=original_requirement,
                    generated_result=json.dumps(result, ensure_ascii=False)
                    if not (isinstance(result, dict) and ("error" in result))
                    else json.dumps({"error": result, "raw": raw_response_payload}, ensure_ascii=False),
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

    def generate_test_cases_excel(
        self,
        requirement: str,
        project_id: int,
        db: Session = None,
        doc_type: str = "requirement",
        compress: bool = False,
        user_id: int = None,
        current_biz_key: str = "",
        only_current_biz: bool = False,
        multi_pass: bool = True,
        generation_mode: str = "",
    ) -> bytes:
        # Generate test cases in JSON format
        json_result = self.generate_test_cases_json(
            requirement,
            project_id,
            db,
            doc_type,
            compress,
            user_id=user_id,
            current_biz_key=current_biz_key,
            only_current_biz=only_current_biz,
            multi_pass=multi_pass,
            generation_mode=generation_mode,
        )
        
        return self.convert_json_to_excel(json_result)

    def convert_json_to_excel(self, json_data: list | dict) -> bytes:
        """
        瀵煎嚭鍏ュ彛淇濇寔鍘熸柟娉曠鍚嶏紝鍐呴儴濮旀墭缁欑粍浠跺疄鐜般€?

        杩欐牱鍙伩鍏嶈矾鐢卞眰璋冪敤璺緞鍙樺寲锛屽悓鏃惰瀵煎嚭閫昏緫涓庣敓鎴愮紪鎺掗€昏緫瑙ｈ€︺€?
        """
        return _convert_json_to_excel_adapter(json_data)
