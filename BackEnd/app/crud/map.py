import json
import logging

from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.map import Business, Area, RobotMap, MapPOI, MapLine, MapPolygon
from app.models.robot import Robot

logger = logging.getLogger(__name__)


def _safe_json_loads(value: str | None):
    """JSON 문자열을 안전하게 파싱한다. 실패 시 None 반환."""
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"JSON 파싱 실패: {e}")
        return None


# ── Business CRUD ─────────────────────────────────────────────

def get_businesses(db: Session) -> list[dict]:
    items = (
        db.query(Business)
        .filter(Business.is_active == True)
        .order_by(Business.name)
        .all()
    )
    return [
        {
            "business_id": b.business_id,
            "name": b.name,
            "areas": [
                {"area_id": a.area_id, "name": a.name}
                for a in b.areas if a.is_active
            ],
            "created_at": b.created_at,
            "updated_at": b.updated_at,
        }
        for b in items
    ]


def create_business(db: Session, name: str) -> dict:
    exists = db.query(Business).filter(Business.name == name).first()
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"이미 존재하는 사업장입니다: {name}")
    biz = Business(name=name)
    db.add(biz)
    db.commit()
    db.refresh(biz)
    return {"business_id": biz.business_id, "name": biz.name, "created_at": biz.created_at, "updated_at": biz.updated_at}


def delete_business(db: Session, business_id: int) -> dict:
    biz = db.query(Business).filter(Business.business_id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="사업장을 찾지 못했습니다.")
    biz_name = biz.name
    # 사업장에 속한 영역들 삭제 (영역 → 맵 → POI/라인/폴리곤/파일 포함)
    areas = db.query(Area).filter(Area.business_id == business_id).all()
    for area in areas:
        delete_area(db, area.area_id)
    db.delete(biz)
    db.commit()
    return {"message": f"사업장 '{biz_name}'이(가) 삭제되었습니다.", "business_id": business_id}


# ── Area CRUD ─────────────────────────────────────────────────

def get_areas(db: Session, business_id: int) -> list[dict]:
    items = (
        db.query(Area)
        .filter(Area.business_id == business_id, Area.is_active == True)
        .order_by(Area.name)
        .all()
    )
    return [
        {
            "area_id": a.area_id,
            "business_id": a.business_id,
            "name": a.name,
            "is_main_floor": getattr(a, "is_main_floor", True),
            "created_at": a.created_at,
            "updated_at": a.updated_at,
        }
        for a in items
    ]


def create_area(db: Session, business_id: int, name: str) -> dict:
    biz = db.query(Business).filter(Business.business_id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="사업장을 찾지 못했습니다.")
    area = Area(business_id=business_id, name=name)
    db.add(area)
    db.commit()
    db.refresh(area)
    return {"area_id": area.area_id, "business_id": area.business_id, "name": area.name, "created_at": area.created_at, "updated_at": area.updated_at}


def delete_area(db: Session, area_id: int) -> dict:
    area = db.query(Area).filter(Area.area_id == area_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="영역을 찾지 못했습니다.")
    area_name = area.name
    # 영역에 속한 맵들 삭제 (맵 → POI/라인/폴리곤/파일 포함)
    maps = db.query(RobotMap).filter(RobotMap.area_id == area_id).all()
    for rm in maps:
        delete_map(db, rm.id)
    # 영역 삭제
    db.delete(area)
    db.commit()
    return {"message": f"영역 '{area_name}'이(가) 삭제되었습니다.", "area_id": area_id}


# ── RobotMap CRUD ─────────────────────────────────────────────

