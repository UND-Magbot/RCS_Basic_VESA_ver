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
    with_rack: bool = True  # (리프팅) True=렉 픽업 후 작업, False=렉 없이
    robot_type: Optional[str] = None  # 'lifting'/'serving' — 부를 로봇 타입 (None=아무 타입)


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
      reserved    — 가용 로봇이 없어 예약됨 (로봇 종료 시 자동 호출 대기)
      calling     — 로봇이 이 위치로 오고 있음 (starting/picking_up/moving)
      arrived     — 로봇이 이 위치에 도착 (awaiting_next)
      returning   — 로봇이 종료 시퀀스 중 (다른 위치로 가는 거 아님, standby 복귀)
    """
    poi_id: int
    poi_name: Optional[str] = None
    state: str
    reserved: bool = False  # 이 위치에 대기 중 예약이 있는지
    reserved_with_rack: Optional[bool] = None  # 예약 시 선택한 렉 모드
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
    reserved: bool = False  # 가용 로봇이 없어 즉시 호출 대신 예약된 경우 True


# ── 콘솔 (범용 단말 — 모든 POI를 한 화면에서 호출/예약/제어) ──


class POIConsoleItem(BaseModel):
    """콘솔 그리드의 POI 한 칸 상태."""
    poi_id: int
    poi_name: str
    state: str  # empty / reserved / calling / arrived
    robot_id: Optional[int] = None
    robot_name: Optional[str] = None
    robot_ip: Optional[str] = None
    robot_battery: Optional[int] = None
    with_rack: Optional[bool] = None
    session_status: Optional[str] = None
    target_poi_id: Optional[int] = None
    target_poi_name: Optional[str] = None
    reserved: bool = False
    reserved_with_rack: Optional[bool] = None
    # 경유지 진행(awaiting_confirm) — 콘솔에서도 [확인] 누를 수 있게
    can_confirm: bool = False           # 지금 [확인] 가능한지
    next_poi_name: Optional[str] = None  # 확인 시 갈 다음 경유지
    is_last: bool = False               # 마지막 경유지(확인 시 종료)


class ConsoleRobotItem(BaseModel):
    """콘솔 사이드바의 로봇 한 장 (상태/배터리 + 직접 제어)."""
    robot_id: int
    robot_name: str
    robot_ip: Optional[str] = None
    robot_type: Optional[str] = None
    online: bool = False        # 라이브 ONLINE 여부
    battery: Optional[int] = None
    busy: bool = False          # 활성 배차(워커) 진행 중
    session_status: Optional[str] = None  # 진행 중이면 세션 상태
    current_poi_name: Optional[str] = None  # 현재/목적 위치 요약


class ConsoleStatusOut(BaseModel):
    pois: list[POIConsoleItem] = Field(default_factory=list)
    robots: list[ConsoleRobotItem] = Field(default_factory=list)
    occupied_poi_ids: list[int] = Field(default_factory=list)
    # 미래 목적지(다른 로봇의 다음 경유지/이동 목표)만 모음 — 콘솔의 "경유지 예약" 배지용.
    # 출발지(떠나는 로봇의 current_poi_id)는 제외한다.
    reserved_poi_ids: list[int] = Field(default_factory=list)
    available_robot_count: int = 0
    available_lifting_count: int = 0
    available_serving_count: int = 0


# ── 경유지 경로 / 로봇 부착 태블릿 ──────────────────────────


class DispatchRouteRequest(BaseModel):
    waypoints: list[int]  # 경유지 POI id 순서대로


class WaypointBrief(BaseModel):
    seq: int
    poi_id: Optional[int] = None
    poi_name: Optional[str] = None
    status: str  # pending / current / done


class RobotTabletStatus(BaseModel):
    """로봇 부착 태블릿용 상태."""
    robot_id: int
    robot_name: Optional[str] = None
    active: bool = False                  # 진행 중인 배차가 있는지
    session_status: Optional[str] = None  # awaiting_next / moving / awaiting_confirm / returning ...
    current_poi_id: Optional[int] = None
    current_poi_name: Optional[str] = None
    next_poi_id: Optional[int] = None     # [확인] 누르면 갈 다음 경유지
    next_poi_name: Optional[str] = None
    is_last: bool = False                 # 마지막 경유지 도착(확인 시 종료)
    can_confirm: bool = False             # 지금 [확인] 가능한지 (awaiting_confirm)
    waypoints: list[WaypointBrief] = Field(default_factory=list)


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
