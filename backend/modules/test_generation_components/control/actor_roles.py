from __future__ import annotations

import re
from typing import Any


CANONICAL_ROLE_SESSION_KEYS = {
    "admin": "admin_session",
    "guest": "guest_session",
    "authenticated": "authenticated_session",
    "anonymous": "anonymous_session",
    "business_user": "business_user_session",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: str, *, fallback: str = "business_user") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", _text(value).lower()).strip("_")
    return normalized[:64] or fallback


def normalize_actor_role(
    value: Any,
    *,
    default_role: str = "business_user",
) -> str:
    """规范化显式角色；正文只描述行为，不作为角色推断依据。"""
    explicit_role = _slug(_text(value), fallback="")
    if explicit_role:
        return explicit_role
    return _slug(default_role, fallback="business_user")


def session_key_for_role(role: Any) -> str:
    normalized = _slug(_text(role), fallback="business_user")
    if normalized in CANONICAL_ROLE_SESSION_KEYS:
        return CANONICAL_ROLE_SESSION_KEYS[normalized]
    return f"{_slug(normalized)}_session"


__all__ = [
    "CANONICAL_ROLE_SESSION_KEYS",
    "normalize_actor_role",
    "session_key_for_role",
]
