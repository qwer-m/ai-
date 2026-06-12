import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from modules.testing import evaluation as evaluation_module
from modules.testing.evaluation import (
    EvaluationModule,
    _normalize_compare_result_json,
    _parse_test_cases_payload,
)


def _build_payload(missing_points: list[str], hallucinations: list[str]) -> str:
    return json.dumps(
        {
            "metrics": {
                "precision": 0.5,
                "recall": 0.5,
                "f1_score": 0.5,
                "semantic_similarity": 0.5,
            },
            "defect_analysis": {
                "missing_points": missing_points,
                "hallucinations": hallucinations,
                "modifications": [],
            },
            "summary": "test",
        },
        ensure_ascii=False,
    )


def test_normalizer_swaps_when_direction_is_obviously_reversed() -> None:
    raw = _build_payload(
        missing_points=[
            "断网答题后网络恢复自动同步至词汇档案（TC-002）",
        ],
        hallucinations=[
            "新增“单词本”相关用例，原生成用例未包含单词本模块",
            "新增断网下点击“继续学习”应提示网络异常的用例",
        ],
    )

    normalized = json.loads(_normalize_compare_result_json(raw))
    defect = normalized["defect_analysis"]
    assert defect["missing_points"][0].startswith("新增“单词本”相关用例")
    assert defect["hallucinations"][0].startswith("断网答题后网络恢复自动同步至词汇档案")


def test_normalizer_keeps_correct_orientation() -> None:
    raw = _build_payload(
        missing_points=[
            "未覆盖支付失败后的重试提示",
            "原生成用例未包含弱网重连验证",
        ],
        hallucinations=[
            "多余增加了与需求无关的邮箱绑定场景",
            "重复验证了同一路径，属于冗余步骤",
        ],
    )

    normalized = json.loads(_normalize_compare_result_json(raw))
    defect = normalized["defect_analysis"]
    assert defect["missing_points"][0] == "未覆盖支付失败后的重试提示"
    assert defect["hallucinations"][0] == "多余增加了与需求无关的邮箱绑定场景"


def test_normalizer_does_not_swap_without_enough_signal() -> None:
    raw = _build_payload(
        missing_points=["场景 A", "场景 B"],
        hallucinations=["场景 C", "场景 D"],
    )

    normalized = json.loads(_normalize_compare_result_json(raw))
    defect = normalized["defect_analysis"]
    assert defect["missing_points"] == ["场景 A", "场景 B"]
    assert defect["hallucinations"] == ["场景 C", "场景 D"]


