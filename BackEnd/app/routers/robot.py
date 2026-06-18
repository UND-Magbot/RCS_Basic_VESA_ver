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

@router.get("/quick-status/{robot_ip}")
def api_get_robot_quick_status(robot_ip: str):
    """단일 로봇 상태 빠른 조회 (태블릿용)"""
    import requests as req
    result = {"online": False, "run_state": "OFFLINE", "battery": "-"}
    try:
        r = req.get(f"http://{robot_ip}:8090/chassis/status", timeout=2)
        if r.status_code == 200:
            result["online"] = True
            data = r.json()
            mode = data.get("control_mode", "auto")
            if data.get("emergency_stop_pressed"):
                result["run_state"] = "ESTOP"
            elif mode == "remote":
                result["run_state"] = "REMOTE"
            else:
                try:
                    mr = req.get(f"http://{robot_ip}:8090/chassis/moves/current", timeout=2)
                    if mr.status_code == 200 and mr.json().get("state") == "moving":
                        result["run_state"] = "MOVING"
                    else:
                        result["run_state"] = "IDLE"
                except Exception:
                    result["run_state"] = "IDLE"
    except Exception:
        return result
    # 배터리 (WebSocket 1회 조회)
    ws = None
    try:
        import websocket as _ws, json as _json, time as _time
        ws = _ws.create_connection(f"ws://{robot_ip}:8090/ws/v2/topics", timeout=2)
        ws.send(_json.dumps({"enable_topic": "/battery_state"}))
        deadline = _time.time() + 2
        while _time.time() < deadline:
            raw = ws.recv()
            pkt = _json.loads(raw)
            if pkt.get("topic") == "/battery_state":
                pct = pkt.get("percentage") or pkt.get("level") or pkt.get("power_percent")
                if pct is not None:
                    pct = float(pct)
                    if pct <= 1.0:
                        pct = pct * 100
                    result["battery"] = f"{int(pct)}%"
                break
    except Exception:
        pass
    finally:
        if ws:
            try: ws.close()
            except Exception: pass
    return result


# ── /live 응답 5초 TTL 캐시 ──
import time as _time_mod
import threading as _threading_mod
_live_cache: dict = {"data": None, "ts": 0.0}
_live_cache_lock = _threading_mod.Lock()
_LIVE_CACHE_TTL = 5.0


@router.get("/live")
def api_get_robots_live(db: Session = Depends(get_db)):
    """DB 로봇 목록을 기반으로 실시간 API 정보를 병합하여 반환.
    5초 TTL 캐시 적용 — 여러 클라이언트가 동시에 폴링해도 로봇에 중복 호출 안 함.
    """
    # 캐시 체크
    with _live_cache_lock:
        cached = _live_cache["data"]
        age = _time_mod.time() - _live_cache["ts"]
        if cached is not None and age < _LIVE_CACHE_TTL:
            return cached

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
            "ROBOT_TYPE": getattr(r, "robot_type", "lifting") or "lifting",
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
    result = {"total": len(items), "items": items}
    # 캐시 업데이트
    with _live_cache_lock:
        _live_cache["data"] = result
        _live_cache["ts"] = _time_mod.time()
    return result


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


