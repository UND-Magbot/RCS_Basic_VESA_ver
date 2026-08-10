"""is_charging() 모의 테스트 — 라이브 로봇 미접촉.

실행: python tests/test_is_charging.py

검증 목표:
  A) power_supply_status 별 판정 (charging/full=True, discharging/not_charging=False)
  B) 조회 실패/오프라인/필드 없음 → None (호출부는 fail-open 해야 함)
  C) /planning_state 를 요청하지 않은 호출은 배터리 토픽 1건에 즉시 종료 (3초 낭비 없음)
  D) 기존 fetch_robot_live 경로의 break 조건은 그대로 (동작 불변)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "BackEnd"))

# Windows 콘솔 기본 인코딩(cp949)에서 한글/기호 출력이 깨지지 않도록
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.robot_api import robot_live_service as rls  # noqa: E402

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


def with_ws(collected, error=None):
    """_collect_ws_topics 를 가짜로 교체하는 컨텍스트."""

    class _Ctx:
        def __enter__(self):
            self._orig = rls._collect_ws_topics
            rls._collect_ws_topics = lambda ip, topics, timeout_sec=None: (collected, error)
            return self

        def __exit__(self, *exc):
            rls._collect_ws_topics = self._orig
            return False

    return _Ctx()


def battery(status):
    return {"/detailed_battery_state": {"power_supply_status": status}}


print("[A] power_supply_status 별 판정")
for status, expected in [
    ("charging", True),
    ("full", True),
    ("Charging", True),        # 대소문자 무시
    ("discharging", False),
    ("not_charging", False),
]:
    with with_ws(battery(status)):
        got = rls.is_charging("1.2.3.4")
    check(f"{status!r} -> {expected}", got is expected, f"실제 {got}")

print("\n[B] 판정 불가 → None (호출부가 막으면 안 되는 케이스)")
with with_ws({}, error="connection refused"):
    check("WS 실패", rls.is_charging("1.2.3.4") is None)
with with_ws({}):
    check("빈 응답", rls.is_charging("1.2.3.4") is None)
with with_ws({"/battery_state": {}}):
    check("필드 없음", rls.is_charging("1.2.3.4") is None)
with with_ws({"/battery_state": {"power_supply_status": "unknown"}}):
    check("알 수 없는 값", rls.is_charging("1.2.3.4") is None)

print("\n[B-2] /battery_state 폴백도 읽는다")
with with_ws({"/battery_state": {"power_supply_status": "charging"}}):
    check("detailed 없어도 판정", rls.is_charging("1.2.3.4") is True)

print("\n[C] 배터리 토픽만 요청하면 1건 수신 즉시 종료 (deadline 까지 안 기다림)")


class FakeWs:
    """battery 패킷 1건만 주고, 그 뒤로는 계속 다른 토픽을 흘린다."""

    def __init__(self):
        self.recv_count = 0
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)

    def recv(self):
        self.recv_count += 1
        if self.recv_count == 1:
            return '{"topic": "/battery_state", "power_supply_status": "charging"}'
        return '{"topic": "/other"}'

    def close(self):
        pass


fake = FakeWs()
_orig_conn = rls.create_connection
rls.create_connection = lambda url, timeout=None: fake
try:
    got = rls.is_charging("1.2.3.4", timeout_sec=3)
finally:
    rls.create_connection = _orig_conn
check("충전 판정", got is True, str(got))
check("recv 1회로 종료 (3초 대기 없음)", fake.recv_count == 1, f"실제 {fake.recv_count}회")
check("배터리 토픽 2개만 구독", len(fake.sent) == 2, f"실제 {len(fake.sent)}개: {fake.sent}")

print("\n[D] 기존 경로(WS_TOPICS) 는 동작 불변 — planning_state + battery 둘 다 와야 종료")


class FakeWs2:
    def __init__(self):
        self.recv_count = 0
        self.seq = [
            '{"topic": "/battery_state", "power_supply_status": "discharging"}',
            '{"topic": "/other"}',
            '{"topic": "/planning_state", "move_state": "idle"}',
        ]

    def send(self, payload):
        pass

    def recv(self):
        i = self.recv_count
        self.recv_count += 1
        return self.seq[i] if i < len(self.seq) else '{"topic": "/other"}'

    def close(self):
        pass


fake2 = FakeWs2()
rls.create_connection = lambda url, timeout=None: fake2
try:
    collected, err = rls._collect_ws_topics("1.2.3.4", rls.WS_TOPICS, timeout_sec=3)
finally:
    rls.create_connection = _orig_conn
check("battery 만 왔을 땐 안 끝나고 planning_state 까지 기다림",
      fake2.recv_count == 3, f"실제 recv {fake2.recv_count}회")
check("두 토픽 모두 수집됨",
      "/planning_state" in collected and "/battery_state" in collected, str(collected.keys()))
check("WS_TOPICS 구성 그대로", rls.WS_TOPICS ==
      ["/planning_state", "/detailed_battery_state", "/battery_state"], str(rls.WS_TOPICS))

print(f"\n{'='*50}\n결과: {_passed} passed, {_failed} failed\n{'='*50}")
sys.exit(1 if _failed else 0)
