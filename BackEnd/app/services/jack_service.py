"""
잭킹 작업 공용 서비스
- 로봇 REST API 헬퍼 함수
- 잭킹 흐름 실행 (align_with_rack → jack_up → to_unload_point → jack_down → standby)
"""
import logging
import math
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

ROBOT_PORT = 8090
HTTP_TIMEOUT = 10
import json as _json


def get_docking_point_coords(ip: str, charger_name: str):
    """로봇 맵에서 충전소 도킹포인트(type=36) 좌표 조회 → (x, y, yaw) 또는 None"""
    try:
        r = requests.get(f"http://{ip}:{ROBOT_PORT}/chassis/current-map", timeout=HTTP_TIMEOUT)
        map_id = r.json().get("id")
        if not map_id:
            return None
        r = requests.get(f"http://{ip}:{ROBOT_PORT}/maps/{map_id}", timeout=HTTP_TIMEOUT)
        overlays = _json.loads(r.json().get("overlays", "{}"))
        features = overlays.get("features", [])

        # 충전소(type=9)에서 도킹포인트 ID 찾기
        docking_point_id = None
        for feat in features:
            props = feat.get("properties", {})
            if str(props.get("type")) == "9" and props.get("name") == charger_name:
                docking_point_id = props.get("dockingPointId")
                break

        if not docking_point_id:
            return None

        # 도킹포인트(type=36) 좌표 조회
        for feat in features:
            if feat.get("id") == docking_point_id:
                coords = feat.get("geometry", {}).get("coordinates", [])
                props = feat.get("properties", {})
                yaw = float(props.get("yaw", 0))
                if len(coords) >= 2:
                    logger.info(f"[{ip}] '{charger_name}' 도킹포인트: ({coords[0]}, {coords[1]}, yaw={yaw})")
                    return (coords[0], coords[1], yaw)
        return None
    except Exception as e:
        logger.warning(f"[{ip}] 도킹포인트 좌표 조회 실패: {e}")
        return None
POLL_INTERVAL = 1.0
MOVE_TIMEOUT = 120

# 실행 중인 작업 추적 (robot_ip → stop flag)
_stop_flags: dict[str, bool] = {}

# 일시정지 플래그 (robot_ip → bool). True 인 동안 wait_move/safe_move/_interruptible_sleep 가 대기.
_paused_flags: dict[str, bool] = {}

# 실행 중인 작업 상태 (robot_ip → job info)
_job_status: dict[str, dict] = {}

# 수동 확인 대기 (robot_ip → threading.Event)
import threading as _threading
_confirm_events: dict[str, _threading.Event] = {}

# 다음 포인트 (robot_ip → poi dict or "return")
_next_poi: dict[str, dict | str | None] = {}

# ── 자동 재시도용 작업 메타데이터 ──────────────────────────────
# 진행 중인 route 작업의 입력값 (양보 후 재실행 가능하도록 보관)
# robot_ip → {robot_id, waypoints, manual_confirm, area_id, work_mode}
_active_route_jobs: dict[str, dict] = {}
# 양보로 일시정지된 작업 (deadlock_monitor 가 시간 경과 후 재실행)
# robot_ip → {**meta, paused_at, retries_left}
_paused_route_jobs: dict[str, dict] = {}
# create_move 의 zone 사전 락에 robot_id 를 라우팅하기 위한 매핑
# (한 로봇은 동시에 한 route 작업만 실행되도록 보장되므로 안전)
_running_robot_id_by_ip: dict[str, int] = {}


def _get_standby_poi(area_id: int | None = None, robot_ip: str | None = None) -> dict | None:
    """로봇의 standby_id 우선, 없으면 영역의 standby POI(W1) 조회"""
    from app.database import SessionLocal
    from app.models.map import MapPOI, RobotMap
    from app.models.robot import Robot
    db = SessionLocal()
    try:
        # 1) 로봇에 standby_id가 지정되어 있으면 그것 사용
        if robot_ip:
            robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
            if robot and robot.standby_id:
                poi = db.query(MapPOI).filter(
                    MapPOI.id == robot.standby_id, MapPOI.is_active == True
                ).first()
                if poi and poi.world_x is not None:
                    return {"name": poi.name, "x": poi.world_x, "y": poi.world_y, "ori": poi.angle or 0}

        # 2) 폴백: area_id 또는 최신 맵의 standby POI
        if area_id:
            active_map = db.query(RobotMap).filter(
                RobotMap.area_id == area_id, RobotMap.is_active == True
            ).order_by(RobotMap.id.desc()).first()
        else:
            active_map = db.query(RobotMap).filter(RobotMap.is_active == True).order_by(RobotMap.id.desc()).first()
        if not active_map:
            return None
        poi = db.query(MapPOI).filter(
            MapPOI.map_id == active_map.id,
            MapPOI.poi_type == "standby",
            MapPOI.is_active == True,
        ).first()
        if poi and poi.world_x is not None:
            return {"name": poi.name, "x": poi.world_x, "y": poi.world_y, "ori": poi.angle or 0}
        return None
    finally:
        db.close()


# 사용자 확인 대기 기본 한도 (좀비 작업 방지)
CONFIRM_TIMEOUT_SEC = 1800  # 30분


def wait_for_confirm(robot_ip: str, timeout: int | None = None) -> bool:
    """사용자 확인 버튼을 기다림.
    timeout=None 이면 CONFIRM_TIMEOUT_SEC(30분) 적용 — 무한 대기 방지.

    반환:
      - True  : 정상 confirm 또는 stop 신호로 깨어남
                (stop 으로 깬 경우 호출자는 이어지는 _check_stop 에서 RuntimeError 로 종료됨)
      - False : 타임아웃

    Event 객체는 try/finally 로 _confirm_events 에서 정리 보장 — 예외 시 좀비 entry 누적 방지.
    """
    evt = _threading.Event()
    _confirm_events[robot_ip] = evt
    effective_timeout = timeout if timeout is not None else CONFIRM_TIMEOUT_SEC
    try:
        result = evt.wait(timeout=effective_timeout)
        if not result:
            logger.warning(f"[wait_for_confirm] {robot_ip} 확인 대기 타임아웃 ({effective_timeout}s)")
        return result
    finally:
        _confirm_events.pop(robot_ip, None)


def confirm_robot(robot_ip: str):
    """사용자가 확인 버튼을 눌렀을 때 호출"""
    evt = _confirm_events.get(robot_ip)
    if evt:
        evt.set()
        logger.info(f"[jack_service] confirm received for {robot_ip}")


def set_next_poi(robot_ip: str, poi: dict | str):
    """다음 포인트 설정 (poi dict 또는 'return') + confirm 트리거"""
    _next_poi[robot_ip] = poi
    confirm_robot(robot_ip)
    logger.info(f"[jack_service] next poi set for {robot_ip}: {poi if isinstance(poi, str) else poi.get('name')}")


def get_next_poi(robot_ip: str) -> dict | str | None:
    """다음 포인트 가져오기 (한 번 읽으면 제거)"""
    return _next_poi.pop(robot_ip, None)


