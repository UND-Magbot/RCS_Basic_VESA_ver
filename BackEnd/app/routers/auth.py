from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from passlib.hash import bcrypt

from app.schemas.auth import LoginRequest, LoginResponse, AuthUser, VerifyPasswordRequest, ChangePasswordRequest
from app.schemas.user import ROLE_MAP
from app.crud.auth import authenticate_user, create_access_token, get_current_user
from app.crud.activity_log import log_activity
from app.models.user import User

router = APIRouter(prefix="/api/auth", tags=["인증"])


@router.post("/login", response_model=LoginResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    """로그인 — JWT 토큰 발급

    프론트엔드 연동:
      POST /api/auth/login
      Body: { "login_id": "admin", "password": "Admin1234!" }
      → { "access_token": "...", "token_type": "bearer", "user": {...} }
    """
    user, role_code = authenticate_user(db, data.login_id, data.password)
    token = create_access_token(user.id, role_code)
    log_activity("user", "user_login",
                 f"사용자 '{data.login_id}' 로그인",
                 source="login")

    return LoginResponse(
        access_token=token,
        user=AuthUser(
            id=user.id,
            login_id=user.login_id,
            username=user.username,
            role=role_code,
            role_name=ROLE_MAP.get(role_code, "Unknown"),
        ),
    )


@router.get("/me", response_model=AuthUser)
def get_me(current_user: AuthUser = Depends(get_current_user)):
    """현재 로그인한 사용자 정보 조회

    프론트엔드 연동:
      GET /api/auth/me
      Header: Authorization: Bearer <token>
      → { "id": 1, "login_id": "admin", "username": "관리자", "role": 1, "role_name": "Administrator" }
    """
    return current_user


@router.post("/verify-password")
def verify_password(
    data: VerifyPasswordRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """현재 비밀번호 확인

    프론트엔드 연동:
      POST /api/auth/verify-password
      Header: Authorization: Bearer <token>
      Body: { "password": "현재비밀번호" }
      → { "valid": true/false }
    """
    user = db.query(User).filter(User.id == current_user.id).first()
    valid = user is not None and bcrypt.verify(data.password, user.password_hash)
    return {"valid": valid}


@router.put("/change-password")
def change_password(
    data: ChangePasswordRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """비밀번호 변경

    프론트엔드 연동:
      PUT /api/auth/change-password
      Header: Authorization: Bearer <token>
      Body: { "current_password": "현재비밀번호", "new_password": "새비밀번호" }
      → { "message": "비밀번호가 변경되었습니다." }
    """
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾지 못했습니다.")

    if not bcrypt.verify(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 일치하지 않습니다.")

    user.password_hash = bcrypt.hash(data.new_password)
    db.commit()

    log_activity("user", "password_change",
                 f"사용자 '{current_user.login_id}' 비밀번호 변경",
                 source="change_password")

    return {"message": "비밀번호가 변경되었습니다."}
