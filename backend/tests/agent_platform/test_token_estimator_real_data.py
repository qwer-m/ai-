"""真实 Run65 冻结协议/来源的 CPU 计量；不伪造历史 SDK 输入或新模型回执。"""

import asyncio
from copy import deepcopy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

from agents import Agent, ModelSettings, RunContextWrapper
from agents.usage import Usage
import pytest
import tiktoken

from modules.agent_platform.autonomous_budget import (
    AutonomousBudgetHooks, model_request_context_bytes, model_request_input_estimate,
    model_request_input_reservation,
)
from modules.agent_platform.token_counting import (
    input_protocol_reservation, input_token_estimate, json_token_text, token_encoding_profile,
)


@pytest.fixture(scope="module")
def real_data():
    folder = Path(__file__).parent / "fixtures"
    protocol = json.loads(gzip.decompress((folder / "run65_token_estimator_real.json.gz").read_bytes()))
    source = json.loads(gzip.decompress((folder / "run65_quote_layout_real.json.gz").read_bytes()))
    return protocol, source["source_state_projection"]["evidence_catalog"]


@pytest.fixture
def declared_alias(monkeypatch, real_data):
    protocol, _ = real_data
    alias = protocol["agent"]["model_routes"]["main"]["model"]
    monkeypatch.setenv("AGENT_TOKEN_ENCODING_MAP", json.dumps({alias: "o200k_base"}))
    return alias


def projected_agent_input(real_data, model):
    protocol, source = real_data
    agent = Agent(name=protocol["agent"]["name"], model=model,
                  instructions=protocol["agent"]["instructions"],
                  model_settings=ModelSettings(max_tokens=protocol["agent"]["runtime_config"]["max_output_tokens"]))
    # 内容来自已保存来源；该组装只用于新计量器检验，不声称是历史请求原文。
    items = [{"role": "user", "content": json.dumps(source, ensure_ascii=False)}]
    return agent, items


def test_actual_chinese_source_and_frozen_protocol_use_bpe_with_declared_margin(real_data, declared_alias):
    protocol, source = real_data
    view = {"instructions": protocol["agent"]["instructions"], "tools": protocol["tools"], "source": source}
    estimate = input_token_estimate(view, declared_alias)
    expected = len(tiktoken.get_encoding("o200k_base").encode(json_token_text(view), disallowed_special=()))
    assert estimate["text_tokens"] == expected
    assert estimate["protocol_tokens"] == input_protocol_reservation(text_tokens=expected)
    assert estimate["reserved_input_tokens"] == expected + estimate["protocol_tokens"]
    assert estimate["reserved_input_tokens"] * 2 < len(json_token_text(view).encode("utf-8")) + 8192
    assert estimate["mapping_source"] == "explicit_alias_estimate"
    assert estimate["accuracy"] == "local_estimate_not_reported_usage"
    assert protocol["provenance"]["original_sdk_inputs_saved"] is False


def test_sdk_agent_model_object_and_public_reservation_share_one_estimate(real_data, declared_alias):
    agent, items = projected_agent_input(real_data, declared_alias)
    estimate = model_request_input_estimate(agent=agent, system_prompt=agent.instructions, input_items=items)
    assert model_request_input_reservation(agent=agent, system_prompt=agent.instructions, input_items=items) == estimate["reserved_input_tokens"]
    assert model_request_context_bytes(agent=agent, system_prompt=agent.instructions, input_items=items) > estimate["reserved_input_tokens"]
    assert token_encoding_profile(SimpleNamespace(model=declared_alias))["model"] == declared_alias


def test_unknown_alias_cannot_silently_use_default_encoding(monkeypatch, real_data):
    alias = real_data[0]["agent"]["model_routes"]["main"]["model"]
    monkeypatch.delenv("AGENT_TOKEN_ENCODING_MAP", raising=False)
    with pytest.raises(ValueError, match="显式声明近似编码"):
        token_encoding_profile(alias)
    with pytest.raises(ValueError, match="缺少实际模型名"):
        token_encoding_profile(None)
    assert token_encoding_profile("gpt-4o")["mapping_source"] == "tiktoken_model_map"


