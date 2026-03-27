from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import pymysql
from urllib.parse import quote_plus

# MariaDB 접속 정보 (환경변수 우선, 없으면 기본값)
import os
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "1234")
DB_HOST = os.getenv("DB_HOST", "192.168.0.21")

DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "rcs_basic_db")

DATABASE_URL = f"mysql+pymysql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

engine = create_engine(
    DATABASE_URL, echo=False, pool_pre_ping=True,
    pool_size=10, max_overflow=20, pool_recycle=3600,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def create_database_if_not_exists():
    """rcs_db 데이터베이스가 없으면 자동 생성"""
    try:
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            charset="utf8mb4",
        )
    except Exception as e:
        print(f"[DB] 데이터베이스 서버 연결 실패 ({DB_HOST}:{DB_PORT}): {e}")
        raise

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    except Exception as e:
        print(f"[DB] 데이터베이스 생성 실패 ({DB_NAME}): {e}")
        raise
    finally:
        conn.close()


def init_db():
    """DB 생성 + 테이블 생성"""
    try:
        create_database_if_not_exists()
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        print(f"[DB] 데이터베이스 초기화 실패: {e}")
        raise


def get_db():
    """FastAPI Dependency — 요청마다 세션 생성/반환"""
    from fastapi import HTTPException
    db = SessionLocal()
    try:
        yield db
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"[DB] 세션 처리 중 오류 발생, 롤백 수행: {e}")
        raise
    finally:
        db.close()
