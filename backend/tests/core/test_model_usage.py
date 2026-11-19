"""Token 计量的纯协议边界，不构造模型响应或业务产物。"""

import pytest

from core.ai.usage import normalized_usage


def test_real_run47_usage_keeps_unknown_cache_distinct_from_zero():
    # Run 47 / Node 373 的实际 SDK 用量；供应商未给出的缓存明细保持未知。
    actual = {"requests": 3, "input_tokens": 11150, "total_tokens": 19015, "output_tokens": 7865}
    usage = normalized_usage(actual)
    assert usage == {"input_tokens": 11150, "output_tokens": 7865, "total_tokens": 19015,
                     "cached_input_tokens": None, "uncached_input_tokens": None,
                     "reasoning_tokens": None}


def test_missing_usage_is_not_zero_usage():
    assert all(value is None for value in normalized_usage(None).values())
    assert normalized_usage({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}) == {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "cached_input_tokens": None, "uncached_input_tokens": None, "reasoning_tokens": None,
    }


def test_explicit_zero_cache_is_known_for_both_provider_protocols():
    for value in ({"prompt_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0},
                  {"input_tokens": 0, "input_tokens_details": {"cached_tokens": 0}}):
        usage = normalized_usage(value)
        assert usage["cached_input_tokens"] == usage["uncached_input_tokens"] == 0


def test_negative_and_boolean_counters_are_not_valid_usage():
    usage = normalized_usage({"prompt_tokens": False, "completion_tokens": -1,
                              "prompt_tokens_details": {"cached_tokens": -1}})
    assert all(value is None for value in usage.values())


def test_contradictory_cache_counters_are_rejected():
    with pytest.raises(ValueError, match="超过输入总量"):
        normalized_usage({"prompt_tokens": 0, "prompt_cache_hit_tokens": 1})
    with pytest.raises(ValueError, match="之和"):
        normalized_usage({"prompt_tokens": 0, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 1})
