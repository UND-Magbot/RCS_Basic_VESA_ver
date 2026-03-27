from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.auth import AuthUser
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserListResponse
from app.crud.auth import get_current_user
from app.crud.user import (
    create_user,
    get_user,
    get_users,
    update_user,
    delete_user,
)
from app.crud.activity_log import log_activity

router = APIRouter(prefix="/api/users", tags=["사용자 관리"])


@router.post("", response_model=UserResponse, status_code=201)
def api_create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """DB-01 사용자 등록"""
    result = create_user(db, data)
    log_activity("user", "user_create",
                 f"[{current_user.login_id}] 사용자 등록: {data.login_id}",
                 source="api_create_user")
    return result


@router.get("", response_model=UserListResponse)
def api_get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """사용자 목록 조회"""
    items = get_users(db, skip=skip, limit=limit)
    return UserListResponse(total=len(items), items=items)


@router.get("/{user_id}", response_model=UserResponse)
def api_get_user(user_id: int, db: Session = Depends(get_db)):
    """사용자 단건 조회"""
    return get_user(db, user_id)


@router.put("/{user_id}", response_model=UserResponse)
def api_update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """DB-02 사용자 수정"""
    result = update_user(db, user_id, data)
    user = get_user(db, user_id)
    user_display = user.login_id if user else f"ID {user_id}"
    log_activity("user", "user_update",
                 f"[{current_user.login_id}] 사용자 정보 수정: {user_display}",
                 source="api_update_user")
    return result


@router.delete("/{user_id}")
def api_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """DB-03 사용자 삭제 (Soft Delete)"""
    user = get_user(db, user_id)
    user_display = user.login_id if user else f"ID {user_id}"
    result = delete_user(db, user_id)
    log_activity("user", "user_delete",
                 f"[{current_user.login_id}] 사용자 삭제: {user_display}",
                 source="api_delete_user")
    return result
