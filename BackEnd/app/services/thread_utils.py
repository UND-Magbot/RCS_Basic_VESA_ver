"""
daemon 백그라운드 스레드 공통 wrapper.

목적: 작업 스레드(`threading.Thread(daemon=True)`) 에서 raise 된 예외가
조용히 사라지지 않도록 logger.exception + activity_log 로 기록한다.

사용:
    from app.services.thread_utils import safe_thread
    safe_thread(target=_run, name="manual-run", daemon=True).start()
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def safe_thread(target: Callable, args: tuple = (), kwargs: dict | None = None,
                name: Optional[str] = None, daemon: bool = True) -> threading.Thread:
    """target 을 try/except 로 감싸서 새 Thread 반환.

    target 안에서 예외가 발생하면 logger.exception 으로 기록 + activity_log 에 남김.
    조용한 좀비 스레드 방지.
    """
    kwargs = kwargs or {}
    label = name or getattr(target, "__name__", "thread")

    def _wrapped():
        try:
            target(*args, **kwargs)
        except Exception as exc:
            logger.exception(f"[thread:{label}] unhandled exception: {exc}")
            try:
                from app.crud.activity_log import log_activity
                log_activity(
                    "system", "thread_crash",
                    f"백그라운드 스레드 '{label}' 예외: {exc}",
                    source="safe_thread",
                )
            except Exception:
                pass

    return threading.Thread(target=_wrapped, name=label, daemon=daemon)
