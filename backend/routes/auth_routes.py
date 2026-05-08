from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from auth import get_current_user, get_db
from models import User
from schemas import AuthResponse, CurrentUserResponse, LoginRequest, RegisterRequest
from services.auth_service import login_user, register_user

router = APIRouter()


@router.post("/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    return register_user(db, request)


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    return login_user(db, request)


@router.get("/auth/me", response_model=CurrentUserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return CurrentUserResponse(username=current_user.username, role=current_user.role)