@router.post("/{robot_id}/switch-floor")
def api_switch_floor(robot_id: int, body: dict, db: Session = Depends(get_db)):
    """로봇 층(Area) 전환 — 맵 동기화 + area_id 업데이트"""
    from app.services.jack_service import get_job_status
    from app.models.map import RobotMap, MapPOI

    area_id = body.get("area_id")
    if not area_id:
        raise HTTPException(400, "area_id 필수")

    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    if not robot or not robot.ip_address:
        raise HTTPException(404, "로봇을 찾을 수 없습니다")

    # 작업 중이면 거부
    if get_job_status(robot.ip_address):
        raise HTTPException(409, "로봇이 작업 중입니다. 작업 완료 후 전환하세요.")

    # 해당 area의 활성 맵 조회
    area_map = db.query(RobotMap).filter(
        RobotMap.area_id == area_id, RobotMap.is_active == True
    ).order_by(RobotMap.id.desc()).first()
    if not area_map:
        raise HTTPException(404, "해당 층에 활성 맵이 없습니다")

    # robot area_id 업데이트
    robot.area_id = str(area_id)
    # 충전소/대기장소 자동 재설정
    charging = db.query(MapPOI).filter(
        MapPOI.map_id == area_map.id, MapPOI.poi_type == "charging", MapPOI.is_active == True
    ).first()
    standby = db.query(MapPOI).filter(
        MapPOI.map_id == area_map.id, MapPOI.poi_type == "standby", MapPOI.is_active == True
    ).first()
    robot.charging_id = charging.id if charging else None
    robot.standby_id = standby.id if standby else None
    db.commit()

    # 맵 전환 — 로봇 맵 ID로 current-map만 전환
    robot_ip = robot.ip_address
    map_id = area_map.id

    import requests as req
    robot_map_id = area_map.robot_map_id
    if not robot_map_id:
        raise HTTPException(400, "해당 맵에 로봇 맵 ID가 설정되지 않았습니다. 맵 동기화를 먼저 해주세요.")

    try:
        r = req.post(f"http://{robot_ip}:8090/chassis/current-map",
                     json={"map_id": robot_map_id}, timeout=10)
        if r.status_code != 200:
            raise HTTPException(500, f"맵 전환 실패: {r.text[:200]}")
    except req.exceptions.RequestException as e:
        raise HTTPException(500, f"맵 전환 실패: {str(e)}")

    # LiDAR 위치 자동 탐색
    try:
        req.post(f"http://{robot_ip}:8090/services/start_global_positioning",
                 json={}, timeout=5)
        logger.info(f"[switch-floor] LiDAR 위치 자동 탐색 시작")
    except Exception as e:
        logger.warning(f"[switch-floor] LiDAR 위치 탐색 실패: {e}")

    # 기본 영역 업데이트 (새로고침해도 이 맵 유지)
    import app.routers.map as _map_mod
    _map_mod._current_area_id = int(area_id)

    log_activity("robot", "switch_floor",
                 f"층 전환: {robot.name} → area_id={area_id} (맵: {area_map.name})",
                 robot_id=robot_id, source="api_switch_floor")

    return {
        "ok": True,
        "message": f"층 전환 완료 (맵: {area_map.name})",
        "area_id": area_id,
        "map_id": map_id,
        "map_name": area_map.name,
        "charging_poi": charging.name if charging else None,
        "standby_poi": standby.name if standby else None,
    }


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
def api_get_speed(robot_ip: str, db: Session = Depends(get_db)):
    """로봇 속도 조회 (DB 우선, 없으면 로봇에서)"""
    import requests as req
    robot = db.query(Robot).filter(Robot.ip_address == robot_ip, Robot.is_active == True).first()
    speed = robot.max_speed if robot and robot.max_speed else 1.2
    try:
        r = req.get(f"http://{robot_ip}:8090/robot-params", timeout=3)
        params = r.json()
        return {
            "max_forward_velocity": speed,
            "max_backward_velocity": abs(params.get("/wheel_control/max_backward_velocity", -0.5)),
            "max_angular_velocity": params.get("/wheel_control/max_angular_velocity", 1.2),
        }
    except Exception:
        return {
            "max_forward_velocity": speed,
            "max_backward_velocity": 0.5,
            "max_angular_velocity": 1.2,
        }


@router.post("/speed/{robot_ip}")
def api_set_speed(robot_ip: str, body: dict, db: Session = Depends(get_db)):
    """로봇 속도 변경 (DB 저장 + 로봇 전송)"""
    import requests as req
    speed = body.get("max_forward_velocity", 1.2)
    # 1) DB 먼저 저장
    robot = db.query(Robot).filter(Robot.ip_address == robot_ip, Robot.is_active == True).first()
    import logging
    logging.getLogger(__name__).info(f"[speed] robot_ip={robot_ip}, found={robot is not None}, speed={speed}")
    if robot:
        robot.max_speed = speed
        db.commit()
        logging.getLogger(__name__).info(f"[speed] DB saved: {robot.max_speed}")
    # 2) 로봇에 전송 (실패해도 DB는 이미 저장됨)
    try:
        req.post(f"http://{robot_ip}:8090/robot-params",
                 json={"/wheel_control/max_forward_velocity": speed}, timeout=5)
    except Exception:
        pass
    return {"ok": True, "max_forward_velocity": speed}


