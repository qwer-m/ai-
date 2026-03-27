import json
from dataclasses import dataclass

import pytest

import modules.testing.test_generation_components.legacy_generation_impl as legacy_mod
import modules.testing.test_generation_components.legacy.json_generation as json_generation_mod
import modules.testing.test_generation_components.legacy.stream.prepare as stream_prepare_mod
import modules.testing.test_generation_components.legacy.context.hybrid as hybrid_mod
from modules.testing.test_generation_components.legacy_generation_impl import TestGenerationModule


@dataclass
class _DummyGuardConfig:
    """测试用兜底配置对象，用于稳定控制策略分支。"""

    strategy: str = "fail_fast"
    sync_snapshot_retry_enabled: bool = True
    sync_snapshot_retry_timeout_sec: int = 3

    def normalized_strategy(self) -> str:
        return self.strategy


class _FakeClient:
    """模型客户端桩：仅记录是否被调用，不做真实外部请求。"""

    def __init__(self) -> None:
        self.generate_calls = 0
        self.stream_calls = 0

    def generate_response(self, *args, **kwargs):
        self.generate_calls += 1
        # 中文注释：返回最小可解析 JSON，便于生成链路继续。
        return json.dumps(
            [
                {
                    "id": "TC-001",
                    "description": "测试",
                    "test_module": "模块",
                    "preconditions": [],
                    "steps": ["步骤1"],
                    "test_input": "输入",
                    "expected_result": "期望",
                    "priority": "P0",
                }
            ],
            ensure_ascii=False,
        )

    def generate_response_stream(self, *args, **kwargs):
        self.stream_calls += 1
        yield self.generate_response(*args, **kwargs)

    def compress_context(self, context: str, *args, **kwargs):
        return context


@pytest.fixture()
def tg_env(monkeypatch):
    """统一测试夹具：注入 fake client 和轻量 meta 分析。"""
    fake_client = _FakeClient()

    # 中文注释：拦截 JSON/流式两条链路的 client 获取，确保任何场景都不会打到真实模型。
    monkeypatch.setattr(json_generation_mod, "get_client_for_user", lambda user_id, db: fake_client)
    monkeypatch.setattr(stream_prepare_mod, "get_client_for_user", lambda user_id, db: fake_client)
    # 中文注释：兼容旧测试桩写法，保留门面模块同名符号。
    monkeypatch.setattr(legacy_mod, "get_client_for_user", lambda user_id, db: fake_client, raising=False)

    # 中文注释：避免分析阶段引入额外模型调用，聚焦验证“双空保护”。
    monkeypatch.setattr(
        TestGenerationModule,
        "analyze_requirement_context",
        lambda self, requirement, kb_context, client, db: {
            "system_type": "Web",
            "complexity": "Medium",
            "suggested_ratios": {"functional": 0.6, "regression": 0.2, "non_functional": 0.2},
            "focus_areas": ["核心流程"],
            "device_scenarios": ["web"],
            "impact_scope": "single_module",
        },
    )

    module = TestGenerationModule()
    return module, fake_client


def _patch_kb(monkeypatch, snapshot_result, rag_result):
    """注入 snapshot 与 RAG 结果桩。"""
    monkeypatch.setattr(
        hybrid_mod.knowledge_base,
        "get_or_build_context_snapshot",
        lambda **kwargs: snapshot_result,
    )
    monkeypatch.setattr(
        hybrid_mod.knowledge_base,
        "get_relevant_context",
        lambda **kwargs: rag_result,
    )


@pytest.mark.parametrize("decision", ["fail_fast"])
def test_scene1_force_double_empty_abort(monkeypatch, tg_env, capsys, decision):
    """场景1：强制双空，必须立即终止且不调用模型。"""
    module, fake_client = tg_env

    monkeypatch.setattr(
        hybrid_mod,
        "HYBRID_EMPTY_GUARD_CONFIG",
        _DummyGuardConfig(strategy=decision, sync_snapshot_retry_enabled=False),
    )
    _patch_kb(
        monkeypatch,
        snapshot_result={
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_skip",
            "queue_result": {"queued": False, "reason": "already_pending"},
            "snapshot_status": "pending",
        },
        rag_result={
            "debug": {
                "lane_counts": {"original_raw": 0},
                "lane_reasons": {"original_raw": "no_hit"},
                "final_chunks": [],
            }
        },
    )

    monkeypatch.setattr(
        hybrid_mod,
        "build_hybrid_context",
        lambda **kwargs: {
            "context": "",
            "debug": {
                "snapshot_used": False,
                "rag_used": False,
                "fusion_mode": "empty",
                "rag_chunk_count": 0,
                "final_context_tokens": 0,
            },
        },
    )

    result = module.generate_test_cases_json(
        requirement="Redis 为什么这么快",
        project_id=61,
        db=object(),
        user_id=29,
        compress=False,
    )

    assert result["error"] == "HYBRID_EMPTY_CONTEXT_ABORT"
    debug = result["fusion_debug"]
    assert debug["hybrid_empty_context"] is True
    assert debug["final_decision"] == "fail_fast"
    assert debug["snapshot_queue_reason"] == "already_pending"
    # 中文注释：双空必须完全阻断模型调用。
    assert fake_client.generate_calls == 0

    out = capsys.readouterr().out
    assert "Hybrid guard abort(json)" in out
    assert "snapshot_queue_reason=already_pending" in out


