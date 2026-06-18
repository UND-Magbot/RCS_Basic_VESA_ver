"""인터랙티브 배차 세션 (VESA 모드)

태블릿에서 사람이 매 포지션마다 다음 위치를 누르는 운영 방식.
잭은 시작 시 1회 업 → 종료 시 1회 다운 (포지션마다 잭 사이클 없음).
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class DispatchSession(Base):
    """로봇별 인터랙티브 배차 세션.

    status:
      starting       — start 직후, 워커가 standby로 가서 픽업 준비 중
      picking_up     — standby에서 align_with_rack → jack_up 진행 중
      moving         — target_poi_id 로 이동 중 (current_poi_id 비어있음)
      awaiting_next  — target_poi_id 도착, 다음 명령 대기
      returning      — 종료 명령 받고 standby 복귀 + 잭다운 + 충전소 도킹 중
      completed      — 정상 종료
      failed         — 오류로 종료
    """
    __tablename__ = "dispatch_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    robot_id = Column(Integer, ForeignKey("robots.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="starting")
    with_rack = Column(Boolean, nullable=False, default=True)  # True=렉 픽업 후 작업, False=잭 조작 없이 바로 작업
    first_poi_id = Column(Integer, ForeignKey("map_pois.id", ondelete="SET NULL"), nullable=True)
    current_poi_id = Column(Integer, ForeignKey("map_pois.id", ondelete="SET NULL"), nullable=True)
    target_poi_id = Column(Integer, ForeignKey("map_pois.id", ondelete="SET NULL"), nullable=True)
    last_error = Column(String(500), nullable=True)
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    ended_at = Column(DateTime, nullable=True)

    robot = relationship("Robot")
    first_poi = relationship("MapPOI", foreign_keys=[first_poi_id])
    current_poi = relationship("MapPOI", foreign_keys=[current_poi_id])
    target_poi = relationship("MapPOI", foreign_keys=[target_poi_id])


class TabletSlot(Base):
    """태블릿 슬롯 → POI 매핑.

    태블릿 앱이 외우기 쉽게 1, 2, 3... 슬롯 번호로 접속하고,
    슬롯 번호는 DB에서 실제 POI ID로 변환됨.
    슬롯에 매핑이 없으면 태블릿 페이지에서 설정 모드 노출.
    """
    __tablename__ = "tablet_slots"

    slot_number = Column(Integer, primary_key=True, autoincrement=False)
    poi_id = Column(Integer, ForeignKey("map_pois.id", ondelete="CASCADE"), nullable=False)
    alias = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    poi = relationship("MapPOI")
