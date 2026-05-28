import asyncio
import json
import logging
import math
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

import requests as http_requests
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

STATIC_MAPS_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "maps"
STATIC_MAPS_DIR.mkdir(parents=True, exist_ok=True)

from app.database import get_db
from app.crud.activity_log import log_activity
from app.models.robot import Robot
from app.models.map import RobotMap, Business, Area, MapPolygon, MapPOI
from app.routers.robot import DEFAULT_SECRET


def _get_robots_from_db(db) -> list[dict]:
    """DB에서 활성 로봇 IP 목록"""
    robots = db.query(Robot).filter(Robot.is_active == True, Robot.ip_address != None).all()
    return [{"ip": r.ip_address, "secret": DEFAULT_SECRET} for r in robots if r.ip_address]
from app.crud.map import (
    get_businesses,
    create_business,
    delete_business,
    get_areas,
    create_area,
    delete_area,
    save_robot_map,
    get_maps_by_area,
    get_map_by_id,
    delete_map as crud_delete_map,
    save_map_elements,
    get_map_elements,
    get_charging_pois,
)
from app.robot_api.robot_map_service import (
    RobotWSRelay,
    get_maps,
    get_map,
    create_map,
    update_map,
    delete_map,
    patch_map,
    get_map_by_id as get_robot_map_by_id,
    delete_map_by_id,
    patch_map_by_id,
    update_map_by_id,
    download_map_image,
    restart_robot_service,
    get_mappings,
    get_mapping_by_id,
    create_mapping,
    update_mapping,
    delete_mapping,
    patch_mapping,
    get_current_mapping,
    create_current_mapping,
    update_current_mapping,
    delete_current_mapping,
    patch_current_mapping,
    set_chassis_pose,
    get_current_map,
    set_current_map,
)

router = APIRouter(prefix="/api/map", tags=["맵 관리"])


# ── helper ────────────────────────────────────────────────────

def _find_secret(robot_ip: str) -> str:
    return DEFAULT_SECRET


def _proxy(func, *args, **kwargs) -> Any:
    try:
        return func(*args, **kwargs)
    except HTTPException:
        raise
    except http_requests.exceptions.HTTPError as exc:
        # 로봇이 4xx/5xx 응답한 경우
        resp_text = ""
        if exc.response is not None:
            resp_text = exc.response.text[:500]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"로봇 API 오류가 발생했습니다 ({exc.response.status_code if exc.response else '?'}): {resp_text or exc}",
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"로봇 API 호출에 실패했습니다: {type(exc).__name__}: {exc}",
        )


def _update_robots_area(db: Session, area_id: str):
    """연결 가능한 모든 로봇의 area_id를 업데이트한다."""
    robots = db.query(Robot).filter(Robot.is_active == True).all()
    for robot in robots:
        if not robot.ip_address:
            continue
        try:
            res = http_requests.get(
                f"http://{robot.ip_address}:8090/device/info",
                headers={"Secret": DEFAULT_SECRET},
                timeout=3,
            )
            res.raise_for_status()
            robot.area_id = area_id
            logger.info(f"[save_map] 로봇 {robot.serial_number} area_id={area_id} 업데이트")
        except Exception:
            logger.warning(f"[save_map] 로봇 {robot.serial_number} ({robot.ip_address}) 연결 불가 → 건너뜀")
    db.commit()


DOCKING_OFFSET = 0.3  # POI(로봇 도킹 위치) 기준 충전기는 yaw 반대 방향(뒤쪽)으로 오프셋


def _correct_map_grid_origin(db: Session, map_id: int) -> bool:
    """DB 맵의 grid_origin이 실제 로봇 맵과 다르면 보정.

    맵핑 원시 데이터의 grid_origin과 로봇이 서비스 재시작 후 만든
    실제 맵의 grid_origin이 달라지는 문제를 감지하고 자동 수정한다.
    DB grid_origin, image_url, download_url(JSON) 모두 갱신.

    Returns: 보정 수행 시 True, 불필요하거나 실패 시 False.
    """
    import json as _json

    rm = db.query(RobotMap).filter(RobotMap.id == map_id, RobotMap.is_active == True).first()
    if not rm or not rm.robot_sn:
        return False

    source_robot = db.query(Robot).filter(
        Robot.serial_number == rm.robot_sn, Robot.is_active == True
    ).first()
    if not source_robot or not source_robot.ip_address:
        return False

    source_ip = source_robot.ip_address
    try:
        source_secret = _find_secret(source_ip)
    except HTTPException:
        return False

    try:
        maps_list = get_maps(source_ip, source_secret)
        if not isinstance(maps_list, list) or not maps_list:
            return False

        # robot_map_id가 있으면 해당 맵, 없으면 첫 번째 맵
        actual_map_id = rm.robot_map_id or maps_list[0]["id"]
        actual_detail = get_robot_map_by_id(source_ip, source_secret, actual_map_id)
        actual_gx = float(actual_detail.get("grid_origin_x", 0))
        actual_gy = float(actual_detail.get("grid_origin_y", 0))
        actual_res = float(actual_detail.get("grid_resolution", 0.05))

        db_gx = float(rm.grid_origin_x or 0)
        db_gy = float(rm.grid_origin_y or 0)

        if abs(db_gx - actual_gx) < 0.1 and abs(db_gy - actual_gy) < 0.1:
            return False  # 보정 불필요

        logger.warning(f"[map-correct] grid_origin 불일치 감지 (map_id={map_id}): "
                       f"DB=({db_gx}, {db_gy}) vs 로봇=({actual_gx}, {actual_gy})")

        # ── 1) DB grid_origin 갱신 ──
        rm.grid_origin_x = actual_gx
        rm.grid_origin_y = actual_gy
        rm.grid_resolution = actual_res

        # ── 2) 맵 이미지 재다운로드 ──
        try:
            img_bytes = download_map_image(source_ip, source_secret, actual_map_id)
            img_filename = f"map_img_{uuid.uuid4().hex[:12]}.png"
            img_filepath = STATIC_MAPS_DIR / img_filename
            img_filepath.write_bytes(img_bytes)
            rm.image_url = f"/static/maps/{img_filename}"
        except Exception as img_err:
            logger.warning(f"[map-correct] 이미지 갱신 실패 (무시): {img_err}")

        # ── 3) JSON 매핑 데이터의 grid_origin 갱신 ──
        if rm.download_url and rm.download_url.startswith("/static/"):
            try:
                json_path = Path(__file__).resolve().parent.parent.parent / rm.download_url.lstrip("/")
                if json_path.exists():
                    raw = _json.loads(json_path.read_text())
                    if isinstance(raw, list):
                        raw = raw[0] if raw else {}
                    raw["grid_origin_x"] = actual_gx
                    raw["grid_origin_y"] = actual_gy
                    raw["grid_resolution"] = actual_res
                    new_json_name = f"map_data_{uuid.uuid4().hex[:12]}.json"
                    new_json_path = STATIC_MAPS_DIR / new_json_name
                    new_json_path.write_text(_json.dumps(raw))
                    rm.download_url = f"/static/maps/{new_json_name}"
            except Exception as json_err:
                logger.warning(f"[map-correct] JSON 갱신 실패 (무시): {json_err}")

        db.commit()
        logger.info(f"[map-correct] 보정 완료: grid_origin=({actual_gx}, {actual_gy}), "
                    f"image={rm.image_url}, json={rm.download_url}")
        return True

    except Exception as e:
        logger.error(f"[map-correct] 보정 실패 (로봇 연결 문제?): {e}")
        return False


def _build_charging_overlay_features(charging_pois: list) -> list[dict]:
    """충전소 POI 목록을 로봇 오버레이용 GeoJSON Feature 리스트로 변환.

    각 충전소마다 2개의 Feature를 생성:
    - 충전소 (type "9"): 충전기 위치
    - 도킹 포인트 (type "36"): 로봇이 도킹하는 위치 (충전소 yaw 방향 0.9m 앞)
    """
    features = []
    for poi in charging_pois:
        charger_id = uuid.uuid4().hex[:24]
        docking_id = uuid.uuid4().hex[:24]

        # angle: DB에 radian 저장 → degree 문자열로 변환
        yaw_deg = 0.0
        if poi.angle is not None:
            yaw_deg = poi.angle * 180.0 / math.pi
        charger_yaw = str(int(round(yaw_deg)))  # 정수 문자열 ("180", "90" 등)

        # POI = 로봇 도킹 중심, 충전기는 yaw 반대 방향(로봇 뒤쪽 = 벽쪽)으로 오프셋
        yaw_rad = math.radians(yaw_deg)
        # 도킹 포인트 = POI 위치 그대로 (로봇 도킹 시 중심)
        dock_x = poi.world_x
        dock_y = poi.world_y
        # 충전기 = POI 기준 yaw 반대 방향으로 DOCKING_OFFSET만큼 뒤
        charger_x = poi.world_x - DOCKING_OFFSET * math.cos(yaw_rad)
        charger_y = poi.world_y - DOCKING_OFFSET * math.sin(yaw_rad)
        raw_dock_yaw = int(round((yaw_deg + 180) % 360))
        dock_yaw = str(360 if raw_dock_yaw == 0 else raw_dock_yaw)  # 0° → "360" (1SSS 방식)

        poi_name = poi.name or ""

        # 충전소 Feature (type "9") — 실제 충전기 위치 (도킹 포인트보다 yaw 반대로 0.3m 뒤)
        features.append({
            "id": charger_id,
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [charger_x, charger_y],
            },
            "properties": {
                "deviceIds": None,
                "dockingPointId": docking_id,
                "mapOverlay": True,
                "name": poi_name,
                "subtype": "24v",
                "type": "9",
                "yaw": charger_yaw,
            },
        })

        # 도킹 포인트 Feature (type "36")
        features.append({
            "id": docking_id,
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [dock_x, dock_y],
            },
            "properties": {
                "dockingPointId": charger_id,
                "mapOverlay": True,
                "name": f"{poi_name} Docking Point",
                "type": "36",
                "yaw": dock_yaw,
            },
        })

    return features