def test_compare_large_input_uses_balanced_single_pass(monkeypatch) -> None:
    class SinglePassClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def generate_response(self, prompt, system_prompt, **kwargs):
            payload = json.loads(prompt)
            self.calls.append({"prompt": payload, "system_prompt": system_prompt, "kwargs": kwargs})
            assert "groups" in payload
            assert payload["requirement_text"].startswith("需求：手机号验证码登录")
            assert isinstance(payload.get("requirement_heuristic_baseline"), dict)
            assert "requirement_baseline" in (payload.get("json_schema") or {})
            assert "每条不超过 20" not in system_prompt
            assert "chunk_results" not in payload
            assert "case_judgements" not in (payload.get("json_schema") or {})
            assert all(isinstance(item.get("generated"), list) for item in payload["groups"])
            return json.dumps(
                {
                    "metrics": {
                        "precision": 0.9,
                        "recall": 0.8,
                        "f1_score": 0.8471,
                        "semantic_similarity": 0.85,
                    },
                    "defect_analysis": {
                        "missing_points": ["需求要求错误验证码提示，AI 生成用例未覆盖该异常登录路径"],
                        "hallucinations": ["AI 生成的资料绑定流程不在需求和人工最终用例中"],
                        "modifications": [],
                    },
                    "requirement_baseline": {
                        "requirement_points": ["手机号验证码登录", "错误验证码提示"],
                        "ai_requirement_gaps": ["错误验证码提示"],
                        "human_requirement_gaps": [],
                        "ai_unanchored_points": ["资料绑定流程"],
                        "human_added_value": ["错误验证码提示"],
                        "both_missing_points": [],
                        "summary": "已按需求锚点评估",
                    },
                    "summary": "compact single pass completed",
                },
                ensure_ascii=False,
            )

    monkeypatch.setenv("EVAL_LLM_COMPARE_MAX_CHARS", "300")
    monkeypatch.setenv("EVAL_LLM_COMPARE_SINGLE_PASS_MAX_CHARS", "100000")
    monkeypatch.setenv("EVAL_LLM_COMPARE_SINGLE_PASS_BRIEF_CHARS", "80")
    fake_client = SinglePassClient()
    monkeypatch.setattr(evaluation_module, "get_client_for_user", lambda *args, **kwargs: fake_client)

    long_steps = "Open page, input data, submit, and verify the business result. " * 8
    generated = json.dumps(
        [
            {
                "id": "TC-001",
                "module": "login",
                "description": "phone verification login succeeds",
                "steps": long_steps,
                "expected_result": "login success",
            },
            {
                "id": "TC-002",
                "module": "profile",
                "description": "extra profile binding flow",
                "steps": long_steps,
                "expected_result": "profile binding success",
            },
        ],
        ensure_ascii=False,
    )
    modified = json.dumps(
        [
            {
                "id": "TC-001",
                "module": "login",
                "description": "phone verification login succeeds",
                "steps": long_steps,
                "expected_result": "login success",
            },
            {
                "id": "TC-003",
                "module": "login",
                "description": "wrong verification code shows error",
                "steps": long_steps,
                "expected_result": "show verification code error",
            },
        ],
        ensure_ascii=False,
    )

    payload = json.loads(
        EvaluationModule().compare_test_cases(
            generated,
            modified,
            db=None,
            project_id=8,
            user_id=1,
            requirement_text="需求：手机号验证码登录成功；错误验证码需要展示明确错误提示。",
        )
    )

    assert payload["analysis_status"] == "completed"
    assert payload["analysis_mode"] == "llm_single_pass_balanced"
    assert payload["is_final_evaluation"] is True
    assert payload["metrics"]["precision"] == 0.9
    assert payload["chunk_summary"]["failed_chunk_count"] == 0
    assert payload["chunk_summary"]["aggregation"] == "single_pass_balanced_model"
    assert payload["chunk_summary"]["requirement_text_in_prompt"] is True
    assert payload["chunk_summary"]["requirement_baseline_in_prompt"] is True
    assert payload["input_stats"]["llm_single_pass_case_brief_chars"] == 80
    assert payload["input_stats"]["llm_single_pass_requirement_chars"] > 0
    assert payload["requirement_baseline"]["summary"] == "已按需求锚点评估"
    assert "heuristic" in payload["requirement_baseline"]
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["kwargs"]["task_type"] == "review"


