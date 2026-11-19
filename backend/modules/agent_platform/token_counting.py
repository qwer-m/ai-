"""可审计的本地文本分词估计；服务商回执仍是实际用量依据。"""

from __future__ import annotations

from functools import lru_cache
import json
import math
import os
from typing import Any

import tiktoken


_PROTOCOL_MARGIN_RATIO = 0.15
_MIN_PROTOCOL_TOKENS = 256
# 未绑定模型图像编码时的独立预留，不能把图片 Base64 当作文本分词。
_IMAGE_TOKENS = 4096


def json_token_text(value: Any) -> str:
    def encode_object(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json", exclude_none=True)
        raise TypeError(f"模型输入包含无法计量的对象: {type(item).__name__}")

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=encode_object)


@lru_cache(maxsize=8)
def _configured_encodings(raw: str) -> dict[str, str]:
    try:
        configured = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("AGENT_TOKEN_ENCODING_MAP 必须是模型名到 tiktoken 编码名的 JSON 对象") from exc
    if not isinstance(configured, dict) or any(
        not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
        for key, value in configured.items()
    ):
        raise ValueError("AGENT_TOKEN_ENCODING_MAP 必须是非空模型名到非空编码名的 JSON 对象")
    return configured


def token_encoding_profile(model: Any) -> dict[str, Any]:
    """未知代理别名必须显式配置，不能冒用官方模型到分词器的映射。"""
    name = model if isinstance(model, str) else getattr(model, "model", None)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Token 预算缺少实际模型名，不能使用隐式默认分词器")
    configured = _configured_encodings(os.getenv("AGENT_TOKEN_ENCODING_MAP", "{}"))
    if name in configured:
        encoding_name, mapping_source = configured[name], "explicit_alias_estimate"
    else:
        try:
            encoding_name = tiktoken.encoding_name_for_model(name)
        except KeyError as exc:
            raise ValueError(
                f"模型 {name} 没有已知 tiktoken 映射；请在 AGENT_TOKEN_ENCODING_MAP 显式声明近似编码"
            ) from exc
        mapping_source = "tiktoken_model_map"
    try:
        tiktoken.get_encoding(encoding_name)
    except ValueError as exc:
        raise ValueError(f"模型 {name} 配置了不存在的 tiktoken 编码: {encoding_name}") from exc
    return {"model": name, "encoding": encoding_name, "mapping_source": mapping_source,
            "tokenizer_version": tiktoken.__version__, "accuracy": "local_estimate_not_reported_usage"}


def input_budget_view(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        views = [input_budget_view(item) for item in value]
        return [item for item, _ in views], sum(count for _, count in views)
    if isinstance(value, dict):
        if value.get("type") == "input_image":
            return {key: item for key, item in value.items() if key not in {"image_url", "file_id"}}, 1
        views = {key: input_budget_view(item) for key, item in value.items()}
        return {key: item for key, (item, _) in views.items()}, sum(count for _, count in views.values())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value, 0
    if hasattr(value, "model_dump"):
        return input_budget_view(value.model_dump(mode="json", exclude_none=True))
    raise TypeError(f"模型输入包含无法计量的对象: {type(value).__name__}")


def input_protocol_reservation(image_count: int = 0, *, text_tokens: int = 0) -> int:
    """声明 15%（至少 256）的协议余量，图片另按每张 4096 近似预留。"""
    if type(image_count) is not int or image_count < 0 or type(text_tokens) is not int or text_tokens < 0:
        raise ValueError("图片数量和文本 Token 数必须是非负整数")
    return max(_MIN_PROTOCOL_TOKENS, math.ceil(text_tokens * _PROTOCOL_MARGIN_RATIO)) + image_count * _IMAGE_TOKENS


def input_token_estimate(value: Any, model: Any, *, image_count: int = 0) -> dict[str, Any]:
    """额外 image_count 用于尚未装载像素的图片；内联图片会自动统计且移除编码。"""
    if type(image_count) is not int or image_count < 0:
        raise ValueError("图片预留数量必须是非负整数")
    profile = token_encoding_profile(model)
    view, inline_images = input_budget_view(value)
    images = image_count + inline_images
    text_tokens = len(tiktoken.get_encoding(profile["encoding"]).encode(json_token_text(view), disallowed_special=()))
    protocol_tokens = input_protocol_reservation(text_tokens=text_tokens)
    image_tokens = input_protocol_reservation(images, text_tokens=text_tokens) - protocol_tokens
    return {**profile, "text_tokens": text_tokens, "protocol_margin_ratio": _PROTOCOL_MARGIN_RATIO,
            "protocol_minimum_tokens": _MIN_PROTOCOL_TOKENS, "protocol_tokens": protocol_tokens,
            "image_count": images, "image_tokens": image_tokens,
            "image_profile": "unverified_model_image_encoding_4096_per_image",
            "reserved_input_tokens": text_tokens + protocol_tokens + image_tokens}


def estimate_input_tokens(value: Any, model: Any, *, image_count: int = 0) -> int:
    return input_token_estimate(value, model, image_count=image_count)["reserved_input_tokens"]


def estimate_json_tokens(value: Any) -> int:
    """容量分批使用 o200k_base 文本近似；不代表任何模型的实际收费用量。"""
    view, _ = input_budget_view(value)
    return max(1, len(tiktoken.get_encoding("o200k_base").encode(json_token_text(view), disallowed_special=())))
