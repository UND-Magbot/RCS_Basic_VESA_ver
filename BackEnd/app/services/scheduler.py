"""
APScheduler 기반 작업 스케줄러
- 날짜/시간/요일 기반 트리거
"""
import logging
from datetime import datetime, time, date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.database import SessionLocal
from app.models.task import ScheduledTask, TaskHistory, TaskRoute, TaskRouteWaypoint
from app.models.map import MapPOI
from app.models.robot import Robot
from app.services.jack_service import run_jack_job, run_route_job
from app.services import poi_lock

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_running_robots: set[int] = set()
import threading as _threading_lock
_running_robots_lock = _threading_lock.Lock()


def _try_mark_running(robot_id: int) -> bool:
    """원자적으로 robot 작업 시작 표시. 이미 실행 중이면 False."""
    with _running_robots_lock:
        if robot_id in _running_robots:
            return False
        _running_robots.add(robot_id)
        return True


def _unmark_running(robot_id: int) -> None:
    with _running_robots_lock:
        _running_robots.discard(robot_id)

# 요일 매핑: 1=월 ~ 7=일
DOW_MAP = {"1": "mon", "2": "tue", "3": "wed", "4": "thu", "5": "fri", "6": "sat", "7": "sun"}


def _build_trigger(task: ScheduledTask):
    """날짜/시간/요일 → APScheduler 트리거 변환"""
    h, m = task.start_time.split(":")
    hour, minute = int(h), int(m)

    if task.repeat_type == "once":
        dt = datetime.combine(task.start_date, time(hour, minute))
        return DateTrigger(run_date=dt)

    elif task.repeat_type == "daily":
        return CronTrigger(
            hour=hour, minute=minute,
            start_date=task.start_date,
            end_date=task.end_date,
        )

    elif task.repeat_type == "weekly":
        days = task.repeat_days or "1,2,3,4,5"
        dow = ",".join(DOW_MAP.get(d.strip(), "mon") for d in days.split(","))
        return CronTrigger(
            day_of_week=dow,
            hour=hour, minute=minute,
            start_date=task.start_date,
            end_date=task.end_date,
        )

    return None


def init_scheduler():
    scheduler.start()
    logger.info("[scheduler] Started")

    db = SessionLocal()
    try:
        # 만료된 once 작업 자동 삭제 (cascade 방지를 위해 직접 DELETE)
        from datetime import date
        expired_ids = [t.id for t in db.query(ScheduledTask.id).filter(
            ScheduledTask.repeat_type == "once",
            ScheduledTask.start_date < date.today(),
        ).all()]
        if expired_ids:
            db.query(TaskHistory).filter(TaskHistory.task_id.in_(expired_ids)).delete(synchronize_session=False)
            db.query(ScheduledTask).filter(ScheduledTask.id.in_(expired_ids)).delete(synchronize_session=False)
            db.commit()
            for eid in expired_ids:
                logger.info(f"[scheduler] Deleted expired task {eid}")

        tasks = db.query(ScheduledTask).filter(
            ScheduledTask.is_active == True,
        ).all()
        for task in tasks:
            add_task_job(task)
            logger.info(f"[scheduler] Loaded task {task.id}: {task.name}")
    except Exception as e:
        logger.error(f"[scheduler] Failed to load tasks: {e}")
    finally:
        db.close()


def shutdown_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("[scheduler] Shutdown")


def add_task_job(task: ScheduledTask):
    trigger = _build_trigger(task)
    if not trigger:
        return
    try:
        scheduler.add_job(
            execute_scheduled_task,
            trigger=trigger,
            id=f"task_{task.id}",
            args=[task.id],
            replace_existing=True,
            misfire_grace_time=60,
        )
    except Exception as e:
        logger.error(f"[scheduler] Failed to add job task_{task.id}: {e}")


def add_task_job_by_id(task_id: int):
    """DB에서 task를 읽어서 스케줄러에 등록"""
    db = SessionLocal()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if task and task.is_active:
            add_task_job(task)
    finally:
        db.close()


def remove_task_job(task_id: int):
    try:
        scheduler.remove_job(f"task_{task_id}")
    except Exception:
        pass


def get_next_run_time(task_id: int) -> datetime | None:
    job = scheduler.get_job(f"task_{task_id}")
    if job and job.next_run_time:
        return job.next_run_time
    return None


