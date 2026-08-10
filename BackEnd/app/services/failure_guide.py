"""호출/다음 실패 안내 문구 단일 소스 (Single Source of Truth).

태블릿에 "왜 안 됐는지(why) + 어떻게 해결하는지(how)"를 보여주기 위한
매핑 테이블. 문구는 오직 여기에만 두고, 프론트는 why/how/for_admin/code를
받아 표시만 한다.

키 종류:
  - 로봇 int 코드 (AutoXing chassis moves 응답의 fail_reason): 506, 501, 101, 3, 9, 503 …
  - 즉시거부/내부 문자열 키: "NO_ROBOT", "POI_OCCUPIED", "STANDBY_MISSING" …

사용:
  guide_for(506)            -> {"why":..., "how":..., "for_admin":False, "code":506}
  guide_for("NO_ROBOT")     -> {..., "code":"NO_ROBOT"}
  guide_for("506")          -> 506 과 동일 (숫자 문자열은 int 로 정규화)
  guide_for(<미지의 키>)     -> GENERIC 폴백 (+ code=<원본키>)
  guide_for(9, with_rack=True) -> 렉 적재 상황 전용 문구 (아래 FAILURE_GUIDE_WITH_RACK)

렉 적재 여부에 따라 원인이 달라지는 코드가 있어서(대표: 9 경로계산 실패),
그런 코드만 FAILURE_GUIDE_WITH_RACK 에 오버라이드로 둔다. 없으면 기본 표를 쓴다.
"""
from typing import Union

# ── 폴백 문구 ────────────────────────────────────────────────
GENERIC = {
    "why": "로봇 동작이 실패했어요.",
    "how": "잠시 후 다시 시도하고, 계속되면 관리자에게 알리세요.",
    "for_admin": False,
}

# ── 단일 소스 매핑 테이블 ────────────────────────────────────
FAILURE_GUIDE: dict = {
    # ── 로봇 int 코드 (chassis moves fail_reason) ──
    506: {
        "why": "로봇이 이미 렉을 들고 있어요.",
        "how": "'렉 없이'로 호출하세요. (또는 렉을 내려놓고 '렉 포함'으로 다시 호출)",
        "for_admin": False,
    },
    501: {
        "why": "렉 두는 자리에 렉이 없어요 (로봇이 렉을 찾지 못함).",
        "how": "렉을 제자리에 놓고 다시 호출하세요.",
        "for_admin": False,
    },
    101: {
        "why": "충전독을 찾지 못했어요.",
        "how": "로봇을 충전소 가까이 옮긴 뒤 다시 시도하세요.",
        "for_admin": False,
    },
    3: {
        "why": "로봇이 자기 위치를 몰라요 (위치추정 필요).",
        "how": "rb-admin에서 재위치(Relocate)를 한 뒤 다시 호출하세요.",
        "for_admin": False,
    },
    9: {
        "why": "목적지까지 갈 수 있는 길이 막혔어요.",
        "how": "통로에 장애물이 없는지, 맵 경로가 올바른지 확인하세요.",
        "for_admin": False,
    },
    503: {
        "why": "렉을 내려놓을 자리가 이미 차 있어요.",
        "how": "그 자리를 비운 뒤 다시 시도하세요.",
        "for_admin": False,
    },

    # ── 즉시거부 / 내부 문자열 키 ──
    "NO_ROBOT": {
        "why": "지금 쓸 수 있는 로봇이 없어요 (모두 작업 중이거나 꺼져 있음).",
        "how": "다른 로봇이 작업을 마칠 때까지 기다렸다가 다시 호출하세요.",
        "for_admin": False,
    },
    "QUEUED": {
        "why": "지금은 로봇이 모두 작업 중이에요. 대기열에 등록했습니다.",
        "how": "먼저 끝나는 로봇이 자동으로 옵니다. 다시 누르지 않아도 됩니다.",
        "for_admin": False,
    },
    "ALREADY_QUEUED": {
        "why": "이미 대기열에 등록돼 있어요.",
        "how": "순서가 되면 자동으로 옵니다. 그대로 기다려 주세요.",
        "for_admin": False,
    },
    "POI_OCCUPIED": {
        "why": "이미 다른 로봇이 이 위치에 있거나 오고 있어요.",
        "how": "비어 있는 다른 위치를 쓰거나, 그 로봇이 떠난 뒤 호출하세요.",
        "for_admin": False,
    },
    "STANDBY_MISSING": {
        "why": "이 로봇의 렉 픽업 위치(standby)가 설정돼 있지 않아요.",
        "how": "로봇 설정에서 렉 픽업 위치를 지정하세요.",
        "for_admin": True,
    },
    "POI_NOT_FOUND": {
        "why": "위치 정보를 찾을 수 없어요.",
        "how": "맵/POI 설정을 확인하세요.",
        "for_admin": True,
    },
    "PICKUP_FAILED": {
        "why": "렉 픽업에 실패했어요.",
        "how": "잭 상태와 렉 위치를 확인한 뒤 다시 호출하세요.",
        "for_admin": False,
    },
    "NEXT_OCCUPIED": {
        "why": "그 위치엔 이미 다른 로봇이 있어요.",
        "how": "비어 있는 다른 위치를 선택하세요.",
        "for_admin": False,
    },
    "NOT_AWAITING": {
        "why": "로봇이 아직 이동/처리 중이에요.",
        "how": "'도착 — 대기 중'이 뜬 뒤 다시 누르세요.",
        "for_admin": False,
    },
    "SESSION_GONE": {
        "why": "이 작업은 이미 끝났어요.",
        "how": "새로 '로봇 호출'부터 시작하세요.",
        "for_admin": False,
    },
}


# ── 렉 적재(with_rack=True) 상황 전용 오버라이드 ─────────────
# 같은 코드라도 렉을 실었을 때는 원인/조치가 달라지는 것만 여기에 둔다.
FAILURE_GUIDE_WITH_RACK: dict = {
    9: {
        "why": "렉을 실은 상태로는 지나갈 공간이 부족해요.",
        "how": "렉 놓는 자리 주변을 0.6m 이상 비우거나, 렉 위치를 여유 있는 곳으로 옮긴 뒤 다시 호출하세요.",
        "for_admin": False,
    },
}


def _normalize_key(key: Union[int, str, None]):
    """숫자 문자열은 int 로 정규화 (예: "506" -> 506). 그 외는 그대로."""
    if isinstance(key, str):
        ks = key.strip()
        if ks.lstrip("-").isdigit():
            try:
                return int(ks)
            except ValueError:
                return ks
        return ks
    return key


def guide_for(key: Union[int, str, None], *, with_rack: bool = False) -> dict:
    """실패 키 -> 안내 dict.

    존재하면 해당 항목의 복사본 + {"code": 원본키} 반환.
    없으면 GENERIC 폴백의 복사본 + {"code": 원본키} 반환.
    (원본키를 그대로 code 로 돌려주어 화면에 "코드 506" 처럼 표시)

    with_rack=True 이면 FAILURE_GUIDE_WITH_RACK 을 먼저 보고, 거기 없으면 기본 표를 쓴다.
    (기본 False 라 기존 호출부는 수정 없이 그대로 동작)
    """
    lookup = _normalize_key(key)
    entry = None
    if with_rack:
        entry = FAILURE_GUIDE_WITH_RACK.get(lookup)
    if entry is None:
        entry = FAILURE_GUIDE.get(lookup)
    if entry is None:
        out = dict(GENERIC)
    else:
        out = dict(entry)
    out["code"] = key
    return out
