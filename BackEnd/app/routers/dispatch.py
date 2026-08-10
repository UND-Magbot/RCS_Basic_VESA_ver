"""인터랙티브 배차 (VESA 모드) 라우터

엔드포인트:
  POST   /api/dispatch/start              — 여러 로봇 동시 시작
  POST   /api/dispatch/{robot_id}/next    — 다음 POI 지시
  POST   /api/dispatch/{robot_id}/end     — 종료 (랙 반납 + 충전소 복귀)
  GET    /api/dispatch/status             — 전체 상태 + 점유 POI
  GET    /api/dispatch/tablet/{slot_number}  — 슬롯 기반 태블릿 페이지 (메인)
  GET    /api/dispatch/tablet/poi/{poi_id}   — POI 기반 태블릿 페이지 (호환)
  GET    /api/dispatch/tablet             — 태블릿 페이지 HTML (로봇 선택)
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.dispatch import DispatchSession
from app.models.map import MapPOI, RobotMap
from app.models.robot import Robot
from app.schemas.dispatch import (
    DispatchStartRequest, DispatchNextRequest,
    DispatchSessionOut, DispatchStatusOut, POIBrief,
    DispatchPOIStatusOut, DispatchCallResult, DispatchCallRequest,
    TabletSlotIn, TabletSlotOut,
    POIConsoleItem, ConsoleRobotItem, ConsoleStatusOut,
    DispatchRouteRequest, WaypointBrief, RobotTabletStatus,
)
from app.crud import dispatch as dispatch_crud
from app.services import dispatch_service
from app.models.dispatch import DispatchSession as DispatchSessionModel
from app.models.robot import Robot as RobotModel, RobotStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dispatch", tags=["인터랙티브 배차 (VESA)"])

_TABLET_TEMPLATE = Path(__file__).parent.parent / "templates" / "dispatch_tablet.html"
_CONSOLE_TEMPLATE = Path(__file__).parent.parent / "templates" / "dispatch_console.html"
_ROBOT_TABLET_TEMPLATE = Path(__file__).parent.parent / "templates" / "dispatch_robot_tablet.html"


# ── 헬퍼 ─────────────────────────────────────────────────────


def _session_to_out(session: DispatchSession, db: Session) -> DispatchSessionOut:
    robot = db.query(Robot).filter(Robot.id == session.robot_id).first()
    current = (
        db.query(MapPOI).filter(MapPOI.id == session.current_poi_id).first()
        if session.current_poi_id else None
    )
    target = (
        db.query(MapPOI).filter(MapPOI.id == session.target_poi_id).first()
        if session.target_poi_id else None
    )
    return DispatchSessionOut(
        id=session.id,
        robot_id=session.robot_id,
        robot_name=robot.name if robot else None,
        status=session.status,
        with_rack=bool(session.with_rack) if session.with_rack is not None else True,
        first_poi_id=session.first_poi_id,
        current_poi_id=session.current_poi_id,
        current_poi_name=current.name if current else None,
        target_poi_id=session.target_poi_id,
        target_poi_name=target.name if target else None,
        last_error=session.last_error,
        started_at=session.started_at,
        updated_at=session.updated_at,
        ended_at=session.ended_at,
    )


def _list_available_pois(db: Session, robot_id: Optional[int] = None) -> list[POIBrief]:
    """대상 로봇이 속한 area의 활성 맵에서 'jack' 타입 POI 목록.

    robot_id가 None이면 전체 활성 맵 POI 중 jack 타입을 모아서 반환.
    """
    if robot_id:
        robot = db.query(Robot).filter(Robot.id == robot_id).first()
        if not robot:
            return []
        if robot.area_id:
            active_map = (
                db.query(RobotMap)
                .filter(RobotMap.area_id == int(robot.area_id), RobotMap.is_active == True)
                .order_by(RobotMap.id.desc())
                .first()
            )
        else:
            active_map = (
                db.query(RobotMap)
                .filter(RobotMap.is_active == True)
                .order_by(RobotMap.id.desc())
                .first()
            )
        if not active_map:
            return []
        pois = (
            db.query(MapPOI)
            .filter(
                MapPOI.map_id == active_map.id,
                MapPOI.is_active == True,
                MapPOI.poi_type == "jack",
            )
            .order_by(MapPOI.name.asc())
            .all()
        )
    else:
        # 전체 (status 페이지용)
        pois = (
            db.query(MapPOI)
            .filter(MapPOI.is_active == True, MapPOI.poi_type == "jack")
            .order_by(MapPOI.name.asc())
            .all()
        )
    return [
        POIBrief(id=p.id, name=p.name, poi_type=p.poi_type, world_x=p.world_x, world_y=p.world_y)
        for p in pois
    ]


def _current_area_active_pois(db: Session) -> list[POIBrief]:
    """현재 [메인 적용]된 area의 활성 맵에서 jack POI 목록.

    맵관리에서 적용한 area_id 기준. 다른 층/다른 맵의 POI는 제외한다.
    (콘솔/슬롯 모두 이 목록을 써서 현재 맵 POI만 노출)
    """
    from app.routers import map as map_mod
    area_id = map_mod._current_area_id
    if area_id is None:
        # 파일에서 한번 더 시도 (서버 부팅 직후 등)
        area_id = map_mod._load_persisted_default_area()
    if area_id is None:
        return []

    active_map = (
        db.query(RobotMap)
        .filter(RobotMap.area_id == area_id, RobotMap.is_active == True)
        .order_by(RobotMap.updated_at.desc())
        .first()
    )
    if not active_map:
        return []

    pois = (
        db.query(MapPOI)
        .filter(
            MapPOI.map_id == active_map.id,
            MapPOI.is_active == True,
            MapPOI.poi_type == "jack",
        )
        .order_by(MapPOI.name.asc())
        .all()
    )
    return [
        POIBrief(id=p.id, name=p.name, poi_type=p.poi_type, world_x=p.world_x, world_y=p.world_y)
        for p in pois
    ]


# ── API ──────────────────────────────────────────────────────


@router.post("/start")
def start(body: DispatchStartRequest, db: Session = Depends(get_db)):
    """여러 로봇에 첫 작업 POI를 지정해서 동시 시작."""
    if not body.robots:
        raise HTTPException(status_code=400, detail="시작할 로봇이 없습니다")

    # 사전 검증: POI 중복 / 이미 진행 중인 로봇
    poi_ids: set[int] = set()
    for item in body.robots:
        if item.first_poi_id in poi_ids:
            raise HTTPException(status_code=400, detail="여러 로봇이 같은 첫 POI를 가질 수 없습니다")
        poi_ids.add(item.first_poi_id)
        if dispatch_service.has_active_worker(item.robot_id):
            raise HTTPException(
                status_code=409,
                detail=f"로봇 {item.robot_id} 이미 진행 중인 배차가 있습니다",
            )

    # 다른 활성 세션이 점유 중인 POI와도 충돌 검사
    occupied = dispatch_crud.occupied_poi_ids(db)
    conflict = poi_ids & occupied
    if conflict:
        raise HTTPException(status_code=409, detail=f"점유 중인 POI 충돌: {sorted(conflict)}")

    results = []
    for item in body.robots:
        ok, msg = dispatch_service.start_session(item.robot_id, item.first_poi_id)
        results.append({"robot_id": item.robot_id, "ok": ok, "message": msg})
    return {"results": results}


@router.post("/{robot_id:int}/next")
def next_position(robot_id: int, body: DispatchNextRequest, db: Session = Depends(get_db)):
    # 다른 세션이 점유 중이면 거부
    occupied = dispatch_crud.occupied_poi_ids(db)
    # 자기 세션의 current는 제외 (해제 처리는 워커가 함)
    own = dispatch_crud.get_active_session(db, robot_id)
    if own:
        occupied.discard(own.current_poi_id or 0)
        occupied.discard(own.target_poi_id or 0)
    if body.next_poi_id in occupied:
        raise HTTPException(status_code=409, detail="다른 로봇이 점유 중인 POI 입니다")

    ok, msg = dispatch_service.send_next(robot_id, body.next_poi_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True}


@router.post("/{robot_id:int}/end")
def end(robot_id: int):
    ok, msg = dispatch_service.send_end(robot_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True}


@router.get("/sessions/today", response_model=list[DispatchSessionOut])
def sessions_today(db: Session = Depends(get_db)):
    """오늘 시작된 모든 dispatch 세션 (활성 + 종료된 것 포함).

    관제 대시보드의 KPI / 최근 이벤트 집계용.
    """
    from datetime import datetime, date as _date
    today_start = datetime.combine(_date.today(), datetime.min.time())
    rows = (
        db.query(DispatchSessionModel)
        .filter(DispatchSessionModel.started_at >= today_start)
        .order_by(DispatchSessionModel.id.desc())
        .all()
    )
    return [_session_to_out(r, db) for r in rows]


@router.get("/status", response_model=DispatchStatusOut)
def status(db: Session = Depends(get_db)):
    sessions = dispatch_crud.list_active_sessions(db)
    occupied = sorted(dispatch_crud.occupied_poi_ids(db))
    return DispatchStatusOut(
        sessions=[_session_to_out(s, db) for s in sessions],
        occupied_poi_ids=occupied,
        available_pois=_list_available_pois(db, robot_id=None),
    )


@router.get("/{robot_id:int}/status", response_model=DispatchSessionOut)
def robot_status(robot_id: int, db: Session = Depends(get_db)):
    session = dispatch_crud.get_active_session(db, robot_id)
    if not session:
        raise HTTPException(status_code=404, detail="활성 세션 없음")
    return _session_to_out(session, db)


@router.get("/{robot_id:int}/pois", response_model=list[POIBrief])
def robot_pois(robot_id: int, db: Session = Depends(get_db)):
    """해당 로봇이 갈 수 있는 POI 목록 (자기 area 기준)."""
    return _list_available_pois(db, robot_id=robot_id)


# ── 태블릿 페이지 ────────────────────────────────────────────


# 레거시 로봇 기준 태블릿 라우터(/tablet/{robot_id})는 슬롯 라우터(/tablet/{n})와
# 경로 충돌하므로 제거됨. 호환이 필요한 경우 /tablet/poi/{poi_id}를 사용.


# ══════════════════════════════════════════════════════════
# POI 기준 API (위치별 태블릿 — VESA 신 운영 방식)
# ══════════════════════════════════════════════════════════


def _count_available_robots(db: Session) -> int:
    """실제 호출 가능한 로봇 수.

    조건:
      - is_active=True, ip_address 있음
      - 활성 워커 없음 (=다른 호출에 배정 안 됨)
      - **현재 온라인** (관제와 동일한 라이브 체크 — fetch_all_robots_live)
    """
    robots = db.query(Robot).filter(Robot.is_active == True, Robot.ip_address != None).all()
    # 1) 인메모리 필터 (활성 워커 제외)
    idle_robots = [r for r in robots if not dispatch_service.has_active_worker(r.id)]
    if not idle_robots:
        return 0
    # 2) 라이브 ONLINE 체크 (TTL 캐시 — 배차 선정 로직과 동일)
    try:
        online_ips = dispatch_service.online_ips_cached([r.ip_address for r in idle_robots])
        return sum(1 for r in idle_robots if r.ip_address in online_ips)
    except Exception:
        # 라이브 서비스 자체가 죽었으면 폴백 — 등록된 idle 수 그대로
        return len(idle_robots)


def _poi_status(db: Session, poi_id: int) -> DispatchPOIStatusOut:
    poi = db.query(MapPOI).filter(MapPOI.id == poi_id).first()
    poi_name = poi.name if poi else f"POI #{poi_id}"

    # 1) 이 위치에 "도착해 있는" 세션
    #    - awaiting_next  : 다음 명령 대기
    #    - moving + target=NULL : 도착 직후 잭다운 처리 중 (set_current가 target을 NULL로 비움)
    from sqlalchemy import and_, or_
    arrived = (
        db.query(DispatchSessionModel)
        .filter(
            DispatchSessionModel.current_poi_id == poi_id,
            or_(
                DispatchSessionModel.status == "awaiting_next",
                DispatchSessionModel.status == "awaiting_confirm",
                and_(
                    DispatchSessionModel.status == "moving",
                    DispatchSessionModel.target_poi_id.is_(None),
                ),
            ),
        )
        .order_by(DispatchSessionModel.id.desc())
        .first()
    )
    # 2) 이 위치로 오는 중인 세션 (다음 POI로 이동 중인 prev POI는 자동 제외 — target 매치 안 함)
    heading = (
        db.query(DispatchSessionModel)
        .filter(
            DispatchSessionModel.target_poi_id == poi_id,
            DispatchSessionModel.status.in_(("starting", "picking_up", "moving")),
        )
        .order_by(DispatchSessionModel.id.desc())
        .first()
    )

    session = arrived or heading
    state = "empty"
    if arrived:
        state = "arrived"
    elif heading:
        state = "calling"

    # 예약 상태 (가용 로봇 없어 대기 중) — 로봇이 없을 때만 의미 있음
    reservation = dispatch_crud.get_waiting_reservation(db, poi_id)
    reserved = reservation is not None
    reserved_with_rack = bool(reservation.with_rack) if reservation else None
    if state == "empty" and reserved:
        state = "reserved"

    robot_id = robot_name = robot_ip = battery = None
    target_id = target_name = None
    session_status = None
    with_rack_val: Optional[bool] = None
    if session:
        session_status = session.status
        with_rack_val = bool(session.with_rack) if session.with_rack is not None else True
        target_id = session.target_poi_id
        if target_id:
            tp = db.query(MapPOI).filter(MapPOI.id == target_id).first()
            target_name = tp.name if tp else None
        robot = db.query(Robot).filter(Robot.id == session.robot_id).first()
        if robot:
            robot_id = robot.id
            robot_name = robot.name
            robot_ip = robot.ip_address
            st = db.query(RobotStatus).filter(RobotStatus.robot_id == robot.id).first()
            if st:
                battery = st.battery_level

    # 사용 가능한 다음 위치 (점유 안 된 POI). POI 자기 자신은 제외
    available = _list_available_pois(db, robot_id=robot_id)
    occupied = sorted(dispatch_crud.occupied_poi_ids(db))
    available = [p for p in available if p.id != poi_id]

    return DispatchPOIStatusOut(
        poi_id=poi_id,
        poi_name=poi_name,
        state=state,
        reserved=reserved,
        reserved_with_rack=reserved_with_rack,
        robot_id=robot_id,
        robot_name=robot_name,
        robot_ip=robot_ip,
        robot_battery=battery,
        with_rack=with_rack_val,
        session_status=session_status,
        target_poi_id=target_id,
        target_poi_name=target_name,
        available_pois=available,
        occupied_poi_ids=occupied,
        available_robot_count=_count_available_robots(db),
    )


@router.get("/poi/{poi_id}/status", response_model=DispatchPOIStatusOut)
def poi_status(poi_id: int, db: Session = Depends(get_db)):
    return _poi_status(db, poi_id)


@router.post("/poi/{poi_id}/call", response_model=DispatchCallResult)
def poi_call(poi_id: int, body: DispatchCallRequest | None = None, db: Session = Depends(get_db)):
    """이 POI로 가용 로봇 1대 호출 (배터리 많은 순).

    가용 로봇이 없으면 예약을 생성한다 (reserved=True). 이후 어떤 로봇이
    작업을 종료해 가용해지면 먼저 예약한 위치부터 자동으로 호출된다.

    body.with_rack (기본 True):
      True  → standby에서 렉 픽업 후 이 POI로 이동
      False → 잭 조작 없이 바로 이 POI로 이동
    """
    with_rack = body.with_rack if body is not None else True
    robot_type = body.robot_type if body is not None else None
    ok, msg, robot_id, reserved = dispatch_service.call_to_poi(
        poi_id, robot_type=robot_type, with_rack=with_rack
    )
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    robot_name = None
    if robot_id:
        r = db.query(Robot).filter(Robot.id == robot_id).first()
        robot_name = r.name if r else None
    return DispatchCallResult(
        ok=True,
        message="reserved" if reserved else "ok",
        robot_id=robot_id,
        robot_name=robot_name,
        reserved=reserved,
    )


@router.delete("/poi/{poi_id}/reserve")
def poi_cancel_reservation(poi_id: int):
    """이 위치의 대기 중 예약을 취소."""
    cancelled = dispatch_service.cancel_reservation_at_poi(poi_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="취소할 예약이 없습니다")
    return {"ok": True}


# ══════════════════════════════════════════════════════════
# 콘솔 (범용 단말 — 모든 POI를 한 화면에서 호출/예약/제어)
# ══════════════════════════════════════════════════════════


def _available_robot_counts(db: Session) -> dict:
    """가용 로봇 수를 타입별로 집계 (라이브 ONLINE 체크 1회)."""
    robots = db.query(Robot).filter(Robot.is_active == True, Robot.ip_address != None).all()
    idle = [r for r in robots if not dispatch_service.has_active_worker(r.id)]
    if not idle:
        return {"total": 0, "lifting": 0, "serving": 0}
    # 라이브 ONLINE 체크 — TTL 캐시 사용 (폴링마다 수 초 걸리는 것 방지)
    online_ips = dispatch_service.online_ips_cached([r.ip_address for r in idle])
    online = [r for r in idle if r.ip_address in online_ips]
    return {
        "total": len(online),
        "lifting": sum(1 for r in online if (r.robot_type or "lifting") == "lifting"),
        "serving": sum(1 for r in online if r.robot_type == "serving"),
    }


def _console_status(db: Session) -> ConsoleStatusOut:
    """모든 작업 POI(jack)의 상태를 한 번에 집계. (콘솔 폴링용)

    POI별 상태 계산은 _poi_status 와 동일 규칙이되, 세션/로봇/예약을 일괄
    조회해 N+1 쿼리를 피한다.
    """
    pois = _current_area_active_pois(db)  # 현재 적용 area의 활성 맵 jack POI만

    sessions = (
        db.query(DispatchSessionModel)
        .filter(DispatchSessionModel.status.notin_(("completed", "failed")))
        .all()
    )
    arrived_by_poi: dict[int, DispatchSessionModel] = {}
    heading_by_poi: dict[int, DispatchSessionModel] = {}
    for s in sessions:
        if s.current_poi_id and (
            s.status == "awaiting_next"
            or s.status == "awaiting_confirm"
            or (s.status == "moving" and s.target_poi_id is None)
        ):
            arrived_by_poi[s.current_poi_id] = s
        if s.target_poi_id and s.status in ("starting", "picking_up", "moving"):
            heading_by_poi[s.target_poi_id] = s

    reservations = {r.poi_id: r for r in dispatch_crud.list_waiting_reservations(db)}
    occupied = sorted(dispatch_crud.occupied_poi_ids(db))
    poi_name_by_id = {p.id: p.name for p in pois}

    # "경유지 예약" 배지용 집합 — 미래 목적지(이동 목표 target + 아직 안 간 경유지)만.
    # 로봇이 떠나는 중인 current_poi_id 는 제외 → 출발지가 잘못 "예약"으로 뜨는 것 방지.
    from app.models.dispatch import DispatchWaypoint as _DWp
    reserved_ids: set[int] = set()
    for s in sessions:
        if s.target_poi_id:
            reserved_ids.add(s.target_poi_id)
    _sids = [s.id for s in sessions]
    if _sids:
        for (pid,) in (
            db.query(_DWp.poi_id)
            .filter(_DWp.session_id.in_(_sids), _DWp.status.in_(("pending", "current")))
            .all()
        ):
            if pid:
                reserved_ids.add(pid)

    robot_ids = {s.robot_id for s in sessions if s.robot_id}
    robots: dict[int, Robot] = {}
    statuses: dict[int, RobotStatus] = {}
    if robot_ids:
        robots = {r.id: r for r in db.query(Robot).filter(Robot.id.in_(robot_ids)).all()}
        statuses = {
            st.robot_id: st
            for st in db.query(RobotStatus).filter(RobotStatus.robot_id.in_(robot_ids)).all()
        }

    items: list[POIConsoleItem] = []
    for p in pois:
        arrived = arrived_by_poi.get(p.id)
        heading = heading_by_poi.get(p.id)
        session = arrived or heading

        state = "empty"
        if arrived:
            state = "arrived"
        elif heading:
            state = "calling"

        reservation = reservations.get(p.id)
        reserved = reservation is not None
        reserved_with_rack = bool(reservation.with_rack) if reservation else None
        if state == "empty" and reserved:
            state = "reserved"

        robot_id = robot_name = robot_ip = battery = None
        with_rack_val = session_status = target_id = target_name = None
        can_confirm = False
        next_poi_name = None
        is_last = False
        if session:
            session_status = session.status
            with_rack_val = bool(session.with_rack) if session.with_rack is not None else True
            target_id = session.target_poi_id
            target_name = poi_name_by_id.get(target_id) if target_id else None
            r = robots.get(session.robot_id)
            if r:
                robot_id = r.id
                robot_name = r.name
                robot_ip = r.ip_address
                st = statuses.get(r.id)
                if st:
                    battery = st.battery_level
            # 경유지 진행 중이면 콘솔에서도 [확인] 가능 — 다음 경유지/마지막 여부 계산
            if session.status == "awaiting_confirm":
                can_confirm = True
                wps = dispatch_crud.list_waypoints(db, session.id)
                cur_seq = next((w.seq for w in wps if w.status == "current"), None)
                nxt = next(
                    (w for w in wps if w.status == "pending"
                     and (cur_seq is None or w.seq > cur_seq)),
                    None,
                )
                if nxt:
                    next_poi_name = poi_name_by_id.get(nxt.poi_id)
                    # 콘솔 POI 목록에 없는(다른 층) 이름 폴백
                    if next_poi_name is None and nxt.poi_id:
                        _np = db.query(MapPOI).filter(MapPOI.id == nxt.poi_id).first()
                        next_poi_name = _np.name if _np else None
                else:
                    is_last = True

        items.append(POIConsoleItem(
            poi_id=p.id, poi_name=p.name, state=state,
            robot_id=robot_id, robot_name=robot_name, robot_ip=robot_ip,
            robot_battery=battery, with_rack=with_rack_val,
            session_status=session_status, target_poi_id=target_id,
            target_poi_name=target_name,
            reserved=reserved, reserved_with_rack=reserved_with_rack,
            can_confirm=can_confirm, next_poi_name=next_poi_name, is_last=is_last,
        ))

    counts = _available_robot_counts(db)
    robot_items = _console_robot_items(db, sessions, poi_name_by_id)
    return ConsoleStatusOut(
        pois=items,
        robots=robot_items,
        occupied_poi_ids=occupied,
        reserved_poi_ids=sorted(reserved_ids),
        available_robot_count=counts["total"],
        available_lifting_count=counts["lifting"],
        available_serving_count=counts["serving"],
    )


def _console_robot_items(db: Session, sessions: list, poi_name_by_id: dict) -> list[ConsoleRobotItem]:
    """콘솔 사이드바용 로봇 목록 — 등록된 활성 로봇 전체 + 상태/배터리/원격제어 IP.

    online 판정은 배차 선정과 동일한 라이브 TTL 캐시를 재사용한다.
    """
    robots = (
        db.query(Robot)
        .filter(Robot.is_active == True)
        .order_by(Robot.name.asc())
        .all()
    )
    if not robots:
        return []

    ips = [r.ip_address for r in robots if r.ip_address]
    try:
        online_ips = dispatch_service.online_ips_cached(ips)
    except Exception:
        online_ips = set(ips)  # 라이브 서비스 장애 시 온라인 취급

    # 배터리 일괄 조회
    ids = [r.id for r in robots]
    statuses = {
        st.robot_id: st
        for st in db.query(RobotStatus).filter(RobotStatus.robot_id.in_(ids)).all()
    } if ids else {}
    # 로봇별 현재 세션 (진행 상태/위치 요약)
    session_by_robot = {s.robot_id: s for s in sessions if s.robot_id}

    out: list[ConsoleRobotItem] = []
    for r in robots:
        st = statuses.get(r.id)
        sess = session_by_robot.get(r.id)
        cur_name = None
        if sess:
            cur = sess.current_poi_id or sess.target_poi_id
            cur_name = poi_name_by_id.get(cur) if cur else None
        out.append(ConsoleRobotItem(
            robot_id=r.id,
            robot_name=r.name,
            robot_ip=r.ip_address,
            robot_type=r.robot_type or "lifting",
            online=(r.ip_address in online_ips) if r.ip_address else False,
            battery=st.battery_level if st else None,
            busy=dispatch_service.has_active_worker(r.id),
            session_status=sess.status if sess else None,
            current_poi_name=cur_name,
        ))
    return out


@router.get("/console/status", response_model=ConsoleStatusOut)
def console_status(db: Session = Depends(get_db)):
    """콘솔 폴링 — 모든 POI 상태 + 점유 목록 + 가용 로봇 수."""
    return _console_status(db)


@router.get("/console", response_class=HTMLResponse)
def console_page():
    """범용 콘솔 페이지 (모든 POI를 한 화면에서 호출/예약/다음/종료)."""
    if not _CONSOLE_TEMPLATE.exists():
        return HTMLResponse(content="<h1>console template not found</h1>", status_code=500)
    return HTMLResponse(content=_CONSOLE_TEMPLATE.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════
# 경유지 경로 + 로봇 부착 태블릿
# ══════════════════════════════════════════════════════════


@router.post("/poi/{poi_id:int}/route")
def poi_route(poi_id: int, body: DispatchRouteRequest, db: Session = Depends(get_db)):
    """이 위치에 도착한 로봇에 경유지 경로(순서)를 등록하고 출발시킨다.

    이후 각 경유지 도착마다 로봇 부착 태블릿의 [확인]으로 다음 진행, 마지막 확인 후 자동 종료.
    """
    session = dispatch_service.get_session_at_poi(poi_id)
    if not session:
        raise HTTPException(status_code=404, detail="이 위치에 대기 중인 로봇이 없습니다")
    if not body.waypoints:
        raise HTTPException(status_code=400, detail="경유지를 1개 이상 지정하세요")

    # 입력 정리: 출발 위치 자신 제외 + 중복 제거(순서 유지)
    # (같은 곳을 연속 방문하거나 제자리 이동하는 비정상 동작 방지)
    seen: set[int] = set()
    waypoints: list[int] = []
    for w in body.waypoints:
        if w == poi_id or w in seen:
            continue
        seen.add(w)
        waypoints.append(w)
    if not waypoints:
        raise HTTPException(
            status_code=400,
            detail="유효한 경유지가 없습니다 (출발 위치이거나 중복만 지정됨)",
        )

    # 점유 검증: 경유지가 다른 로봇 점유 POI면 거부
    occupied = dispatch_crud.occupied_poi_ids(db)
    occupied.discard(poi_id)  # 자기 현재 위치
    for wp in dispatch_crud.list_waypoints(db, session.id):
        if wp.poi_id:
            occupied.discard(wp.poi_id)  # 자기 세션의 기존 경유지(새 경로로 덮어씀)
    conflict = sorted({w for w in waypoints if w in occupied})
    if conflict:
        raise HTTPException(status_code=409, detail=f"점유 중인 경유지가 있습니다: {conflict}")

    ok, msg = dispatch_service.send_route(session.robot_id, waypoints)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "robot_id": session.robot_id}


def _robot_tablet_status(db: Session, robot_id: int) -> RobotTabletStatus:
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    robot_name = robot.name if robot else None

    session = dispatch_crud.get_active_session(db, robot_id)
    if not session:
        return RobotTabletStatus(robot_id=robot_id, robot_name=robot_name, active=False)

    wps = dispatch_crud.list_waypoints(db, session.id)
    poi_ids = {w.poi_id for w in wps if w.poi_id}
    if session.current_poi_id:
        poi_ids.add(session.current_poi_id)
    poi_names: dict[int, str] = {}
    if poi_ids:
        for p in db.query(MapPOI).filter(MapPOI.id.in_(poi_ids)).all():
            poi_names[p.id] = p.name

    current_poi_id = session.current_poi_id
    current_poi_name = poi_names.get(current_poi_id) if current_poi_id else None

    # 현재 진행 중(current) 경유지의 다음 pending = [확인] 시 갈 곳
    cur_seq = next((w.seq for w in wps if w.status == "current"), None)
    next_wp = next(
        (w for w in wps if w.status == "pending" and (cur_seq is None or w.seq > cur_seq)),
        None,
    )
    next_poi_id = next_wp.poi_id if next_wp else None
    next_poi_name = poi_names.get(next_poi_id) if next_poi_id else None

    can_confirm = (session.status == "awaiting_confirm")
    is_last = can_confirm and next_wp is None

    return RobotTabletStatus(
        robot_id=robot_id,
        robot_name=robot_name,
        active=True,
        session_status=session.status,
        current_poi_id=current_poi_id,
        current_poi_name=current_poi_name,
        next_poi_id=next_poi_id,
        next_poi_name=next_poi_name,
        is_last=is_last,
        can_confirm=can_confirm,
        waypoints=[
            WaypointBrief(seq=w.seq, poi_id=w.poi_id,
                          poi_name=poi_names.get(w.poi_id), status=w.status)
            for w in wps
        ],
    )


@router.get("/robot/{robot_id:int}/tablet-status", response_model=RobotTabletStatus)
def robot_tablet_status(robot_id: int, db: Session = Depends(get_db)):
    """로봇 부착 태블릿 폴링 — 현재/다음 경유지 + 확인 가능 여부."""
    return _robot_tablet_status(db, robot_id)


@router.post("/robot/{robot_id:int}/confirm")
def robot_confirm(robot_id: int):
    """로봇 부착 태블릿 [확인] — 다음 경유지로 진행 (마지막이면 종료로)."""
    ok, msg = dispatch_service.send_confirm(robot_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True}


@router.post("/robot/{robot_id:int}/end")
def robot_end(robot_id: int):
    """로봇 부착 태블릿 [작업 종료] — 즉시 종료 시퀀스(복귀+충전소)."""
    ok, msg = dispatch_service.send_end(robot_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True}


@router.get("/robot-tablet/{robot_id:int}", response_class=HTMLResponse)
def robot_tablet_page(robot_id: int, db: Session = Depends(get_db)):
    """로봇 부착 태블릿 페이지 (현재/다음 경유지 + [확인]/[종료])."""
    if not _ROBOT_TABLET_TEMPLATE.exists():
        return HTMLResponse(content="<h1>robot tablet template not found</h1>", status_code=500)
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    html = _ROBOT_TABLET_TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("{{ROBOT_ID}}", str(robot_id))
    html = html.replace("{{ROBOT_NAME}}", (robot.name if robot else f"로봇 #{robot_id}"))
    return HTMLResponse(content=html)


@router.post("/poi/{poi_id}/next")
def poi_send_next(poi_id: int, body: DispatchNextRequest, db: Session = Depends(get_db)):
    """이 위치에 도착한 로봇을 비어있는 다음 POI로 보냄."""
    session = dispatch_service.get_session_at_poi(poi_id)
    if not session:
        raise HTTPException(status_code=404, detail="이 위치에 대기 중인 로봇이 없습니다")

    # 점유 검증
    occupied = dispatch_crud.occupied_poi_ids(db)
    occupied.discard(poi_id)  # 자기 위치 (출발)
    if body.next_poi_id in occupied:
        raise HTTPException(status_code=409, detail="다른 로봇이 점유 중인 위치입니다")

    ok, msg = dispatch_service.send_next(session.robot_id, body.next_poi_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "robot_id": session.robot_id}


@router.post("/poi/{poi_id}/end")
def poi_send_end(poi_id: int):
    """이 위치에 있는 로봇을 종료 시퀀스로 (standby → 잭다운 → 충전소)."""
    session = dispatch_service.get_session_at_poi(poi_id)
    if not session:
        raise HTTPException(status_code=404, detail="이 위치에 대기 중인 로봇이 없습니다")
    ok, msg = dispatch_service.send_end(session.robot_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "robot_id": session.robot_id}


@router.get("/tablet/poi/{poi_id}", response_class=HTMLResponse)
def tablet_poi_page(poi_id: int, db: Session = Depends(get_db)):
    """[호환] 위치(POI ID)별 태블릿 페이지."""
    poi = db.query(MapPOI).filter(MapPOI.id == poi_id).first()
    poi_name = poi.name if poi else f"POI #{poi_id}"
    return _render_tablet_html(slot_number=0, poi_id=poi_id, poi_name=poi_name)


# ══════════════════════════════════════════════════════════
# 슬롯 (1, 2, 3 …) — 메인 운영 방식
# ══════════════════════════════════════════════════════════


def _render_tablet_html(slot_number: int, poi_id: int, poi_name: str,
                         alias: str = "") -> HTMLResponse:
    if not _TABLET_TEMPLATE.exists():
        return HTMLResponse(content="<h1>tablet template not found</h1>", status_code=500)
    html = _TABLET_TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("{{SLOT_NUMBER}}", str(slot_number))
    html = html.replace("{{POI_ID}}", str(poi_id))
    html = html.replace("{{POI_NAME}}", poi_name or "")
    html = html.replace("{{SLOT_ALIAS}}", alias or "")
    html = html.replace("{{ROBOT_ID}}", "0")
    html = html.replace("{{ROBOT_NAME}}", "")
    html = html.replace("{{MODE}}", "poi" if slot_number == 0 else "slot")
    return HTMLResponse(content=html)


@router.get("/tablet/{slot_number}", response_class=HTMLResponse)
def tablet_slot_page(slot_number: int, db: Session = Depends(get_db)):
    """슬롯 번호로 접속. 매핑 없으면 페이지 내에서 설정 UI 표시."""
    slot = dispatch_crud.get_slot(db, slot_number)
    if not slot:
        # 매핑 없음 — 설정 모드 (POI_ID=0)
        return _render_tablet_html(slot_number=slot_number, poi_id=0,
                                    poi_name=f"슬롯 {slot_number}")
    poi = db.query(MapPOI).filter(MapPOI.id == slot.poi_id).first()
    poi_name = (slot.alias or (poi.name if poi else f"POI #{slot.poi_id}"))
    return _render_tablet_html(slot_number=slot_number,
                                poi_id=slot.poi_id,
                                poi_name=poi_name,
                                alias=slot.alias or "")


@router.get("/slots", response_model=list[TabletSlotOut])
def list_slots(db: Session = Depends(get_db)):
    rows = dispatch_crud.list_slots(db)
    out = []
    for s in rows:
        poi = db.query(MapPOI).filter(MapPOI.id == s.poi_id).first()
        out.append(TabletSlotOut(
            slot_number=s.slot_number, poi_id=s.poi_id,
            poi_name=(poi.name if poi else None), alias=s.alias,
        ))
    return out


@router.get("/slots/available-pois", response_model=list[POIBrief])
def slots_available_pois(db: Session = Depends(get_db)):
    """슬롯에 매핑 가능한 POI 목록 (작업 위치 = jack 타입).

    맵관리에서 [메인 적용]된 area_id의 활성 맵의 POI만 반환.
    맵 적용을 바꾸면 자동으로 여기 결과도 바뀜.

    주의: /slots/{slot_number}보다 먼저 정의돼야 한다 (FastAPI 라우팅 순서).
    """
    return _current_area_active_pois(db)


@router.get("/slots/{slot_number}", response_model=TabletSlotOut)
def get_slot(slot_number: int, db: Session = Depends(get_db)):
    s = dispatch_crud.get_slot(db, slot_number)
    if not s:
        raise HTTPException(status_code=404, detail="슬롯 매핑 없음")
    poi = db.query(MapPOI).filter(MapPOI.id == s.poi_id).first()
    return TabletSlotOut(
        slot_number=s.slot_number, poi_id=s.poi_id,
        poi_name=(poi.name if poi else None), alias=s.alias,
    )


@router.put("/slots/{slot_number}", response_model=TabletSlotOut)
def set_slot(slot_number: int, body: TabletSlotIn, db: Session = Depends(get_db)):
    # POI 존재 검증
    poi = db.query(MapPOI).filter(MapPOI.id == body.poi_id, MapPOI.is_active == True).first()
    if not poi:
        raise HTTPException(status_code=400, detail="유효하지 않은 POI")
    s = dispatch_crud.upsert_slot(db, slot_number, body.poi_id, body.alias)
    return TabletSlotOut(
        slot_number=s.slot_number, poi_id=s.poi_id,
        poi_name=poi.name, alias=s.alias,
    )


@router.delete("/slots/{slot_number}")
def remove_slot(slot_number: int, db: Session = Depends(get_db)):
    ok = dispatch_crud.delete_slot(db, slot_number)
    if not ok:
        raise HTTPException(status_code=404, detail="슬롯 매핑 없음")
    return {"ok": True}
