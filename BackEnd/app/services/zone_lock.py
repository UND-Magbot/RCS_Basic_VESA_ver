"""
Zone 락 매니저 — 좁은 양방향 통로 등 '한 번에 한 로봇만' 통과해야 하는 구간 보호.

MapPolygon (shape_type='zone') 에 정의된 구역을 단위로 락.
POI 락과 별개 — POI 락은 정적 목적지 보호, zone 락은 통로 통과 보호.
in-memory, thread-safe.
"""
import threading
from typing import Iterable

_lock = threading.Lock()
_held: dict[int, int] = {}  # zone_id (= map_polygons.id) → robot_id


def _normalize(zone_ids: Iterable[int | None]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for zid in zone_ids:
        if zid is None or zid in seen:
            continue
        seen.add(zid)
        out.append(zid)
    return out


def try_acquire(zone_ids: Iterable[int | None], robot_id: int) -> tuple[bool, int | None]:
    """여러 zone 을 원자적으로 락. 하나라도 충돌 시 전체 롤백."""
    ids = _normalize(zone_ids)
    if not ids:
        return True, None
    with _lock:
        for zid in ids:
            owner = _held.get(zid)
            if owner is not None and owner != robot_id:
                return False, zid
        for zid in ids:
            _held[zid] = robot_id
        return True, None


def release(zone_ids: Iterable[int | None], robot_id: int) -> None:
    ids = _normalize(zone_ids)
    if not ids:
        return
    with _lock:
        for zid in ids:
            if _held.get(zid) == robot_id:
                _held.pop(zid, None)


def release_all_by_robot(robot_id: int) -> None:
    with _lock:
        to_pop = [zid for zid, rid in _held.items() if rid == robot_id]
        for zid in to_pop:
            _held.pop(zid, None)


def get_owner(zone_id: int) -> int | None:
    with _lock:
        return _held.get(zone_id)


def snapshot() -> dict[int, int]:
    with _lock:
        return dict(_held)
