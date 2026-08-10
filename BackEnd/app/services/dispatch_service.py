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
    with_rack: bool = True   # (리프팅) True=렉 픽업 후 작업, False=렉 없이
    use_jack: bool = True    # False=서빙 로봇 (잭 동작 전혀 없음, 단순 이동만)
    thread: Optional[threading.Thread] = None
    next_event: threading.Event = field(default_factory=threading.Event)       # 경유지 등록+출발 신호
    confirm_event: threading.Event = field(default_factory=threading.Event)    # 로봇 태블릿 [확인] 신호
    end_flag: bool = False
    abort_flag: bool = False   # 강제 정리 — 복귀 시퀀스 없이 세션만 종료 (로봇 이동/잭 동작 없음)
    pending_route: Optional[list[int]] = None  # 콘솔이 등록한 경유지 큐 (출발 시 워커가 소비)


# 워커 레지스트리 — robot_id 기준
_workers: dict[int, _Worker] = {}
_workers_lock = threading.Lock()

# 예약 자동 처리 직렬화 — 여러 워커가 동시에 종료해도 예약 배정은 한 번에 하나씩
_reservation_lock = threading.Lock()

# ── 로봇 라이브(ONLINE) 상태 캐시 ────────────────────────────
# fetch_all_robots_live 는 로봇당 REST(3s)+WS(최대 4s) 를 태워 수 초가 걸린다.
# 콘솔 폴링(2초)과 호출/예약 요청마다 이걸 그대로 태우면 응답이 밀려 UI 반영이 늦어지므로
# IP 별로 짧은 TTL 캐시를 둔다.
LIVE_CACHE_TTL = 15.0  # LTE: 라이브 체크가 무거우므로 캐시를 길게 (온·오프라인은 급변 안 함)
_live_ip_cache: dict[str, tuple[float, bool]] = {}  # ip -> (조회시각, online)
_live_cache_lock = threading.Lock()


