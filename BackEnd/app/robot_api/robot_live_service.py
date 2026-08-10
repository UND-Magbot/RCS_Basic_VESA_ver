import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from websocket import WebSocketException, create_connection


PORT = 8090
# LTE(M2M) 환경 대응: 왕복 지연이 크고(수백 ms~1s+) 간헐적 스파이크가 있어
# 짧은 타임아웃이면 정상 로봇도 오프라인으로 오판된다. 넉넉히 상향.
HTTP_TIMEOUT = 10
WS_TIMEOUT = 12

REST_ENDPOINTS = {
    "device_info": "/device/info",
    "wifi_info": "/device/wifi_info",
}
WS_TOPICS = ["/planning_state", "/detailed_battery_state", "/battery_state"]


def _get(ip: str, secret: str, path: str, timeout: float = HTTP_TIMEOUT) -> dict:
    url = f"http://{ip}:{PORT}{path}"
    res = requests.get(url, headers={"Secret": secret}, timeout=timeout)
    res.raise_for_status()
    return res.json()


def _collect_ws_topics(ip: str, topics: list[str], timeout_sec: int = WS_TIMEOUT) -> tuple[dict, str | None]:
    collected: dict = {}
    ws = None
    ws_url = f"ws://{ip}:{PORT}/ws/v2/topics"
    deadline = time.time() + timeout_sec

    try:
        ws = create_connection(ws_url, timeout=HTTP_TIMEOUT)

        for topic in topics:
            ws.send(json.dumps({"enable_topic": topic}))

        while time.time() < deadline:
            raw = ws.recv()
            if not raw:
                continue

            packet = json.loads(raw)
            topic_name = packet.get("topic")
            if topic_name in topics:
                collected[topic_name] = packet
                if "/planning_state" in collected and (
                    "/detailed_battery_state" in collected or "/battery_state" in collected
                ):
                    break

        return collected, None
    except (WebSocketException, OSError, json.JSONDecodeError) as exc:
        return collected, str(exc)
    finally:
        if ws is not None:
            ws.close()


def _to_runstate(planning: dict, battery: dict, online: bool) -> str:
    if not online:
        return "OFFLINE"
    if not planning and not battery:
        return "N/A"

    move_state = str(planning.get("move_state", "")).lower()
    power_supply_status = str(battery.get("power_supply_status", "")).lower()
    action_type = str(planning.get("action_type", "")).lower()
    waiting_for_dest = planning.get("is_waiting_for_dest") is True

    if move_state == "moving":
        return "EXECUTING"

    # 충전 판정: power_supply_status(BMS)를 우선 확인
    # discharging/not_charging → 충전 아님 (이전 charge 액션이 남아있어도 무시)
    if power_supply_status in {"discharging", "not_charging"}:
        if move_state in {"idle", "failed", "cancelled", "succeeded"} or waiting_for_dest:
            return "IDLE"
        return "IDLE"

    if power_supply_status in {"charging", "full"}:
        return "CHARGING"

    # power_supply_status 정보 없을 때만 action_type 폴백
    if action_type == "charge" and move_state in {"idle", "none", "succeeded"}:
        return "CHARGING"

    if move_state in {"idle", "failed", "cancelled", "succeeded"} or waiting_for_dest:
        return "IDLE"

    return "IDLE"

def _to_power(percentage: Any, online: bool) -> str:
    if not online:
        return "-"
    if not isinstance(percentage, (int, float)):
        return "N/A"
    if percentage <= 1:
        percentage = percentage * 100
    return f"{max(0, min(100, int(round(percentage))))}%"


def _to_signal(wifi_info: dict, online: bool) -> str:
    if not online:
        return "N/A"

    ap = wifi_info.get("active_access_point", {}) if isinstance(wifi_info, dict) else {}
    if isinstance(ap.get("strength"), (int, float)):
        return f"{int(round(max(0, min(100, ap['strength']))))}%"
    if isinstance(wifi_info.get("strength"), (int, float)):
        return f"{int(round(max(0, min(100, wifi_info['strength']))))}%"
    return "N/A"