def _build_firewall_overlay_features(firewall_polygons: list) -> list[dict]:
    """가상벽(firewall) 폴리곤을 로봇 오버레이용 GeoJSON Feature 리스트로 변환.

    AutoXing 로봇 가상벽: overlay type="1", LineString 좌표 (폴리곤 둘레를 닫힌 LineString으로)
    """
    features = []
    for poly in firewall_polygons:
        wall_id = uuid.uuid4().hex[:24]
        points = json.loads(poly.points_json) if isinstance(poly.points_json, str) else poly.points_json
        coords = []
        for pt in points:
            wx = pt.get("worldX")
            wy = pt.get("worldY")
            if wx is not None and wy is not None:
                coords.append([wx, wy])
        if len(coords) < 3:
            continue
        # 닫힌 폴리곤 (첫 점 반복)
        coords.append(coords[0])
        features.append({
            "id": wall_id,
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
            "properties": {
                "mapOverlay": True,
                "name": poly.name or f"VW_{wall_id[:6]}",
                "type": "1",
            },
        })
    return features


# ── 로봇 목록 / 연결 확인 ─────────────────────────────────────

@router.get("/robots")
def api_get_robots_for_map(db: Session = Depends(get_db)):
    """맵 편집용 로봇 목록 (DB 내 활성 로봇의 SN·이름·IP 반환)"""
    robots = db.query(Robot).filter(Robot.is_active == True).all()
    return {
        "total": len(robots),
        "items": [
            {
                "sn": r.serial_number,
                "name": r.name,
                "ip_address": r.ip_address,
            }
            for r in robots
        ],
    }


@router.post("/connect/{sn}")
def api_connect_robot(sn: str, db: Session = Depends(get_db)):
    """로봇 연결 확인 — SN으로 IP 조회 후 GET /device/info 호출.
    성공 시 로봇 정보 반환, 실패 시 503."""
    robot = (
        db.query(Robot)
        .filter(Robot.serial_number == sn, Robot.is_active == True)
        .first()
    )
    if not robot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"로봇을 찾지 못했습니다: {sn}",
        )
    if not robot.ip_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"로봇 IP 주소가 등록되어 있지 않습니다: {sn}",
        )

    try:
        url = f"http://{robot.ip_address}:8090/device/info"
        headers = {"Secret": DEFAULT_SECRET}
        res = http_requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        device_info = res.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"로봇에 연결하지 못했습니다: {exc}",
        )

    return {
        "connected": True,
        "sn": robot.serial_number,
        "name": robot.name,
        "ip_address": robot.ip_address,
        "device_info": device_info,
    }


# ── Business (사업장) CRUD ────────────────────────────────────

@router.get("/businesses")
def api_get_businesses(db: Session = Depends(get_db)):
    """사업장 목록 조회 (드롭다운용)"""
    try:
        items = get_businesses(db)
        return {"total": len(items), "items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"사업장 목록 조회 에 실패했습니다: {e}")


@router.post("/businesses", status_code=201)
def api_create_business(body: dict, db: Session = Depends(get_db)):
    """사업장 생성"""
    try:
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="사업장 이름은 필수입니다.")
        result = create_business(db, name)
        log_activity("map", "business_create",
                     f"사업장 생성: {name}",
                     source="api_create_business")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"사업장 생성 에 실패했습니다: {e}")


@router.delete("/businesses/{business_id}")
def api_delete_business(business_id: int, db: Session = Depends(get_db)):
    """사업장 비활성화"""
    try:
        biz = db.query(Business).filter(Business.business_id == business_id).first()
        biz_name = biz.name if biz else f"ID:{business_id}"
        result = delete_business(db, business_id)
        log_activity("map", "business_delete",
                     f"사업장 삭제: {biz_name}",
                     source="api_delete_business")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"사업장 삭제 에 실패했습니다: {e}")


# ── Area (영역) CRUD ──────────────────────────────────────────

@router.get("/businesses/{business_id}/areas")
def api_get_areas(business_id: int, db: Session = Depends(get_db)):
    """해당 사업장의 영역 목록 조회 (드롭다운용)"""
    try:
        items = get_areas(db, business_id)
        return {"total": len(items), "items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"영역 목록 조회 에 실패했습니다: {e}")


@router.post("/areas", status_code=201)
def api_create_area(body: dict, db: Session = Depends(get_db)):
    """영역 생성"""
    try:
        business_id = body.get("business_id")
        name = body.get("name", "").strip()
        if not business_id:
            raise HTTPException(status_code=400, detail="business_id는 필수입니다.")
        if not name:
            raise HTTPException(status_code=400, detail="영역 이름은 필수입니다.")
        result = create_area(db, business_id, name)
        log_activity("map", "area_create",
                     f"영역 생성: {name}",
                     source="api_create_area")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"영역 생성 에 실패했습니다: {e}")


@router.patch("/areas/{area_id}")
def api_update_area(area_id: int, body: dict, db: Session = Depends(get_db)):
    """영역 수정 (is_main_floor 등)"""
    area = db.query(Area).filter(Area.area_id == area_id).first()
    if not area:
        raise HTTPException(404, "영역을 찾지 못했습니다.")
    if "is_main_floor" in body:
        area.is_main_floor = bool(body["is_main_floor"])
    if "name" in body and body["name"].strip():
        area.name = body["name"].strip()
    db.commit()
    return {"area_id": area.area_id, "name": area.name, "is_main_floor": area.is_main_floor}


@router.delete("/areas/{area_id}")
def api_delete_area(area_id: int, db: Session = Depends(get_db)):
    """영역 비활성화"""
    try:
        area = db.query(Area).filter(Area.area_id == area_id).first()
        area_name = area.name if area else f"ID:{area_id}"
        result = delete_area(db, area_id)
        log_activity("map", "area_delete",
                     f"영역 삭제: {area_name}",
                     source="api_delete_area")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"영역 삭제 에 실패했습니다: {e}")


# ── 매핑 결과 저장 / 조회 ─────────────────────────────────────

def _download_robot_image(url: str, prefix: str = "map") -> str | None:
    """로봇 이미지를 다운로드하여 로컬에 저장하고, 서버 경로를 반환."""
    if not url:
        return None
    try:
        res = http_requests.get(url, timeout=15)
        res.raise_for_status()
        # 확장자 추출
        content_type = res.headers.get("Content-Type", "")
        ext = ".png"
        if "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        filename = f"{prefix}_{uuid.uuid4().hex[:12]}{ext}"
        filepath = STATIC_MAPS_DIR / filename
        filepath.write_bytes(res.content)
        return f"/static/maps/{filename}"
    except Exception as e:
        logger.error(f"[save_map] 이미지 다운로드 실패 ({url}): {e}")
        return None


def _download_robot_map_data(download_url: str, secret: str | None = None) -> str | None:
    """로봇 맵 데이터(JSON)를 다운로드하여 로컬에 저장하고, 서버 경로를 반환."""
    if not download_url:
        return None
    try:
        headers = {"Secret": secret} if secret else {}
        res = http_requests.get(download_url, headers=headers, timeout=30)
        res.raise_for_status()
        filename = f"map_data_{uuid.uuid4().hex[:12]}.json"
        filepath = STATIC_MAPS_DIR / filename
        filepath.write_bytes(res.content)
        return f"/static/maps/{filename}"
    except Exception as e:
        logger.error(f"[save_map] 맵 데이터 다운로드 실패 ({download_url}): {e}")
        return None


def _download_robot_file(url: str, prefix: str, ext: str, secret: str | None = None) -> str | None:
    """로봇 파일(bag, trajectories 등)을 다운로드하여 /static/maps/에 저장."""
    if not url:
        return None
    try:
        headers = {"Secret": secret} if secret else {}
        res = http_requests.get(url, headers=headers, timeout=60)
        res.raise_for_status()
        filename = f"{prefix}_{uuid.uuid4().hex[:12]}{ext}"
        filepath = STATIC_MAPS_DIR / filename
        filepath.write_bytes(res.content)
        return f"/static/maps/{filename}"
    except Exception as e:
        logger.error(f"[save_map] 파일 다운로드 실패 ({url}): {e}")
        return None