def online_ips_cached(ips: list[str]) -> set[str]:
    """주어진 IP 중 온라인인 것 (TTL 캐시). 만료된 IP만 실제 라이브 조회."""
    if not ips:
        return set()

    now = time.time()
    result: set[str] = set()
    stale: list[str] = []
    with _live_cache_lock:
        for ip in ips:
            entry = _live_ip_cache.get(ip)
            if entry and (now - entry[0]) < LIVE_CACHE_TTL:
                if entry[1]:
                    result.add(ip)
            else:
                stale.append(ip)

    if stale:
        try:
            from app.robot_api.robot_live_service import fetch_all_robots_live
            from app.routers.robot import DEFAULT_SECRET
            live = fetch_all_robots_live([{"ip": ip, "secret": DEFAULT_SECRET} for ip in stale])
            online = {it.get("IP") for it in live.get("items", []) if it.get("ONLINE") == "Online"}
        except Exception as e:
            logger.warning(f"[live] 라이브 체크 실패(무시 — 전부 온라인 취급): {e}")
            online = set(stale)

        ts = time.time()
        with _live_cache_lock:
            for ip in stale:
                _live_ip_cache[ip] = (ts, ip in online)
        result |= {ip for ip in stale if ip in online}

    return result


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
    if w is None:
        return False
    # thread 미시작(예약 상태) 또는 실행 중이면 활성 — 동시 호출의 중복 배정 방지
    return w.thread is None or w.thread.is_alive()


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
      - 서빙(use_jack=False) : 잭/대기장소 없이 바로 충전소
      - 리프팅 + 렉  : 잭 든 채 standby 이동 → 잭다운(렉 반납) → 충전소 도킹
      - 리프팅 + 렉없이 : 그 자리에서 잭다운 → 충전소 도킹
    """
    robot = _load_robot(worker.robot_id)
    if not robot:
        return

    if not worker.use_jack:
        # 서빙 로봇 : 잭 없음, 대기장소 경유 없이 바로 충전소
        logger.info(f"[dispatch] {worker.robot_ip} 서빙 로봇 — 잭/대기장소 생략, 바로 충전소")
    elif worker.with_rack:
        # 도착 상태에서 잭을 든 채로 대기장소까지 운반 (잭업 불필요 — 작업 내내 들고 있음)
        standby = jack_service._get_standby_poi(area_id=worker.area_id, robot_ip=worker.robot_ip)
        if standby:
            jack_service.update_job_status(worker.robot_ip, message="복귀 중: 대기장소")
            jack_service.safe_move(
                worker.robot_ip,
                "standard",
                standby["x"], standby["y"], standby["ori"],
            )

        # standby에 도착 후 잭 다운 (렉 반납) — 작업 전체에서 유일한 잭다운
        _jack_down_step(worker, label="잭 다운 — 대기장소 반납")
    else:
        # 리프팅 + 렉없이 : standby 경유 생략, 그 자리에서 잭 다운 후 충전소로
        _jack_down_step(worker, label="잭 다운 — 작업 종료")
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
            if not worker.use_jack:
                # 서빙 로봇 : 잭/픽업 없음 — 바로 첫 POI로 이동
                pass
            elif worker.with_rack:
                # 리프팅 + 렉 : standby에서 align_with_rack + jack_up
                _set_db_status(worker.session_id, "picking_up")
                ok = _pickup_at_standby(worker)
                if not ok:
                    _set_db_status(worker.session_id, "failed", error="픽업 실패")
                    return
            else:
                # 리프팅 + 렉 없이 : standby 안 거치고 잭만 올림 (안전한 이동을 위해)
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
            # 도착 — 잭을 든 채로 작업 대기 (잭 사이클 없음: 픽업 시 1회만 업)
            _set_db_status(worker.session_id, "awaiting_next")
            jack_service.update_job_status(worker.robot_ip, message=f"{poi['name']} 도착 — 작업 대기")

        # 3) 메인 루프 — 경유지 등록(awaiting_next) → 경유지 순회(moving/awaiting_confirm)
        while True:
            # === 경유지 등록 + 출발 대기 (호출 위치 도착 직후, 콘솔이 경로 등록) ===
            # 지나간 출발 신호/경로가 남아 있으면 이번 대기를 즉시 통과해
            # 의도치 않은 경로로 출발한다. 상태를 열기 전에 비운다.
            worker.next_event.clear()
            worker.pending_route = None
            if worker.end_flag:
                break

            _set_db_status(worker.session_id, "awaiting_next")
            jack_service.update_job_status(worker.robot_ip, message="경유지 등록 대기 중")

            worker.next_event.wait()
            worker.next_event.clear()
            if worker.end_flag:
                break

            route = worker.pending_route
            worker.pending_route = None
            if not route:
                continue  # 잘못 깨움 — 다시 대기

            # === 경유지를 순서대로 진행 (각 도착마다 로봇 태블릿 [확인] 대기) ===
            aborted = False
            for idx, next_id in enumerate(route):
                poi = _load_poi(next_id)
                if not poi:
                    logger.warning(f"[dispatch] 경유지 POI {next_id} 조회 실패 — 건너뜀")
                    _mark_waypoint(worker.session_id, idx, "done")
                    continue

                _set_target_poi(worker.session_id, next_id)
                _mark_waypoint(worker.session_id, idx, "current")
                # 잭을 든 채로 이동 (잭 사이클 없음). 이 시점이 "출발"
                _set_db_status(worker.session_id, "moving")
                ok = _move_to_poi(worker, poi)
                if not ok:
                    logger.warning(f"[dispatch] {poi['name']} 이동 실패")
                _set_current_poi(worker.session_id, next_id)

                is_last = (idx == len(route) - 1)

                # 지나간 [확인] 신호가 남아 있으면 이번 대기를 즉시 통과해
                # 사람 확인 없이 다음 위치로 출발해 버린다(연타/재시도 시 발생).
                # 상태를 열기 전에 반드시 비운다.
                worker.confirm_event.clear()
                if worker.end_flag:
                    aborted = True
                    break

                _set_db_status(worker.session_id, "awaiting_confirm")
                label = f"{poi['name']} 도착 — 확인 대기" + (" (마지막 위치)" if is_last else "")
                jack_service.update_job_status(worker.robot_ip, message=label)

                # 로봇 부착 태블릿의 [확인] 대기 (사람이 누를 때까지)
                worker.confirm_event.wait()
                worker.confirm_event.clear()
                _mark_waypoint(worker.session_id, idx, "done")
                if worker.end_flag:
                    aborted = True
                    break

            if worker.end_flag or aborted:
                break

            # 경유지 전체 완료 — 배터리 충분 + 대기 예약이 있으면
            # 충전소/렉 반납 없이 렉을 든 채 바로 다음 예약 장소로 이어서 진행
            cont = _take_next_reservation_if_continuable(worker)
            if cont is None:
                break  # 이어받을 예약 없음(또는 배터리 부족) → 종료 시퀀스로
            cont_poi, cont_res_id = cont

            poi = _load_poi(cont_poi)
            if not poi:
                logger.warning(f"[dispatch] 이어받기 POI {cont_poi} 조회 실패 — 예약 복구 후 종료")
                _mark_reservation(cont_res_id, "waiting")  # 선점 롤백
                break
            logger.info(f"[dispatch] 예약 이어받기 — robot={worker.robot_id} → poi={cont_poi} "
                        f"(충전소/렉반납 생략)")
            _clear_waypoints(worker.session_id)
            _set_target_poi(worker.session_id, cont_poi)
            _set_db_status(worker.session_id, "moving")
            jack_service.update_job_status(worker.robot_ip, message=f"이어서 작업 — {poi['name']}로 이동")
            ok = _move_to_poi(worker, poi)
            if not ok:
                # 이동 실패 — 예약을 되돌려 다른 로봇/다음 기회에 처리되게 하고 종료 시퀀스로
                logger.warning("[dispatch] 이어받기 이동 실패 — 예약 복구 후 종료 시퀀스로 전환")
                _mark_reservation(cont_res_id, "waiting")
                break
            _set_current_poi(worker.session_id, cont_poi)
            _set_db_status(worker.session_id, "awaiting_next")
            jack_service.update_job_status(worker.robot_ip, message=f"{poi['name']} 도착 — 경유지 등록 대기")
            continue  # 메인 루프 재진입 (새 경유지 등록 대기)

        # 4) 종료 시퀀스
        if worker.abort_flag:
            # 강제 정리 — 로봇 이동/잭 동작 없이 세션만 종료 (물리 위치·잭 상태 그대로).
            # 로봇을 충전소로 보내려면 원격제어의 '충전소 복귀'를 별도로 사용한다.
            logger.info(f"[dispatch] 강제 정리 — robot={worker.robot_id} 세션 종료(이동 없음)")
            _clear_waypoints(worker.session_id)
            _set_db_status(worker.session_id, "failed", error="작업 강제 정리")
        else:
            # 중간 종료로 남은 경유지가 있으면 점유가 계속 잡히므로 비운다
            _clear_waypoints(worker.session_id)
            _set_db_status(worker.session_id, "returning")
            _set_target_poi(worker.session_id, None)
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
        # 이 로봇이 가용해졌으니 대기 중 예약이 있으면 FIFO로 자동 호출
        try:
            try_fulfill_reservations()
        except Exception:
            logger.exception("[dispatch] 종료 후 예약 자동 처리 실패")


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


def _set_route_waypoints(session_id: int, poi_ids: list[int]) -> None:
    db = SessionLocal()
    try:
        dispatch_crud.set_route_waypoints(db, session_id, poi_ids)
    finally:
        db.close()


def _mark_waypoint(session_id: int, seq: int, status: str) -> None:
    db = SessionLocal()
    try:
        dispatch_crud.mark_waypoint(db, session_id, seq, status)
    finally:
        db.close()


def _clear_waypoints(session_id: int) -> None:
    db = SessionLocal()
    try:
        dispatch_crud.clear_route_waypoints(db, session_id)
    finally:
        db.close()


def _take_next_reservation_if_continuable(worker: "_Worker") -> Optional[tuple[int, int]]:
    """작업 종료 시점에 '충전소 안 가고 이어서' 가능한지 판단.

    조건: 종료 명령 없음 + 현재 배터리 >= robot.min_battery + 점유되지 않은 대기 예약 존재.
    만족하면 그 예약을 선점(fulfilled)하고 (poi_id, reservation_id) 반환, 아니면 None.
    호출자는 실제 이동에 실패하면 reservation_id 를 'waiting' 으로 되돌려야 한다.
    (잭/렉은 든 상태 그대로 유지 — 반납·충전소 생략)
    """
    if worker.end_flag:
        return None

    # 배터리 체크
    db = SessionLocal()
    try:
        robot = db.query(Robot).filter(Robot.id == worker.robot_id).first()
        if not robot:
            return None
        stat = db.query(RobotStatus).filter(RobotStatus.robot_id == worker.robot_id).first()
        battery = stat.battery_level if (stat and stat.battery_level is not None) else None
        min_batt = robot.min_battery if robot.min_battery is not None else 20
    finally:
        db.close()

    # 배터리 정보가 '있고' 기준 미만일 때만 충전 우선. 정보가 없으면(None) 이어받기 허용
    # (find_available_robot 의 배정 정책과 일관 — 모르면 진행).
    if battery is not None and battery < min_batt:
        logger.info(f"[dispatch] 이어받기 보류 — robot={worker.robot_id} 배터리 {battery}% < {min_batt}% → 충전 먼저")
        return None

    # 예약 소비 (try_fulfill_reservations 와 직렬화)
    with _reservation_lock:
        db = SessionLocal()
        try:
            reservations = dispatch_crud.list_waiting_reservations(db)
            if not reservations:
                logger.info(f"[dispatch] 이어받기 없음 — robot={worker.robot_id} 대기 예약 0 (battery={battery})")
                return None

            # 점유된 POI(다른 로봇이 가 있거나 경유지로 잡아둔 곳)는 건너뜀.
            # 단 내 현재 위치는 곧 떠나므로 제외한다.
            occupied = dispatch_crud.occupied_poi_ids(db)
            own = db.query(DispatchSession).filter(DispatchSession.id == worker.session_id).first()
            if own and own.current_poi_id:
                occupied.discard(own.current_poi_id)

            res = next((r for r in reservations if r.poi_id not in occupied), None)
            if not res:
                logger.info(f"[dispatch] 이어받기 없음 — robot={worker.robot_id} 대기 예약이 모두 점유 중")
                return None

            dispatch_crud.mark_reservation(db, res.id, "fulfilled")
            logger.info(f"[dispatch] 이어받기 — robot={worker.robot_id} battery={battery} min={min_batt} → poi={res.poi_id}")
            return res.poi_id, res.id
        finally:
            db.close()


# ── 외부 API ──────────────────────────────────────────────────


def start_session(robot_id: int, first_poi_id: int, with_rack: bool = True) -> tuple[bool, str]:
    """세션 시작. 이미 활성(또는 예약 중)인 워커가 있으면 거부.

    with_rack=False 면 standby 픽업 단계를 건너뛰고 바로 first_poi로 이동.
    동시 호출이 같은 로봇을 중복 배정하지 못하도록 워커 슬롯을 _workers_lock 안에서
    원자적으로 예약(thread=None 상태로 등록)한 뒤 DB 세션 생성/스레드 기동을 진행한다.
    """
    robot = _load_robot(robot_id)
    if not robot or not robot.ip_address:
        return False, "로봇 정보 없음 또는 IP 미설정"
    if not robot.is_active:
        return False, "비활성 로봇"

    poi = _load_poi(first_poi_id)
    if not poi:
        return False, "첫 작업 POI 조회 실패"

    # 서빙 로봇은 잭 동작 없음 + 렉 개념 무의미
    use_jack = (robot.robot_type != "serving")
    eff_with_rack = with_rack if use_jack else False

    worker = _Worker(
        robot_id=robot_id,
        session_id=0,  # DB 세션 생성 후 채움
        robot_ip=robot.ip_address,
        area_id=int(robot.area_id) if robot.area_id else None,
        with_rack=eff_with_rack,
        use_jack=use_jack,
    )

    # ── 원자적 슬롯 예약: 확인과 등록 사이의 틈(TOCTOU)을 제거 ──
    with _workers_lock:
        existing = _workers.get(robot_id)
        if existing is not None and (existing.thread is None or existing.thread.is_alive()):
            return False, "이미 진행 중인 배차가 있습니다"
        _workers[robot_id] = worker  # thread=None 상태로 슬롯 점유(예약)

    # ── 예약 확보 후 DB 세션 생성 + 워커 스레드 기동 ──
    try:
        db = SessionLocal()
        try:
            session = dispatch_crud.create_session(db, robot_id, first_poi_id, with_rack=with_rack)
            worker.session_id = session.id
        finally:
            db.close()
    except Exception as e:
        _remove_worker(robot_id)  # 예약 롤백
        logger.exception("[dispatch] 세션 생성 실패")
        return False, f"세션 생성 실패: {e}"

    t = safe_thread(target=_worker_loop, args=(worker,), name=f"dispatch-{robot_id}")
    worker.thread = t
    t.start()
    return True, "ok"


def send_route(robot_id: int, poi_ids: list[int]) -> tuple[bool, str]:
    """호출 위치에 도착(awaiting_next)한 로봇에 경유지 경로를 등록하고 출발시킨다.

    poi_ids 순서대로 이동하며, 각 위치 도착 후 로봇 태블릿 [확인](send_confirm)을
    눌러야 다음으로 진행한다. 마지막 확인 후 자동 종료.
    """
    worker = _get_worker(robot_id)
    if not worker:
        return False, "활성 배차 없음"
    if worker.end_flag:
        return False, "종료 진행 중 — 명령 무시"

    db = SessionLocal()
    try:
        s = db.query(DispatchSession).filter(DispatchSession.id == worker.session_id).first()
        if not s or s.status in ("returning", "completed", "failed"):
            return False, "종료/완료된 세션 — 명령 무시"
        if s.status != "awaiting_next":
            return False, f"현재 상태({s.status})에서는 경유지를 등록할 수 없습니다"
    finally:
        db.close()

    # 유효한 POI만 추림 — 중복 제거(순서 유지) + 조회 가능한 것만.
    # 연속으로 같은 위치가 들어오면 제자리 이동이 되므로 서비스 계층에서도 방어한다.
    valid: list[int] = []
    seen: set[int] = set()
    for pid in poi_ids:
        if pid in seen:
            continue
        if _load_poi(pid):
            seen.add(pid)
            valid.append(pid)
    if not valid:
        return False, "유효한 경유지가 없습니다"

    _set_route_waypoints(worker.session_id, valid)  # DB 영속 (로봇 태블릿 조회용)
    # 출발 상태를 동기적으로 반영 — 워커가 이벤트를 처리하기 전에 폴링이 이전 상태(awaiting_next)를
    # 보여 콘솔 UI가 롤백되는 것을 막는다. 워커는 깨어나 같은 값으로 재설정 후 실제 이동을 수행한다.
    _set_target_poi(worker.session_id, valid[0])
    _mark_waypoint(worker.session_id, 0, "current")
    _set_db_status(worker.session_id, "moving")
    worker.pending_route = valid
    worker.next_event.set()
    return True, "ok"


def send_next(robot_id: int, next_poi_id: int) -> tuple[bool, str]:
    """레거시 단일 다음 위치 — 경유지 1개짜리 경로로 처리."""
    return send_route(robot_id, [next_poi_id])


def send_confirm(robot_id: int) -> tuple[bool, str]:
    """로봇 부착 태블릿의 [확인] — 다음 경유지로 진행(또는 마지막이면 종료 유도)."""
    worker = _get_worker(robot_id)
    if not worker:
        return False, "활성 배차 없음"
    if worker.end_flag:
        return False, "종료 진행 중 — 명령 무시"

    db = SessionLocal()
    try:
        s = db.query(DispatchSession).filter(DispatchSession.id == worker.session_id).first()
        if not s or s.status != "awaiting_confirm":
            return False, f"확인할 수 있는 상태가 아닙니다 ({s.status if s else 'none'})"
    finally:
        db.close()

    worker.confirm_event.set()
    return True, "ok"


def send_end(robot_id: int) -> tuple[bool, str]:
    worker = _get_worker(robot_id)
    if not worker:
        return False, "활성 배차 없음"
    if worker.end_flag:
        return True, "이미 종료 처리 중"
    worker.end_flag = True
    # 어느 대기 상태든 깨어나도록 두 이벤트 모두 set
    worker.next_event.set()
    worker.confirm_event.set()
    return True, "ok"


def force_clear(robot_id: int) -> tuple[bool, str]:
    """실행 중인 배차 작업을 로봇 이동 없이 강제 정리.

    - 메모리 워커에 abort 신호 → 복귀(충전소) 시퀀스 없이 세션을 종료하고 워커 슬롯 해제.
      (로봇은 물리적으로 그대로 — 잭/위치 유지. 충전소로 보내려면 '충전소 복귀'를 별도로.)
    - 워커가 없으면(서버 재시작 후 유실 등) DB 활성 세션만 정리한다.
    """
    worker = _get_worker(robot_id)
    if worker:
        worker.abort_flag = True
        worker.end_flag = True  # 대기 루프를 깨워 종료로 진입시킴 (abort_flag 로 복귀 스킵)
        worker.next_event.set()
        worker.confirm_event.set()
        return True, "ok"
    # 워커 없음 — DB 활성 세션만 정리
    db = SessionLocal()
    try:
        sess = dispatch_crud.get_active_session(db, robot_id)
        if not sess:
            return True, "정리할 작업 없음"
        dispatch_crud.clear_route_waypoints(db, sess.id)
        dispatch_crud.update_status(db, sess.id, "failed", error="작업 강제 정리")
    finally:
        db.close()
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
        # 남은 경유지/이동 목표는 정리한다 (안 그러면 그 POI들이 계속 점유로 잡힘)
        _clear_waypoints(session.id)
        _set_target_poi(session.id, None)
        _set_db_status(session.id, "awaiting_next")

        # 서빙 로봇은 잭 동작 없음 — 복구 시에도 동일하게 반영해야 종료 때 잭다운을 시도하지 않음
        use_jack = (robot.robot_type != "serving")
        rec_with_rack = bool(session.with_rack) if session.with_rack is not None else True
        if not use_jack:
            rec_with_rack = False

        worker = _Worker(
            robot_id=session.robot_id,
            session_id=session.id,
            robot_ip=robot.ip_address,
            area_id=int(robot.area_id) if robot.area_id else None,
            with_rack=rec_with_rack,
            use_jack=use_jack,
        )
        t = safe_thread(target=_worker_loop, args=(worker,), kwargs={"skip_pickup": True},
                        name=f"dispatch-recover-{session.robot_id}")
        worker.thread = t
        _set_worker(worker)
        t.start()
        logger.info(f"[dispatch] 재시작 복구 — robot_id={session.robot_id} session={session.id}")

    # 복구 후, 가용 로봇이 남아있고 대기 예약이 있으면 자동 호출
    try:
        try_fulfill_reservations()
    except Exception:
        logger.exception("[dispatch] 복구 후 예약 자동 처리 실패")


# ── POI 호출 (위치별 태블릿용) ────────────────────────────────


def find_available_robot(area_id: Optional[int] = None,
                         robot_type: Optional[str] = None) -> Optional[Robot]:
    """가용 로봇 1대 선정.

    조건:
      - is_active=True, ip_address 있음
      - 같은 area_id (있을 경우)
      - 같은 robot_type (있을 경우 — 'lifting'/'serving')
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
        if robot_type is not None:
            q = q.filter(Robot.robot_type == robot_type)
        rows = q.all()
    finally:
        db.close()

    candidates = []
    for robot, stat in rows:
        if has_active_worker(robot.id):
            continue
        battery_val = stat.battery_level if (stat and stat.battery_level is not None) else None
        if battery_val is not None:
            min_batt = robot.min_battery if robot.min_battery is not None else 20
            if battery_val < min_batt:
                continue  # 배터리 부족 — 충전 우선, 배정에서 제외
        battery = battery_val if battery_val is not None else -1
        candidates.append((battery, robot.id, robot))

    if not candidates:
        return None
    # 배터리 내림차순, 동률이면 ID 오름차순
    candidates.sort(key=lambda x: (-x[0], x[1]))

    # 라이브 ONLINE 체크 — TTL 캐시 사용 (매 요청마다 수 초 걸리는 것 방지)
    online_ips = online_ips_cached([r.ip_address for _, _, r in candidates])

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


