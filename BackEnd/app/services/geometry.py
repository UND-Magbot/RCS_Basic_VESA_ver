"""
2D 지오메트리 헬퍼 — zone 점/선 포함 판정용.

좌표계: 로봇 월드 좌표 (world_x, world_y).
MapPolygon.points_json 의 항목은 {x, y, worldX, worldY} 형태 — worldX/worldY 를 사용.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

logger = logging.getLogger(__name__)


def _polygon_world_points(points_json: str | None) -> list[tuple[float, float]]:
    if not points_json:
        return []
    try:
        raw = json.loads(points_json)
    except Exception:
        return []
    pts: list[tuple[float, float]] = []
    for p in raw or []:
        wx = p.get("worldX")
        wy = p.get("worldY")
        if wx is None or wy is None:
            # 폴백: x/y 가 world 인 경우
            wx = p.get("x")
            wy = p.get("y")
        if wx is None or wy is None:
            continue
        pts.append((float(wx), float(wy)))
    return pts


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray casting. polygon 은 닫힌 다각형으로 간주 (마지막 점이 첫 점과 자동 연결)."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _segments_intersect(a1, a2, b1, b2) -> bool:
    """선분 a1-a2 와 b1-b2 가 교차하면 True."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1 = cross(b1, b2, a1)
    d2 = cross(b1, b2, a2)
    d3 = cross(a1, a2, b1)
    d4 = cross(a1, a2, b2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def segment_crosses_polygon(p1: tuple[float, float], p2: tuple[float, float],
                            polygon: list[tuple[float, float]]) -> bool:
    """선분이 폴리곤과 교차하거나 끝점이 폴리곤 내부면 True.
    (이동 경로가 zone 을 지나가는지 판정용)
    """
    if point_in_polygon(p1[0], p1[1], polygon) or point_in_polygon(p2[0], p2[1], polygon):
        return True
    n = len(polygon)
    for i in range(n):
        a = polygon[i]
        b = polygon[(i + 1) % n]
        if _segments_intersect(p1, p2, a, b):
            return True
    return False


def load_zone_polygons(db, map_id: int) -> list[tuple[int, str, list[tuple[float, float]]]]:
    """주어진 맵의 zone 폴리곤 목록 (id, name, points)."""
    from app.models.map import MapPolygon
    rows = db.query(MapPolygon).filter(
        MapPolygon.map_id == map_id,
        MapPolygon.shape_type == "zone",
        MapPolygon.is_active == True,
    ).all()
    out: list[tuple[int, str, list[tuple[float, float]]]] = []
    for r in rows:
        pts = _polygon_world_points(r.points_json)
        if len(pts) >= 3:
            out.append((r.id, r.name, pts))
    return out


def zones_crossed_by_segment(zones: Iterable[tuple[int, str, list[tuple[float, float]]]],
                             p1: tuple[float, float],
                             p2: tuple[float, float]) -> list[int]:
    """선분이 지나가는 zone id 리스트."""
    out: list[int] = []
    for zid, _name, poly in zones:
        if segment_crosses_polygon(p1, p2, poly):
            out.append(zid)
    return out


def zones_containing_point(zones: Iterable[tuple[int, str, list[tuple[float, float]]]],
                           x: float, y: float) -> list[int]:
    out: list[int] = []
    for zid, _name, poly in zones:
        if point_in_polygon(x, y, poly):
            out.append(zid)
    return out