@router.post("/maps/save", status_code=201)
def api_save_map(body: dict, db: Session = Depends(get_db)):
    """매핑 종료 후 결과를 DB에 저장.
    이미지·맵 데이터를 로봇에서 다운로드하여 로컬 서버에 저장한 뒤 경로를 DB에 기록."""
    # 로봇 secret 조회 (download_url에 필요)
    robot_secret = None
    robot_ip = None
    download_url = body.get("download_url")
    if download_url:
        try:
            from urllib.parse import urlparse
            robot_ip = urlparse(download_url).hostname
            robot_secret = _find_secret(robot_ip)
        except Exception as e:
            logger.warning(f"[save_map] 로봇 Secret 조회 실패: {e}")
            pass

    # 로봇 URL → 로컬 서버 파일로 다운로드
    if body.get("image_url"):
        local_path = _download_robot_image(body["image_url"], "map_img")
        if local_path:
            body["image_url"] = local_path

    if body.get("thumbnail_url"):
        local_path = _download_robot_image(body["thumbnail_url"], "map_thumb")
        if local_path:
            body["thumbnail_url"] = local_path

    # 맵 데이터(JSON) 다운로드 저장
    if download_url:
        local_data_path = _download_robot_map_data(download_url, robot_secret)
        if local_data_path:
            body["download_url"] = local_data_path

    # bag 파일(.bag) 다운로드 저장
    if body.get("bag_url"):
        local_path = _download_robot_file(body["bag_url"], "map_bag", ".bag", robot_secret)
        if local_path:
            body["bag_url"] = local_path

    # trajectories 데이터(.json) 다운로드 저장
    if body.get("trajectories_url"):
        local_path = _download_robot_file(body["trajectories_url"], "map_traj", ".json", robot_secret)
        if local_path:
            body["trajectories_url"] = local_path

    try:
        result = save_robot_map(db, body)
    except Exception as e:
        logger.error(f"[save_map] DB 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=f"맵 DB 저장 에 실패했습니다: {e}")

    # 로봇의 맵 목록에서 이름 매칭으로 robot_map_id 저장 (층 전환용)
    saved_map_id_tmp = result.get("id")
    db_map_name = body.get("name") or result.get("name")
    if saved_map_id_tmp and robot_ip and db_map_name:
        try:
            secret = _find_secret(robot_ip)
            robot_maps_list = get_maps(robot_ip, secret)
            if isinstance(robot_maps_list, list):
                matched_id = None
                for rmap in robot_maps_list:
                    if rmap.get("map_name") == db_map_name or rmap.get("name") == db_map_name:
                        matched_id = rmap.get("id")
                        break
                if matched_id:
                    rm = db.query(RobotMap).filter(RobotMap.id == saved_map_id_tmp).first()
                    if rm:
                        rm.robot_map_id = matched_id
                        db.commit()
                        logger.info(f"[save_map] robot_map_id={matched_id} 저장 (map_id={saved_map_id_tmp}, name={db_map_name})")
                else:
                    logger.warning(f"[save_map] 로봇 맵 목록에서 '{db_map_name}' 매칭 실패")
        except Exception as e:
            logger.warning(f"[save_map] robot_map_id 저장 실패: {e}")

    # 맵 저장 성공 시, 연결 가능한 모든 로봇의 area_id 업데이트
    area_id = body.get("area_id")
    if area_id:
        try:
            _update_robots_area(db, str(area_id))
        except Exception as e:
            logger.error(f"[save_map] 로봇 area_id 업데이트 실패: {e}")

    # ── 자동 동기화: 서버 데이터 기반으로 즉시 동기화 + 후속 DB 보정 ──
    saved_map_id = result.get("id")
    source_sn = body.get("robot_sn")
    area_name = body.get("name", "synced-map")
    if saved_map_id and source_sn:
        import threading

        def _auto_sync():
            from app.database import SessionLocal
            sync_db = SessionLocal()
            try:
                source_robot = sync_db.query(Robot).filter(
                    Robot.serial_number == source_sn, Robot.is_active == True
                ).first()
                source_ip = source_robot.ip_address if source_robot else None
                if not source_ip:
                    logger.warning(f"[auto-sync] 소스 로봇 IP를 찾지 못했습니다: {source_sn}")
                    return

                # ── 1) 서버 데이터 기반 즉시 동기화 (소스 로봇 대기 불필요) ──
                targets = [r["ip"] for r in _get_robots_from_db(sync_db) if r["ip"] != source_ip]
                if targets:
                    logger.info(f"[auto-sync] 서버 데이터 기반 즉시 동기화: → {targets}")
                    for target_ip in targets:
                        try:
                            api_sync_map_to_robot(
                                map_id=saved_map_id,
                                body={"robot_ip": target_ip, "area_name": area_name, "method": "full"},
                                db=sync_db,
                            )
                            logger.info(f"[auto-sync] {target_ip} 동기화 완료")
                        except Exception as e:
                            logger.error(f"[auto-sync] {target_ip} 동기화 실패: {e}")
                else:
                    logger.info("[auto-sync] 동기화할 타겟 로봇이 없습니다.")

                # ── 2) 소스 로봇의 새 맵 생성 대기 → DB 보정 (grid_origin + image + JSON) ──
                import time as _time
                source_secret = _find_secret(source_ip)
                old_maps = get_maps(source_ip, source_secret)
                # 모든 맵의 ID/버전 기록 (새 맵 감지용)
                old_map_ids = {m["id"]: m.get("map_version", 0) for m in old_maps} if isinstance(old_maps, list) else {}
                old_map_id = old_maps[0]["id"] if isinstance(old_maps, list) and old_maps else None
                old_map_version = old_maps[0].get("map_version", 0) if old_map_id else 0

                logger.info(f"[auto-sync] DB 보정용 새 맵 대기 중... (현재 id={old_map_id}, ver={old_map_version})")
                new_map_found = False
                cur_id = old_map_id  # 새 맵 감지 실패 시 기존 ID 유지
                for wait_i in range(24):  # 최대 120초 (5초 × 24)
                    _time.sleep(5)
                    cur_maps = get_maps(source_ip, source_secret)
                    if isinstance(cur_maps, list) and cur_maps:
                        # 새로 추가되거나 버전 변경된 맵 찾기
                        for cm in cur_maps:
                            cm_id = cm["id"]
                            cm_ver = cm.get("map_version", 0)
                            if cm_id not in old_map_ids or cm_ver != old_map_ids.get(cm_id, 0):
                                cur_id = cm_id
                                logger.info(f"[auto-sync] 새/변경 맵 감지: id={cur_id}, ver={cm_ver} ({(wait_i+1)*5}초)")
                                new_map_found = True
                                break
                        if new_map_found:
                            break
                    if wait_i % 6 == 5:
                        logger.debug(f"[auto-sync] 대기 중... ({(wait_i+1)*5}초 경과)")

                if not new_map_found:
                    logger.warning("[auto-sync] 120초 내 새 맵 미생성 — DB 보정은 api_get_default_map에서 재시도됨")
                    return

                # 새 맵의 로봇 맵 ID를 DB에 저장 (층 전환용) — 이름 매칭
                try:
                    _rm = sync_db.query(RobotMap).filter(RobotMap.id == saved_map_id).first()
                    if _rm and _rm.name and isinstance(cur_maps, list):
                        matched_id = None
                        for cm in cur_maps:
                            if cm.get("map_name") == _rm.name or cm.get("name") == _rm.name:
                                matched_id = cm.get("id")
                                break
                        if not matched_id:
                            # 이름 매칭 실패 시 새로 감지된 맵 ID 사용 (폴백)
                            matched_id = cur_id if cur_id else (cur_maps[0]["id"] if cur_maps else None)
                        if matched_id:
                            _rm.robot_map_id = matched_id
                            sync_db.commit()
                            logger.info(f"[auto-sync] robot_map_id={matched_id} 저장 (name={_rm.name})")
                    elif _rm:
                        # 이름 없으면 기존 방식 (폴백)
                        fallback_id = cur_id if cur_id else (cur_maps[0]["id"] if isinstance(cur_maps, list) and cur_maps else None)
                        if fallback_id:
                            _rm.robot_map_id = fallback_id
                            sync_db.commit()
                            logger.info(f"[auto-sync] robot_map_id={fallback_id} 저장 (폴백)")
                except Exception as e:
                    logger.warning(f"[auto-sync] robot_map_id 저장 실패: {e}")

                # ── _correct_map_grid_origin()으로 한 번에 보정 (grid_origin + image + JSON) ──
                corrected = _correct_map_grid_origin(sync_db, saved_map_id)
                if corrected:
                    logger.info("[auto-sync] DB 보정 완료 (grid_origin + image + JSON)")
                else:
                    logger.info("[auto-sync] DB 보정 불필요 또는 실패 — api_get_default_map에서 재시도됨")

                # 소스 로봇 위치 재설정 (새 맵 좌표계에 맞게)
                try:
                    import requests as _req
                    _req.post(
                        f"http://{source_ip}:8090/services/start_global_positioning",
                        json={}, timeout=5,
                    )
                    logger.info(f"[auto-sync] 소스 로봇 위치 보정 시작 ({source_ip})")
                except Exception as e:
                    logger.warning(f"[auto-sync] 소스 로봇 위치 보정 실패: {e}")

            except Exception as e:
                logger.error(f"[auto-sync] 오류: {e}")
            finally:
                sync_db.close()

        threading.Thread(target=_auto_sync, daemon=True).start()

    log_activity("map", "map_save",
                 f"맵 저장 완료: {body.get('name', '')}",
                 source="api_save_map")
    return result


DEFAULT_AREA_ID = 21

# 운영자가 맵 관리에서 "메인 적용" 으로 지정한 default area_id.
# 서버 재시작에도 유지되도록 JSON 파일에 영구 저장.
import json as _json_mod
from pathlib import Path as _Path

_DEFAULT_AREA_FILE = _Path(__file__).resolve().parent.parent.parent / "default_area.json"


def _load_persisted_default_area() -> int | None:
    try:
        if _DEFAULT_AREA_FILE.exists():
            data = _json_mod.loads(_DEFAULT_AREA_FILE.read_text(encoding="utf-8"))
            v = data.get("area_id")
            return int(v) if v is not None else None
    except Exception:
        pass
    return None


def _save_persisted_default_area(area_id: int | None) -> None:
    try:
        _DEFAULT_AREA_FILE.write_text(
            _json_mod.dumps({"area_id": area_id}), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[default-area] 저장 실패: {e}")


_current_area_id: int | None = _load_persisted_default_area()  # 서버 시작 시 파일 로드


@router.post("/default-area/{area_id}")
def api_set_default_area(area_id: int, db: Session = Depends(get_db)):
    """운영자가 맵 관리 페이지에서 '메인 적용' 누르면 호출.
    이 area_id 의 최신 맵이 모니터링의 default 가 된다 (POI/라인 포함)."""
    # 검증: area 존재 + 그 area 에 활성 맵이 있는지
    from app.models.map import Area, RobotMap as _RM
    area = db.query(Area).filter(Area.area_id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="영역을 찾을 수 없습니다")
    latest = (
        db.query(_RM)
        .filter(_RM.area_id == area_id, _RM.is_active == True)
        .order_by(_RM.updated_at.desc())
        .first()
    )
    if not latest:
        raise HTTPException(status_code=400, detail="이 영역에 활성 맵이 없습니다")

    global _current_area_id
    _current_area_id = area_id
    _save_persisted_default_area(area_id)
    log_activity(
        "map", "default_area_set",
        f"메인 default 영역 적용: area_id={area_id}, 맵={latest.name}",
        source="api_set_default_area",
    )
    return {"ok": True, "area_id": area_id, "map_id": latest.id, "map_name": latest.name}


@router.get("/default-area")
def api_get_default_area(db: Session = Depends(get_db)):
    """현재 default 영역 + 그 영역의 사업장 ID 조회."""
    from app.models.map import Area
    business_id = None
    if _current_area_id:
        area = db.query(Area).filter(Area.area_id == _current_area_id).first()
        if area:
            business_id = area.business_id
    return {
        "area_id": _current_area_id,
        "business_id": business_id,
        "fallback": DEFAULT_AREA_ID,
    }


@router.get("/default-map")
def api_get_default_map(db: Session = Depends(get_db)):
    """현재 선택된 영역 또는 기본 영역의 최신 맵 반환 (모니터링 페이지 기본값용)"""
    try:
        area_id = _current_area_id or DEFAULT_AREA_ID
        latest = (
            db.query(RobotMap)
            .filter(RobotMap.area_id == area_id, RobotMap.is_active == True)
            .order_by(RobotMap.updated_at.desc())
            .first()
        )
        if not latest:
            latest = (
                db.query(RobotMap)
                .filter(RobotMap.is_active == True)
                .order_by(RobotMap.updated_at.desc())
                .first()
            )
        if not latest:
            return {"map_id": None, "image_url": None, "grid_origin_x": 0, "grid_origin_y": 0, "grid_resolution": 0.05, "area_id": None}

        # grid_origin 보정 확인 (로봇 연결 가능 시 자동 보정, 실패해도 기존 값 반환)
        if _correct_map_grid_origin(db, latest.id):
            db.refresh(latest)

        return {
            "map_id": latest.id,
            "image_url": latest.image_url,
            "grid_origin_x": float(latest.grid_origin_x) if latest.grid_origin_x else 0,
            "grid_origin_y": float(latest.grid_origin_y) if latest.grid_origin_y else 0,
            "grid_resolution": float(latest.grid_resolution) if latest.grid_resolution else 0.05,
            "area_id": latest.area_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"기본 맵 조회 중 오류가 발생했습니다: {e}")


@router.get("/areas/{area_id}/maps")
def api_get_maps_by_area(area_id: int, db: Session = Depends(get_db)):
    """해당 영역의 저장된 맵 목록 조회 (드롭다운용)"""
    try:
        items = get_maps_by_area(db, area_id)
        return {"total": len(items), "items": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"맵 목록 조회 에 실패했습니다: {e}")


@router.get("/maps/{map_id}")
def api_get_map_detail(map_id: int, db: Session = Depends(get_db)):
    """맵 단건 조회"""
    try:
        return get_map_by_id(db, map_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"맵 상세 조회 에 실패했습니다: {e}")


@router.delete("/maps/{map_id}")
def api_delete_saved_map(map_id: int, db: Session = Depends(get_db)):
    """맵 삭제 (POI, 라인, 폴리곤, 파일 포함)"""
    try:
        rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
        map_label = rm.name if rm and rm.name else f"ID:{map_id}"
        result = crud_delete_map(db, map_id)
        log_activity("map", "map_delete",
                     f"맵 삭제: {map_label}",
                     source="api_delete_saved_map")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"맵 삭제 에 실패했습니다: {e}")


@router.post("/maps/{map_id}/sync-to-robot")
def api_sync_map_to_robot(map_id: int, body: dict, db: Session = Depends(get_db)):
    """맵 동기화: 소스 로봇의 맵 데이터를 타겟 로봇에 동기화.

    body: {
        robot_ip: str,          # 타겟 로봇 IP (필수)
        area_name: str,         # 맵 이름 (기본 "synced-map")
        method: "patch"|"full"  # 동기화 방식 (기본 "patch")
    }

    method 옵션:
      - "patch": 오버레이(POI·경로)만 동기화 (빠름, 타겟 로봇의 SLAM 유지)
      - "full":  소스 매핑의 SLAM + 이미지 + 오버레이 전체 업로드 후 current-map 설정
                 ※ 타겟 로봇의 기존 맵 삭제됨, 재부팅 시 py_axbot이 자체 매핑으로 복원할 수 있음
    """
    import json as _json

    robot_ip = body.get("robot_ip")
    area_name = body.get("area_name", "synced-map")
    sync_method = body.get("method", "patch")  # "patch" or "full"
    if not robot_ip:
        raise HTTPException(status_code=400, detail="robot_ip는 필수입니다.")

    target_secret = _find_secret(robot_ip)
    target_robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
    robot_label = target_robot.name if target_robot else robot_ip

    # DB에서 맵 조회
    rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
    if not rm:
        raise HTTPException(status_code=404, detail="맵을 찾지 못했습니다.")

    # robot_map_id가 있으면 해당 맵을 current-map으로 전환 후 동기화
    if rm.robot_map_id:
        try:
            cur_map = http_requests.get(f"http://{robot_ip}:8090/chassis/current-map", timeout=5).json()
            if cur_map.get("id") != rm.robot_map_id:
                http_requests.post(f"http://{robot_ip}:8090/chassis/current-map",
                                   json={"map_id": rm.robot_map_id}, timeout=10)
                import time as _sync_time
                _sync_time.sleep(3)
                logger.info(f"[sync] current-map 전환: {cur_map.get('id')} → {rm.robot_map_id}")
        except Exception as e:
            logger.warning(f"[sync] current-map 전환 실패: {e}")

    # ── 1) 서버에 저장된 매핑 데이터 로드 ──
    mapping_data = None
    if rm.download_url and rm.download_url.startswith("/static/"):
        local_path = Path(__file__).resolve().parent.parent.parent / rm.download_url.lstrip("/")
        if local_path.exists():
            import json as _json_load
            mapping_data = _json_load.loads(local_path.read_text())
            logger.info(f"[sync] 서버 매핑 데이터 로드: {rm.download_url}")

    if not mapping_data:
        raise HTTPException(
            status_code=400,
            detail="서버에 저장된 매핑 데이터가 없습니다. 맵을 다시 저장해주세요.",
        )

    # ── 2) 오버레이 구성 (병합 방식: 로봇 기존 오버레이 보존) ──
    # 우리가 관리하는 오버레이 타입
    CHARGING_TYPES = {"9", "36"}       # 충전소, 도킹포인트
    FIREWALL_TYPES = {"1"}             # 가상벽

    overlay_data = {"type": "FeatureCollection", "features": []}
    overlay_synced = False
    overlay_error = None
    jack_pois = []
    try:
        # 1) 로봇 기존 오버레이 읽기 (robot_map_id 우선, 없으면 current-map)
        existing_other = []       # 관리 외 feature
        existing_charging = []    # 기존 충전소 feature (DB에 없으면 보존용)
        existing_firewall = []    # 기존 가상벽 feature (DB에 없으면 보존용)
        try:
            overlay_read_map_id = effective_robot_map_id
            if not overlay_read_map_id:
                r_cur = http_requests.get(
                    f"http://{robot_ip}:8090/chassis/current-map",
                    headers={"Authorization": f"Secret {target_secret}"},
                    timeout=5,
                )
                if r_cur.status_code == 200:
                    overlay_read_map_id = r_cur.json().get("id")
            if overlay_read_map_id:
                    r_map = http_requests.get(
                        f"http://{robot_ip}:8090/maps/{overlay_read_map_id}",
                        headers={"Authorization": f"Secret {target_secret}"},
                        timeout=5,
                    )
                    if r_map.status_code == 200:
                        import json as _json_overlay
                        old_overlays = _json_overlay.loads(r_map.json().get("overlays", "{}"))
                        for feat in old_overlays.get("features", []):
                            feat_type = str(feat.get("properties", {}).get("type", ""))
                            if feat_type in CHARGING_TYPES:
                                existing_charging.append(feat)
                            elif feat_type in FIREWALL_TYPES:
                                existing_firewall.append(feat)
                            elif feat_type == "34":
                                pass  # Shelves Point는 DB에서 새로 생성하므로 기존 것 제외
                            else:
                                existing_other.append(feat)
                        logger.info(f"[sync] 기존 오버레이: 충전소={len(existing_charging)} "
                                    f"가상벽={len(existing_firewall)} 기타={len(existing_other)}")
        except Exception as e:
            logger.warning(f"[sync] 기존 오버레이 읽기 실패 (새로 구성): {e}")

        # 2) DB에서 새 feature 조회 (있으면 교체, 없으면 기존 보존)
        new_charging = []
        charging_pois = get_charging_pois(db, map_id)
        if charging_pois:
            new_charging = _build_charging_overlay_features(charging_pois)
            logger.info(f"[sync] 충전소 POI {len(charging_pois)}개 → Feature {len(new_charging)}개 (DB)")
        else:
            new_charging = existing_charging
            logger.info(f"[sync] 충전소 POI DB에 없음 → 기존 {len(existing_charging)}개 보존")

        new_firewall = []
        fw_polys = db.query(MapPolygon).filter(
            MapPolygon.map_id == map_id,
            MapPolygon.shape_type == "firewall",
            MapPolygon.is_active == True,
        ).all()
        if fw_polys:
            new_firewall = _build_firewall_overlay_features(fw_polys)
            logger.info(f"[sync] 가상벽 {len(fw_polys)}개 → Feature {len(new_firewall)}개 (DB)")
        else:
            new_firewall = existing_firewall
            logger.info(f"[sync] 가상벽 DB에 없음 → 기존 {len(existing_firewall)}개 보존")

        # 3) jack/standby 타입 POI → Shelves Point overlay 생성 (type=34, subtype=rack)
        new_shelves_points = []
        jack_pois = db.query(MapPOI).filter(
            MapPOI.map_id == map_id,
            MapPOI.poi_type.in_(["jack", "standby"]),
            MapPOI.is_active == True,
        ).all()
        if jack_pois:
            import math as _math
            for jp in jack_pois:
                wx = jp.world_x if jp.world_x is not None else 0.0
                wy = jp.world_y if jp.world_y is not None else 0.0
                yaw_deg = str(round((jp.angle or 0) * 180 / _math.pi, 2))
                new_shelves_points.append({
                    "id": f"shelves_{jp.id}",
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [wx, wy],
                    },
                    "properties": {
                        "deviceIds": None,
                        "dockViaDirection": "front",
                        "enforcePullOut": False,
                        "hasFixedLegs": False,
                        "mapOverlay": True,
                        "name": jp.name,
                        "shelvesState": "0",
                        "subtype": "rack",
                        "type": "34",
                        "yaw": yaw_deg,
                    }
                })
            logger.info(f"[sync] 잭킹 POI {len(jack_pois)}개 → Shelves Point {len(new_shelves_points)}개")

        # 4) 병합: 기타 + 충전소 + 가상벽 + Shelves Point
        merged = existing_other + new_charging + new_firewall + new_shelves_points
        overlay_data["features"] = merged
        overlay_synced = True
        logger.info(f"[sync] 오버레이 병합 완료: 기타={len(existing_other)} + 충전소={len(new_charging)} "
                     f"+ 가상벽={len(new_firewall)} + Shelves Point={len(new_shelves_points)} = {len(merged)}")
    except Exception as e:
        overlay_error = str(e)
        logger.error(f"[sync] 오버레이 처리 실패: {e}")

    logger.info(f"[sync] 타겟: {robot_ip} ({sync_method}), "
                f"carto_map: {len(mapping_data.get('carto_map', ''))}자, "
                f"오버레이: {len(overlay_data.get('features', []))}개")

    # ── 3) jack POI가 있으면 rack.specs 자동 설정 — 로봇 모델명 기준 ──
    if jack_pois:
        try:
            # 대상 로봇의 model 조회 → 모델명에서 사이즈(S300/S600) 자동 매칭 → spec 1개
            from app.constants.rack_specs import build_rack_specs_for_robot_model, spec_name_for_robot_model
            _target_robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
            _model = _target_robot.model if _target_robot else None
            _specs_list = build_rack_specs_for_robot_model(_model)
            _rack_specs = {"rack.specs": _specs_list}
            http_requests.patch(
                f"http://{robot_ip}:8090/system/settings/user",
                headers={"Authorization": f"Secret {target_secret}"},
                json=_rack_specs, timeout=5,
            )
            logger.info(f"[sync] rack.specs 자동 설정 완료 → {robot_ip} (model={_model}, spec={spec_name_for_robot_model(_model)})")
        except Exception as e:
            logger.warning(f"[sync] rack.specs 설정 실패: {e}")

    # ── 4) full 방식: 서버 데이터로 전체 동기화 ──
    # body에서 대상 로봇 맵 ID 지정 가능 (프론트에서 선택)
    body_target_map_id = body.get("target_robot_map_id")
    effective_robot_map_id = body_target_map_id or rm.robot_map_id

    # 선택한 robot_map_id를 DB에도 저장
    if body_target_map_id and rm.robot_map_id != body_target_map_id:
        rm.robot_map_id = body_target_map_id
        db.commit()
        logger.info(f"[sync] robot_map_id={body_target_map_id} DB 저장 (map_id={map_id})")

    if sync_method == "full":
        result = _sync_full_from_server(
            mapping_data, robot_ip, target_secret, area_name,
            overlay_data, overlay_synced, overlay_error,
            target_robot_map_id=effective_robot_map_id,
        )
        log_activity("map", "map_sync",
                     f"맵 동기화 완료 → 로봇 '{robot_label}'",
                     source="api_sync_map_to_robot")
        return result

    # ── 5) patch 방식: 오버레이만 동기화 ──
    result = _sync_patch_to_robot(
        robot_ip, target_secret,
        overlay_data, overlay_synced, overlay_error,
    )
    log_activity("map", "map_sync",
                 f"맵 동기화 완료 → 로봇 '{robot_label}'",
                 source="api_sync_map_to_robot")
    return result


