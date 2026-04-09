"""Business service for authentication routes."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from core.authn.auth import create_access_token, get_password_hash, verify_password
from core.settings.config import settings
from core.settings.config_manager import config_manager
from modules.system_components.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)


class AuthService:
    """Use-case layer for register/login flows."""

    def __init__(self, db: Session):
        self.user_repo = UserRepository(db)
        self.db = db

    def register_user(self, *, username: str, password: str):
        existing = self.user_repo.get_by_username(username)
        if existing:
            raise HTTPException(status_code=400, detail="Username already registered")

        user = self.user_repo.create_user(
            username=username,
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

