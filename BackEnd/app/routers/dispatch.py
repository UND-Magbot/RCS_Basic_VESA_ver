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
)
from app.crud import dispatch as dispatch_crud
from app.services import dispatch_service
from app.models.dispatch import DispatchSession as DispatchSessionModel
from app.models.robot import Robot as RobotModel, RobotStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dispatch", tags=["인터랙티브 배차 (VESA)"])

_TABLET_TEMPLATE = Path(__file__).parent.parent / "templates" / "dispatch_tablet.html"


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


@router.post("/{robot_id}/next")
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


@router.post("/{robot_id}/end")
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


@router.get("/{robot_id}/status", response_model=DispatchSessionOut)
def robot_status(robot_id: int, db: Session = Depends(get_db)):
    session = dispatch_crud.get_active_session(db, robot_id)
    if not session:
        raise HTTPException(status_code=404, detail="활성 세션 없음")
    return _session_to_out(session, db)


@router.get("/{robot_id}/pois", response_model=list[POIBrief])
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
    # 2) 라이브 ONLINE 체크 (배차 선정 로직과 동일)
    try:
        from app.robot_api.robot_live_service import fetch_all_robots_live
        from app.routers.robot import DEFAULT_SECRET
        live = fetch_all_robots_live([
            {"ip": r.ip_address, "secret": DEFAULT_SECRET} for r in idle_robots
        ])
        online_ips = {it.get("IP") for it in live.get("items", []) if it.get("ONLINE") == "Online"}
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

    body.with_rack (기본 True):
      True  → standby에서 렉 픽업 후 이 POI로 이동
      False → 잭 조작 없이 바로 이 POI로 이동
    """
    with_rack = body.with_rack if body is not None else True
    ok, msg, robot_id = dispatch_service.call_to_poi(poi_id, with_rack=with_rack)
    robot_name = None
    if ok and robot_id:
        r = db.query(Robot).filter(Robot.id == robot_id).first()
        robot_name = r.name if r else None
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return DispatchCallResult(ok=True, message="ok", robot_id=robot_id, robot_name=robot_name)


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
    # map 라우터 모듈에서 현재 적용된 area_id 가져오기 (변경 추적 위해 모듈 속성 접근)
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
        POIBrief(id=p.id, name=p.name, poi_type=p.poi_type,
                 world_x=p.world_x, world_y=p.world_y)
        for p in pois
    ]


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
