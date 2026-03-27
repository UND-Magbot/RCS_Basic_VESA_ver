import io
import logging
import os
import subprocess
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["DB 백업"])


def _run_mysqldump() -> tuple[bytes | None, str]:
    """mysqldump 실행. 성공 시 (bytes, "") 반환, 실패 시 (None, 에러메시지) 반환."""
    cmd = [
        "mysqldump",
        f"--host={DB_HOST}",
        f"--port={DB_PORT}",
        f"--user={DB_USER}",
        f"--password={DB_PASSWORD}",
        "--default-character-set=utf8mb4",
        DB_NAME,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError:
        return None, "mysqldump not found"
    except subprocess.TimeoutExpired:
        return None, "mysqldump timeout"

    if result.returncode != 0:
        return None, result.stderr.decode("utf-8", errors="replace").strip()

    return result.stdout, ""


def _build_sql_python(db: Session) -> bytes:
    """Python + SQLAlchemy로 SQL 덤프 생성 (mysqldump 없이 동작)."""
    lines: list[str] = []
    lines.append(f"-- Python SQL Dump — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"-- Database: {DB_NAME}")
    lines.append("-- --------------------------------------------------------\n")
    lines.append("SET NAMES utf8mb4;")
    lines.append("SET FOREIGN_KEY_CHECKS=0;\n")

    # 테이블 목록 조회
    rows = db.execute(text("SHOW TABLES")).fetchall()
    tables = [r[0] for r in rows]

    for table in tables:
        lines.append(f"-- Table: `{table}`")
        lines.append("-- --------------------------------------------------------")

        # CREATE TABLE
        create_row = db.execute(text(f"SHOW CREATE TABLE `{table}`")).fetchone()
        if create_row:
            lines.append(f"DROP TABLE IF EXISTS `{table}`;")
            lines.append(create_row[1] + ";\n")

        # 데이터 INSERT
        data_rows = db.execute(text(f"SELECT * FROM `{table}`")).fetchall()
        if data_rows:
            col_names = db.execute(text(f"SELECT * FROM `{table}` LIMIT 0")).keys()
            cols = ", ".join(f"`{c}`" for c in col_names)
            for row in data_rows:
                vals = []
                for v in row:
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    elif isinstance(v, datetime):
                        vals.append(f"'{v.strftime('%Y-%m-%d %H:%M:%S')}'")
                    else:
                        escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
                        vals.append(f"'{escaped}'")
                lines.append(f"INSERT INTO `{table}` ({cols}) VALUES ({', '.join(vals)});")
            lines.append("")

    lines.append("SET FOREIGN_KEY_CHECKS=1;")
    return "\n".join(lines).encode("utf-8")


@router.get("/db")
def api_backup_db(db: Session = Depends(get_db)):
    """DB SQL 백업 — mysqldump 우선, 없으면 Python 방식으로 생성"""
    now = datetime.now()
    date_str = now.strftime("%Y%m%d_%H%M%S")
    filename = f"db_backup_{date_str}.sql"

    sql_bytes, err = _run_mysqldump()
    if sql_bytes is None:
        logger.warning(f"[backup] mysqldump 실패 ({err}) — Python 방식으로 대체")
        sql_bytes = _build_sql_python(db)

    logger.warning(f"[backup] SQL 백업 완료: {filename} ({len(sql_bytes):,} bytes)")
    return Response(
        content=sql_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/browse")
def api_browse(path: str = "/"):
    """서버 디렉터리 목록 조회 (폴더만 반환). 존재하지 않으면 가장 가까운 상위 경로로 대체."""
    abs_path = os.path.abspath(path)
    # 존재하지 않는 경로면 상위로 올라가며 존재하는 경로 탐색
    while abs_path != "/" and not os.path.isdir(abs_path):
        abs_path = os.path.dirname(abs_path)
    try:
        entries = sorted(
            e for e in os.listdir(abs_path)
            if os.path.isdir(os.path.join(abs_path, e)) and not e.startswith(".")
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    parent = str(os.path.dirname(abs_path)) if abs_path != "/" else None
    return {"current": abs_path, "parent": parent, "dirs": entries}


class SaveRequest(BaseModel):
    save_path: str  # 서버 측 저장 경로 (디렉터리)


@router.post("/save")
def api_backup_save(body: SaveRequest, db: Session = Depends(get_db)):
    """DB 백업 SQL 파일을 서버 로컬 경로에 저장"""
    save_path = body.save_path.strip()
    if not save_path:
        raise HTTPException(status_code=400, detail="저장 경로를 입력해주세요.")

    now = datetime.now()
    date_str = now.strftime("%Y%m%d_%H%M%S")

    dir_path = save_path if save_path.endswith(("/", "\\")) else save_path
    if not os.path.isdir(dir_path):
        os.makedirs(dir_path, exist_ok=True)

    sql_path = os.path.join(dir_path, f"db_backup_{date_str}.sql")

    sql_bytes, err = _run_mysqldump()
    if sql_bytes is None:
        logger.warning(f"[backup/save] mysqldump 실패 ({err}) — Python 방식으로 대체")
        sql_bytes = _build_sql_python(db)
    try:
        with open(sql_path, "wb") as f:
            f.write(sql_bytes)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"SQL 파일 저장 실패: {e}")

    logger.warning(f"[backup/save] 저장 완료: {sql_path}")
    return {
        "sql_path": sql_path,
        "sql_size": len(sql_bytes),
    }
