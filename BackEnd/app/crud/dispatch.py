"""DispatchSession CRUD"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.dispatch import DispatchSession, TabletSlot


def get_active_session(db: Session, robot_id: int) -> Optional[DispatchSession]:
    """로봇의 종료되지 않은 세션 1개 (없으면 None)."""
    return (
        db.query(DispatchSession)
        .filter(
            DispatchSession.robot_id == robot_id,
            DispatchSession.status.notin_(("completed", "failed")),
        )
        .order_by(DispatchSession.id.desc())
        .first()
    )


def list_active_sessions(db: Session) -> list[DispatchSession]:
    return (
        db.query(DispatchSession)
        .filter(DispatchSession.status.notin_(("completed", "failed")))
        .order_by(DispatchSession.robot_id.asc())
        .all()
    )


def create_session(db: Session, robot_id: int, first_poi_id: int,
                   with_rack: bool = True) -> DispatchSession:
    s = DispatchSession(
        robot_id=robot_id,
        first_poi_id=first_poi_id,
        target_poi_id=first_poi_id,
        status="starting",
        with_rack=with_rack,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def update_status(db: Session, session_id: int, status: str, *, error: Optional[str] = None) -> None:
    s = db.query(DispatchSession).filter(DispatchSession.id == session_id).first()
    if not s:
        return
    s.status = status
    if error is not None:
        s.last_error = error[:500]
    if status in ("completed", "failed"):
        s.ended_at = datetime.utcnow()
    db.commit()


def set_target(db: Session, session_id: int, target_poi_id: Optional[int]) -> None:
    s = db.query(DispatchSession).filter(DispatchSession.id == session_id).first()
    if not s:
        return
    s.target_poi_id = target_poi_id
    db.commit()


def set_current(db: Session, session_id: int, current_poi_id: Optional[int]) -> None:
    s = db.query(DispatchSession).filter(DispatchSession.id == session_id).first()
    if not s:
        return
    s.current_poi_id = current_poi_id
    s.target_poi_id = None
    db.commit()


# ── 슬롯 매핑 CRUD ─────────────────────────────────────


def get_slot(db: Session, slot_number: int) -> Optional[TabletSlot]:
    return db.query(TabletSlot).filter(TabletSlot.slot_number == slot_number).first()


def list_slots(db: Session) -> list[TabletSlot]:
    return db.query(TabletSlot).order_by(TabletSlot.slot_number.asc()).all()


def upsert_slot(db: Session, slot_number: int, poi_id: int,
                alias: Optional[str] = None) -> TabletSlot:
    slot = get_slot(db, slot_number)
    if slot:
        slot.poi_id = poi_id
        slot.alias = alias
    else:
        slot = TabletSlot(slot_number=slot_number, poi_id=poi_id, alias=alias)
        db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def delete_slot(db: Session, slot_number: int) -> bool:
    slot = get_slot(db, slot_number)
    if not slot:
        return False
    db.delete(slot)
    db.commit()
    return True


def occupied_poi_ids(db: Session) -> set[int]:
    """현재 활성 세션이 점유 중인 POI id (current/target 양쪽 포함)."""
    rows = (
        db.query(DispatchSession.current_poi_id, DispatchSession.target_poi_id)
        .filter(DispatchSession.status.notin_(("completed", "failed")))
        .all()
    )
    s: set[int] = set()
    for c, t in rows:
        if c:
            s.add(c)
        if t:
            s.add(t)
    return s
