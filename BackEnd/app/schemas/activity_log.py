from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ── 카테고리 상수 ──

CATEGORY_MAP = {
    "map": "맵",
    "robot": "로봇",
    "system": "시스템",
}


# ── 요청 스키마 ──

class ActivityLogCreate(BaseModel):
    category: str = Field(..., max_length=20)
    action: str = Field(..., max_length=50)
    message: str = Field(..., max_length=500)
    detail: Optional[str] = None
    robot_id: Optional[int] = None
    robot_name: Optional[str] = Field(None, max_length=100)
    source: Optional[str] = Field(None, max_length=100)


# ── 응답 스키마 ──

class ActivityLogResponse(BaseModel):
    id: int
    category: str
    category_name: str
    action: str
    message: str
    detail: Optional[str]
    robot_id: Optional[int]
    robot_name: Optional[str]
    source: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityLogListResponse(BaseModel):
    total: int
    items: list[ActivityLogResponse]
