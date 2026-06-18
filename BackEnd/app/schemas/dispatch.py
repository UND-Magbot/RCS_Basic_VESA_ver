"""인터랙티브 배차 (VESA 모드) Pydantic 스키마"""
from typing import Optional
from datetime import datetime

from pydantic import BaseModel, Field


class DispatchStartItem(BaseModel):
    robot_id: int
    first_poi_id: int


class DispatchStartRequest(BaseModel):
    robots: list[DispatchStartItem]


class DispatchNextRequest(BaseModel):
    next_poi_id: int


class DispatchCallRequest(BaseModel):
    with_rack: bool = True  # True=렉 픽업 후 작업, False=잭 조작 없이 바로 작업


class DispatchSessionOut(BaseModel):
    id: int
    robot_id: int
    robot_name: Optional[str] = None
    status: str
    with_rack: bool = True
    first_poi_id: Optional[int] = None
    current_poi_id: Optional[int] = None
    current_poi_name: Optional[str] = None
    target_poi_id: Optional[int] = None
    target_poi_name: Optional[str] = None
    last_error: Optional[str] = None
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class POIBrief(BaseModel):
    id: int
    name: str
    poi_type: Optional[str] = None
    world_x: Optional[float] = None
    world_y: Optional[float] = None


class DispatchStatusOut(BaseModel):
    sessions: list[DispatchSessionOut] = Field(default_factory=list)
    occupied_poi_ids: list[int] = Field(default_factory=list)
    available_pois: list[POIBrief] = Field(default_factory=list)


class DispatchPOIStatusOut(BaseModel):
    """POI 기준 상태 (위치별 태블릿용).

    state:
      empty       — 이 위치 비어있음, 호출 가능
      calling     — 로봇이 이 위치로 오고 있음 (starting/picking_up/moving)
      arrived     — 로봇이 이 위치에 도착 (awaiting_next)
      returning   — 로봇이 종료 시퀀스 중 (다른 위치로 가는 거 아님, standby 복귀)
    """
    poi_id: int
    poi_name: Optional[str] = None
    state: str
    robot_id: Optional[int] = None
    robot_name: Optional[str] = None
    robot_ip: Optional[str] = None  # 강제 제어용 — /api/robots/remote/* 호출
    robot_battery: Optional[int] = None
    with_rack: Optional[bool] = None  # 활성 세션의 with_rack 모드
    session_status: Optional[str] = None
    target_poi_id: Optional[int] = None
    target_poi_name: Optional[str] = None
    available_pois: list[POIBrief] = Field(default_factory=list)
    occupied_poi_ids: list[int] = Field(default_factory=list)
    available_robot_count: int = 0


class DispatchCallResult(BaseModel):
    ok: bool
    message: str
    robot_id: Optional[int] = None
    robot_name: Optional[str] = None


# ── 슬롯 ─────────────────────────────────────────


class TabletSlotIn(BaseModel):
    poi_id: int
    alias: Optional[str] = None


class TabletSlotOut(BaseModel):
    slot_number: int
    poi_id: int
    poi_name: Optional[str] = None
    alias: Optional[str] = None

    class Config:
        from_attributes = True