def stop_robot_job(robot_ip: str):
    """특정 로봇의 진행 중인 작업에 중지 플래그 설정 + 상태 제거.

    wait_for_confirm 안에서 대기 중인 작업도 즉시 깨우기 위해 _confirm_events 의 event 를 set.
    깨어난 작업은 곧이은 _check_stop() 에서 RuntimeError 로 정상 종료된다.
    (set 안 하면 최대 CONFIRM_TIMEOUT_SEC=30분 좀비 thread 가 됨)
    """
    _stop_flags[robot_ip] = True
    _job_status.pop(robot_ip, None)
    _next_poi.pop(robot_ip, None)
    evt = _confirm_events.get(robot_ip)
    if evt is not None:
        evt.set()
    logger.info(f"[jack_service] stop flag set for {robot_ip}")


def yield_robot_job(robot_ip: str, retries: int = 1) -> bool:
    """양보 — 현재 작업을 멈추되, 재시도용 메타를 _paused_route_jobs 로 옮김.
    deadlock_monitor 가 N초 후 같은 입력으로 run_route_job 을 다시 호출하게 된다.
    반환: 양보 가능 여부 (메타가 있으면 True).
    """
    meta = _active_route_jobs.get(robot_ip)
    if meta is None:
        # 등록된 메타가 없으면 일반 stop 으로 폴백
        stop_robot_job(robot_ip)
        return False
    _paused_route_jobs[robot_ip] = {
        **meta,
        "paused_at": time.time(),
        "retries_left": retries,
    }
    stop_robot_job(robot_ip)
    logger.info(f"[jack_service] yielded {robot_ip} (retries_left={retries})")
    return True


def get_paused_jobs() -> dict[str, dict]:
    """양보로 일시정지된 작업 스냅샷 (deadlock_monitor 가 폴링)."""
    return dict(_paused_route_jobs)


def consume_paused_job(robot_ip: str) -> dict | None:
    """양보된 메타를 꺼내가며 제거 (재실행 직전 호출)."""
    return _paused_route_jobs.pop(robot_ip, None)


def force_return_and_dock(robot_ip: str, robot_id: int | None = None):
    """강제 종료 — 현재 진행 중 작업을 중단하고 충전소 도킹까지 수행.

    work_mode 별 분기:
      - rack_pickup        : 현재 위치에서 잭 업 → 랙 위치(standby POI) 복귀 → 잭 다운 → 충전소
      - delivery_no_rack   : 안전을 위해 잭 다운 한 번 시도 → 충전소
      - simple_move        : 잭 조작 없이 곧장 충전소 복귀

    work_mode 가 명시되지 않으면 _active_route_jobs 메타에서 추정, 그것도 없으면
    rack_pickup 으로 폴백(랙을 들고 있을 가능성을 가정 — 안전한 디폴트).
    """
    from app.services.scheduler import _return_to_charger

    # work_mode 캡쳐 — stop_robot_job 전에 메타에서 읽어둠 (작업 스레드가 finally 에서 pop 할 수 있어 선캡쳐)
    meta = _active_route_jobs.get(robot_ip) or {}
    work_mode = (meta.get("work_mode") or "rack_pickup")
    is_rack_pickup = (work_mode == "rack_pickup")
    is_delivery_no_rack = (work_mode == "delivery_no_rack")

    logger.warning(f"[force_return] {robot_ip} 강제 종료 시작 (work_mode={work_mode})")

    # 1) 진행 중 작업 중단 (현재 thread 가 RuntimeError 로 빠져나옴)
    stop_robot_job(robot_ip)
    # 2) 진행 중 이동 cancel
    try:
        cancel_current_move(robot_ip)
    except Exception:
        pass
    # 3) 진행 중 thread 가 정리될 시간 확보 + paused 풀기
    time.sleep(3)
    _stop_flags.pop(robot_ip, None)
    _paused_flags.pop(robot_ip, None)

    try:
        if is_rack_pickup:
            update_job_status(robot_ip, status="returning", message="강제 종료 — 랙 보관 후 충전소 복귀")

            # 4) 현재 위치에서 잭 업 (이미 들고 있으면 일부 펌웨어는 noop, 일부는 에러 — 무시)
            try:
                jack_up(robot_ip)
                time.sleep(JACK_WAIT_SEC)
            except Exception as e:
                logger.warning(f"[force_return] jack_up: {e}")

            # 5) 랙 위치(standby POI) 로 이동 + 잭 다운
            standby = _get_standby_poi(robot_ip=robot_ip)
            if standby:
                update_job_status(robot_ip, status="moving",
                                  message=f"랙 위치({standby['name']})로 복귀 중")
                try:
                    safe_move(robot_ip, "to_unload_point",
                              standby["x"], standby["y"], standby.get("ori", 0),
                              max_attempts=30, timeout=120)
                except Exception as e:
                    logger.warning(f"[force_return] standby 이동 실패: {e}")
                try:
                    update_job_status(robot_ip, status="jacking_down",
                                      message=f"랙 위치({standby['name']}) 잭 내리는 중")
                    jack_down(robot_ip)
                    time.sleep(JACK_WAIT_SEC)
                except Exception as e:
                    logger.warning(f"[force_return] jack_down: {e}")
            else:
                logger.info(f"[force_return] {robot_ip} 랙 위치 POI 없음 — 충전소만 복귀")
        elif is_delivery_no_rack:
            # delivery_no_rack — 잭이 들려 있을 수 있어 안전을 위해 한 번 잭다운 시도 후 충전소
            update_job_status(robot_ip, status="jacking_down",
                              message="강제 종료 — 잭 내리는 중")
            try:
                jack_down(robot_ip)
                time.sleep(JACK_WAIT_SEC)
            except Exception as e:
                logger.warning(f"[force_return] jack_down: {e}")
            update_job_status(robot_ip, status="returning",
                              message="강제 종료 — 충전소로 복귀")
        else:
            # simple_move — 잭 조작 없이 곧장 충전소
            update_job_status(robot_ip, status="returning",
                              message=f"강제 종료 — 충전소로 복귀 ({work_mode})")

        # 6) 충전소 복귀
        try:
            _return_to_charger(robot_ip, [])
        except Exception as e:
            logger.warning(f"[force_return] 충전소 복귀 실패: {e}")

        from app.crud.activity_log import log_activity
        log_activity("robot", "force_return",
                     f"강제 종료 완료: {robot_ip} (work_mode={work_mode})",
                     source="force_return_and_dock")
    finally:
        # 7) 락/상태 정리
        if robot_id is not None:
            try:
                from app.services import poi_lock as _pl, zone_lock as _zl
                _pl.release_all_by_robot(robot_id)
                _zl.release_all_by_robot(robot_id)
            except Exception:
                pass
        clear_job_status(robot_ip)
        _stop_flags.pop(robot_ip, None)
        _paused_flags.pop(robot_ip, None)
        _active_route_jobs.pop(robot_ip, None)
        _running_robot_id_by_ip.pop(robot_ip, None)
        logger.info(f"[force_return] {robot_ip} 강제 종료 종료")


def _check_stop(robot_ip: str):
    """중지 플래그 확인 — True면 예외 발생"""
    if _stop_flags.get(robot_ip):
        _stop_flags.pop(robot_ip, None)
        raise RuntimeError(f"작업 중지됨 (robot={robot_ip})")


def is_paused(robot_ip: str) -> bool:
    return bool(_paused_flags.get(robot_ip))


