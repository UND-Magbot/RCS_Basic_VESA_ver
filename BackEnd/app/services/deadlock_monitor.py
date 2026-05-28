"""
다중 로봇 데드락 자동 감지 + 양보.

배경: AutoXing 펌웨어는 P2P 분산 회피만 수행해, 두 로봇이 좁은 통로에서
마주보거나 동일 목적지를 점유하면 풀리지 않는 정체가 생긴다.
이 모듈이 외부에서 감지해 한쪽 작업을 중단시켜 흐름을 회복한다.

판정:
  A) /planning_state.is_waiting_for_dest = True 가 연속 DEADLOCK_HITS 샘플
  B) move_state = moving 이면서 remaining_distance 변화가 STUCK_DELTA_M 미만
이 모듈은 잭킹 작업의 wait_move 안에서 일어나는 호출과 무관하게 동작하며,
stop_robot_job 으로 작업 중단을 유발하면 jack_service 의 _check_stop 가 다음
틱에서 RuntimeError 를 던져 작업이 정상적으로 종료된다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Optional

from websocket import WebSocketException, create_connection

logger = logging.getLogger(__name__)

ROBOT_PORT = 8090

# ── 튜닝 가능한 정책 상수 ─────────────────────────────────────
POLL_INTERVAL_SEC = 5.0      # 활성 로봇 폴링 주기
WS_RECV_TIMEOUT_SEC = 1.5    # 한 번 받을 때 최대 대기
WINDOW_SIZE = 6              # 누적 샘플 (= 30초 분량)
DEADLOCK_HITS = 5            # 연속 N개가 같은 데드락 패턴이면 발동 (= 25초)
STUCK_DELTA_M = 0.05         # remaining_distance 변화량 임계
RETRY_AFTER_SEC = 30.0       # 양보 후 자동 재시도까지 대기
MAX_RETRIES = 1              # 자동 재시도 횟수 한도
NEAR_PEER_HITS = 3           # 가까운 다른 로봇 감지 시 양보 트리거 임계 (= 15초, 짧음)
# ──────────────────────────────────────────────────────────────

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None
_window: dict[str, deque] = {}  # robot_ip → deque[sample dict]
_window_lock = threading.Lock()  # _window 동시 접근 보호


def _fetch_robot_signals(ip: str) -> dict:
    """단일 WebSocket으로 /planning_state + /nearby_robots 1회씩 수신.
    반환 키: 'planning', 'nearby_count' (수신 못한 토픽은 누락)."""
    ws = None
    out: dict = {}
    try:
        ws = create_connection(f"ws://{ip}:{ROBOT_PORT}/ws/v2/topics", timeout=2)
        ws.send(json.dumps({"enable_topic": "/planning_state"}))
        ws.send(json.dumps({"enable_topic": "/nearby_robots"}))
        ws.settimeout(WS_RECV_TIMEOUT_SEC)
        deadline = time.time() + WS_RECV_TIMEOUT_SEC
        while time.time() < deadline and not ("planning" in out and "nearby_count" in out):
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            packet = json.loads(raw)
            t = packet.get("topic")
            if t == "/planning_state" and "planning" not in out:
                out["planning"] = packet
            elif t == "/nearby_robots" and "nearby_count" not in out:
                # 메시지 포맷이 robots 배열을 포함하거나 비어있을 수 있음
                robots = packet.get("robots") or packet.get("nearby_robots") or []
                out["nearby_count"] = len(robots) if isinstance(robots, list) else 0
        return out
    except (WebSocketException, OSError, json.JSONDecodeError) as exc:
        logger.debug(f"[deadlock] {ip} signal fetch failed: {exc}")
        return out
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _detect(ip: str) -> tuple[bool, str]:
    """윈도우 분석. (True, 사유) 또는 (False, '')."""
    # 정밀 동작 중(align_with_rack / 잭킹 / 충전 도킹) 은 펌웨어가 천천히 미세 움직임을 만드므로
    # 데드락 감지에서 제외. 그 외 일반 이동 상태에서만 stuck 판정.
    try:
        from app.services.jack_service import get_job_status
        js = get_job_status(ip)
        if js:
            status = str(js.get("status", "")).lower()
            if status in {"aligning", "jacking_up", "jacking_down", "charging",
                           "returning", "waiting", "waiting_confirm",
                           "waiting_confirm_return", "waiting_next_or_return"}:
                return False, ""
    except Exception:
        pass

    with _window_lock:
        win = _window.get(ip)
        if not win:
            return False, ""
        snapshot_list = list(win)

    # 최근 샘플들에 가까운 다른 로봇이 있었으면 임계 단축 (사전 양보)
    win = snapshot_list  # 이후 로직은 list 로 처리
    tail_check = list(win)[-NEAR_PEER_HITS:]
    has_recent_peer = len(tail_check) >= NEAR_PEER_HITS and all(
        s.get("nearby_count", 0) > 0 for s in tail_check
    )
    hits = NEAR_PEER_HITS if has_recent_peer else DEADLOCK_HITS

    if len(win) < hits:
        return False, ""
    recent = list(win)[-hits:]

    suffix = " (peer 근접으로 단축 임계 적용)" if has_recent_peer else ""

    # A) 목적지 점유 — 펌웨어가 스스로 길가에서 대기 중인 상태가 오래 지속
    if all(s.get("waiting_for_dest") for s in recent):
        return True, f"is_waiting_for_dest 지속 (목적지 점유){suffix}"

    # B) 물리적 stuck — moving 인데 거리 변화 거의 없음
    if all(str(s.get("move_state", "")).lower() == "moving" for s in recent):
        ds = [s.get("remaining_distance") for s in recent if s.get("remaining_distance") is not None]
        if len(ds) >= hits:
            # 목적지 1m 이내는 정밀 도착(to_unload_point 정렬, charge 도킹 등) 단계 — 펌웨어가 천천히 미세 움직임.
            # 데드락 감지에서 제외 (false positive 방지).
            if ds[-1] < 1.0:
                return False, ""
            if (max(ds) - min(ds)) < STUCK_DELTA_M:
                return True, f"remaining_distance 정체 ({ds[-1]:.2f}m){suffix}"

    return False, ""


def _collect_active_robots() -> list[tuple[int, str]]:
    """현재 작업 중인 로봇 (robot_id, ip) 리스트.
    jack_service._job_status 의 IP 와 poi_lock 이 잡고 있는 robot_id 를 합집합으로.
    """
    from app.services import jack_service, poi_lock
    from app.database import SessionLocal
    from app.models.robot import Robot

    active_ips = set(jack_service._job_status.keys())
    locked_robot_ids = set(poi_lock.snapshot().values())

    if not active_ips and not locked_robot_ids:
        return []

    db = SessionLocal()
    try:
        q = db.query(Robot.id, Robot.ip_address).filter(Robot.is_active == True)
        if locked_robot_ids and active_ips:
            q = q.filter((Robot.id.in_(locked_robot_ids)) | (Robot.ip_address.in_(active_ips)))
        elif locked_robot_ids:
            q = q.filter(Robot.id.in_(locked_robot_ids))
        else:
            q = q.filter(Robot.ip_address.in_(active_ips))
        return [(rid, ip) for rid, ip in q.all() if ip]
    finally:
        db.close()


def _yield_robot(robot_id: int, robot_ip: str, reason: str) -> None:
    """양보: 작업 중단 + 재시도용 메타 보관(jack_service) + activity_log."""
    from app.services.jack_service import yield_robot_job
    from app.crud.activity_log import log_activity

    logger.warning(f"[deadlock] robot {robot_id} ({robot_ip}) 양보 — {reason}")
    try:
        paused = yield_robot_job(robot_ip, retries=MAX_RETRIES)
    except Exception:
        logger.exception(f"[deadlock] yield_robot_job 실패 ({robot_ip})")
        paused = False
    try:
        msg = (
            f"데드락 감지 → 로봇 {robot_id} 양보 (자동 재시도 예약): {reason}"
            if paused else
            f"데드락 감지 → 로봇 {robot_id} 작업 중단: {reason}"
        )
        log_activity("robot", "deadlock_yield", msg, source="deadlock_monitor")
    except Exception:
        pass
    with _window_lock:
        _window.pop(robot_ip, None)


def _retry_paused_jobs() -> None:
    """양보로 일시정지된 작업을 시간 경과 + 비활성 확인 후 새 스레드로 재실행."""
    from app.services import jack_service, poi_lock
    from app.crud.activity_log import log_activity

    paused = jack_service.get_paused_jobs()
    if not paused:
        return
    now = time.time()
    for ip, meta in paused.items():
        if (now - meta.get("paused_at", now)) < RETRY_AFTER_SEC:
            continue
        # 아직 로봇이 활성 (이전 작업이 정리 안됨) — 다음 사이클에 다시 시도
        if jack_service.get_job_status(ip):
            continue
        consumed = jack_service.consume_paused_job(ip)
        if not consumed:
            continue
        if consumed.get("retries_left", 0) <= 0:
            log_activity(
                "robot", "deadlock_retry_skip",
                f"로봇 {consumed.get('robot_id')} 재시도 횟수 초과 — 자동 재시도 포기",
                source="deadlock_monitor",
            )
            continue
        # POI 락 재획득 (충돌 시 포기)
        rid = consumed["robot_id"]
        wps = consumed.get("waypoints") or []
        pids = [w.get("poi_id") for w in wps]
        ok, conflict = poi_lock.try_acquire(pids, rid)
        if not ok:
            log_activity(
                "robot", "deadlock_retry_skip",
                f"로봇 {rid} 재시도 보류 — POI {conflict} 여전히 점유 중",
                source="deadlock_monitor",
            )
            # 한 번 더 기회를 주기 위해 다시 _paused 로 되돌림
            consumed["retries_left"] = max(0, consumed["retries_left"])
            consumed["paused_at"] = now  # 30초 더 대기
            jack_service._paused_route_jobs[ip] = consumed
            continue
        # 새 스레드로 재실행
        consumed["retries_left"] -= 1
        log_activity(
            "robot", "deadlock_retry",
            f"로봇 {rid} 자동 재시도 시작 (남은 재시도 {consumed['retries_left']})",
            source="deadlock_monitor",
        )
        from app.services.thread_utils import safe_thread
        safe_thread(target=_do_retry, args=(consumed,), name=f"deadlock-retry-{rid}").start()


def _do_retry(meta: dict) -> None:
    """양보된 작업을 같은 인자로 재실행. 종료 시 POI 락 해제."""
    from app.services.jack_service import run_route_job
    from app.services import poi_lock

    rid = meta["robot_id"]
    ip = meta["robot_ip"]
    try:
        run_route_job(
            ip,
            meta.get("waypoints") or [],
            manual_confirm=meta.get("manual_confirm", False),
            skip_standby_pickup=meta.get("skip_standby_pickup", False),
            skip_standby_return=meta.get("skip_standby_return", False),
            area_id=meta.get("area_id"),
            work_mode=meta.get("work_mode", "rack_pickup"),
            robot_id=rid,
        )
    except Exception:
        logger.exception(f"[deadlock] retry job for robot {rid} failed")
    finally:
        poi_lock.release_all_by_robot(rid)


def _loop() -> None:
    logger.info("[deadlock] monitor started (poll=%.1fs, hits=%d)", POLL_INTERVAL_SEC, DEADLOCK_HITS)
    while not _stop_event.is_set():
        try:
            active = _collect_active_robots()
            active_ips = {ip for _, ip in active}

            # 비활성이 된 로봇 윈도우 정리
            with _window_lock:
                for stale in list(_window.keys()):
                    if stale not in active_ips:
                        _window.pop(stale, None)

            # 활성 로봇이 1대 이하면 데드락 감지 자체가 의미 없음 (다른 로봇이 없음).
            # 단, 양보 후 자동 재시도(_retry_paused_jobs) 는 이전 사이클의 잔여 작업 회수용이라 그대로 실행.
            if len(active) < 2:
                with _window_lock:
                    _window.clear()
                _retry_paused_jobs()
                _stop_event.wait(POLL_INTERVAL_SEC)
                continue

            # 폴링 + 누적
            candidates: dict[str, tuple[int, str]] = {}  # ip → (robot_id, reason)
            for rid, ip in active:
                signals = _fetch_robot_signals(ip)
                planning = signals.get("planning")
                if planning is None:
                    continue
                sample = {
                    "move_state": planning.get("move_state"),
                    "waiting_for_dest": planning.get("is_waiting_for_dest") is True,
                    "remaining_distance": planning.get("remaining_distance"),
                    "nearby_count": signals.get("nearby_count", 0),
                }
                with _window_lock:
                    _window.setdefault(ip, deque(maxlen=WINDOW_SIZE)).append(sample)
                hit, reason = _detect(ip)
                if hit:
                    candidates[ip] = (rid, reason)

            # 후보가 여럿이면 robot_id 가장 큰 하나만 양보 (반대편 통과시키려는 의도)
            if candidates:
                victim_ip = max(candidates.keys(), key=lambda i: candidates[i][0])
                rid, reason = candidates[victim_ip]
                _yield_robot(rid, victim_ip, reason)

            # 양보된 작업 자동 재시도
            _retry_paused_jobs()

        except Exception:
            logger.exception("[deadlock] loop error")

        _stop_event.wait(POLL_INTERVAL_SEC)
    logger.info("[deadlock] monitor stopped")


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="deadlock-monitor", daemon=True)
    _thread.start()


def stop() -> None:
    _stop_event.set()


def snapshot() -> dict[str, list[dict]]:
    """디버깅용: 현재 누적된 샘플 윈도우."""
    with _window_lock:
        return {ip: list(win) for ip, win in _window.items()}