def _sync_full_from_server(
    mapping_data: dict,
    target_ip: str, target_secret: str, area_name: str,
    overlay_data: dict, overlay_synced: bool, overlay_error: str | None,
    target_robot_map_id: int | None = None,
) -> dict:
    """Full 동기화 (서버 데이터 기반): 서버에 저장된 매핑 데이터로 타겟 로봇의 맵 전체 교체.

    전략:
      1) 서버 JSON의 carto_map + occupancy_grid + grid_origin 사용
      2) 타겟 로봇의 네이티브 맵에 PUT /maps/{id} 로 전체 교체
      3) PUT 후 서비스 재시작 → py_axbot이 carto_map에서 맵 재생성
      4) PUT 실패 시 fallback: POST sync 맵 생성
    """
    import json as _json

    carto_map = mapping_data.get("carto_map", "")
    if not carto_map:
        raise HTTPException(status_code=400, detail="매핑 데이터에 SLAM 데이터(carto_map)가 없습니다.")

    occupancy_grid = mapping_data.get("occupancy_grid", "")
    grid_x = mapping_data.get("grid_origin_x", 0)
    grid_y = mapping_data.get("grid_origin_y", 0)
    grid_res = mapping_data.get("grid_resolution", 0.05)

    logger.info(f"[sync:full] 서버 데이터 → {target_ip}: "
                f"carto_map={len(carto_map)}자, grid_origin=({grid_x}, {grid_y})")

    overlay_json = _json.dumps(overlay_data)

    # ── 1. 타겟 로봇의 맵 목록 → 대상 맵 찾기 ──
    native_map_id = None
    native_map_name = None
    sync_map_ids = []
    try:
        old_maps = get_maps(target_ip, target_secret)
        if isinstance(old_maps, list):
            for m in old_maps:
                mid = m.get("id")
                mname = m.get("map_name", "")
                if "-sync-" in str(mname):
                    sync_map_ids.append(mid)
                elif target_robot_map_id and mid == target_robot_map_id:
                    # DB에 지정된 로봇 맵 ID와 일치하는 맵만 대상으로
                    native_map_id = mid
                    native_map_name = mname
                elif not target_robot_map_id and native_map_id is None:
                    # robot_map_id 미지정 시 — 기존 맵 덮어쓰지 않고 새 맵 생성 (pass)
                    pass
    except Exception:
        pass

    logger.info(f"[sync:full] 타겟 {target_ip}: native={native_map_id}({native_map_name}), "
                f"sync맵={sync_map_ids}")

    # ── 2. 네이티브 맵 PUT 전체 교체 ──
    put_success = False
    if native_map_id is not None:
        put_data = {
            "map_name": native_map_name or area_name,
            "carto_map": carto_map,
            "occupancy_grid": occupancy_grid,
            "grid_origin_x": grid_x,
            "grid_origin_y": grid_y,
            "grid_resolution": grid_res,
            "overlays": overlay_json,
        }
        try:
            update_map_by_id(target_ip, target_secret, native_map_id, put_data)
            put_success = True
            logger.info(f"[sync:full] 네이티브 맵 PUT 성공: id={native_map_id}")
        except Exception as e:
            logger.warning(f"[sync:full] 네이티브 맵 PUT 실패: {e} → fallback 진행")

    if put_success:
        # PUT 후 서비스 재시작 → py_axbot이 carto_map에서 맵 재생성
        try:
            restart_robot_service(target_ip, target_secret)
            logger.info(f"[sync:full] 서비스 재시작 요청 완료 (약 60-90초)")
        except Exception as e:
            logger.error(f"[sync:full] 서비스 재시작 실패: {e}")

        # 서비스 재시작 후 Shelves Point overlay 재적용 (백그라운드)
        shelves_features = [f for f in overlay_data.get("features", [])
                           if str(f.get("properties", {}).get("type", "")) == "34"]
        if shelves_features:
            import threading
            _target_map_id = native_map_id  # 대상 맵 ID 고정 (클로저용)
            def _patch_shelves_after_restart():
                """재시작 완료 대기 후 Shelves Point overlay PATCH"""
                import time as _time
                _time.sleep(90)  # 재시작 대기 (60-90초)
                for attempt in range(3):
                    try:
                        # 대상 맵에 current-map 전환
                        http_requests.post(
                            f"http://{target_ip}:8090/chassis/current-map",
                            headers={"Authorization": f"Secret {target_secret}"},
                            json={"map_id": _target_map_id}, timeout=10,
                        )
                        _time.sleep(3)

                        # 대상 맵의 overlay 읽기
                        r_map = http_requests.get(
                            f"http://{target_ip}:8090/maps/{_target_map_id}",
                            headers={"Authorization": f"Secret {target_secret}"},
                            timeout=10,
                        )
                        if r_map.status_code != 200:
                            _time.sleep(15)
                            continue
                        existing_ov = _json.loads(r_map.json().get("overlays", "{}"))
                        existing_feats = existing_ov.get("features", [])

                        # 기존 Shelves Point 제거 후 새로 추가
                        merged = [f for f in existing_feats
                                  if str(f.get("properties", {}).get("type", "")) != "34"]
                        merged.extend(shelves_features)

                        new_ov = _json.dumps({"type": "FeatureCollection", "features": merged})
                        http_requests.patch(
                            f"http://{target_ip}:8090/maps/{_target_map_id}",
                            headers={"Authorization": f"Secret {target_secret}"},
                            json={"overlays": new_ov}, timeout=10,
                        )
                        # current-map 재선택 → overlay 리로드
                        http_requests.post(
                            f"http://{target_ip}:8090/chassis/current-map",
                            headers={"Authorization": f"Secret {target_secret}"},
                            json={"map_id": _target_map_id}, timeout=10,
                        )
                        logger.info(f"[sync:full] Shelves Point {len(shelves_features)}개 재적용 완료 (맵 {_target_map_id})")
                        return
                    except Exception as ex:
                        logger.warning(f"[sync:full] Shelves Point 재적용 시도 {attempt+1} 실패: {ex}")
                        _time.sleep(15)

            threading.Thread(target=_patch_shelves_after_restart, daemon=True).start()
            logger.info(f"[sync:full] Shelves Point 재적용 예약됨 (90초 후, 맵 {_target_map_id}, {len(shelves_features)}개)")

        # 이전 sync 맵 삭제
        for sid in sync_map_ids:
            try:
                delete_map_by_id(target_ip, target_secret, sid)
                logger.info(f"[sync:full] sync 맵 삭제: id={sid}")
            except Exception:
                pass

        return {
            "message": "맵 동기화 완료 (서버 데이터 → 네이티브 맵 전체 교체)",
            "robot_ip": target_ip,
            "robot_map_id": native_map_id,
            "overlay_features": len(overlay_data.get("features", [])),
            "overlay_synced": overlay_synced,
            "overlay_error": overlay_error,
            "method": "full_put",
            "carto_map_size": len(carto_map),
        }

    # ── 3. PUT 실패 fallback: POST sync 맵 생성 ──
    logger.warning("[sync:full] fallback: POST sync 맵 생성")

    # 네이티브 맵에 overlay만 PATCH
    if native_map_id is not None:
        try:
            patch_map_by_id(target_ip, target_secret, native_map_id, {
                "overlays": overlay_json,
            })
            logger.info(f"[sync:full:fb] 네이티브 맵 overlay PATCH: id={native_map_id}")
        except Exception as e:
            logger.warning(f"[sync:full:fb] overlay PATCH 실패: {e}")

    # 이전 sync 맵 삭제
    for sid in sync_map_ids:
        try:
            delete_map_by_id(target_ip, target_secret, sid)
        except Exception:
            pass

    # POST sync 맵 생성 (재부팅 시 삭제됨)
    sync_map_name = f"{area_name}-sync-{target_ip.split('.')[-1]}"
    post_data = {
        "map_name": sync_map_name,
        "carto_map": carto_map,
        "occupancy_grid": occupancy_grid,
        "grid_origin_x": grid_x,
        "grid_origin_y": grid_y,
        "grid_resolution": grid_res,
        "overlays": overlay_json,
    }

    try:
        created = create_map(target_ip, target_secret, post_data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"타겟 로봇 맵 생성 에 실패했습니다: {e}")

    new_map_id = created.get("id")
    logger.info(f"[sync:full:fb] sync 맵 생성: id={new_map_id}")

    try:
        set_current_map(target_ip, target_secret, {"map_id": new_map_id})
        logger.info(f"[sync:full:fb] current-map → sync id={new_map_id}")
    except Exception as e:
        logger.error(f"[sync:full:fb] current-map 설정 실패: {e}")

    # 새로 생성된 맵 ID를 DB에 저장 (robot_map_id 미지정이었던 경우)
    if new_map_id and not target_robot_map_id:
        try:
            from app.database import SessionLocal as _SL
            _db = _SL()
            try:
                # area_name으로 매칭하여 robot_map_id 저장
                _rm = _db.query(RobotMap).filter(
                    RobotMap.is_active == True,
                    RobotMap.name == area_name,
                    RobotMap.robot_map_id == None,
                ).first()
                if _rm:
                    _rm.robot_map_id = new_map_id
                    _db.commit()
                    logger.info(f"[sync:full:fb] robot_map_id={new_map_id} DB 저장 (name={area_name})")
            finally:
                _db.close()
        except Exception as e:
            logger.warning(f"[sync:full:fb] robot_map_id DB 저장 실패: {e}")

    return {
        "message": "맵 동기화 완료 (fallback: sync 맵 — 재부팅 시 삭제됨)",
        "robot_ip": target_ip,
        "robot_map_id": new_map_id,
        "native_map_id": native_map_id,
        "overlay_features": len(overlay_data.get("features", [])),
        "overlay_synced": overlay_synced,
        "overlay_error": overlay_error,
        "method": "full_fallback",
        "carto_map_size": len(carto_map),
    }


