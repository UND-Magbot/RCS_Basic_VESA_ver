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
from app.services import poi_lock
from app.services.thread_utils import safe_thread

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
        "work_mode": getattr(route, "work_mode", "rack_pickup"),
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
    route = TaskRoute(name=data.name, work_mode=data.work_mode or "rack_pickup")
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
    if data.work_mode is not None:
        route.work_mode = data.work_mode

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
    # 만료된 once 작업 정리는 init_scheduler()에서 기동 시 1회만 수행
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
    safe_thread(target=execute_scheduled_task, args=(task_id,), name=f"task-run-{task_id}").start()
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

    # 로봇 타입별 작업 모드 검증
    from app.constants.robot_types import is_work_mode_allowed, ROBOT_TYPE_LABELS
    robot_type = getattr(robot, "robot_type", "lifting") or "lifting"
    route_mode = getattr(route, "work_mode", "rack_pickup") or "rack_pickup"
    if not is_work_mode_allowed(robot_type, route_mode):
        label = ROBOT_TYPE_LABELS.get(robot_type, robot_type)
        raise HTTPException(400, f"{label} 로봇은 이 경로의 작업 종류를 지원하지 않습니다")

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
            "poi_id": poi.id,
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

    # POI 락 — 다른 로봇이 같은 POI를 점유 중이면 거부
    lock_pids = [w.get("poi_id") for w in wp_list]
    if getattr(robot, "charging_id", None):
        lock_pids.append(robot.charging_id)
    if getattr(robot, "standby_id", None):
        lock_pids.append(robot.standby_id)
    ok, conflict_pid = poi_lock.try_acquire(lock_pids, robot.id)
    if not ok:
        owner = poi_lock.get_owner(conflict_pid)
        raise HTTPException(409, f"다른 로봇(id={owner})이 동일 POI를 사용 중입니다")

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

    robot_id_for_lock = robot.id

    def _run():
        from app.services.jack_service import run_route_job
        from app.services.scheduler import _return_to_charger
        from app.database import SessionLocal
        try:
            result = run_route_job(robot_ip, wp_list, work_mode=getattr(route, "work_mode", "rack_pickup"), robot_id=robot_id_for_lock)
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
        finally:
            poi_lock.release_all_by_robot(robot_id_for_lock)

    safe_thread(target=_run, name=f"manual-run-{robot_id_for_lock}").start()

    from app.crud.activity_log import log_activity
    log_activity("user", "manual_run", f"수동 배차: {route.name} → {robot.name}", source="api_manual_run")
    return {"message": "수동 실행 시작", "history_id": history_id}


