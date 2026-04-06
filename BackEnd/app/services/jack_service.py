"""
잭킹 작업 공용 서비스
- 로봇 REST API 헬퍼 함수
- 잭킹 흐름 실행 (align_with_rack → jack_up → to_unload_point → jack_down → standby)
"""
import logging
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

# 실행 중인 작업 상태 (robot_ip → job info)
_job_status: dict[str, dict] = {}

# 수동 확인 대기 (robot_ip → threading.Event)
import threading as _threading
_confirm_events: dict[str, _threading.Event] = {}

# 다음 포인트 (robot_ip → poi dict or "return")
_next_poi: dict[str, dict | str | None] = {}


def _get_standby_poi() -> dict | None:
    """DB에서 현재 활성 맵의 standby POI(W1) 조회"""
    from app.database import SessionLocal
    from app.models.map import MapPOI, RobotMap
    db = SessionLocal()
    try:
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


def wait_for_confirm(robot_ip: str, timeout: int | None = None) -> bool:
    """사용자 확인 버튼을 기다림. True=확인됨, False=타임아웃. timeout=None이면 무한 대기"""
    evt = _threading.Event()
    _confirm_events[robot_ip] = evt
    result = evt.wait(timeout=timeout)
    _confirm_events.pop(robot_ip, None)
    return result


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
    """특정 로봇의 진행 중인 작업에 중지 플래그 설정 + 상태 제거"""
    _stop_flags[robot_ip] = True
    _job_status.pop(robot_ip, None)
    _next_poi.pop(robot_ip, None)
    logger.info(f"[jack_service] stop flag set for {robot_ip}")


def _check_stop(robot_ip: str):
    """중지 플래그 확인 — True면 예외 발생"""
    if _stop_flags.get(robot_ip):
        _stop_flags.pop(robot_ip, None)
        raise RuntimeError(f"작업 중지됨 (robot={robot_ip})")


def _interruptible_sleep(robot_ip: str, seconds: float):
    """중지 가능한 대기 — 1초 간격으로 stop 플래그 체크"""
    elapsed = 0.0
    while elapsed < seconds:
        _check_stop(robot_ip)
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