def test_scene2_double_empty_then_sync_retry_success(monkeypatch, tg_env):
    """场景2：双空后同步补救成功，应继续生成并允许调用模型。"""
    module, fake_client = tg_env

    monkeypatch.setattr(
        hybrid_mod,
        "HYBRID_EMPTY_GUARD_CONFIG",
        _DummyGuardConfig(strategy="sync_snapshot_retry_then_fail", sync_snapshot_retry_enabled=True),
    )
    _patch_kb(
        monkeypatch,
        snapshot_result={
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_skip",
            "queue_result": {"queued": False, "reason": "already_pending"},
            "snapshot_status": "pending",
        },
        rag_result={
            "debug": {
                "lane_counts": {"original_raw": 0},
                "lane_reasons": {"original_raw": "no_hit"},
                "final_chunks": [],
            }
        },
    )

    def _hybrid_build(**kwargs):
        # 中文注释：首次空；补救后 snapshot 有值则返回非空上下文。
        if (kwargs.get("snapshot_text") or "").strip():
            return {
                "context": "【项目知识背景】补救后的 snapshot",
                "debug": {
                    "snapshot_used": True,
                    "rag_used": False,
                    "fusion_mode": "snapshot_only",
                    "rag_chunk_count": 0,
                    "final_context_tokens": 24,
                },
            }
        return {
            "context": "",
            "debug": {
                "snapshot_used": False,
                "rag_used": False,
                "fusion_mode": "empty",
                "rag_chunk_count": 0,
                "final_context_tokens": 0,
            },
        }

    monkeypatch.setattr(hybrid_mod, "build_hybrid_context", _hybrid_build)
    monkeypatch.setattr(
        TestGenerationModule,
        "_try_sync_snapshot_retry_once",
        lambda self, project_id, user_id, timeout_sec: {
            "success": True,
            "result": {"success": True, "snapshot_text": "补救快照内容"},
            "error": "",
        },
    )

    # 先直接验证融合编排 debug 字段。
    ctx = module._resolve_kb_context_with_hybrid(
        requirement="下家能看到自己的账号余额吗",
        project_id=61,
        db=object(),
        user_id=29,
        precision_mode=True,
    )
    debug = ctx["fusion_debug"]
    assert debug["sync_snapshot_retry_attempted"] is True
    assert debug["sync_snapshot_retry_success"] is True
    assert debug["final_decision"] == "retry_snapshot_then_proceed"
    assert ctx["abort_generation"] is False
    assert (ctx["kb_context"] or "").strip()

    # 再验证进入生成流程，模型允许被调用。
    result = module.generate_test_cases_json(
        requirement="下家能看到自己的账号余额吗",
        project_id=61,
        db=object(),
        user_id=29,
        compress=False,
    )
    assert isinstance(result, list)
    assert fake_client.generate_calls > 0


def test_scene3_double_empty_and_retry_failed(monkeypatch, tg_env):
    """场景3：双空且补救失败，只能一次重试并返回终止错误。"""
    module, fake_client = tg_env

    monkeypatch.setattr(
        hybrid_mod,
        "HYBRID_EMPTY_GUARD_CONFIG",
        _DummyGuardConfig(strategy="sync_snapshot_retry_then_fail", sync_snapshot_retry_enabled=True, sync_snapshot_retry_timeout_sec=3),
    )
    _patch_kb(
        monkeypatch,
        snapshot_result={
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_skip",
            "queue_result": {"queued": False, "reason": "enqueue_failed", "error": "redis down"},
            "snapshot_status": "failed",
        },
        rag_result={
            "debug": {
                "lane_counts": {"original_raw": 0, "original_summary": 0},
                "lane_reasons": {"original_raw": "embedding_failed", "original_summary": "no_hit"},
                "final_chunks": [],
                "final_failure_reason": "embedding_failed",
            }
        },
    )

    monkeypatch.setattr(
        hybrid_mod,
        "build_hybrid_context",
        lambda **kwargs: {
            "context": "",
            "debug": {
                "snapshot_used": False,
                "rag_used": False,
                "fusion_mode": "empty",
                "rag_chunk_count": 0,
                "final_context_tokens": 0,
            },
        },
    )

    retry_counter = {"n": 0}

    def _retry_fail(self, project_id, user_id, timeout_sec):
        retry_counter["n"] += 1
        return {"success": False, "result": None, "error": f"sync_snapshot_retry_timeout:{timeout_sec}s"}

    monkeypatch.setattr(TestGenerationModule, "_try_sync_snapshot_retry_once", _retry_fail)

    result = module.generate_test_cases_json(
        requirement="为什么检索有时候查不到",
        project_id=61,
        db=object(),
        user_id=29,
        compress=False,
    )

    assert retry_counter["n"] == 1
    assert result["error"] == "HYBRID_EMPTY_CONTEXT_ABORT"
    debug = result["fusion_debug"]
    assert debug["sync_snapshot_retry_attempted"] is True
    assert debug["sync_snapshot_retry_success"] is False
    assert "sync_snapshot_retry_timeout" in debug["sync_snapshot_retry_error"]
    assert debug["final_decision"] == "degraded_to_error"
    assert fake_client.generate_calls == 0