def _map_to_response(rm: RobotMap) -> dict:
    return {
        "id": rm.id,
        "business_id": rm.business_id,
        "area_id": rm.area_id,
        "robot_sn": rm.robot_sn,
        "mapping_id": rm.mapping_id,
        "name": rm.name,
        "thumbnail_url": rm.thumbnail_url,
        "image_url": rm.image_url,
        "grid_origin_x": rm.grid_origin_x,
        "grid_origin_y": rm.grid_origin_y,
        "grid_resolution": rm.grid_resolution,
        "initial_x": rm.initial_x,
        "initial_y": rm.initial_y,
        "initial_ori": rm.initial_ori,
        "url": rm.url,
        "start_time": rm.start_time,
        "end_time": rm.end_time,
        "state": rm.state,
        "bag_id": rm.bag_id,
        "bag_url": rm.bag_url,
        "download_url": rm.download_url,
        "trajectories_url": rm.trajectories_url,
        "is_active": rm.is_active,
        "created_at": rm.created_at,
        "updated_at": rm.updated_at,
    }


def save_robot_map(db: Session, data: dict) -> dict:
    """매핑 종료 후 결과를 DB에 저장."""
    business_id = data.get("business_id")
    area_id = data.get("area_id")

    if not business_id or not area_id:
        raise HTTPException(status_code=400, detail="business_id와 area_id는 필수입니다.")

    biz = db.query(Business).filter(Business.business_id == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="사업장을 찾지 못했습니다.")
    area = db.query(Area).filter(Area.area_id == area_id, Area.business_id == business_id).first()
    if not area:
        raise HTTPException(status_code=404, detail="영역을 찾지 못했습니다.")

    rm = RobotMap(
        business_id=business_id,
        area_id=area_id,
        robot_sn=data.get("robot_sn"),
        mapping_id=data.get("mapping_id"),
        name=data.get("name"),
        thumbnail_url=data.get("thumbnail_url"),
        image_url=data.get("image_url"),
        grid_origin_x=data.get("grid_origin_x", 0.0),
        grid_origin_y=data.get("grid_origin_y", 0.0),
        grid_resolution=data.get("grid_resolution", 0.0),
        initial_x=data.get("initial_x", 0.0),
        initial_y=data.get("initial_y", 0.0),
        initial_ori=data.get("initial_ori", 0.0),
        url=data.get("url"),
        start_time=data.get("start_time"),
        end_time=data.get("end_time"),
        state=data.get("state"),
        bag_id=data.get("bag_id"),
        bag_url=data.get("bag_url"),
        download_url=data.get("download_url"),
        pbstream_url=data.get("pbstream_url"),
        trajectories_url=data.get("trajectories_url"),
    )
    db.add(rm)
    db.commit()
    db.refresh(rm)
    return _map_to_response(rm)


def remap_task_waypoints_to_map(db: Session, target_map_id: int) -> dict:
    """target_map_id 의 POI 이름을 키로, 같은 area 의 다른 맵 POI를 참조 중인
    TaskRouteWaypoint 들을 target 맵의 같은 이름 POI 로 자동 재매핑.

    반환: {"updated": N, "missing": [<old_poi_name>...], "target_map_id": ...}
    """
    from app.models.task import TaskRouteWaypoint

    target = db.query(RobotMap).filter(RobotMap.id == target_map_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="대상 맵을 찾을 수 없습니다")
    if not target.area_id:
        raise HTTPException(status_code=400, detail="대상 맵에 area_id 가 없습니다")

    new_pois = db.query(MapPOI).filter(
        MapPOI.map_id == target_map_id, MapPOI.is_active == True
    ).all()
    if not new_pois:
        return {"updated": 0, "missing": [], "target_map_id": target_map_id}
    name_to_new_id: dict[str, int] = {p.name: p.id for p in new_pois}

    # 같은 area 의 다른 맵 POI
    other_pois = db.query(MapPOI).join(RobotMap, MapPOI.map_id == RobotMap.id).filter(
        RobotMap.area_id == target.area_id,
        MapPOI.map_id != target_map_id,
    ).all()
    if not other_pois:
        return {"updated": 0, "missing": [], "target_map_id": target_map_id}
    other_pid_to_name: dict[int, str] = {p.id: p.name for p in other_pois}

    waypoints = db.query(TaskRouteWaypoint).filter(
        TaskRouteWaypoint.poi_id.in_(list(other_pid_to_name.keys()))
    ).all()
    updated = 0
    missing: list[str] = []
    for wp in waypoints:
        old_name = other_pid_to_name.get(wp.poi_id)
        if not old_name:
            continue
        new_id = name_to_new_id.get(old_name)
        if new_id and new_id != wp.poi_id:
            logger.info(f"[remap] wp {wp.id} ({old_name}): {wp.poi_id} → {new_id}")
            wp.poi_id = new_id
            updated += 1
        elif not new_id:
            if old_name not in missing:
                missing.append(old_name)
    if updated:
        db.commit()
    return {"updated": updated, "missing": missing, "target_map_id": target_map_id}


