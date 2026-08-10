"""failure_guide 단위 테스트 — 라이브 DB/로봇 미접촉, 순수 매핑 검증.

실행: python tests/test_failure_guide.py
목적:
  1) 기존 매핑(506/501/... , NO_ROBOT 등)이 그대로 살아있는지 회귀 확인
  2) 2026-08-10 추가된 with_rack 오버라이드(9번)가 정확히 동작하는지
  3) 미지 코드가 GENERIC 으로 폴백하고 code 는 원본을 유지하는지
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "BackEnd"))

# Windows 콘솔 기본 인코딩(cp949)에서 한글/기호 출력이 깨지지 않도록
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.services.failure_guide import (  # noqa: E402
    FAILURE_GUIDE,
    FAILURE_GUIDE_WITH_RACK,
    GENERIC,
    guide_for,
)

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


print("[1] 기존 매핑 회귀 — 모든 키가 why/how/for_admin 을 갖고 code 가 원본키로 나온다")
for key in FAILURE_GUIDE:
    g = guide_for(key)
    check(
        f"guide_for({key!r})",
        all(k in g for k in ("why", "how", "for_admin", "code")) and g["code"] == key,
        str(g),
    )

print("\n[2] 대표 코드 문구 확인 (현장에서 실제로 본 것들)")
check("506 = 렉 들고 있음", "렉을 들고" in guide_for(506)["why"], guide_for(506)["why"])
check("501 = 렉 없음", "렉이 없어요" in guide_for(501)["why"], guide_for(501)["why"])
check("NO_ROBOT", "쓸 수 있는 로봇이 없" in guide_for("NO_ROBOT")["why"])
check("STANDBY_MISSING 은 관리자용", guide_for("STANDBY_MISSING")["for_admin"] is True)
check("506 은 관리자용 아님", guide_for(506)["for_admin"] is False)

print("\n[3] 숫자 문자열 정규화 — '506' 과 506 은 같은 문구, code 는 원본 유지")
check("문구 동일", guide_for("506")["why"] == guide_for(506)["why"])
check("code 는 원본 문자열", guide_for("506")["code"] == "506")

print("\n[4] with_rack 오버라이드 (신규)")
g_plain = guide_for(9)
g_rack = guide_for(9, with_rack=True)
check("렉 없이 9 = 기존 문구", g_plain["why"] == FAILURE_GUIDE[9]["why"], g_plain["why"])
check("렉 포함 9 = 공간 부족 문구", "공간이 부족" in g_rack["why"], g_rack["why"])
check("렉 포함 9 조치에 0.6m 안내", "0.6m" in g_rack["how"], g_rack["how"])
check("두 문구가 실제로 다름", g_plain["why"] != g_rack["why"])
check("code 는 둘 다 9", g_plain["code"] == 9 and g_rack["code"] == 9)

print("\n[5] 오버라이드에 없는 코드는 with_rack=True 여도 기본 표를 쓴다")
for key in (506, 501, 3, "NO_ROBOT"):
    check(
        f"{key!r} 폴스루",
        guide_for(key, with_rack=True)["why"] == guide_for(key)["why"],
    )
check(
    "오버라이드 테이블은 9번만 (늘어나면 이 테스트도 갱신할 것)",
    set(FAILURE_GUIDE_WITH_RACK) == {9},
    str(set(FAILURE_GUIDE_WITH_RACK)),
)

print("\n[6] 미지 코드 → GENERIC 폴백, code 는 원본 유지")
for unknown in (999, "WHATEVER", None, -1):
    g = guide_for(unknown)
    check(f"{unknown!r} 폴백", g["why"] == GENERIC["why"] and g["code"] == unknown, str(g))
check("미지 코드 + with_rack 도 GENERIC", guide_for(999, with_rack=True)["why"] == GENERIC["why"])

print("\n[7] 반환값은 복사본 — 호출부가 고쳐도 원본 테이블이 안 망가진다")
g = guide_for(506)
g["why"] = "오염"
check("원본 테이블 무사", FAILURE_GUIDE[506]["why"] != "오염")
check("다음 호출도 무사", guide_for(506)["why"] != "오염")

print(f"\n{'='*50}\n결과: {_passed} passed, {_failed} failed\n{'='*50}")
sys.exit(1 if _failed else 0)