def test_actual_action_catalog_wrapper_keeps_model_identity_for_budget(real_data, declared_alias):
    from agents import OpenAIChatCompletionsModel
    from modules.agent_platform.sdk_adapter import _ActionCatalogModel

    # 只构建实际 SDK 对象做 CPU 计量，不发 HTTP 请求或制造模型回执。
    underlying = OpenAIChatCompletionsModel(model=declared_alias, openai_client=None)
    wrapped = _ActionCatalogModel(underlying, None)
    agent, items = projected_agent_input(real_data, wrapped)
    estimate = model_request_input_estimate(agent=agent, system_prompt=agent.instructions, input_items=items)
    assert estimate["model"] == declared_alias
    assert wrapped.model == underlying.model


@pytest.mark.parametrize("scoped", [False, True])
def test_missing_stream_usage_keeps_real_request_reservation_instead_of_sdk_zero(real_data, declared_alias, scoped):
    async def check():
        protocol, _ = real_data
        parent = AutonomousBudgetHooks(protocol["budget"]["limits"])
        hooks = parent.scoped_requests(2) if scoped else parent
        agent, items = projected_agent_input(real_data, declared_alias)
        context = RunContextWrapper(context=SimpleNamespace(run_id=protocol["provenance"]["run_id"]))
        await hooks.on_llm_start(context, agent, agent.instructions, items)
        reserved = parent.snapshot()["charged"]
        # SDK缺少供应商末块时会构造零Usage；此控制元数据不是实际零用量回执。
        await hooks.on_llm_end(context, agent, SimpleNamespace(usage=Usage(requests=1), usage_reported=False))
        saved = parent.snapshot()
        assert saved["charged"] == reserved
        assert saved["reported_usage"]["requests"] == saved["reported_usage"]["total_tokens"] == 0
        assert saved["pending_requests"] == 0
        assert saved["completed_requests"] == 1
    asyncio.run(check())


def test_new_estimator_does_not_reprice_run65_unknown_504_or_historical_usage(real_data, declared_alias):
    async def check():
        protocol, _ = real_data
        initial = deepcopy(protocol["budget"])
        hooks = AutonomousBudgetHooks(initial["limits"], checkpoint=initial)
        assert hooks.snapshot() == initial
        assert "input_estimator" not in hooks.snapshot()
        unknown = next(row for row in protocol["requests"] if row["status"] == "ended_without_model_response")
        assert initial["charged"]["input_tokens"] - initial["reported_usage"]["input_tokens"] == unknown["reserved_input_tokens"]
        assert initial["charged"]["output_tokens"] - initial["reported_usage"]["output_tokens"] == unknown["reserved_output_tokens"]
        agent, items = projected_agent_input(real_data, declared_alias)
        context = RunContextWrapper(context=SimpleNamespace(run_id=protocol["provenance"]["run_id"]))
        await hooks.on_llm_start(context, agent, agent.instructions, items)
        admitted = hooks.snapshot()
        estimate = admitted["input_estimator"][declared_alias]
        assert admitted["charged"]["input_tokens"] == initial["charged"]["input_tokens"] + estimate["reserved_input_tokens"]
        hooks.interrupt_requests(execution_context=context.context)
        interrupted = hooks.snapshot()
        assert interrupted["charged"] == admitted["charged"]
        assert interrupted["reported_usage"] == initial["reported_usage"]
        assert interrupted["interrupted_requests"] == initial["interrupted_requests"] + 1
        restored = AutonomousBudgetHooks(initial["limits"], checkpoint=interrupted)
        assert restored.snapshot() == interrupted
    asyncio.run(check())