def wait_move(ip: str, move_id: int, timeout: int = MOVE_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_stop(ip)
        resp = robot_get(ip, f"/chassis/moves/{move_id}")
        state = resp.get("state", "")
        if state in ("succeeded", "failed", "cancelled"):
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
def align_with_retry(ip: str, x: float, y: float, ori: float = 0) -> dict:
    """align_with_rack 1회 시도"""
    _check_stop(ip)
    move_id = create_move(ip, "align_with_rack", x, y, ori)
    return wait_move(ip, move_id, timeout=120)


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
        move_id = create_move(ip, "to_unload_point", dropoff["x"], dropoff["y"], dropoff.get("ori", 0))
        result = wait_move(ip, move_id, timeout=120)
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
            move_id = create_move(ip, "standard", standby["x"], standby["y"], standby.get("ori", 0))
            result = wait_move(ip, move_id)
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
    is_main_floor: bool = True,
) -> dict:
    """경로 기반 작업 실행
    waypoints: [{"name", "x", "y", "ori", "waypoint_type", "poi_type", "wait_sec"}, ...]
    waypoint_type: pickup / dropoff / standby / charging
    poi_type: jack / standby / charging / general 등
    """
    total_steps = len(waypoints)
    route_names = " → ".join(w["name"] for w in waypoints)

    def _notify(status: str, message: str, step: int = 0):
        if on_status:
            on_status(status, message)
        logger.info(f"[route-job] {status}: {message}")
        update_job_status(ip,
            status=status,
            message=message,
            route=route_names,
            current_step=step,
            total_steps=total_steps,
            started_at=_job_status.get(ip, {}).get("started_at", time.time()),
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

    jacked_up = False  # 잭 올림 상태 추적
    update_job_status(ip, status="started", route=route_names, current_step=0,
                      total_steps=total_steps, started_at=time.time(), message="작업 시작")

    # 활동 로그 기록
    from app.crud.activity_log import log_activity
    log_activity("robot", "task_start", f"작업 시작: {route_names}", source="jack_service")

    _move_with_rack = "to_unload_point"

    try:
        # ── 위치 보정 (충전소에서 출발 시 SLAM 매칭 불량 방지) ──
        try:
            _notify("aligning", "위치 보정 중...", 0)
            requests.post(f"http://{ip}:{ROBOT_PORT}/services/start_global_positioning",
                          json={}, timeout=5)
            # 위치 보정 완료 대기 (최대 5초, 매칭되면 즉시 종료)
            for _ in range(5):
                _check_stop(ip)
                time.sleep(1)
                try:
                    import websocket as _ws, json as _json
                    ws = _ws.create_connection(f"ws://{ip}:{ROBOT_PORT}/ws/v2/topics", timeout=2)
                    deadline = time.time() + 1
                    while time.time() < deadline:
                        raw = ws.recv()
                        pkt = _json.loads(raw)
                        if pkt.get("topic") == "/slam/state" and pkt.get("lidar_matched"):
                            ws.close()
                            raise StopIteration
                    ws.close()
                except StopIteration:
                    break
                except Exception:
                    pass
            logger.info(f"[route-job] 위치 보정 완료 ({ip})")
        except Exception as e:
            logger.warning(f"[route-job] 위치 보정 실패 (무시): {e}")

        # ── 시작: 대기장소(W1)에서 랙 픽업 (첫 회차만) ──
        standby_poi = _get_standby_poi()
        if standby_poi and not skip_standby_pickup:
            sname = standby_poi["name"]
            _check_stop(ip)
            _notify("aligning", f"대기장소({sname})에서 랙 픽업 중...", 0)
            result = align_with_retry(ip, standby_poi["x"], standby_poi["y"], standby_poi.get("ori", 0))
            if result["state"] == "succeeded":
                _notify("jacking_up", f"대기장소({sname}) 잭 올리는 중...", 0)
                jack_up(ip)
                _interruptible_sleep(ip, JACK_WAIT_SEC)
                jacked_up = True
            else:
                msg = f"대기장소({sname}) 랙 픽업 실패: {result.get('fail_message', '')}"
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
                    move_id = create_move(ip, _move_with_rack, wp["x"], wp["y"], wp.get("ori", 0))
                    result = wait_move(ip, move_id, timeout=120)
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
                # 드롭오프: 이동 → jack_down
                _notify("moving_to_dropoff", f"[{i+1}/{total_steps}] {name} 드롭오프 이동 중...", i+1)
                move_id = create_move(ip, _move_with_rack, wp["x"], wp["y"], wp.get("ori", 0))
                result = wait_move(ip, move_id, timeout=120)
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

            elif ptype == "charging" or wtype == "charging":
                # 충전소: 도킹포인트 좌표로 charge 명령
                _notify("charging", f"[{i+1}/{total_steps}] {name} 충전소 도킹 중...", i+1)
                cx, cy = wp["x"], wp["y"]
                cyaw = wp.get("ori", 0)
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
                move_id = create_move(ip, "standard", wp["x"], wp["y"], wp.get("ori", 0))
                result = wait_move(ip, move_id, timeout=120)
                if result["state"] != "succeeded":
                    msg = f"{name} 이동 실패: {result.get('fail_message', '')}"
                    log_activity("robot", "move_error", msg, source="jack_service")
                    return _fail(msg)

                if wait_sec > 0:
                    _notify("waiting", f"{name} 대기 중 ({wait_sec}초)...", i+1)
                    _interruptible_sleep(ip, wait_sec)

        # 마지막 드롭오프 후: 다음 포인트 / 복귀 선택 루프 (수동) 또는 바로 복귀 (자동)
        # 다른층: jacked_up이 True 상태지만 루프 진입 필요
        if jacked_up is False and not skip_standby_return:
            _check_stop(ip)
            standby_poi = _get_standby_poi()
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
                    move_id = create_move(ip, _move_with_rack, next_poi["x"], next_poi["y"], next_poi.get("ori", 0))
                    result = wait_move(ip, move_id, timeout=120)
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
                _notify("aligning", f"대기장소 이동을 위해 랙 재정렬 중...", 0)
                result = align_with_retry(ip, current_wp["x"], current_wp["y"], current_wp.get("ori", 0))
                if result["state"] == "succeeded":
                    _notify("jacking_up", "대기장소 이동을 위해 잭 올리는 중...", total_steps)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

                    _notify("moving", f"대기장소({sname})로 이동 중...", total_steps)
                    move_id = create_move(ip, _move_with_rack, standby_poi["x"], standby_poi["y"], standby_poi.get("ori", 0))
                    wait_move(ip, move_id, timeout=120)

                    _notify("jacking_down", f"대기장소({sname}) 잭 내리는 중...", total_steps)
                    jack_down(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

            # ── 자동: 바로 복귀 ──
            elif standby_poi and last_dropoff_wp:
                sname = standby_poi["name"]
                _notify("aligning", f"대기장소 이동을 위해 랙 재정렬 중...", total_steps)
                result = align_with_retry(ip, last_dropoff_wp["x"], last_dropoff_wp["y"], last_dropoff_wp.get("ori", 0))
                if result["state"] == "succeeded":
                    _notify("jacking_up", "대기장소 이동을 위해 잭 올리는 중...", total_steps)
                    jack_up(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

                    _notify("moving", f"대기장소({sname})로 이동 중...", total_steps)
                    move_id = create_move(ip, _move_with_rack, standby_poi["x"], standby_poi["y"], standby_poi.get("ori", 0))
                    wait_move(ip, move_id, timeout=120)

                    _notify("jacking_down", f"대기장소({sname}) 잭 내리는 중...", total_steps)
                    jack_down(ip)
                    _interruptible_sleep(ip, JACK_WAIT_SEC)

        _notify("done", f"완료: {route_names}", total_steps)
        clear_job_status(ip)
        log_activity("robot", "task_complete", f"작업 완료: {route_names}", source="jack_service")
        return {"status": "done", "message": f"완료: {route_names}"}

    except Exception as e:
        return _fail(f"오류: {str(e)}")
