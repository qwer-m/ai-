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
        current_biz_key = str(state.get("current_biz_key") or "")
        multi_pass = bool(state.get("multi_pass", True))
        generation_mode = str(state.get("generation_mode") or "").strip().lower()

        try:
            postprocess_result = yield from stream_postprocess_cases(
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
                current_biz_key=current_biz_key,
                multi_pass=multi_pass,
                generation_mode=generation_mode,
            )

            stage_counts: dict[str, Any] = {}
            coverage_payload: dict[str, Any] = {}
            if isinstance(postprocess_result, dict):
                parsed_result = postprocess_result.get("cases")
                if not isinstance(parsed_result, list):
                    parsed_result = []
                stage_counts = dict(postprocess_result.get("stage_counts") or {})
                coverage_payload = dict(postprocess_result.get("coverage") or {})
            else:
                parsed_result = postprocess_result if isinstance(postprocess_result, list) else []

            if len(parsed_result) == 0:
                yield "\n@@STATUS@@:鐢熸垚澶辫触\n"
                yield "Error: 妯″瀷杩斿洖绌虹粨鏋滄垨瑙ｆ瀽涓嶅埌鏈夋晥鐢ㄤ緥锛岃妫€鏌ユā鍨嬮厤缃?鎻愮ず璇?缃戠粶鍚庨噸璇昞n"

            cleaned_response = json.dumps(parsed_result, ensure_ascii=False)

            if db:
                if overwrite:
                    from sqlalchemy import desc

                    query = db.query(TestGeneration).filter(
                        TestGeneration.project_id == project_id,
                        TestGeneration.requirement_text == original_requirement,
                    )
                    if user_id:
                        query = query.filter(TestGeneration.user_id == user_id)
                    existing_entry_overwrite = query.order_by(desc(TestGeneration.created_at)).first()
                    if existing_entry_overwrite:
                        existing_entry_overwrite.generated_result = cleaned_response
                        db.commit()
                    else:
                        db.add(
                            TestGeneration(
                                requirement_text=original_requirement,
                                generated_result=cleaned_response,
                                project_id=project_id,
                                user_id=user_id,
                            )
                        )
                        db.commit()
                elif append and existing_entry:
                    merged_result = merge_cases_for_append(
                        existing_cases,
                        parsed_result,
                        deduplicate_test_cases_fn=deduplicate_test_cases,
                        reorder_cases_by_closed_loop_fn=reorder_cases_by_closed_loop,
                    )
                    existing_entry.generated_result = json.dumps(merged_result, ensure_ascii=False)
                    db.commit()
                else:
                    db.add(
                        TestGeneration(
                            requirement_text=original_requirement,
                            generated_result=cleaned_response,
                            project_id=project_id,
                            user_id=user_id,
                        )
                    )
                    db.commit()

                mode_payload = {
                    "kind": "generation_mode",
                    "mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    "biz_keys": [current_biz_key or "unknown"],
                    "current_biz_key": current_biz_key or "unknown",
                    "multi_pass": bool(multi_pass),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(mode_payload, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )
                yield f"GEN_DIAG:{json.dumps(mode_payload, ensure_ascii=False)}\\n"

                # 中文注释：记录阶段日志，便于观察 multi-pass 执行情况。
                for stage in ("primary", "gap", "review"):
                    payload = {
                        "kind": "generation_stage",
                        "stage": stage,
                        "case_count": int(stage_counts.get(stage, 0)),
                        "multi_pass": bool(multi_pass),
                        "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                    }
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(payload, ensure_ascii=False)}\n"

                # GEN_DIAG 鎬昏
                full_input = (system_prompt or "") + requirement
                actual_model = client.select_model(full_input, task_type="generation")
                diag = {
                    "kind": "gen_diag",
                    "mode": "stream",
                    "doc_type": doc_type,
                    "compress": compress,
                    "expected_count": expected_count,
                    "generated_count": count_unique_test_cases(parsed_result),
                    "content_length": len(requirement),
                    "kb_length": len(kb_context or ""),
                    "model": actual_model,
                    "max_tokens": client.max_tokens,
                    "multi_pass": bool(multi_pass),
                    "generation_mode": generation_mode or ("multi_pass" if multi_pass else "single_pass"),
                }
                db.add(
                    LogEntry(
                        project_id=project_id,
                        log_type="system",
                        message=f"GEN_DIAG:{json.dumps(diag, ensure_ascii=False)}",
                        user_id=user_id,
                    )
                )

                # 中文注释：记录覆盖检查日志。
                if coverage_payload:
                    coverage_payload["multi_pass"] = bool(multi_pass)
                    coverage_payload["generation_mode"] = generation_mode or ("multi_pass" if multi_pass else "single_pass")
                    db.add(
                        LogEntry(
                            project_id=project_id,
                            log_type="system",
                            message=f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}",
                            user_id=user_id,
                        )
                    )
                    yield f"GEN_DIAG:{json.dumps(coverage_payload, ensure_ascii=False)}\n"

                # 淇濈暀鏃㈡湁瑕嗙洊璇婃柇
                if STAGE25_SWITCHES.coverage_diagnostics_enabled:
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