class ManualRunPoisRequest(_BaseModel):
    robot_id: int
    pickup_poi_id: int
    dropoff_poi_id: int
    manual_confirm: bool = False
    work_mode: str = "rack_pickup"

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

    # 로봇 타입별 작업 모드 검증
    from app.constants.robot_types import is_work_mode_allowed, ROBOT_TYPE_LABELS
    robot_type = getattr(robot, "robot_type", "lifting") or "lifting"
    if not is_work_mode_allowed(robot_type, data.work_mode or "rack_pickup"):
        label = ROBOT_TYPE_LABELS.get(robot_type, robot_type)
        raise HTTPException(400, f"{label} 로봇은 이 작업 종류를 지원하지 않습니다")

    pickup = db.query(MapPOI).filter(MapPOI.id == data.pickup_poi_id, MapPOI.is_active == True).first()
    dropoff = db.query(MapPOI).filter(MapPOI.id == data.dropoff_poi_id, MapPOI.is_active == True).first()
    if not pickup or not dropoff:
        raise HTTPException(404, "POI를 찾을 수 없습니다")
    if pickup.world_x is None or pickup.world_y is None:
        raise HTTPException(400, f"픽업 POI '{pickup.name}'의 좌표가 없습니다")
    if dropoff.world_x is None or dropoff.world_y is None:
        raise HTTPException(400, f"드롭오프 POI '{dropoff.name}'의 좌표가 없습니다")

    wp_list = [
        {"poi_id": pickup.id, "name": pickup.name, "x": pickup.world_x, "y": pickup.world_y,
         "ori": pickup.angle or 0, "waypoint_type": "pickup",
         "poi_type": pickup.poi_type or "general", "wait_sec": 0},
        {"poi_id": dropoff.id, "name": dropoff.name, "x": dropoff.world_x, "y": dropoff.world_y,
         "ori": dropoff.angle or 0, "waypoint_type": "dropoff",
         "poi_type": dropoff.poi_type or "general", "wait_sec": 0},
    ]

    # POI 락
    lock_pids = [pickup.id, dropoff.id]
    if getattr(robot, "charging_id", None):
        lock_pids.append(robot.charging_id)
    if getattr(robot, "standby_id", None):
        lock_pids.append(robot.standby_id)
    ok, conflict_pid = poi_lock.try_acquire(lock_pids, robot.id)
    if not ok:
        owner = poi_lock.get_owner(conflict_pid)
        raise HTTPException(409, f"다른 로봇(id={owner})이 동일 POI를 사용 중입니다")

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
    robot_id_for_lock = robot.id

    use_confirm = data.manual_confirm

    def _run():
        from app.services.jack_service import run_route_job
        from app.services.scheduler import _return_to_charger
        from app.database import SessionLocal
        try:
            # 영역 ID 확인 (POI → 맵 → 영역)
            from app.models.map import RobotMap as _RM
            _db3 = SessionLocal()
            try:
                _map = _db3.query(_RM).join(MapPOI, MapPOI.map_id == _RM.id).filter(MapPOI.id == data.pickup_poi_id).first()
                _area_id = _map.area_id if _map else None
            finally:
                _db3.close()
            result = run_route_job(robot_ip, wp_list, manual_confirm=use_confirm, area_id=_area_id, work_mode=data.work_mode or "rack_pickup", robot_id=robot_id_for_lock)
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
        finally:
            poi_lock.release_all_by_robot(robot_id_for_lock)

    safe_thread(target=_run, name=f"manual-run-pois-{robot_id_for_lock}").start()

    from app.crud.activity_log import log_activity
    log_activity("user", "manual_run", f"수동 배차: {pickup.name}→{dropoff.name} ({robot.name})", source="api_manual_run_pois")
    return {"message": "수동 실행 시작", "history_id": history_id}


# ══════════════════════════════════════
# 배치 수동 배차 (여러 로봇 동시 + 반복)
# ══════════════════════════════════════

class BatchAssignment(_BaseModel):
    robot_id: int
    work_mode: str = "rack_pickup"   # rack_pickup / delivery_no_rack / simple_move
    pickup_poi_id: int
    dropoff_poi_id: int
    wait_sec: int = 0                # 각 위치 도착 후 다음 위치 가기 전 대기 (초)
    repeat_count: int | None = None  # None or 0 = 무한 반복


class ManualRunBatchRequest(_BaseModel):
    assignments: list[BatchAssignment]


