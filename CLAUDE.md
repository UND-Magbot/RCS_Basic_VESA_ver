# RCS-Basic 프로젝트

## 언어
- 모든 답변은 한국어로 작성

## 프로젝트 개요
- RCS (Robot Control System) 기본 모델 — 기존 RCS에서 불필요 기능을 제거한 경량화 버전
- AutoXing 로봇 제어 시스템 (crawler_s300_op5 모델, 펌웨어 2.12.21-opi64)
- 백엔드: FastAPI (Python 3.10) + MySQL
- 프론트엔드: Next.js 16 + TypeScript + Three.js (3D 모니터링)
- DB: MySQL (192.168.0.21, rcs_basic_db)
- 로컬 REST API만 사용 (CSP/클라우드 미사용)

## 디렉토리 구조
- `BackEnd/` — FastAPI 백엔드
  - `app/routers/` — API 라우터 (robot, map, log, jack_test 등)
  - `app/models/` — SQLAlchemy 모델
  - `app/crud/` — DB CRUD 함수
  - `app/schemas/` — Pydantic 스키마
- `frontend/` — Next.js 프론트엔드
  - `app/monitoring/` — 3D 모니터링 페이지 (Three.js)
  - `app/map/` — 맵 관리 페이지
  - `app/robots/` — 로봇 관리 페이지
  - `app/logs/` — 로그 페이지
  - `app/settings/` — 설정 페이지
- `docker-compose.yml` — 배포용 Docker 설정

## 제거된 기능 (Basic 모델에서 삭제)
- Convoy(대열) 작업
- ACS 인터페이스 (화재경보 수신)
- 화재경보/비상정지/대피 오버레이
- WCS 연동
- 선택적 메뉴 권한 관리
- 시스템 로그 (별도 테이블)
- 엑셀 내보내기
- 작업(Task) 이동 관리

## 유지 기능
- 3D 모니터링 (Three.js)
- 맵 관리 (매핑, POI, 라인, 폴리곤, 동기화)
- 로봇 관리 (IP 입력 → 자동 등록)
- 로그 관리
- 잭 테스트 (align_with_rack → jack_up → to_unload_point → jack_down)
- DB 백업/복원

## AutoXing 로봇 API 핵심 정보

### REST API
- 기본 URL: `http://{robot_ip}:8090/`
- 이동: `POST /chassis/moves` (type: standard, align_with_rack, to_unload_point, charge)
- 잭: `POST /services/jack_up`, `POST /services/jack_down`
- 맵: `GET/POST/PATCH/DELETE /maps/{id}`
- 현재 맵: `GET/POST /chassis/current-map`
- 설정: `GET/PATCH /system/settings/user`
- 서비스 재시작: `POST /services/restart_py_axbot`

### 잭킹 흐름 (테스트 완료)
1. `align_with_rack` — Shelves Point로 이동하며 랙 다리 LiDAR 감지 후 중앙 정렬
2. `jack_up` — 잭 올리기 (10초 대기)
3. `to_unload_point` — 드롭오프 위치로 정밀 이동
4. `jack_down` — 잭 내리기 (10초 대기)
5. `standard` — 대기장소(W1)로 복귀

### Shelves Point (핵심!)
- overlay에서 POI type="34", subtype="rack"으로 등록해야 align_with_rack이 동작
- type이 다르면 로봇이 rack을 인식하지 못함 (failed to find rack 에러)
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

### overlays_version 갱신 문제 (해결됨)
- API PATCH로 overlay를 수정해도 overlays_version이 올라가지 않음
- 해결: PATCH 후 `POST /chassis/current-map`으로 같은 맵을 재선택하면 로봇이 overlay 리로드
- full 동기화 시: 서비스 재시작 → 90초 대기 → Shelves Point PATCH → current-map 재선택

### rack.specs 설정
```json
{
  "rack.specs": [{
    "width": 0.66,
    "depth": 0.70,
    "margin": [0, 0, 0, 0],
    "alignment": "center",
    "alignment_margin_back": 0.02,
    "extra_leg_offset": 0.015,
    "leg_shape": "square",
    "leg_size": 0.03,
    "foot_radius": 0.0,
    "cargo_to_jack_front_edge_min_distance": 0.05
  }]
}
```

### WebSocket 토픽
- `/detected_rack` — 랙 감지 상태
- `/jack_state` — 잭 상태 (jacking_up, jacking_down, hold)
- `/robot_model` — 로봇 풋프린트 (잭 업 시 확장됨)

## DB 구조 (rcs_basic_db)
- `robots` — 로봇 목록 (IP 등록 방식)
- `robot_maps` — 맵 데이터
- `map_pois` — POI (poi_type: general, standby, jack)
- `map_lines` — 라인
- `map_polygons` — 폴리곤
- `businesses` — 사업장
- `areas` — 영역
- `users` — 사용자
- `activity_logs` — 활동 로그

