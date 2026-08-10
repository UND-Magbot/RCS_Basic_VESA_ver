"""DispatchSession CRUD"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.dispatch import DispatchSession, TabletSlot, DispatchReservation, DispatchWaypoint


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


# ── 경유지 (콘솔에서 등록한 작업 경로) CRUD ─────────────────


def set_route_waypoints(db: Session, session_id: int, poi_ids: list[int]) -> None:
    """세션의 경유지를 새로 설정 (기존 것 삭제 후 seq 순서대로 생성)."""
    db.query(DispatchWaypoint).filter(DispatchWaypoint.session_id == session_id).delete()
    for seq, pid in enumerate(poi_ids):
        db.add(DispatchWaypoint(session_id=session_id, seq=seq, poi_id=pid, status="pending"))
    db.commit()


def list_waypoints(db: Session, session_id: int) -> list[DispatchWaypoint]:
    return (
        db.query(DispatchWaypoint)
        .filter(DispatchWaypoint.session_id == session_id)
        .order_by(DispatchWaypoint.seq.asc())
        .all()
    )


def mark_waypoint(db: Session, session_id: int, seq: int, status: str) -> None:
    wp = (
        db.query(DispatchWaypoint)
        .filter(DispatchWaypoint.session_id == session_id, DispatchWaypoint.seq == seq)
        .first()
    )
    if wp:
        wp.status = status
        db.commit()


def next_pending_waypoint(db: Session, session_id: int) -> Optional[DispatchWaypoint]:
    """아직 안 간(또는 진행 중) 경유지 중 가장 빠른 seq."""
    return (
        db.query(DispatchWaypoint)
        .filter(
            DispatchWaypoint.session_id == session_id,
            DispatchWaypoint.status.in_(("pending", "current")),
        )
        .order_by(DispatchWaypoint.seq.asc())
        .first()
    )


def clear_route_waypoints(db: Session, session_id: int) -> None:
    db.query(DispatchWaypoint).filter(DispatchWaypoint.session_id == session_id).delete()
    db.commit()


# ── 예약 (가용 로봇 없을 때 대기열) CRUD ─────────────────


def get_waiting_reservation(db: Session, poi_id: int) -> Optional[DispatchReservation]:
    """그 POI의 대기 중(waiting) 예약 1건 (없으면 None)."""
    return (
        db.query(DispatchReservation)
        .filter(
            DispatchReservation.poi_id == poi_id,
            DispatchReservation.status == "waiting",
        )
        .order_by(DispatchReservation.id.asc())
        .first()
    )


def list_waiting_reservations(db: Session) -> list[DispatchReservation]:
    """대기 중 예약 전체 — 먼저 예약한 순서(FIFO)."""
    return (
        db.query(DispatchReservation)
        .filter(DispatchReservation.status == "waiting")
        .order_by(DispatchReservation.created_at.asc(), DispatchReservation.id.asc())
        .all()
    )


def waiting_reservation_poi_ids(db: Session) -> set[int]:
    return {r.poi_id for r in list_waiting_reservations(db)}


def create_reservation(db: Session, poi_id: int, with_rack: bool = True) -> DispatchReservation:
    """예약 생성. 같은 POI에 이미 waiting 예약이 있으면 그걸 그대로 반환(중복 방지)."""
    existing = get_waiting_reservation(db, poi_id)
    if existing:
        return existing
    r = DispatchReservation(poi_id=poi_id, with_rack=with_rack, status="waiting")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def cancel_reservation(db: Session, poi_id: int) -> bool:
    """그 POI의 대기 중 예약을 취소. 취소된 게 있으면 True."""
    r = get_waiting_reservation(db, poi_id)
    if not r:
        return False
    r.status = "cancelled"
    db.commit()
    return True


def mark_reservation(db: Session, reservation_id: int, status: str) -> None:
    r = db.query(DispatchReservation).filter(DispatchReservation.id == reservation_id).first()
    if not r:
        return
    r.status = status
    if status == "fulfilled":
        r.fulfilled_at = datetime.utcnow()
    db.commit()


def occupied_poi_ids(db: Session) -> set[int]:
    """현재 활성 세션이 점유 중인 POI id.

    포함:
      - current_poi_id : 지금 도착해 있는 곳 (단, 떠나는 중이면 제외 — 아래)
      - target_poi_id  : 지금 이동 중인 곳
      - 아직 방문하지 않은 경유지(pending/current) : 그 로봇이 곧 갈 곳이므로 미리 점유
        (이걸 빼면 다른 로봇이 그 경유지를 선점해 충돌한다)

    제외(그 위치를 '떠나는 중'이라 곧 비워짐):
      - returning : 종료 복귀 중
      - moving 이고 target 이 따로 있음 : 다른 POI로 이동 중이라 current 는 출발지(비워짐).
        (이걸 점유로 남기면, 방금 떠난 POI를 호출/예약할 때 '점유중'으로 잘못 거부됨)
    """
    rows = (
        db.query(
            DispatchSession.id,
            DispatchSession.status,
            DispatchSession.current_poi_id,
            DispatchSession.target_poi_id,
        )
        .filter(DispatchSession.status.notin_(("completed", "failed")))
        .all()
    )
    s: set[int] = set()
    session_ids: list[int] = []
    for sid, st, c, t in rows:
        session_ids.append(sid)
        leaving_current = (st == "returning") or (st == "moving" and t is not None)
        if c and not leaving_current:
            s.add(c)
        if t:
            s.add(t)

    if session_ids:
        wp_rows = (
            db.query(DispatchWaypoint.poi_id)
            .filter(
                DispatchWaypoint.session_id.in_(session_ids),
                DispatchWaypoint.status.in_(("pending", "current")),
            )
            .all()
        )
        for (pid,) in wp_rows:
            if pid:
                s.add(pid)
    return s
