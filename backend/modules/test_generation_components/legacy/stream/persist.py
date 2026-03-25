from typing import Any, Iterator
import json

from core.db.models import LogEntry, TestGeneration
from modules.domain.stage25_switches import STAGE25_SWITCHES
from modules.testing.test_generation_components.prompting.generation_diagnostics import build_coverage_diagnostics
from modules.testing.test_generation_components.prompting.prompt_orchestration import (
    build_supplement_closed_loop_instruction,
)
from modules.testing.test_generation_components.postprocess.result_postprocess import (
    merge_cases_for_append,
    stream_postprocess_cases,
)
from modules.testing.test_generation_components.legacy.adapters import (
    count_unique_test_cases,
    deduplicate_test_cases,
    infer_case_kind,
    normalize_json_structure,
    reorder_cases_by_closed_loop,
    clean_and_parse_json,
)


class LegacyGenerationStreamPersistMixin:

    def _stream_persist_phase(
        self,
        *,
        state: dict[str, Any],
    ) -> Iterator[None]:
        client = state["client"]
        requirement = state["requirement"]
        project_id = state["project_id"]
        db = state["db"]
        doc_type = state["doc_type"]
        compress = state["compress"]
        expected_count = state["expected_count"]
        overwrite = state["overwrite"]
        append = state["append"]
        user_id = state["user_id"]
        original_requirement = state["original_requirement"]
        kb_context = state.get("kb_context") or ""
        start_id = int(state.get("start_id") or 1)
        existing_cases = state.get("existing_cases") or []
        existing_entry = state.get("existing_entry")
        context_result = state.get("context_result") or {}
        gate_debug = state.get("gate_debug") or {}
        base_prompt = state.get("base_prompt") or ""
        full_content = state.get("full_content") or ""
        existing_unique_count = int(state.get("existing_unique_count") or 0)
        system_prompt = state.get("system_prompt") or ""
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
