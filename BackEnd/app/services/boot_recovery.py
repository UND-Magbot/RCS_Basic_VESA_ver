"""로봇 부팅(재부팅) 후 자동 복구 모니터.

배경: 로봇 부팅 시간이 30초~1분으로 길어지면서, 부팅 중/직후에는 current-map 이
안 잡히고 SLAM 위치가 리셋된다. 로봇은 **항상 충전소에 도킹된 상태로 부팅**하므로,
로봇이 오프라인 → 온라인(부팅 완료)으로 전환되면 자동으로:
  1) current-map 을 해당 area 활성 맵으로 설정 (안 잡혀 있거나 다른 맵일 때만)
  2) 충전소(charging POI) 기준으로 위치재조정(relocalize)
을 수행한다.

안전장치:
  - 활성 배차 워커가 있는 로봇(작업 중이라 도킹 위치가 아닐 수 있음)은 제외.
  - 백그라운드 시작 시점의 상태는 기준선으로만 기록 — 이미 켜져 있던 로봇은
    건드리지 않고, 실제로 오프라인→온라인 전환이 관측된 경우에만 복구한다.
"""
import logging
import threading

logger = logging.getLogger(__name__)

POLL_SEC = 10        # 온라인 상태 점검 주기
STABILIZE_SEC = 8    # 온라인 감지 후 SLAM 안정화 대기 (부팅 직후 API 는 떠도 SLAM 준비 전일 수 있음)

_prev_online: dict[int, bool] = {}
_recovering: set[int] = set()
_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    from app.services.thread_utils import safe_thread
    _thread = safe_thread(target=_loop, name="boot-recovery")
    _thread.start()
    logger.info("[boot_recovery] 시작 (부팅 후 맵/위치 자동 복구)")


def stop() -> None:
    _stop.set()


def _loop() -> None:
    while not _stop.is_set():
        try:
            _check_all()
        except Exception:
            logger.exception("[boot_recovery] 점검 오류")
        _stop.wait(POLL_SEC)


def _check_all() -> None:
    from app.database import SessionLocal
    from app.models.robot import Robot
    from app.services import dispatch_service

    db = SessionLocal()
    try:
        robots = db.query(Robot).filter(
            Robot.is_active == True, Robot.ip_address != None
        ).all()
        # 필요한 값만 뽑아둔다 (세션 닫은 뒤 접근 방지)
        infos = [(r.id, r.ip_address) for r in robots]
    finally:
        db.close()
    if not infos:
        return

    ips = [ip for _, ip in infos]
    try:
        online_ips = dispatch_service.online_ips_cached(ips)
    except Exception:
        return  # 라이브 체크 자체가 실패하면 이번 주기 건너뜀

    for robot_id, ip in infos:
        online = ip in online_ips
        with _lock:
            was = _prev_online.get(robot_id)
            _prev_online[robot_id] = online
            already = robot_id in _recovering

        # 오프라인 → 온라인 전환 = 부팅 완료 신호
        if online and was is False and not already:
            # 활성 배차 중이면 도킹 위치가 아닐 수 있으므로 자동 복구 제외
            if dispatch_service.has_active_worker(robot_id):
                continue
            with _lock:
                _recovering.add(robot_id)
            from app.services.thread_utils import safe_thread
            safe_thread(target=_recover_robot, args=(robot_id,),
                        name=f"boot-recover-{robot_id}").start()
            logger.info(f"[boot_recovery] 부팅 완료 감지 → 복구 시작 (robot_id={robot_id}, {ip})")


