"""통계 더미 데이터 시드 스크립트.

생성하는 데이터:
  1) 작업1 → 작업2 이동 성공률 비교
     - 이전(어제): 100회 중 90회 성공 / 10회 실패
     - 이후(오늘): 100회 중 93회 성공 / 7회 실패  (성공률 90% → 93%)

  2) 작업3 → 작업4 1시간 처리량 비교
     - 이전(어제 09:00~10:00): 100회 이동 완료
     - 이후(오늘  09:00~10:00): 103회 이동 완료  (처리량 +3)

사용:
  cd BackEnd
  $env:DB_NAME = "rcs_vesa_db"
  python ../scripts/seed_stats_data.py            # insert
  python ../scripts/seed_stats_data.py --clean    # 이 스크립트가 넣은 행 제거 후 재시작
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta, date

# 이 스크립트는 BackEnd/ 디렉토리에서 실행됐다고 가정 (app.* import 위해)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "BackEnd"))

from app.database import SessionLocal
from app.models.task import TaskHistory
from app.models.robot import Robot

SEED_TAG = "[seed_stats_data]"  # 나중에 제거할 때 식별용 (error_message에 넣음)

# 비교 시점
TODAY = date(2026, 6, 4)
YESTERDAY = TODAY - timedelta(days=1)


def _pick_robot(db) -> tuple[int, str]:
    """첫 활성 로봇 1대를 가짜 운영 주체로 사용."""
    r = (
        db.query(Robot)
        .filter(Robot.is_active == True)
        .order_by(Robot.id.asc())
        .first()
    )
    if r:
        return r.id, r.name
    return 1, "Robot #1"


def _row(*, route_name: str, pickup: str, dropoff: str,
         status: str, started: datetime, finished: datetime | None,
         robot_id: int, robot_name: str, err: str | None = None) -> TaskHistory:
    return TaskHistory(
        task_id=None,
        task_name=route_name,
        route_name=route_name,
        robot_id=robot_id,
        robot_name=robot_name,
        pickup_poi_name=pickup,
        dropoff_poi_name=dropoff,
        status=status,
        started_at=started,
        finished_at=finished,
        error_message=err if err else SEED_TAG,
    )


def seed(db) -> int:
    robot_id, robot_name = _pick_robot(db)
    rows: list[TaskHistory] = []

    # ── 1) 작업1 → 작업2 성공률 (이전 90/100 → 이후 93/100) ─────
    def add_success_block(target_day: date, success_n: int, fail_n: int):
        # 09:00 ~ 17:00 사이 균등 분포 (시도 1회당 4~5분)
        base = datetime.combine(target_day, datetime.min.time()).replace(hour=9)
        total = success_n + fail_n
        for i in range(total):
            started = base + timedelta(minutes=i * 5)
            finished = started + timedelta(minutes=4, seconds=30)
            is_success = i < success_n
            rows.append(_row(
                route_name="작업1→작업2",
                pickup="작업1",
                dropoff="작업2",
                status="succeeded" if is_success else "failed",
                started=started,
                finished=finished,
                robot_id=robot_id, robot_name=robot_name,
                err=None if is_success else "이동 중 이상 감지 (시드 데이터)",
            ))

    add_success_block(YESTERDAY, success_n=90,  fail_n=10)   # 이전
    add_success_block(TODAY,     success_n=93,  fail_n=7)    # 이후

    # ── 2) 작업3 → 작업4 1시간 처리량 (이전 100건/h → 이후 103건/h) ──
    def add_throughput_block(target_day: date, count: int):
        # 09:00 ~ 10:00 사이 균등 분포
        base = datetime.combine(target_day, datetime.min.time()).replace(hour=9)
        step_sec = int(3600 / count)
        for i in range(count):
            started = base + timedelta(seconds=i * step_sec)
            finished = started + timedelta(seconds=step_sec - 5)
            rows.append(_row(
                route_name="작업3→작업4",
                pickup="작업3",
                dropoff="작업4",
                status="succeeded",
                started=started,
                finished=finished,
                robot_id=robot_id, robot_name=robot_name,
            ))

    add_throughput_block(YESTERDAY, count=100)   # 이전
    add_throughput_block(TODAY,     count=103)   # 이후

    db.bulk_save_objects(rows)
    db.commit()
    return len(rows)


def clean(db) -> int:
    """이 스크립트가 만든 행만 제거 (error_message에 SEED_TAG가 있거나 같은 시드 패턴)."""
    n1 = (
        db.query(TaskHistory)
        .filter(TaskHistory.error_message == SEED_TAG)
        .delete(synchronize_session=False)
    )
    # 성공 블록의 실패 행에는 다른 에러 메시지가 들어가서 별도 필터
    n2 = (
        db.query(TaskHistory)
        .filter(
            TaskHistory.route_name.in_(["작업1→작업2", "작업3→작업4"]),
            TaskHistory.error_message == "이동 중 이상 감지 (시드 데이터)",
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return n1 + n2


def main():
    db = SessionLocal()
    try:
        if "--clean" in sys.argv:
            removed = clean(db)
            print(f"[clean] 제거: {removed}행")
            inserted = seed(db)
            print(f"[seed]  삽입: {inserted}행")
        elif "--clean-only" in sys.argv:
            removed = clean(db)
            print(f"[clean] 제거: {removed}행")
        else:
            inserted = seed(db)
            print(f"[seed]  삽입: {inserted}행")
    finally:
        db.close()


if __name__ == "__main__":
    main()
