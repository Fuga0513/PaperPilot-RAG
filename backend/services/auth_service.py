"""Authentication business logic for HTTP routes."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from auth import authenticate_user, create_access_token, get_password_hash, resolve_role
from models import User
from schemas import AuthResponse, LoginRequest, RegisterRequest


def register_user(db: Session, request: RegisterRequest) -> AuthResponse:
    username = (request.username or "").strip()
    password = (request.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    exists = db.query(User).filter(User.username == username).first()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")

    role = resolve_role(request.role, request.admin_code)
    user = User(username=username, password_hash=get_password_hash(password), role=role)
    db.add(user)
    db.commit()

    token = create_access_token(username=username, role=role)
    return AuthResponse(access_token=token, username=username, role=role)


def login_user(db: Session, request: LoginRequest) -> AuthResponse:
    user = authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(username=user.username, role=user.role)
    return AuthResponse(access_token=token, username=user.username, role=user.role)
