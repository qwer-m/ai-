from __future__ import annotations

import json
from importlib import import_module
from typing import Any

from sqlalchemy.orm import Session

from .schemas import StageKey


def _test_generator():
    return import_module("modules.testing.test_generation").test_generator


def _ui_automator():
    return import_module("modules.testing.ui_automation").ui_automator


def _api_tester():
    return import_module("modules.testing.api_testing").api_tester


def _evaluator():
    return import_module("modules.testing.evaluation").evaluator


def _execute_stage_once(
    stage: StageKey,
    payload: dict[str, Any],
    artifacts: dict[str, Any],
    db: Session,
    project_id: int,
    user_id: int,
) -> dict[str, Any]:
    """
    执行单个 pipeline 阶段。

    保持现有返回契约：
    - status: success / skipped / failed
    - message: 阶段说明
    - artifacts: 新产物快照
    - meta: 诊断信息
    """
    stage_artifacts = dict(artifacts or {})
    try:
        if stage == "test_generation":
            requirement = str(payload.get("requirement") or "").strip()
            if not requirement:
                raise ValueError("Missing pipeline requirement.")
            expected_count = int(payload.get("expected_count") or 20)
            compress = bool(payload.get("compress") or False)

            test_cases = _test_generator().generate_test_cases_json(
                requirement=requirement,
                project_id=project_id,
                db=db,
                doc_type="requirement",
                compress=compress,
                expected_count=max(1, expected_count),
                batch_size=20,
                batch_index=0,
                user_id=user_id,
            )
            if isinstance(test_cases, dict) and test_cases.get("error"):
                raise RuntimeError(str(test_cases.get("error")))

            generated_text = test_cases if isinstance(test_cases, str) else json.dumps(test_cases, ensure_ascii=False)
            generated_count = len(test_cases) if isinstance(test_cases, list) else 1
            stage_artifacts["test_generation"] = {
                "generated_cases": generated_text,
                "generated_count": generated_count,
            }
            return {
                "status": "success",
                "message": f"Generated {generated_count} cases.",
                "artifacts": stage_artifacts,
                "meta": {"generated_count": generated_count},
            }

        if stage == "ui_automation":
            ui_cfg = dict(payload.get("ui") or {})
            task = str(ui_cfg.get("task") or "").strip() or str(payload.get("requirement") or "").strip()
            target = str(ui_cfg.get("target") or "http://localhost:5173")
            automation_type = str(ui_cfg.get("automation_type") or "web")

            ui_automator = _ui_automator()
            script = ui_automator.generate_ai_image_recognition_script(
                task_description=task,
                url=target,
                automation_type=automation_type,
                db=db,
                user_id=user_id,
                token=None,
                image_model=None,
                requirement_context=None,
            )
            exec_result = ui_automator.execute_script(
                script=script,
                url=target,
                task_description=task,
                automation_type=automation_type,
                db=db,
                project_id=project_id,
                user_id=user_id,
            )
            exec_status = str(exec_result.get("status") or "failed")
            output = (
                f"status: {exec_status}\n\nstdout:\n{exec_result.get('stdout') or ''}\n\n"
                f"stderr:\n{exec_result.get('stderr') or exec_result.get('error') or ''}"
            )
            stage_artifacts["ui_automation"] = {
                "script": script or "",
                "execution_result": output,
                "raw_result": exec_result,
            }
            if exec_status == "failed":
                return {
                    "status": "failed",
                    "message": "UI execution returned failed status.",
                    "artifacts": stage_artifacts,
                    "meta": {"exec_status": exec_status},
                }
            return {
                "status": "success",
                "message": "UI automation completed.",
                "artifacts": stage_artifacts,
                "meta": {"exec_status": exec_status},
            }

        if stage == "api_automation":
            api_cfg = dict(payload.get("api") or {})
            api_requirement = str(api_cfg.get("requirement") or "").strip() or str(payload.get("requirement") or "").strip()
            base_url = str(api_cfg.get("base_url") or "")
            api_path = str(api_cfg.get("api_path") or "")
            mode = str(api_cfg.get("mode") or "structured")
            test_types = list(api_cfg.get("test_types") or ["Functional"])

            api_tester = _api_tester()
            script = api_tester.generate_api_test_script(
                requirement=api_requirement,
                base_url=base_url,
                api_path=api_path,
                test_types=test_types,
                api_docs="",
                db=db,
                mode=mode,
                user_id=user_id,
            )
            exec_result = api_tester.execute_api_tests(
                script_content=script,
                requirement=api_requirement,
                base_url=base_url,
                db=db,
                project_id=project_id,
                user_id=user_id,
            )
            failed = int(((exec_result.get("structured_report") or {}).get("failed") or 0))
            output = (
                f"result:\n{exec_result.get('result') or ''}\n\nstructured_report:\n"
                f"{json.dumps(exec_result.get('structured_report') or {}, ensure_ascii=False)}"
            )
            stage_artifacts["api_automation"] = {
                "script": script or "",
                "execution_result": output,
                "raw_result": exec_result,
            }
            if failed > 0:
                return {
                    "status": "failed",
                    "message": f"API tests completed with {failed} failures.",
                    "artifacts": stage_artifacts,
                    "meta": {"failed": failed},
                }
            return {
                "status": "success",
                "message": "API automation completed.",
                "artifacts": stage_artifacts,
                "meta": {"failed": failed},
            }

        eval_cfg = dict(payload.get("evaluation") or {})
        run_testcase_eval = bool(eval_cfg.get("run_testcase_eval"))
        run_ui_eval = bool(eval_cfg.get("run_ui_eval", True))
        run_api_eval = bool(eval_cfg.get("run_api_eval", True))
        baseline = str(eval_cfg.get("baseline_test_cases") or "")

        sections: list[str] = []
        warnings: list[str] = []
        selected_any = run_testcase_eval or run_ui_eval or run_api_eval

        if run_testcase_eval:
            generated_cases = str((stage_artifacts.get("test_generation") or {}).get("generated_cases") or "")
            if generated_cases.strip() and baseline.strip():
                evaluator = _evaluator()
                result = evaluator.compare_test_cases(
                    generated_test_case=generated_cases,
                    modified_test_case=baseline,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                )
                sections.append(f"## Test Case Evaluation\n{result}")
            else:
                warnings.append("Test case evaluation skipped: missing generated cases or baseline.")

        if run_ui_eval:
            ui_art = dict(stage_artifacts.get("ui_automation") or {})
            script = str(ui_art.get("script") or "")
            execution_result = str(ui_art.get("execution_result") or "")
            if script.strip() and execution_result.strip():
                evaluator = _evaluator()
                result = evaluator.evaluate_ui_automation(
                    ui_script=script,
                    execution_result=execution_result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    journey_json=None,
                )
                sections.append(f"## UI Evaluation\n{result}")
            else:
                warnings.append("UI evaluation skipped: missing script or execution result.")

        if run_api_eval:
            api_art = dict(stage_artifacts.get("api_automation") or {})
            script = str(api_art.get("script") or "")
            execution_result = str(api_art.get("execution_result") or "")
            if script.strip() and execution_result.strip():
                evaluator = _evaluator()
                result = evaluator.evaluate_api_test(
                    api_script=script,
                    execution_result=execution_result,
                    db=db,
                    project_id=project_id,
                    user_id=user_id,
                    openapi_spec=None,
                )
                sections.append(f"## API Evaluation\n{result}")
            else:
                warnings.append("API evaluation skipped: missing script or execution result.")

        if not selected_any:
            return {
                "status": "skipped",
                "message": "No evaluation selected.",
                "artifacts": stage_artifacts,
                "meta": {"warnings": warnings},
            }

        output = "\n\n".join([*sections, *(["## Evaluation Warnings", *warnings] if warnings else [])])
        stage_artifacts["evaluation"] = {"output": output, "warnings": warnings}
        if sections:
            return {
                "status": "success",
                "message": "Evaluation completed.",
                "artifacts": stage_artifacts,
                "meta": {"sections": len(sections), "warnings": len(warnings)},
            }
        return {
            "status": "failed",
            "message": "; ".join(warnings) or "Evaluation failed.",
            "artifacts": stage_artifacts,
            "meta": {"warnings": warnings},
        }
    except Exception as e:
        return {
            "status": "failed",
            "message": f"{type(e).__name__}: {e}",
            "artifacts": stage_artifacts,
            "meta": {"exception_type": type(e).__name__},
        }