def _return_rack_to_standby(robot_ip: str):
    """마지막 드롭오프 위치에서 빈 랙을 다시 들어서 랙 위치 (standby POI) 에 보관."""
    from app.services.jack_service import (
        create_move, wait_move, safe_move, jack_up, jack_down,
        _get_standby_poi, update_job_status, JACK_WAIT_SEC,
        _interruptible_sleep,
    )

    standby = _get_standby_poi()
    if not standby:
        logger.info("[scheduler] 랙 위치 POI 없음, 랙 복귀 생략")
        return

    try:
        update_job_status(robot_ip, status="aligning", message="랙 위치로 복귀 위해 잭 올리는 중...")

        # 현재 위치(마지막 드롭오프) 에서 바로 잭 업 (랙 다시 들기)
        jack_up(robot_ip)
        _interruptible_sleep(robot_ip, JACK_WAIT_SEC)

        # 랙 위치로 이동
        update_job_status(robot_ip, status="moving", message=f"랙 위치({standby['name']})로 복귀 중...")
        safe_move(robot_ip, "to_unload_point", standby["x"], standby["y"], standby.get("ori", 0), timeout=120)

        # 잭 다운 (랙 보관)
        update_job_status(robot_ip, status="jacking_down", message=f"랙 위치({standby['name']}) 잭 내리는 중...")
        jack_down(robot_ip)
        _interruptible_sleep(robot_ip, JACK_WAIT_SEC)

        logger.info(f"[scheduler] 랙 위치 복귀 완료: {standby['name']}")
    except Exception as e:
        logger.error(f"[scheduler] 랙 위치 복귀 실패: {e}")
        from app.crud.activity_log import log_activity
        log_activity("robot", "move_error", f"랙 위치 복귀 실패: {str(e)}", source="scheduler")


def _return_to_charger(robot_ip: str, wp_list: list[dict]):
    """작업 종료 후 충전소 복귀"""
    # 1) 로봇에 지정된 charging_id 우선 사용
    charger = None
    db = SessionLocal()
    try:
        from app.models.robot import Robot
        robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
        if robot and robot.charging_id:
            charging_poi = db.query(MapPOI).filter(
                MapPOI.id == robot.charging_id, MapPOI.is_active == True
            ).first()
            if charging_poi and charging_poi.world_x is not None:
                charger = {
                    "name": charging_poi.name,
                    "x": charging_poi.world_x,
                    "y": charging_poi.world_y,
                    "ori": charging_poi.angle or 0,
                }
    finally:
        db.close()

    # 2) 경로에서 충전소 찾기
    if not charger:
        for wp in wp_list:
            if wp.get("poi_type") == "charging" or wp.get("waypoint_type") == "charging":
                charger = wp
                break

    # 3) 경로에도 없으면 DB에서 첫 번째 충전소 POI 찾기 (폴백)
    if not charger:
        db = SessionLocal()
        try:
            charging_poi = db.query(MapPOI).filter(
                MapPOI.poi_type == "charging",
                MapPOI.is_active == True,
            ).first()
            if charging_poi and charging_poi.world_x is not None:
                charger = {
                    "name": charging_poi.name,
                    "x": charging_poi.world_x,
                    "y": charging_poi.world_y,
                    "ori": charging_poi.angle or 0,
                }
        finally:
            db.close()

    if not charger:
        logger.info("[scheduler] 충전소 POI 없음, 복귀 생략")
        return

    logger.info(f"[scheduler] 충전소 복귀: {charger['name']}")
    try:
        from app.services.jack_service import create_move, wait_move, safe_move, update_job_status
        import time as _time
        import math as _math
        update_job_status(robot_ip, status="returning", detail="작업 완료 후 충전소로 복귀 중...")
        # DB 충전소 좌표 = 도킹 지점. yaw 는 로봇이 충전기를 바라볼 방향.
        cx, cy = charger["x"], charger["y"]
        cyaw = charger.get("ori", 0)

        # 사전 접근 지점 (도킹 지점에서 yaw 반대 방향 60cm)
        APPROACH_DIST = 0.6  # m
        approach_x = cx - APPROACH_DIST * _math.cos(cyaw)
        approach_y = cy - APPROACH_DIST * _math.sin(cyaw)

        # 1단계: standard 사전 접근 — best-effort (실패해도 charge 로 직접 진행)
        # `failed to calc global path` 등 경로 계산 불가 케이스가 자주 발생하므로 무한 재시도 X.
        try:
            std_move = create_move(robot_ip, "standard", approach_x, approach_y, cyaw)
            std_result = wait_move(robot_ip, std_move, timeout=60)
            if std_result.get("state") != "succeeded":
                logger.warning(
                    f"[scheduler] 사전 접근 실패({std_result.get('fail_message') or std_result.get('state')}) "
                    f"— charge 단계로 직접 진행"
                )
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"[scheduler] 사전 접근 예외: {e} — charge 단계로 직접 진행")
        _time.sleep(2)

        # 2단계: charge로 도킹 (target_ori 명시, 재시도)
        for attempt in range(5):
            try:
                move_id = create_move(robot_ip, "charge", cx, cy, cyaw, charge_retry_count=3)
                wait_move(robot_ip, move_id, timeout=120)
                logger.info(f"[scheduler] 충전소 도킹 완료: {charger['name']}")
                from app.services.jack_service import clear_job_status
                clear_job_status(robot_ip)
                break
            except RuntimeError:
                # 중지 명령 — 도킹 재시도 중단
                raise
            except Exception as e:
                if attempt < 4:
                    logger.warning(f"[scheduler] 충전소 도킹 재시도 ({attempt+1}/5): {e}")
                    _time.sleep(5)
                else:
                    raise
    except Exception as e:
        logger.error(f"[scheduler] 충전소 복귀 실패: {e}")
        from app.crud.activity_log import log_activity
        log_activity("robot", "dock_error", f"충전소 복귀 실패: {str(e)}", source="scheduler")


