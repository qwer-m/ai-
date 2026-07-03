"""Auth routes."""

import html
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.authn.auth import get_current_user
from core.db.database import get_db
from core.db.models import User
from modules.system_components.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


class UserCreate(BaseModel):
    username: str
    email: str | None = None
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str | None = None

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class PasswordResetRequest(BaseModel):
    username: str
    email: str
    new_password: str


class PasswordResetResponse(BaseModel):
    status: str
    message: str


class GoogleLoginUrl(BaseModel):
    login_url: str


@router.post("/register", response_model=UserResponse)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    return AuthService(db).register_user(
        username=user_in.username,
        email=user_in.email,
        password=user_in.password,
    )


@router.post("/login", response_model=Token)
@router.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return AuthService(db).login(username=form_data.username, password=form_data.password)


@router.post("/password-reset", response_model=PasswordResetResponse)
def reset_password(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    return AuthService(db).reset_password(
        username=payload.username,
        email=payload.email,
        new_password=payload.new_password,
    )


@router.get("/google/login", response_model=GoogleLoginUrl)
def google_login(db: Session = Depends(get_db)):
    return AuthService(db).google_login_url()


@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(code: str = "", error: str = "", db: Session = Depends(get_db)):
    frontend_url = os.getenv("VITE_DEV_SERVER_URL", "http://127.0.0.1:5173")
    if error:
        return _google_callback_error(frontend_url, f"Google login failed: {error}")
    try:
        token_payload = AuthService(db).login_with_google_code(code=code)
    except HTTPException as exc:
        return _google_callback_error(frontend_url, str(exc.detail), status_code=exc.status_code)

    token = token_payload.get("access_token") or ""
    return HTMLResponse(
        content=f"""
<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>Google 登录完成</title></head>
  <body>
    <p>Google 登录成功，正在返回系统...</p>
    <script>
      localStorage.setItem("token", {json.dumps(token)});
      window.location.replace({json.dumps(frontend_url)});
    </script>
  </body>
</html>
""".strip()
    )


def _google_callback_error(frontend_url: str, message: str, *, status_code: int = 400) -> HTMLResponse:
    safe_message = html.escape(str(message or "Google login failed"))
    return HTMLResponse(
        status_code=status_code,
        content=f"""
<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>Google 登录失败</title></head>
  <body>
    <p>{safe_message}</p>
    <p><a href="{html.escape(frontend_url)}">返回登录页</a></p>
  </body>
</html>
""".strip(),
    )


@router.get("/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user
