"""배차 동시성/대기열 테스트 (E1·E2·E3) — 실제 dispatch_service 를 검증한다.

실행: python tests/test_dispatch_race.py

기존 race_repro.py 는 로직을 흉내낸 '격리 모델'이라 실제 코드가 고쳐졌는지는 알 수 없다.
이 테스트는 **진짜 app.services.dispatch_service 를 import** 하고, DB/로봇/스레드에
닿는 함수만 가짜로 바꿔서 배정 경로를 그대로 태운다.

검증:
  E2) 여러 스레드가 동시에 호출 → 로봇 1대에 워커가 2개 붙지 않는다
  E3) 같은 POI 를 동시에 호출 → 딱 1건만 성공한다
  E1) 로봇이 없으면 거부가 아니라 대기열에 등록되고, 워커가 끝나면 FIFO 로 자동 배정된다
      + TTL 만료 폐기 / 중복 등록 거부 / 취소
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "BackEnd"))

# Windows 콘솔 기본 인코딩(cp949)에서 한글/기호 출력이 깨지지 않도록
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.services import dispatch_service as ds  # noqa: E402

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


class FakeRobot:
    def __init__(self, rid):
        self.id = rid
        self.name = f"robot{rid}"
        self.ip_address = f"10.0.0.{rid}"
        self.area_id = "26"
        self.is_active = True


class Harness:
    """DB/로봇/스레드에 닿는 부분만 가짜로 교체. 배정 로직 본체는 진짜를 쓴다."""

    def __init__(self, robot_ids, db_delay=0.004):
        self.robots = {r: FakeRobot(r) for r in robot_ids}
        self.db_delay = db_delay          # 실제 배정 창(_load_robot + create_session) 흉내
        self.assign_count = {}            # robot_id -> 누적 배정 횟수(재사용 포함)
        # 진짜 이중배정 카운터: "이미 살아있는 워커가 있는 로봇"에 세션이 또 만들어진 횟수.
        # assign_count 는 워커가 끝난 뒤의 정상 재배정도 세므로 이 지표로 판정해야 한다.
        self.double_assign = 0
        self.sessions = []                # (robot_id, poi_id)
        self.session_seq = 1000
        self.occupied_pois = set()        # 세션이 잡고 있는 POI (DB 대체)
        self.lock = threading.Lock()
        # 대기열은 DB(dispatch_reservations)에 저장된다 → 그 테이블을 흉내낸 저장소.
        # keep_reservations=True 로 Harness 를 다시 열면 "백엔드 재시작" 을 재현할 수 있다.
        self.reservations = []            # [{id, poi_id, with_rack, status, created_at}]
        self.res_seq = 0

    # ── DB(dispatch_reservations) 흉내 ──
    def _waiting(self):
        return [r for r in self.reservations if r["status"] == "waiting"]

    def __enter__(self):
        self._orig = {k: getattr(ds, k) for k in (
            "_load_robot", "_load_poi", "find_available_robot",
            "get_session_at_poi", "get_session_heading_to_poi",
            "_area_id_of_poi", "safe_thread", "SessionLocal",
        )}
        for fn in ("create_session", "list_waiting_reservations", "create_reservation",
                   "cancel_reservation", "mark_reservation", "waiting_reservation_position"):
            self._orig[fn] = getattr(ds.dispatch_crud, fn)

        def fake_load_robot(rid):
            time.sleep(self.db_delay)          # ← 여기가 예전에 레이스가 나던 창
            return self.robots.get(rid)

        def fake_find_available(area_id=None):
            for rid in sorted(self.robots):
                if not ds.has_active_worker(rid):
                    return self.robots[rid]
            return None

        class FakeSession:
            def __init__(s, sid):
                s.id = sid

        def fake_create_session(db, robot_id, first_poi_id, with_rack=True):
            time.sleep(self.db_delay)
            # 세션이 만들어지는 시점에 이미 워커가 살아있다면 = 락이 뚫린 것
            already = ds.has_active_worker(robot_id)
            with self.lock:
                if already:
                    self.double_assign += 1
                self.session_seq += 1
                self.assign_count[robot_id] = self.assign_count.get(robot_id, 0) + 1
                self.sessions.append((robot_id, first_poi_id))
                self.occupied_pois.add(first_poi_id)
                return FakeSession(self.session_seq)

        class FakeThread:
            """start() 해도 아무것도 안 하는 스레드 — 워커는 '살아있는' 것으로 보이게 한다."""
            def __init__(s, *a, **k):
                s._alive = True

            def start(s):
                pass

            def is_alive(s):
                return s._alive

        # ── dispatch_reservations 테이블 흉내 (DB 대신 메모리 리스트) ──
        class Res(dict):
            __getattr__ = dict.get

        def fake_list_waiting(db):
            return [Res(r) for r in sorted(self._waiting(), key=lambda x: (x["created_at"], x["id"]))]

        def fake_create_res(db, poi_id, with_rack=True):
            for r in self._waiting():
                if r["poi_id"] == poi_id:
                    return Res(r), False          # 중복 등록 방지
            self.res_seq += 1
            r = {"id": self.res_seq, "poi_id": poi_id, "with_rack": with_rack,
                 "status": "waiting", "created_at": datetime.utcnow()}
            self.reservations.append(r)
            return Res(r), True

        def fake_cancel_res(db, poi_id):
            for r in self._waiting():
                if r["poi_id"] == poi_id:
                    r["status"] = "cancelled"
                    return True
            return False

        def fake_mark_res(db, res_id, status):
            for r in self.reservations:
                if r["id"] == res_id:
                    r["status"] = status

        def fake_res_position(db, poi_id):
            for i, r in enumerate(sorted(self._waiting(), key=lambda x: (x["created_at"], x["id"]))):
                if r["poi_id"] == poi_id:
                    return i + 1
            return None

        class FakeDb:
            def close(s):
                pass

        ds._load_robot = fake_load_robot
        ds._load_poi = lambda pid: {"name": f"P{pid}", "x": 0.0, "y": 0.0, "ori": 0.0}
        ds.find_available_robot = fake_find_available
        ds.get_session_at_poi = lambda pid: (pid in self.occupied_pois) or None
        ds.get_session_heading_to_poi = lambda pid: None
        ds._area_id_of_poi = lambda pid: 26
        ds.SessionLocal = lambda: FakeDb()
        ds.dispatch_crud.list_waiting_reservations = fake_list_waiting
        ds.dispatch_crud.create_reservation = fake_create_res
        ds.dispatch_crud.cancel_reservation = fake_cancel_res
        ds.dispatch_crud.mark_reservation = fake_mark_res
        ds.dispatch_crud.waiting_reservation_position = fake_res_position
        ds.safe_thread = lambda **kw: FakeThread()
        ds.dispatch_crud.create_session = fake_create_session
        # 워커 레지스트리(메모리)만 초기화. 예약은 DB 쪽이라 여기서 지우지 않는다.
        with ds._workers_lock:
            ds._workers.clear()
        return self

    def __exit__(self, *exc):
        crud_fns = ("create_session", "list_waiting_reservations", "create_reservation",
                    "cancel_reservation", "mark_reservation", "waiting_reservation_position")
        for k, v in self._orig.items():
            if k in crud_fns:
                setattr(ds.dispatch_crud, k, v)
            else:
                setattr(ds, k, v)
        with ds._workers_lock:
            ds._workers.clear()
        return False

    def finish_worker(self, robot_id):
        """워커가 끝난 상황 재현 — _worker_loop 의 finally 와 같은 순서."""
        ds._remove_worker(robot_id)
        ds._drain_pending_queue()


def run_concurrent(fn, n):
    """n개 스레드를 동시에 출발시킨다 (배리어로 타이밍을 맞춰 레이스를 최대화)."""
    barrier = threading.Barrier(n)
    results = [None] * n

    def runner(i):
        barrier.wait()
        results[i] = fn(i)

    ts = [threading.Thread(target=runner, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return results


ROUNDS = 25
THREADS = 8

print(f"[E2] 로봇 이중배정 — {THREADS}스레드 동시 start_session × {ROUNDS}회")
dup_rounds = 0
for _ in range(ROUNDS):
    with Harness([1]) as h:
        run_concurrent(lambda i: ds.start_session(1, 900 + i), THREADS)
        if h.double_assign or h.assign_count.get(1, 0) > 1:
            dup_rounds += 1
check(f"이중배정 0회 (실제 {dup_rounds}회)", dup_rounds == 0, f"{dup_rounds}/{ROUNDS} 라운드에서 발생")

print(f"\n[E3] 같은 POI 동시 호출 — {THREADS}스레드 × {ROUNDS}회 (로봇 {THREADS}대 여유)")
multi_rounds = 0
for _ in range(ROUNDS):
    with Harness(list(range(1, THREADS + 1))) as h:
        res = run_concurrent(lambda i: ds.call_to_poi(500, with_rack=False), THREADS)
        ok_count = sum(1 for r in res if r[0])
        if ok_count != 1:
            multi_rounds += 1
check(f"항상 1건만 성공 (어긋난 라운드 {multi_rounds}회)", multi_rounds == 0,
      f"{multi_rounds}/{ROUNDS}")

print("\n[E3-2] 서로 다른 POI 동시 호출은 모두 성공해야 한다 (과잉 차단 방지)")
with Harness([1, 2, 3, 4]) as h:
    res = run_concurrent(lambda i: ds.call_to_poi(600 + i, with_rack=False), 4)
    ok_count = sum(1 for r in res if r[0])
check("4건 전부 성공", ok_count == 4, f"성공 {ok_count}건: {res}")
check("로봇 4대에 1건씩 배정", sorted(h.assign_count.values()) == [1, 1, 1, 1], str(h.assign_count))

print("\n[E1] 로봇이 없으면 거부가 아니라 대기열 등록")
with Harness([1]) as h:
    ok1, key1, _ = ds.call_to_poi(701, with_rack=True)     # 로봇 1대 → 배정
    ok2, key2, _ = ds.call_to_poi(702, with_rack=True)     # 없음 → 대기열
    ok3, key3, _ = ds.call_to_poi(703, with_rack=False)    # 없음 → 대기열
    check("첫 호출은 배정 성공", ok1 is True and key1 is None, f"{ok1},{key1}")
    check("둘째는 QUEUED", ok2 is False and key2 == "QUEUED", f"{ok2},{key2}")
    check("셋째도 QUEUED", key3 == "QUEUED", str(key3))
    check("대기열 2건", ds.pending_count() == 2, str(ds.pending_count()))
    check("순번 조회 702=1, 703=2",
          ds.pending_position(702) == 1 and ds.pending_position(703) == 2,
          f"{ds.pending_position(702)}, {ds.pending_position(703)}")
    check("대기 안 한 POI 는 None", ds.pending_position(999) is None)

    # 같은 POI 재호출 → 중복 등록 거부
    ok4, key4, _ = ds.call_to_poi(702, with_rack=True)
    check("중복 등록은 ALREADY_QUEUED", key4 == "ALREADY_QUEUED", str(key4))
    check("대기열 여전히 2건", ds.pending_count() == 2, str(ds.pending_count()))

    # 워커 종료 → FIFO 로 자동 배정
    h.finish_worker(1)
    check("종료 후 702 가 배정됨(FIFO)", (1, 702) in h.sessions, str(h.sessions))
    check("703 은 아직 대기(로봇 1대뿐)", ds.pending_position(703) == 1,
          f"pending={ds.pending_count()}, pos={ds.pending_position(703)}")

    # 한 번 더 종료 → 703 배정
    h.finish_worker(1)
    check("다음 종료에서 703 배정", (1, 703) in h.sessions, str(h.sessions))
    check("대기열 비었음", ds.pending_count() == 0, str(ds.pending_count()))

print("\n[E1-2] 대기 취소")
with Harness([1]) as h:
    ds.call_to_poi(801, with_rack=True)      # 배정
    ds.call_to_poi(802, with_rack=True)      # 대기
    check("취소 성공", ds.cancel_pending(802) is True)
    check("대기열 비었음", ds.pending_count() == 0)
    check("없는 항목 취소는 False", ds.cancel_pending(802) is False)

print("\n[E1-3] TTL 만료 항목은 폐기")
with Harness([1]) as h:
    ds.call_to_poi(901, with_rack=True)      # 배정
    ds.call_to_poi(902, with_rack=True)      # 대기
    h.reservations[0]["created_at"] = datetime.utcnow() - timedelta(seconds=ds.PENDING_TTL_SEC + 10)
    h.finish_worker(1)
    check("만료 항목 폐기됨", ds.pending_count() == 0, str(ds.pending_count()))
    check("902 는 배정되지 않음", (1, 902) not in h.sessions, str(h.sessions))

print("\n[E1-4] 이미 세션이 생긴 POI 는 대기열에서 제거")
with Harness([1, 2]) as h:
    ds.call_to_poi(910, with_rack=True)      # robot1 배정
    ds.call_to_poi(911, with_rack=True)      # robot2 배정
    # 이미 로봇이 간 POI 에 대기 항목이 남아 있는 상황을 만든다
    h.res_seq += 1
    h.reservations.append({"id": h.res_seq, "poi_id": 910, "with_rack": True,
                           "status": "waiting", "created_at": datetime.utcnow()})
    h.finish_worker(1)
    check("이미 점유된 POI 항목 제거", ds.pending_position(910) is None,
          f"pending={ds.pending_count()}")

print("\n[E1-5] ⭐ 백엔드가 재시작돼도 대기열이 남아있다 (DB 저장의 핵심 이유)")
saved = None
with Harness([1]) as h:
    ds.call_to_poi(950, with_rack=True)      # robot1 배정
    ds.call_to_poi(951, with_rack=True)      # 대기열 등록
    check("대기 1건 등록", ds.pending_count() == 1, str(ds.pending_count()))
    saved = h.reservations                    # DB 는 백엔드가 죽어도 남는다

# 새 Harness = 백엔드 재시작(메모리 초기화). DB 내용(saved)만 그대로 이어받는다.
with Harness([1]) as h2:
    h2.reservations = saved
    h2.res_seq = max((r["id"] for r in saved), default=0)
    check("재시작 후에도 대기 1건 살아있음", ds.pending_count() == 1, str(ds.pending_count()))
    check("순번도 유지", ds.pending_position(951) == 1, str(ds.pending_position(951)))
    # 재시작 후 로봇이 놀고 있으므로 워커 종료 없이도 다음 배차 기회에 배정된다
    ds._drain_pending_queue()
    check("재시작 후 자동 배정됨", (1, 951) in h2.sessions, str(h2.sessions))
    check("대기열 비었음", ds.pending_count() == 0, str(ds.pending_count()))

print("\n[통합] 대기열 등록과 워커 종료가 동시에 일어나도 이중배정 없음")
dup = 0
for _ in range(ROUNDS):
    with Harness([1, 2]) as h:
        ds.call_to_poi(920, with_rack=True)
        ds.call_to_poi(921, with_rack=True)      # 로봇 2대 모두 사용
        ds.call_to_poi(922, with_rack=True)      # 대기열

        def worker_done(i):
            if i == 0:
                h.finish_worker(1)
            else:
                ds.call_to_poi(923, with_rack=True)
            return None

        run_concurrent(worker_done, 2)
        # 워커가 끝난 뒤의 재배정은 정상이므로 double_assign(살아있는 워커에 덧배정)으로 판정
        if h.double_assign:
            dup += 1
check(f"이중배정 0회 (실제 {dup}회)", dup == 0, f"{dup}/{ROUNDS}")

print(f"\n{'='*50}\n결과: {_passed} passed, {_failed} failed\n{'='*50}")
sys.exit(1 if _failed else 0)