def _wait_if_paused(robot_ip: str, poll: float = 1.0):
    """paused 동안 폴링 대기 — 중지가 들어오면 즉시 RuntimeError."""
    while _paused_flags.get(robot_ip):
        _check_stop(robot_ip)
        time.sleep(poll)


def pause_robot_job(robot_ip: str):
    """일시정지 — 현재 이동 즉시 cancel + paused 플래그 set.
    재개될 때까지 safe_move/wait_move/_interruptible_sleep 가 대기."""
    _paused_flags[robot_ip] = True
    try:
        cancel_current_move(robot_ip)
    except Exception:
        pass
    update_job_status(robot_ip, message="일시정지됨")
    logger.info(f"[pause] {robot_ip} 일시정지")


def resume_robot_job(robot_ip: str):
    """일시정지 해제 — 이전에 cancel 된 이동은 safe_move 가 자동 재시도."""
    _paused_flags.pop(robot_ip, None)
    update_job_status(robot_ip, message="작업 재개")
    logger.info(f"[resume] {robot_ip} 재개")


def _interruptible_sleep(robot_ip: str, seconds: float):
    """중지/일시정지 가능한 대기 — 1초 간격으로 plain check.
    paused 중에는 elapsed 가 진행되지 않음 (재개 시점부터 다시 카운트)."""
    elapsed = 0.0
    while elapsed < seconds:
        _check_stop(robot_ip)
        _wait_if_paused(robot_ip)
        sleep_time = min(1.0, seconds - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time


def update_job_status(robot_ip: str, **kwargs):
    """작업 상태 업데이트"""
    if robot_ip not in _job_status:
        _job_status[robot_ip] = {}
    _job_status[robot_ip].update(kwargs)


def clear_job_status(robot_ip: str):
    """작업 상태 제거"""
    _job_status.pop(robot_ip, None)


def get_job_status(robot_ip: str) -> dict | None:
    """작업 상태 조회"""
    return _job_status.get(robot_ip)


def get_all_job_status() -> dict:
    """모든 로봇 작업 상태 조회"""
    return dict(_job_status)


# ── 로봇 REST API 헬퍼 ──

def robot_url(ip: str, path: str) -> str:
    return f"http://{ip}:{ROBOT_PORT}{path}"


def robot_get(ip: str, path: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(robot_url(ip, path), timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt < retries - 1:
                time.sleep(3)
            else:
                raise


def robot_post(ip: str, path: str, json_body: dict | None = None, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.post(robot_url(ip, path), json=json_body or {}, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if attempt < retries - 1:
                time.sleep(3)
            else:
                raise


def robot_patch(ip: str, path: str, json_body: dict) -> dict:
    r = requests.patch(robot_url(ip, path), json=json_body, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def create_move(ip: str, move_type: str, target_x: float, target_y: float,
                target_ori: float = 0, retries: int = 5, **extra) -> int:
    # Zone 사전 락 — 이번 이동이 zone(좁은 통로 등)을 지나가면 다른 로봇 점유 해제까지 대기.
    rid = _running_robot_id_by_ip.get(ip)
    if rid is not None and move_type != "charge":
        try:
            from app.services.zone_guard import acquire_zones_for_move
            acquire_zones_for_move(
                ip, rid, target_x, target_y,
                is_stop_fn=lambda: _stop_flags.get(ip, False),
            )
        except Exception as e:
            logger.warning(f"[zone_guard] acquire 실패(무시): {e}")
    body = {
        "creator": "rcs",
        "type": move_type,
        "target_x": target_x,
        "target_y": target_y,
        "target_ori": target_ori,
        **extra,
    }
    for attempt in range(retries):
        try:
            resp = robot_post(ip, "/chassis/moves", body)
            return resp.get("id")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 400 and attempt < retries - 1:
                logger.warning(f"[move] 400 에러, {5}초 후 재시도 ({attempt+1}/{retries})")
                time.sleep(5)
            else:
                from app.crud.activity_log import log_activity
                log_activity("robot", "move_error", f"이동 명령 실패 ({move_type}): {str(e)}", source="jack_service")
                raise


# 좌표 자체가 잘못된 경우 영원히 도는 것 방지용 대형 한도 (≈ 200 * 5s = 17분)
SAFE_MOVE_DEFAULT_MAX_ATTEMPTS = 200


def safe_move(ip: str, move_type: str, target_x: float, target_y: float,
              target_ori: float = 0, retry_delay: float = 5.0,
              max_attempts: int | None = None, timeout: int = MOVE_TIMEOUT,
              **extra) -> dict:
    """create_move + wait_move 통합 + 자동 재시도.

    "작업이 무조건 이어가도록" 설계:
      - 통신 실패(ConnectionError 등) → 짧은 대기 후 재시도
      - wait_move 결과 'failed' / 'timeout' → 대기 후 재시도
      - 사용자 중지(_check_stop) → RuntimeError 로 즉시 빠져나감
      - 'succeeded' / 'cancelled' → 결과 반환
      - max_attempts=None 이면 SAFE_MOVE_DEFAULT_MAX_ATTEMPTS(200회 ≈ 17분) 까지 재시도.
        그 이상은 좌표 자체 오류 가능성 — 영원히 도는 것 방지.
    """
    if max_attempts is None:
        max_attempts = SAFE_MOVE_DEFAULT_MAX_ATTEMPTS
    attempt = 0
    last_result: dict = {}
    while True:
        _check_stop(ip)
        _wait_if_paused(ip)
        attempt += 1
        # 1) 이동 명령 발행
        try:
            move_id = create_move(ip, move_type, target_x, target_y, target_ori, **extra)
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"[safe_move] {ip} create_move 실패 (시도 {attempt}): {e}")
            update_job_status(ip, message=f"통신 오류 — 재시도 중 ({attempt}회차)")
            if max_attempts is not None and attempt >= max_attempts:
                return {"state": "failed", "fail_message": f"create_move 통신 실패: {e}"}
            _interruptible_sleep(ip, retry_delay)
            continue
        # 2) 이동 완료 대기
        try:
            result = wait_move(ip, move_id, timeout=timeout)
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"[safe_move] {ip} wait_move 통신 실패 (시도 {attempt}): {e}")
            update_job_status(ip, message=f"통신 오류 — 재시도 중 ({attempt}회차)")
            if max_attempts is not None and attempt >= max_attempts:
                return {"state": "failed", "fail_message": f"wait_move 통신 실패: {e}"}
            _interruptible_sleep(ip, retry_delay)
            continue
        last_result = result
        state = str(result.get("state", "")).lower()
        if state == "succeeded":
            if attempt > 1:
                logger.info(f"[safe_move] {ip} {attempt}회차 만에 성공 ({move_type})")
                update_job_status(ip, message=f"재시도 {attempt}회차에 이동 성공")
            return result
        if state == "cancelled":
            # paused/resumed 로 인한 cancel — 재개 후 같은 이동 재시도
            if result.get("_paused_cancel") or _paused_flags.get(ip):
                logger.info(f"[safe_move] {ip} pause/resume 으로 cancel — 같은 이동 재시도")
                _wait_if_paused(ip)
                _check_stop(ip)
                continue
            return result
        # 3) failed / timeout / unknown → 재시도
        fail_msg = result.get("fail_message") or state or "unknown"
        logger.warning(f"[safe_move] {ip} 이동 미완료 (시도 {attempt}, state={state}): {fail_msg}")
        update_job_status(ip, message=f"이동 미완료 ({fail_msg}) — 재시도 중 ({attempt}회차)")
        if max_attempts is not None and attempt >= max_attempts:
            return last_result
        _interruptible_sleep(ip, retry_delay)


def wait_move(ip: str, move_id: int, timeout: int = MOVE_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    saw_pause = False  # 이번 wait 동안 paused 들어간 적 있는지 추적
    while time.time() < deadline:
        _check_stop(ip)
        if _paused_flags.get(ip):
            saw_pause = True
        _wait_if_paused(ip)
        resp = robot_get(ip, f"/chassis/moves/{move_id}")
        state = resp.get("state", "")
        if state in ("succeeded", "failed", "cancelled"):
            # paused 때문에 발생한 cancel 임을 표시 — safe_move 가 재시도하도록
            if state == "cancelled" and saw_pause:
                resp["_paused_cancel"] = True
            return resp
        time.sleep(POLL_INTERVAL)
    return {"state": "timeout", "fail_message": f"Move {move_id} timed out after {timeout}s"}


def jack_up(ip: str) -> dict:
    return robot_post(ip, "/services/jack_up")


def jack_down(ip: str) -> dict:
    return robot_post(ip, "/services/jack_down")


def cancel_current_move(ip: str) -> dict:
    return robot_patch(ip, "/chassis/moves/current", {"state": "cancelled"})


JACK_WAIT_SEC = 10  # 잭 업/다운 고정 대기 시간(초)
JACK_IDLE_TIMEOUT = 30  # 잭 다운 후 로봇 idle 대기 최대 시간


def recover_positioning(ip: str, max_wait_sec: int = 15) -> bool:
    """SLAM 위치 복구 — start_global_positioning 호출 후 global_positioning_state 구독.

    1) start_global_positioning (use_barcode+use_base_map_match)
    2) /global_positioning_state WebSocket 구독하여 성공 대기
    3) 실패 시 current-map 재선택 후 재시도

    반환: True=성공, False=실패
    """
    import websocket as _ws
    import json as _json

    def _attempt() -> bool:
        try:
            requests.post(
                f"http://{ip}:{ROBOT_PORT}/services/start_global_positioning",
                json={"use_barcode": True, "use_base_map_match": True},
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"[recover] start_global_positioning 호출 실패: {e}")
            return False

        # /global_positioning_state 또는 /slam/state 구독
        deadline = time.time() + max_wait_sec
        try:
            ws = _ws.create_connection(f"ws://{ip}:{ROBOT_PORT}/ws/v2/topics", timeout=3)
            try:
                ws.send(_json.dumps({"enable_topic": "/global_positioning_state"}))
                ws.send(_json.dumps({"enable_topic": "/slam/state"}))
            except Exception:
                pass
            ws.settimeout(2)
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    continue
                try:
                    pkt = _json.loads(raw)
                except Exception:
                    continue
                topic = pkt.get("topic", "")
                if topic == "/global_positioning_state":
                    state = pkt.get("state", "")
                    if state in ("succeeded", "success", "done"):
                        ws.close()
                        logger.info(f"[recover] 위치 복구 성공 ({ip})")
                        return True
                    if state in ("failed", "error"):
                        ws.close()
                        logger.warning(f"[recover] 위치 복구 실패 state={state}")
                        return False
                elif topic == "/slam/state":
                    if pkt.get("lidar_matched") or pkt.get("reliable"):
                        ws.close()
                        logger.info(f"[recover] SLAM 매칭 확인 ({ip})")
                        return True
            ws.close()
        except Exception as e:
            logger.warning(f"[recover] WebSocket 오류: {e}")
        return False

    # 1차 시도
    if _attempt():
        return True

    # 2차: current-map 재선택 후 재시도
    try:
        cur = requests.get(f"http://{ip}:{ROBOT_PORT}/chassis/current-map", timeout=5)
        if cur.status_code == 200:
            map_id = cur.json().get("id")
            if map_id:
                requests.post(
                    f"http://{ip}:{ROBOT_PORT}/chassis/current-map",
                    json={"map_id": map_id}, timeout=10,
                )
                time.sleep(3)
                logger.info(f"[recover] current-map 재선택 후 재시도")
    except Exception as e:
        logger.warning(f"[recover] current-map 재선택 실패: {e}")

    return _attempt()


# 랙 인식 실패 방어용 — 단계별 회복 후 재시도
ALIGN_MAX_RETRIES = 4
ALIGN_BACKOFF_M = 0.5            # 후진 거리 (m) — LiDAR 시야 확보용
ALIGN_LATERAL_OFFSET_M = 0.3     # 좌/우 우회 거리 (m) — 마지막 시도용


def align_with_retry(ip: str, x: float, y: float, ori: float = 0,
                     max_retries: int = ALIGN_MAX_RETRIES) -> dict:
    """align_with_rack 재시도 — "정말 랙이 없는 게 아닌 한 인식되도록" 방어 로직.

    단계:
      1차) 그대로 시도
      2차) 재로컬화 후 시도               — 위치 추정 오류 대응
      3차) 후진 0.5m → 재로컬화 → 시도    — LiDAR 시야 협소 대응
      4차) 측면 우회 + 재로컬화 → 시도    — 접근 각도 변경 대응
    모든 단계 실패 시 마지막 결과 반환. cancel(중지)은 즉시 반환.
    """
    last_result: dict = {}
    for attempt in range(1, max_retries + 1):
        # 같은 시도 내에서 paused/resumed 로 인한 cancel 은 재시도 (attempt 카운트 X)
        while True:
            _check_stop(ip)
            _wait_if_paused(ip)
            try:
                move_id = create_move(ip, "align_with_rack", x, y, ori)
                result = wait_move(ip, move_id, timeout=120)
            except RuntimeError:
                raise
            except Exception as e:
                result = {"state": "failed", "fail_message": str(e)}
            # paused 로 인한 cancel 이면 resume 대기 후 같은 시도 그대로 다시
            if result.get("state") == "cancelled" and result.get("_paused_cancel"):
                logger.info(f"[align_retry] {ip}: pause/resume — 같은 시도 재실행")
                continue
            break
        last_result = result

        state = result.get("state", "")
        if state == "succeeded":
            if attempt > 1:
                logger.info(f"[align_retry] {ip}: 재시도 {attempt}회차에 성공")
                update_job_status(ip, message=f"랙 정렬 재시도 {attempt}회차에 성공")
            return result
        if state == "cancelled":
            return result

        fail_msg = result.get("fail_message") or state
        logger.warning(f"[align_retry] {ip}: 시도 {attempt}/{max_retries} 실패 — {fail_msg}")

        if attempt >= max_retries:
            break

        # 단계별 회복 액션
        _check_stop(ip)
        try:
            if attempt == 1:
                # 2차 시도 전: 재로컬화만
                update_job_status(ip, message="랙 인식 실패 — 위치 재보정 후 재시도")
                _interruptible_sleep(ip, 2)
                try:
                    recover_positioning(ip, max_wait_sec=10)
                except Exception:
                    pass
                _interruptible_sleep(ip, 2)
            elif attempt == 2:
                # 3차 시도 전: 0.5m 후진 + 재로컬화
                update_job_status(ip, message="랙 인식 실패 — 후진 후 재시도")
                back_x = x - ALIGN_BACKOFF_M * math.cos(ori)
                back_y = y - ALIGN_BACKOFF_M * math.sin(ori)
                try:
                    bm = create_move(ip, "standard", back_x, back_y, ori)
                    wait_move(ip, bm, timeout=60)
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.warning(f"[align_retry] 후진 실패(무시): {e}")
                _interruptible_sleep(ip, 2)
                try:
                    recover_positioning(ip, max_wait_sec=10)
                except Exception:
                    pass
                _interruptible_sleep(ip, 2)
            else:
                # 4차(마지막) 시도 전: 측면으로 우회 + 재로컬화
                update_job_status(ip, message="랙 인식 실패 — 측면 우회 후 재시도")
                # 좌측(ori + 90°) 으로 30cm 이동 후 ori 그대로 정면 복귀
                side_x = x + ALIGN_LATERAL_OFFSET_M * math.cos(ori + math.pi / 2) \
                          - ALIGN_BACKOFF_M * math.cos(ori)
                side_y = y + ALIGN_LATERAL_OFFSET_M * math.sin(ori + math.pi / 2) \
                          - ALIGN_BACKOFF_M * math.sin(ori)
                try:
                    sm = create_move(ip, "standard", side_x, side_y, ori)
                    wait_move(ip, sm, timeout=60)
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.warning(f"[align_retry] 우회 이동 실패(무시): {e}")
                _interruptible_sleep(ip, 2)
                try:
                    recover_positioning(ip, max_wait_sec=10)
                except Exception:
                    pass
                _interruptible_sleep(ip, 2)
        except RuntimeError:
            raise

    return last_result


def wait_robot_idle(ip: str, timeout: int = JACK_IDLE_TIMEOUT):
    """로봇이 idle(현재 이동 없음) 상태가 될 때까지 폴링"""
    deadline = time.time() + timeout
    time.sleep(3)  # 최소 대기
    while time.time() < deadline:
        try:
            r = requests.get(robot_url(ip, "/chassis/moves/current"), timeout=HTTP_TIMEOUT)
            if r.status_code == 404:
                # Not found = 현재 이동 없음 = idle
                return True
            data = r.json()
            state = data.get("state", "")
            if state in ("succeeded", "failed", "cancelled", ""):
                return True
        except Exception:
            pass
        time.sleep(1)
    logger.warning(f"[jack] 로봇 idle 대기 타임아웃 ({timeout}초)")
    return False


# ── 잭킹 작업 실행 ──

def run_jack_job(
    ip: str,
    pickup: dict,
    dropoff: dict,
    standby: Optional[dict] = None,
    on_status: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """잭킹 작업 동기 실행
    pickup/dropoff/standby: {"name": str, "x": float, "y": float, "ori": float}
    on_status(status, message): 상태 변경 콜백
    반환: {"status": "done"|"error", "message": str}
    """
    def _notify(status: str, message: str):
        if on_status:
            on_status(status, message)
        logger.info(f"[jack-job] {status}: {message}")

    def _fail(msg: str) -> dict:
        """에러 복구 + 에러 리턴"""
        _notify("error", msg)
        try:
            cancel_current_move(ip)
        except Exception:
            pass
        try:
            jack_down(ip)
        except Exception:
            pass
        return {"status": "error", "message": msg}

    try:
        # 1) align_with_rack (재시도 포함)
        _notify("aligning", f"픽업 위치({pickup['name']})로 랙 정렬 이동 중...")
        result = align_with_retry(ip, pickup["x"], pickup["y"], pickup.get("ori", 0))
        if result["state"] != "succeeded":
            msg = f"랙 정렬 실패: {result.get('fail_message', result['state'])}"
            _notify("error", msg)
            return _fail(msg)

        # 2) 잭 업
        _notify("jacking_up", "잭 올리는 중...")
        jack_up(ip)
        time.sleep(JACK_WAIT_SEC)

        # 3) to_unload_point
        _notify("moving_to_dropoff", f"드롭오프 위치({dropoff['name']})로 이동 중...")
        result = safe_move(ip, "to_unload_point", dropoff["x"], dropoff["y"], dropoff.get("ori", 0), timeout=120)
        if result["state"] != "succeeded":
            msg = f"드롭오프 이동 실패: {result.get('fail_message', result['state'])}"
            _notify("error", msg)
            return _fail(msg)

        # 4) 잭 다운
        _notify("jacking_down", "잭 내리는 중...")
        jack_down(ip)
        time.sleep(JACK_WAIT_SEC)

        # 5) 대기장소 복귀
        if standby:
            _notify("returning", f"대기장소({standby['name']})로 복귀 중...")
            result = safe_move(ip, "standard", standby["x"], standby["y"], standby.get("ori", 0))
            if result["state"] != "succeeded":
                msg = f"복귀 실패: {result.get('fail_message', result['state'])}"
                _notify("error", msg)
                return _fail(msg)

        summary = f"완료: {pickup['name']} → {dropoff['name']} → {standby['name'] if standby else '정지'}"
        _notify("done", summary)
        return {"status": "done", "message": summary}

    except Exception as e:
        msg = f"오류: {str(e)}"
        _notify("error", msg)
        return _fail(msg)


def run_route_job(
    ip: str,
    waypoints: list[dict],
    on_status: Optional[Callable[[str, str], None]] = None,
    manual_confirm: bool = False,
    skip_standby_pickup: bool = False,
    skip_standby_return: bool = False,
    area_id: int | None = None,
    work_mode: str = "rack_pickup",
    robot_id: int | None = None,
    start_jacked: bool = False,
    end_jacked: bool = False,
) -> dict:
    """경로 기반 작업 실행
    waypoints: [{"name", "x", "y", "ori", "waypoint_type", "poi_type", "wait_sec"}, ...]
    waypoint_type: pickup / dropoff / standby / charging
    poi_type: jack / standby / charging / general 등
    robot_id 가 전달되면 양보 후 자동 재시도용 메타가 등록됨.
    """
    # 좀비 entry 정리 — 이전 작업이 비정상 종료되어 같은 ip 에 남아있을 수 있는 전역 상태 청소.
    # (정상 작업은 finally 에서 정리되지만, 강제 종료/크래시 시 남을 수 있음)
    _stop_flags.pop(ip, None)
    _paused_flags.pop(ip, None)
    _job_status.pop(ip, None)
    _next_poi.pop(ip, None)
    _confirm_events.pop(ip, None)
    _active_route_jobs.pop(ip, None)
    # 자동 재시도용 메타데이터 등록 + zone guard 라우팅
    if robot_id is not None:
        _active_route_jobs[ip] = {
            "robot_ip": ip,
            "robot_id": robot_id,
            "waypoints": waypoints,
            "manual_confirm": manual_confirm,
            "skip_standby_pickup": skip_standby_pickup,
            "skip_standby_return": skip_standby_return,
            "area_id": area_id,
            "work_mode": work_mode,
        }
        _running_robot_id_by_ip[ip] = robot_id
    total_steps = len(waypoints)
    route_names = " → ".join(w["name"] for w in waypoints)
    # 강제 종료 분기용 work_mode 노출 (프론트가 job-status 로 읽음)
    update_job_status(ip, work_mode=work_mode)

    def _notify(status: str, message: str, step: int = 0):
        if on_status:
            on_status(status, message)
        logger.info(f"[route-job] {status}: {message}")
        # route / total_steps / started_at 은 머지로 보존 — 추가 목적지/복귀 단계에서
        # 외부 update_job_status 로 갱신한 값이 _notify 다음 호출에 덮어쓰이는 버그 방지.
        update_job_status(ip,
            status=status,
            message=message,
            current_step=step,
        )

    def _fail(msg: str) -> dict:
        """에러 복구 + 에러 리턴"""
        _notify("error", msg)
        clear_job_status(ip)
        try:
            cancel_current_move(ip)
        except Exception:
            pass
        try:
            jack_down(ip)
        except Exception:
            pass
        from app.crud.activity_log import log_activity as _log
        _log("robot", "task_error", f"작업 실패: {msg}", source="jack_service")
        return {"status": "error", "message": msg}

    jacked_up = bool(start_jacked)  # 잭 올림 상태 추적 (반복 사이클 사이 유지용)
    # start_jacked=True 면 standby 픽업 자동 skip (이미 랙 들고 있는 상태)
    if start_jacked:
        skip_standby_pickup = True
    update_job_status(ip, status="started", route=route_names, current_step=0,
                      total_steps=total_steps, started_at=time.time(), message="작업 시작")

    # 활동 로그 기록
    from app.crud.activity_log import log_activity
    log_activity("robot", "task_start", f"작업 시작: {route_names}", source="jack_service")

    _move_with_rack = "to_unload_point"

    try:
        # ── 위치 보정 (start_global_positioning + /global_positioning_state 대기) ──
        _notify("aligning", "위치 보정 중...", 0)
        if recover_positioning(ip, max_wait_sec=15):
            logger.info(f"[route-job] 위치 보정 완료 ({ip})")
        else:
            logger.warning(f"[route-job] 위치 보정 미확인 (진행 계속)")

        # ── work_mode: simple_move / delivery_no_rack 분기 ──
        if work_mode in ("simple_move", "delivery_no_rack"):
            for i, wp in enumerate(waypoints):
                name = wp["name"]
                wtype = wp["waypoint_type"]
                wait_sec = wp.get("wait_sec", 0)

                # 이동
                _notify("moving", f"[{i+1}/{total_steps}] {name} 이동 중...", i+1)
                result = safe_move(ip, "standard", wp["x"], wp["y"], wp.get("ori", 0), timeout=120)
                if result["state"] != "succeeded":
                    msg = f"{name} 이동 실패: {result.get('fail_message', '')}"
                    log_activity("robot", "move_error", msg, source="jack_service")
                    return _fail(msg)

                # delivery_no_rack: 픽업에서 잭 업, 드롭오프에서 잭 다운
                if work_mode == "delivery_no_rack":
                    if wtype == "pickup":
                        _notify("jacking_up", f"{name} 잭 올리는 중...", i+1)
                        jack_up(ip)
                        _interruptible_sleep(ip, JACK_WAIT_SEC)
                    elif wtype == "dropoff":
                        _notify("jacking_down", f"{name} 잭 내리는 중...", i+1)
                        jack_down(ip)
                        _interruptible_sleep(ip, JACK_WAIT_SEC)

                # 대기 / 확인 버튼 (마지막 포인트는 스킵 — 다음/복귀 선택으로 바로 진입)
                is_last = (i == len(waypoints) - 1)
                if manual_confirm and not is_last:
                    _notify("waiting_confirm", f"{name} 도착. 출발 버튼을 눌러주세요", i+1)
                    if not wait_for_confirm(ip):
                        return _fail("출발 확인 타임아웃")
                    _check_stop(ip)
                elif not manual_confirm and wait_sec > 0:
                    _notify("waiting", f"{name} 대기 중 ({wait_sec}초)...", i+1)
                    _interruptible_sleep(ip, wait_sec)

            # ── 마지막 포인트 후: 다음 포인트 / 복귀 선택 (수동만) ──
            if manual_confirm:
                last_wp = waypoints[-1]
                current_wp = last_wp
                while True:
                    _check_stop(ip)
                    update_job_status(ip,
                        status="waiting_next_or_return",
                        message="다음 포인트를 선택하거나 복귀 버튼을 눌러주세요",
                        route=f"{current_wp['name']} → ?",
                        current_step=0, total_steps=1,
                        started_at=_job_status.get(ip, {}).get("started_at", time.time()),
                    )
                    if on_status:
                        on_status("waiting_next_or_return", "다음 포인트를 선택하거나 복귀 버튼을 눌러주세요")
                    if not wait_for_confirm(ip):
                        return _fail("선택 타임아웃")
                    _check_stop(ip)
                    next_poi = get_next_poi(ip)
                    if next_poi is None or next_poi == "return":
                        break
                    # delivery_no_rack: 잭업 → 이동 → 잭다운 사이클 (simple_move 는 그대로 이동만)
                    is_delivery = (work_mode == "delivery_no_rack")
                    total = 3 if is_delivery else 1
                    update_job_status(ip,
                        route=f"{current_wp['name']} → {next_poi['name']}",
                        current_step=0, total_steps=total,
                    )

                    if is_delivery:
                        # 잭업 (현재 위치에서)
                        _notify("jacking_up", f"{current_wp['name']} 잭 올리는 중...", 1)
                        jack_up(ip)
                        _interruptible_sleep(ip, JACK_WAIT_SEC)

                    # 이동
                    _notify("moving", f"{next_poi['name']} 이동 중...", 2 if is_delivery else 1)
                    result = safe_move(ip, "standard", next_poi["x"], next_poi["y"], next_poi.get("ori", 0), timeout=120)
                    if result["state"] != "succeeded":
                        msg = f"{next_poi['name']} 이동 실패: {result.get('fail_message', '')}"
                        log_activity("robot", "move_error", msg, source="jack_service")
                        return _fail(msg)

                    if is_delivery:
                        # 잭다운 (도착 후)
                        _notify("jacking_down", f"{next_poi['name']} 잭 내리는 중...", 3)
                        jack_down(ip)
                        _interruptible_sleep(ip, JACK_WAIT_SEC)

                    current_wp = next_poi

            _notify("done", f"완료: {route_names}", total_steps)
            clear_job_status(ip)
            log_activity("robot", "task_complete", f"작업 완료: {route_names}", source="jack_service")
            return {"status": "done", "message": f"완료: {route_names}"}

        # ── 시작: 랙 위치 (standby POI) 에서 랙 픽업 (첫 회차만) ──
        # 랙 위치(standby) 와 작업 위치(jack) 는 별개. 충전소 → 랙 위치 → 작업 위치 → 랙 위치 → 충전소 흐름.
        standby_poi = _get_standby_poi(area_id, robot_ip=ip)
        if standby_poi and not skip_standby_pickup:
            sname = standby_poi["name"]
            _check_stop(ip)
            _notify("aligning", f"랙 위치({sname})에서 랙 픽업 중...", 0)
            result = align_with_retry(ip, standby_poi["x"], standby_poi["y"], standby_poi.get("ori", 0))
            if result["state"] == "succeeded":
                _notify("jacking_up", f"랙 위치({sname}) 잭 올리는 중...", 0)
                jack_up(ip)
                _interruptible_sleep(ip, JACK_WAIT_SEC)
                jacked_up = True
            else:
                msg = f"랙 위치({sname}) 랙 픽업 실패: {result.get('fail_message', '')}"
                return _fail(msg)

        for i, wp in enumerate(waypoints):
            name = wp["name"]
            wtype = wp["waypoint_type"]
            ptype = wp.get("poi_type", "general")
            wait_sec = wp.get("wait_sec", 0)

            if wtype == "pickup":
                if jacked_up:
                    # 잭 올린 상태 → 픽업 위치로 이동 → 잭 다운 → 물건 올림 → 잭 업
                    _notify("moving_to_dropoff", f"[{i+1}/{total_steps}] {name} 랙 배달 중...", i+1)
                    result = safe_move(ip, _move_with_rack, wp["x"], wp["y"], wp.get("ori", 0), timeout=120)
                    if result["state"] != "succeeded":
                        msg = f"{name} 이동 실패: {result.get('fail_message', '')}"
                        log_activity("robot", "move_error", msg, source="jack_service")
                        return _fail(msg)

                    _notify("jacking_down", f"{name} 잭 내리는 중...", i+1)
                    jack_down(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)
                    jacked_up = False

                    # 수동: 출발 버튼 누르면 자동으로 잭 업 → 출발 / 자동: wait_sec 대기
                    if manual_confirm:
                        _notify("waiting_confirm", f"{name} 물건 적재 후 출발 버튼을 눌러주세요", i+1)
                        if not wait_for_confirm(ip):
                            return _fail("출발 확인 타임아웃 (5분)")
                        _check_stop(ip)
                    elif wait_sec > 0:
                        _notify("waiting", f"{name} 대기 중 ({wait_sec}초)...", i+1)
                        _interruptible_sleep(ip, wait_sec)

                    # 잭 업 → 바로 출발 (출발 대기 없음)
                    _notify("aligning", f"{name} 랙 재정렬 중...", i+1)
                    result = align_with_retry(ip, wp["x"], wp["y"], wp.get("ori", 0))
                    if result["state"] != "succeeded":
                        msg = f"{name} 랙 재정렬 실패: {result.get('fail_message', '')}"
                        log_activity("robot", "move_error", msg, source="jack_service")
                        return _fail(msg)

                    _notify("jacking_up", f"{name} 잭 올리는 중...", i+1)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)
                    jacked_up = True
                else:
                    # 잭이 내려간 상태 → align_with_rack로 랙 픽업
                    _notify("aligning", f"[{i+1}/{total_steps}] {name} 랙 정렬 이동 중...", i+1)
                    result = align_with_retry(ip, wp["x"], wp["y"], wp.get("ori", 0))
                    if result["state"] != "succeeded":
                        msg = f"{name} 랙 정렬 실패: {result.get('fail_message', '')}"
                        log_activity("robot", "move_error", msg, source="jack_service")
                        return _fail(msg)

                    _notify("jacking_up", f"{name} 잭 올리는 중...", i+1)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)
                    jacked_up = True

                if wait_sec > 0:
                    _notify("waiting", f"{name} 대기 중 ({wait_sec}초)...", i+1)
                    _interruptible_sleep(ip, wait_sec)

            elif wtype == "dropoff":
                # 드롭오프: 이동 → jack_down → 대기 → (마지막이고 end_jacked=False 가 아니면) align + jack_up
                _notify("moving_to_dropoff", f"[{i+1}/{total_steps}] {name} 드롭오프 이동 중...", i+1)
                result = safe_move(ip, _move_with_rack, wp["x"], wp["y"], wp.get("ori", 0), timeout=120)
                if result["state"] != "succeeded":
                    msg = f"{name} 드롭오프 이동 실패: {result.get('fail_message', '')}"
                    log_activity("robot", "move_error", msg, source="jack_service")
                    return _fail(msg)

                _notify("jacking_down", f"{name} 잭 내리는 중...", i+1)
                jack_down(ip)
                _interruptible_sleep(ip, JACK_WAIT_SEC)
                jacked_up = False

                if wait_sec > 0:
                    _notify("waiting", f"{name} 대기 중 ({wait_sec}초)...", i+1)
                    _interruptible_sleep(ip, wait_sec)

                # 다음 waypoint 가 있거나, 마지막이지만 end_jacked=True (다음 회차에서 잭업 상태 필요) → 다시 잭업
                is_last = (i == len(waypoints) - 1)
                need_jack_up_again = (not is_last) or end_jacked
                if need_jack_up_again:
                    _notify("aligning", f"{name} 다음 단계 위해 랙 재정렬 중...", i+1)
                    align_result = align_with_retry(ip, wp["x"], wp["y"], wp.get("ori", 0))
                    if align_result["state"] != "succeeded":
                        msg = f"{name} 재정렬 실패: {align_result.get('fail_message', '')}"
                        log_activity("robot", "move_error", msg, source="jack_service")
                        return _fail(msg)
                    _notify("jacking_up", f"{name} 잭 다시 올리는 중...", i+1)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)
                    jacked_up = True

            elif ptype == "charging" or wtype == "charging":
                # 충전소: 사전 접근 POI("<charger_name>-1") 가 있으면 그쪽으로 먼저 이동 후 charge.
                # 없으면 충전소 POI 좌표 그대로 standard → charge.
                _notify("charging", f"[{i+1}/{total_steps}] {name} 충전소 접근 중...", i+1)
                cx, cy = wp["x"], wp["y"]
                cyaw = wp.get("ori", 0)
                # 사전 접근 POI 검색 (같은 영역의 활성 맵에서 이름 매칭)
                _ap = None
                _db_ap = None
                try:
                    from app.database import SessionLocal as _SL
                    from app.models.map import MapPOI as _MP, RobotMap as _RM
                    from app.models.robot import Robot as _RB
                    _db_ap = _SL()
                    _r = _db_ap.query(_RB).filter(_RB.ip_address == ip).first()
                    _map_q = None
                    if _r and _r.area_id:
                        _map_q = _db_ap.query(_RM).filter(
                            _RM.area_id == int(_r.area_id), _RM.is_active == True
                        ).order_by(_RM.id.desc()).first()
                    if _map_q:
                        _apoi = _db_ap.query(_MP).filter(
                            _MP.map_id == _map_q.id,
                            _MP.name == f"{name}-1",
                            _MP.is_active == True,
                        ).first()
                        if _apoi and _apoi.world_x is not None:
                            _ap = {
                                "name": _apoi.name,
                                "x": _apoi.world_x,
                                "y": _apoi.world_y,
                                "ori": _apoi.angle if _apoi.angle is not None else cyaw,
                            }
                except Exception as e:
                    logger.warning(f"[charge-approach] {ip} 사전 접근 POI 조회 예외: {e}")
                finally:
                    if _db_ap is not None:
                        _db_ap.close()

                if _ap:
                    sx, sy, syaw = _ap["x"], _ap["y"], _ap["ori"]
                    _label = f"사전 접근({_ap['name']})"
                else:
                    sx, sy, syaw = cx, cy, cyaw
                    _label = "사전 접근"

                # 1단계: 사전 접근 — best-effort. 실패해도 charge 단계로 진행.
                try:
                    _std_id = create_move(ip, "standard", sx, sy, syaw)
                    _std_res = wait_move(ip, _std_id, timeout=60)
                    if _std_res.get("state") != "succeeded":
                        logger.warning(f"[charge-approach] {ip} {_label} 실패({_std_res.get('fail_message')}) — 직접 도킹")
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.warning(f"[charge-approach] {ip} {_label} 예외: {e} — 직접 도킹")
                time.sleep(2)
                # 2단계: 도킹
                _notify("charging", f"[{i+1}/{total_steps}] {name} 충전소 도킹 중...", i+1)
                move_id = create_move(ip, "charge", cx, cy, cyaw, charge_retry_count=3)
                result = wait_move(ip, move_id, timeout=120)
                if result["state"] != "succeeded":
                    msg = f"{name} 충전 도킹 실패: {result.get('fail_message', '')}"
                    log_activity("robot", "dock_error", msg, source="jack_service")
                    return _fail(msg)

                if wait_sec > 0:
                    _notify("waiting", f"{name} 대기 중 ({wait_sec}초)...", i+1)
                    _interruptible_sleep(ip, wait_sec)

            else:
                # 대기/일반: standard 이동
                _notify("moving", f"[{i+1}/{total_steps}] {name} 이동 중...", i+1)
                result = safe_move(ip, "standard", wp["x"], wp["y"], wp.get("ori", 0), timeout=120)
                if result["state"] != "succeeded":
                    msg = f"{name} 이동 실패: {result.get('fail_message', '')}"
                    log_activity("robot", "move_error", msg, source="jack_service")
                    return _fail(msg)

                if wait_sec > 0:
                    _notify("waiting", f"{name} 대기 중 ({wait_sec}초)...", i+1)
                    _interruptible_sleep(ip, wait_sec)

        # 마지막 드롭오프 후: 다음 포인트 / 복귀 선택 루프 (수동) 또는 바로 복귀 (자동)
        # 복귀 대상은 랙 위치 (standby POI).
        if jacked_up is False and not skip_standby_return:
            _check_stop(ip)
            standby_poi = _get_standby_poi(area_id, robot_ip=ip)
            last_dropoff_wp = None
            for wp in reversed(waypoints):
                if wp["waypoint_type"] == "dropoff":
                    last_dropoff_wp = wp
                    break

            # ── 수동: 다음 포인트 / 복귀 루프 ──
            if manual_confirm and standby_poi and last_dropoff_wp:
                current_wp = last_dropoff_wp
                extra_step = 0
                while True:
                    _check_stop(ip)
                    update_job_status(ip,
                        status="waiting_next_or_return",
                        message="다음 포인트를 선택하거나 복귀 버튼을 눌러주세요",
                        route=f"{current_wp['name']} → ?",
                        current_step=0, total_steps=1,
                        started_at=_job_status.get(ip, {}).get("started_at", time.time()),
                    )
                    if on_status:
                        on_status("waiting_next_or_return", "다음 포인트를 선택하거나 복귀 버튼을 눌러주세요")
                    if not wait_for_confirm(ip):
                        return _fail("선택 타임아웃")
                    _check_stop(ip)

                    next_poi = get_next_poi(ip)
                    if next_poi is None or next_poi == "return":
                        break

                    # 경로 업데이트
                    extra_step += 1
                    new_route = f"{current_wp['name']} → {next_poi['name']}"
                    update_job_status(ip, route=new_route, current_step=0, total_steps=2)

                    # 현재 위치에서 잭 업 → 다음 포인트 → 잭 다운
                    update_job_status(ip, status="aligning", message=f"{current_wp['name']} 랙 재정렬 중...", current_step=0)
                    _notify("aligning", f"{current_wp['name']} 랙 재정렬 중...", 0)
                    result = align_with_retry(ip, current_wp["x"], current_wp["y"], current_wp.get("ori", 0))
                    if result["state"] != "succeeded":
                        msg = f"랙 재정렬 실패: {result.get('fail_message', '')}"
                        log_activity("robot", "move_error", msg, source="jack_service")
                        return _fail(msg)

                    update_job_status(ip, status="jacking_up", message="잭 올리는 중...", current_step=1)
                    _notify("jacking_up", "잭 올리는 중...", 1)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

                    update_job_status(ip, status="moving_to_dropoff", message=f"{next_poi['name']} 이동 중...", current_step=1)
                    _notify("moving_to_dropoff", f"{next_poi['name']} 이동 중...", 1)
                    result = safe_move(ip, _move_with_rack, next_poi["x"], next_poi["y"], next_poi.get("ori", 0), timeout=120)
                    if result["state"] != "succeeded":
                        msg = f"{next_poi['name']} 이동 실패: {result.get('fail_message', '')}"
                        log_activity("robot", "move_error", msg, source="jack_service")
                        return _fail(msg)

                    update_job_status(ip, status="jacking_down", message=f"{next_poi['name']} 잭 내리는 중...", current_step=2)
                    _notify("jacking_down", f"{next_poi['name']} 잭 내리는 중...", 2)
                    jack_down(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)
                    current_wp = next_poi
                    log_activity("robot", "dropoff", f"드롭오프: {next_poi['name']}", source="jack_service")

                # 복귀
                sname = standby_poi["name"]
                update_job_status(ip, route=f"{current_wp['name']} → {sname}", current_step=0, total_steps=2)
                _notify("aligning", f"랙 위치로 복귀 위해 재정렬 중...", 0)
                result = align_with_retry(ip, current_wp["x"], current_wp["y"], current_wp.get("ori", 0))
                if result["state"] == "succeeded":
                    _notify("jacking_up", "랙 위치로 복귀 위해 잭 올리는 중...", total_steps)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

                    _notify("moving", f"랙 위치({sname})로 복귀 중...", total_steps)
                    safe_move(ip, _move_with_rack, standby_poi["x"], standby_poi["y"], standby_poi.get("ori", 0), timeout=120)

                    _notify("jacking_down", f"랙 위치({sname}) 잭 내리는 중...", total_steps)
                    jack_down(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

            # ── 자동: 바로 복귀 ──
            elif standby_poi and last_dropoff_wp:
                sname = standby_poi["name"]
                _notify("aligning", f"랙 위치로 복귀 위해 재정렬 중...", total_steps)
                result = align_with_retry(ip, last_dropoff_wp["x"], last_dropoff_wp["y"], last_dropoff_wp.get("ori", 0))
                if result["state"] == "succeeded":
                    _notify("jacking_up", "랙 위치로 복귀 위해 잭 올리는 중...", total_steps)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

                    _notify("moving", f"랙 위치({sname})로 복귀 중...", total_steps)
                    safe_move(ip, _move_with_rack, standby_poi["x"], standby_poi["y"], standby_poi.get("ori", 0), timeout=120)

                    _notify("jacking_down", f"랙 위치({sname}) 잭 내리는 중...", total_steps)
                    jack_down(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

        _notify("done", f"완료: {route_names}", total_steps)
        clear_job_status(ip)
        log_activity("robot", "task_complete", f"작업 완료: {route_names}", source="jack_service")
        return {"status": "done", "message": f"완료: {route_names}"}

    except Exception as e:
        return _fail(f"오류: {str(e)}")
    finally:
        _active_route_jobs.pop(ip, None)
        _running_robot_id_by_ip.pop(ip, None)
        if robot_id is not None:
            try:
                from app.services import zone_lock as _zl
                _zl.release_all_by_robot(robot_id)
            except Exception:
                pass