@router.post("/remote/stop-all/{robot_ip}")
def api_stop_all(robot_ip: str):
    """모든 작업 정지 (이동 취소 + 잭 다운 + 작업 중단)"""
    import requests as req
    # 1) 백엔드 스케줄/수동 배차 작업 중단 (먼저 — 새 명령 방지)
    from app.services.jack_service import stop_robot_job
    stop_robot_job(robot_ip)
    # 2) 로봇 이동 취소
    try:
        req.patch(
            f"http://{robot_ip}:8090/chassis/moves/current",
            json={"state": "cancelled"},
            timeout=5,
        )
    except Exception:
        pass
    # 3) 잭이 올라가 있으면 잭 다운
    try:
        req.post(f"http://{robot_ip}:8090/services/jack_down", json={}, timeout=5)
    except Exception:
        pass
    return {"ok": True, "message": "모든 작업이 정지되었습니다"}


@router.post("/remote/pause/{robot_ip}")
def api_pause_robot(robot_ip: str):
    """일시정지 — 현재 이동 즉시 cancel + paused 플래그 set.
    재개될 때까지 작업 thread 가 대기."""
    from app.services.jack_service import pause_robot_job
    pause_robot_job(robot_ip)
    return {"ok": True, "message": "일시정지"}


@router.post("/remote/resume/{robot_ip}")
def api_resume_robot(robot_ip: str):
    """일시정지 해제 — cancel 된 이동은 safe_move 가 자동 재시도."""
    from app.services.jack_service import resume_robot_job
    resume_robot_job(robot_ip)
    return {"ok": True, "message": "재개"}


@router.post("/remote/force-return/{robot_ip}")
def api_force_return(robot_ip: str, db: Session = Depends(get_db)):
    """강제 종료 — 현재 위치에서 잭 업 → 랙 위치 복귀 → 충전소 도킹.
    별도 thread 로 전체 절차를 수행하며 즉시 응답."""
    from app.services.jack_service import force_return_and_dock
    from app.services.thread_utils import safe_thread

    robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
    robot_id = robot.id if robot else None
    safe_thread(
        target=force_return_and_dock,
        args=(robot_ip, robot_id),
        name=f"force-return-{robot_ip}",
    ).start()
    from app.crud.activity_log import log_activity
    log_activity("user", "force_return_request", f"강제 종료 요청: {robot_ip}", source="api_force_return")
    return {"ok": True, "message": "강제 종료 시작 — 랙 보관 후 충전소 복귀합니다"}


@router.post("/remote/relocalize/{robot_ip}")
def api_relocalize(robot_ip: str):
    """위치 복구 — start_global_positioning + 실패 시 시스템 재시작 옵션"""
    from app.services.jack_service import recover_positioning
    try:
        ok = recover_positioning(robot_ip, max_wait_sec=15)
        if ok:
            return {"ok": True, "message": "위치 복구 완료"}
        # 복구 실패 시 시스템 재시작
        import requests as req
        req.post(f"http://{robot_ip}:8090/services/restart_service",
                 headers={"Authorization": f"Secret {DEFAULT_SECRET}"},
                 json={}, timeout=10)
        return {"ok": True, "message": "위치 복구 실패 → 시스템 재시작 시작 (약 90초)"}
    except Exception as e:
        raise HTTPException(500, f"위치 복구 실패: {str(e)}")


@router.post("/remote/confirm/{robot_ip}")
def api_confirm_robot(robot_ip: str):
    """잭 업 후 출발 확인"""
    from app.services.jack_service import confirm_robot
    confirm_robot(robot_ip)
    return {"ok": True, "message": "출발 확인됨"}


@router.post("/remote/next-point/{robot_ip}")
def api_set_next_point(robot_ip: str, body: dict, db: Session = Depends(get_db)):
    """다음 포인트 설정 (드롭오프 후 계속 이동)"""
    from app.services.jack_service import set_next_poi
    poi_id = body.get("poi_id")
    if not poi_id:
        # 복귀
        set_next_poi(robot_ip, "return")
        return {"ok": True, "action": "return"}
    poi = db.query(MapPOI).filter(MapPOI.id == poi_id, MapPOI.is_active == True).first()
    if not poi:
        raise HTTPException(404, "POI를 찾을 수 없습니다")
    set_next_poi(robot_ip, {"name": poi.name, "x": poi.world_x, "y": poi.world_y, "ori": poi.angle or 0})
    return {"ok": True, "action": "next", "poi_name": poi.name}


@router.post("/remote/return/{robot_ip}")
def api_return_to_standby(robot_ip: str):
    """복귀 선택"""
    from app.services.jack_service import set_next_poi
    set_next_poi(robot_ip, "return")
    return {"ok": True, "action": "return"}


