"""Repository for user account persistence operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from core.db.models import User


class UserRepository:
    """Session-backed repository for auth flows."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_username(self, username: str) -> Optional[User]:
        return self.db.query(User).filter(User.username == username).first()

    def create_user(self, *, username: str, hashed_password: str) -> User:
        user = User(username=username, hashed_password=hashed_password)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

