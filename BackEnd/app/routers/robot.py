import logging

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from app.robot_api.robot_live_service import fetch_all_robots_live

from app.database import get_db
from app.models.robot import Robot
from app.models.map import RobotMap, MapPOI
from app.schemas.robot import (
    RobotCreate,
    RobotUpdate,
    RobotResponse,
    RobotListResponse,
    RobotStatusUpdate,
    RobotStatusResponse,
    MinBatteryUpdate,
)
from app.crud.robot import (
    create_robot,
    get_robot,
    get_robots,
    update_robot,
    delete_robot,
    update_robot_status,
    get_robot_status,
    get_min_battery_by_sn,
    update_min_battery_by_sn,
)

from app.crud.activity_log import log_activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/robots", tags=["로봇 관리"])


# ── 로봇 시크릿 (AutoXing 기본값) ──
DEFAULT_SECRET = "19a11878aaab420fba94577ce3620dce"


def _get_robot_list(db: Session) -> list[dict]:
    """DB에서 활성 로봇 IP 목록 → fetch_all_robots_live용 리스트"""
    robots = db.query(Robot).filter(Robot.is_active == True, Robot.ip_address != None).all()
    return [{"ip": r.ip_address, "secret": DEFAULT_SECRET} for r in robots if r.ip_address]

@router.get("/live")
def api_get_robots_live(db: Session = Depends(get_db)):
    """DB 로봇 목록을 기반으로 실시간 API 정보를 병합하여 반환.

    1차: DB robots 테이블에서 is_active인 로봇 목록
    2차: 실시간 API로 RUNSTATE, ONLINE, SIGNAL, POWER 등 오버레이
    3차: DB에 없지만 API에서 새로 발견된 로봇도 추가
    """
    # ── 1차: DB 로봇 목록 가져오기 ──
    db_robots = db.query(Robot).filter(Robot.is_active == True).all()

    # DB 로봇 → 기본 아이템 생성 (SN 기준 매핑)
    sn_to_item: dict[str, dict] = {}
    ip_to_sn: dict[str, str] = {}
    ip_to_robot_id: dict[str, int] = {}

    items = []
    for r in db_robots:
        item = {
            "ID": r.id,
            "IP": r.ip_address or "",
            "SN": r.serial_number,
            "ROBOTNAME": r.name,
            "MODEL": r.model or "-",
            "NICKNAME": None,
            "AXBOT_VERSION": None,
            "PLATFORM": None,
            "RUNSTATE": "OFFLINE",
            "ONLINE": "Offline",
            "SIGNAL": "N/A",
            "POWER(%)": "-",
        }
        items.append(item)
        sn_to_item[r.serial_number] = item
        if r.ip_address:
            ip_to_sn[r.ip_address] = r.serial_number
            ip_to_robot_id[r.ip_address] = r.id

    # ── 2차: 실시간 API로 상태 오버레이 ──
    live = fetch_all_robots_live(_get_robot_list(db))
    for live_item in live.get("items", []):
        live_ip = live_item.get("IP", "")
        live_sn = live_item.get("SN", "")

        # IP로 DB 로봇 매칭
        matched_sn = ip_to_sn.get(live_ip)
        # SN으로도 매칭 시도
        if not matched_sn and live_sn and live_sn != "N/A":
            matched_sn = live_sn if live_sn in sn_to_item else None

        if matched_sn and matched_sn in sn_to_item:
            # DB에 있는 로봇 → 실시간 정보 오버레이
            target = sn_to_item[matched_sn]
            if live_item.get("ROBOTNAME") and live_item["ROBOTNAME"] != "N/A":
                target["ROBOTNAME"] = live_item["ROBOTNAME"]
            if live_item.get("MODEL") and live_item["MODEL"] != "N/A":
                target["MODEL"] = live_item["MODEL"]
            target["NICKNAME"] = live_item.get("NICKNAME")
            target["AXBOT_VERSION"] = live_item.get("AXBOT_VERSION")
            target["PLATFORM"] = live_item.get("PLATFORM")
            target["RUNSTATE"] = live_item.get("RUNSTATE", "OFFLINE")
            target["ONLINE"] = live_item.get("ONLINE", "Offline")
            target["SIGNAL"] = live_item.get("SIGNAL", "N/A")
            target["POWER(%)"] = live_item.get("POWER(%)", "-")
            if not target["IP"] and live_ip:
                target["IP"] = live_ip

        else:
            # DB에 없는 새 로봇 → 리스트에 추가
            if live_sn and live_sn != "N/A":
                new_item = {
                    "ID": None,
                    "IP": live_ip,
                    "SN": live_sn,
                    "ROBOTNAME": live_item.get("ROBOTNAME", live_sn),
                    "MODEL": live_item.get("MODEL", "-"),
                    "NICKNAME": live_item.get("NICKNAME"),
                    "AXBOT_VERSION": live_item.get("AXBOT_VERSION"),
                    "PLATFORM": live_item.get("PLATFORM"),
                    "RUNSTATE": live_item.get("RUNSTATE", "OFFLINE"),
                    "ONLINE": live_item.get("ONLINE", "Offline"),
                    "SIGNAL": live_item.get("SIGNAL", "N/A"),
                    "POWER(%)": live_item.get("POWER(%)", "-"),
                }
                items.append(new_item)
                sn_to_item[live_sn] = new_item

    items.sort(key=lambda x: str(x.get("IP", "")))
    return {"total": len(items), "items": items}