@router.post("/remote/return-to-standby/{robot_ip}")
def api_return_to_standby_now(robot_ip: str):
    """랙 위치 즉시 복귀 — 현재 위치에서 jack_up → 랙 위치 이동 → jack_down"""
    import threading
    from app.services.jack_service import (
        _get_standby_poi, align_with_retry, jack_up, jack_down,
        create_move, wait_move, update_job_status, clear_job_status,
        JACK_WAIT_SEC,
    )

    standby = _get_standby_poi(robot_ip=robot_ip)
    if not standby:
        raise HTTPException(400, "랙 위치 POI가 없습니다")

    def _run():
        try:
            update_job_status(robot_ip, status="returning", message="랙 위치 복귀 중...")
            # 1) 잭 업 (이미 올려져 있을 수 있으나 안전하게)
            try:
                jack_up(robot_ip)
                import time; time.sleep(JACK_WAIT_SEC)
            except Exception:
                pass
            # 2) W1으로 이동
            move_id = create_move(robot_ip, "to_unload_point",
                                  standby["x"], standby["y"], standby.get("ori", 0))
            result = wait_move(robot_ip, move_id, timeout=120)
            # 3) 잭 다운
            jack_down(robot_ip)
            import time; time.sleep(JACK_WAIT_SEC)
            clear_job_status(robot_ip)
        except Exception as e:
            logger.warning(f"[return-to-standby] 실패: {e}")
            clear_job_status(robot_ip)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": f"대기장소({standby['name']}) 복귀 시작"}


@router.post("/remote/dock/{robot_ip}")
def api_dock_to_charger(robot_ip: str, db: Session = Depends(get_db)):
    """충전소로 복귀"""
    import requests as req
    # 로봇의 현재 영역 맵에서 충전소 POI 찾기
    robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
    charger_query = db.query(MapPOI).filter(
        MapPOI.poi_type == "charging",
        MapPOI.is_active == True,
    )
    if robot and robot.charging_id:
        charger = db.query(MapPOI).filter(MapPOI.id == robot.charging_id).first()
    elif robot and robot.area_id:
        from app.models.map import RobotMap
        area_map = db.query(RobotMap).filter(
            RobotMap.area_id == int(robot.area_id), RobotMap.is_active == True
        ).order_by(RobotMap.id.desc()).first()
        charger = charger_query.filter(MapPOI.map_id == area_map.id).first() if area_map else charger_query.first()
    else:
        charger = charger_query.first()
    if not charger or charger.world_x is None or charger.world_y is None:
        raise HTTPException(status_code=404, detail="충전소 POI를 찾을 수 없습니다")
    try:
        cx = charger.world_x
        cy = charger.world_y
        cyaw = charger.angle if charger.angle is not None else 0

        # 사전 접근 POI ("<charger_name>-1") 같은 맵에서 검색 — 있으면 그쪽으로 먼저 이동
        ax, ay, ayaw = cx, cy, cyaw
        approach_name_used = None
        try:
            ap = db.query(MapPOI).filter(
                MapPOI.map_id == charger.map_id,
                MapPOI.name == f"{charger.name}-1",
                MapPOI.is_active == True,
            ).first()
            if ap and ap.world_x is not None and ap.world_y is not None:
                ax, ay = ap.world_x, ap.world_y
                ayaw = ap.angle if ap.angle is not None else cyaw
                approach_name_used = ap.name
        except Exception:
            pass

        # 1) standard로 사전 접근 POI(있으면) 또는 충전소 POI 좌표로 이동
        req.post(
            f"http://{robot_ip}:8090/chassis/moves",
            json={
                "creator": "rcs",
                "type": "standard",
                "target_x": ax,
                "target_y": ay,
                "target_ori": ayaw,
            },
            timeout=5,
        )
        # 2) charge 명령 — target_ori 명시 (로봇 정렬 후 정확한 충전기 인식)
        import threading
        def _then_charge():
            import time as _t
            _t.sleep(8)  # standard 이동 완료 대기
            try:
                req.post(
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
            except Exception:
                pass
        threading.Thread(target=_then_charge, daemon=True).start()
        return {"ok": True, "charger": charger.name, "approach": approach_name_used}
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


@router.post("/remote/shutdown/{robot_ip}")
def api_shutdown_robot(robot_ip: str):
    """로봇 전원 종료"""
    import requests as req
    try:
        r = req.post(
            f"http://{robot_ip}:8090/services/baseboard/shutdown",
            json={"target": "main_power_supply", "reboot": False},
            timeout=5,
        )
        return {"status": r.status_code, "message": "로봇 종료 명령 전송"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
