"""
랙 사이즈별 rack.specs 정의.

AutoXing 펌웨어는 align_with_rack 시 등록된 rack.specs 중 LiDAR 측정값과
가까운 것을 매칭함. spec 이 여러 개면 잘못된 spec 과 매칭 시도해 실패하는
케이스가 관찰됨 → **사용하는 사이즈 종류만큼만 등록**하는 게 안전.

이 파일은 각 사이즈별 정의를 모아두고, 맵의 standby(랙 위치) POI 들이
가진 rack_size 종류만 모아 sync 시 전송한다.
"""
from __future__ import annotations

# 사이즈별 spec 정의
RACK_SPECS: dict[str, dict] = {
    "S300": {
        "width": 0.765, "depth": 0.765,
        "margin": [0.0925, 0.0925, 0.0925, 0.0925],
        "alignment": "center",
        "alignment_margin_back": 0.02,
        "extra_leg_offset": 0.0,
        "leg_shape": "other",
        "leg_size": 0.04,
        "foot_radius": 0.02,
        "cargo_to_jack_front_edge_min_distance": 0.05,
    },
    "S600": {
        "width": 0.83, "depth": 0.87,
        "margin": [0.1, 0.1, 0.1, 0.1],
        "alignment": "center",
        "alignment_margin_back": 0.02,
        "extra_leg_offset": 0.0,
        "leg_shape": "other",
        "leg_size": 0.05,
        "foot_radius": 0.025,
        "cargo_to_jack_front_edge_min_distance": 0.05,
    },
    # LG — longjack 로봇 전용 랙 (2026-07 실측: align_with_rack 성공 확인)
    "LG": {
        "width": 0.70, "depth": 0.50,
        "margin": [0.05, 0.05, 0.05, 0.05],
        "alignment": "center",
        "alignment_margin_back": 0.02,
        "extra_leg_offset": 0.0,
        "leg_shape": "round",
        "leg_size": 0.05,
        "foot_radius": 0.02,
        "cargo_to_jack_front_edge_min_distance": 0.05,
    },
    # LG2 — longjack 로봇용 2번째 LG 랙 (도면 기준: 다리 중심 665 × 600mm, 캐스터 GDS-100)
    # (구 이름 LG_V2 → LG2 로 통일)
    # 2026-08-06 실측: 구조 다리(프레임 기둥) 지름 25mm → leg_size 0.025 / foot_radius 0.0125.
    #   기존 0.05(50mm)는 다리 대신 옆 캐스터 브래킷을 다리로 오인식하는 원인이었음.
    # width/depth 는 도면 치수(665×600)가 아니라 라이다가 재는 "다리 중심 간격" 실측값을 넣어야 함.
    #   체인 제거 후에도 뒷다리 검출이 캐스터 간섭으로 depth 0.52~0.57 로 흔들림(약 5cm).
    #   펌웨어 크기매칭 허용오차 < 5cm 라 고정값 하나로 둘 다 못 맞음 → 관측 범위 중앙값 0.545 로 설정
    #   (0.52·0.57 양쪽 오차 0.025 로 균등). width 0.64 는 안정적.
    #   (초기 0.47/0.37 은 중앙에 늘어진 체인이 라이다에 잡혀 나온 가짜값이었음.)
    # margin 0.05→0.08: 캐스터(바퀴)가 다리 라인 밖으로 튀어나와 장애물로 잡히는 문제 →
    #   랙 풋프린트를 넓혀 캐스터를 랙 일부로 포함시킴 (leg_size 는 그대로).
    "LG2": {
        "width": 0.64, "depth": 0.545,
        "margin": [0.08, 0.08, 0.08, 0.08],
        "alignment": "center",
        "alignment_margin_back": 0.02,
        "extra_leg_offset": 0.0,
        "leg_shape": "round",
        "leg_size": 0.025,
        "foot_radius": 0.0125,
        "cargo_to_jack_front_edge_min_distance": 0.05,
    },
}

# 등록 안 된 사이즈의 폴백 (S300 단일 spec 검증된 값)
DEFAULT_SPEC_NAME = "S300"


def build_rack_specs_for_sizes(sizes: set[str] | list[str] | None) -> list[dict]:
    """주어진 사이즈 집합에 해당하는 spec dict 리스트.
    - 비어있으면 DEFAULT_SPEC_NAME 하나만 반환 (안전 기본).
    - 한 사이즈만 들어있으면 spec 1개 (펌웨어 매칭 문제 회피).
    - 여러 사이즈면 모두 등록.
    """
    pool = set(s for s in (sizes or []) if s in RACK_SPECS)
    if not pool:
        pool = {DEFAULT_SPEC_NAME}
    # 정렬: 안정적 순서 보장 (가장 작은 사이즈가 첫 번째)
    ordered = sorted(pool, key=lambda s: RACK_SPECS[s]["width"])
    return [RACK_SPECS[s] for s in ordered]


# 로봇 모델명 → spec 이름 (하드코딩 매핑)
MODEL_TO_SPEC: dict[str, str] = {
    "crawler_s300_op5": "S300",
    "crawler_heavy": "S600",
}


def spec_name_for_robot_model(model: str | None) -> str:
    """robot.model → spec 이름. 매핑에 없으면 DEFAULT_SPEC_NAME(S300) 폴백."""
    if not model:
        return DEFAULT_SPEC_NAME
    return MODEL_TO_SPEC.get(model, DEFAULT_SPEC_NAME)


def build_rack_specs_for_robot_model(model: str | None) -> list[dict]:
    """로봇 모델명에 맞는 rack.specs 리스트 (항상 spec 1개) — 펌웨어 매칭 충돌 없음."""
    name = spec_name_for_robot_model(model)
    return [RACK_SPECS[name]]


def collect_rack_sizes_in_map(db, map_id: int) -> set[str]:
    """해당 맵의 standby/jack POI 들에 지정된 rack_size 집합 반환.
    rack_size 가 비어있는 POI 는 무시 (운영자가 명시한 사이즈만 사용).
    """
    from app.models.map import MapPOI
    rows = db.query(MapPOI.rack_size).filter(
        MapPOI.map_id == map_id,
        MapPOI.poi_type.in_(["standby", "jack"]),
        MapPOI.is_active == True,
        MapPOI.rack_size.isnot(None),
    ).all()
    return {r[0] for r in rows if r[0]}


def build_rack_specs_for_map(db, map_id: int) -> list[dict]:
    """맵별 동적 spec 결정 — 그 맵의 POI rack_size 종류만큼만 spec 생성."""
    sizes = collect_rack_sizes_in_map(db, map_id)
    return build_rack_specs_for_sizes(sizes)
