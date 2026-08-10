"""
Jack 작업 테스트 라우터
- 로봇이 POI로 이동 → 랙 정렬 → 잭 업 → 목표 POI로 이동 → 잭 다운
"""
import logging
import time
import threading

import requests

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.map import MapPOI
from app.crud.activity_log import log_activity
from app.services.jack_service import (
    robot_get, robot_patch, run_jack_job, cancel_current_move,
)
from app.services import rack_detect_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jack-test", tags=["Jack 테스트"])


# ── 작업 상태 관리 (인메모리) ──

_jobs: dict[str, dict] = {}


# ── 요청/응답 스키마 ──

class JackTestRequest(BaseModel):
    robot_ip: str
    pickup_poi_name: str
    dropoff_poi_name: str


class JackTestStatus(BaseModel):
    job_id: str
    status: str
    message: str
    detail: dict | None = None


# ── 백그라운드 잭 작업 ──

def _run_jack_job_wrapper(job_id: str, ip: str, pickup: dict, dropoff: dict, standby: dict | None = None):
    job = _jobs[job_id]

    def on_status(status, message):
        job["status"] = status
        job["message"] = message

    result = run_jack_job(ip, pickup, dropoff, standby, on_status=on_status)

    if result["status"] == "done":
        log_activity("robot", "jack_test",
                     result["message"] + f" (IP: {ip})",
                     source="jack_test")


def _db_poi_to_world(poi) -> dict:
    return {"name": poi.name, "x": poi.world_x, "y": poi.world_y, "ori": poi.angle or 0}


# ── API 엔드포인트 ──

@router.post("/start", response_model=JackTestStatus)
def api_start_jack_test(req: JackTestRequest, db: Session = Depends(get_db)):
    """Jack 테스트 시작"""
    from app.models.map import RobotMap
    active_map = db.query(RobotMap).filter(RobotMap.is_active == True).order_by(RobotMap.id.desc()).first()
    if not active_map:
        raise HTTPException(404, "활성 맵이 없습니다")
    map_id = active_map.id

    pickup_poi = db.query(MapPOI).filter(
        MapPOI.map_id == map_id, MapPOI.name == req.pickup_poi_name, MapPOI.is_active == True
    ).first()
    if not pickup_poi or pickup_poi.world_x is None:
        raise HTTPException(404, f"픽업 POI를 찾을 수 없습니다 (name={req.pickup_poi_name})")

    dropoff_poi = db.query(MapPOI).filter(
        MapPOI.map_id == map_id, MapPOI.name == req.dropoff_poi_name, MapPOI.is_active == True
    ).first()
    if not dropoff_poi or dropoff_poi.world_x is None:
        raise HTTPException(404, f"드롭오프 POI를 찾을 수 없습니다 (name={req.dropoff_poi_name})")

    pickup = _db_poi_to_world(pickup_poi)
    dropoff = _db_poi_to_world(dropoff_poi)

    standby = None
    standby_poi = db.query(MapPOI).filter(
        MapPOI.map_id == map_id, MapPOI.name == "W1", MapPOI.is_active == True
    ).first()
    if standby_poi and standby_poi.world_x is not None:
        standby = _db_poi_to_world(standby_poi)

    job_id = f"{req.robot_ip}_{int(time.time())}"
    _jobs[job_id] = {"status": "pending", "message": "작업 시작 대기 중...", "detail": None}

    t = threading.Thread(target=_run_jack_job_wrapper, args=(job_id, req.robot_ip, pickup, dropoff, standby), daemon=True)
    t.start()

    return JackTestStatus(job_id=job_id, status="pending", message="작업이 시작되었습니다.")


@router.get("/status/{job_id}", response_model=JackTestStatus)
def api_get_jack_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"작업을 찾을 수 없습니다 (job_id={job_id})")
    return JackTestStatus(job_id=job_id, **job)


@router.post("/cancel/{job_id}")
def api_cancel_jack_test(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"작업을 찾을 수 없습니다 (job_id={job_id})")

    ip = job_id.rsplit("_", 1)[0]
    try:
        cancel_current_move(ip)
    except Exception as e:
        logger.warning(f"[jack-test] Cancel move failed: {e}")

    job["status"] = "error"
    job["message"] = "사용자에 의해 취소됨"
    return {"message": "취소 요청 완료", "job_id": job_id}


@router.get("/jobs")
def api_list_jobs():
    return {
        job_id: {"status": j["status"], "message": j["message"]}
        for job_id, j in _jobs.items()
    }


# ── 랙 스펙 설정 ──

class RackSpec(BaseModel):
    width: float = 0.83
    depth: float = 0.87
    margin: list[float] = [0.05, 0.05, 0.05, 0.05]
    alignment: str = "center"
    alignment_margin_back: float = 0.02
    extra_leg_offset: float = 0.0
    leg_shape: str = "other"
    leg_size: float = 0.05
    foot_radius: float = 0.025


@router.get("/rack-specs/{robot_ip}")
def api_get_rack_specs(robot_ip: str):
    try:
        settings = robot_get(robot_ip, "/system/settings/user")
        return {"rack_specs": settings.get("rack.specs", []), "raw": settings}
    except requests.RequestException as e:
        raise HTTPException(502, f"로봇 통신 실패 ({robot_ip}): {e}")


@router.patch("/rack-specs/{robot_ip}")
def api_update_rack_specs(robot_ip: str, spec: RackSpec):
    body = {"rack.specs": [spec.model_dump()]}
    try:
        resp = robot_patch(robot_ip, "/system/settings/user", body)
        return {"message": "랙 스펙 업데이트 완료", "response": resp}
    except requests.RequestException as e:
        raise HTTPException(502, f"로봇 통신 실패 ({robot_ip}): {e}")


@router.get("/settings/{robot_ip}")
def api_get_robot_user_settings(robot_ip: str):
    try:
        return robot_get(robot_ip, "/system/settings/user")
    except requests.RequestException as e:
        raise HTTPException(502, f"로봇 통신 실패 ({robot_ip}): {e}")


# ── 랙 사이즈 자동 감지 ──
# 흐름: start → (로봇을 랙 아래로 천천히 이동) → status 로 확인 → stop 으로 치수 확정
#      → 그 값을 PATCH /rack-specs/{robot_ip} 로 등록

@router.post("/rack-detect/{robot_ip}/start")
def api_rack_detect_start(robot_ip: str, duration_sec: int = 60):
    """랙 크기 감지 시작 (라이다로 폭/깊이 측정).

    호출 후 로봇을 랙 아래로 천천히 이동시키면 측정값이 쌓인다.
    """
    try:
        return rack_detect_service.start(robot_ip, duration_sec=duration_sec)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.get("/rack-detect/{robot_ip}/status")
def api_rack_detect_status(robot_ip: str):
    """진행 중인 감지의 현재 측정값 (중앙값 + 범위)."""
    return rack_detect_service.status(robot_ip)


@router.post("/rack-detect/{robot_ip}/stop")
def api_rack_detect_stop(robot_ip: str):
    """감지 중지 + 최종 측정값 반환.

    반환된 width/depth 를 실측치와 대조한 뒤 rack.specs 에 등록할 것.
    (제조사 안내: 수동 실측이 더 정확하므로 자동 감지는 보조 수단)
    """
    return rack_detect_service.stop(robot_ip)
