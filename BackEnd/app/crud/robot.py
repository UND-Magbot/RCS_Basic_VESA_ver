from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.robot import Robot, RobotStatus, RobotStatusHistory
from app.schemas.robot import (
    RobotCreate,
    RobotUpdate,
    RobotResponse,
    RobotStatusUpdate,
    RobotStatusResponse,
    MinBatteryUpdate,
    STATUS_MAP,
    CHARGING_MAP,
)


def _status_to_response(rs: RobotStatus) -> RobotStatusResponse:
    """RobotStatus ORM → RobotStatusResponse"""
    return RobotStatusResponse(
        battery_level=rs.battery_level,
        charging_status=rs.charging_status,
        charging_status_name=CHARGING_MAP.get(rs.charging_status, "알 수 없음"),
        position_x=rs.position_x,
        position_y=rs.position_y,
        position_yaw=rs.position_yaw,
        status=rs.status,
        status_name=STATUS_MAP.get(rs.status, "알 수 없음"),
        updated_at=rs.updated_at,
    )


def _to_response(robot: Robot) -> RobotResponse:
    """Robot ORM → RobotResponse"""
    return RobotResponse(
        id=robot.id,
        name=robot.name,
        serial_number=robot.serial_number,
        site=robot.site,
        model=robot.model,
        ip_address=robot.ip_address,
        max_battery=robot.max_battery,
        min_battery=robot.min_battery,
        robot_type=getattr(robot, "robot_type", "lifting") or "lifting",
        is_active=robot.is_active,
        business_id=robot.business_id,
        area_id=robot.area_id,
        status=_status_to_response(robot.status) if robot.status else None,
        created_at=robot.created_at,
        updated_at=robot.updated_at,
    )


# ── RB-01 로봇 등록 ──
def create_robot(db: Session, data: RobotCreate) -> RobotResponse:
    # 시리얼 넘버 중복 체크
    exists = db.query(Robot).filter(Robot.serial_number == data.serial_number).first()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 등록된 시리얼 넘버입니다: {data.serial_number}",
        )

    if data.min_battery >= data.max_battery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_battery가 max_battery보다 크게 설정되었습니다.",
        )

    robot = Robot(
        name=data.name,
        serial_number=data.serial_number,
        site=data.site,
        model=data.model,
        ip_address=data.ip_address,
        max_battery=data.max_battery,
        min_battery=data.min_battery,
    )
    db.add(robot)
    db.flush()

    # 로봇 등록 시 초기 상태 레코드 생성 (OFFLINE)
    robot_status = RobotStatus(robot_id=robot.id, status=4)
    db.add(robot_status)
    db.commit()
    db.refresh(robot)

    return _to_response(robot)


# ── 로봇 단건 조회 ──
def get_robot(db: Session, robot_id: int) -> RobotResponse:
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    if not robot:
        raise HTTPException(status_code=404, detail="로봇을 찾지 못했습니다.")
    return _to_response(robot)


# ── 로봇 목록 조회 ──
def get_robots(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    business_id: str | None = None,
    area_id: str | None = None,
) -> list[RobotResponse]:
    query = db.query(Robot).filter(Robot.is_active == True)
    if business_id is not None:
        query = query.filter(Robot.business_id == business_id)
    if area_id is not None:
        query = query.filter(Robot.area_id == area_id)
    robots = query.offset(skip).limit(limit).all()
    return [_to_response(r) for r in robots]


# ── RB-02 로봇 정보 수정 ──
def update_robot(db: Session, robot_id: int, data: RobotUpdate) -> RobotResponse:
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    if not robot:
        raise HTTPException(status_code=404, detail="로봇을 찾지 못했습니다.")

    if data.name is not None:
        robot.name = data.name
    if data.site is not None:
        robot.site = data.site
    if data.model is not None:
        robot.model = data.model
    if data.ip_address is not None:
        robot.ip_address = data.ip_address
    if data.robot_type is not None:
        robot.robot_type = data.robot_type

    db.commit()
    db.refresh(robot)
    return _to_response(robot)


