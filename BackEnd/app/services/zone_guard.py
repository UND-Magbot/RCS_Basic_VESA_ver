"""
Zone Guard — create_move 직전에 호출해 이동 경로가 zone(좁은 양방향 통로 등)을
지나갈 경우 사전 락을 획득. 다른 로봇이 그 zone 을 점유 중이면 대기.

설계: run_route_job 의 매 이동 직전 호출. 이동 완료 후 release 는 호출자가
release_all_by_robot 으로 한꺼번에 처리(zone_lock).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from app.services import zone_lock
from app.services.geometry import (
    load_zone_polygons,
    zones_crossed_by_segment,
    zones_containing_point,
)

logger = logging.getLogger(__name__)

ROBOT_PORT = 8090
HTTP_TIMEOUT = 10  # LTE 지연 대응 (pose 조회 실패 시 통로 보호가 약해지므로 넉넉히)
WAIT_FOR_ZONE_TIMEOUT_SEC = 60.0  # zone 점유 해제 대기 한계
WAIT_POLL_SEC = 1.0


def _fetch_pose(robot_ip: str) -> tuple[float, float] | None:
    """로봇 현재 월드 좌표 (x, y). 실패 시 None."""
    try:
        r = requests.get(f"http://{robot_ip}:{ROBOT_PORT}/chassis/pose", timeout=HTTP_TIMEOUT)
        d = r.json() if r.ok else {}
        # AutoXing 응답 포맷 가변 — 흔한 후보들 시도
        if isinstance(d, dict):
            if "x" in d and "y" in d:
                return float(d["x"]), float(d["y"])
            pose = d.get("pose") or {}
            if "x" in pose and "y" in pose:
                return float(pose["x"]), float(pose["y"])
            pos = pose.get("pos") if isinstance(pose, dict) else None
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                return float(pos[0]), float(pos[1])
    except Exception as e:
        logger.debug(f"[zone_guard] pose fetch failed ({robot_ip}): {e}")
    return None


def _active_map_id(db, robot_ip: str) -> int | None:
    from app.models.robot import Robot
    from app.models.map import RobotMap
    robot = db.query(Robot).filter(Robot.ip_address == robot_ip).first()
    if not robot or not robot.area_id:
        return None
    rm = db.query(RobotMap).filter(
        RobotMap.area_id == robot.area_id, RobotMap.is_active == True
    ).order_by(RobotMap.id.desc()).first()
    return rm.id if rm else None


def acquire_zones_for_move(robot_ip: str, robot_id: int | None,
                           target_x: float, target_y: float,
                           is_stop_fn: Optional[callable] = None) -> tuple[bool, list[int]]:
    """이동 경로가 지나갈 zone 들을 사전에 락. 다른 로봇 점유 시 대기.

    반환: (proceed, zone_ids)
      - proceed=True  : 이동해도 됨 (지나갈 zone 없음 → []  또는 모두 획득 → zone_ids)
      - proceed=False : 이동하면 안 됨 (중지 요청 또는 점유 해제 대기 타임아웃)

    robot_id 가 None 이면 no-op → (True, []).
    is_stop_fn: 작업 중지 콜백 (True 반환 시 (False, []) 즉시 반환).

    핵심: 점유를 못 잡았을 때 '강제 진행'하지 않는다. 못 잡으면 (False, [])를 돌려
    호출자가 이동을 보류·재시도하게 해, 좁은 통로에서의 정면 교착을 방지한다.
    """
    if robot_id is None:
        return True, []

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        map_id = _active_map_id(db, robot_ip)
        if map_id is None:
            return True, []
        zones = load_zone_polygons(db, map_id)
        if not zones:
            return True, []
    finally:
        db.close()

    pose = _fetch_pose(robot_ip)
    if pose is None:
        # pose 못 가져오면 안전하게 모든 활성 zone 중 target 만 포함하는 것으로 폴백
        crossed = zones_containing_point(zones, target_x, target_y)
    else:
        crossed = zones_crossed_by_segment(zones, pose, (target_x, target_y))

    if not crossed:
        return True, []

    # 모두 잡을 때까지 대기 (한 zone 만 점유돼도 retry)
    deadline = time.time() + WAIT_FOR_ZONE_TIMEOUT_SEC
    logged_wait = False
    while True:
        if is_stop_fn and is_stop_fn():
            return False, []
        ok, conflict = zone_lock.try_acquire(crossed, robot_id)
        if ok:
            if logged_wait:
                logger.info(f"[zone_guard] robot {robot_id} zone 진입 가능 → 락 획득 {crossed}")
            return True, crossed
        if not logged_wait:
            logger.info(f"[zone_guard] robot {robot_id} zone {conflict} 점유 대기 중...")
            logged_wait = True
        if time.time() > deadline:
            # 강제 진행하지 않음 — 이동 보류 신호. 호출자가 재시도하며 통로가 빌 때까지 대기.
            logger.warning(f"[zone_guard] robot {robot_id} zone {conflict} 대기 타임아웃 — 이동 보류")
            return False, []
        time.sleep(WAIT_POLL_SEC)


def release_robot_zones(robot_id: int | None) -> None:
    if robot_id is None:
        return
    zone_lock.release_all_by_robot(robot_id)
