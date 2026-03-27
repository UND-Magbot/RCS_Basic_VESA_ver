from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.activity_log import ActivityLog

KST = timezone(timedelta(hours=9))

# activity_log.category → 화면 표시 카테고리
_ACTIVITY_CATEGORY_MAP = {
    "robot":  "로봇",
    "system": "시스템",
    "map":    "시스템",
    "user":   "사용자",
}


def _activity_display_category(category: str) -> str:
    return _ACTIVITY_CATEGORY_MAP.get(category, "사용자")


def _today_range() -> tuple[datetime, datetime]:
    now_kst = datetime.now(KST)
    start = now_kst.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    end = start + timedelta(days=1)
    return start, end


def get_unified_logs(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    display_category: str | None = None,
    message: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[list[dict], int]:
    """activity_logs 조회 (created_at 내림차순)"""
    if date_from is None and date_to is None:
        date_from, date_to = _today_range()

    rows: list[dict] = []

    aq = db.query(ActivityLog)
    if date_from:
        aq = aq.filter(ActivityLog.created_at >= date_from)
    if date_to:
        aq = aq.filter(ActivityLog.created_at < date_to)
    if message:
        aq = aq.filter(ActivityLog.message.ilike(f"%{message}%"))

    if display_category == "사용자":
        aq = aq.filter(ActivityLog.category.in_(["user"]))
    elif display_category == "시스템":
        aq = aq.filter(ActivityLog.category.in_(["system", "map"]))
    elif display_category == "로봇":
        aq = aq.filter(ActivityLog.category == "robot")

    for log in aq.all():
        rows.append({
            "id": f"a{log.id}",
            "display_category": _activity_display_category(log.category),
            "action": log.action,
            "message": log.message,
            "detail": log.detail,
            "robot_id": log.robot_id,
            "robot_name": log.robot_name,
            "source": log.source,
            "created_at": log.created_at,
        })

    rows.sort(key=lambda x: x["created_at"] or datetime.min, reverse=True)
    total = len(rows)
    return rows[skip: skip + limit], total
