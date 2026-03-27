from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Robot(Base):
    """로봇 기본 정보 테이블 (RB-01 ~ RB-03)"""
    __tablename__ = "robots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    serial_number = Column(String(100), unique=True, nullable=False, index=True)
    site = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    ip_address = Column(String(45), nullable=True)
    mac = Column(String(50), nullable=True)
    business_id = Column(String(100), nullable=True)
    area_id = Column(String(100), nullable=True)
    max_battery = Column(Integer, nullable=False, default=100)
    min_battery = Column(Integer, nullable=False, default=20)
    charging_id = Column(Integer, ForeignKey("map_pois.id", ondelete="SET NULL"), nullable=True)
    standby_id = Column(Integer, ForeignKey("map_pois.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)  # Soft Delete용
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # 관계
    status = relationship("RobotStatus", back_populates="robot", uselist=False, cascade="all, delete-orphan")
    status_history = relationship("RobotStatusHistory", back_populates="robot", cascade="all, delete-orphan")


class RobotStatus(Base):
    """로봇 실시간 상태 테이블 (RB-04, RB-05)
    status 코드: 0=IDLE, 1=WORKING, 2=CHARGING, 3=ERROR, 4=OFFLINE
    charging_status 코드: 0=NOT_CHARGING, 1=CHARGING, 2=FULLY_CHARGED
    """
    __tablename__ = "robot_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    robot_id = Column(Integer, ForeignKey("robots.id", ondelete="CASCADE"), unique=True, nullable=False)
    battery_level = Column(Integer, default=0)
    charging_status = Column(Integer, default=0)  # 0=미충전, 1=충전중, 2=완충
    position_x = Column(Float, default=0.0)
    position_y = Column(Float, default=0.0)
    position_yaw = Column(Float, default=0.0)
    status = Column(Integer, default=4)  # 0=대기, 1=작업중, 2=충전중, 3=에러, 4=오프라인
    is_manual_mode = Column(Boolean, default=False)
    is_remote_mode = Column(Boolean, default=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # 관계
    robot = relationship("Robot", back_populates="status")


class RobotStatusHistory(Base):
    """로봇 상태 이력 테이블 (실시간 데이터 ≠ 이력 데이터 분리 저장)"""
    __tablename__ = "robot_status_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    robot_id = Column(Integer, ForeignKey("robots.id", ondelete="CASCADE"), nullable=False, index=True)
    battery_level = Column(Integer, default=0)
    charging_status = Column(Integer, default=0)
    position_x = Column(Float, default=0.0)
    position_y = Column(Float, default=0.0)
    position_yaw = Column(Float, default=0.0)
    status = Column(Integer, default=4)
    recorded_at = Column(DateTime, server_default=func.now(), nullable=False)

    # 관계
    robot = relationship("Robot", back_populates="status_history")
