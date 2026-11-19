"""供应商实际 Token 用量标准化，不参与价格计算或请求放行。"""

from typing import Any


def _mapping(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(vars(value)) if value is not None and hasattr(value, "__dict__") else {}


def normalized_usage(value: Any) -> dict[str, Any]:
    """未报告与零用量分开；兼容标准协议和厂商明确的缓存命中字段。"""
    raw = _mapping(value)

    def count(*keys):
        for key in keys:
            candidate = raw.get(key)
            if type(candidate) is int and candidate >= 0:
                return candidate
        return None

    input_tokens = count("prompt_tokens", "input_tokens")
    output_tokens = count("completion_tokens", "output_tokens")
    details = _mapping(raw.get("prompt_tokens_details") or raw.get("input_tokens_details"))
    output_details = _mapping(raw.get("completion_tokens_details") or raw.get("output_tokens_details"))
    cached = count("prompt_cache_hit_tokens")
    if cached is None and type(details.get("cached_tokens")) is int and details["cached_tokens"] >= 0:
        cached = details["cached_tokens"]
    uncached = count("prompt_cache_miss_tokens")
    if uncached is None and input_tokens is not None and cached is not None and cached <= input_tokens:
        uncached = input_tokens - cached
    reasoning = output_details.get("reasoning_tokens")
    result = {"input_tokens": input_tokens, "output_tokens": output_tokens,
              "total_tokens": count("total_tokens"), "cached_input_tokens": cached,
              "uncached_input_tokens": uncached,
              "reasoning_tokens": reasoning if type(reasoning) is int and reasoning >= 0 else None}
    if cached is not None and input_tokens is not None and cached > input_tokens:
        raise ValueError("供应商缓存用量超过输入总量")
    if cached is not None and uncached is not None and input_tokens is not None and cached + uncached != input_tokens:
        raise ValueError("供应商命中与未命中用量之和不等于输入总量")
    return result