@router.post("/maps/{map_id}/sync-overlays")
def api_sync_overlays_to_robot(map_id: int, body: dict, db: Session = Depends(get_db)):
    """rack area 등 overlay만 로봇에 PATCH로 전송.

    body: { robot_ip: str }
    """
    import json as _json

    robot_ip = body.get("robot_ip")
    if not robot_ip:
        raise HTTPException(status_code=400, detail="robot_ip는 필수입니다.")

    target_secret = _find_secret(robot_ip)

    # DB에서 충전소 overlay
    CHARGING_TYPES = {"9", "36"}
    charging_features = []
    charging_pois = get_charging_pois(db, map_id)
    if charging_pois:
        charging_features = _build_charging_overlay_features(charging_pois)

    # DB에서 가상벽 overlay
    fw_features = []
    fw_polys = db.query(MapPolygon).filter(
        MapPolygon.map_id == map_id,
        MapPolygon.shape_type == "firewall",
        MapPolygon.is_active == True,
    ).all()
    if fw_polys:
        fw_features = _build_firewall_overlay_features(fw_polys)

    # DB에서 jack/standby POI → Shelves Point overlay (type=34, subtype=rack)
    shelves_features = []
    from app.models.map import MapPOI
    jack_pois = db.query(MapPOI).filter(
        MapPOI.map_id == map_id,
        MapPOI.poi_type.in_(["jack", "standby"]),
        MapPOI.is_active == True,
    ).all()
    import math as _math2
    for jp in jack_pois:
        wx = jp.world_x if jp.world_x is not None else 0.0
        wy = jp.world_y if jp.world_y is not None else 0.0
        yaw_deg = str(round((jp.angle or 0) * 180 / _math2.pi, 2))
        shelves_features.append({
            "id": f"shelves_{jp.id}",
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [wx, wy]},
            "properties": {
                "deviceIds": None,
                "dockViaDirection": "front",
                "enforcePullOut": False,
                "hasFixedLegs": False,
                "mapOverlay": True,
                "name": jp.name,
                "shelvesState": "0",
                "subtype": "rack",
                "type": "34",
                "yaw": yaw_deg,
            }
        })

    # DB에서 해당 맵의 robot_map_id 조회 → 대상 맵 결정
    rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
    target_map_id = rm.robot_map_id if rm else None

    # robot_map_id가 없으면 current-map 사용 (폴백)
    if not target_map_id:
        try:
            r_cur = http_requests.get(
                f"http://{robot_ip}:8090/chassis/current-map",
                headers={"Authorization": f"Secret {target_secret}"},
                timeout=5,
            )
            if r_cur.status_code == 200:
                target_map_id = r_cur.json().get("id")
        except Exception:
            pass

    if not target_map_id:
        raise HTTPException(400, "대상 로봇 맵을 찾을 수 없습니다.")

    # 로봇 기존 overlay에서 관리 외 feature 보존
    existing_other = []
    try:
        r_map = http_requests.get(
            f"http://{robot_ip}:8090/maps/{target_map_id}",
            headers={"Authorization": f"Secret {target_secret}"},
            timeout=5,
        )
        if r_map.status_code == 200:
            old_overlays = _json.loads(r_map.json().get("overlays", "{}"))
            for feat in old_overlays.get("features", []):
                feat_type = str(feat.get("properties", {}).get("type", ""))
                # 충전소, 가상벽, Shelves Point 제외 → 나머지 보존
                if feat_type not in CHARGING_TYPES and feat_type != "1" and feat_type != "34":
                    existing_other.append(feat)
    except Exception as e:
        logger.warning(f"[sync-overlays] 기존 overlay 읽기 실패: {e}")

    # 병합
    merged = existing_other + charging_features + fw_features + shelves_features
    overlay_data = {"type": "FeatureCollection", "features": merged}
    overlay_json = _json.dumps(overlay_data)

    # PATCH → 대상 맵에 overlay 적용 + current-map 재선택
    try:
        patch_map_by_id(robot_ip, target_secret, target_map_id, {"overlays": overlay_json})
        # current-map 재선택 → 로봇이 overlay 리로드
        http_requests.post(
            f"http://{robot_ip}:8090/chassis/current-map",
            headers={"Authorization": f"Secret {target_secret}"},
            json={"map_id": target_map_id}, timeout=10,
        )
        logger.info(f"[sync-overlays] overlay PATCH + 맵 리로드 완료: {len(merged)}개 features")
    except Exception as e:
        logger.error(f"[sync-overlays] overlay PATCH 실패: {e}")
        raise HTTPException(status_code=500, detail=f"overlay 전송 실패: {e}")

    # jack POI가 있으면 rack.specs 자동 설정
    if jack_pois:
        try:
            from app.constants.rack_specs import build_rack_specs_for_robot_model, spec_name_for_robot_model
            _target_robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
            _model = _target_robot.model if _target_robot else None
            http_requests.patch(
                f"http://{robot_ip}:8090/system/settings/user",
                headers={"Authorization": f"Secret {target_secret}"},
                json={"rack.specs": build_rack_specs_for_robot_model(_model)}, timeout=5,
            )
            logger.info(f"[sync-overlays] rack.specs 자동 설정 완료 → {robot_ip} (model={_model}, spec={spec_name_for_robot_model(_model)})")
        except Exception as e:
            logger.warning(f"[sync-overlays] rack.specs 설정 실패: {e}")

    log_activity("map", "overlay_sync",
                 f"overlay 동기화 → {robot_ip}: 충전소={len(charging_features)} 가상벽={len(fw_features)} Shelves Point={len(shelves_features)}",
                 source="api_sync_overlays_to_robot")

    return {
        "message": "overlay 동기화 완료",
        "robot_ip": robot_ip,
        "charging": len(charging_features),
        "firewall": len(fw_features),
        "shelves_point": len(shelves_features),
        "total_features": len(merged),
    }


