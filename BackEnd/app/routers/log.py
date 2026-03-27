from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.crud.log import get_unified_logs

router = APIRouter(prefix="/api/logs", tags=["통합 로그"])


@router.get("")
def api_get_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    display_category: str | None = Query(None, description="사용자 | 시스템 | 로봇"),
    message: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: Session = Depends(get_db),
):
    """activity_logs 조회 (기본: 당일 KST)"""
    items, total = get_unified_logs(
        db,
        skip=skip,
        limit=limit,
        display_category=display_category,
        message=message,
        date_from=date_from,
        date_to=date_to,
    )
    return {"total": total, "items": items}
