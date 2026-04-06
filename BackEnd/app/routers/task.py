"""
작업 관리 라우터
- 경로(Route) CRUD
- 스케줄 작업 CRUD + 즉시 실행
- 실행 이력 조회
- 태블릿 전용 페이지
"""
import logging
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import TaskRoute, TaskRouteWaypoint, ScheduledTask, TaskHistory
from app.models.map import MapPOI, RobotMap
from app.models.robot import Robot
from app.schemas.task import (
    TaskRouteCreate, TaskRouteUpdate, TaskRouteResponse, WaypointResponse,
    ScheduledTaskCreate, ScheduledTaskUpdate, ScheduledTaskResponse,
    TaskHistoryResponse,
)
from app.services.scheduler import (
    add_task_job_by_id, remove_task_job, get_next_run_time, execute_scheduled_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["작업 관리"])


# ══════════════════════════════════════
# 경로(Route) CRUD
# ══════════════════════════════════════

def _route_to_response(route: TaskRoute) -> dict:
    waypoints = []
    for wp in route.waypoints:
        poi = wp.poi
        waypoints.append({
            "id": wp.id,
            "poi_id": wp.poi_id,
            "poi_name": poi.name if poi else None,
            "order": wp.order,
            "waypoint_type": wp.waypoint_type,
            "wait_sec": wp.wait_sec or 0,
            "world_x": poi.world_x if poi else None,
            "world_y": poi.world_y if poi else None,
        })
    return {
        "id": route.id,
        "name": route.name,
        "waypoints": waypoints,
        "is_active": route.is_active,
        "created_at": route.created_at,
    }


