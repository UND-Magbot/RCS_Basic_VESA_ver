"""로봇 온라인 상태 백그라운드 캐시 (태블릿 지연 문제 전용, 독립 모듈).

배경:
    태블릿 상태조회(`/api/dispatch/poi/{id}/status`, 2초 폴링)가 요청 경로에서
    idle 로봇 전체에 실시간 접속(`fetch_all_robots_live`)을 하면서,
    오프라인 로봇 1대당 ~9초(HTTP 3s + WS 연결 3s + 수집)씩 매달려 태블릿이 멈추던 문제.
    (여러 태블릿이 2초마다 9초짜리 요청을 쌓으면 브라우저 연결 한도 초과 → 버튼이 수십초~수분 지연)

해결:
    데몬 스레드가 REFRESH_INTERVAL마다 온라인 상태를 갱신해 메모리에 캐시하고,
    요청 경로는 `get_online_ips()`로 캐시만 즉시 읽는다(네트워크 호출 0).
    → 로봇이 몇 대든, 몇 대가 오프라인이든 요청 경로는 절대 블로킹되지 않는다.

간섭 최소화 원칙:
    - `main.py` 등 앱 수명주기를 건드리지 않도록 첫 접근 시 스레드를 지연 시작한다.
    - 기존 `fetch_all_robots_live` / 타임아웃 상수(HTTP_TIMEOUT, WS_TIMEOUT)는 그대로 둔다
      → 관제 실시간 뷰 등 다른 기능에 전혀 영향 없음.
    - 모든 app 임포트는 함수 내부 지연 임포트로 순환참조를 피한다.
"""
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 3.0  # 캐시 갱신 주기(초) — 온라인 정보 최대 이만큼 지연(가용 카운트엔 무해)

_lock = threading.Lock()
_online_ips: set = set()
_updated_at: float = 0.0
_ready: bool = False

_thread: Optional[threading.Thread] = None
_thread_start_lock = threading.Lock()


def _get_active_targets() -> list:
    """DB에서 활성+IP 로봇의 {ip, secret} 목록.

    (지연 임포트로 순환참조 회피 / 테스트에서 이 함수 자체를 교체 가능)
    """
    from app.database import SessionLocal
    from app.models.robot import Robot
    from app.routers.robot import DEFAULT_SECRET
    db = SessionLocal()
    try:
        robots = db.query(Robot).filter(
            Robot.is_active == True, Robot.ip_address != None  # noqa: E712
        ).all()
        return [{"ip": r.ip_address, "secret": DEFAULT_SECRET} for r in robots]
    finally:
        db.close()


def _fetch_live(targets: list) -> dict:
    """실시간 온라인 조회 (테스트에서 이 함수 자체를 교체 가능)."""
    from app.robot_api.robot_live_service import fetch_all_robots_live
    return fetch_all_robots_live(targets)


def _refresh_once() -> None:
    """온라인 IP 집합을 1회 갱신한다."""
    global _online_ips, _updated_at, _ready
    targets = _get_active_targets()
    if not targets:
        online: set = set()
    else:
        live = _fetch_live(targets)
        online = {
            it.get("IP")
            for it in live.get("items", [])
            if it.get("ONLINE") == "Online"
        }
    # dict/set 통째 교체로 원자적 갱신 (읽는 쪽과 경합 최소화)
    with _lock:
        _online_ips = online
        _updated_at = time.time()
        _ready = True


def _loop() -> None:
    while True:
        try:
            _refresh_once()
        except Exception as e:  # 어떤 이유로도 스레드가 죽지 않게
            logger.warning(f"[online_cache] 갱신 실패(무시): {e}")
        time.sleep(REFRESH_INTERVAL)


def _ensure_started() -> None:
    """백그라운드 갱신 스레드를 최초 1회 시작(지연 시작)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _thread_start_lock:
        if _thread is not None and _thread.is_alive():
            return
        t = threading.Thread(target=_loop, name="robot-online-cache", daemon=True)
        t.start()
        _thread = t
        logger.info("[online_cache] 백그라운드 온라인 갱신 스레드 시작 (%.1fs 주기)", REFRESH_INTERVAL)


def is_ready() -> bool:
    """캐시가 최소 1회 채워졌는지 여부. (호출 시 스레드 시작도 보장)"""
    _ensure_started()
    with _lock:
        return _ready


def get_online_ips() -> set:
    """현재 온라인 로봇 IP 집합(캐시 복사본). 즉시 반환 — 네트워크 호출 없음."""
    _ensure_started()
    with _lock:
        return set(_online_ips)
