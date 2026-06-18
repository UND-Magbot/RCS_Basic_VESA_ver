"""시스템 전역 설정 — 시스템 이름 등.

저장소: BackEnd/static/system_settings.json (도커 볼륨 마운트로 영속)
- DB 마이그레이션 불필요
- RCS 웹과 태블릿이 같은 값을 보도록 단일 진실 원천 역할
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_SETTINGS_PATH = Path(__file__).resolve().parent.parent.parent / "static" / "system_settings.json"
_DEFAULT_SYSTEM_NAME = "UND RCS"
_lock = threading.Lock()


def _read_settings() -> dict:
    if not _SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[settings] system_settings.json 읽기 실패: {e}")
        return {}


def _write_settings(data: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class SystemNameResponse(BaseModel):
    system_name: str


class SystemNameUpdate(BaseModel):
    system_name: str = Field(..., max_length=200)


@router.get("/system-name", response_model=SystemNameResponse)
def get_system_name():
    with _lock:
        data = _read_settings()
    return SystemNameResponse(system_name=data.get("system_name") or _DEFAULT_SYSTEM_NAME)


@router.patch("/system-name", response_model=SystemNameResponse)
def update_system_name(payload: SystemNameUpdate):
    name = payload.system_name.strip()
    # 빈 문자열이면 기본값으로 복원 — 키 제거
    with _lock:
        data = _read_settings()
        if name:
            data["system_name"] = name
        else:
            data.pop("system_name", None)
        try:
            _write_settings(data)
        except Exception as e:
            logger.exception(f"[settings] system_settings.json 저장 실패: {e}")
            raise HTTPException(500, f"설정 저장 실패: {e}")
    effective = name or _DEFAULT_SYSTEM_NAME
    logger.info(f"[settings] system_name 변경: {effective}")
    return SystemNameResponse(system_name=effective)