def fetch_robot_live(ip: str, secret: str) -> dict:
    rest_data: dict = {}
    errors: dict = {}
    online = False

    # 1) 온라인 판정은 '가벼운' 경로로 먼저 확정한다.
    #    LTE(M2M) 환경에서 /device/info 는 응답이 커서 경로가 막히는(0 bytes) 사례가 있어,
    #    그것에 의존하면 멀쩡한 로봇이 오프라인으로 잡혀 배차에서 빠진다.
    #    /chassis/current-map 은 응답이 작아 LTE 에서도 안정적으로 온다.
    try:
        # 상태코드는 따지지 않는다 — 응답이 오기만 하면(맵 미설정 404 포함) 로봇은 살아있다.
        # raise_for_status 를 하면 맵이 안 잡힌 로봇이 오프라인으로 오판된다.
        requests.get(
            f"http://{ip}:{PORT}/chassis/current-map",
            headers={"Secret": secret},
            timeout=HTTP_TIMEOUT,
        )
        online = True
    except requests.RequestException as exc:
        errors["online_probe"] = str(exc)

    # 2) 상세 정보(device_info/wifi_info) — 성공하면 채우고, 실패해도 온라인 판정엔 영향 없음.
    #    device_info 는 LTE 에서 막혀 오래 걸리므로 짧게만 시도한다(온라인은 위에서 이미 확정).
    #    안 그러면 목록 조회가 device_info 타임아웃만큼 느려지고 불안정해진다.
    for key, path in REST_ENDPOINTS.items():
        try:
            to = 3.0 if key == "device_info" else HTTP_TIMEOUT
            rest_data[key] = _get(ip, secret, path, timeout=to)
            online = True
        except requests.RequestException as exc:
            errors[key] = str(exc)

    ws_data, ws_error = _collect_ws_topics(ip, WS_TOPICS)
    if ws_error:
        errors["ws_topics"] = ws_error

    device_info = rest_data.get("device_info", {}).get("device", {})
    planning = ws_data.get("/planning_state", {})
    battery = ws_data.get("/detailed_battery_state", {}) or ws_data.get("/battery_state", {})
    wifi_info = rest_data.get("wifi_info", {})

    return {
        "IP": ip,
        "SN": device_info.get("sn", "N/A"),
        "ROBOTNAME": device_info.get("name", "N/A"),
        "MODEL": device_info.get("model", "N/A"),
        "NICKNAME": device_info.get("nickname"),
        "AXBOT_VERSION": rest_data.get("device_info", {}).get("axbot_version"),
        "PLATFORM": device_info.get("platform"),
        "RUNSTATE": _to_runstate(planning, battery, online),
        "ONLINE": "Online" if online else "Offline",
        "SIGNAL": _to_signal(wifi_info, online),
        "POWER(%)": _to_power(battery.get("percentage"), online),
        "errors": errors,
    }


def fetch_all_robots_live(robots: list[dict]) -> dict:
    if not robots:
        return {"total": 0, "items": []}

    items: list[dict] = []
    max_workers = min(16, len(robots))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_robot_live, robot["ip"], robot["secret"]): robot
            for robot in robots
        }

        for future in as_completed(future_map):
            robot = future_map[future]
            try:
                items.append(future.result())
            except Exception as exc:
                items.append(
                    {
                        "IP": robot.get("ip", "N/A"),
                        "SN": "N/A",
                        "ROBOTNAME": "N/A",
                        "MODEL": "N/A",
                        "NICKNAME": None,
                        "AXBOT_VERSION": None,
                        "PLATFORM": None,
                        "RUNSTATE": "OFFLINE",
                        "ONLINE": "Offline",
                        "SIGNAL": "N/A",
                        "POWER(%)": "-",
                        "errors": {"fetch_robot_live": str(exc)},
                    }
                )

    items.sort(key=lambda x: str(x.get("IP", "")))
    return {"total": len(items), "items": items}
