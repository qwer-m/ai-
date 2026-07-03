"""Business service for authentication routes."""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import HTTPException, status
import httpx
from sqlalchemy.orm import Session

from core.authn.auth import create_access_token, get_password_hash, verify_password
from core.settings.config import settings
from core.settings.config_manager import config_manager
from modules.system_components.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class AuthService:
    """Use-case layer for register/login flows."""

    def __init__(self, db: Session):
        self.user_repo = UserRepository(db)
        self.db = db

    def register_user(self, *, username: str, password: str, email: str | None = None):
        username = str(username or "").strip()
        email = str(email or "").strip().lower() or None
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password are required")

        existing = self.user_repo.get_by_username(username)
        if existing:
            raise HTTPException(status_code=400, detail="Username already registered")
        if email and self.user_repo.get_by_email(email):
            raise HTTPException(status_code=400, detail="Email already registered")

        user = self.user_repo.create_user(
            username=username,
            email=email,
            hashed_password=get_password_hash(password),
        )

        try:
            config_manager.create_config(
                self.db,
                provider="dashscope",
                model_name="",
                vl_model_name="",
                turbo_model_name="",
                api_key="",
                activate=True,
                user_id=user.id,
            )
        except Exception as exc:
            logger.warning("Failed to init config for user %s: %s", user.id, exc)
        return user

    def login(self, *, username: str, password: str) -> dict[str, str]:
        user = self.user_repo.get_by_username(username)
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        token = create_access_token(data={"sub": user.username}, expires_delta=expires)
        return {"access_token": token, "token_type": "bearer"}

    def reset_password(self, *, username: str, email: str, new_password: str) -> dict[str, str]:
        username = str(username or "").strip()
        email = str(email or "").strip().lower()
        if not username or not email or not new_password:
            raise HTTPException(status_code=400, detail="Username, email and new password are required")
        if len(str(new_password)) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

        user = self.user_repo.get_by_username(username)
        if not user or str(user.email or "").strip().lower() != email:
            raise HTTPException(status_code=400, detail="Username and email do not match")

        self.user_repo.update_password(user, hashed_password=get_password_hash(new_password))
        return {"status": "ok", "message": "Password updated"}

    def google_login_url(self) -> dict[str, str]:
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        redirect_uri = os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://127.0.0.1:8000/api/auth/google/callback",
        ).strip()
        if not client_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Google login is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
            )

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "online",
            "prompt": "select_account",
            "state": secrets.token_urlsafe(24),
        }
        return {"login_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}

    def login_with_google_code(self, *, code: str) -> dict[str, str]:
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://127.0.0.1:8000/api/auth/google/callback",
        ).strip()
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Google login is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
            )
        if not str(code or "").strip():
            raise HTTPException(status_code=400, detail="Missing Google authorization code")

        try:
            with httpx.Client(timeout=10.0) as client:
                token_response = client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                token_response.raise_for_status()
                token_payload = token_response.json()
                id_token = str(token_payload.get("id_token") or "")
                if not id_token:
                    raise HTTPException(status_code=400, detail="Google did not return an id_token")

                info_response = client.get(GOOGLE_TOKENINFO_URL, params={"id_token": id_token})
                info_response.raise_for_status()
                info = info_response.json()
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Google OAuth exchange failed: %s", exc)
            raise HTTPException(status_code=400, detail="Google login failed") from exc

        if str(info.get("aud") or "") != client_id:
            raise HTTPException(status_code=400, detail="Google token audience mismatch")
        email = str(info.get("email") or "").strip().lower()
        email_verified = info.get("email_verified")
        if not email or str(email_verified).lower() not in {"true", "1"}:
            raise HTTPException(status_code=400, detail="Google email is not verified")

        user = self.user_repo.get_by_email(email)
        if not user:
            username = self._unique_google_username(email)
            user = self.user_repo.create_user(
                username=username,
                email=email,
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
            )

        expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        token = create_access_token(data={"sub": user.username}, expires_delta=expires)
        return {"access_token": token, "token_type": "bearer"}

    def _unique_google_username(self, email: str) -> str:
        base = re.sub(r"[^a-zA-Z0-9_]+", "_", email.split("@", 1)[0]).strip("_") or "google_user"
        username = base[:40]
        suffix = 1
        while self.user_repo.get_by_username(username):
            suffix += 1
            username = f"{base[:35]}_{suffix}"
        return username

