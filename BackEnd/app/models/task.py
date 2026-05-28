from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class TaskRoute(Base):
    """경로 — POI 순서를 미리 정의"""
    __tablename__ = "task_routes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    work_mode = Column(String(30), nullable=False, default="rack_pickup")
    # rack_pickup: W1 랙 픽업 → 배달 → 복귀 (풀 흐름)
    # delivery_no_rack: W1 픽업 스킵, standard 이동 + 각 포인트 jack_up/jack_down
    # simple_move: standard 이동만, 잭 조작 없음
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    waypoints = relationship("TaskRouteWaypoint", back_populates="route",
                             order_by="TaskRouteWaypoint.order", cascade="all, delete-orphan")
    schedules = relationship("ScheduledTask", back_populates="route")


class TaskRouteWaypoint(Base):
    """경로 내 웨이포인트 (픽업/드롭오프/대기 등)"""
    __tablename__ = "task_route_waypoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    route_id = Column(Integer, ForeignKey("task_routes.id", ondelete="CASCADE"), nullable=False)
    poi_id = Column(Integer, ForeignKey("map_pois.id", ondelete="CASCADE"), nullable=False)
    order = Column(Integer, nullable=False)
    waypoint_type = Column(String(20), nullable=False)  # pickup / dropoff / standby
    wait_sec = Column(Integer, nullable=False, default=0)

    route = relationship("TaskRoute", back_populates="waypoints")
    poi = relationship("MapPOI")


class ScheduledTask(Base):
    """스케줄 작업"""
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    robot_id = Column(Integer, ForeignKey("robots.id", ondelete="CASCADE"), nullable=False)
    route_id = Column(Integer, ForeignKey("task_routes.id", ondelete="CASCADE"), nullable=False)
    start_time = Column(String(5), nullable=False)          # "HH:MM"
    end_time = Column(String(5), nullable=True)             # "HH:MM"
    repeat_type = Column(String(20), nullable=False, default="once")  # once / daily / weekly
    repeat_days = Column(String(20), nullable=True)         # "1,2,3,4,5" (월~금)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    robot = relationship("Robot", foreign_keys=[robot_id])
    route = relationship("TaskRoute", back_populates="schedules")
    history = relationship("TaskHistory", back_populates="task", cascade="all, delete-orphan")


class TaskHistory(Base):
    """실행 이력"""
    __tablename__ = "task_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("scheduled_tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    task_name = Column(String(200), nullable=True)
    route_name = Column(String(200), nullable=True)
    robot_id = Column(Integer, nullable=False, index=True)
    robot_name = Column(String(100), nullable=True)
    pickup_poi_name = Column(String(100), nullable=True)
    dropoff_poi_name = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="running", index=True)
    started_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    finished_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    task = relationship("ScheduledTask", back_populates="history")