@router.post("/manual-run-batch")
def api_manual_run_batch(data: ManualRunBatchRequest, db: Session = Depends(get_db)):
    """여러 로봇 동시 수동 배차 + 반복.

    - 로봇별 (work_mode, 픽업, 드롭오프, 위치 간 대기, 반복 횟수) 명세
    - 한 어사인먼트의 실패/skip 은 다른 어사인먼트에 영향 X
    - repeat_count=None or 0 → 무한 반복 (stop_robot_job 으로 중지 가능)
    - 응답: { started: [...], skipped: [...] }
    """
    from datetime import datetime as _dt
    from app.services.jack_service import get_job_status, run_route_job, _interruptible_sleep
    from app.services.scheduler import _return_to_charger
    from app.database import SessionLocal as _SL
    from app.constants.robot_types import is_work_mode_allowed, ROBOT_TYPE_LABELS

    if not data.assignments:
        raise HTTPException(400, "어사인먼트가 비어있습니다")

    started: list[dict] = []
    skipped: list[dict] = []

    for a in data.assignments:
        # 1) 로봇 검증
        robot = db.query(Robot).filter(Robot.id == a.robot_id).first()
        if not robot or not robot.ip_address:
            skipped.append({"robot_id": a.robot_id, "reason": "로봇을 찾을 수 없습니다"})
            continue
        if get_job_status(robot.ip_address):
            skipped.append({"robot_id": a.robot_id, "reason": "로봇이 이미 작업 중입니다"})
            continue

        # 2) 작업 모드 검증
        work_mode = (a.work_mode or "rack_pickup").strip()
        robot_type = getattr(robot, "robot_type", "lifting") or "lifting"
        if not is_work_mode_allowed(robot_type, work_mode):
            label = ROBOT_TYPE_LABELS.get(robot_type, robot_type)
            skipped.append({"robot_id": a.robot_id,
                            "reason": f"{label} 로봇은 '{work_mode}' 작업을 지원하지 않습니다"})
            continue

        # 3) POI 검증
        pickup = db.query(MapPOI).filter(MapPOI.id == a.pickup_poi_id, MapPOI.is_active == True).first()
        dropoff = db.query(MapPOI).filter(MapPOI.id == a.dropoff_poi_id, MapPOI.is_active == True).first()
        if not pickup or not dropoff:
            skipped.append({"robot_id": a.robot_id, "reason": "POI를 찾을 수 없습니다"})
            continue
        if pickup.world_x is None or dropoff.world_x is None:
            skipped.append({"robot_id": a.robot_id, "reason": "POI 좌표가 없습니다"})
            continue

        # 4) wp_list 구성 — 각 위치 도착 후 다음으로 가기 전 wait_sec 대기
        wait_sec = max(0, int(a.wait_sec or 0))
        wp_list = [
            {"poi_id": pickup.id, "name": pickup.name, "x": pickup.world_x, "y": pickup.world_y,
             "ori": pickup.angle or 0, "waypoint_type": "pickup",
             "poi_type": pickup.poi_type or "general", "wait_sec": wait_sec},
            {"poi_id": dropoff.id, "name": dropoff.name, "x": dropoff.world_x, "y": dropoff.world_y,
             "ori": dropoff.angle or 0, "waypoint_type": "dropoff",
             "poi_type": dropoff.poi_type or "general", "wait_sec": wait_sec},
        ]

        # 5) POI 락
        lock_pids: list[int] = [pickup.id, dropoff.id]
        if getattr(robot, "charging_id", None):
            lock_pids.append(robot.charging_id)
        if getattr(robot, "standby_id", None):
            lock_pids.append(robot.standby_id)
        ok, conflict_pid = poi_lock.try_acquire(lock_pids, robot.id)
        if not ok:
            owner = poi_lock.get_owner(conflict_pid)
            skipped.append({"robot_id": a.robot_id,
                            "reason": f"다른 로봇(id={owner})이 동일 POI(id={conflict_pid}) 사용 중"})
            continue

        # 6) 이력 row
        rc = a.repeat_count if (a.repeat_count is not None and a.repeat_count > 0) else None
        repeat_label = f"x{rc}" if rc is not None else "∞"
        history = TaskHistory(
            task_name=f"배치({repeat_label}): {pickup.name}→{dropoff.name}",
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
        robot_id_for_lock = robot.id

        # 7) 백그라운드 실행 (반복 루프)
        def _run(history_id=history_id, robot_ip=robot_ip, wp_list=wp_list,
                 work_mode=work_mode, robot_id_for_lock=robot_id_for_lock,
                 repeat_max=rc, wait_between=wait_sec):
            try:
                iteration = 0
                final_status = "succeeded"
                final_msg: str | None = None
                while True:
                    iteration += 1
                    # 첫 회만 standby 에서 랙 픽업. 마지막 회만 standby 로 랙 복귀.
                    # 중간/무한 반복 회차는 잭업 상태로 사이클을 계속 이어감 (start_jacked / end_jacked).
                    is_first = iteration == 1
                    is_last_iteration = (repeat_max is not None) and (iteration >= repeat_max)
                    result = run_route_job(
                        robot_ip, wp_list,
                        work_mode=work_mode,
                        skip_standby_pickup=(not is_first),
                        skip_standby_return=(not is_last_iteration),
                        start_jacked=(not is_first),
                        end_jacked=(not is_last_iteration),
                        robot_id=robot_id_for_lock,
                    )
                    if result["status"] != "done":
                        final_status = "failed"
                        final_msg = result.get("message")
                        break
                    # 종료 조건
                    if is_last_iteration:
                        break
                    if wait_between > 0:
                        _interruptible_sleep(robot_ip, wait_between)

                # 이력 마무리
                db2 = _SL()
                try:
                    h = db2.query(TaskHistory).filter(TaskHistory.id == history_id).first()
                    if h:
                        h.status = final_status
                        h.finished_at = _dt.now()
                        h.error_message = final_msg
                        db2.commit()
                finally:
                    db2.close()
                # 충전소 복귀 (성공 시에만)
                if final_status == "succeeded":
                    _return_to_charger(robot_ip, wp_list)
            except RuntimeError:
                # 사용자 중지
                db2 = _SL()
                try:
                    h = db2.query(TaskHistory).filter(TaskHistory.id == history_id).first()
                    if h:
                        h.status = "cancelled"
                        h.finished_at = _dt.now()
                        db2.commit()
                finally:
                    db2.close()
            finally:
                poi_lock.release_all_by_robot(robot_id_for_lock)

        safe_thread(target=_run, name=f"batch-{robot.id}").start()
        started.append({"robot_id": a.robot_id, "history_id": history_id,
                        "repeat": repeat_label})

    # 활동 로그
    from app.crud.activity_log import log_activity
    log_activity(
        "user", "manual_run_batch",
        f"배치 수동 배차: 시작 {len(started)}대 / skip {len(skipped)}대",
        source="api_manual_run_batch",
    )
    return {"started": started, "skipped": skipped}


# ══════════════════════════════════════
# POI 락 상태 (디버깅/운영)
# ══════════════════════════════════════

@router.get("/poi-locks")
def api_get_poi_locks(db: Session = Depends(get_db)):
    """현재 잡혀있는 POI 락 스냅샷 (poi_id → robot_id)"""
    snap = poi_lock.snapshot()
    if not snap:
        return {"locks": []}
    poi_ids = list(snap.keys())
    robot_ids = list(set(snap.values()))
    pois = {p.id: p.name for p in db.query(MapPOI).filter(MapPOI.id.in_(poi_ids)).all()}
    robots = {r.id: r.name for r in db.query(Robot).filter(Robot.id.in_(robot_ids)).all()}
    return {
        "locks": [
            {
                "poi_id": pid,
                "poi_name": pois.get(pid),
                "robot_id": rid,
                "robot_name": robots.get(rid),
            }
            for pid, rid in snap.items()
        ]
    }


@router.delete("/poi-locks/robot/{robot_id}")
def api_release_poi_locks(robot_id: int):
    """특정 로봇이 잡고 있는 모든 POI 락 강제 해제 (작업이 비정상 종료된 경우용)"""
    poi_lock.release_all_by_robot(robot_id)
    return {"message": f"robot {robot_id} 의 POI 락을 모두 해제했습니다"}


@router.get("/deadlock-monitor")
def api_get_deadlock_snapshot():
    """데드락 모니터가 누적 중인 로봇별 샘플 윈도우 (디버깅용)"""
    from app.services import deadlock_monitor
    return {"window": deadlock_monitor.snapshot()}


@router.post("/routes/remap-to-map/{map_id}")
def api_remap_routes(map_id: int, db: Session = Depends(get_db)):
    """같은 area 의 다른 맵 POI를 참조 중인 경로 웨이포인트를 새 맵으로 자동 재매핑.
    POI 이름이 같은 것끼리 매칭. 새 맵에 없는 이름은 missing 으로 반환."""
    from app.crud.map import remap_task_waypoints_to_map
    return remap_task_waypoints_to_map(db, map_id)


@router.get("/zone-locks")
def api_get_zone_locks(db: Session = Depends(get_db)):
    """현재 점유 중인 zone (좁은 통로) 락 스냅샷 (zone_id → robot)"""
    from app.services import zone_lock
    from app.models.map import MapPolygon
    snap = zone_lock.snapshot()
    if not snap:
        return {"locks": []}
    zone_ids = list(snap.keys())
    robot_ids = list(set(snap.values()))
    zones = {z.id: z.name for z in db.query(MapPolygon).filter(MapPolygon.id.in_(zone_ids)).all()}
    robots = {r.id: r.name for r in db.query(Robot).filter(Robot.id.in_(robot_ids)).all()}
    return {
        "locks": [
            {
                "zone_id": zid,
                "zone_name": zones.get(zid),
                "robot_id": rid,
                "robot_name": robots.get(rid),
            }
            for zid, rid in snap.items()
        ]
    }


@router.delete("/zone-locks/robot/{robot_id}")
def api_release_zone_locks(robot_id: int):
    """특정 로봇의 zone 락 강제 해제 (비정상 종료 복구용)"""
    from app.services import zone_lock
    zone_lock.release_all_by_robot(robot_id)
    return {"message": f"robot {robot_id} 의 zone 락 해제 완료"}


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

    # 로봇의 현재 영역 맵에서 POI 조회
    robot_area_id = int(robot.area_id) if robot and robot.area_id else None
    if robot_area_id:
        active_map = db.query(RobotMap).filter(
            RobotMap.area_id == robot_area_id, RobotMap.is_active == True
        ).order_by(RobotMap.id.desc()).first()
    else:
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
