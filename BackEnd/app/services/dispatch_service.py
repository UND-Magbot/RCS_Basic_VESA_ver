"""인터랙티브 배차 (VESA 모드) — 워커 스레드 + Event 기반 next/end

운영 흐름:
  start  → standby에서 align_with_rack → jack_up
         → first POI로 이동 → 도착 (잭 유지)
  next   → target POI로 이동 → 도착 (잭 유지)
  end    → standby로 이동 → jack_down → 충전소 도킹

핵심:
  - 로봇당 워커 스레드 1개 (동시 세션 1개 보장)
  - next/end 명령은 threading.Event 로 워커에 전달
  - 종료(returning) 상태가 되면 그 이후 next/end 명령은 모두 무시
  - 서버 재시작 시 awaiting_next 세션은 워커만 재기동 (로봇은 이미 도착 상태)
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from app.database import SessionLocal
from app.models.dispatch import DispatchSession
from app.models.map import MapPOI
from app.models.robot import Robot, RobotStatus
from app.crud import dispatch as dispatch_crud
from app.services import jack_service
from app.services.thread_utils import safe_thread

logger = logging.getLogger(__name__)

# 잭 업/다운 후 안정화 대기 (jack_service의 JACK_WAIT_SEC와 동일 기준)
JACK_SETTLE_SEC = 8


@dataclass
class _Worker:
    robot_id: int
    session_id: int
    robot_ip: str
    area_id: Optional[int]
    with_rack: bool = True   # True=픽업 후 작업, False=잭 조작 없이 바로 작업
    thread: Optional[threading.Thread] = None
    next_event: threading.Event = field(default_factory=threading.Event)
    end_flag: bool = False
    pending_next_poi_id: Optional[int] = None  # 외부에서 set, 워커가 소비


# 워커 레지스트리 — robot_id 기준
_workers: dict[int, _Worker] = {}
_workers_lock = threading.Lock()


def _get_worker(robot_id: int) -> Optional[_Worker]:
    with _workers_lock:
        return _workers.get(robot_id)


def _set_worker(worker: _Worker) -> None:
    with _workers_lock:
        _workers[worker.robot_id] = worker


def _remove_worker(robot_id: int) -> None:
    with _workers_lock:
        _workers.pop(robot_id, None)


def has_active_worker(robot_id: int) -> bool:
    w = _get_worker(robot_id)
    return w is not None and w.thread is not None and w.thread.is_alive()


# ── POI 조회 헬퍼 ─────────────────────────────────────────────


def _load_poi(poi_id: int) -> Optional[dict]:
    """POI id → {id, name, x, y, ori}"""
    db = SessionLocal()
    try:
        p = db.query(MapPOI).filter(MapPOI.id == poi_id, MapPOI.is_active == True).first()
        if not p or p.world_x is None or p.world_y is None:
            return None
        return {
            "id": p.id,
            "name": p.name,
            "x": float(p.world_x),
            "y": float(p.world_y),
            "ori": float(p.angle or 0),
        }
    finally:
        db.close()


def _load_robot(robot_id: int) -> Optional[Robot]:
    db = SessionLocal()
    try:
        return db.query(Robot).filter(Robot.id == robot_id).first()
    finally:
        db.close()


def _load_charging_poi(robot: Robot) -> Optional[dict]:
    if not robot.charging_id:
        return None
    return _load_poi(robot.charging_id)


def _find_charge_approach_poi(area_id: Optional[int], charger_name: str) -> Optional[dict]:
    """충전소 사전 접근 POI("<charger_name>-1") 검색.

    같은 area의 활성 맵에서 name=`{charger_name}-1` 인 활성 POI를 찾음.
    없으면 None (그러면 호출자가 충전소 좌표를 그대로 사전 접근에 사용).
    """
    if not charger_name:
        return None
    from app.models.map import RobotMap
    db = SessionLocal()
    try:
        active_map = None
        if area_id is not None:
            active_map = (
                db.query(RobotMap)
                .filter(RobotMap.area_id == area_id, RobotMap.is_active == True)
                .order_by(RobotMap.id.desc())
                .first()
            )
        if active_map is None:
            active_map = (
                db.query(RobotMap)
                .filter(RobotMap.is_active == True)
                .order_by(RobotMap.id.desc())
                .first()
            )
        if not active_map:
            return None
        poi = (
            db.query(MapPOI)
            .filter(
                MapPOI.map_id == active_map.id,
                MapPOI.name == f"{charger_name}-1",
                MapPOI.is_active == True,
            )
            .first()
        )
        if poi and poi.world_x is not None:
            return {
                "name": poi.name,
                "x": float(poi.world_x),
                "y": float(poi.world_y),
                "ori": float(poi.angle if poi.angle is not None else 0),
            }
        return None
    finally:
        db.close()


# ── 이동 + 잭 헬퍼 (jack_service 재활용) ─────────────────────


def _pickup_at_standby(worker: _Worker) -> bool:
    """standby로 가서 align_with_rack → jack_up. 성공 시 True."""
    standby = jack_service._get_standby_poi(area_id=worker.area_id, robot_ip=worker.robot_ip)
    if not standby:
        logger.error(f"[dispatch] robot_id={worker.robot_id} standby POI 조회 실패")
        return False

    jack_service.update_job_status(worker.robot_ip, message="대기장소로 이동 (랙 정렬)")
    result = jack_service.safe_move(
        worker.robot_ip,
        "align_with_rack",
        standby["x"], standby["y"], standby["ori"],
    )
    if str(result.get("state", "")).lower() != "succeeded":
        logger.error(f"[dispatch] align_with_rack 실패: {result}")
        return False

    jack_service.update_job_status(worker.robot_ip, message="잭 업 진행 중")
    try:
        jack_service.jack_up(worker.robot_ip)
    except Exception as e:
        logger.error(f"[dispatch] jack_up 실패: {e}")
        return False
    time.sleep(JACK_SETTLE_SEC)
    return True


def _move_to_poi(worker: _Worker, poi: dict) -> bool:
    """POI로 standard 이동."""
    jack_service.update_job_status(worker.robot_ip, message=f"이동 중: {poi['name']}")
    result = jack_service.safe_move(
        worker.robot_ip,
        "standard",
        poi["x"], poi["y"], poi["ori"],
    )
    return str(result.get("state", "")).lower() == "succeeded"


def _jack_up_step(worker: _Worker, label: str = "잭 업") -> None:
    """잭 업 + 안정화 대기 — with_rack 무관 항상 동작 (렉 없는 모드여도 잭은 올림)."""
    jack_service.update_job_status(worker.robot_ip, message=label)
    try:
        jack_service.jack_up(worker.robot_ip)
    except Exception as e:
        logger.warning(f"[dispatch] jack_up 실패: {e}")
    time.sleep(JACK_SETTLE_SEC)


def _jack_down_step(worker: _Worker, label: str = "잭 다운") -> None:
    """잭 다운 + 안정화 대기 — with_rack 무관 항상 동작."""
    jack_service.update_job_status(worker.robot_ip, message=label)
    try:
        jack_service.jack_down(worker.robot_ip)
    except Exception as e:
        logger.warning(f"[dispatch] jack_down 실패: {e}")
    time.sleep(JACK_SETTLE_SEC)


def _return_to_standby_and_park(worker: _Worker) -> None:
    """종료 시퀀스:
      - with_rack=True : 잭업 → standby 이동 → 잭다운(렉 반납) → 충전소 도킹
      - with_rack=False: 그대로 → 충전소 사전 접근 → 충전소 도킹
      어느 모드든 도킹 전엔 잭이 내려간 상태 (메인 루프 마지막 도착 시 _jack_down_step 호출).
    """
    robot = _load_robot(worker.robot_id)
    if not robot:
        return

    if worker.with_rack:
        # 현재 도착 상태에서는 이미 잭 다운 상태 — 운반하려면 다시 잭업
        _jack_up_step(worker, label="잭 업 — 대기장소 복귀 준비")

        standby = jack_service._get_standby_poi(area_id=worker.area_id, robot_ip=worker.robot_ip)
        if standby:
            jack_service.update_job_status(worker.robot_ip, message="복귀 중: 대기장소")
            jack_service.safe_move(
                worker.robot_ip,
                "standard",
                standby["x"], standby["y"], standby["ori"],
            )

        # standby에 도착 후 잭 다운 (렉 반납)
        _jack_down_step(worker, label="잭 다운 — 대기장소 반납")
    else:
        # with_rack=False : 도착 시점에 이미 잭 다운 상태 → 바로 충전소로
        logger.info(f"[dispatch] {worker.robot_ip} 렉 없이 모드 — standby 경유 생략, 바로 충전소")

    # 충전소 도킹 (선택) — 2단계: 사전 접근 POI("<name>-1") → charge
    charging = _load_charging_poi(robot)
    if charging:
        cx, cy, cori = charging["x"], charging["y"], charging["ori"]
        approach = _find_charge_approach_poi(worker.area_id, charging["name"])

        # 1단계: 사전 접근 (best-effort — 실패해도 charge 단계로 진행)
        sx, sy, sori = (approach["x"], approach["y"], approach["ori"]) if approach else (cx, cy, cori)
        label = f"사전 접근({approach['name']})" if approach else "충전소 사전 접근"
        jack_service.update_job_status(worker.robot_ip, message=label)
        try:
            jack_service.safe_move(
                worker.robot_ip, "standard", sx, sy, sori,
                max_attempts=12, timeout=60,
            )
        except Exception as e:
            logger.warning(f"[dispatch] {label} 실패(무시): {e}")
        time.sleep(2)

        # 2단계: 도킹
        jack_service.update_job_status(worker.robot_ip, message="충전소 도킹 중")
        try:
            jack_service.safe_move(
                worker.robot_ip,
                "charge",
                cx, cy, cori,
                charge_retry_count=5,
            )
        except Exception as e:
            logger.warning(f"[dispatch] 충전소 도킹 실패: {e}")


# ── 워커 메인 루프 ─────────────────────────────────────────────


def _worker_loop(worker: _Worker, *, skip_pickup: bool = False) -> None:
    """로봇 1대의 인터랙티브 배차 전 생명주기.

    skip_pickup=True: 서버 재시작 복구 — 이미 잭 들고 도착해 있으므로 픽업 단계 스킵.
    worker.with_rack=False: 렉 없이 운영 — standby 픽업(align_with_rack) 건너뜀, 종료 시 standby 경유 안 함.
                           단 잭 동작은 항상 수행 (안전한 이동 + 작업 대기를 위해).
    """
    try:
        jack_service._running_robot_id_by_ip[worker.robot_ip] = worker.robot_id
        jack_service._stop_flags[worker.robot_ip] = False
        jack_service._paused_flags[worker.robot_ip] = False

        db = SessionLocal()
        try:
            session = db.query(DispatchSession).filter(DispatchSession.id == worker.session_id).first()
            if not session:
                logger.error(f"[dispatch] session {worker.session_id} not found")
                return
            first_target_id = session.target_poi_id
        finally:
            db.close()

        # 1) 픽업 단계
        if not skip_pickup:
            if worker.with_rack:
                # with_rack=True : standby에서 align_with_rack + jack_up
                _set_db_status(worker.session_id, "picking_up")
                ok = _pickup_at_standby(worker)
                if not ok:
                    _set_db_status(worker.session_id, "failed", error="픽업 실패")
                    return
            else:
                # with_rack=False : standby 안 거치고 잭만 올림 (안전한 이동을 위해)
                _set_db_status(worker.session_id, "picking_up")
                _jack_up_step(worker, label="잭 업 — 이동 준비")

        # 2) 첫 POI 이동 — 재시작 복구일 땐 스킵 (이미 도착 가정)
        if not skip_pickup:
            if first_target_id is None:
                _set_db_status(worker.session_id, "failed", error="first_poi_id 없음")
                return
            poi = _load_poi(first_target_id)
            if not poi:
                _set_db_status(worker.session_id, "failed", error="first POI 조회 실패")
                return
            _set_db_status(worker.session_id, "moving")
            ok = _move_to_poi(worker, poi)
            if not ok:
                _set_db_status(worker.session_id, "failed", error="첫 POI 이동 실패")
                return
            _set_current_poi(worker.session_id, first_target_id)
            # 도착 표시 즉시 업데이트 (잭다운 sleep 동안 태블릿이 calling→arrived 빠르게 전환)
            _set_db_status(worker.session_id, "awaiting_next")
            # 도착 후 잭 다운 (실제 잭다운 + 안정화 대기)
            _jack_down_step(worker, label=f"잭 다운 — {poi['name']} 작업 대기")

        # 3) 메인 루프 — awaiting_next ↔ moving
        while True:
            # 이미 awaiting_next로 설정됨 (첫 도착 후 또는 다음 POI 도착 후)
            jack_service.update_job_status(worker.robot_ip, message="다음 명령 대기 중")

            # 다음 명령 대기 (timeout 없음 — 사람이 누를 때까지)
            worker.next_event.wait()
            worker.next_event.clear()

            if worker.end_flag:
                break

            next_id = worker.pending_next_poi_id
            worker.pending_next_poi_id = None
            if next_id is None:
                # 누가 잘못 깨운 경우 다시 대기
                continue

            poi = _load_poi(next_id)
            if not poi:
                logger.warning(f"[dispatch] next POI {next_id} 조회 실패 — 대기 유지")
                continue

            _set_target_poi(worker.session_id, next_id)
            # 출발 전 잭 업 (들고 가기) — 잭업 동안은 status="awaiting_next" 유지
            _jack_up_step(worker, label=f"잭 업 — {poi['name']} 이동 준비")
            # 잭업 끝난 후 실제 이동 시작 — 이 시점이 "출발" 시점
            _set_db_status(worker.session_id, "moving")
            ok = _move_to_poi(worker, poi)
            if not ok:
                logger.warning(f"[dispatch] {poi['name']} 이동 실패 — 대기 상태로 복귀")
                _set_db_status(worker.session_id, "awaiting_next", error=f"{poi['name']} 이동 실패")
                continue
            _set_current_poi(worker.session_id, next_id)
            # 도착 표시 즉시 업데이트 (잭다운 sleep 동안 태블릿이 빠르게 arrived로 전환)
            _set_db_status(worker.session_id, "awaiting_next")
            # 도착 후 잭 다운 (실제 잭다운 + 안정화 대기)
            _jack_down_step(worker, label=f"잭 다운 — {poi['name']} 작업 대기")

        # 4) 종료 시퀀스
        _set_db_status(worker.session_id, "returning")
        jack_service.update_job_status(worker.robot_ip, message="작업 종료 — 복귀 시퀀스 시작")
        _return_to_standby_and_park(worker)
        _set_db_status(worker.session_id, "completed")
        jack_service.update_job_status(worker.robot_ip, message="작업 완료")

    except Exception as e:
        logger.exception(f"[dispatch] 워커 예외 — robot_id={worker.robot_id}")
        _set_db_status(worker.session_id, "failed", error=str(e))
    finally:
        jack_service._running_robot_id_by_ip.pop(worker.robot_ip, None)
        try:
            from app.services.zone_lock import release_all_by_robot as _release_zones
            _release_zones(worker.robot_id)
        except Exception:
            pass
        _remove_worker(worker.robot_id)


# ── DB 상태 갱신 헬퍼 ─────────────────────────────────────────


def _set_db_status(session_id: int, status: str, *, error: Optional[str] = None) -> None:
    db = SessionLocal()
    try:
        dispatch_crud.update_status(db, session_id, status, error=error)
    finally:
        db.close()


def _set_target_poi(session_id: int, poi_id: Optional[int]) -> None:
    db = SessionLocal()
    try:
        dispatch_crud.set_target(db, session_id, poi_id)
    finally:
        db.close()


def _set_current_poi(session_id: int, poi_id: int) -> None:
    db = SessionLocal()
    try:
        dispatch_crud.set_current(db, session_id, poi_id)
    finally:
        db.close()


# ── 외부 API ──────────────────────────────────────────────────


def start_session(robot_id: int, first_poi_id: int, with_rack: bool = True) -> tuple[bool, str]:
    """세션 시작. 이미 활성 워커가 있으면 거부.

    with_rack=False 면 standby 픽업 단계를 건너뛰고 바로 first_poi로 이동.
    """
    if has_active_worker(robot_id):
        return False, "이미 진행 중인 배차가 있습니다"

    robot = _load_robot(robot_id)
    if not robot or not robot.ip_address:
        return False, "로봇 정보 없음 또는 IP 미설정"
    if not robot.is_active:
        return False, "비활성 로봇"

    poi = _load_poi(first_poi_id)
    if not poi:
        return False, "첫 작업 POI 조회 실패"

    db = SessionLocal()
    try:
        session = dispatch_crud.create_session(db, robot_id, first_poi_id, with_rack=with_rack)
        session_id = session.id
    finally:
        db.close()

    worker = _Worker(
        robot_id=robot_id,
        session_id=session_id,
        robot_ip=robot.ip_address,
        area_id=int(robot.area_id) if robot.area_id else None,
        with_rack=with_rack,
    )
    t = safe_thread(target=_worker_loop, args=(worker,), name=f"dispatch-{robot_id}")
    worker.thread = t
    _set_worker(worker)
    t.start()
    return True, "ok"


def send_next(robot_id: int, next_poi_id: int) -> tuple[bool, str]:
    worker = _get_worker(robot_id)
    if not worker:
        return False, "활성 배차 없음"
    if worker.end_flag:
        return False, "종료 진행 중 — 명령 무시"

    # 종료 진행 중이면 DB status도 returning 일 것 → 한번 더 검증
    db = SessionLocal()
    try:
        s = db.query(DispatchSession).filter(DispatchSession.id == worker.session_id).first()
        if not s or s.status in ("returning", "completed", "failed"):
            return False, "종료/완료된 세션 — 명령 무시"
        if s.status != "awaiting_next":
            return False, f"현재 상태({s.status})에서는 다음 명령을 받을 수 없습니다"
    finally:
        db.close()

    poi = _load_poi(next_poi_id)
    if not poi:
        return False, "POI 조회 실패"

    worker.pending_next_poi_id = next_poi_id
    worker.next_event.set()
    return True, "ok"


def send_end(robot_id: int) -> tuple[bool, str]:
    worker = _get_worker(robot_id)
    if not worker:
        return False, "활성 배차 없음"
    if worker.end_flag:
        return True, "이미 종료 처리 중"
    worker.end_flag = True
    worker.next_event.set()
    return True, "ok"


# ── 재시작 복구 ───────────────────────────────────────────────


def recover_on_startup() -> None:
    """서버 재시작 시 미완료 세션 복구.

    starting/picking_up/moving 상태는 로봇 실제 위치를 알 수 없으므로
    안전하게 awaiting_next로 강제 전환 후 워커 재기동 (사람이 다음 명령 결정).
    returning 상태는 로봇이 어디까지 갔는지 불명 → failed로 마감.
    """
    db = SessionLocal()
    try:
        active = dispatch_crud.list_active_sessions(db)
    finally:
        db.close()

    for session in active:
        robot = _load_robot(session.robot_id)
        if not robot or not robot.ip_address:
            _set_db_status(session.id, "failed", error="복구 시 로봇 정보 없음")
            continue

        if session.status == "returning":
            _set_db_status(session.id, "failed", error="서버 재시작 — 복귀 중 중단")
            continue

        # 그 외 상태는 awaiting_next 로 정상화하고 워커 재시작 (잭은 들고 있다고 가정 — with_rack=True인 경우)
        _set_db_status(session.id, "awaiting_next")
        worker = _Worker(
            robot_id=session.robot_id,
            session_id=session.id,
            robot_ip=robot.ip_address,
            area_id=int(robot.area_id) if robot.area_id else None,
            with_rack=bool(session.with_rack) if session.with_rack is not None else True,
        )
        t = safe_thread(target=_worker_loop, args=(worker,), kwargs={"skip_pickup": True},
                        name=f"dispatch-recover-{session.robot_id}")
        worker.thread = t
        _set_worker(worker)
        t.start()
        logger.info(f"[dispatch] 재시작 복구 — robot_id={session.robot_id} session={session.id}")


# ── POI 호출 (위치별 태블릿용) ────────────────────────────────


def find_available_robot(area_id: Optional[int] = None) -> Optional[Robot]:
    """가용 로봇 1대 선정.

    조건:
      - is_active=True, ip_address 있음
      - 같은 area_id (있을 경우)
      - 활성 워커 없음 (= 다른 호출에 배정 안 됨)
      - **현재 온라인** (관제와 동일한 라이브 체크 — `fetch_all_robots_live`)

    정렬: 배터리 내림차순 → robot_id 오름차순
    """
    db = SessionLocal()
    try:
        q = db.query(Robot, RobotStatus).outerjoin(RobotStatus, RobotStatus.robot_id == Robot.id) \
              .filter(Robot.is_active == True, Robot.ip_address != None)
        if area_id is not None:
            q = q.filter(Robot.area_id == str(area_id))
        rows = q.all()
    finally:
        db.close()

    candidates = []
    for robot, stat in rows:
        if has_active_worker(robot.id):
            continue
        battery = stat.battery_level if stat and stat.battery_level is not None else -1
        candidates.append((battery, robot.id, robot))

    if not candidates:
        return None
    # 배터리 내림차순, 동률이면 ID 오름차순
    candidates.sort(key=lambda x: (-x[0], x[1]))

    # 라이브 ONLINE 체크 — 후보 IP만 모아 한 번에 병렬 조회
    try:
        from app.robot_api.robot_live_service import fetch_all_robots_live
        from app.routers.robot import DEFAULT_SECRET
        cand_ips = [r.ip_address for _, _, r in candidates]
        live = fetch_all_robots_live([{"ip": ip, "secret": DEFAULT_SECRET} for ip in cand_ips])
        online_ips = {it.get("IP") for it in live.get("items", []) if it.get("ONLINE") == "Online"}
    except Exception as e:
        logger.warning(f"[find_available_robot] 라이브 체크 실패(무시): {e}")
        online_ips = set(r.ip_address for _, _, r in candidates)  # 폴백 — 모두 통과

    for battery, rid, robot in candidates:
        if robot.ip_address in online_ips:
            return robot
        logger.info(f"[find_available_robot] {robot.name} ({robot.ip_address}) Offline — 제외")
    return None


def get_session_at_poi(poi_id: int) -> Optional[DispatchSession]:
    """그 POI에 도착해서 awaiting_next 상태로 있는 세션."""
    db = SessionLocal()
    try:
        return (
            db.query(DispatchSession)
            .filter(
                DispatchSession.current_poi_id == poi_id,
                DispatchSession.status == "awaiting_next",
            )
            .order_by(DispatchSession.id.desc())
            .first()
        )
    finally:
        db.close()


def get_session_heading_to_poi(poi_id: int) -> Optional[DispatchSession]:
    """그 POI로 이동/준비 중인 세션."""
    db = SessionLocal()
    try:
        return (
            db.query(DispatchSession)
            .filter(
                DispatchSession.target_poi_id == poi_id,
                DispatchSession.status.in_(("starting", "picking_up", "moving")),
            )
            .order_by(DispatchSession.id.desc())
            .first()
        )
    finally:
        db.close()


def call_to_poi(poi_id: int, with_rack: bool = True) -> tuple[bool, str, Optional[int]]:
    """그 POI로 가용 로봇 1대 배정해서 호출.

    with_rack=True : standby에서 렉 픽업 → 그 POI로 이동
    with_rack=False: 잭 조작 없이 바로 그 POI로 이동

    반환: (성공 여부, 메시지, 배정된 robot_id)
    """
    # 1) 점유 검증 — 다른 활성 세션이 그 POI를 current/target으로 들고 있으면 거부
    if get_session_at_poi(poi_id) or get_session_heading_to_poi(poi_id):
        return False, "이미 다른 로봇이 점유 중인 위치입니다", None

    poi = _load_poi(poi_id)
    if not poi:
        return False, "POI 조회 실패", None

    # 2) 가용 로봇 선정 — 같은 area 우선
    db = SessionLocal()
    try:
        p = db.query(MapPOI).filter(MapPOI.id == poi_id).first()
        # POI가 속한 area_id 구하기 — robot_maps.area_id 경유
        poi_area_id: Optional[int] = None
        if p and p.map_id:
            from app.models.map import RobotMap
            rm = db.query(RobotMap).filter(RobotMap.id == p.map_id).first()
            if rm and rm.area_id is not None:
                poi_area_id = int(rm.area_id)
    finally:
        db.close()

    robot = find_available_robot(area_id=poi_area_id)
    if not robot:
        # 같은 area 없으면 전체에서 한번 더 시도 (필요 시)
        robot = find_available_robot(area_id=None)
    if not robot:
        return False, "가용 로봇이 없습니다 (작업 중 또는 오프라인)", None

    ok, msg = start_session(robot.id, poi_id, with_rack=with_rack)
    if not ok:
        return False, msg, None
    return True, "ok", robot.id
