# RCS Basic VESA 프로젝트

## 언어
- 모든 답변은 한국어로 작성

## 프로젝트 개요
- RCS (Robot Control System) **VESA/LG 버전** — Basic 모델에서 분기, 인터랙티브 배차 운영 방식으로 전환
- AutoXing 로봇 제어 시스템. 현장 로봇: **longjack 모델(펌웨어 2.12.28-pi64)**, LG 대차(랙) 운반. (구 crawler_s300_op5 계열도 지원)
- 백엔드: FastAPI (Python 3.10) + MariaDB
- 프론트엔드: Next.js 16 + TypeScript + Three.js (3D 모니터링)
- 태블릿: Android WebView 셸 (Kotlin) + 백엔드 서빙 HTML (콘솔용 / 로봇부착용 2종)
- DB: 원격 MariaDB (192.168.0.21, **rcs_vesa_db**) / 로컬 개발 **rcs_lg_db** (`run_local.ps1`)
- 로컬 REST API만 사용 (CSP/클라우드 미사용). **LTE(M2M) 망 운영 대응** — 유동 IP·지연 보정
- GitHub: https://github.com/UND-Magbot/RCS_Basic_VESA_ver (브랜치: `feature/backend_noah`)

### 운영 UI 방식 (현행)
- **관제 메인화면(Next.js `/monitoring`)** — 3D 모니터링 + 로봇별 원격제어(방향/잭/충전소복귀/시스템재시작/**작업 강제 종료**)
- **콘솔(`/api/dispatch/console`)** — 범용 단말 1대로 모든 POI 호출/예약/경유지출발/종료 + 우측 로봇 사이드바(상태·배터리) + 카드별 [직접 제어](원격제어 모달)
- **로봇 부착 태블릿(`/api/dispatch/robot-tablet/{id}`)** — 경유지 진행용 [확인]/[작업 종료]
- (레거시) 위치별 태블릿(`/api/dispatch/tablet/...`) — 콘솔로 대체됨

## VESA 운영 방식 (인터랙티브 배차 — 핵심)

**위치별 태블릿이 시작 트리거**. 관제 일괄 시작 없음. 작업자가 자기 위치 태블릿에서 호출/다음/종료를 직접 지시.

### 흐름
```
[충전소] 로봇 3대 idle
  ↓
[호출] 위치 A 태블릿에서 [로봇 호출] → 시스템이 가용 로봇 1대 선정(배터리 1순위)
  ↓
[픽업 1회] 선정 로봇: standby → align_with_rack → jack_up
  ↓
[이동] 위치 A로 이동 → 도착 (잭 유지)
  ↓
[다음 보내기] 위치 A 태블릿에서 비어있는 다른 위치 누름 → 이동 → 도착 (잭 유지)
  ↓
[루프] 그 위치 태블릿에서 다음 위치 누르거나 [종료]
  ↓
[종료] [작업 종료] → standby 복귀 → jack_down → 충전소 도킹
```

### 핵심 단순화
- **align_with_rack 1회**, **jack_up 1회**, **jack_down 1회** (잭 모터 부하 최소화)
- 포지션 도착 = 잭 유지 상태로 대기 (작업자가 렉 위 화물만 처리)
- 잭 사이클(픽업/드롭오프마다 업/다운) **없음**

### 상태 머신 (`dispatch_sessions.status`)
- `starting` → `picking_up` → `moving` → `awaiting_next` ↔ (`moving` → `awaiting_confirm`) → `returning` → `completed`/`failed`
- `awaiting_next` : 호출 위치 도착, 콘솔에서 경유지 등록 대기
- `awaiting_confirm` : 경유지 도착, 로봇 태블릿/콘솔의 [확인] 대기 (확인 시 다음 경유지, 마지막이면 종료)
- `returning` 진입 후에는 모든 next/end 명령 **무시**

### 경유지(route) 흐름 (콘솔 운영)
1. 콘솔에서 도착한 로봇에 **경유지들을 순서대로 등록 + [출발]** (`POST /poi/{id}/route` body `{waypoints:[...]}`)
2. 각 경유지 도착 → `awaiting_confirm` → 로봇 태블릿(또는 콘솔)의 **[확인]**(`POST /robot/{robot_id}/confirm`)으로 다음 진행
3. 마지막 경유지 [확인] → 자동 종료(복귀). `send_route`는 출발 상태를 **동기적으로 먼저 반영**해 UI 롤백 방지

### POI 점유 / 동시성
- 3대 동시 운영. `dispatch_sessions.current_poi_id` + `target_poi_id`로 점유 추적
- 태블릿 그리드에서 다른 로봇 점유 POI는 비활성화 (백엔드도 한 번 더 검증)
- 호출 시 그 POI를 이미 다른 로봇이 점유 / 그쪽으로 이동 중이면 호출 거부
- `zone_guard.acquire_zones_for_move`, `poi_lock`은 기존 그대로 활용

### 로봇 자동 배정
- 가용 로봇 = 활성 워커 없음 (=충전소 대기) + `is_active=True` + `ip_address` 있음
- 우선순위: **`robot_status.battery_level` 내림차순 → robot_id 오름차순**
- 가용 로봇 0대면 호출 거부 (태블릿에 "가용 로봇 없음")

## 디렉토리 구조
- `BackEnd/` — FastAPI 백엔드
  - `app/routers/` — `auth, user, robot, map, alarm_log, activity_log, backup, log, jack_test, task, **dispatch**`
  - `app/models/` — SQLAlchemy 모델 (+ `dispatch.py`)
  - `app/crud/` — DB CRUD (+ `dispatch.py`)
  - `app/schemas/` — Pydantic (+ `dispatch.py`)
  - `app/services/` — `dispatch_service`(VESA 워커), `jack_service`, `scheduler`, `zone_guard`, `zone_lock`, `poi_lock`, `geometry`, `deadlock_monitor`, `thread_utils`, **`boot_recovery`**(부팅 후 자동 복구), `rack_detect_service`
  - `app/constants/` — `robot_types`(work_mode), `rack_specs`(랙 사이즈별 — S300/S600/LG/LG2, 맵 POI 기준 동적 전송)
  - `app/templates/` — `tablet.html`·`dispatch_tablet.html`(레거시 위치별), **`dispatch_console.html`**(범용 콘솔+로봇 사이드바+원격제어), **`dispatch_robot_tablet.html`**(로봇 부착 태블릿)
- `frontend/` — Next.js 프론트엔드
  - `app/monitoring/` — 3D 모니터링 (Three.js)
  - `app/map/` — 맵 관리
  - `app/robots/` — 로봇 관리
  - `app/tasks/`, `app/routes/`, `app/schedules/` — 레거시 자동 모드용 (VESA에서는 미사용)
- `TabletApp/` — Android WebView 셸 (`MainActivity.kt`) → `/api/dispatch/tablet/{robot_id}` 로드
- `docker-compose.yml` — 배포용 Docker 설정
- `작업일지.md` — 일일 작업 기록 (최신순)

## VESA 신규 API

기본 prefix: `/api/dispatch`

### POI 기준 (신 운영 방식 — 위치별 태블릿)
| Method | Endpoint | 동작 |
|---|---|---|
| POST | `/poi/{poi_id}/call` | 그 POI로 가용 로봇 1대 호출 (배터리 1순위) |
| POST | `/poi/{poi_id}/next` | body `{next_poi_id}` — 그 POI에 있는 로봇을 비어있는 POI로 보냄 |
| POST | `/poi/{poi_id}/end` | 그 POI에 있는 로봇만 종료 (standby + 잭다운 + 충전소) |
| GET | `/poi/{poi_id}/status` | 그 POI의 상태 (empty/calling/arrived) + 가용 다음 POI + 가용 로봇 수 |
| GET | `/tablet/poi/{poi_id}` | **위치별 태블릿 HTML** (메인 페이지) |

### 콘솔 / 경유지 / 로봇 태블릿 (현행 운영)
| Method | Endpoint | 동작 |
|---|---|---|
| GET | `/console` | **범용 콘솔 HTML** (모든 POI + 로봇 사이드바 + 원격제어) |
| GET | `/console/status` | 콘솔 폴링 — POI 상태·점유·`reserved_poi_ids`·가용수 + **`robots`(사이드바)** |
| POST | `/poi/{poi_id}/call` | 가용 로봇 호출 (없으면 예약). body `{robot_type, with_rack}` |
| POST | `/poi/{poi_id}/route` | body `{waypoints:[...]}` — 경유지 등록 + 출발 |
| DELETE | `/poi/{poi_id}/reserve` | 대기 예약 취소 |
| GET | `/robot/{robot_id}/tablet-status` | 로봇 부착 태블릿 폴링 (현재/다음 경유지, 확인 가능 여부) |
| POST | `/robot/{robot_id}/confirm` | 로봇/콘솔 [확인] — 다음 경유지(마지막이면 종료) |
| POST | `/robot/{robot_id}/end` | 로봇 [작업 종료] — 복귀 시퀀스 |
| GET | `/robot-tablet/{robot_id}` | 로봇 부착 태블릿 HTML |

### 원격제어 (관제/콘솔 공통, `/api/robots/remote`)
| Method | Endpoint | 동작 |
|---|---|---|
| POST | `/control-mode/{ip}`, `/twist/{ip}`, `/twist-close/{ip}` | 원격 모드 전환 / 방향 조종 |
| POST | `/jack/{ip}/{jack_up\|jack_down}`, `/dock/{ip}`, `/return-to-standby/{ip}` | 잭 / 충전소·대기장소 복귀 |
| POST | `/cancel-move/{ip}`, `/stop-all/{ip}`, `/relocalize/{ip}` | 정지 / 시스템 재시작 |
| POST | **`/clear-dispatch/{ip}`** | **작업 강제 종료** — 로봇 이동 없이 배차 세션만 정리(로봇 즉시 가용) |

### 로봇/관제 기준 (모니터링/레거시)
| Method | Endpoint | 동작 |
|---|---|---|
| POST | `/start` | (레거시) body `[{robot_id, first_poi_id}]` — 직접 시작 |
| POST | `/{robot_id}/next`, `/{robot_id}/end` | 로봇 기준 명령 |
| GET | `/status` | 모든 활성 세션 + 점유 POI 목록 (관제용) |
| GET | `/{robot_id}/status`, `/{robot_id}/pois` | 로봇 단위 조회 |

## AutoXing 로봇 API (변경 없음)

### REST API
- 기본 URL: `http://{robot_ip}:8090/`
- 이동: `POST /chassis/moves` (type: standard, align_with_rack, to_unload_point, charge)
- 잭: `POST /services/jack_up`, `POST /services/jack_down`
- 맵: `GET/POST/PATCH/DELETE /maps/{id}`
- 현재 맵: `GET/POST /chassis/current-map`
- 설정: `GET/PATCH /system/settings/user`
- 서비스 재시작: `POST /services/restart_py_axbot`

### Shelves Point (필수)
- overlay에 POI `type="34"`, `subtype="rack"`으로 등록해야 `align_with_rack` 동작
- 필수 속성:
  ```json
  {
    "type": "34",
    "subtype": "rack",
    "shelvesState": "0",
    "hasFixedLegs": false,
    "dockViaDirection": "front",
    "mapOverlay": true
  }
  ```
- PATCH 후 `POST /chassis/current-map` 재선택으로 overlay 리로드

### rack.specs — 랙 정렬(align_with_rack) 매칭 파라미터
- 정의: `app/constants/rack_specs.py` (S300 / S600 / **LG** / **LG2**). 동기화 시 **그 맵의 standby/jack POI 가 가진 `rack_size` 종류만큼만** 로봇에 전송(`build_rack_specs_for_map`) — 여러 spec 이 뜨면 오매칭하므로 사용 사이즈만.
- POI 편집(프론트)에서 랙 위치(standby) 에 사이즈 선택: `S600/S300/LG/LG2` (`frontend/lib/types/map.ts`, `POIEditPopup.tsx`).

**핵심 원칙 — width/depth 는 "도면 치수"가 아니라 "라이다가 재는 다리 중심 간격(detected)"에 맞춘다.**
`Wrong rack size: detected(a,b), configed(c,d)` 오류는 spec 값이 실측과 안 맞을 때 발생 → **로그의 detected 값을 그대로 넣는다.** 검출이 흔들리면(캐스터 간섭 등) 관측 범위 중앙값 사용.

- `leg_shape`/`leg_size`/`foot_radius` : 다리 검출 기준. **실제 구조 다리 지름에 맞춰야** 옆 캐스터를 다리로 오인식 안 함(예 25mm → leg_size 0.025 / foot_radius 0.0125).
- `margin[4]` : 랙 풋프린트(장애물 제외 영역). 캐스터가 다리 밖으로 튀어나와 장애물로 잡히면 margin 을 키워 랙 일부로 포함(예 0.08).
- 중앙에 늘어진 체인 등 물리 간섭물은 소프트웨어로 못 거름 → 물리적으로 제거 필요.

LG2 실측 예 (2026-08): `width 0.64, depth 0.545(0.52~0.57 중앙), margin 0.08, leg_shape round, leg_size 0.025, foot_radius 0.0125`.

```json
{ "rack.specs": [{
  "width": 0.83, "depth": 0.87, "margin": [0.1,0.1,0.1,0.1],
  "alignment": "center", "alignment_margin_back": 0.02,
  "leg_shape": "other", "leg_size": 0.05, "foot_radius": 0.025,
  "cargo_to_jack_front_edge_min_distance": 0.05
}] }
```

### WebSocket 토픽
- `/detected_rack` — 랙 감지 상태
- `/jack_state` — 잭 상태 (jacking_up, jacking_down, hold)
- `/robot_model` — 로봇 풋프린트 (잭 업 시 확장)
- `/planning_state` — 이동 상태 (`is_waiting_for_dest`, `remaining_distance`)

## DB 구조 (rcs_vesa_db)

기존 18개 테이블 + VESA 신규 1개:

| 테이블 | 용도 |
|---|---|
| `robots` | 로봇 목록 (IP 등록, `charging_id`, `standby_id`, `max_speed`, `robot_type`) |
| `robot_status`, `robot_status_history` | 실시간/이력 상태 |
| `robot_maps` | 맵 메타데이터 (`area_id` 기준 활성 맵) |
| `map_pois` | POI (`poi_type`: general / standby / jack / charging) |
| `map_lines`, `map_polygons` | 라인, 폴리곤(zone 포함) |
| `businesses`, `areas` | 사업장, 영역(층) |
| `users`, `user_roles` | 사용자 |
| `activity_logs`, `alarm_logs` | 활동/알람 로그 |
| `task_routes`, `task_route_waypoints`, `scheduled_tasks`, `task_history` | 레거시 자동 모드용 (VESA 미사용, 데이터 보존) |
| **`dispatch_sessions`** | **VESA 인터랙티브 배차 세션 (상태 머신 영속)** |

## 개발 규칙
- 백엔드 실행: `cd BackEnd; $env:DB_NAME="rcs_vesa_db"; python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- 프론트엔드 실행: `cd frontend && npm run dev`
- Git 브랜치: `feature/backend_noah` → `dev`
- CSS @import 사용 금지 (Turbopack 호환 문제, globals.css에 인라인)
- POI world 좌표: DB에 `world_x`, `world_y` 저장 (pixel 좌표와 별도)
- 맵 동기화 시 overlay에 Shelves Point(type=34) 포함 필요

## 배포 가이드

### Docker 배포 (서버)
```bash
cd ~/RCS_Basic_VESA_ver
cat > .env << 'EOF'
SERVER_IP=서버IP
DB_PORT=3306
DB_USER=root
DB_PASSWORD=1234
DB_NAME=rcs_vesa_db
NEXT_PUBLIC_API_URL=http://서버IP:8002
EOF
docker-compose up -d --build
```

### rcs_basic_db → rcs_vesa_db 마이그레이션 (완료된 작업 기록)
```python
# 1) DB 복제 (스키마 + 데이터)
import pymysql
conn = pymysql.connect(host='192.168.0.21', user='root', password='1234', charset='utf8mb4')
cur = conn.cursor()
cur.execute("CREATE DATABASE rcs_vesa_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
cur.execute("USE rcs_basic_db"); cur.execute("SHOW TABLES")
tables = [r[0] for r in cur.fetchall()]
cur.execute("SET FOREIGN_KEY_CHECKS=0")
for t in tables:
    cur.execute(f"CREATE TABLE rcs_vesa_db.`{t}` LIKE rcs_basic_db.`{t}`")
    cur.execute(f"INSERT INTO rcs_vesa_db.`{t}` SELECT * FROM rcs_basic_db.`{t}`")
cur.execute("SET FOREIGN_KEY_CHECKS=1")
conn.commit()

# 2) FK 정의 복원 (CREATE TABLE LIKE는 FK를 미복사 — ALTER로 별도 추가)
#    information_schema.KEY_COLUMN_USAGE + REFERENTIAL_CONSTRAINTS 조인으로
#    rcs_basic_db의 FK 22개를 추출해서 rcs_vesa_db에 동일하게 ADD CONSTRAINT
```

### IP 변경 시
1. `.env`의 `SERVER_IP`와 `NEXT_PUBLIC_API_URL` 수정
2. `docker-compose down && docker rmi rcs_basic_vesa_ver_frontend && docker-compose build --no-cache frontend && docker-compose up -d`
3. 태블릿 앱 설정에서 서버 주소 변경

### 접속 주소
- 웹: `http://서버IP:3002`
- 백엔드 API: `http://서버IP:8002`
- 태블릿: 앱 설정 → 서버 주소 `http://서버IP:8002`, **작업 위치 POI ID** 입력 → `/api/dispatch/tablet/poi/{poi_id}` 자동 로드 (태블릿은 위치에 고정)

## 최근 개발 반영 (2026-08)

### 콘솔 + 로봇 사이드바 원격제어
- `dispatch_console.html` : 우측에 등록 로봇 카드(온라인·배터리·상태) + [직접 제어] → 원격제어 모달(방향/잭/충전소·대기장소 복귀/시스템 재시작/**작업 강제 종료**). 관제 `RemoteControlModal.tsx` 와 동일 기능.
- `console/status` 응답에 `robots`(사이드바용) + `reserved_poi_ids`(미래 경유지 예약 배지용) 추가.
- 콘솔에서도 로봇 태블릿 [확인] 대행 가능(`awaiting_confirm` 타일에 [확인] 버튼).

### 작업 강제 종료 (force_clear)
- `dispatch_service.force_clear(robot_id)` + `_Worker.abort_flag` : 복귀(충전소) 시퀀스 **없이** 세션만 종료 → **로봇은 물리적으로 그대로**(잭·위치 유지), 워커 해제로 즉시 재사용.
- 엔드포인트 `POST /api/robots/remote/clear-dispatch/{ip}` — 관제·콘솔 원격제어 버튼. (충전소로 보내려면 '충전소 복귀' 별도)

### 점유 판정 규칙 (예약/호출 버그 수정)
- `crud/dispatch.occupied_poi_ids` : **떠나는 중인 로봇의 출발지 제외** — `returning` + **`moving` 이고 target 있음**(다른 POI로 이동 중이면 current 는 비워진 출발지). 이걸 안 빼면 방금 떠난 POI 호출/예약이 "점유중"으로 거부됨.

### 위치재조정 (relocalize) — 충전소 기준
- 충전소 POI 기준 포즈 = `position: [world_x + DOCKING_OFFSET·cos(yaw), world_y + DOCKING_OFFSET·sin(yaw), 0]`, **`ori = yaw`(POI 방향 그대로, 180° 뒤집지 않음)**. `DOCKING_OFFSET=0.3`.
- 이 펌웨어는 `GET /chassis/pose` 가 help 텍스트만 반환 → 현재 포즈는 **WS `/tracked_pose`**(`{pos:[x,y], ori}`)로 읽고, 설정은 `POST /chassis/pose`(`{position:[x,y,0], ori}`).
- 공용 함수 `boot_recovery.relocalize_robot_to_dock(robot_id)` (부팅 복구·동기화 공용). 수동: `POST /api/map/relocalize`.

### 부팅 후 자동 복구 (`boot_recovery.py`)
- 로봇 부팅(30초~1분)이 길어 부팅 직후 맵 미설정·위치 리셋되던 문제 대응.
- 10초 주기로 온라인 점검 → **오프라인→온라인 전환(부팅 완료)** 시: current-map 설정(안 맞을 때만) + 충전소 기준 위치재조정. **작업 중(활성 워커) 로봇 제외**(도킹 위치가 아닐 수 있음). `main.py` lifespan 에서 start/stop.
- 전제: **유휴 로봇 = 충전소 도킹 상태**.

### 동기화(sync) 위치 보존 + 맵 설정 안정화 (`routers/map.py`)
- `_reselect_current_map_keep_pose()` : current-map 재선택(overlay 리로드) 전 포즈 저장 → **설정 검증하며 최대 3회 재시도**(LTE 지연으로 맵 미설정 방지) → 포즈 복원.
- 동기화 후 **유휴(도킹) 로봇은 충전소 기준으로 자동 위치재조정**(작업 중 로봇은 저장/복원한 포즈 유지).

## 남은 작업

### VESA 모드 (완료)
- ~~`dispatch_sessions` 모델 + CRUD + 스키마~~
- ~~`dispatch_service` 워커 스레드 + threading.Event 기반 next/end~~
- ~~`/api/dispatch/*` 5개 엔드포인트~~
- ~~`dispatch_tablet.html` 태블릿 UI (POI 그리드, 점유 비활성, 2초 폴링)~~
- ~~서버 재시작 시 미완료 세션 복구 (`recover_on_startup`)~~
- ~~`interactive_relay` work_mode 추가~~
- ~~TabletApp URL 경로 `/api/dispatch/tablet/{id}`로 변경~~
- ~~`rcs_vesa_db` 마이그레이션 (18 테이블 + FK 22개 복원)~~

### VESA 모드 (미완료/테스트 필요)
- 3대 동시 운영 시 zone_guard / poi_lock 실전 충돌 시나리오 검증
- 태블릿 UI 실제 사용 후 UX 다듬기 (현재 폴링 2초 → 필요 시 WebSocket)
- 운영 중 작업자가 잘못된 POI를 누르는 경우의 백엔드 거부 로그/알림

### 프론트엔드 UI (미완료, 레거시 자동 모드용)
- 맵핑 시작 시 로봇 미연결 안내창
- 맵 저장 완료 후 해당 맵 자동 표시
- 영역 드롭다운 최신 선택

### Basic 정리 (선택 — VESA 분기됐으므로 우선순위 낮음)
- 레거시 라우터/페이지 정리 (`task`, `routes`, `schedules`) — 운영 안 쓰면 제거 검토
- 사이드바/타입에서 미사용 메뉴 정리
- ~~모니터링 좌측 하단 단일/배치 수동배차 패널 제거 (VESA에서 시작은 태블릿이 담당)~~ → `ActiveJobsPanel`만 유지
- ~~사이드바에서 "작업관리" 메뉴 제거~~ → 페이지 파일(`/tasks/page.tsx`)은 보존

## 미해결 이슈
- **LTE 유동 IP** — 로봇 라우터 WAN IP 가 재부팅마다 바뀜 → 실운영 전 고정 IP 또는 DDNS 필수. (매핑 등 대용량은 WiFi 권장)
- **뒷다리 검출 편차** — LG2 랙 뒷다리 근처 캐스터 간섭으로 depth 가 ±수 cm 흔들림. rack.specs 중앙값으로 완화하나 근본은 물리(캐스터 정리).
- `align_with_rack`에서 `rack_area_id` 사용 불가 (regionType 미확인 — AutoXing 문의 필요)
- `detectRackSize` REST API 없음 (SDK 전용 — AutoXing 문의 필요)
- 잭 다운 후 로봇 빠져나오기 시간 불확실 (고정 10초 대기 + 400 에러 시 5초 간격 재시도)
- `to_unload_point` J1 이동 미작동 이슈 확인 필요
- 맵 변경 시 경로 웨이포인트 POI ID 자동 매핑 필요 (레거시 자동 모드용 — VESA 영향 없음)

## 업무일지 양식

`작업일지.md`에 최신 날짜를 위쪽에 `## YYYY-MM-DD` 섹션으로 추가.

외부 보고용 양식:
```
Noah 일일 업무 보고 <YYYY.MM.DD>

1.RCS Basic VESA 모델 개발

- 작업 내용 1
- 작업 내용 2
- ...
```