def get_maps_by_area(db: Session, area_id: int) -> list[dict]:
    """특정 영역의 맵 목록 조회."""
    items = (
        db.query(RobotMap)
        .filter(RobotMap.area_id == area_id, RobotMap.is_active == True)
        .order_by(RobotMap.id.desc())
        .all()
    )
    return [_map_to_response(m) for m in items]


def get_map_by_id(db: Session, map_id: int) -> dict:
    """맵 단건 조회."""
    rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
    if not rm:
        raise HTTPException(status_code=404, detail="맵을 찾지 못했습니다.")
    return _map_to_response(rm)


def delete_map(db: Session, map_id: int) -> dict:
    """맵 완전 삭제 (POI, 라인, 폴리곤, 이미지 파일 포함)."""
    from pathlib import Path

    rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
    if not rm:
        raise HTTPException(status_code=404, detail="맵을 찾지 못했습니다.")

    # 정적 파일 삭제
    static_dir = Path(__file__).resolve().parent.parent.parent
    for url_field in [rm.image_url, rm.thumbnail_url, rm.download_url, rm.bag_url, rm.trajectories_url]:
        if url_field and url_field.startswith("/static/"):
            fpath = static_dir / url_field.lstrip("/")
            if fpath.exists():
                try:
                    fpath.unlink()
                except Exception:
                    pass

    # 관련 데이터 삭제 (폴리곤 → 라인 → POI → 맵 순서)
    db.query(MapPolygon).filter(MapPolygon.map_id == map_id).delete()
    db.query(MapLine).filter(MapLine.map_id == map_id).delete()
    db.query(MapPOI).filter(MapPOI.map_id == map_id).delete()
    db.delete(rm)
    db.commit()
    return {"message": "맵이 삭제되었습니다.", "id": map_id}


# ── Map Elements (POI + Line) CRUD ───────────────────────────