def _sync_patch_to_robot(
    target_ip: str, target_secret: str,
    overlay_data: dict, overlay_synced: bool, overlay_error: str | None,
) -> dict:
    """Patch 동기화: 오버레이(충전소 POI)만 타겟 로봇의 현재 맵에 PATCH."""
    import json as _json

    target_map_id = None

    # current-map 확인
    try:
        current = get_current_map(target_ip, target_secret)
        if isinstance(current, dict) and current.get("id"):
            target_map_id = current["id"]
    except Exception:
        pass

    # current-map 없으면 맵 목록에서 첫 번째 사용
    if target_map_id is None:
        try:
            maps_list = get_maps(target_ip, target_secret)
            if isinstance(maps_list, list) and maps_list:
                target_map_id = maps_list[0]["id"]
        except Exception:
            pass

    if target_map_id is None:
        raise HTTPException(
            status_code=502,
            detail="타겟 로봇에 맵이 없습니다. full 동기화를 사용하세요.",
        )

    patch_data = {"overlays": _json.dumps(overlay_data)}

    try:
        patch_map_by_id(target_ip, target_secret, target_map_id, patch_data)
        logger.info(f"[sync:patch] PATCH 완료: {target_ip} id={target_map_id}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"타겟 로봇 맵 PATCH 에 실패했습니다: {e}")

    return {
        "message": "오버레이 동기화 완료",
        "robot_ip": target_ip,
        "robot_map_id": target_map_id,
        "overlay_features": len(overlay_data.get("features", [])),
        "overlay_synced": overlay_synced,
        "overlay_error": overlay_error,
        "method": "patch",
    }


@router.get("/{robot_ip}/maps/{robot_map_id}/detail")
def api_get_robot_map_detail(robot_ip: str, robot_map_id: int):
    """로봇에 저장된 맵 상세 정보 조회 (overlays 확인용)"""
    try:
        import json as _json
        secret = _find_secret(robot_ip)
        detail = get_robot_map_by_id(robot_ip, secret, robot_map_id)

        # overlays가 문자열이면 파싱해서 보기 좋게 반환
        raw = detail.get("overlays")
        if isinstance(raw, str):
            try:
                detail["overlays"] = _json.loads(raw)
            except Exception:
                pass
        return detail
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"로봇 맵 상세 조회 중 오류가 발생했습니다: {e}")


