"""랙 사이즈 자동 감지 (AutoXing start/stop_rack_size_detection + /detected_rack)

사용 흐름 (AutoXing 문서 기준):
  1) 로봇을 랙 앞에 정렬
  2) POST /services/start_rack_size_detection
  3) WebSocket /detected_rack 구독하며 측정값 수집
  4) 로봇을 천천히 랙 아래로 이동시키면 값이 갱신됨
  5) POST /services/stop_rack_size_detection 후 치수 확정
  6) 그 값으로 rack.specs 등록

주의(문서 명시): 제조사는 "수동 실측이 더 정확하므로 자동 감지는 마지막 수단"으로 안내한다.
따라서 측정값은 참고치로 쓰고, 등록 전에 실측과 대조하는 것을 권장한다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ROBOT_PORT = 8090
HTTP_TIMEOUT = 10  # LTE 지연 대응

# 로봇 IP → 감지 세션 상태
_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def _robot_url(ip: str, path: str) -> str:
    return f"http://{ip}:{ROBOT_PORT}{path}"


def _extract_size(pkt: dict) -> Optional[dict]:
    """/detected_rack 패킷에서 폭/깊이 추출.

    펌웨어 버전에 따라 필드 구성이 달라서 rack_box / rack_box_aligned 양쪽을 훑고,
    width/height(=depth) 키를 관대하게 찾는다.
    """
    if not pkt.get("rack_detected", False):
        return None

    for key in ("rack_box_aligned", "rack_box"):
        box = pkt.get(key)
        if not isinstance(box, dict):
            continue
        w = box.get("width")
        # 문서상 depth 가 height 로 실려오는 경우가 있어 둘 다 본다
        d = box.get("depth", box.get("height"))
        if w is None or d is None:
            continue
        try:
            w = round(float(w), 4)
            d = round(float(d), 4)
        except (TypeError, ValueError):
            continue
        if w <= 0 or d <= 0:
            continue
        return {"width": w, "depth": d, "source": key}
    return None


def _collect_loop(ip: str, sess: dict, duration_sec: int) -> None:
    """/detected_rack 을 구독하며 측정값을 모은다."""
    import websocket as _ws

    deadline = time.time() + duration_sec
    ws = None
    try:
        ws = _ws.create_connection(f"ws://{ip}:{ROBOT_PORT}/ws/v2/topics", timeout=3)
        ws.send(json.dumps({"enable_topic": "/detected_rack"}))
        ws.settimeout(2)

        while time.time() < deadline:
            if sess.get("stop_flag"):
                break
            try:
                raw = ws.recv()
            except Exception:
                continue
            try:
                pkt = json.loads(raw)
            except Exception:
                continue
            if pkt.get("topic") != "/detected_rack":
                continue

            size = _extract_size(pkt)
            with _lock:
                sess["packets"] += 1
                if size:
                    sess["samples"].append(size)
                    sess["last"] = size
    except Exception as e:
        with _lock:
            sess["error"] = str(e)
        logger.warning(f"[rack_detect] {ip} 수집 오류: {e}")
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        with _lock:
            sess["running"] = False
            sess["ended_at"] = time.time()
        logger.info(f"[rack_detect] {ip} 수집 종료 — 샘플 {len(sess['samples'])}개")


def start(ip: str, duration_sec: int = 60) -> dict:
    """감지 시작. 이미 진행 중이면 그 세션을 그대로 알려준다."""
    with _lock:
        cur = _sessions.get(ip)
        if cur and cur.get("running"):
            return {"ok": True, "message": "이미 감지 중입니다", "already_running": True}

    # 로봇에 감지 시작 요청
    try:
        requests.post(
            _robot_url(ip, "/services/start_rack_size_detection"),
            headers={"Content-Type": "application/json"},
            timeout=HTTP_TIMEOUT,
        ).raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"감지 시작 실패 ({ip}): {e}")

    sess = {
        "running": True,
        "stop_flag": False,
        "samples": [],
        "last": None,
        "packets": 0,
        "error": None,
        "started_at": time.time(),
        "ended_at": None,
    }
    with _lock:
        _sessions[ip] = sess

    t = threading.Thread(target=_collect_loop, args=(ip, sess, duration_sec),
                         name=f"rack-detect-{ip}", daemon=True)
    t.start()
    logger.info(f"[rack_detect] {ip} 감지 시작 (최대 {duration_sec}초)")
    return {"ok": True, "message": "감지를 시작했습니다. 로봇을 랙 아래로 천천히 이동시키세요.",
            "duration_sec": duration_sec}


def _summarize(sess: dict) -> dict:
    """수집 샘플 요약 — 중앙값을 대표값으로 (튀는 값 영향 최소화)."""
    samples = sess.get("samples") or []
    result = {
        "running": sess.get("running", False),
        "packets": sess.get("packets", 0),
        "sample_count": len(samples),
        "last": sess.get("last"),
        "error": sess.get("error"),
        "width": None,
        "depth": None,
        "width_range": None,
        "depth_range": None,
    }
    if not samples:
        return result

    ws = sorted(s["width"] for s in samples)
    ds = sorted(s["depth"] for s in samples)
    mid = len(ws) // 2
    result["width"] = round(ws[mid], 4)
    result["depth"] = round(ds[mid], 4)
    result["width_range"] = [ws[0], ws[-1]]
    result["depth_range"] = [ds[0], ds[-1]]
    return result


def status(ip: str) -> dict:
    with _lock:
        sess = _sessions.get(ip)
        if not sess:
            return {"running": False, "sample_count": 0, "message": "감지 이력이 없습니다"}
        return _summarize(sess)


def stop(ip: str) -> dict:
    """감지 중지 + 측정 결과 요약 반환."""
    with _lock:
        sess = _sessions.get(ip)
        if sess:
            sess["stop_flag"] = True

    try:
        requests.post(
            _robot_url(ip, "/services/stop_rack_size_detection"),
            headers={"Content-Type": "application/json"},
            timeout=HTTP_TIMEOUT,
        ).raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[rack_detect] {ip} 감지 중지 요청 실패(무시): {e}")

    # 수집 스레드가 정리될 짧은 여유
    time.sleep(0.4)
    with _lock:
        sess = _sessions.get(ip)
        if not sess:
            return {"ok": True, "sample_count": 0, "message": "감지 이력이 없습니다"}
        summary = _summarize(sess)

    summary["ok"] = True
    if summary["sample_count"] == 0:
        summary["message"] = ("측정값을 받지 못했습니다. 로봇이 랙을 인식할 수 있는 위치인지, "
                              "랙 다리가 라이다에 보이는지 확인하세요.")
    else:
        summary["message"] = "측정 완료 — 실측치와 대조 후 rack.specs 에 등록하세요."
    logger.info(f"[rack_detect] {ip} 결과: w={summary['width']} d={summary['depth']} "
                f"(샘플 {summary['sample_count']})")
    return summary