@router.get("/routes")
def api_get_routes(
    robot_id: int | None = None,
    area_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(TaskRoute).filter(TaskRoute.is_active == True)
    if robot_id:
        query = query.filter(TaskRoute.robot_id == robot_id)
    if area_id:
        # area의 맵에 속하는 POI ID 집합으로 경로 필터
        area_poi_ids = db.query(MapPOI.id).join(RobotMap, MapPOI.map_id == RobotMap.id).filter(
            RobotMap.area_id == area_id, RobotMap.is_active == True, MapPOI.is_active == True
        ).subquery()
        route_ids = db.query(TaskRouteWaypoint.route_id).filter(
            TaskRouteWaypoint.poi_id.in_(area_poi_ids)
        ).distinct().subquery()
        query = query.filter(TaskRoute.id.in_(route_ids))
    routes = query.order_by(TaskRoute.id.desc()).all()
    return {"total": len(routes), "items": [_route_to_response(r) for r in routes]}


@router.post("/routes", status_code=201)
def api_create_route(data: TaskRouteCreate, db: Session = Depends(get_db)):
    route = TaskRoute(name=data.name)
    db.add(route)
    db.flush()

    for wp in data.waypoints:
        poi = db.query(MapPOI).filter(MapPOI.id == wp.poi_id, MapPOI.is_active == True).first()
        if not poi:
            raise HTTPException(404, f"POI를 찾을 수 없습니다 (id={wp.poi_id})")
        db.add(TaskRouteWaypoint(
            route_id=route.id,
            poi_id=wp.poi_id,
            order=wp.order,
            waypoint_type=wp.waypoint_type,
            wait_sec=wp.wait_sec,
        ))

    db.commit()
    db.refresh(route)
    return _route_to_response(route)


@router.get("/routes/{route_id}")
def api_get_route(route_id: int, db: Session = Depends(get_db)):
    route = db.query(TaskRoute).filter(TaskRoute.id == route_id).first()
    if not route:
        raise HTTPException(404, "경로를 찾을 수 없습니다")
    return _route_to_response(route)


@router.put("/routes/{route_id}")
def api_update_route(route_id: int, data: TaskRouteUpdate, db: Session = Depends(get_db)):
    route = db.query(TaskRoute).filter(TaskRoute.id == route_id).first()
    if not route:
        raise HTTPException(404, "경로를 찾을 수 없습니다")

    if data.name is not None:
        route.name = data.name

    if data.waypoints is not None:
        db.query(TaskRouteWaypoint).filter(TaskRouteWaypoint.route_id == route_id).delete()
        for wp in data.waypoints:
            db.add(TaskRouteWaypoint(
                route_id=route_id,
                poi_id=wp.poi_id,
                order=wp.order,
                waypoint_type=wp.waypoint_type,
                wait_sec=wp.wait_sec,
            ))

    db.commit()
    db.refresh(route)
    return _route_to_response(route)


@router.delete("/routes/{route_id}")
def api_delete_route(route_id: int, db: Session = Depends(get_db)):
    route = db.query(TaskRoute).filter(TaskRoute.id == route_id).first()
    if not route:
        raise HTTPException(404, "경로를 찾을 수 없습니다")
    # 연관된 스케줄 삭제
    db.query(ScheduledTask).filter(ScheduledTask.route_id == route_id).delete()
    # 연관된 웨이포인트 삭제
    db.query(TaskRouteWaypoint).filter(TaskRouteWaypoint.route_id == route_id).delete()
    db.delete(route)
    db.commit()
    return {"message": "삭제 완료"}


# ══════════════════════════════════════
# 스케줄 작업 CRUD
# ══════════════════════════════════════

def _task_to_response(task: ScheduledTask) -> dict:
    route = task.route
    robot_name = task.robot.name if task.robot else None
    return {
        "id": task.id,
        "name": task.name,
        "route_id": task.route_id,
        "route_name": route.name if route else None,
        "robot_id": task.robot_id,
        "robot_name": robot_name,
        "start_time": task.start_time,
        "end_time": task.end_time,
        "repeat_type": task.repeat_type,
        "repeat_days": task.repeat_days,
        "start_date": task.start_date,
        "end_date": task.end_date,
        "is_active": task.is_active,
        "last_run_at": task.last_run_at,
        "next_run_at": get_next_run_time(task.id),
        "created_at": task.created_at,
    }


@router.get("")
def api_get_tasks(
    is_active: bool | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from datetime import date as date_cls
    # 만료된 once 작업 자동 삭제 (cascade 방지)
    expired_ids = [t.id for t in db.query(ScheduledTask.id).filter(
        ScheduledTask.repeat_type == "once",
        ScheduledTask.start_date < date_cls.today(),
    ).all()]
    if expired_ids:
        db.query(TaskHistory).filter(TaskHistory.task_id.in_(expired_ids)).delete(synchronize_session=False)
        db.query(ScheduledTask).filter(ScheduledTask.id.in_(expired_ids)).delete(synchronize_session=False)
        db.commit()

    query = db.query(ScheduledTask)
    if is_active is not None:
        query = query.filter(ScheduledTask.is_active == is_active)
    total = query.count()
    tasks = query.order_by(ScheduledTask.id.desc()).offset(skip).limit(limit).all()
    return {"total": total, "items": [_task_to_response(t) for t in tasks]}


@router.post("", status_code=201)
def api_create_task(data: ScheduledTaskCreate, db: Session = Depends(get_db)):
    route = db.query(TaskRoute).filter(TaskRoute.id == data.route_id).first()
    if not route:
        raise HTTPException(404, "경로를 찾을 수 없습니다")

    robot = db.query(Robot).filter(Robot.id == data.robot_id).first()
    if not robot:
        raise HTTPException(404, "로봇을 찾을 수 없습니다")

    task = ScheduledTask(
        name=data.name,
        robot_id=data.robot_id,
        route_id=data.route_id,
        start_time=data.start_time,
        end_time=data.end_time,
        repeat_type=data.repeat_type,
        repeat_days=data.repeat_days,
        start_date=data.start_date,
        end_date=data.end_date,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    add_task_job_by_id(task.id)

    from app.crud.activity_log import log_activity
    log_activity("user", "schedule_create", f"스케줄 생성: {data.name} (경로: {route.name})", source="api_create_task")
    return _task_to_response(task)


@router.get("/schedule/{task_id}")
def api_get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    return _task_to_response(task)


@router.put("/schedule/{task_id}")
def api_update_task(task_id: int, data: ScheduledTaskUpdate, db: Session = Depends(get_db)):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "작업을 찾을 수 없습니다")

    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(task, k, v)
    db.commit()
    db.refresh(task)

    if task.is_active:
        add_task_job_by_id(task.id)
    else:
        remove_task_job(task.id)

    return _task_to_response(task)


@router.delete("/schedule/{task_id}")
def api_delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    remove_task_job(task.id)
    db.delete(task)
    db.commit()
    return {"message": "삭제 완료"}


@router.post("/schedule/{task_id}/toggle")
def api_toggle_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    task.is_active = not task.is_active
    db.commit()
    db.refresh(task)
    if task.is_active:
        add_task_job_by_id(task.id)
    else:
        remove_task_job(task.id)
    return _task_to_response(task)


@router.post("/schedule/{task_id}/run")
def api_run_task_now(task_id: int, db: Session = Depends(get_db)):
    task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    t = threading.Thread(target=execute_scheduled_task, args=[task_id], daemon=True)
    t.start()
    return {"message": "작업 실행 시작", "task_id": task_id}


# ══════════════════════════════════════
# 수동 실행 (스케줄 생성 없이)
# ══════════════════════════════════════

from pydantic import BaseModel as _BaseModel

class ManualRunRequest(_BaseModel):
    robot_id: int
    route_id: int

@router.post("/manual-run")
def api_manual_run(data: ManualRunRequest, db: Session = Depends(get_db)):
    """수동 배차 — 스케줄 생성 없이 즉시 실행"""
    robot = db.query(Robot).filter(Robot.id == data.robot_id).first()
    if not robot or not robot.ip_address:
        raise HTTPException(404, "로봇을 찾을 수 없습니다")

    # 로봇이 이미 작업 중인지 체크
    from app.services.jack_service import get_job_status
    if get_job_status(robot.ip_address):
        raise HTTPException(409, "로봇이 이미 작업 중입니다")

    route = db.query(TaskRoute).filter(TaskRoute.id == data.route_id).first()
    if not route:
        raise HTTPException(404, "경로를 찾을 수 없습니다")

    waypoints = db.query(TaskRouteWaypoint).filter(
        TaskRouteWaypoint.route_id == route.id
    ).order_by(TaskRouteWaypoint.order).all()

    wp_list = []
    first_pickup = first_dropoff = None
    for wp in waypoints:
        poi = db.query(MapPOI).filter(MapPOI.id == wp.poi_id).first()
        if not poi:
            continue
        wp_list.append({
            "name": poi.name, "x": poi.world_x, "y": poi.world_y,
            "ori": poi.angle or 0, "waypoint_type": wp.waypoint_type,
            "poi_type": poi.poi_type or "general", "wait_sec": wp.wait_sec or 0,
        })
        if wp.waypoint_type == "pickup" and not first_pickup:
            first_pickup = poi.name
        if wp.waypoint_type == "dropoff" and not first_dropoff:
            first_dropoff = poi.name

    if len(wp_list) < 2:
        raise HTTPException(400, "경로에 웨이포인트가 부족합니다")

    # 이력 생성
    from datetime import datetime
    history = TaskHistory(
        task_name=f"수동: {route.name}",
        route_name=route.name,
        robot_id=robot.id,
        robot_name=robot.name,
        pickup_poi_name=first_pickup,
        dropoff_poi_name=first_dropoff,
        status="running",
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    history_id = history.id
    robot_ip = robot.ip_address

    def _run():
        from app.services.jack_service import run_route_job
        from app.services.scheduler import _return_to_charger
        from app.database import SessionLocal
        # 메인층 여부 확인
        from app.models.map import Area as _Area
        _db3 = SessionLocal()
        try:
            _area = _db3.query(_Area).filter(_Area.area_id == route.area_id).first()
            _is_main = _area.is_main_floor if _area and hasattr(_area, "is_main_floor") else True
        finally:
            _db3.close()
        result = run_route_job(robot_ip, wp_list, is_main_floor=_is_main)
        db2 = SessionLocal()
        try:
            h = db2.query(TaskHistory).filter(TaskHistory.id == history_id).first()
            if h:
                h.status = "succeeded" if result["status"] == "done" else "failed"
                h.finished_at = datetime.now()
                h.error_message = result.get("message") if result["status"] != "done" else None
                db2.commit()
        finally:
            db2.close()
        # 성공 시에만 충전소 복귀
        if result["status"] == "done":
            _return_to_charger(robot_ip, wp_list)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    from app.crud.activity_log import log_activity
    log_activity("user", "manual_run", f"수동 배차: {route.name} → {robot.name}", source="api_manual_run")
    return {"message": "수동 실행 시작", "history_id": history_id}


class ManualRunPoisRequest(_BaseModel):
    robot_id: int
    pickup_poi_id: int
    dropoff_poi_id: int
    manual_confirm: bool = False

@router.post("/manual-run-pois")
def api_manual_run_pois(data: ManualRunPoisRequest, db: Session = Depends(get_db)):
    """수동 배차 — 픽업/드롭오프 POI 직접 지정"""
    robot = db.query(Robot).filter(Robot.id == data.robot_id).first()
    if not robot or not robot.ip_address:
        raise HTTPException(404, "로봇을 찾을 수 없습니다")

    # 로봇이 이미 작업 중인지 체크
    from app.services.jack_service import get_job_status
    if get_job_status(robot.ip_address):
        raise HTTPException(409, "로봇이 이미 작업 중입니다")

    pickup = db.query(MapPOI).filter(MapPOI.id == data.pickup_poi_id, MapPOI.is_active == True).first()
    dropoff = db.query(MapPOI).filter(MapPOI.id == data.dropoff_poi_id, MapPOI.is_active == True).first()
    if not pickup or not dropoff:
        raise HTTPException(404, "POI를 찾을 수 없습니다")
    if pickup.world_x is None or pickup.world_y is None:
        raise HTTPException(400, f"픽업 POI '{pickup.name}'의 좌표가 없습니다")
    if dropoff.world_x is None or dropoff.world_y is None:
        raise HTTPException(400, f"드롭오프 POI '{dropoff.name}'의 좌표가 없습니다")

    wp_list = [
        {"name": pickup.name, "x": pickup.world_x, "y": pickup.world_y,
         "ori": pickup.angle or 0, "waypoint_type": "pickup",
         "poi_type": pickup.poi_type or "general", "wait_sec": 0},
        {"name": dropoff.name, "x": dropoff.world_x, "y": dropoff.world_y,
         "ori": dropoff.angle or 0, "waypoint_type": "dropoff",
         "poi_type": dropoff.poi_type or "general", "wait_sec": 0},
    ]

    from datetime import datetime
    history = TaskHistory(
        task_name=f"수동: {pickup.name}→{dropoff.name}",
        route_name=f"{pickup.name}→{dropoff.name}",
        robot_id=robot.id,
        robot_name=robot.name,
        pickup_poi_name=pickup.name,
        dropoff_poi_name=dropoff.name,
        status="running",
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    history_id = history.id
    robot_ip = robot.ip_address

    use_confirm = data.manual_confirm

    def _run():
        from app.services.jack_service import run_route_job
        from app.services.scheduler import _return_to_charger
        from app.database import SessionLocal
        # 메인층 여부 확인
        from app.models.map import RobotMap as _RM, Area as _Area
        _db3 = SessionLocal()
        try:
            _map = _db3.query(_RM).join(MapPOI, MapPOI.map_id == _RM.id).filter(MapPOI.id == data.pickup_poi_id).first()
            _area = _db3.query(_Area).filter(_Area.area_id == _map.area_id).first() if _map else None
            _is_main = _area.is_main_floor if _area and hasattr(_area, "is_main_floor") else True
        finally:
            _db3.close()
        result = run_route_job(robot_ip, wp_list, manual_confirm=use_confirm, is_main_floor=_is_main)
        db2 = SessionLocal()
        try:
            h = db2.query(TaskHistory).filter(TaskHistory.id == history_id).first()
            if h:
                h.status = "succeeded" if result["status"] == "done" else "failed"
                h.finished_at = datetime.now()
                h.error_message = result.get("message") if result["status"] != "done" else None
                db2.commit()
        finally:
            db2.close()
        # 성공 시에만 충전소 복귀
        if result["status"] == "done":
            _return_to_charger(robot_ip, wp_list)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    from app.crud.activity_log import log_activity
    log_activity("user", "manual_run", f"수동 배차: {pickup.name}→{dropoff.name} ({robot.name})", source="api_manual_run_pois")
    return {"message": "수동 실행 시작", "history_id": history_id}


# ══════════════════════════════════════
# 실행 이력
# ══════════════════════════════════════

@router.get("/history/all")
def api_get_all_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = db.query(TaskHistory)
    total = query.count()
    items = query.order_by(TaskHistory.id.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [TaskHistoryResponse.model_validate(h).model_dump() for h in items],
    }


@router.get("/history/{task_id}")
def api_get_task_history(
    task_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(TaskHistory).filter(TaskHistory.task_id == task_id)
    total = query.count()
    items = query.order_by(TaskHistory.id.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [TaskHistoryResponse.model_validate(h).model_dump() for h in items],
    }


# ── 통계 API ──

@router.get("/stats/completion")
def api_stats_completion(
    days: int = Query(default=7, ge=1, le=90),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """일별 작업 완료율 (완료/실패/취소 건수)"""
    from sqlalchemy import func, case, cast, Date, text
    from datetime import timedelta

    if start_date and end_date:
        since = datetime.strptime(start_date, "%Y-%m-%d")
        until = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    else:
        since = datetime.now() - timedelta(days=days)
        until = datetime.now() + timedelta(days=1)
    rows = (
        db.query(
            cast(TaskHistory.started_at, Date).label("date"),
            func.count().label("total"),
            func.sum(case((TaskHistory.status == "succeeded", 1), else_=0)).label("completed"),
            func.sum(case((TaskHistory.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((TaskHistory.status == "cancelled", 1), else_=0)).label("cancelled"),
        )
        .filter(TaskHistory.started_at >= since, TaskHistory.started_at < until)
        .group_by(cast(TaskHistory.started_at, Date))
        .order_by(cast(TaskHistory.started_at, Date))
        .all()
    )
    return [
        {
            "date": str(r.date),
            "total": r.total,
            "completed": int(r.completed or 0),
            "failed": int(r.failed or 0),
            "cancelled": int(r.cancelled or 0),
        }
        for r in rows
    ]


@router.get("/stats/robot-utilization")
def api_stats_robot_utilization(
    days: int = Query(default=7, ge=1, le=90),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """로봇별 작업 건수 및 총 소요 시간"""
    from sqlalchemy import func, text
    from datetime import timedelta

    if start_date and end_date:
        since = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        since = datetime.now() - timedelta(days=days)
    rows = (
        db.query(
            TaskHistory.robot_name,
            func.count().label("task_count"),
            func.sum(
                func.timestampdiff(
                    text("SECOND"),
                    TaskHistory.started_at,
                    TaskHistory.finished_at,
                )
            ).label("total_seconds"),
        )
        .filter(TaskHistory.started_at >= since, TaskHistory.finished_at.isnot(None))
        .group_by(TaskHistory.robot_name)
        .all()
    )
    return [
        {
            "robot_name": r.robot_name or "알 수 없음",
            "task_count": r.task_count,
            "total_minutes": round((r.total_seconds or 0) / 60, 1),
        }
        for r in rows
    ]


@router.get("/stats/route-duration")
def api_stats_route_duration(
    days: int = Query(default=7, ge=1, le=90),
    start_date: str = Query(default=None),
    end_date: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """경로별 평균 소요 시간"""
    from sqlalchemy import func, text
    from datetime import timedelta

    if start_date and end_date:
        since = datetime.strptime(start_date, "%Y-%m-%d")
    else:
        since = datetime.now() - timedelta(days=days)
    rows = (
        db.query(
            TaskHistory.route_name,
            func.count().label("count"),
            func.avg(
                func.timestampdiff(
                    text("SECOND"),
                    TaskHistory.started_at,
                    TaskHistory.finished_at,
                )
            ).label("avg_seconds"),
        )
        .filter(
            TaskHistory.started_at >= since,
            TaskHistory.finished_at.isnot(None),
            TaskHistory.route_name.isnot(None),
        )
        .group_by(TaskHistory.route_name)
        .all()
    )
    return [
        {
            "route_name": r.route_name,
            "count": r.count,
            "avg_minutes": round((r.avg_seconds or 0) / 60, 1),
        }
        for r in rows
    ]


# ══════════════════════════════════════
# 태블릿 전용 페이지
# ══════════════════════════════════════

_TABLET_TEMPLATE = Path(__file__).parent.parent / "templates" / "tablet.html"

@router.get("/tablet/{robot_id}", response_class=HTMLResponse)
def tablet_page(robot_id: int, db: Session = Depends(get_db)):
    """태블릿 수동 배차 웹 페이지"""
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    robot_name = robot.name if robot else f"Robot #{robot_id}"
    robot_ip = robot.ip_address if robot else ""

    active_map = db.query(RobotMap).filter(RobotMap.is_active == True).order_by(RobotMap.id.desc()).first()
    pois = db.query(MapPOI).filter(
        MapPOI.map_id == active_map.id if active_map else -1,
        MapPOI.is_active == True,
        MapPOI.poi_type == "jack",
    ).all()
    poi_options = "".join(f'<option value="{p.id}">{p.name}</option>' for p in pois)

    html = _TABLET_TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("{{ROBOT_NAME}}", robot_name)
    html = html.replace("{{ROBOT_ID}}", str(robot_id))
    html = html.replace("{{ROBOT_IP}}", robot_ip)
    html = html.replace("{{POI_OPTIONS}}", poi_options)
    return HTMLResponse(content=html)