# ── RB-03 로봇 삭제 (Soft Delete) ──
def delete_robot(db: Session, robot_id: int) -> dict:
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    if not robot:
        raise HTTPException(status_code=404, detail="로봇을 찾지 못했습니다.")

    # 작업 중인지 확인 (상태가 WORKING=1이면 삭제 불가)
    if robot.status and robot.status.status == 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="작업 중인 로봇의 삭제가 거부되었습니다.",
        )

    robot.is_active = False  # Soft Delete
    db.commit()

    return {"message": f"로봇 '{robot.name}'이(가) 비활성화되었습니다.", "robot_id": robot_id}


# ── RB-04 로봇 상태 수집 (SDK/API 연동 지점) ──
def update_robot_status(db: Session, robot_id: int, data: RobotStatusUpdate) -> RobotStatusResponse:
    """
    로봇 상태를 업데이트하고 이력을 저장합니다.
    ※ 이 함수는 AutoXing SDK/API로부터 수신한 데이터를 반영하는 지점입니다.
    """
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    if not robot:
        raise HTTPException(status_code=404, detail="로봇을 찾지 못했습니다.")

    rs = robot.status
    if not rs:
        rs = RobotStatus(robot_id=robot_id)
        db.add(rs)
        db.flush()

    # 상태 업데이트
    if data.battery_level is not None:
        rs.battery_level = data.battery_level
    if data.charging_status is not None:
        rs.charging_status = data.charging_status
    if data.position_x is not None:
        rs.position_x = data.position_x
    if data.position_y is not None:
        rs.position_y = data.position_y
    if data.position_yaw is not None:
        rs.position_yaw = data.position_yaw
    if data.status is not None:
        rs.status = data.status

    # 이력 저장 (실시간 데이터 ≠ 이력 데이터 → 분리 저장)
    history = RobotStatusHistory(
        robot_id=robot_id,
        battery_level=rs.battery_level,
        charging_status=rs.charging_status,
        position_x=rs.position_x,
        position_y=rs.position_y,
        position_yaw=rs.position_yaw,
        status=rs.status,
    )
    db.add(history)
    db.commit()
    db.refresh(rs)

    return _status_to_response(rs)


# ── 최소 배터리 조회 (SN 기반) ──
def get_min_battery_by_sn(db: Session, sn: str) -> dict:
    robot = db.query(Robot).filter(Robot.serial_number == sn, Robot.is_active == True).first()
    if not robot:
        return {"min_battery": 20, "charging_id": None, "standby_id": None}
    return {"min_battery": robot.min_battery, "charging_id": robot.charging_id, "standby_id": robot.standby_id}


# ── 최소 배터리 수정 (SN 기반) ──
def update_min_battery_by_sn(db: Session, sn: str, data: MinBatteryUpdate) -> dict:
    robot = db.query(Robot).filter(Robot.serial_number == sn, Robot.is_active == True).first()
    if not robot:
        raise HTTPException(status_code=404, detail="등록되지 않은 로봇입니다.")

    robot.min_battery = data.min_battery
    robot.charging_id = data.charging_id
    robot.standby_id = data.standby_id
    db.commit()
    db.refresh(robot)
    return {"min_battery": robot.min_battery, "charging_id": robot.charging_id, "standby_id": robot.standby_id}


# ── RB-05 로봇 상태 조회 ──
def get_robot_status(db: Session, robot_id: int) -> RobotStatusResponse:
    robot = db.query(Robot).filter(Robot.id == robot_id).first()
    if not robot:
        raise HTTPException(status_code=404, detail="로봇을 찾지 못했습니다.")
    if not robot.status:
        raise HTTPException(status_code=404, detail="로봇 상태 정보가 없습니다.")
    return _status_to_response(robot.status)