def save_map_elements(db: Session, map_id: int, payload: dict) -> dict:
    """맵의 POI·라인을 전체 교체 방식으로 저장한다.

    1) 기존 라인 → POI 순서로 삭제  (FK 참조 순서)
    2) POI 삽입 후 프론트엔드 ID → DB ID 매핑
    3) 라인 삽입 (fromId/toId → DB ID 변환)
    """
    rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
    if not rm:
        raise HTTPException(status_code=404, detail="맵을 찾지 못했습니다.")

    try:
        # ── 로봇 POI 매핑 백업: 삭제 전에 (charging_id/standby_id → POI 이름) 저장 ──
        old_pois = (
            db.query(MapPOI)
            .filter(MapPOI.map_id == map_id, MapPOI.poi_type.in_(["charging", "standby"]))
            .all()
        )
        old_poi_id_to_name: dict[int, str] = {p.id: p.name for p in old_pois}
        old_poi_id_to_type: dict[int, str] = {p.id: p.poi_type for p in old_pois}

        # charging_id / standby_id가 이 맵의 POI를 가리키는 로봇들 백업
        charging_backup: list[tuple[int, str]] = []  # [(robot_id, poi_name)]
        standby_backup: list[tuple[int, str]] = []   # [(robot_id, poi_name)]
        if old_poi_id_to_name:
            old_ids = list(old_poi_id_to_name.keys())
            robots_linked = (
                db.query(Robot)
                .filter(
                    (Robot.charging_id.in_(old_ids)) | (Robot.standby_id.in_(old_ids))
                )
                .all()
            )
            for r in robots_linked:
                if r.charging_id and r.charging_id in old_poi_id_to_name:
                    charging_backup.append((r.id, old_poi_id_to_name[r.charging_id]))
                if r.standby_id and r.standby_id in old_poi_id_to_name:
                    standby_backup.append((r.id, old_poi_id_to_name[r.standby_id]))

        # 기존 데이터 삭제 (라인 → POI 순서)
        db.query(MapLine).filter(MapLine.map_id == map_id).delete()
        db.query(MapPOI).filter(MapPOI.map_id == map_id).delete()
        db.flush()

        # POI 삽입
        client_id_to_db_id: dict[str, int] = {}
        new_charging_name_to_id: dict[str, int] = {}
        new_standby_name_to_id: dict[str, int] = {}
        for p in payload.get("pois", []):
            poi = MapPOI(
                map_id=map_id,
                name=p.get("name", ""),
                x=p["x"],
                y=p["y"],
                world_x=p.get("worldX"),
                world_y=p.get("worldY"),
                poi_type=p.get("type", "waypoint"),
                phone_number=p.get("phoneNumber"),
                angle=p.get("angle"),
                load_type=p.get("loadType"),
                robot_sns=json.dumps(p["robotSns"]) if p.get("robotSns") else None,
                address=p.get("address"),
                docking_radius=p.get("dockingRadius"),
                area_name=p.get("areaName"),
                rack_size=p.get("rackSize"),
            )
            db.add(poi)
            db.flush()  # id 확정
            client_id_to_db_id[p["id"]] = poi.id
            if p.get("type") == "charging":
                new_charging_name_to_id[p.get("name", "")] = poi.id
            elif p.get("type") == "standby":
                new_standby_name_to_id[p.get("name", "")] = poi.id

        # 라인 삽입
        for ln in payload.get("lines", []):
            from_db_id = client_id_to_db_id.get(ln["fromId"])
            to_db_id = client_id_to_db_id.get(ln["toId"])
            if from_db_id is None or to_db_id is None:
                continue  # 참조할 POI가 없으면 건너뛰기

            line = MapLine(
                map_id=map_id,
                from_poi_id=from_db_id,
                to_poi_id=to_db_id,
                from_world_x=ln.get("fromWorldX"),
                from_world_y=ln.get("fromWorldY"),
                to_world_x=ln.get("toWorldX"),
                to_world_y=ln.get("toWorldY"),
                from_ori=ln.get("fromOri"),
                to_ori=ln.get("toOri"),
                direction=ln.get("direction", "forward"),
                line_type=ln.get("lineType", "straight"),
                control_points=json.dumps(ln["controlPoints"]) if ln.get("controlPoints") else None,
                area_name=ln.get("areaName"),
            )
            db.add(line)

        # 폴리곤 삽입 (가상벽 등)
        db.query(MapPolygon).filter(MapPolygon.map_id == map_id).delete()
        for pg in payload.get("polygons", []):
            poly = MapPolygon(
                map_id=map_id,
                name=pg.get("name", ""),
                shape_type=pg.get("shapeType", "polygon"),
                points_json=json.dumps(pg.get("points", [])),
            )
            db.add(poly)

        # ── POI 매핑 복원: 같은 이름의 새 POI로 charging_id/standby_id 재설정 ──
        restored_charging = 0
        restored_standby = 0
        all_restore = [(charging_backup, new_charging_name_to_id, "charging_id"),
                       (standby_backup, new_standby_name_to_id, "standby_id")]
        for backup_list, name_map, field in all_restore:
            for robot_id, poi_name in backup_list:
                new_poi_id = name_map.get(poi_name)
                if new_poi_id:
                    robot = db.query(Robot).filter(Robot.id == robot_id).first()
                    if robot:
                        setattr(robot, field, new_poi_id)
                        if field == "charging_id":
                            restored_charging += 1
                        else:
                            restored_standby += 1
        if charging_backup or standby_backup:
            logger.info(f"[save_map_elements] 매핑 복원: "
                         f"충전소 {restored_charging}/{len(charging_backup)}, "
                         f"대기장소 {restored_standby}/{len(standby_backup)}")

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"맵 요소 저장 중 DB 오류가 발생했습니다: {e}",
        )
    return {"message": "저장 완료", "map_id": map_id}


