"""离线解析辅助函数。"""

from __future__ import annotations

from typing import Any

MAX_PARSE_ERROR_LENGTH = 2000
PARSE_FAILURE_PREFIXES = (
    "[Error parsing file:",
    "[Error reading Excel:",
    "[Error reading CSV:",
    "[Error processing image:",
    "[Unsupported file type:",
)


def safe_error_message(error: Any) -> str:
    """把异常对象转换为可展示文本，并做长度保护。"""
    text = str(error or "").strip()
    if not text:
        return "离线解析失败，请稍后重试。"

    lower = text.lower()
    mapping = [
        ("timeout", "连接超时，请检查网络后重试。"),
        ("timed out", "连接超时，请检查网络后重试。"),
        ("ssl", "SSL 连接异常，请检查网络或证书配置。"),
        ("unexpected_eof_while_reading", "网络连接被中断，请稍后重试。"),
        ("connection refused", "目标服务拒绝连接，请检查服务状态。"),
        ("econnrefused", "目标服务拒绝连接，请检查服务状态。"),
        ("not found", "未找到对应资源，请检查输入。"),
        ("permission", "权限不足，请检查账号权限。"),
        ("unauthorized", "鉴权失败，请检查密钥或登录状态。"),
    ]
    for key, message in mapping:
        if key in lower:
            return message

    if len(text) > MAX_PARSE_ERROR_LENGTH:
        return text[:MAX_PARSE_ERROR_LENGTH] + "..."
    return text


def validate_parsed_content(content: str) -> None:
    """校验解析结果，防止把错误占位文本误当作正常内容入库。"""
    normalized = str(content or "").strip()
    if not normalized:
        raise ValueError("未解析到可用文本内容，请检查文件是否为空。")

    for prefix in PARSE_FAILURE_PREFIXES:
        if normalized.startswith(prefix):
            raise ValueError(f"文件解析失败：{normalized[:180]}")