# ── 맵 요소 (POI·라인) 저장 / 조회 ──────────────────────────

@router.put("/maps/{map_id}/elements")
def api_save_map_elements(map_id: int, body: dict, db: Session = Depends(get_db)):
    """맵의 POI·라인을 전체 교체 방식으로 저장.
    저장 후 같은 area 의 경로 웨이포인트가 다른 맵 POI를 참조하고 있으면
    POI 이름이 같은 것끼리 자동 재매핑한다."""
    try:
        result = save_map_elements(db, map_id, body)
        rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
        map_label = rm.name if rm and rm.name else ""
        log_activity("map", "elements_save",
                     f"맵 요소 저장 완료: {map_label}",
                     source="api_save_map_elements")
        # 경로 웨이포인트 자동 재매핑 (실패는 치명적이지 않으니 swallow)
        try:
            from app.crud.map import remap_task_waypoints_to_map
            remap_result = remap_task_waypoints_to_map(db, map_id)
            if remap_result.get("updated"):
                log_activity(
                    "map", "routes_remapped",
                    f"맵 '{map_label}' 저장에 따라 경로 웨이포인트 {remap_result['updated']}개 자동 재매핑",
                    source="api_save_map_elements",
                )
            if remap_result.get("missing"):
                logger.warning(f"[remap] 매칭 실패 POI: {remap_result['missing']}")
        except Exception as e:
            logger.warning(f"[remap] 자동 재매핑 실패: {e}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"맵 요소 저장 에 실패했습니다: {e}")


@router.get("/maps/{map_id}/elements")
def api_get_map_elements(map_id: int, db: Session = Depends(get_db)):
    """맵에 저장된 POI·라인 조회"""
    try:
        return get_map_elements(db, map_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"맵 요소 조회 에 실패했습니다: {e}")


# ── 이미지 프록시 ─────────────────────────────────────────────

@router.get("/proxy-image")
def api_proxy_image(url: str = Query(..., description="로봇 이미지 URL")):
    """로봇의 이미지를 프록시하여 CORS 문제 없이 프론트엔드에 전달."""
    try:
        res = http_requests.get(url, timeout=10)
        res.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"이미지를 가져오지 못했습니다: {exc}")
    content_type = res.headers.get("Content-Type", "image/png")
    return Response(content=res.content, media_type=content_type)


# ── 로봇 프록시: /maps ───────────────────────────────────────

@router.get("/{robot_ip}/maps")
def api_get_robot_maps(robot_ip: str):
    """로봇 맵 목록 조회 (로봇 직접 통신)"""
    secret = _find_secret(robot_ip)
    return _proxy(get_maps, robot_ip, secret)


@router.get("/{robot_ip}/maps/{map_name}")
def api_get_robot_map(robot_ip: str, map_name: str):
    """로봇 맵 상세 조회"""
    secret = _find_secret(robot_ip)
    return _proxy(get_map, robot_ip, secret, map_name)


@router.post("/{robot_ip}/maps", status_code=201)
def api_create_robot_map(robot_ip: str, body: dict):
    """로봇 맵 생성"""
    secret = _find_secret(robot_ip)
    return _proxy(create_map, robot_ip, secret, body)


@router.put("/{robot_ip}/maps/{map_name}")
def api_update_robot_map(robot_ip: str, map_name: str, body: dict):
    """로봇 맵 수정"""
    secret = _find_secret(robot_ip)
    return _proxy(update_map, robot_ip, secret, map_name, body)


@router.delete("/{robot_ip}/maps/{map_name}")
def api_delete_robot_map(robot_ip: str, map_name: str):
    """로봇 맵 삭제"""
    secret = _find_secret(robot_ip)
    return _proxy(delete_map, robot_ip, secret, map_name)


@router.patch("/{robot_ip}/maps/{map_name}")
def api_patch_robot_map(robot_ip: str, map_name: str, body: dict):
    """로봇 맵 부분 수정"""
    secret = _find_secret(robot_ip)
    return _proxy(patch_map, robot_ip, secret, map_name, body)


# ── 로봇 프록시: /chassis ─────────────────────────────────────

@router.post("/{robot_ip}/chassis/pose")
def api_set_chassis_pose(robot_ip: str, body: dict):
    """로봇 포즈(위치·방향) 설정"""
    secret = _find_secret(robot_ip)
    return _proxy(set_chassis_pose, robot_ip, secret, body)


@router.post("/{robot_ip}/chassis/current-map")
def api_set_current_map(robot_ip: str, body: dict):
    """현재 지도 설정 — {map_id: N}"""
    secret = _find_secret(robot_ip)
    return _proxy(set_current_map, robot_ip, secret, body)