def get_map_elements(db: Session, map_id: int) -> dict:
    """맵에 저장된 POI·라인을 프론트엔드 형식으로 반환한다."""
    rm = db.query(RobotMap).filter(RobotMap.id == map_id).first()
    if not rm:
        raise HTTPException(status_code=404, detail="맵을 찾지 못했습니다.")

    pois = db.query(MapPOI).filter(MapPOI.map_id == map_id, MapPOI.is_active == True).all()
    lines = db.query(MapLine).filter(MapLine.map_id == map_id, MapLine.is_active == True).all()

    # DB ID → 프론트엔드 ID 매핑
    db_id_to_client: dict[int, str] = {}
    poi_list = []
    for p in pois:
        client_id = f"poi-{p.id}"
        db_id_to_client[p.id] = client_id
        poi_list.append({
            "id": client_id,
            "x": p.x,
            "y": p.y,
            "worldX": p.world_x,
            "worldY": p.world_y,
            "name": p.name,
            "type": p.poi_type,
            "phoneNumber": p.phone_number,
            "angle": p.angle,
            "loadType": p.load_type,
            "robotSns": _safe_json_loads(p.robot_sns),
            "address": p.address,
            "dockingRadius": p.docking_radius,
            "areaName": p.area_name,
            "rackSize": p.rack_size,
        })

    line_list = []
    for ln in lines:
        from_client = db_id_to_client.get(ln.from_poi_id)
        to_client = db_id_to_client.get(ln.to_poi_id)
        if not from_client or not to_client:
            continue
        line_list.append({
            "id": f"line-{ln.id}",
            "fromId": from_client,
            "toId": to_client,
            "fromWorldX": ln.from_world_x,
            "fromWorldY": ln.from_world_y,
            "toWorldX": ln.to_world_x,
            "toWorldY": ln.to_world_y,
            "fromOri": ln.from_ori,
            "toOri": ln.to_ori,
            "direction": ln.direction,
            "lineType": ln.line_type,
            "controlPoints": _safe_json_loads(ln.control_points),
            "areaName": ln.area_name,
        })

    # 폴리곤
    polys = db.query(MapPolygon).filter(MapPolygon.map_id == map_id, MapPolygon.is_active == True).all()
    polygon_list = []
    for pg in polys:
        polygon_list.append({
            "id": f"polygon-{pg.id}",
            "name": pg.name,
            "shapeType": pg.shape_type,
            "points": _safe_json_loads(pg.points_json) or [],
        })

    return {"pois": poi_list, "lines": line_list, "polygons": polygon_list}


def get_charging_pois(db: Session, map_id: int):
    """충전소 타입 POI 중 월드 좌표가 유효한 것만 반환."""
    return (
        db.query(MapPOI)
        .filter(
            MapPOI.map_id == map_id,
            MapPOI.poi_type == "charging",
            MapPOI.is_active == True,
            MapPOI.world_x.isnot(None),
            MapPOI.world_y.isnot(None),
        )
        .all()
    )


def get_firewall_lines(db: Session, map_id: int):
    """가상벽(firewall) 타입 라인 중 월드 좌표가 유효한 것만 반환."""
    return (
        db.query(MapLine)
        .filter(
            MapLine.map_id == map_id,
            MapLine.line_type == "firewall",
            MapLine.is_active == True,
            MapLine.from_world_x.isnot(None),
            MapLine.from_world_y.isnot(None),
            MapLine.to_world_x.isnot(None),
            MapLine.to_world_y.isnot(None),
        )
        .all()
    )
