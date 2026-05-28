from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

# ── 상태 코드 상수 ──
# status: 0=IDLE, 1=WORKING, 2=CHARGING, 3=ERROR, 4=OFFLINE
# charging_status: 0=NOT_CHARGING, 1=CHARGING, 2=FULLY_CHARGED

STATUS_MAP = {0: "대기", 1: "작업중", 2: "충전중", 3: "에러", 4: "오프라인"}
CHARGING_MAP = {0: "미충전", 1: "충전중", 2: "완충"}


# ── 요청 스키마 ──

class RobotCreate(BaseModel):
    """RB-01 로봇 등록 요청"""
    name: str = Field(..., min_length=1, max_length=100)
    serial_number: str = Field(..., min_length=1, max_length=100)
    site: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    ip_address: Optional[str] = Field(None, max_length=45)
    max_battery: int = Field(default=100, ge=0, le=100)
    min_battery: int = Field(default=20, ge=0, le=100)


class RobotUpdate(BaseModel):
    """RB-02 로봇 정보 수정 요청"""
    name: Optional[str] = Field(None, max_length=100)
    site: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    ip_address: Optional[str] = Field(None, max_length=45)
    robot_type: Optional[str] = Field(None, max_length=30)


class MinBatteryUpdate(BaseModel):
    """최소 배터리 + 충전소 + 대기장소 수정 요청"""
    min_battery: int = Field(..., ge=0, le=100)
    charging_id: Optional[int] = None
    standby_id: Optional[int] = None


class RobotStatusUpdate(BaseModel):
    """RB-04 로봇 상태 수집 — SDK/API로부터 수신한 데이터를 업데이트"""
    battery_level: Optional[int] = Field(None, ge=0, le=100)
    charging_status: Optional[int] = Field(None, ge=0, le=2)
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    position_yaw: Optional[float] = None
    status: Optional[int] = Field(None, ge=0, le=4)


# ── 응답 스키마 ──

class RobotStatusResponse(BaseModel):
    """로봇 상태 응답"""
    battery_level: int
    charging_status: int
    charging_status_name: str
    position_x: float
    position_y: float
    position_yaw: float
    status: int
    status_name: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class RobotResponse(BaseModel):
    """로봇 단건 응답"""
    id: int
    name: str
    serial_number: str
    site: Optional[str]
    model: Optional[str]
    ip_address: Optional[str]
    max_battery: int
    min_battery: int
    robot_type: Optional[str] = "lifting"
    is_active: bool
    business_id: Optional[str] = None
    area_id: Optional[str] = None
    status: Optional[RobotStatusResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RobotListResponse(BaseModel):
    """로봇 목록 응답"""
    total: int
    items: list[RobotResponse]
