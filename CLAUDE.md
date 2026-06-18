# RCS Basic VESA 프로젝트

## 언어
- 모든 답변은 한국어로 작성

## 프로젝트 개요
- RCS (Robot Control System) **VESA 버전** — Basic 모델에서 분기, 인터랙티브 배차 운영 방식으로 전환
- AutoXing 로봇 제어 시스템 (crawler_s300_op5 모델, 펌웨어 2.12.21-opi64)
- 백엔드: FastAPI (Python 3.10) + MariaDB
- 프론트엔드: Next.js 16 + TypeScript + Three.js (3D 모니터링)
- 태블릿: Android WebView 셸 (Kotlin) + 백엔드 서빙 HTML
- DB: MariaDB (192.168.0.21, **rcs_vesa_db**)
- 로컬 REST API만 사용 (CSP/클라우드 미사용)
- GitHub: https://github.com/UND-Magbot/RCS_Basic_VESA_ver (브랜치: `feature/backend_noah`)

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
- `starting` → `picking_up` → `moving` → `awaiting_next` ↔ `moving` → `returning` → `completed`/`failed`
- `returning` 진입 후에는 모든 next/end 명령 **무시**

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
  - `app/services/` — `dispatch_service`(VESA 워커), `jack_service`, `scheduler`, `zone_guard`, `zone_lock`, `poi_lock`, `geometry`, `deadlock_monitor`, `thread_utils`
  - `app/constants/` — `robot_types`(work_mode), `rack_specs`(랙 사이즈별)
  - `app/templates/` — `tablet.html`(레거시), `**dispatch_tablet.html**`(VESA)
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

### 로봇/관제 기준 (모니터링/레거시)
| Method | Endpoint | 동작 |
|---|---|---|
| POST | `/start` | (레거시) body `[{robot_id, first_poi_id}]` — 직접 시작 |
| POST | `/{robot_id}/next`, `/{robot_id}/end` | 로봇 기준 명령 |
| GET | `/status` | 모든 활성 세션 + 점유 POI 목록 (관제용) |
| GET | `/{robot_id}/status`, `/{robot_id}/pois` | 로봇 단위 조회 |
| GET | `/tablet/{robot_id}` | (레거시) — 안내 페이지로 전환됨 |

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

### rack.specs (S600)
```json
{
  "rack.specs": [{
    "width": 0.83, "depth": 0.87,
    "margin": [0.05, 0.05, 0.05, 0.05],
    "alignment": "center", "alignment_margin_back": 0.02,
    "leg_shape": "other", "leg_size": 0.05,
    "foot_radius": 0.025,
    "cargo_to_jack_front_edge_min_distance": 0.05
  }]
}
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
