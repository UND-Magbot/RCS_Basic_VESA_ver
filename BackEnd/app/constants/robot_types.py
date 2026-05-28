"""로봇 타입별 허용 작업 모드"""

# 로봇 타입별 허용되는 work_mode 목록
ROBOT_TYPE_WORK_MODES = {
    "lifting": ["rack_pickup", "delivery_no_rack", "simple_move"],
    "serving": ["simple_move"],
}

# 로봇 타입 한글 라벨
ROBOT_TYPE_LABELS = {
    "lifting": "리프팅",
    "serving": "서빙",
}


def is_work_mode_allowed(robot_type: str | None, work_mode: str) -> bool:
    """로봇 타입에서 해당 작업 모드가 허용되는지"""
    rtype = robot_type or "lifting"
    allowed = ROBOT_TYPE_WORK_MODES.get(rtype, ["simple_move"])
    return work_mode in allowed