def _poi_area_id(poi_id: int) -> Optional[int]:
    """POI가 속한 area_id (robot_maps.area_id 경유). 없으면 None."""
    db = SessionLocal()
    try:
        p = db.query(MapPOI).filter(MapPOI.id == poi_id).first()
        if p and p.map_id:
            from app.models.map import RobotMap
            rm = db.query(RobotMap).filter(RobotMap.id == p.map_id).first()
            if rm and rm.area_id is not None:
                return int(rm.area_id)
    finally:
        db.close()
    return None


def _assign_available_robot(poi_id: int, with_rack: bool,
                            robot_type: Optional[str] = None) -> tuple[bool, str, Optional[int]]:
    """가용 로봇 1대를 그 POI에 배정(start_session). 경쟁 시 다음 후보 재시도.

    robot_type 지정 시 그 타입('lifting'/'serving')만 배정.

    반환: (성공, 메시지, robot_id)
      - 성공: (True, "ok", robot_id)
      - 가용 로봇 없음: (False, "no_robot", None)  ← 호출자가 예약으로 전환
      - 기타 실패: (False, 메시지, None)
    """
    poi_area_id = _poi_area_id(poi_id)
    for _ in range(5):
        robot = find_available_robot(area_id=poi_area_id, robot_type=robot_type)
        if not robot:
            # 같은 area 없으면 전체에서 한번 더 시도 (필요 시)
            robot = find_available_robot(area_id=None, robot_type=robot_type)
        if not robot:
            return False, "no_robot", None

        ok, msg = start_session(robot.id, poi_id, with_rack=with_rack)
        if ok:
            return True, "ok", robot.id
        if msg == "이미 진행 중인 배차가 있습니다":
            # 다른 호출이 방금 이 로봇을 가져감 — 다음 후보 재선정
            time.sleep(0.05)
            continue
        return False, msg, None

    return False, "가용 로봇 배정 경합 — 잠시 후 다시 시도하세요", None