@router.post("/sync-live")
def api_sync_live_robots(db: Session = Depends(get_db)):
    """라이브 로봇 정보를 DB robots 테이블에 동기화 (upsert by serial_number)"""
    live = fetch_all_robots_live(_get_robot_list(db))
    items = live.get("items", [])

    created = 0
    updated = 0
    skipped = 0
    synced = []

    for item in items:
        sn = item.get("SN", "")
        if not sn or sn == "N/A":
            skipped += 1
            continue

        ip = item.get("IP", "")
        name = item.get("ROBOTNAME", "") or sn
        model = item.get("MODEL", "")

        existing = db.query(Robot).filter(Robot.serial_number == sn).first()

        if existing:
            if name and name != "N/A":
                existing.name = name
            if model and model != "N/A":
                existing.model = model
            if ip:
                existing.ip_address = ip
            existing.is_active = True
            updated += 1
            synced.append({"sn": sn, "name": name, "action": "updated"})
        else:
            new_robot = Robot(
                name=name if name and name != "N/A" else sn,
                serial_number=sn,
                model=model if model and model != "N/A" else None,
                ip_address=ip or None,
            )
            db.add(new_robot)
            created += 1
            synced.append({"sn": sn, "name": name, "action": "created"})

    db.commit()
    logger.info(f"sync-live: created={created}, updated={updated}, skipped={skipped}")
    log_activity("robot", "robot_sync",
                 f"로봇 동기화 완료: 생성 {created}, 갱신 {updated}, 건너뜀 {skipped}",
                 source="api_sync_live_robots")

    return {
        "message": f"동기화 완료: 생성 {created}, 갱신 {updated}, 건너뜀 {skipped}",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "synced": synced,
    }


@router.post("/register-by-ip", status_code=201)
def api_register_by_ip(ip: str = Query(...), db: Session = Depends(get_db)):
    """IP 입력으로 로봇 자동 등록 — 로봇 API에서 SN/이름/모델을 가져와 DB에 저장"""
    from app.robot_api.robot_live_service import fetch_robot_live

    # 이미 등록된 IP인지 확인
    existing = db.query(Robot).filter(Robot.ip_address == ip, Robot.is_active == True).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"이미 등록된 로봇입니다 (IP: {ip}, 이름: {existing.name})")

    # 로봇에서 정보 가져오기
    live = fetch_robot_live(ip, DEFAULT_SECRET)
    if live.get("ONLINE") != "Online":
        raise HTTPException(status_code=502, detail=f"로봇에 연결할 수 없습니다 (IP: {ip})")

    sn = live.get("SN", "")
    if not sn or sn == "N/A":
        raise HTTPException(status_code=502, detail=f"로봇 SN을 가져올 수 없습니다 (IP: {ip})")

    # SN 중복 확인
    existing_sn = db.query(Robot).filter(Robot.serial_number == sn, Robot.is_active == True).first()
    if existing_sn:
        raise HTTPException(status_code=409, detail=f"이미 등록된 SN입니다 ({sn})")

    name = live.get("ROBOTNAME", "") or sn
    model = live.get("MODEL", "")

    new_robot = Robot(
        name=name if name != "N/A" else sn,
        serial_number=sn,
        model=model if model and model != "N/A" else None,
        ip_address=ip,
    )
    db.add(new_robot)
    db.commit()
    db.refresh(new_robot)

    log_activity("robot", "robot_create",
                 f"로봇 등록 (IP 자동): {new_robot.name} (SN: {sn}, IP: {ip})",
                 source="api_register_by_ip")

    return {
        "id": new_robot.id,
        "name": new_robot.name,
        "serial_number": sn,
        "model": new_robot.model,
        "ip_address": ip,
        "message": f"로봇 등록 완료: {new_robot.name}",
    }