def test_compare_large_input_uses_chunked_model_compare(monkeypatch) -> None:
    class ChunkedCompareClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def generate_response(self, prompt, system_prompt, **kwargs):
            self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
            if "汇总" in system_prompt:
                return json.dumps(
                    {
                        "metrics": {
                            "precision": 0.5,
                            "recall": 0.5,
                            "f1_score": 0.5,
                            "semantic_similarity": 0.5,
                        },
                        "defect_analysis": {
                            "missing_points": ["TC-003 - 错题本按题型筛选"],
                            "hallucinations": ["TC-002 - 微信授权登录"],
                            "modifications": [],
                        },
                        "summary": "模型汇总后的正式评测结果",
                    },
                    ensure_ascii=False,
                )
            payload = json.loads(prompt)
            return json.dumps(
                {
                    "chunk_index": payload["chunk_index"],
                    "metrics": {
                        "precision": 1.0,
                        "recall": 1.0,
                        "f1_score": 1.0,
                        "semantic_similarity": 1.0,
                    },
                    "defect_analysis": {
                        "missing_points": [],
                        "hallucinations": [],
                        "modifications": [],
                    },
                    "case_judgements": [],
                    "summary": "分片评测完成",
                },
                ensure_ascii=False,
            )

    monkeypatch.setenv("EVAL_LLM_COMPARE_MAX_CHARS", "300")
    monkeypatch.setenv("EVAL_LLM_COMPARE_SINGLE_PASS_MAX_CHARS", "0")
    monkeypatch.setenv("EVAL_LLM_COMPARE_CHUNK_CASES", "1")
    fake_client = ChunkedCompareClient()
    monkeypatch.setattr(evaluation_module, "get_client_for_user", lambda *args, **kwargs: fake_client)

    generated = json.dumps(
        {
            "test_cases": [
                {
                    "用例编号": "TC-001",
                    "模块": "登录",
                    "用例标题": "手机号验证码登录成功",
                    "测试步骤": "输入手机号，获取验证码，填写正确验证码并提交。" * 8,
                    "预期结果": "登录成功进入首页",
                },
                {
                    "用例编号": "TC-002",
                    "模块": "登录",
                    "用例标题": "微信授权登录",
                    "测试步骤": "点击微信登录并授权。" * 8,
                    "预期结果": "登录成功进入首页",
                },
            ]
        },
        ensure_ascii=False,
    )
    modified = "\n".join(
        [
            "用例编号,模块,用例标题,测试步骤,预期结果",
            "TC-001,登录,手机号验证码登录成功,输入手机号并提交正确验证码,登录成功进入首页",
            "TC-003,错题本,错题本按题型筛选,进入错题本选择题型筛选条件,仅展示对应题型错题",
        ]
    )

    payload = json.loads(
        EvaluationModule().compare_test_cases(
            generated,
            modified,
            db=None,
            project_id=8,
            user_id=1,
        )
    )

    assert payload["analysis_status"] == "completed"
    assert payload["analysis_mode"] == "llm_chunked"
    assert payload["is_final_evaluation"] is True
    assert payload["input_stats"]["generated_case_count"] == 2
    assert payload["input_stats"]["modified_case_count"] == 2
    assert payload["input_stats"]["llm_chunk_count"] >= 2
    assert payload["metrics"]["precision"] == 0.5
    assert payload["metrics"]["recall"] == 0.5
    assert any("TC-003" in item for item in payload["defect_analysis"]["missing_points"])
    assert any("TC-002" in item for item in payload["defect_analysis"]["hallucinations"])
    assert len(fake_client.calls) >= 3
    assert any("汇总" in call["system_prompt"] for call in fake_client.calls)


def test_chunked_compare_retries_model_error_and_emits_partial_progress(monkeypatch) -> None:
    class FlakyChunkClient:
        def __init__(self) -> None:
            self.chunk_attempts: dict[str, int] = {}

        def generate_response(self, prompt, system_prompt, **kwargs):
            if "汇总" in system_prompt:
                return json.dumps(
                    {
                        "metrics": {
                            "precision": 0.8,
                            "recall": 0.7,
                            "f1_score": 0.7467,
                            "semantic_similarity": 0.8,
                        },
                        "defect_analysis": {
                            "missing_points": ["TC-003 - 补充错题筛选"],
                            "hallucinations": [],
                            "modifications": [],
                        },
                        "summary": "汇总完成",
                    },
                    ensure_ascii=False,
                )

            payload = json.loads(prompt)
            chunk_index = str(payload["chunk_index"])
            self.chunk_attempts[chunk_index] = self.chunk_attempts.get(chunk_index, 0) + 1
            if chunk_index == "1" and self.chunk_attempts[chunk_index] == 1:
                return "Error: HTTP 504 - <html><body><h1>504 Gateway Time-out</h1></body></html>"
            return json.dumps(
                {
                    "chunk_index": payload["chunk_index"],
                    "metrics": {
                        "precision": 1.0,
                        "recall": 1.0,
                        "f1_score": 1.0,
                        "semantic_similarity": 1.0,
                    },
                    "defect_analysis": {
                        "missing_points": [],
                        "hallucinations": [],
                        "modifications": [],
                    },
                    "case_judgements": [],
                    "summary": f"分片 {chunk_index} 完成",
                },
                ensure_ascii=False,
            )

    monkeypatch.setenv("EVAL_LLM_COMPARE_MAX_CHARS", "300")
    monkeypatch.setenv("EVAL_LLM_COMPARE_SINGLE_PASS_MAX_CHARS", "0")
    monkeypatch.setenv("EVAL_LLM_COMPARE_CHUNK_CASES", "1")
    monkeypatch.setenv("EVAL_LLM_COMPARE_CHUNK_RETRIES", "1")
    fake_client = FlakyChunkClient()
    monkeypatch.setattr(evaluation_module, "get_client_for_user", lambda *args, **kwargs: fake_client)

    generated = json.dumps(
        {
            "test_cases": [
                {
                    "用例编号": "TC-001",
                    "模块": "登录",
                    "用例标题": "手机号验证码登录成功",
                    "测试步骤": "输入手机号，获取验证码，填写正确验证码并提交。" * 8,
                    "预期结果": "登录成功进入首页",
                },
                {
                    "用例编号": "TC-002",
                    "模块": "错题本",
                    "用例标题": "错题列表加载",
                    "测试步骤": "进入错题本页面并等待列表加载。" * 8,
                    "预期结果": "展示错题列表",
                },
            ]
        },
        ensure_ascii=False,
    )
    modified = "\n".join(
        [
            "用例编号,模块,用例标题,测试步骤,预期结果",
            "TC-001,登录,手机号验证码登录成功,输入手机号并提交正确验证码,登录成功进入首页",
            "TC-003,错题本,错题本按题型筛选,进入错题本选择题型筛选条件,仅展示对应题型错题",
        ]
    )
    progress_events: list[dict[str, object]] = []

    payload = json.loads(
        EvaluationModule().compare_test_cases(
            generated,
            modified,
            db=None,
            project_id=8,
            user_id=1,
            progress_callback=progress_events.append,
            comparison_id=99,
        )
    )

    assert payload["analysis_status"] == "completed"
    assert payload["comparison_id"] == 99
    assert fake_client.chunk_attempts["1"] == 2
    assert any((event.get("progress") or {}).get("phase") == "retrying" for event in progress_events)
    assert any(event.get("partial_chunk_results") for event in progress_events)
    partial_event = next(event for event in progress_events if event.get("partial_chunk_results"))
    assert partial_event["analysis_status"] == "running"
    assert partial_event["is_final_evaluation"] is False
    assert partial_event["comparison_id"] == 99
    assert (partial_event.get("progress") or {}).get("completed_chunks", 0) >= 1


