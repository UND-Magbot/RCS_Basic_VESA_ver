import logging

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import user, robot, auth, map, alarm_log, activity_log, backup, log, jack_test, task
from app.services.scheduler import init_scheduler, shutdown_scheduler
# 데드락 감지/양보 기능 비활성화 — 좁은 통로 없는 사이트.
# 다시 켜려면 아래 import 와 lifespan 의 start/stop 주석을 해제하세요.
# from app.services import deadlock_monitor

# 모델 import (테이블 메타데이터 등록용)
import app.models  # noqa: F401


def _apply_saved_speeds():
    """서버 시작 시 DB에 저장된 속도를 로봇에 적용"""
    import requests
    from app.database import SessionLocal
    from app.models.robot import Robot
    db = SessionLocal()
    try:
        robots = db.query(Robot).filter(Robot.is_active == True, Robot.ip_address != None, Robot.max_speed != None).all()
        for r in robots:
            try:
                requests.post(
                    f"http://{r.ip_address}:8090/robot-params",
                    json={"/wheel_control/max_forward_velocity": r.max_speed},
                    timeout=3,
                )
                logging.getLogger(__name__).info(f"[startup] 속도 적용: {r.name} → {r.max_speed} m/s")
            except Exception:
                logging.getLogger(__name__).warning(f"[startup] 속도 적용 실패: {r.name} ({r.ip_address})")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(application: FastAPI):
    log = logging.getLogger(__name__)
    log.info("[startup] init_db...")
    init_db()
    log.info("[startup] init_db done")
    from app.services.thread_utils import safe_thread
    safe_thread(target=_apply_saved_speeds, name="apply-saved-speeds").start()
    log.info("[startup] init_scheduler...")
    init_scheduler()
    log.info("[startup] init_scheduler done")
    # 데드락 자동 감지/양보 기능 비활성화 — 사이트에 좁은 통로 없어 양보 불필요
    # 다시 켜려면 아래 두 줄 주석 해제
    # log.info("[startup] deadlock_monitor.start...")
    # deadlock_monitor.start()
    yield
    # deadlock_monitor.stop()
    shutdown_scheduler()


app = FastAPI(
    title="RCS API",
    description="Robot Control System — 사용자 / 로봇 관리 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (프론트 연결용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(auth.router)
app.include_router(user.router)
app.include_router(robot.router)
app.include_router(map.router)
app.include_router(alarm_log.router)
app.include_router(activity_log.router)
app.include_router(backup.router)
app.include_router(log.router)
app.include_router(jack_test.router)
app.include_router(task.router)


# 정적 파일 서빙 (맵 이미지 등)
_static_dir = Path(__file__).resolve().parent.parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/ping")
def ping():
    return {"message": "pong"}


@app.get("/health")
def health_check():
    """헬스체크 — DB 연결 확인"""
    from app.database import engine
    from sqlalchemy import text as sa_text
    import time

    result = {"status": "ok", "timestamp": time.time(), "checks": {}}

    try:
        with engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        result["checks"]["database"] = "ok"
    except Exception as e:
        result["checks"]["database"] = f"error: {e}"
        result["status"] = "degraded"

    return result