def execute_scheduled_task(task_id: int):
    """스케줄러에서 호출 — 경로 기반 잭킹 실행"""
    db = SessionLocal()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task or not task.is_active:
            return

        route = db.query(TaskRoute).filter(TaskRoute.id == task.route_id).first()
        if not route:
            logger.error(f"[scheduler] Route {task.route_id} not found for task {task_id}")
            from app.crud.activity_log import log_activity
            log_activity("robot", "task_error", f"스케줄 '{task.name}' 실행 실패: 경로를 찾을 수 없습니다", source="scheduler")
            return

        robot = db.query(Robot).filter(Robot.id == task.robot_id).first()
        if not robot or not robot.ip_address:
            logger.error(f"[scheduler] Robot not found for route {route.id}")
            from app.crud.activity_log import log_activity
            log_activity("robot", "task_error", f"스케줄 '{task.name}' 실행 실패: 로봇을 찾을 수 없습니다", source="scheduler")
            return

        if not _try_mark_running(robot.id):
            logger.warning(f"[scheduler] Robot {robot.id} busy, skipping task {task_id}")
            from app.crud.activity_log import log_activity
            log_activity("robot", "task_error", f"스케줄 '{task.name}' 실행 건너뜀: 로봇이 작업 중입니다", source="scheduler")
            return

        # 경로 웨이포인트에서 POI 추출
        waypoints = db.query(TaskRouteWaypoint).filter(
            TaskRouteWaypoint.route_id == route.id
        ).order_by(TaskRouteWaypoint.order).all()

        wp_list = []
        first_pickup = None
        first_dropoff = None
        for wp in waypoints:
            poi = db.query(MapPOI).filter(MapPOI.id == wp.poi_id).first()
            if not poi:
                continue
            wp_data = {
                "poi_id": poi.id,
                "name": poi.name,
                "x": poi.world_x,
                "y": poi.world_y,
                "ori": poi.angle or 0,
                "waypoint_type": wp.waypoint_type,
                "poi_type": poi.poi_type or "general",
                "wait_sec": wp.wait_sec or 0,
            }
            wp_list.append(wp_data)
            if wp.waypoint_type == "pickup" and not first_pickup:
                first_pickup = poi.name
            if wp.waypoint_type == "dropoff" and not first_dropoff:
                first_dropoff = poi.name

        if len(wp_list) < 2:
            logger.error(f"[scheduler] Not enough waypoints for route {route.id}")
            from app.crud.activity_log import log_activity
            log_activity("robot", "task_error", f"스케줄 '{task.name}' 실행 실패: 경로에 웨이포인트가 부족합니다", source="scheduler")
            _unmark_running(robot.id)
            return

        # POI 락 — 같은 POI를 다른 로봇이 잡고 있으면 작업 skip
        lock_pids = [w.get("poi_id") for w in wp_list]
        if getattr(robot, "charging_id", None):
            lock_pids.append(robot.charging_id)
        if getattr(robot, "standby_id", None):
            lock_pids.append(robot.standby_id)
        ok, conflict_pid = poi_lock.try_acquire(lock_pids, robot.id)
        if not ok:
            owner = poi_lock.get_owner(conflict_pid)
            logger.warning(f"[scheduler] POI {conflict_pid} busy (robot={owner}), skipping task {task_id}")
            from app.crud.activity_log import log_activity
            log_activity("robot", "task_error", f"스케줄 '{task.name}' 실행 건너뜀: POI를 다른 로봇이 사용 중", source="scheduler")
            _unmark_running(robot.id)
            return

        # 이력 생성
        history = TaskHistory(
            task_id=task.id,
            task_name=task.name,
            route_name=route.name,
            robot_id=robot.id,
            robot_name=robot.name,
            pickup_poi_name=first_pickup,
            dropoff_poi_name=first_dropoff,
            status="running",
        )
        db.add(history)
        task.last_run_at = datetime.now()
        db.commit()
        db.refresh(history)
        history_id = history.id
        robot_ip = robot.ip_address
        robot_id = robot.id

        # 세션 닫기 전에 값 저장
        task_name = task.name
        route_name = route.name
        robot_name = robot.name
        end_time_str = task.end_time

    except Exception as e:
        logger.exception(f"[scheduler] Error preparing task {task_id}: {e}")
        _rid = getattr(robot, 'id', -1) if 'robot' in dir() else -1
        _unmark_running(_rid)
        poi_lock.release_all_by_robot(_rid)
        db.close()
        return
    db.close()

    def _is_within_end_time():
        """현재 시간이 end_time 이전인지 확인"""
        if not end_time_str:
            return False  # end_time 없으면 반복 안 함
        try:
            h, m = end_time_str.split(":")
            now = datetime.now()
            end_dt = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
            return now < end_dt
        except Exception:
            return False

    def _run_once(skip_standby_pickup=False, skip_standby_return=False) -> dict:
        """1회 실행 + 이력 기록"""
        nonlocal history_id
        # 이력 생성 (반복 시 새 이력)
        db_h = SessionLocal()
        try:
            hist = TaskHistory(
                task_id=task_id,
                task_name=task_name,
                route_name=route_name,
                robot_id=robot_id,
                robot_name=robot_name,
                pickup_poi_name=first_pickup,
                dropoff_poi_name=first_dropoff,
                status="running",
            )
            db_h.add(hist)
            db_h.commit()
            db_h.refresh(hist)
            history_id = hist.id
        finally:
            db_h.close()

        result = run_route_job(robot_ip, wp_list,
                               skip_standby_pickup=skip_standby_pickup,
                               skip_standby_return=skip_standby_return,
                               work_mode=getattr(route, "work_mode", "rack_pickup"),
                               robot_id=robot_id)

        db_h2 = SessionLocal()
        try:
            h = db_h2.query(TaskHistory).filter(TaskHistory.id == history_id).first()
            if h:
                h.status = "succeeded" if result["status"] == "done" else "failed"
                h.finished_at = datetime.now()
                h.error_message = result.get("message") if result["status"] != "done" else None
                db_h2.commit()
        finally:
            db_h2.close()
        return result

    # 경로 실행 (end_time까지 반복)
    has_repeat = bool(end_time_str) and _is_within_end_time()

    try:
        # 첫 실행: W1 픽업 O, W1 복귀는 반복이면 스킵
        result = run_route_job(robot_ip, wp_list,
                               skip_standby_pickup=False,
                               skip_standby_return=has_repeat,
                               work_mode=getattr(route, "work_mode", "rack_pickup"),
                               robot_id=robot_id)
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

        # end_time 전이면 반복 실행 (W1 픽업/복귀 둘 다 스킵)
        while result["status"] == "done" and _is_within_end_time():
            logger.info(f"[scheduler] Task {task_id}: 완료 후 재실행 (end_time={end_time_str}까지)")
            # 마지막인지 미리 알 수 없으므로 W1 복귀 스킵
            result = _run_once(skip_standby_pickup=True, skip_standby_return=True)
            if result["status"] != "done":
                break

        # 반복 종료 후 마지막 드롭오프에서 랙 위치(standby POI) 로 복귀
        if has_repeat and result["status"] == "done":
            _return_rack_to_standby(robot_ip)

        # 충전소 복귀 (성공 시에만)
        if result["status"] == "done":
            _return_to_charger(robot_ip, wp_list)

    except Exception as e:
        logger.exception(f"[scheduler] Task {task_id} error: {e}")
        db2 = SessionLocal()
        try:
            h = db2.query(TaskHistory).filter(TaskHistory.id == history_id).first()
            if h:
                h.status = "failed"
                h.finished_at = datetime.now()
                h.error_message = str(e)
                db2.commit()
        finally:
            db2.close()
    finally:
        _unmark_running(robot_id)
        poi_lock.release_all_by_robot(robot_id)
