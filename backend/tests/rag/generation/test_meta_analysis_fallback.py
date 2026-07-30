import json

import modules.testing.test_generation_components.legacy.stream.batches as batches_mod
from modules.testing.test_generation_components.legacy_generation_impl import TestGenerationModule


class _FakeClient:
    """中文注释：最小模型桩，避免触发真实模型调用。"""

    def generate_response(self, *args, **kwargs):
        # 中文注释：返回数组字符串，触发“非 dict 元分析结果”分支。
        return "[]"

    def generate_response_stream(self, *args, **kwargs):
        yield "[]"


class _DuplicateBatchClient:
    """中文注释：固定返回同一条用例，验证局部低增益不会截断全局批次。"""

    def generate_response(self, *args, **kwargs):
        return "[]"

    def generate_response_stream(self, *args, **kwargs):
        yield (
            '[{"id":"TC-001","description":"重复场景","test_module":"模块A",'
            '"preconditions":["authenticated user exists"],"steps":["step1"],"test_input":"input",'
            '"expected_result":"the saved state is visible after reopening","priority":"P2"}]'
        )


class _PromptCaptureClient:
    def __init__(self) -> None:
        self.stream_prompts: list[str] = []

    def generate_response(self, *args, **kwargs):
        return "[]"

    def generate_response_stream(self, requirement: str, prompt: str, **kwargs):  # noqa: ANN001, ARG002
        self.stream_prompts.append(str(prompt or ""))
        yield (
            '[{"id":"TC-001","description":"验证二轮复习模块顺序","test_module":"首页",'
            '"preconditions":["用户已登录"],"steps":["打开首页"],"test_input":"无",'
            '"expected_result":"二轮复习模块位于查漏补缺与真题套卷之间","priority":"P1"}]'
        )


class _ParallelShardClient:
    def __init__(self, case_id: str, description: str) -> None:
        self.case_id = case_id
        self.description = description
        self.last_response_metadata = {
            "model": "parallel-test-model",
            "input_tokens": 10,
            "output_tokens": 5,
        }

    def generate_response_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
        cases = [
            {
                "id": f"{self.case_id}-{index:02d}",
                "description": f"{self.description} scenario {index}",
                "test_module": "Forum",
                "preconditions": ["authenticated user exists"],
                "steps": ["open", f"submit scenario {index}"],
                "test_input": f"valid data {index}",
                "expected_result": f"scenario {index} state is updated",
                "priority": "P1",
            }
            for index in range(1, 14)
        ]
        yield json.dumps(cases, ensure_ascii=False)


class _ParallelContractClient:
    def __init__(self, *, public_module: str, semantic_module: str, description: str) -> None:
        self.public_module = public_module
        self.semantic_module = semantic_module
        self.description = description
        self.last_response_metadata = {"model": "parallel-contract-model"}
        self.stream_call_count = 0

    def generate_response_stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.stream_call_count += 1
        module_key = self.semantic_module.lower()
        yield json.dumps(
            [
                {
                    "id": "raw-case",
                    "description": self.description,
                    "test_module": self.public_module,
                    "preconditions": ["authenticated user exists"],
                    "steps": [self.description],
                    "test_input": "valid data",
                    "expected_result": "state is updated",
                    "priority": "P1",
                    "_semantic": {
                        "module_candidates": [
                            {
                                "module_key": module_key,
                                "module_name": self.semantic_module,
                                "role": "primary",
                                "confidence": 0.9,
                                "evidence": [self.description],
                            }
                        ],
                        "interaction_ids": [],
                        "workflow_stage_candidates": [],
                        "precondition_states": [],
                        "produced_states": [],
                    },
                }
            ],
            ensure_ascii=False,
        )


