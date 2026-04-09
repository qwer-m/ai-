"""Auth routes."""

from fastapi import APIRouter, Depends
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
    return AuthService(db).register_user(username=user_in.username, password=user_in.password)


@router.post("/login", response_model=Token)
@router.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return AuthService(db).login(username=form_data.username, password=form_data.password)


@router.get("/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user