def call_to_poi(poi_id: int, robot_type: Optional[str] = None,
                with_rack: bool = True) -> tuple[bool, str, Optional[int], bool]:
    """그 POI로 가용 로봇 1대 배정해서 호출. 가용 로봇이 없으면 예약 생성.

    robot_type: 'lifting'/'serving' 지정 시 그 타입만. None이면 아무 타입.
    with_rack : (리프팅) True=렉 픽업 후 이동, False=렉 없이. 서빙은 무시(잭 없음).

    반환: (성공 여부, 메시지, 배정된 robot_id, 예약 여부)
      - 즉시 배차: (True, "ok", robot_id, False)
      - 예약 생성: (True, "reserved", None, True)
      - 거부:      (False, 사유, None, False)
    """
    # 1) 점유 검증 — current/target 뿐 아니라 '다른 로봇의 미방문 경유지'도 거부
    db = SessionLocal()
    try:
        occupied = dispatch_crud.occupied_poi_ids(db)
    finally:
        db.close()
    if poi_id in occupied:
        return False, "다른 로봇이 점유(또는 경유지로 예약) 중인 위치입니다", None, False

    poi = _load_poi(poi_id)
    if not poi:
        return False, "POI 조회 실패", None, False

    # 2) 가용 로봇 배정 시도
    ok, msg, robot_id = _assign_available_robot(poi_id, with_rack, robot_type=robot_type)
    if ok:
        # 이 POI에 남아있던 대기 예약이 있으면 소진 처리 (직접 배차로 충족됨)
        db = SessionLocal()
        try:
            r = dispatch_crud.get_waiting_reservation(db, poi_id)
            if r:
                dispatch_crud.mark_reservation(db, r.id, "fulfilled")
        finally:
            db.close()
        return True, "ok", robot_id, False
    if msg != "no_robot":
        return False, msg, None, False

    # 3) 가용 로봇 없음 → 예약 생성 (FIFO 대기열). 로봇이 종료되면 자동 호출됨.
    db = SessionLocal()
    try:
        dispatch_crud.create_reservation(db, poi_id, with_rack=with_rack)
    finally:
        db.close()
    return True, "reserved", None, True


