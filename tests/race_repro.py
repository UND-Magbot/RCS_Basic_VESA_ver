# -*- coding: utf-8 -*-
"""
격리 재현: RCS 배차의 '체크→배정' 레이스 (E2/E3).
실제 dispatch_service.start_session / call_to_poi 의 구조를 그대로 모델링.
DB/백엔드/로봇은 전혀 건드리지 않음 — 순수 로직 재현.
"""
import threading, time, random

# 실제 코드의 배정 창(window): has_active_worker 체크 후 _set_worker 사이에
# _load_robot(DB read) + create_session(DB write) 이 끼어있어 수 ms 지연이 있음.
def db_work():
    time.sleep(random.uniform(0.002, 0.008))  # DB 왕복 흉내

# ─────────────────────────────────────────────────────────────
# 1) 현재 코드 구조 (락 없음) — E2: 같은 로봇 이중배정
# ─────────────────────────────────────────────────────────────
class CurrentImpl:
    def __init__(self):
        self._workers = {}
        self._workers_lock = threading.Lock()   # 실제도 dict 개별접근만 보호
        self.assign_count = {}                   # robot_id -> 몇 번 배정됐나

    def has_active_worker(self, robot_id):
        with self._workers_lock:
            return robot_id in self._workers

    def _set_worker(self, robot_id):
        with self._workers_lock:
            self._workers[robot_id] = True

    def start_session(self, robot_id):
        # === 실제 start_session 구조 그대로 ===
        if self.has_active_worker(robot_id):     # 체크
            return False
        db_work()                                # _load_robot + create_session (창)
        self._set_worker(robot_id)               # 배정(세팅)
        self.assign_count[robot_id] = self.assign_count.get(robot_id, 0) + 1
        return True

# ─────────────────────────────────────────────────────────────
# 2) 수정본 (배정 전체를 하나의 락으로 원자화)
# ─────────────────────────────────────────────────────────────
class FixedImpl:
    def __init__(self):
        self._workers = {}
        self._assign_lock = threading.Lock()     # 체크+배정 통째로 보호
        self.assign_count = {}

    def start_session(self, robot_id):
        with self._assign_lock:                  # ← 원자화
            if robot_id in self._workers:
                return False
            db_work()
            self._workers[robot_id] = True
            self.assign_count[robot_id] = self.assign_count.get(robot_id, 0) + 1
            return True

# ─────────────────────────────────────────────────────────────
# 테스트 러너: N개 스레드가 '동시에' 같은 로봇 배정 시도
# ─────────────────────────────────────────────────────────────
def hammer(impl, robot_id, n_threads):
    barrier = threading.Barrier(n_threads)       # 동시에 출발(버튼 동시 누름 흉내)
    results = []
    def worker():
        barrier.wait()
        results.append(impl.start_session(robot_id))
    ts = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in ts: t.start()
    for t in ts: t.join()
    return sum(1 for r in results if r)           # 성공(=배정됨) 횟수

def run_trials(Impl, label, trials=200, threads=8):
    bad = 0
    worst = 1
    for _ in range(trials):
        impl = Impl()
        succ = hammer(impl, robot_id=9, n_threads=threads)
        if succ > 1:            # 로봇 1대인데 2번 이상 배정 = 이중배정(버그)
            bad += 1
            worst = max(worst, succ)
    print(f"  [{label}] {trials}회 중 이중배정 발생: {bad}회  (최악: 로봇1대에 {worst}개 워커)")
    return bad

print("=== E2 재현: 같은 순간 여러 태블릿이 호출 → 같은 유휴로봇 배정 ===")
print("  (성공=1 이어야 정상. 2 이상이면 로봇 1대에 워커 여러 개 = 이중배정)")
run_trials(CurrentImpl, "현재코드(락없음)")
run_trials(FixedImpl,   "수정본(배정 원자화)")