def test_chunked_compare_continues_after_failed_chunk_and_returns_partial(monkeypatch) -> None:
    class PartiallyFailingClient:
        def generate_response(self, prompt, system_prompt, **kwargs):
            if "汇总" in system_prompt:
                return json.dumps(
                    {
                        "metrics": {
                            "precision": 0.6,
                            "recall": 0.5,
                            "f1_score": 0.5455,
                            "semantic_similarity": 0.6,
                        },
                        "defect_analysis": {
                            "missing_points": ["已完成分片中的遗漏"],
                            "hallucinations": [],
                            "modifications": [],
                        },
                        "summary": "只汇总已完成分片",
                    },
                    ensure_ascii=False,
                )

            payload = json.loads(prompt)
            if str(payload["chunk_index"]) == "1":
                return "Error: Empty response from model deepseek-v4-pro"
            return json.dumps(
                {
                    "chunk_index": payload["chunk_index"],
                    "metrics": {
                        "precision": 1.0,
                        "recall": 1.0,
                        "f1_score": 1.0,
                        "semantic_similarity": 1.0,
                    },
                    "defect_analysis": {
                        "missing_points": [],
                        "hallucinations": [],
                        "modifications": [],
                    },
                    "case_judgements": [],
                    "summary": "后续分片完成",
                },
                ensure_ascii=False,
            )

    monkeypatch.setenv("EVAL_LLM_COMPARE_MAX_CHARS", "300")
    monkeypatch.setenv("EVAL_LLM_COMPARE_SINGLE_PASS_MAX_CHARS", "0")
    monkeypatch.setenv("EVAL_LLM_COMPARE_CHUNK_CASES", "1")
    monkeypatch.setenv("EVAL_LLM_COMPARE_CHUNK_RETRIES", "0")
    monkeypatch.setattr(evaluation_module, "get_client_for_user", lambda *args, **kwargs: PartiallyFailingClient())

    generated = json.dumps(
        {
            "test_cases": [
                {
                    "用例编号": "TC-001",
                    "模块": "登录",
                    "用例标题": "手机号验证码登录成功",
                    "测试步骤": "输入手机号，获取验证码，填写正确验证码并提交。" * 8,
                    "预期结果": "登录成功进入首页",
                },
                {
                    "用例编号": "TC-002",
                    "模块": "错题本",
                    "用例标题": "错题列表加载",
                    "测试步骤": "进入错题本页面并等待列表加载。" * 8,
                    "预期结果": "展示错题列表",
                },
            ]
        },
        ensure_ascii=False,
    )
    modified = "\n".join(
        [
            "用例编号,模块,用例标题,测试步骤,预期结果",
            "TC-001,登录,手机号验证码登录成功,输入手机号并提交正确验证码,登录成功进入首页",
            "TC-003,错题本,错题本按题型筛选,进入错题本选择题型筛选条件,仅展示对应题型错题",
        ]
    )
    progress_events: list[dict[str, object]] = []

    payload = json.loads(
        EvaluationModule().compare_test_cases(
            generated,
            modified,
            db=None,
            project_id=8,
            user_id=1,
            progress_callback=progress_events.append,
            comparison_id=100,
        )
    )

    assert payload["analysis_status"] == "partial_completed"
    assert payload["is_final_evaluation"] is False
    assert payload["comparison_id"] == 100
    assert payload["chunk_summary"]["failed_chunk_count"] == 1
    assert payload["chunk_summary"]["successful_chunk_result_count"] >= 1
    assert payload["partial_chunk_results"]
    assert payload["progress"]["failed_chunks"] == 1
    assert any((event.get("progress") or {}).get("phase") == "chunk_failed_continuing" for event in progress_events)