## 개발 규칙
- 백엔드 실행: `cd BackEnd && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- 프론트엔드 실행: `cd frontend && npm run dev`
- Git 브랜치: feature/backend_noah → dev
- CSS @import 사용 금지 (Turbopack 호환 문제, globals.css에 인라인)
- POI world 좌표: DB에 world_x, world_y 저장됨 (pixel 좌표와 별도)
- 맵 동기화 시 overlay에 Shelves Point(type=34) 포함 필요

## 남은 작업

### Basic 모델 정리 (미완료)
- 불필요한 파일 삭제 (tar, sql덤프, 임시파일, mock, static/maps)
- 백엔드 - Convoy 관련 제거 (라우터, 서비스, 모델)
- 백엔드 - Task 관련 제거 (라우터, 서비스, 모델, 스키마)
- 백엔드 - ACS/WCS/시스템로그/권한 제거
- 백엔드 - main.py/database.py 정리
- 프론트엔드 - 제거 대상 컴포넌트/페이지 삭제
- 프론트엔드 - 모니터링/설정 페이지에서 제거된 기능 참조 정리
- 프론트엔드 - 사이드바/타입 등 나머지 참조 정리

### 잭 테스트 코드 반영 (완료)
- ~~jack_test.py에 align_with_rack + to_unload_point 흐름 적용~~
- ~~맵 동기화 시 Shelves Point(type=34) overlay 자동 포함~~
- ~~overlays_version 갱신 자동화 — PATCH 후 current-map 재선택~~

### 프론트엔드 UI (완료)
- ~~로딩중 텍스트 전체 제거~~
- ~~맵 페이지 방화벽/라인 생성 버튼 숨기기~~
- ~~로봇 등록 (IP 입력 → 자동 정보 조회 → DB 저장)~~
- ~~로봇 등록 완료 후 리스트 즉시 업데이트~~
- ~~사업장 생성 기능 맵 드롭다운에 추가~~
- ~~맵 영역 이름 중복 체크 (매핑 시작 시 사전 체크)~~
- ~~맵 페이지 작업 포인트 생성 기능 (POI type=jack, 보라색 마커)~~
- ~~맵/모니터링 작업 포인트 마커: 2D 박스(rack 크기) + V자 쉐브론 방향 (90도 보정)~~
- ~~3D 모니터링 작업 포인트: Three.js 박스 모델 + 쉐브론~~
- ~~모니터링/로봇관리/작업관리 데이터 로딩 스피너 추가~~
- ~~POI 편집 팝업에서 충전소 타입 제거 (상단 버튼으로만 생성)~~

### 작업 관리 시스템 (완료)
- ~~경로(Route) 모델 — 웨이포인트 순서 + 타입(pickup/dropoff/standby/charging) + 대기시간~~
- ~~스케줄 모델 — 날짜/시간/요일 기반 (크론 제거), 종료 시간까지 반복 실행~~
- ~~APScheduler 스케줄러 — once/daily/weekly 트리거, 종료 후 충전소 자동 복귀~~
- ~~경로 관리 페이지 — 맵에서 POI 클릭으로 경로 편집, 카드 UI (순서변경/타입선택/대기시간)~~
- ~~스케줄 페이지 — CLOi 스타일 모달 (날짜/시간/반복요일/종료시간)~~
- ~~수동 배차 — 모니터링 좌측 패널 (로봇+경로 선택 → 즉시 실행, 중지 시 로봇 이동 취소)~~
- ~~수동 실행 전용 API (스케줄 생성 없이 이력만 기록)~~
- ~~경로/스케줄 이름 중복 체크~~
- ~~커스텀 알림/확인 모달 (alert/confirm 대체)~~
- ~~잭킹 서비스 분리 (jack_service.py) — run_jack_job + run_route_job~~
- ~~충전소 도킹 로직 (standard 접근 → charge 도킹, charge_retry_count 필수)~~
- ~~잭 다운 후 고정 대기 + 400 에러 재시도 (5회)~~
- ~~맵 동기화 시 Shelves Point 중복 방지 (기존 type=34 제거 후 추가)~~
- ~~사이드바 작업관리 메뉴 + 아이콘~~

### 모니터링 UI (완료)
- ~~수동 배차 패널 좌측 통합 (로봇 목록 + 수동 배차 상하 분할)~~
- ~~로봇 리스트 페이지네이션 (5대 단위)~~
- ~~로봇 이동 경로 표시 (현재 위치 → 목적지 직선 + 목적지 마커, 로봇별 색상)~~

### 프론트엔드 UI (미완료)
- 맵핑 시작 시 로봇 미연결 안내창
- 맵 저장 완료 후 해당 맵 자동 표시
- 영역 드롭다운 최신 선택

## 업무일지 양식
```
Noah 일일 업무 보고 <YYYY.MM.DD>

1.RCS Basic 모델 개발

- 작업 내용 1
- 작업 내용 2
- ...
```

## 미해결 이슈
- align_with_rack에서 rack_area_id 사용 불가 (regionType 미확인 — AutoXing 문의 필요)
- detectRackSize REST API 없음 (SDK 전용 — AutoXing 문의 필요)
- 잭 다운 후 로봇 빠져나오기 시간 불확실 (고정 10초 대기 + 400 에러 시 5초 간격 재시도)
