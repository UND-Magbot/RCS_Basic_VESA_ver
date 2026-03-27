from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime


# ── 경로 스키마 ──

class WaypointInput(BaseModel):
    poi_id: int
    order: int
    waypoint_type: str  # pickup / dropoff / standby
    wait_sec: int = 0


class TaskRouteCreate(BaseModel):
    name: str
    waypoints: list[WaypointInput]


class TaskRouteUpdate(BaseModel):
    name: Optional[str] = None
    waypoints: Optional[list[WaypointInput]] = None


class WaypointResponse(BaseModel):
    id: int
    poi_id: int
    poi_name: Optional[str] = None
    order: int
    waypoint_type: str
    wait_sec: int = 0
    world_x: Optional[float] = None
    world_y: Optional[float] = None

    model_config = {"from_attributes": True}


class TaskRouteResponse(BaseModel):
    id: int
    name: str
    waypoints: list[WaypointResponse] = []
    is_active: bool = True
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── 스케줄 스키마 ──

class ScheduledTaskCreate(BaseModel):
    name: str
    robot_id: int
    route_id: int
    start_time: str           # "HH:MM"
    end_time: Optional[str] = None
    repeat_type: str = "once"  # once / daily / weekly
    repeat_days: Optional[str] = None  # "1,2,3,4,5"
    start_date: date
    end_date: Optional[date] = None


class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    robot_id: Optional[int] = None
    route_id: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    repeat_type: Optional[str] = None
    repeat_days: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None


class ScheduledTaskResponse(BaseModel):
    id: int
    name: str
    route_id: int
    route_name: Optional[str] = None
    robot_name: Optional[str] = None
    start_time: str
    end_time: Optional[str] = None
    repeat_type: str
    repeat_days: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: bool
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── 이력 스키마 ──

class TaskHistoryResponse(BaseModel):
    id: int
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    route_name: Optional[str] = None
    robot_id: int
    robot_name: Optional[str] = None
    pickup_poi_name: Optional[str] = None
    dropoff_poi_name: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None

    model_config = {"from_attributes": True}
