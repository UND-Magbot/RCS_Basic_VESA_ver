from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, BigInteger, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Business(Base):
    """사업장 테이블"""
    __tablename__ = "businesses"

    business_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, unique=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    areas = relationship("Area", back_populates="business", cascade="all, delete-orphan")


class Area(Base):
    """영역(구역) 테이블"""
    __tablename__ = "areas"

    area_id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.business_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    is_main_floor = Column(Boolean, default=True, nullable=False)  # 메인층 여부 (False면 잭 든 상태로 작업)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    business = relationship("Business", back_populates="areas")


class RobotMap(Base):
    """매핑 결과 데이터 테이블
    - business_id / area_id: 어느 사업장·영역의 맵인지
    - robot_sn: 어떤 로봇으로 매핑했는지 (참조용)
    """
    __tablename__ = "robot_maps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_id = Column(Integer, ForeignKey("businesses.business_id", ondelete="SET NULL"), nullable=True, index=True)
    area_id = Column(Integer, ForeignKey("areas.area_id", ondelete="SET NULL"), nullable=True, index=True)
    robot_sn = Column(String(100), nullable=True)                             # 매핑한 로봇 SN (참조)
    mapping_id = Column(Integer, nullable=True)                               # 로봇 내부 맵핑 ID
    name = Column(String(200), nullable=True)                                 # 맵 이름 (사용자 지정)
    continue_mapping = Column(Boolean, default=False)                         # 이어서 매핑 여부
    thumbnail_url = Column(String(500), nullable=True)
    image_url = Column(String(500), nullable=True)
    grid_origin_x = Column(Float, default=0.0)
    grid_origin_y = Column(Float, default=0.0)
    grid_resolution = Column(Float, default=0.0)
    initial_x = Column(Float, default=0.0)                                   # 매핑 시작 위치 X (월드 좌표)
    initial_y = Column(Float, default=0.0)                                   # 매핑 시작 위치 Y (월드 좌표)
    initial_ori = Column(Float, default=0.0)                                 # 매핑 시작 방향 (rad)
    url = Column(String(500), nullable=True)
    start_time = Column(BigInteger, nullable=True)                            # unix timestamp
    end_time = Column(BigInteger, nullable=True)                              # unix timestamp
    state = Column(String(50), nullable=True)                                 # finished, cancelled 등
    bag_id = Column(Integer, nullable=True)
    bag_url = Column(String(500), nullable=True)
    download_url = Column(String(500), nullable=True)
    pbstream_url = Column(String(500), nullable=True)
    trajectories_url = Column(String(500), nullable=True)
    properties_url = Column(String(500), nullable=True)
    robot_map_id = Column(Integer, nullable=True)                              # 로봇 내부 맵 ID (층 전환용)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    business = relationship("Business")
    area = relationship("Area")
    pois = relationship("MapPOI", back_populates="robot_map", cascade="all, delete-orphan")
    lines = relationship("MapLine", back_populates="robot_map", cascade="all, delete-orphan")
    polygons = relationship("MapPolygon", back_populates="robot_map", cascade="all, delete-orphan")


class MapPOI(Base):
    """맵 POI (경유지·충전소·대기지점 등) 테이블"""
    __tablename__ = "map_pois"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, ForeignKey("robot_maps.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)
    world_x = Column(Float, nullable=True)                                  # 로봇 물리계 X 좌표
    world_y = Column(Float, nullable=True)                                  # 로봇 물리계 Y 좌표
    poi_type = Column(String(50), nullable=False, default="waypoint")       # waypoint / standby / charging
    phone_number = Column(String(50), nullable=True)
    angle = Column(Float, nullable=True)
    load_type = Column(String(20), nullable=True)                           # normal / heavy
    robot_sns = Column(Text, nullable=True)                                 # JSON array string
    address = Column(String(300), nullable=True)
    docking_radius = Column(Float, nullable=True)
    area_name = Column(String(200), nullable=True)                             # 소속 영역 이름
    rack_size = Column(String(10), nullable=True)                              # 랙 위치(jack) 일 때: 'S600' / 'S300'
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    robot_map = relationship("RobotMap", back_populates="pois")


class MapLine(Base):
    """맵 경로 라인 테이블"""
    __tablename__ = "map_lines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, ForeignKey("robot_maps.id", ondelete="CASCADE"), nullable=False, index=True)
    from_poi_id = Column(Integer, ForeignKey("map_pois.id", ondelete="CASCADE"), nullable=False)
    to_poi_id = Column(Integer, ForeignKey("map_pois.id", ondelete="CASCADE"), nullable=False)
    from_world_x = Column(Float, nullable=True)                             # 시작 POI 물리계 X
    from_world_y = Column(Float, nullable=True)                             # 시작 POI 물리계 Y
    to_world_x = Column(Float, nullable=True)                               # 끝 POI 물리계 X
    to_world_y = Column(Float, nullable=True)                               # 끝 POI 물리계 Y
    from_ori = Column(Float, nullable=True)                                 # 시작 POI orientation (rad)
    to_ori = Column(Float, nullable=True)                                   # 끝 POI orientation (rad)
    direction = Column(String(20), nullable=False, default="forward")       # forward / backward / bidirectional
    line_type = Column(String(20), nullable=False, default="straight")      # straight / curve
    control_points = Column(Text, nullable=True)                            # curve 제어점 JSON
    area_name = Column(String(200), nullable=True)                             # 소속 영역 이름
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    robot_map = relationship("RobotMap", back_populates="lines")
    from_poi = relationship("MapPOI", foreign_keys=[from_poi_id])
    to_poi = relationship("MapPOI", foreign_keys=[to_poi_id])


class MapPolygon(Base):
    """맵 폴리곤 (가상벽 영역 등)"""
    __tablename__ = "map_polygons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    map_id = Column(Integer, ForeignKey("robot_maps.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    shape_type = Column(String(20), nullable=False, default="polygon")  # polygon / firewall
    points_json = Column(Text, nullable=False)          # JSON: [{x, y, worldX, worldY}, ...]
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    robot_map = relationship("RobotMap", back_populates="polygons")