def test_chunked_compare_fast_fails_repeated_empty_responses(monkeypatch) -> None:
    class EmptyAfterFirstClient:
        def __init__(self) -> None:
            self.chunk_calls: list[str] = []
            self.aggregate_calls = 0

        def generate_response(self, prompt, system_prompt, **kwargs):
            payload = json.loads(prompt)
            if "chunk_results" in payload:
                self.aggregate_calls += 1
                return json.dumps({"metrics": {}, "defect_analysis": {}, "summary": "should not aggregate"})

            chunk_index = str(payload["chunk_index"])
            self.chunk_calls.append(chunk_index)
            if chunk_index == "1":
                return json.dumps(
                    {
                        "chunk_index": payload["chunk_index"],
                        "metrics": {
                            "precision": 1.0,
                            "recall": 1.0,
                            "f1_score": 1.0,
                            "semantic_similarity": 1.0,
                        },
                        "defect_analysis": {
                            "missing_points": [],
                            "hallucinations": [],
                            "modifications": [],
                        },
                        "case_judgements": [],
                        "summary": "first chunk completed",
                    },
                    ensure_ascii=False,
                )
            return "Error: Empty response from model deepseek-v4-pro"

    monkeypatch.setenv("EVAL_LLM_COMPARE_MAX_CHARS", "1")
    monkeypatch.setenv("EVAL_LLM_COMPARE_SINGLE_PASS_MAX_CHARS", "0")
    monkeypatch.setenv("EVAL_LLM_COMPARE_CHUNK_GROUPS", "1")
    monkeypatch.setenv("EVAL_LLM_COMPARE_CHUNK_RETRIES", "3")
    monkeypatch.setenv("EVAL_LLM_COMPARE_SUB_CHUNK_RETRIES", "3")
    monkeypatch.setenv("EVAL_LLM_COMPARE_EMPTY_FAILURE_LIMIT", "3")
    fake_client = EmptyAfterFirstClient()
    monkeypatch.setattr(evaluation_module, "get_client_for_user", lambda *args, **kwargs: fake_client)

    generated_cases = [
        {
            "id": f"TC-{idx:03d}",
            "title": f"Generated case {idx}",
            "steps": f"Open page and validate generated flow {idx}",
            "expected_result": f"Generated result {idx}",
        }
        for idx in range(1, 7)
    ]
    modified_cases = [
        {
            "id": f"TC-{idx:03d}",
            "title": f"Modified case {idx}",
            "steps": f"Open page and validate modified flow {idx}",
            "expected_result": f"Modified result {idx}",
        }
        for idx in range(1, 7)
    ]
    progress_events: list[dict[str, object]] = []

    payload = json.loads(
        EvaluationModule().compare_test_cases(
            json.dumps(generated_cases),
            json.dumps(modified_cases),
            db=None,
            project_id=8,
            user_id=1,
            progress_callback=progress_events.append,
            comparison_id=101,
        )
    )

    assert payload["analysis_status"] == "partial_completed"
    assert payload["is_final_evaluation"] is False
    assert payload["partial_chunk_results"]
    assert payload["progress"]["failed_chunks"] == 3
    assert payload["progress"]["phase"] == "partial_completed"
    assert fake_client.chunk_calls == ["1", "2", "3", "4"]
    assert fake_client.aggregate_calls == 0
    assert not any((event.get("progress") or {}).get("phase") == "retrying" for event in progress_events)