def _enable_parallel_shards(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(batches_mod.settings, "GENERATION_STREAM_COVERAGE_SHARDS_ENABLED", True, raising=False)
    monkeypatch.setattr(batches_mod.settings, "GENERATION_STREAM_COVERAGE_SHARD_MAX_WORKERS", 2, raising=False)
    monkeypatch.setattr(batches_mod.settings, "GENERATION_STREAM_COVERAGE_SHARD_MIN_EXPECTED_COUNT", 1, raising=False)
    monkeypatch.setattr(batches_mod.settings, "GENERATION_STREAM_COVERAGE_SHARD_MIN_RULES", 2, raising=False)
    monkeypatch.setattr(batches_mod.settings, "GENERATION_STREAM_COVERAGE_SHARD_DUPLICATE_RATE_ABORT", 1.0, raising=False)
    monkeypatch.setattr(batches_mod.settings, "GENERATION_STREAM_COVERAGE_SHARD_MIN_UNIQUE_RATIO", 0.01, raising=False)


def _patch_two_rule_coverage_plan(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        batches_mod,
        "_build_stream_coverage_plan_lite",
        lambda requirement, analyze_coverage_fn: (
            "PLAN-LITE",
            [
                {"rule_id": "RULE-001", "rule_text": "forum post can be created"},
                {"rule_id": "RULE-002", "rule_text": "forum reply can be created"},
            ],
        ),
    )


def test_meta_analysis_returns_default_when_non_dict():
    module = TestGenerationModule()
    client = _FakeClient()
    plan = module.analyze_requirement_context("需求A", "上下文B", client, db=None)
    assert isinstance(plan, dict)
    assert plan.get("system_type")
    assert isinstance(plan.get("suggested_ratios"), dict)
    assert isinstance(plan.get("focus_areas"), list)


def test_stream_batches_guard_against_none_plan(monkeypatch):
    module = TestGenerationModule()
    client = _FakeClient()

    # 中文注释：强制元分析返回 None，验证 stream 阶段仍不会因 .get 崩溃。
    monkeypatch.setattr(
        TestGenerationModule,
        "analyze_requirement_context",
        lambda self, requirement, kb_context, client, db: None,
    )

    state = {
        "client": client,
        "requirement": "需求A",
        "project_id": 1,
        "db": None,
        "doc_type": "requirement",
        "expected_count": 1,
        "batch_size": 1,
        "append": False,
        "user_id": 1,
        "request_id": "r-1",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "context_result": {},
        "gate_debug": {},
    }

    chunks = list(module._stream_run_batches_phase(state=state))
    assert any("Meta-Analysis" in c for c in chunks)
    assert any("分析完成" in c for c in chunks)

    # 中文注释：恢复被 monkeypatch 的符号，避免 lint 报未使用导入。
    assert batches_mod is not None


def test_stream_batches_keep_global_plan_after_local_low_incremental_gain() -> None:
    module = TestGenerationModule()
    client = _DuplicateBatchClient()
    state = {
        "client": client,
        "requirement": "需求A",
        "project_id": 1,
        "db": None,
        "doc_type": "requirement",
        "expected_count": 80,
        "batch_size": 25,
        "append": False,
        "user_id": 1,
        "request_id": "r-2",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "context_result": {},
        "gate_debug": {},
    }

    gen = module._stream_run_batches_phase(state=state)
    final_state = None
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as stop:
            final_state = stop.value
            break

    assert isinstance(final_state, dict)
    assert final_state.get("stream_early_stop_triggered") is False
    assert final_state.get("stream_early_stop_reason") == ""
    metrics = [row for row in (final_state.get("stream_batch_quality_metrics") or []) if isinstance(row, dict)]
    assert len(metrics) >= 2
    assert metrics[0].get("low_gain_detected") is True
    assert metrics[1].get("low_gain_detected") is True
    assert any("GEN_DIAG:" in str(chunk) and "stream_batch_quality" in str(chunk) for chunk in chunks)
    assert any("global_batches_continue" in str(chunk) for chunk in chunks)


def test_stream_batches_injects_coverage_plan_lite() -> None:
    module = TestGenerationModule()
    client = _PromptCaptureClient()
    state = {
        "client": client,
        "requirement": "新增二轮复习模块，插入至查漏补缺与真题套卷之间；打印功能保留教材和答案双选项。",
        "project_id": 1,
        "db": None,
        "doc_type": "requirement",
        "expected_count": 1,
        "batch_size": 1,
        "append": False,
        "user_id": 1,
        "request_id": "r-3",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "context_result": {},
        "gate_debug": {},
    }

    gen = module._stream_run_batches_phase(state=state)
    final_state = None
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            final_state = stop.value
            break

    assert isinstance(final_state, dict)
    assert int(final_state.get("stream_coverage_plan_rule_count") or 0) >= 1
    assert client.stream_prompts
    prompt = client.stream_prompts[-1]
    assert "COVERAGE PLAN-LITE" in prompt
    assert "VERY FIRST" not in prompt
    assert "UI/display independent suite" in prompt
    assert "permission/security -> exception/recovery -> boundary/state rollback" in prompt
    assert "新增二轮复习模块" in prompt
    assert "打印功能保留教材和答案双选项" in prompt


def test_stream_batches_accepts_parallel_coverage_shards(monkeypatch) -> None:
    _enable_parallel_shards(monkeypatch)
    _patch_two_rule_coverage_plan(monkeypatch)
    module = TestGenerationModule()
    client = _FakeClient()
    shard_clients = [
        _ParallelShardClient("S1-001", "create forum post"),
        _ParallelShardClient("S2-001", "create forum reply"),
        _ParallelShardClient("S3-001", "create final public batch case"),
    ]

    def client_factory(shard):  # noqa: ANN001
        return shard_clients[int(shard["shard_index"]) - 1]

    state = {
        "client": client,
        "requirement": "Forum optimization requirement",
        "project_id": 1,
        "db": None,
        "doc_type": "requirement",
        "expected_count": 26,
        "batch_size": 25,
        "append": False,
        "user_id": 1,
        "request_id": "parallel-accepted",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "context_result": {},
        "gate_debug": {},
        "parallel_shard_client_factory": client_factory,
    }

    gen = module._stream_run_batches_phase(state=state)
    chunks: list[str] = []
    final_state = None
    while True:
        try:
            chunks.append(next(gen))
        except StopIteration as stop:
            final_state = stop.value
            break

    assert isinstance(final_state, dict)
    assert final_state.get("stream_parallel_shards_used") is True
    assert final_state.get("stream_parallel_shard_result", {}).get("status") == "accepted"
    assert final_state.get("stream_parallel_shard_result", {}).get(
        "public_batch_targets"
    ) == [25, 1]
    assert final_state.get("stream_batch_acceptance_summaries", [])[0].get("source") == "parallel_shards"
    assert final_state.get("stream_batch_acceptance_summaries", [])[0].get("accepted_count") == 26
    assert "TC-001" in final_state.get("full_content", "")
    assert "TC-002" in final_state.get("full_content", "")
    assert any("parallel_coverage_shard_result" in str(chunk) for chunk in chunks)


def test_parallel_module_conflict_keeps_successful_shard_and_repairs_only_gap(monkeypatch) -> None:
    _enable_parallel_shards(monkeypatch)
    _patch_two_rule_coverage_plan(monkeypatch)
    profile = {
        "functional_architecture": {
            "functional_modules": [
                {
                    "module_key": "forum",
                    "module_name": "Forum",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                },
                {
                    "module_key": "other",
                    "module_name": "Other",
                    "scope_status": "in_scope",
                    "evidence_verified": True,
                },
            ],
            "module_interactions": [],
        }
    }
    monkeypatch.setattr(
        batches_mod,
        "build_structured_prompt_context",
        lambda **kwargs: {
            "requirement_context": kwargs.get("requirement") or "",
            "requirement_semantics_context": "",
            "testcase_context": "",
            "supplement_context": "",
            "control_context": "",
            "current_biz_key": "forum",
            "project_profile": profile,
            "feedback_control_state": {},
        },
    )
    shard_clients = [
        _ParallelContractClient(
            public_module="Forum",
            semantic_module="Forum",
            description="create forum post",
        ),
        _ParallelContractClient(
            public_module="Other",
            semantic_module="Forum",
            description="create forum reply",
        ),
    ]

    state = {
        "client": _FakeClient(),
        "requirement": "Forum optimization requirement",
        "project_id": 1,
        "db": None,
        "doc_type": "requirement",
        "expected_count": 25,
        "batch_size": 25,
        "append": False,
        "user_id": 1,
        "request_id": "parallel-contract-fallback",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "context_result": {},
        "gate_debug": {},
        "parallel_shard_client_factory": lambda shard: shard_clients[
            int(shard["shard_index"]) - 1
        ],
    }

    gen = TestGenerationModule()._stream_run_batches_phase(state=state)
    final_state = None
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            final_state = stop.value
            break

    assert isinstance(final_state, dict)
    result = final_state["stream_parallel_shard_result"]
    assert final_state["stream_parallel_shards_used"] is True
    assert result["status"] == "partial"
    assert result["fallback_reason"] == ""
    assert result["accepted_case_count"] == 1
    assert result["repair_shard_count"] == 2
    assert result["shard_results"][0]["accepted_case_count"] >= 1
    assert "functional_module_contract_rejected" in result["shard_results"][1]["error_codes"]
    assert result["shard_results"][0]["raw_response_chars"] > 0
    assert result["shard_results"][0]["raw_parsed_case_count"] == 2
    assert result["shard_results"][0]["normalized_case_count"] == 2
    assert result["shard_results"][0]["semantic_rejection_count"] == 0
    assert [client.stream_call_count for client in shard_clients] == [2, 2]


def test_stream_batches_reports_failed_shards_without_full_batch_fallback(monkeypatch) -> None:
    _enable_parallel_shards(monkeypatch)
    _patch_two_rule_coverage_plan(monkeypatch)
    module = TestGenerationModule()
    client = _PromptCaptureClient()
    state = {
        "client": client,
        "requirement": "Forum optimization requirement",
        "project_id": 1,
        "db": None,
        "doc_type": "requirement",
        "expected_count": 26,
        "batch_size": 25,
        "append": False,
        "user_id": 1,
        "request_id": "parallel-fallback",
        "kb_context": "",
        "start_id": 1,
        "existing_cases": [],
        "context_result": {},
        "gate_debug": {},
    }

    gen = module._stream_run_batches_phase(state=state)
    final_state = None
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            final_state = stop.value
            break

    assert isinstance(final_state, dict)
    assert final_state.get("stream_parallel_shards_enabled") is True
    assert final_state.get("stream_parallel_shards_used") is True
    assert final_state.get("stream_parallel_shard_result", {}).get("status") == "failed"
    assert final_state.get("stream_parallel_shard_result", {}).get("fallback_reason") == ""
    assert all(
        "client_unavailable" in item.get("error_codes", [])
        for item in final_state.get("stream_parallel_shard_result", {}).get("shard_results", [])
    )
    assert client.stream_prompts == []