# ── 예약 자동 처리 ────────────────────────────────────────────


def cancel_reservation_at_poi(poi_id: int) -> bool:
    """그 POI의 대기 중 예약을 취소. 취소된 게 있으면 True."""
    db = SessionLocal()
    try:
        return dispatch_crud.cancel_reservation(db, poi_id)
    finally:
        db.close()


def _mark_reservation(reservation_id: int, status: str) -> None:
    db = SessionLocal()
    try:
        dispatch_crud.mark_reservation(db, reservation_id, status)
    finally:
        db.close()


def try_fulfill_reservations() -> None:
    """가용 로봇이 생겼을 때 대기 중 예약을 FIFO로 자동 호출.

    - 여러 로봇이 동시에 가용해질 수 있으므로 가용 로봇이 떨어질 때까지 반복.
    - 이미 그 POI에 로봇이 도착/이동 중이면 그 예약은 소진(fulfilled) 처리.
    - 동시 호출은 _reservation_lock 으로 직렬화 (한쪽이 처리 중이면 스킵).
    """
    if not _reservation_lock.acquire(blocking=False):
        return  # 이미 다른 스레드가 처리 중 — 그쪽이 끝까지 소화함
    try:
        while True:
            db = SessionLocal()
            try:
                reservations = dispatch_crud.list_waiting_reservations(db)
                # 점유(도착/이동 중/다른 로봇의 미방문 경유지) 상태
                occupied = dispatch_crud.occupied_poi_ids(db)
            finally:
                db.close()
            if not reservations:
                return

            progressed = False
            for res in reservations:
                # 그새 그 POI가 점유되면(로봇이 이미 가 있거나 가기로 정해짐) 예약 소진 처리
                if res.poi_id in occupied:
                    _mark_reservation(res.id, "fulfilled")
                    progressed = True
                    break

                ok, msg, robot_id = _assign_available_robot(res.poi_id, bool(res.with_rack))
                if ok:
                    _mark_reservation(res.id, "fulfilled")
                    logger.info(f"[dispatch] 예약 자동 호출 — poi={res.poi_id} robot={robot_id}")
                    progressed = True
                    break
                if msg == "no_robot":
                    return  # 가용 로봇 없음 — 다음 종료 때 다시 시도
                # 기타 실패 — 그 예약은 건너뛰고 다음 예약 시도
                logger.warning(f"[dispatch] 예약 자동 호출 실패 poi={res.poi_id}: {msg}")

            if not progressed:
                return  # 처리할 수 있는 예약이 없음
    finally:
        _reservation_lock.release()