def _recover_robot(robot_id: int) -> None:
    import math
    import time
    from app.database import SessionLocal
    from app.models.robot import Robot
    from app.models.map import RobotMap, MapPOI
    from app.robot_api.robot_map_service import get_current_map, set_current_map, set_chassis_pose

    try:
        time.sleep(STABILIZE_SEC)  # SLAM 안정화 대기

        from app.routers.map import _find_secret, _correct_map_grid_origin, DOCKING_OFFSET

        db = SessionLocal()
        pose = None
        ip = secret = None
        try:
            robot = db.query(Robot).filter(Robot.id == robot_id).first()
            if not robot or not robot.ip_address:
                return
            ip = robot.ip_address
            try:
                secret = _find_secret(ip)
            except Exception:
                from app.routers.robot import DEFAULT_SECRET
                secret = DEFAULT_SECRET

            # 1) current-map 설정 — 안 잡혀 있거나 다른 맵일 때만.
            #    (재선택은 포즈를 리셋하므로, 이미 맞으면 건드리지 않는다)
            target_map_id = None
            if robot.area_id:
                rm = (db.query(RobotMap)
                      .filter(RobotMap.area_id == int(robot.area_id),
                              RobotMap.is_active == True)
                      .order_by(RobotMap.id.desc()).first())
                if rm and rm.robot_map_id:
                    target_map_id = rm.robot_map_id
            if target_map_id is not None:
                try:
                    cur = get_current_map(ip, secret)
                    cur_id = cur.get("id") if isinstance(cur, dict) else None
                except Exception:
                    cur_id = None
                if cur_id != target_map_id:
                    try:
                        set_current_map(ip, secret, {"map_id": target_map_id})
                        logger.info(f"[boot_recovery] current-map 설정 {ip}: {cur_id} → {target_map_id}")
                        time.sleep(2.0)  # 맵 로드 대기
                    except Exception as e:
                        logger.warning(f"[boot_recovery] current-map 설정 실패 {ip}: {e}")

            # (2단계 위치재조정은 db 를 닫은 뒤 공용 함수로 처리)
        finally:
            db.close()

        # 2) 충전소 기준 위치재조정 (부팅/동기화 공용 함수)
        relocalize_robot_to_dock(robot_id)
    except Exception:
        logger.exception(f"[boot_recovery] 복구 오류 robot_id={robot_id}")
    finally:
        with _lock:
            _recovering.discard(robot_id)


def relocalize_robot_to_dock(robot_id: int) -> bool:
    """로봇을 충전소(charging POI, 없으면 standby) 기준 위치로 재조정. 성공 시 True.

    로봇이 실제로 그 위치(도킹)에 있어야 유효한 보정이다 — 호출자가 유휴/도킹 상태를
    판단해서 호출한다(부팅 직후, 또는 동기화 시 유휴 로봇).
    """
    import math
    import time
    from app.database import SessionLocal
    from app.models.robot import Robot
    from app.models.map import MapPOI
    from app.robot_api.robot_map_service import set_chassis_pose

    db = SessionLocal()
    ip = secret = None
    pose = None
    try:
        from app.routers.map import _find_secret, _correct_map_grid_origin, DOCKING_OFFSET
        robot = db.query(Robot).filter(Robot.id == robot_id).first()
        if not robot or not robot.ip_address:
            return False
        ip = robot.ip_address
        try:
            secret = _find_secret(ip)
        except Exception:
            from app.routers.robot import DEFAULT_SECRET
            secret = DEFAULT_SECRET

        poi = None
        use_dock = False
        if robot.charging_id:
            poi = db.query(MapPOI).filter(
                MapPOI.id == robot.charging_id, MapPOI.is_active == True).first()
            use_dock = True
        if not poi and robot.standby_id:
            poi = db.query(MapPOI).filter(
                MapPOI.id == robot.standby_id, MapPOI.is_active == True).first()
            use_dock = False
        if not poi:
            logger.warning(f"[relocalize] {ip}: 충전소/대기장소 POI 미지정 — 위치재조정 생략")
            return False

        # POI 가 속한 맵 grid_origin 보정 (world 좌표 정합성)
        if poi.map_id:
            try:
                _correct_map_grid_origin(db, poi.map_id)
            except Exception:
                pass
        if poi.world_x is None or poi.world_y is None:
            logger.warning(f"[relocalize] {ip}: POI '{poi.name}' 월드 좌표 없음 — 위치재조정 생략")
            return False

        yaw = poi.angle if poi.angle is not None else 0.0
        if use_dock:
            # 충전소: yaw 방향 DOCKING_OFFSET 앞, 헤딩은 충전소 POI 방향 그대로(180° 뒤집지 않음)
            tx = poi.world_x + DOCKING_OFFSET * math.cos(yaw)
            ty = poi.world_y + DOCKING_OFFSET * math.sin(yaw)
            tyaw = yaw
        else:
            tx, ty, tyaw = poi.world_x, poi.world_y, yaw
        pose = {"position": [tx, ty, 0], "ori": tyaw}
    finally:
        db.close()

    if not pose or not ip:
        return False

    # set_chassis_pose 재시도 (부팅 직후/맵 로드 직후 SLAM 준비 지연 대비)
    for attempt in range(3):
        try:
            set_chassis_pose(ip, secret, pose)
            logger.info(f"[relocalize] 위치재조정 완료 {ip}: "
                        f"pos=({pose['position'][0]:.2f},{pose['position'][1]:.2f}) "
                        f"ori={pose['ori']:.2f}")
            return True
        except Exception as e:
            logger.warning(f"[relocalize] 위치재조정 실패 {ip} 시도 {attempt+1}/3: {e}")
            time.sleep(2)
    return False