def test_scene4_out_of_kb_question_fail_fast(monkeypatch, tg_env):
    """场景4：知识库外问题，no_hit/低相关后应终止，不得生成。"""
    module, fake_client = tg_env

    monkeypatch.setattr(
        hybrid_mod,
        "HYBRID_EMPTY_GUARD_CONFIG",
        _DummyGuardConfig(strategy="fail_fast", sync_snapshot_retry_enabled=False),
    )
    _patch_kb(
        monkeypatch,
        snapshot_result={
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_skip",
            "queue_result": {"queued": False, "reason": "already_pending"},
            "snapshot_status": "pending",
        },
        rag_result={
            "debug": {
                "lane_counts": {"original_raw": 0, "rewrite_raw": 0},
                "lane_reasons": {"original_raw": "no_hit", "rewrite_raw": "no_hit"},
                "final_chunks": [],
                "final_failure_reason": "low_relevance_filtered",
            }
        },
    )
    monkeypatch.setattr(
        hybrid_mod,
        "build_hybrid_context",
        lambda **kwargs: {
            "context": "",
            "debug": {
                "snapshot_used": False,
                "rag_used": False,
                "fusion_mode": "empty",
                "rag_chunk_count": 0,
                "final_context_tokens": 0,
            },
        },
    )

    result = module.generate_test_cases_json(
        requirement="Kubernetes 调度策略是什么",
        project_id=61,
        db=object(),
        user_id=29,
        compress=False,
    )

    assert result["error"] == "HYBRID_EMPTY_CONTEXT_ABORT"
    debug = result["fusion_debug"]
    assert debug["hybrid_empty_context"] is True
    assert debug["final_decision"] == "fail_fast"
    assert debug["lane_reasons"]["original_raw"] == "no_hit"
    assert debug["hybrid_empty_reason"] in {"low_relevance_filtered", "snapshot_and_rag_both_empty", "no_hit"}
    assert fake_client.generate_calls == 0


def test_scene5_snapshot_failed_requirement_only_fallback(monkeypatch, tg_env):
    """场景5：snapshot failed + RAG 空时，不中断，降级为仅用当前文档继续生成。"""
    module, fake_client = tg_env

    monkeypatch.setattr(
        hybrid_mod,
        "HYBRID_EMPTY_GUARD_CONFIG",
        _DummyGuardConfig(strategy="requirement_only_fallback", sync_snapshot_retry_enabled=False),
    )
    _patch_kb(
        monkeypatch,
        snapshot_result={
            "success": False,
            "fallback_reason": "snapshot_async_rebuild_skip",
            "queue_result": {"queued": True, "reason": "queued", "task_id": "task-2"},
            "snapshot_status": "failed",
        },
        rag_result={
            "debug": {
                "lane_counts": {"original_raw": 0},
                "lane_reasons": {"original_raw": "no_hit"},
                "final_chunks": [],
                "final_failure_reason": "no_hit",
            }
        },
    )
    monkeypatch.setattr(
        hybrid_mod,
        "build_hybrid_context",
        lambda **kwargs: {
            "context": "",
            "debug": {
                "snapshot_used": False,
                "rag_used": False,
                "fusion_mode": "empty",
                "rag_chunk_count": 0,
                "final_context_tokens": 0,
            },
        },
    )

    ctx = module._resolve_kb_context_with_hybrid(
        requirement="上传文档后立即生成测试用例",
        project_id=61,
        db=object(),
        user_id=29,
        precision_mode=True,
    )
    debug = ctx["fusion_debug"]
    assert ctx["abort_generation"] is False
    assert debug["final_decision"] == "requirement_only_fallback"
    assert debug["current_document_used"] is True
    assert debug["snapshot_rebuild_triggered"] is True

    result = module.generate_test_cases_json(
        requirement="上传文档后立即生成测试用例",
        project_id=61,
        db=object(),
        user_id=29,
        compress=False,
    )
    assert isinstance(result, list)
    assert fake_client.generate_calls > 0
