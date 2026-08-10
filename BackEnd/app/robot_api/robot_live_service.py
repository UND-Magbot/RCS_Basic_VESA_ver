import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from websocket import WebSocketException, create_connection


PORT = 8090
HTTP_TIMEOUT = 3
WS_TIMEOUT = 4

REST_ENDPOINTS = {
    "device_info": "/device/info",
    "wifi_info": "/device/wifi_info",
}
WS_TOPICS = ["/planning_state", "/detailed_battery_state", "/battery_state"]


def _get(ip: str, secret: str, path: str) -> dict:
    url = f"http://{ip}:{PORT}{path}"
    res = requests.get(url, headers={"Secret": secret}, timeout=HTTP_TIMEOUT)
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
                # /planning_state 를 아예 요청하지 않은 호출(예: is_charging — 배터리만 필요)은
                # 요청한 토픽이 하나라도 오면 더 기다릴 이유가 없다.
                # (기존 호출부는 WS_TOPICS 에 /planning_state 가 있으므로 동작 불변)
                if "/planning_state" not in topics:
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

    for key, path in REST_ENDPOINTS.items():
        try:
            rest_data[key] = _get(ip, secret, path)
            if key == "device_info":
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


def is_charging(ip: str, timeout_sec: int = 3) -> bool | None:
    """로봇이 지금 충전(도킹) 중인지 — 배터리 토픽만 가볍게 확인.

    반환:
      True  — 확실히 충전 중 (power_supply_status = charging / full)
      False — 확실히 충전 중 아님 (discharging / not_charging)
      None  — 판정 불가 (오프라인, WS 실패, 필드 없음)

    fetch_robot_live() 는 REST 2회(각 3초) + WS 4초라 최악 10초가 걸린다.
    충전 여부만 필요한 호출부(맵핑 시작 가드 등)는 이 함수를 쓴다 — 최대 timeout_sec.

    ⚠️ 호출부는 None 을 "차단"으로 해석하지 말 것. 판정 불가일 때 막으면
    멀쩡한 작업이 막힌다 (fail-open 이 맞다).
    """
    ws_data, ws_error = _collect_ws_topics(
        ip, ["/detailed_battery_state", "/battery_state"], timeout_sec=timeout_sec
    )
    if ws_error and not ws_data:
        return None
    battery = ws_data.get("/detailed_battery_state", {}) or ws_data.get("/battery_state", {})
    status = str(battery.get("power_supply_status", "")).lower()
    if status in {"charging", "full"}:
        return True
    if status in {"discharging", "not_charging"}:
        return False
    return None


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
