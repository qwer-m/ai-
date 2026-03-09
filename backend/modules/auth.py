"""
认证模块 (Auth Module)

该模块处理用户注册、登录和 Token 发放。
主要功能：
1. 用户注册 (register): 创建新用户并初始化默认系统配置。
2. 用户登录 (login): 验证凭据并分发 JWT Token。
3. 获取当前用户信息 (read_users_me)。

调用关系：
- 依赖 `core.auth` 提供的密码哈希和 Token 生成工具。
- 调用 `core.config_manager` 在注册时初始化用户配置。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from fastapi.security import OAuth2PasswordRequestForm
from datetime import timedelta

from core.database import get_db
from core.models import User
from core.auth import verify_password, get_password_hash, create_access_token, get_current_user
from core.config import settings
from core.config_manager import config_manager

router = APIRouter(prefix="/auth", tags=["Auth"])

class UserCreate(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

@router.post("/register", response_model=UserResponse)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    """
    用户注册 (User Registration)
    
    1. 检查用户名是否已存在。
    2. 创建新用户并哈希密码。
    3. 为新用户初始化默认系统配置 (ConfigManager)。
    """
    user = db.query(User).filter(User.username == user_in.username).first()
    if user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = get_password_hash(user_in.password)
    new_user = User(username=user_in.username, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Initialize default system config for the new user
    try:
        config_manager.create_config(
            db,
            provider="dashscope", # Default provider
            model_name="", # Default empty as requested
            vl_model_name="", # Default empty as requested
            turbo_model_name="", # Default empty as requested
            api_key="", # Default empty as requested
            activate=True,
            user_id=new_user.id
        )
    except Exception as e:
        # Log error but don't fail registration
        print(f"Failed to init config for user {new_user.id}: {e}")
        
    return new_user

@router.post("/login", response_model=Token)
@router.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_user)):
    """
    获取当前用户信息 (Get Current User Info)
    
    返回当前登录用户的详细信息 (基于 Token 解析)。
    """
    return current_user