@router.post("/relocalize")
def api_relocalize_robots(body: dict, db: Session = Depends(get_db)):
    """선택된 로봇들의 위치를 충전소 도킹 상태 기준으로 재조정.

    body: { robot_ips: list[str] }

    우선순위:
    1) charging_id → 충전소 좌표 + yaw 반대(로봇 정면이 충전기 밖을 향함)
    2) standby_id → 랙 위치 좌표 (POI yaw 그대로)
    """
    from app.models.map import MapPOI

    robot_ips = body.get("robot_ips", [])
    if not robot_ips:
        raise HTTPException(status_code=400, detail="robot_ips는 필수입니다.")

    # 위치 보정 전 grid_origin 정합성 확인 (잘못된 좌표계 방지)
    _corrected_map_ids: set = set()
    results = []
    for robot_ip in robot_ips:
        result = {"robot_ip": robot_ip, "success": False, "message": ""}
        try:
            secret = _find_secret(robot_ip)
            robot = db.query(Robot).filter(
                Robot.ip_address == robot_ip, Robot.is_active == True
            ).first()
            if not robot:
                result["message"] = "DB에 등록되지 않은 로봇입니다."
                results.append(result)
                continue

            # 위치재조정 — 충전기 도킹 상태 기준. charging_id 우선.
            poi = None
            poi_kind = ""
            use_docking_offset = False

            if robot.charging_id:
                poi = db.query(MapPOI).filter(
                    MapPOI.id == robot.charging_id,
                    MapPOI.is_active == True,
                ).first()
                poi_kind = "충전소"
                use_docking_offset = True

            if not poi and robot.standby_id:
                poi = db.query(MapPOI).filter(
                    MapPOI.id == robot.standby_id,
                    MapPOI.is_active == True,
                ).first()
                poi_kind = "랙 위치"
                use_docking_offset = False

            if not poi:
                result["message"] = "충전소 또는 랙 위치가 지정되지 않았습니다."
                results.append(result)
                continue

            # POI가 속한 맵의 grid_origin 보정 (맵당 1회만)
            if poi.map_id and poi.map_id not in _corrected_map_ids:
                _correct_map_grid_origin(db, poi.map_id)
                _corrected_map_ids.add(poi.map_id)

            if poi.world_x is None or poi.world_y is None:
                result["message"] = f"{poi_kind} POI에 월드 좌표가 없습니다."
                results.append(result)
                continue

            yaw_rad = poi.angle if poi.angle is not None else 0.0

            if use_docking_offset:
                # 충전소: yaw 방향으로 0.9m 앞, 충전소를 바라보는 방향
                target_x = poi.world_x + DOCKING_OFFSET * math.cos(yaw_rad)
                target_y = poi.world_y + DOCKING_OFFSET * math.sin(yaw_rad)
                target_yaw = yaw_rad + math.pi
            else:
                # 대기지점: 정확한 좌표, POI 각도 그대로
                target_x = poi.world_x
                target_y = poi.world_y
                target_yaw = yaw_rad

            set_chassis_pose(robot_ip, secret, {
                "position": [target_x, target_y, 0],
                "ori": target_yaw,
            })

            result["success"] = True
            result["message"] = f"{poi_kind} '{poi.name}' 위치재조정 완료"
            logger.info(f"[relocalize] {robot_ip}: {result['message']}")
            log_activity("map", "relocalize",
                         f"로봇 '{robot.name}' 위치재조정 완료 → {poi_kind} '{poi.name}'",
                         source="api_relocalize_robots")

        except HTTPException:
            result["message"] = f"등록되지 않은 로봇: {robot_ip}"
        except Exception as e:
            result["message"] = f"위치재조정 실패: {e}"
            logger.error(f"[relocalize] {robot_ip}: {e}")

        results.append(result)

    return {"results": results}


# ── 로봇 프록시: /mappings ───────────────────────────────────

@router.get("/{robot_ip}/mappings")
def api_get_mappings(robot_ip: str):
    """맵핑 목록 조회 (로봇 직접 통신)"""
    secret = _find_secret(robot_ip)
    return _proxy(get_mappings, robot_ip, secret)


@router.post("/{robot_ip}/mappings", status_code=201)
def api_create_mapping(robot_ip: str, body: dict, db: Session = Depends(get_db)):
    """맵핑 시작"""
    secret = _find_secret(robot_ip)
    result = _proxy(create_mapping, robot_ip, secret, body)
    robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
    log_activity("map", "mapping_start",
                 f"맵핑 시작 (로봇: {robot.name if robot else '알 수 없는 로봇'})",
                 source="api_create_mapping")
    return result


@router.put("/{robot_ip}/mappings")
def api_update_mapping(robot_ip: str, body: dict):
    """맵핑 수정"""
    secret = _find_secret(robot_ip)
    return _proxy(update_mapping, robot_ip, secret, body)


@router.delete("/{robot_ip}/mappings")
def api_delete_mapping(robot_ip: str):
    """맵핑 삭제"""
    secret = _find_secret(robot_ip)
    return _proxy(delete_mapping, robot_ip, secret)


@router.patch("/{robot_ip}/mappings")
def api_patch_mapping(robot_ip: str, body: dict):
    """맵핑 부분 수정"""
    secret = _find_secret(robot_ip)
    return _proxy(patch_mapping, robot_ip, secret, body)


# ── 로봇 프록시: /mappings/current ───────────────────────────

@router.get("/{robot_ip}/mappings/current")
def api_get_current_mapping(robot_ip: str):
    """현재 맵핑 조회"""
    secret = _find_secret(robot_ip)
    return _proxy(get_current_mapping, robot_ip, secret)


@router.post("/{robot_ip}/mappings/current", status_code=201)
def api_create_current_mapping(robot_ip: str, body: dict):
    """현재 맵핑 시작"""
    secret = _find_secret(robot_ip)
    return _proxy(create_current_mapping, robot_ip, secret, body)


@router.put("/{robot_ip}/mappings/current")
def api_update_current_mapping(robot_ip: str, body: dict):
    """현재 맵핑 수정"""
    secret = _find_secret(robot_ip)
    return _proxy(update_current_mapping, robot_ip, secret, body)


@router.delete("/{robot_ip}/mappings/current")
def api_delete_current_mapping(robot_ip: str, db: Session = Depends(get_db)):
    """현재 맵핑 중지"""
    secret = _find_secret(robot_ip)
    result = _proxy(delete_current_mapping, robot_ip, secret)
    robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
    log_activity("map", "mapping_stop",
                 f"맵핑 중지 (로봇: {robot.name if robot else '알 수 없는 로봇'})",
                 source="api_delete_current_mapping")
    return result


@router.patch("/{robot_ip}/mappings/current")
def api_patch_current_mapping(robot_ip: str, body: dict):
    """현재 맵핑 부분 수정"""
    secret = _find_secret(robot_ip)
    return _proxy(patch_current_mapping, robot_ip, secret, body)


# ── 로봇 프록시: /mappings/{id} (parametric — /current 뒤에 위치) ──

@router.get("/{robot_ip}/mappings/{mapping_id}")
def api_get_mapping_by_id(robot_ip: str, mapping_id: int):
    """맵핑 단건 조회 (로봇 직접 통신)"""
    secret = _find_secret(robot_ip)
    return _proxy(get_mapping_by_id, robot_ip, secret, mapping_id)


# ── WebSocket: 실시간 맵핑 데이터 중계 ───────────────────────

@router.websocket("/ws/{robot_ip}")
async def ws_map_relay(websocket: WebSocket, robot_ip: str, topics: Optional[str] = Query(None)):
    """
    프론트엔드 ↔ FastAPI ↔ 로봇 WebSocket 릴레이.

    연결 시 자동으로 맵핑 관련 토픽을 구독하고,
    로봇에서 수신한 데이터를 프론트엔드로 실시간 전달합니다.
    프론트엔드에서 보내는 메시지(enable_topic/disable_topic 등)는 로봇으로 전달됩니다.

    topics 파라미터로 구독할 토픽을 쉼표로 구분하여 지정 가능.
    예: ?topics=/tracked_pose,/trajectory
    """
    secret = DEFAULT_SECRET

    await websocket.accept()

    topic_list = [t.strip() for t in topics.split(",")] if topics else None
    relay = RobotWSRelay(robot_ip, secret, topics=topic_list)
    try:
        relay.start()
    except Exception as e:
        logger.error(f"[ws_map_relay] 로봇 WS 릴레이 시작 실패 ({robot_ip}): {e}")
        await websocket.send_text(f'{{"error": "로봇 WebSocket 연결 실패: {e}"}}')
        await websocket.close(code=1011)
        return

    try:
        while True:
            try:
                client_msg = await asyncio.wait_for(
                    websocket.receive_text(), timeout=0.05
                )
                relay.send_to_robot(client_msg)
            except asyncio.TimeoutError:
                pass

            messages = relay.poll()
            for msg in messages:
                await websocket.send_text(msg)

            if not messages:
                await asyncio.sleep(0.02)

    except WebSocketDisconnect:
        logger.info(f"[ws_map_relay] 클라이언트 연결 종료 ({robot_ip})")
    except Exception as e:
        logger.error(f"[ws_map_relay] 릴레이 중 오류 ({robot_ip}): {e}")
    finally:
        relay.stop()


@router.get("/active-pois")
def api_get_active_pois(area_id: int | None = None, db: Session = Depends(get_db)):
    """활성 맵의 POI 목록 반환 (area_id 지정 시 해당 영역의 모든 활성 맵 POI 통합).
    standby-pois / charging-pois 와 동일한 정책 — 같은 area 라면 여러 맵에 분산된 POI 모두 포함.
    """
    query = db.query(RobotMap).filter(RobotMap.is_active == True)
    if area_id:
        query = query.filter(RobotMap.area_id == area_id)
    active_maps = query.order_by(RobotMap.id.desc()).all()
    if not active_maps:
        return []
    map_ids = [m.id for m in active_maps]
    pois = db.query(MapPOI).filter(
        MapPOI.map_id.in_(map_ids),
        MapPOI.is_active == True,
    ).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "poi_type": p.poi_type,
            "world_x": p.world_x,
            "world_y": p.world_y,
            "angle": p.angle,
            "rack_size": p.rack_size,
        }
        for p in pois
    ]
