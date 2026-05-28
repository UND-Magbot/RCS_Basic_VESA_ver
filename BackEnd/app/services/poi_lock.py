"""
POI 락 매니저 — 같은 POI를 두 로봇이 동시에 점유하지 못하도록 사전 차단.
다중 로봇 환경의 가장 단순한 중앙 조율: in-memory, thread-safe.

같은 robot_id가 다시 잡으려 하면 통과(재진입 허용).
"""
import threading
from typing import Iterable

_lock = threading.Lock()
_held: dict[int, int] = {}  # poi_id → robot_id


def _normalize(poi_ids: Iterable[int | None]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for pid in poi_ids:
        if pid is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out


def try_acquire(poi_ids: Iterable[int | None], robot_id: int) -> tuple[bool, int | None]:
    """여러 POI를 한 로봇에게 한꺼번에 락. 하나라도 충돌하면 전체 롤백.

    반환: (성공 여부, 충돌한 poi_id 또는 None)
    """
    ids = _normalize(poi_ids)
    if not ids:
        return True, None
    with _lock:
        for pid in ids:
            owner = _held.get(pid)
            if owner is not None and owner != robot_id:
                return False, pid
        for pid in ids:
            _held[pid] = robot_id
        return True, None


def release(poi_ids: Iterable[int | None], robot_id: int) -> None:
    """해당 로봇이 잡고 있던 POI만 해제."""
    ids = _normalize(poi_ids)
    if not ids:
        return
    with _lock:
        for pid in ids:
            if _held.get(pid) == robot_id:
                _held.pop(pid, None)


def release_all_by_robot(robot_id: int) -> None:
    """로봇 단위 강제 해제 — 작업 종료/예외/취소 시 안전망."""
    with _lock:
        to_pop = [pid for pid, rid in _held.items() if rid == robot_id]
        for pid in to_pop:
            _held.pop(pid, None)


def get_owner(poi_id: int) -> int | None:
    with _lock:
        return _held.get(poi_id)


def snapshot() -> dict[int, int]:
    """디버깅/모니터링용 현재 락 상태 스냅샷."""
    with _lock:
        return dict(_held)
