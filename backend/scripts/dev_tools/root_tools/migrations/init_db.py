from __future__ import annotations

import os
import secrets
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import core.db.model_defs  # noqa: F401
from core.authn.auth import get_password_hash
from core.db.database import Base
from core.db.model_defs import Project, User
from core.settings.config import settings


def _ensure_mysql_database() -> bool:
    if "mysql" not in settings.DATABASE_URL:
        return True
    try:
        root_url = (
            f"mysql+pymysql://{settings.DB_USER_ENCODED}:{settings.DB_PASSWORD}"
            f"@{settings.DB_HOST}:{settings.DB_PORT}/mysql"
        )
        root_engine = create_engine(root_url, connect_args={"connect_timeout": 3})
        with root_engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.DB_NAME}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
        return True
    except Exception as exc:
        print(f"创建数据库失败：{exc}")
        return False


def _build_engine():
    database_url = settings.DATABASE_URL
    if "mysql" in database_url and "charset=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}charset=utf8mb4"
    connect_args = {"connect_timeout": 3}
    if "mysql" in database_url:
        connect_args["charset"] = "utf8mb4"
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def _ensure_default_records(engine) -> None:
    with Session(engine) as session:
        username = os.getenv("ADMIN_USERNAME", "admin")
        email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        password = os.getenv("ADMIN_PASSWORD")
        user = session.query(User).filter(User.username == username).first()
        if user is None:
            password = password or secrets.token_urlsafe(16)
            user = User(
                username=username,
                email=email,
                hashed_password=get_password_hash(password),
                is_active=True,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            print(f"已创建默认用户：{username}")
            if not os.getenv("ADMIN_PASSWORD"):
                print(f"一次性临时密码：{password}")
        elif password:
            user.hashed_password = get_password_hash(password)
            user.is_active = True
            session.commit()

        project = (
            session.query(Project)
            .filter(Project.name == "Default Project", Project.user_id == user.id)
            .first()
        )
        if project is None:
            session.add(
                Project(
                    name="Default Project",
                    description="Default project for initial setup",
                    user_id=user.id,
                )
            )
            session.commit()
            print("已创建默认项目")


def init_db() -> bool:
    """按当前 ORM 元数据初始化全新数据库，不修补旧表结构。"""

    if not _ensure_mysql_database():
        return False
    try:
        engine = _build_engine()
        Base.metadata.create_all(bind=engine)
        _ensure_default_records(engine)
        print("数据库初始化完成")
        return True
    except Exception as exc:
        print(f"数据库初始化失败：{exc}")
        return False


if __name__ == "__main__":
    sys.exit(0 if init_db() else 1)
