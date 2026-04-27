import modules.testing.test_generation_components.legacy.stream.batches as batches_mod
from modules.testing.test_generation_components.legacy_generation_impl import TestGenerationModule


class _FakeClient:
    """中文注释：最小模型桩，避免触发真实模型调用。"""

    def generate_response(self, *args, **kwargs):
        # 中文注释：返回数组字符串，触发“非 dict 元分析结果”分支。
        return "[]"

    def generate_response_stream(self, *args, **kwargs):
        yield "[]"


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
