"""safe_move(stop_on_repeated_fail=N) 모의 테스트 — 라이브 로봇 미접촉.

실행: python tests/test_safe_move_stop.py

검증 목표:
  A) 같은 실패코드가 N회 연속이면 조기 중단한다 (렉 적재 시 9=calculation_failed 무한 재시도 방지)
  B) 단발 실패 후 성공하는 상황(사람이 잠깐 지나감)은 그대로 회복한다 — 조기 중단으로 죽지 않는다
  C) 실패코드가 매번 다르면 조기 중단하지 않는다
  D) fail_reason 없는 실패(timeout)는 연속 카운트를 리셋한다
  E) stop_on_repeated_fail=None(기본)이면 기존 동작 그대로 — max_attempts 까지 재시도

실제 로봇 통신 함수(create_move/wait_move)만 가짜로 바꾸고 safe_move 본체는 진짜를 쓴다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "BackEnd"))

# Windows 콘솔 기본 인코딩(cp949)에서 한글/기호 출력이 깨지지 않도록
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.services import jack_service  # noqa: E402

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


class Harness:
    """create_move / wait_move / 대기 / 상태보고를 가짜로 교체."""

    def __init__(self, results):
        self.results = list(results)   # wait_move 가 순서대로 돌려줄 응답
        self.calls = 0                 # create_move 호출 횟수 = 이동 시도 횟수
        self.slept = 0.0               # 실제로는 자지 않고 누적만

    def __enter__(self):
        self._orig = {
            "create_move": jack_service.create_move,
            "wait_move": jack_service.wait_move,
            "update_job_status": jack_service.update_job_status,
            "_check_stop": jack_service._check_stop,
            "_wait_if_paused": jack_service._wait_if_paused,
            "_interruptible_sleep": jack_service._interruptible_sleep,
        }

        def fake_create_move(ip, move_type, x, y, ori=0, **extra):
            self.calls += 1
            return 1000 + self.calls

        def fake_wait_move(ip, move_id, timeout=None):
            idx = self.calls - 1
            if idx < len(self.results):
                return dict(self.results[idx])
            return dict(self.results[-1])   # 목록 소진 후엔 마지막 결과가 계속 반복

        jack_service.create_move = fake_create_move
        jack_service.wait_move = fake_wait_move
        jack_service.update_job_status = lambda *a, **k: None
        jack_service._check_stop = lambda ip: None
        jack_service._wait_if_paused = lambda ip: None
        jack_service._interruptible_sleep = lambda ip, sec: setattr(self, "slept", self.slept + sec)
        return self

    def __exit__(self, *exc):
        for k, v in self._orig.items():
            setattr(jack_service, k, v)
        return False


FAIL9 = {"state": "failed", "fail_reason": 9, "fail_message": "calculation_failed"}
FAIL506 = {"state": "failed", "fail_reason": 506, "fail_message": "jack_in_up_state"}
FAIL3 = {"state": "failed", "fail_reason": 3, "fail_message": "starting_point_out_of_map"}
TIMEOUT = {"state": "timeout", "fail_message": "timed out"}
OK = {"state": "succeeded"}


print("[A] 같은 코드(9) 연속 → 3회에서 조기 중단  ※ 8/7 현장 재현 (실제로는 73회 반복됐음)")
with Harness([FAIL9] * 100) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("3회만 시도하고 멈춤", h.calls == 3, f"실제 {h.calls}회")
check("마지막 실패 결과를 반환", r.get("fail_reason") == 9, str(r))
check("대기시간 2회분(=10초)만 소모", h.slept == 10.0, f"{h.slept}초")

print("\n[B] 단발 실패 후 성공 → 조기 중단 없이 회복 (사람이 잠깐 지나간 경우)")
with Harness([FAIL9, OK]) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("2회째에 성공", h.calls == 2 and r["state"] == "succeeded", f"{h.calls}회 / {r}")

print("\n[B-2] 2회 연속까지는 버티고 3회째 성공하면 회복 (임계값 직전)")
with Harness([FAIL9, FAIL9, OK]) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("3회째 성공", h.calls == 3 and r["state"] == "succeeded", f"{h.calls}회 / {r}")

print("\n[C] 실패코드가 매번 다르면 조기 중단하지 않는다")
with Harness([FAIL9, FAIL506, FAIL3, OK]) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("4회째 성공까지 감", h.calls == 4 and r["state"] == "succeeded", f"{h.calls}회 / {r}")

print("\n[D] fail_reason 없는 실패(timeout)가 끼면 연속 카운트가 리셋된다")
with Harness([FAIL9, FAIL9, TIMEOUT, FAIL9, FAIL9, OK]) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("6회째 성공 (중간 리셋 덕분에 안 죽음)", h.calls == 6 and r["state"] == "succeeded", f"{h.calls}회 / {r}")

print("\n[D-2] 리셋 후 다시 3연속이면 그때는 중단한다")
with Harness([FAIL9, FAIL9, TIMEOUT, FAIL9, FAIL9, FAIL9, OK]) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("6회에서 중단 (7회째 성공까지 못 감)", h.calls == 6 and r["state"] == "failed", f"{h.calls}회 / {r}")

print("\n[E] 기존 동작 불변 — stop_on_repeated_fail 미지정이면 max_attempts 까지 재시도")
with Harness([FAIL9] * 100) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, max_attempts=10)
check("10회 전부 시도", h.calls == 10, f"실제 {h.calls}회")
check("기본 상한이 200회 그대로", jack_service.SAFE_MOVE_DEFAULT_MAX_ATTEMPTS == 200,
      str(jack_service.SAFE_MOVE_DEFAULT_MAX_ATTEMPTS))

print("\n[E-2] 픽업 경로(max_attempts=2)는 8/5 설정 그대로 동작")
with Harness([FAIL506] * 10) as h:
    r = jack_service.safe_move("1.2.3.4", "align_with_rack", 1, 2, 0, max_attempts=2)
check("2회만 시도", h.calls == 2, f"실제 {h.calls}회")
check("506 코드 전달됨 (안내 카드용)", r.get("fail_reason") == 506, str(r))

print("\n[F] 성공/취소는 즉시 반환 (조기중단 로직이 정상 경로를 건드리지 않음)")
with Harness([OK]) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("1회 성공", h.calls == 1 and r["state"] == "succeeded", f"{h.calls}회")
with Harness([{"state": "cancelled"}]) as h:
    r = jack_service.safe_move("1.2.3.4", "standard", 1, 2, 0, stop_on_repeated_fail=3)
check("cancelled 즉시 반환", h.calls == 1 and r["state"] == "cancelled", f"{h.calls}회")

print(f"\n{'='*50}\n결과: {_passed} passed, {_failed} failed\n{'='*50}")
sys.exit(1 if _failed else 0)