def test_compare_model_504_falls_back_to_deterministic_json(monkeypatch) -> None:
    class GatewayTimeoutClient:
        def generate_response(self, *args, **kwargs):
            return "Error: HTTP 504 - <html><body><h1>504 Gateway Time-out</h1></body></html>"

    monkeypatch.setenv("EVAL_LLM_COMPARE_MAX_CHARS", "100000")
    monkeypatch.setattr(evaluation_module, "get_client_for_user", lambda *args, **kwargs: GatewayTimeoutClient())

    generated = json.dumps(
        [
            {
                "id": "TC-001",
                "module": "登录",
                "title": "手机号验证码登录成功",
                "steps": "输入手机号并提交正确验证码",
                "expected_result": "登录成功进入首页",
            }
        ],
        ensure_ascii=False,
    )
    modified = "\n".join(
        [
            "用例编号,模块,用例标题,测试步骤,预期结果",
            "TC-001,登录,手机号验证码登录成功,输入手机号并提交正确验证码,登录成功进入首页",
            "TC-002,登录,验证码错误提示,输入错误验证码并提交,提示验证码错误",
        ]
    )

    result = EvaluationModule().compare_test_cases(
        generated,
        modified,
        db=None,
        project_id=8,
        user_id=1,
    )
    payload = json.loads(result)

    assert payload["analysis_status"] == "model_failed"
    assert payload["analysis_mode"] == "model_required_but_failed"
    assert payload["is_final_evaluation"] is False
    assert payload["metrics"] == {}
    assert payload["defect_analysis"]["missing_points"] == []
    assert payload["local_preanalysis"]["input_stats"]["matched_case_count"] == 1
    assert any("TC-002" in item for item in payload["local_preanalysis"]["defect_analysis"]["missing_points"])
    assert "504" in payload["fallback_reason"]
    assert result.strip().startswith("{")


def test_parser_handles_partial_json_and_metadata_prefixed_csv() -> None:
    partial_json = """
[
  {"id": "TC-001", "description": "验证错题优先抽取", "test_module": "提问逻辑", "steps": ["进入页面"], "expected_result": "优先展示错题"},
  {"id": "TC-002", "description": "验证错题不足补正确题", "test_module": "提问逻辑", "steps": ["进入页面"], "expected_result": "补充正确题"},
  {"id": "TC-003", "description": "截断中的用例"
"""
    csv_text = "\n".join(
        [
            "相关文档,讲错题接入AI 6步督学系统,测试设备",
            ",测试版本,",
            "用例标题,测试模块,执行步骤,前置条件,测试输入,预期结果,用例级别",
            "讲错题内容展示,入口,进入第五步讲错题页面,已登录,存在错题,展示讲错题内容,P1",
            "录音按钮展示,讲错题-录音,点击录音按钮,已进入页面,录入语音,展示提交和取消按钮,P0",
        ]
    )

    generated_cases = _parse_test_cases_payload(partial_json)
    modified_cases = _parse_test_cases_payload(csv_text)

    assert len(generated_cases) == 2
    assert generated_cases[0]["id"] == "TC-001"
    assert generated_cases[1]["description"] == "验证错题不足补正确题"
    assert len(modified_cases) == 2
    assert modified_cases[0]["description"] == "讲错题内容展示"
    assert modified_cases[0]["test_module"] == "入口"


def test_parser_handles_html_table_from_excel_preview() -> None:
    html_text = """
<table>
  <tr><td>相关文档</td><td>讲错题方案</td></tr>
  <tr><td>用例标题</td><td>测试模块</td><td>执行步骤</td><td>预期结果</td></tr>
  <tr><td>录音按钮展示</td><td>讲错题-录音</td><td>点击录音按钮</td><td>展示提交和取消按钮</td></tr>
</table>
"""

    cases = _parse_test_cases_payload(html_text)

    assert len(cases) == 1
    assert cases[0]["description"] == "录音按钮展示"
    assert cases[0]["test_module"] == "讲错题-录音"
    assert cases[0]["expected_result"] == "展示提交和取消按钮"
