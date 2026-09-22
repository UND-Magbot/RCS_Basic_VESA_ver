# -*- coding: utf-8 -*-
"""제자리 회전 오차 측정 — AutoXing 로봇 (S300 / S600)

로봇을 제자리에서 지정 각도만큼 회전시키고, 매 시행마다
  - 명령한 각도
  - 로봇이 "돌았다고 보고하는" 각도 (/tracked_pose)
를 기록한다. **실제 오차(mm)는 바닥 마킹으로 사람이 잰다** — 이 스크립트는
로봇을 재현 가능하게 돌리고, 사람이 마킹할 시간을 주고, 기록지를 만들어 준다.

★ 왜 tracked_pose 를 정답으로 쓰지 않는가
  tracked_pose 는 라이다 스캔매칭 + 오도메트리의 **추정값**이다.
  우리가 재려는 오차의 상당 부분이 바로 그 추정 오차라서, 로봇 자신의 값으로
  채점하면 오차가 0 으로 나온다(자기가 자기를 채점).
  → 바닥 마킹이 ground truth 이고, tracked_pose 는 "로봇이 자기를 얼마나
    잘못 아는가"를 보기 위한 비교값이다. 둘 다 기록해야 의미가 있다.

의존성: requests, websocket-client  (둘 다 프로젝트에서 이미 쓰는 것)

사용 예
  # 연결·포즈만 확인 (로봇 안 움직임)
  python rotation_accuracy.py --ip 192.168.0.50 --dry-run

  # 360도 시계방향 5회 (시행마다 마킹할 시간을 준다)
  python rotation_accuracy.py --ip 192.168.0.50 --angle 360 --dir cw --repeat 5 --label S300

  # 180도 반시계 5회, 멈추지 않고 연속 (누적 오차 측정)
  python rotation_accuracy.py --ip 192.168.0.50 --angle 180 --dir ccw --repeat 5 --no-pause
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import sys
import time
from datetime import datetime

import requests


def _force_utf8_console():
    """Windows 콘솔 한글 깨짐 방지.

    기본 코드페이지가 cp949 라 이 파일의 한글 안내문이 전부 깨져 나온다.
    사용자가 매번 `chcp 65001` 을 치게 하는 대신 여기서 직접 바꾼다.
    실패해도 측정에는 지장이 없으므로 조용히 넘어간다.
    """
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_console()

ROBOT_PORT = 8090
HTTP_TIMEOUT = 10

# 회전을 몇 도씩 쪼개서 명령할지 (기본 180 — 360도면 180x2)
#
#   왜 쪼개나: target_ori 는 **절대 각도**라서 "+360" 을 그대로 보내면
#   "변화 없음" 이 되어 로봇이 아예 안 움직인다.
#
#   ⚠ 180 은 방향을 지정할 수 없다. "+180" 과 "-180" 이 같은 절대각이라
#     **로봇이 어느 쪽으로 돌지 스스로 정한다.** 이때 --dir 은 무시된다.
#     대신 회전 시작 직후 포즈를 한 번 읽어 **실제로 어느 쪽으로 돌았는지 기록**한다
#     (step 의 observed_dir). CW/CCW 를 의도대로 강제하려면 --chunk 90 을 써라.
DEFAULT_CHUNK_DEG = 180

# 이 각도(도) 이상 쪼개면 회전 방향을 지정할 수 없다
DIR_AMBIGUOUS_CHUNK_DEG = 180

# ── twist 모드 (연속 회전) 설정 ────────────────────────────────
#   /chassis/moves 는 목표각이 **절대값**이라 "360도 더" 를 표현할 수 없다.
#   360 을 한 번에 쭉 돌리려면 각속도를 직접 줘야 한다(WS /twist).
#   콘솔 원격조종 방향버튼이 쓰는 그 채널이다.
TWIST_SPEED_RAD = 0.40      # 기본 각속도(rad/s) ≈ 23도/s → 360도 약 16초
TWIST_SLOW_RAD = 0.12       # 막판 감속 각속도 — 오버슈트를 줄인다
TWIST_SLOW_ZONE_DEG = 20.0  # 남은 각이 이 이하면 감속
TWIST_SEND_SEC = 0.10       # 명령 전송 주기(초)
TWIST_RECV_TIMEOUT = 0.05   # 포즈 수신 대기(초)
TWIST_STOP_REPEAT = 5       # 정지 명령 반복 횟수
# 회전 시작 후 방향을 관찰하기까지 기다리는 시간(초)
DIR_PROBE_DELAY_SEC = 1.5
# 방향 판정 최소 각도(도) — 이보다 덜 돌았으면 판정 보류
DIR_PROBE_MIN_DEG = 2.0

SETTLE_SEC = 3.0          # 정지 후 정착 대기
SETTLE_POS_TOL = 0.005    # 정착 판정 — 위치 5mm 이내
SETTLE_ORI_TOL_DEG = 0.3  # 정착 판정 — 각도 0.3도 이내
MOVE_POLL_SEC = 0.5
MOVE_TIMEOUT = 180

_stop_requested = False


# ── 통신 ──────────────────────────────────────────────────────

def _url(ip, path):
    return "http://{}:{}{}".format(ip, ROBOT_PORT, path)


def get_status(ip):
    r = requests.get(_url(ip, "/chassis/status"), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def read_pose(ip, timeout=5.0):
    """WS /tracked_pose 로 현재 포즈 1건. 실패 시 None.

    이 펌웨어는 GET /chassis/pose 가 help 텍스트만 반환해서 REST 로는 못 읽는다.
    반환: {"x": float, "y": float, "ori": float(rad)}
    """
    import websocket  # 선택 의존성 — 여기서만 사용

    ws = None
    try:
        ws = websocket.create_connection(
            "ws://{}:{}/ws/v2/topics".format(ip, ROBOT_PORT), timeout=3)
        ws.send(json.dumps({"enable_topic": "/tracked_pose"}))
        ws.settimeout(2)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                pkt = json.loads(ws.recv())
            except Exception:
                continue
            if pkt.get("topic") == "/tracked_pose" and pkt.get("pos"):
                pos = pkt["pos"]
                return {"x": float(pos[0]), "y": float(pos[1]),
                        "ori": float(pkt.get("ori", 0.0))}
    except Exception as e:
        print("  [경고] tracked_pose 읽기 실패: {}".format(e))
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
    return None


def set_control_mode(ip, mode):
    """제어 모드 전환 (auto / manual / remote).

    ★ /twist(각속도 직접 제어)는 **remote 모드에서만 먹는다.**
      auto 모드에서는 내비게이션 스택이 바퀴를 쥐고 있어 twist 가 무시된다.
      콘솔 원격조종도 twist 를 보내기 전에 이 API 를 먼저 부른다.

    ⚠ **끝나면 반드시 auto 로 되돌려야 한다.** remote 로 두면 RCS 배차가
      로봇을 못 움직인다(이동 명령이 먹지 않는다).
    """
    r = requests.post(
        _url(ip, "/services/wheel_control/set_control_mode"),
        json={"control_mode": mode}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.status_code


def current_control_mode(ip):
    try:
        return get_status(ip).get("control_mode")
    except Exception:
        return None


def create_move(ip, x, y, ori):
    body = {"creator": "rotation_test", "type": "standard",
            "target_x": x, "target_y": y, "target_ori": ori}
    r = requests.post(_url(ip, "/chassis/moves"), json=body, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json().get("id")


def wait_move(ip, move_id, timeout=MOVE_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _stop_requested:
            cancel_move(ip)
            return {"state": "cancelled", "fail_message": "사용자 중지"}
        try:
            r = requests.get(_url(ip, "/chassis/moves/{}".format(move_id)),
                             timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            resp = r.json()
        except Exception as e:
            print("  [경고] move 조회 실패(재시도): {}".format(e))
            time.sleep(MOVE_POLL_SEC)
            continue
        if resp.get("state") in ("succeeded", "failed", "cancelled"):
            return resp
        time.sleep(MOVE_POLL_SEC)
    return {"state": "timeout",
            "fail_message": "move {} {}초 초과".format(move_id, timeout)}


def cancel_move(ip):
    try:
        requests.patch(_url(ip, "/chassis/moves/current"),
                       json={"state": "cancelled"}, timeout=HTTP_TIMEOUT)
    except Exception:
        pass


# ── 각도 유틸 ─────────────────────────────────────────────────

def norm_rad(a):
    """-pi ~ pi 로 정규화"""
    return math.atan2(math.sin(a), math.cos(a))


def ang_diff_deg(a_to, a_from):
    """a_from -> a_to 의 최단 각차 (도, -180~180)"""
    return math.degrees(norm_rad(a_to - a_from))


# ── 측정 ──────────────────────────────────────────────────────

def wait_settle(ip):
    """정지 후 정착 대기 — 포즈가 실제로 멈췄는지 확인하고 최종 포즈를 돌려준다.

    정지 직후 바로 마킹하면 로봇이 미세 조정 중일 수 있다.
    """
    time.sleep(SETTLE_SEC)
    p1 = read_pose(ip)
    if p1 is None:
        return None
    for _ in range(5):
        time.sleep(1.0)
        p2 = read_pose(ip)
        if p2 is None:
            return p1
        moved = math.hypot(p2["x"] - p1["x"], p2["y"] - p1["y"])
        turned = abs(ang_diff_deg(p2["ori"], p1["ori"]))
        p1 = p2
        if moved < SETTLE_POS_TOL and turned < SETTLE_ORI_TOL_DEG:
            return p2
    print("  [경고] 정착이 확인되지 않았다 — 로봇이 계속 미세 조정 중일 수 있다")
    return p1


def rotate_once_twist(ip, total_deg, speed_rad=TWIST_SPEED_RAD):
    """제자리에서 total_deg 만큼 **한 번에 쭉** 회전 (WS /twist 각속도 직접 제어).

    /chassis/moves 는 목표각이 절대값이라 "360도 더" 를 못 시킨다. 그래서
    각속도를 직접 주고, `/tracked_pose` 의 각도 변화를 **누적**해서 목표에
    닿으면 멈춘다. 중간에 서지 않으므로 360 도를 한 바퀴로 돈다.

    ★ 안전 — 이 방식은 목표가 없는 '속도 명령' 이다.
      · 명령을 끊으면 로봇이 선다(펌웨어 워치독). 콘솔 원격조종이 300ms 마다
        재전송하는 구조인 것이 그 근거다 [추정 — 직접 검증하지 않았다].
      · 워치독을 믿지 않고, 끝날 때·중단될 때 **0 속도를 여러 번** 보낸다.
      · 예상 시간의 3배가 지나면 스스로 중단한다.
      **첫 회차는 반드시 옆에서 보고, 이상하면 Ctrl+C 하라.**

    ★ 측정 관점
      멈추는 시점을 우리가 정하므로 정확히 360.0 도에서 서지 않는다(오버슈트).
      그래도 측정은 성립한다 — 우리가 재는 건 "로봇이 몇 도 돌았다고 하는가"
      대 "실제로 몇 도 돌았는가" 이고, 실제는 바닥 마킹이 알려주기 때문이다.
      다만 360 도의 장점(마킹이 제자리로 돌아옴)은 오버슈트만큼 흐려진다.
    """
    import websocket

    start = read_pose(ip)
    if start is None:
        return {"ok": False, "error": "시작 포즈 읽기 실패"}

    target_abs = abs(total_deg)
    sign = 1.0 if total_deg >= 0 else -1.0
    deadline = time.time() + (target_abs / max(1e-6, math.degrees(speed_rad))) * 3 + 20

    # ★ twist 는 remote 모드에서만 먹는다. auto 면 내비게이션이 바퀴를 쥐고 있어
    #   명령이 통째로 무시된다(2026-09-14 실기에서 '안 돌아감' 으로 확인).
    prev_mode = current_control_mode(ip)
    try:
        set_control_mode(ip, "remote")
    except Exception as e:
        return {"ok": False, "error": "remote 모드 전환 실패: {}".format(e),
                "start": start}
    got = current_control_mode(ip)
    if got != "remote":
        try:
            set_control_mode(ip, "auto")
        except Exception:
            pass
        return {"ok": False,
                "error": "remote 모드로 안 바뀜(현재 {}) — twist 를 쓸 수 없다".format(got),
                "start": start}

    ws = None
    turned = 0.0            # 누적 회전량(도, 부호 있음)
    prev_ori = start["ori"]
    end_pose = None
    aborted = None
    try:
        ws = websocket.create_connection(
            "ws://{}:{}/ws/v2/topics".format(ip, ROBOT_PORT), timeout=5)
        ws.send(json.dumps({"enable_topic": "/tracked_pose"}))
        ws.settimeout(TWIST_RECV_TIMEOUT)

        last_send = 0.0
        while True:
            if _stop_requested:
                aborted = "사용자 중지"
                break
            if time.time() > deadline:
                aborted = "시간 초과 — 스스로 중단"
                break

            remaining = target_abs - abs(turned)
            if remaining <= 0:
                break

            now = time.time()
            if now - last_send >= TWIST_SEND_SEC:
                av = speed_rad if remaining > TWIST_SLOW_ZONE_DEG else TWIST_SLOW_RAD
                ws.send(json.dumps({"topic": "/twist",
                                    "linear_velocity": 0,
                                    "angular_velocity": sign * av}))
                last_send = now

            try:
                pkt = json.loads(ws.recv())
            except Exception:
                continue        # 수신 타임아웃 — 정상. 루프를 계속 돌린다
            if pkt.get("topic") == "/tracked_pose" and pkt.get("pos"):
                ori = float(pkt.get("ori", 0.0))
                turned += ang_diff_deg(ori, prev_ori)   # ±pi 넘어가도 누적됨
                prev_ori = ori

        # 정지 — 워치독을 믿지 않고 0 속도를 여러 번 보낸다
        for _ in range(TWIST_STOP_REPEAT):
            try:
                ws.send(json.dumps({"topic": "/twist",
                                    "linear_velocity": 0, "angular_velocity": 0}))
            except Exception:
                break
            time.sleep(0.1)
    except Exception as e:
        aborted = "twist 통신 오류: {}".format(e)
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        # ⚠ 무슨 일이 있어도 제어 모드를 되돌린다.
        #   remote 로 남겨두면 RCS 배차가 이 로봇을 못 움직인다.
        restore = prev_mode if prev_mode in ("auto", "manual") else "auto"
        for attempt in range(3):
            try:
                set_control_mode(ip, restore)
                if current_control_mode(ip) == restore:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("  ★★ 제어 모드를 {} 로 되돌리지 못했다. 콘솔 원격제어에서"
                  " 직접 '자동 모드' 로 바꿔라. 안 그러면 배차가 안 움직인다."
                  .format(restore))

    end_pose = wait_settle(ip)
    if end_pose is None:
        return {"ok": False, "error": "종료 포즈 읽기 실패", "start": start,
                "turned_deg": turned}
    if aborted:
        return {"ok": False, "error": aborted, "start": start,
                "end": end_pose, "turned_deg": turned}

    # 누적값(turned)이 곧 '로봇이 돌았다고 보고하는 각도' 다.
    return {
        "ok": True,
        "mode": "twist",
        "start": start,
        "end": end_pose,
        "steps": [{"step_deg": total_deg, "state": "succeeded",
                   "observed_dir": ("ccw" if turned > 0 else "cw"),
                   "speed_rad": speed_rad}],
        "commanded_deg": total_deg,
        "reported_deg": turned,
        "yaw_err_reported_deg": turned - total_deg,
        "pos_drift_mm": math.hypot(end_pose["x"] - start["x"],
                                   end_pose["y"] - start["y"]) * 1000.0,
    }


def rotate_once(ip, total_deg, chunk_deg):
    """제자리에서 total_deg 만큼 회전. 시작/종료 포즈와 단계별 결과를 돌려준다."""
    start = read_pose(ip)
    if start is None:
        return {"ok": False, "error": "시작 포즈 읽기 실패"}

    # 회전 중에도 x, y 는 시작 위치로 고정해서 보낸다 (제자리 회전 의도)
    base_x, base_y = start["x"], start["y"]

    steps = []
    remaining = total_deg
    cur_ori = start["ori"]
    sign = 1.0 if total_deg >= 0 else -1.0

    while abs(remaining) > 1e-6:
        step = sign * min(abs(chunk_deg), abs(remaining))
        target = norm_rad(cur_ori + math.radians(step))
        try:
            move_id = create_move(ip, base_x, base_y, target)
        except Exception as e:
            return {"ok": False, "error": "move 생성 실패: {}".format(e),
                    "start": start, "steps": steps}

        # 회전 시작 직후 포즈를 한 번 읽어 **실제 회전 방향**을 관찰한다.
        #   180도 단위로 명령하면 +180/-180 이 같은 절대각이라 방향을 지정할 수
        #   없다. 지정은 못 해도 **기록은 해야** 나중에 CW/CCW 를 갈라 볼 수 있다.
        observed_dir = None
        if abs(step) >= DIR_AMBIGUOUS_CHUNK_DEG:
            time.sleep(DIR_PROBE_DELAY_SEC)
            mid = read_pose(ip, timeout=2.0)
            if mid is not None:
                d = ang_diff_deg(mid["ori"], cur_ori)
                if abs(d) >= DIR_PROBE_MIN_DEG:
                    observed_dir = "ccw" if d > 0 else "cw"

        res = wait_move(ip, move_id)
        state = res.get("state")
        steps.append({"step_deg": step, "move_id": move_id, "state": state,
                      "observed_dir": observed_dir,
                      "fail": res.get("fail_message")})
        if state != "succeeded":
            return {"ok": False,
                    "error": "이동 {}: {}".format(state, res.get("fail_message")),
                    "start": start, "steps": steps}
        cur_ori = target
        remaining -= step

    end = wait_settle(ip)
    if end is None:
        return {"ok": False, "error": "종료 포즈 읽기 실패",
                "start": start, "steps": steps}

    # 로봇이 "돌았다고 보고하는" 누적 각도.
    # 최단각 정규화 때문에 180 이상은 부호가 뒤집혀 보일 수 있으므로
    # 명령한 총각에 가장 가까운 등가값으로 되돌려 해석한다.
    reported = ang_diff_deg(end["ori"], start["ori"])
    while reported - total_deg > 180:
        reported -= 360
    while total_deg - reported > 180:
        reported += 360

    return {
        "ok": True,
        "start": start,
        "end": end,
        "steps": steps,
        "commanded_deg": total_deg,
        "reported_deg": reported,
        "yaw_err_reported_deg": reported - total_deg,
        "pos_drift_mm": math.hypot(end["x"] - start["x"],
                                   end["y"] - start["y"]) * 1000.0,
    }


# ── 메인 ──────────────────────────────────────────────────────

def preflight(ip, script_mode="moves"):
    print("[점검] {} 섀시 상태 확인 중...".format(ip))
    try:
        st = get_status(ip)
    except Exception as e:
        print("  [실패] /chassis/status 응답 없음: {}".format(e))
        print("  → IP·전원·네트워크를 확인하라")
        return False
    estop = st.get("emergency_stop_pressed")
    mode = st.get("control_mode", "?")
    overload = st.get("wheel_overloaded")
    print("  비상정지={}  제어모드={}  바퀴과부하={}".format(estop, mode, overload))
    if estop:
        print("  [중단] 비상정지가 눌려 있다. 해제 후 다시 실행하라")
        return False
    if overload:
        print("  [중단] 바퀴 과부하 상태다. 원인을 없앤 뒤 실행하라")
        return False
    if script_mode == "twist":
        # twist 는 remote 모드가 **필요하다.** 스크립트가 알아서 바꾸고 되돌린다.
        print("  → twist 모드: 회전 동안 remote 로 바꿨다가 끝나면 auto 로 되돌린다")
        if mode == "remote":
            print("  [주의] 이미 remote 다. 이전 실행이 비정상 종료했을 수 있다 —"
                  " 끝나면 auto 로 되돌려 놓는다")
        return True
    # moves 모드는 auto 여야 한다 (내비게이션이 목표각까지 몰아준다)
    if mode == "remote":
        print("  [중단] 원격(수동) 모드다. auto 로 바꾼 뒤 실행하라")
        print("         (콘솔 원격제어 모달을 닫거나 '자동 모드' 를 누르면 된다)")
        return False
    return True


def main():
    global _stop_requested

    ap = argparse.ArgumentParser(description="제자리 회전 오차 측정")
    ap.add_argument("--ip", required=True, help="로봇 IP")
    ap.add_argument("--angle", type=float, default=360,
                    help="1시행 회전각(도). 기본 360")
    ap.add_argument("--dir", choices=["cw", "ccw"], default="ccw",
                    help="회전 방향. ccw=반시계(+), cw=시계(-). 기본 ccw")
    ap.add_argument("--repeat", type=int, default=5, help="반복 횟수. 기본 5")
    ap.add_argument("--chunk", type=float, default=DEFAULT_CHUNK_DEG,
                    help="몇 도씩 쪼개 명령할지. 기본 {} (360도면 180x2). "
                         "180 이상은 회전 방향을 로봇이 정하므로 --dir 이 무시된다. "
                         "방향을 강제하려면 --chunk 90"
                         .format(DEFAULT_CHUNK_DEG))
    ap.add_argument("--mode", choices=["moves", "twist"], default="moves",
                    help="moves=목표각 이동(기본, 중간에 멈춤) / "
                         "twist=각속도 직접 제어(360도를 한 번에 쭉 돈다)")
    ap.add_argument("--rot-speed", type=float, default=TWIST_SPEED_RAD,
                    help="twist 모드 각속도(rad/s). 기본 {} (약 {:.0f}도/s)"
                         .format(TWIST_SPEED_RAD, math.degrees(TWIST_SPEED_RAD)))
    ap.add_argument("--label", default="", help="조건 이름 (예: S300)")
    ap.add_argument("--no-pause", action="store_true",
                    help="시행 사이에 멈추지 않는다(누적 오차 측정용)")
    ap.add_argument("--outdir", default="_logs", help="결과 저장 폴더. 기본 _logs")
    ap.add_argument("--dry-run", action="store_true",
                    help="로봇을 움직이지 않고 연결·포즈만 확인")
    args = ap.parse_args()

    def _on_sigint(signum, frame):
        global _stop_requested
        _stop_requested = True
        print("\n[중지] 현재 이동을 취소한다...")

    signal.signal(signal.SIGINT, _on_sigint)

    if not preflight(args.ip, args.mode):
        return 1

    pose = read_pose(args.ip)
    if pose is None:
        print("[실패] tracked_pose 를 읽을 수 없다 — 맵 로드·위치추정 상태를 확인하라")
        return 1
    print("  현재 포즈  x={:.4f}  y={:.4f}  ori={:.4f} rad ({:.2f} deg)".format(
        pose["x"], pose["y"], pose["ori"], math.degrees(pose["ori"])))

    if args.dry_run:
        print("\n[dry-run] 로봇을 움직이지 않고 종료한다.")
        return 0

    total = args.angle if args.dir == "ccw" else -args.angle
    os.makedirs(args.outdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = (args.label + "_") if args.label else ""
    jsonl_path = os.path.join(args.outdir, "rot_{}{}.jsonl".format(tag, stamp))
    csv_path = os.path.join(args.outdir, "rot_{}{}_기록지.csv".format(tag, stamp))

    # twist 는 각속도 부호로 방향을 직접 정하므로 방향이 확정된다.
    dir_ambiguous = (args.mode == "moves" and args.chunk >= DIR_AMBIGUOUS_CHUNK_DEG)
    if args.mode == "twist":
        print("\n조건: {} · {:g}도 {} · {}회 · **연속 회전**(twist, {:.2f} rad/s)".format(
            args.label or "(이름없음)", args.angle, args.dir,
            args.repeat, args.rot_speed))
        print("\n⚠ twist 는 목표 없는 '속도 명령' 이다. 명령을 끊으면 로봇이 선다.")
        print("  끝날 때·중단할 때 0 속도를 여러 번 보내고, 예상시간 3배가 지나면")
        print("  스스로 멈춘다. **첫 회차는 반드시 옆에서 보고 이상하면 Ctrl+C.**")
        print("  정확히 360.0 도에서 서지는 않는다(오버슈트) — 측정에는 지장 없다.")
    else:
        print("\n조건: {} · {:g}도 {} · {}회 · {:g}도씩 분할".format(
            args.label or "(이름없음)", args.angle,
            "(방향 미지정)" if dir_ambiguous else args.dir, args.repeat, args.chunk))
    if dir_ambiguous:
        print("\n⚠ --chunk {:g} 는 회전 방향을 지정할 수 없다.".format(args.chunk))
        print("  +180 과 -180 이 같은 절대각이라 **로봇이 어느 쪽으로 돌지 스스로 정한다.**")
        print("  --dir {} 은 이번 실행에서 무시된다.".format(args.dir))
        print("  대신 실제로 돈 방향을 관찰해 기록한다(시행마다 아래에 표시).")
        print("  CW/CCW 를 의도대로 강제하려면 --chunk 90 으로 다시 실행하라.")
    print("기록: {}".format(jsonl_path))
    print("=" * 66)
    if not args.no_pause:
        print("각 시행이 끝나면 멈춘다. 바닥 마킹을 하고 엔터를 눌러라.")
        print("★ 1회차는 반드시 옆에서 지켜보고, 정말 '제자리'에서 도는지 확인하라.")
        print("  (앞으로 밀고 나가면 즉시 Ctrl+C)")
        try:
            input("\n준비되면 엔터... ")
        except (EOFError, KeyboardInterrupt):
            return 1

    results = []
    for i in range(1, args.repeat + 1):
        if _stop_requested:
            break
        print("\n── 시행 {}/{} ──".format(i, args.repeat))
        t0 = time.time()
        if args.mode == "twist":
            r = rotate_once_twist(args.ip, total, args.rot_speed)
        else:
            r = rotate_once(args.ip, total, args.chunk)
        r["trial"] = i
        r["label"] = args.label
        r["dir"] = args.dir
        r["mode"] = args.mode
        r["chunk_deg"] = (None if args.mode == "twist" else args.chunk)
        r["elapsed_sec"] = round(time.time() - t0, 1)
        r["ts"] = datetime.now().isoformat(timespec="seconds")
        results.append(r)

        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

        if not r.get("ok"):
            print("  [실패] {}".format(r.get("error")))
            if _stop_requested:
                break
            continue

        print("  명령 {:+.1f} deg  /  로봇 보고 {:+.2f} deg  (차이 {:+.2f} deg)".format(
            r["commanded_deg"], r["reported_deg"], r["yaw_err_reported_deg"]))
        print("  로봇 보고 위치 이동 {:.1f} mm   소요 {:.0f}초".format(
            r["pos_drift_mm"], r["elapsed_sec"]))
        obs = [s.get("observed_dir") for s in r.get("steps", [])]
        if any(obs):
            print("  실제 회전 방향(관찰): {}".format(
                " → ".join(o or "?" for o in obs)))
        print("  ※ 위 값은 '로봇이 스스로 보고한' 값이다. 실제 오차는 바닥 마킹으로 재라.")

        if not args.no_pause and i < args.repeat and not _stop_requested:
            try:
                input("  마킹 완료 후 엔터... ")
            except (EOFError, KeyboardInterrupt):
                break

    # ── 요약 + 수기 기록지 생성 ──
    ok = [r for r in results if r.get("ok")]
    print("\n" + "=" * 66)
    print("완료: {}/{} 시행 성공".format(len(ok), len(results)))
    if ok:
        errs = [r["yaw_err_reported_deg"] for r in ok]
        drifts = [r["pos_drift_mm"] for r in ok]
        n = len(errs)
        mean_e = sum(errs) / n
        sd_e = (sum((e - mean_e) ** 2 for e in errs) / n) ** 0.5
        print("\n[로봇 자체 보고 기준 — 참고값]")
        print("  yaw 오차   평균 {:+.3f} deg · 표준편차 {:.3f} · 최대 {:+.3f}".format(
            mean_e, sd_e, max(errs, key=abs)))
        print("  위치 이동  평균 {:.1f} mm · 최대 {:.1f} mm".format(
            sum(drifts) / n, max(drifts)))
        print("\n  ※ 로봇은 자기 오차를 과소평가한다. 이 값은 바닥 실측과 '비교'할")
        print("    대상이지, 이 값 자체가 오차가 아니다.")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["시행", "조건", "각도", "방향(관찰)",
                    "로봇보고_yaw오차(deg)", "로봇보고_위치이동(mm)",
                    "A0_x(mm)", "A0_y(mm)", "B0_x(mm)", "B0_y(mm)",
                    "A1_x(mm)", "A1_y(mm)", "B1_x(mm)", "B1_y(mm)",
                    "스팬검산_전(mm)", "스팬검산_후(mm)", "비고"])
        for r in results:
            # 방향은 '지정한 값' 이 아니라 **실제로 관찰한 값** 을 적는다.
            # 180도 단위 명령에서는 로봇이 방향을 스스로 정하므로 args.dir 은 신뢰할 수 없다.
            obs = [s.get("observed_dir") for s in r.get("steps", [])]
            dir_cell = ("/".join(o or "?" for o in obs) if any(obs) else args.dir)
            if r.get("ok"):
                w.writerow([r["trial"], r.get("label", ""), args.angle, dir_cell,
                            "{:+.3f}".format(r["yaw_err_reported_deg"]),
                            "{:.1f}".format(r["pos_drift_mm"])] + [""] * 11)
            else:
                w.writerow([r["trial"], r.get("label", ""), args.angle, dir_cell,
                            "실패", r.get("error", "")] + [""] * 11)
    print("\n수기 기록지: {}".format(csv_path))
    print("  → A0/B0/A1/B1 칸에 바닥 실측값(mm)을 적어라.")
    print("  → 스팬검산 두 값의 차이가 2mm 를 넘으면 그 시행은 마킹 오류다. 폐기하라.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