@router.post("", response_model=RobotResponse, status_code=201)
def api_create_robot(data: RobotCreate, db: Session = Depends(get_db)):
    """RB-01 로봇 등록"""
    result = create_robot(db, data)
    log_activity("robot", "robot_create",
                 f"로봇 등록: {data.name} (SN: {data.serial_number})",
                 source="api_create_robot")
    return result


@router.get("", response_model=RobotListResponse)
def api_get_robots(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    business_id: str | None = Query(None),
    area_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """로봇 목록 조회"""
    items = get_robots(db, skip=skip, limit=limit, business_id=business_id, area_id=area_id)
    return RobotListResponse(total=len(items), items=items)


# ── 최소 배터리 (SN 기반) ──

@router.get("/sn/{sn}/min-battery")
def api_get_min_battery(sn: str, db: Session = Depends(get_db)):
    """SN 기반 최소 배터리 조회"""
    return get_min_battery_by_sn(db, sn)


@router.patch("/sn/{sn}/min-battery")
def api_update_min_battery(sn: str, data: MinBatteryUpdate, db: Session = Depends(get_db)):
    """SN 기반 최소 배터리 수정"""
    result = update_min_battery_by_sn(db, sn, data)
    changes = [f"최소배터리={data.min_battery}%"]
    if data.charging_id is not None:
        changes.append(f"충전소 변경(ID={data.charging_id})")
    if data.standby_id is not None:
        changes.append(f"귀환장소 변경(ID={data.standby_id})")
    log_activity("robot", "battery_setting",
                 f"로봇 {sn} 설정 변경 — {', '.join(changes)}",
                 source="api_update_min_battery")
    return result


@router.get("/sn/{sn}/charging-pois")
def api_get_charging_pois(sn: str, db: Session = Depends(get_db)):
    """SN 기반 충전소 POI 조회 — 로봇이 속한 영역의 맵에서 충전소 검색.
    area_id가 없으면 전체 활성 맵에서 충전소를 검색한다.
    """
    robot = db.query(Robot).filter(Robot.serial_number == sn, Robot.is_active == True).first()
    if not robot:
        return []

    # area_id가 있으면 해당 영역의 맵만, 없으면 전체 활성 맵
    if robot.area_id:
        try:
            area_id = int(robot.area_id)
        except (ValueError, TypeError):
            area_id = None
    else:
        area_id = None

    if area_id is not None:
        maps = (
            db.query(RobotMap)
            .filter(RobotMap.area_id == area_id, RobotMap.is_active == True)
            .all()
        )
    else:
        maps = db.query(RobotMap).filter(RobotMap.is_active == True).all()

    if not maps:
        return []

    result = []
    for m in maps:
        pois = (
            db.query(MapPOI)
            .filter(
                MapPOI.map_id == m.id,
                MapPOI.poi_type == "charging",
                MapPOI.is_active == True,
            )
            .all()
        )
        for poi in pois:
            result.append({"id": poi.id, "name": poi.name})

    return result


@router.get("/sn/{sn}/standby-pois")
def api_get_standby_pois(sn: str, db: Session = Depends(get_db)):
    """SN 기반 대기장소 POI 조회 — 전체 활성 맵에서 standby 타입 POI 검색"""
    robot = db.query(Robot).filter(Robot.serial_number == sn, Robot.is_active == True).first()
    if not robot:
        return []

    if robot.area_id:
        try:
            area_id = int(robot.area_id)
        except (ValueError, TypeError):
            area_id = None
    else:
        area_id = None

    if area_id is not None:
        maps = (
            db.query(RobotMap)
            .filter(RobotMap.area_id == area_id, RobotMap.is_active == True)
            .all()
        )
    else:
        maps = db.query(RobotMap).filter(RobotMap.is_active == True).all()

    if not maps:
        return []

    result = []
    for m in maps:
        pois = (
            db.query(MapPOI)
            .filter(
                MapPOI.map_id == m.id,
                MapPOI.poi_type == "standby",
                MapPOI.is_active == True,
            )
            .all()
        )
        for poi in pois:
            result.append({"id": poi.id, "name": poi.name})

    return result


@router.get("/job-status")
def api_get_all_job_status():
    """모든 로봇의 현재 작업 상태 조회"""
    from app.services.jack_service import get_all_job_status
    return get_all_job_status()


@router.get("/job-status/{robot_ip}")
def api_get_job_status(robot_ip: str):
    """특정 로봇의 현재 작업 상태 조회"""
    from app.services.jack_service import get_job_status
    status = get_job_status(robot_ip)
    if not status:
        return {"status": "idle"}
    return status


@router.get("/{robot_id}", response_model=RobotResponse)
def api_get_robot(robot_id: int, db: Session = Depends(get_db)):
    """로봇 단건 조회"""
    return get_robot(db, robot_id)


@router.put("/{robot_id}", response_model=RobotResponse)
def api_update_robot(robot_id: int, data: RobotUpdate, db: Session = Depends(get_db)):
    """RB-02 로봇 정보 수정"""
    result = update_robot(db, robot_id, data)
    log_activity("robot", "robot_update",
                 f"로봇 정보 수정: {result.name} (SN: {result.serial_number})",
                 robot_id=robot_id, source="api_update_robot")
    return result


@router.delete("/{robot_id}")
def api_delete_robot(robot_id: int, db: Session = Depends(get_db)):
    """RB-03 로봇 삭제 (Soft Delete)"""
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    robot_label = f"{robot.name} (SN: {robot.serial_number})" if robot else f"ID:{robot_id}"
    result = delete_robot(db, robot_id)
    log_activity("robot", "robot_delete",
                 f"로봇 삭제: {robot_label}",
                 robot_id=robot_id, source="api_delete_robot")
    return result


# ── 로봇 상태 관련 ──

@router.get("/{robot_id}/status", response_model=RobotStatusResponse)
def api_get_robot_status(robot_id: int, db: Session = Depends(get_db)):
    """RB-05 로봇 상태 조회"""
    return get_robot_status(db, robot_id)


@router.put("/{robot_id}/status", response_model=RobotStatusResponse)
def api_update_robot_status(
    robot_id: int, data: RobotStatusUpdate, db: Session = Depends(get_db)
):
    """RB-04 로봇 상태 수집/업데이트
    ※ AutoXing SDK/API 연동 지점: 이 엔드포인트로 로봇 상태 데이터를 전송합니다.
    """
    return update_robot_status(db, robot_id, data)


@router.post("/cancel-move/{robot_ip}")
def api_cancel_robot_move(robot_ip: str):
    """로봇의 현재 이동 취소"""
    import requests as http_req
    try:
        r = http_req.patch(
            f"http://{robot_ip}:8090/chassis/moves/current",
            json={"state": "cancelled"}, timeout=5
        )
        return {"message": "이동 취소 완료", "status": r.status_code}
    except Exception as e:
        return {"message": f"취소 실패: {e}", "status": 500}


@router.get("/target/{robot_ip}")
def api_get_robot_target(robot_ip: str):
    """로봇의 현재 이동 목표 조회"""
    import requests as http_req
    try:
        r = http_req.get(f"http://{robot_ip}:8090/chassis/moves/current", timeout=3)
        if r.status_code == 404:
            return {"state": "idle", "target_x": None, "target_y": None}
        data = r.json()
        return {
            "state": data.get("state", ""),
            "type": data.get("type", ""),
            "target_x": data.get("target_x"),
            "target_y": data.get("target_y"),
        }
    except Exception:
        return {"state": "error", "target_x": None, "target_y": None}


# ── 원격 제어 API ──

@router.post("/remote/control-mode/{robot_ip}")
def api_set_control_mode(robot_ip: str, body: dict):
    """로봇 제어 모드 변경 (auto/manual/remote)"""
    import requests as req
    mode = body.get("mode", "auto")
    try:
        r = req.post(
            f"http://{robot_ip}:8090/services/wheel_control/set_control_mode",
            json={"control_mode": mode},
            timeout=5,
        )
        return {"status": r.status_code, "mode": mode}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/remote/twist/{robot_ip}")
def api_send_twist(robot_ip: str, body: dict):
    """WebSocket /twist 명령을 프록시로 전송"""
    import websocket
    lv = body.get("linear_velocity", 0)
    av = body.get("angular_velocity", 0)
    try:
        ws = websocket.create_connection(
            f"ws://{robot_ip}:8090/ws/v2/topics", timeout=3
        )
        ws.send(
            __import__("json").dumps({
                "topic": "/twist",
                "linear_velocity": lv,
                "angular_velocity": av,
            })
        )
        ws.close()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/remote/cancel-move/{robot_ip}")
def api_cancel_move(robot_ip: str):
    """현재 이동 취소"""
    import requests as req
    try:
        r = req.patch(
            f"http://{robot_ip}:8090/chassis/moves/current",
            json={"state": "cancelled"},
            timeout=5,
        )
        return {"status": r.status_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/speed/{robot_ip}")
def api_get_speed(robot_ip: str):
    """로봇 속도 조회"""
    import requests as req
    try:
        r = req.get(f"http://{robot_ip}:8090/robot-params", timeout=5)
        params = r.json()
        return {
            "max_forward_velocity": params.get("/wheel_control/max_forward_velocity", 1.2),
            "max_backward_velocity": abs(params.get("/wheel_control/max_backward_velocity", -0.5)),
            "max_angular_velocity": params.get("/wheel_control/max_angular_velocity", 1.2),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/speed/{robot_ip}")
def api_set_speed(robot_ip: str, body: dict):
    """로봇 속도 변경"""
    import requests as req
    try:
        speed = body.get("max_forward_velocity", 1.2)
        req.post(f"http://{robot_ip}:8090/robot-params",
                 json={"/wheel_control/max_forward_velocity": speed}, timeout=5)
        return {"ok": True, "max_forward_velocity": speed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/remote/stop-all/{robot_ip}")
def api_stop_all(robot_ip: str):
    """모든 작업 정지 (이동 취소 + 잭 다운 + 스케줄/수동 배차 중단)"""
    import requests as req
    # 1) 로봇 이동 취소
    try:
        req.patch(
            f"http://{robot_ip}:8090/chassis/moves/current",
            json={"state": "cancelled"},
            timeout=5,
        )
    except Exception:
        pass
    # 2) 잭이 올라가 있으면 잭 다운
    try:
        req.post(f"http://{robot_ip}:8090/services/jack_down", json={}, timeout=5)
    except Exception:
        pass
    # 3) 백엔드 스케줄/수동 배차 작업 중단
    from app.services.jack_service import stop_robot_job
    stop_robot_job(robot_ip)
    return {"ok": True, "message": "모든 작업이 정지되었습니다"}


@router.post("/remote/dock/{robot_ip}")
def api_dock_to_charger(robot_ip: str, db: Session = Depends(get_db)):
    """충전소로 복귀"""
    import requests as req
    # DB에서 충전소 POI 찾기
    charger = db.query(MapPOI).filter(
        MapPOI.poi_type == "charging",
        MapPOI.is_active == True,
    ).first()
    if not charger or not charger.world_x or not charger.world_y:
        raise HTTPException(status_code=404, detail="충전소 POI를 찾을 수 없습니다")
    try:
        # 도킹포인트 좌표 조회
        from app.services.jack_service import get_docking_point_coords
        dock_coords = get_docking_point_coords(robot_ip, charger.name)
        if dock_coords:
            cx, cy, cyaw = dock_coords
        else:
            cx, cy, cyaw = charger.world_x, charger.world_y, charger.angle or 0
        r = req.post(
            f"http://{robot_ip}:8090/chassis/moves",
            json={
                "creator": "rcs",
                "type": "charge",
                "target_x": cx,
                "target_y": cy,
                "target_ori": cyaw,
                "charge_retry_count": 3,
            },
            timeout=5,
        )
        return {"status": r.status_code, "charger": charger.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/remote/jack/{robot_ip}/{action}")
def api_jack_control(robot_ip: str, action: str):
    """잭 업/다운 제어"""
    import requests as req
    if action not in ("jack_up", "jack_down"):
        raise HTTPException(status_code=400, detail="action must be jack_up or jack_down")
    try:
        r = req.post(
            f"http://{robot_ip}:8090/services/{action}",
            json={},
            timeout=5,
        )
        return {"status": r.status_code}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
