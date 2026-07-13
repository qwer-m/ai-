from __future__ import annotations

import re
from typing import Any


CANONICAL_ROLE_SESSION_KEYS = {
    "admin": "admin_review_session",
    "supervisor": "supervisor_session",
    "teacher": "supervisor_session",
    "member": "member_student_session",
    "student_free": "free_student_session",
    "student": "student_session",
    "business_user": "business_user_session",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens if token)


def _slug(value: str, *, fallback: str = "business_user") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", _text(value).lower()).strip("_")
    return normalized[:64] or fallback


def normalize_actor_role(
    value: Any,
    *,
    fallback_text: str = "",
    default_role: str = "business_user",
) -> str:
    raw = _text(value).lower()
    if raw in CANONICAL_ROLE_SESSION_KEYS:
        return "supervisor" if raw == "teacher" else raw
    if raw == "administrator":
        return "admin"
    if raw in {"free", "free_student", "non_member", "non-member", "nonmember"}:
        return "student_free"
    if raw == "vip":
        return "member"
    if raw == "user":
        return "business_user"
    if raw == "learner":
        return "student"

    text = " ".join(part for part in (raw, _text(fallback_text).lower()) if part).strip()
    free_markers = (
        "non_member",
        "non-member",
        "nonmember",
        "free student",
        "unpaid student",
        "trial student",
        "非会员",
        "免费用户",
        "未付费",
        "未购买",
        "未订阅",
    )
    if _has_any(text, free_markers):
        return "student_free"

    admin_markers = (
        "admin",
        "administrator",
        "back office",
        "backend",
        "ops",
        "后台",
        "管理员",
        "运营",
        "审核员",
    )
    if _has_any(text, admin_markers):
        return "admin"

    supervisor_markers = (
        "supervisor",
        "teacher",
        "mentor",
        "coach",
        "老师",
        "教师",
        "督导",
        "辅导",
        "教练",
        "管理端",
    )
    if _has_any(text, supervisor_markers):
        return "supervisor"

    student_markers = (
        "student",
        "learner",
        "学生",
        "学员",
        "学生端",
        "学员端",
    )
    if _has_any(text, student_markers):
        return "student"

    member_markers = ("member", "vip", "会员")
    if _has_any(text, member_markers):
        return "member"

    generic_business_user_markers = (
        "user",
        "customer",
        "client",
        "buyer",
        "seller",
        "applicant",
        "requester",
        "approver",
        "operator",
        "用户",
        "客户",
        "买家",
        "卖家",
        "申请人",
        "审批人",
        "操作人",
    )
    if _has_any(text, generic_business_user_markers):
        return "business_user"

    return default_role if default_role in CANONICAL_ROLE_SESSION_KEYS else "business_user"


def session_key_for_role(role: Any) -> str:
    normalized = _text(role).lower()
    if normalized in CANONICAL_ROLE_SESSION_KEYS:
        return CANONICAL_ROLE_SESSION_KEYS[normalized]
    return f"{_slug(normalized)}_session"


__all__ = [
    "CANONICAL_ROLE_SESSION_KEYS",
    "normalize_actor_role",
    "session_key_for_role",
]
