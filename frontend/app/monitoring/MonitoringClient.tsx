"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { OverlayCard } from "../components/ui/monitoring/OverlayCard";
import { LayerButton } from "../components/ui/monitoring/LayerButton";
import { MapModeButton } from "../components/ui/monitoring/MapModeButton";
import { DeviceRow } from "../components/ui/monitoring/DeviceRow";
import { RemoteControlModal } from "../components/ui/monitoring/RemoteControlModal";
import { JobStatusPanel } from "../components/ui/monitoring/JobStatusPanel";
import { RobotDeviceInfo } from "../components/ui/RobotDeviceInfo";
import type { RobotDevice, RunState } from "@/lib/types/robots";
import {
  mapBackendStatusToDeviceStatus,
  mapBackendStatusToPower,
  mapLiveRunStateToDeviceStatus,
  mapLiveOnlineToPower,
  formatBattery,
  formatLiveBattery,
} from "@/lib/utils/robotStatus";
import { useAlert } from "@/lib/context/AlertContext";
import { SearchInput } from "../components/ui/SearchInput";
import { Panel } from "../components/ui/Panel";
import { SideNav, defaultNavItems } from "../components/shell/SideNav";
import { TopBar } from "../components/shell/TopBar";
import { MonitoringMapCanvas } from "../components/ui/monitoring/MonitoringMapCanvas";
import dynamic from "next/dynamic";

const MonitoringMap3D = dynamic(
  () =>
    import("../components/ui/monitoring/MonitoringMap3D").then((mod) => ({
      default: mod.MonitoringMap3D,
    })),
  { ssr: false, loading: () => <div className="monitoring-map3d-loading">Loading 3D...</div> }
);
import type { PoiMarkerData, RobotMarkerData, RouteSegment, WaypointMarkerData } from "@/lib/types/map-markers";
import { BusinessSelectBox } from "../components/ui/monitoring/BusinessSelectBox";
import { JackTestPanel } from "../components/ui/monitoring/JackTestPanel";
import { BatchDispatchPanel } from "../components/ui/monitoring/BatchDispatchPanel";
import { ActiveJobsPanel } from "../components/ui/monitoring/ActiveJobsPanel";
import "../components/ui/monitoring/JackTestPanel.css";
import { apiFetch } from "@/lib/api";
import { getPoiLocks, getZoneLocks } from "@/lib/api/tasks";
import type { ZoneLockEntry } from "@/lib/api/tasks";
import {
  getStoredBusiness, getStoredArea, setStoredBusiness, setStoredArea,
} from "@/lib/util/selectedScope";
import type { Business } from "@/lib/types/robots";
import type { MapMeta } from "@/lib/types/map";

function DispatchTabs({ liveRobots, areaId }: { liveRobots: any[]; areaId?: number }) {
  const [mode, setMode] = useState<"single" | "batch">("single");
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 작업 중 로봇 카드는 단일/배치 모드 무관 공통 표시 */}
      <ActiveJobsPanel liveRobots={liveRobots} areaId={areaId} />
      <div style={{ display: "flex", gap: 4, padding: "8px 12px 0", borderBottom: "1px solid var(--border-color)" }}>
        <button
          onClick={() => setMode("single")}
          style={{
            background: "transparent",
            border: "none",
            padding: "6px 12px",
            cursor: "pointer",
            fontSize: 12,
            borderBottom: mode === "single" ? "2px solid var(--color-primary, #36dfc8)" : "2px solid transparent",
            color: mode === "single" ? "var(--color-primary, #36dfc8)" : "var(--text-muted)",
            fontWeight: mode === "single" ? 600 : 400,
          }}
        >단일</button>
        <button
          onClick={() => setMode("batch")}
          style={{
            background: "transparent",
            border: "none",
            padding: "6px 12px",
            cursor: "pointer",
            fontSize: 12,
            borderBottom: mode === "batch" ? "2px solid var(--color-primary, #36dfc8)" : "2px solid transparent",
            color: mode === "batch" ? "var(--color-primary, #36dfc8)" : "var(--text-muted)",
            fontWeight: mode === "batch" ? 600 : 400,
          }}
        >배치 (여러 대)</button>
      </div>
      <div style={{ flex: 1, overflow: "auto" }}>
        {mode === "single"
          ? <JackTestPanel liveRobots={liveRobots} areaId={areaId} />
          : <BatchDispatchPanel liveRobots={liveRobots} areaId={areaId} />}
      </div>
    </div>
  );
}

type BusinessItem = {
  business_id: number;
  name: string;
  areas: { area_id: number; name: string }[];
};

type AreaItem = {
  area_id: number;
  name: string;
};

type MapItem = {
  id: number;
  name: string | null;
  image_url: string | null;
  mapping_id: number | null;
  state: string | null;
  grid_origin_x: number;
  grid_origin_y: number;
  grid_resolution: number;
};

function mapPoiTypeToMonitorType(
  apiType: string
): "workstation" | "charging" | "pickup" | "dropoff" | "jack" | "standby" {
  switch (apiType) {
    case "charging":
      return "charging";
    case "jack":
      return "jack";
    case "standby":
      return "standby";
    default:
      return "workstation";
  }
}

type ApiRobot = {
  serial_number: string;
  name: string;
  ip_address: string;
};

type ApiRobotStatus = {
  battery_level: number;
  charging_status: number;
  charging_status_name: string;
  position_x: number;
  position_y: number;
  position_yaw: number;
  status: number;
  status_name: string;
  updated_at: string;
};

type ApiRobotFull = {
  id: number;
  name: string;
  serial_number: string;
  site: string | null;
  model: string | null;
  ip_address: string | null;
  max_battery: number;
  min_battery: number;
  is_active: boolean;
  business_id: string | null;
  area_id: string | null;
  status: ApiRobotStatus | null;
  created_at: string;
  updated_at: string;
  robot_type?: string | null;
};

type LiveRobot = {
  ID: number;
  IP: string;
  SN: string;
  ROBOTNAME: string;
  MODEL: string;
  NICKNAME: string | null;
  AXBOT_VERSION: string | null;
  PLATFORM: string | null;
  RUNSTATE: string;
  ONLINE: string;
  SIGNAL: string;
  "POWER(%)": string;
};

const defaultOverlayItems = [
  { label: "맵 배경", checked: true },
  { label: "내비게이션 경로", checked: true },
  { label: "이동 방향", checked: true },
  { label: "가상 벽", checked: false },
  { label: "내비게이션 노드", checked: true },
  { label: "작업 지점", checked: true },
];

function formatDateTime(): string {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const min = String(now.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
}

type Props = {
  initialDateTime: string;
};

export function MonitoringClient({ initialDateTime }: Props) {
  const [navCollapsed, setNavCollapsed] = useState(true);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [mapMode, setMapMode] = useState<"2d" | "3d">("2d");
  const [overlayItems, setOverlayItems] = useState(defaultOverlayItems);
  const [isLayerOpen, setIsLayerOpen] = useState(false);
  const [expandedDeviceId, setExpandedDeviceId] = useState<string | null>(null);
  const [remoteTarget, setRemoteTarget] = useState<{ id: string; name: string; ip: string } | null>(null);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [devicePage, setDevicePage] = useState(1);
  const DEVICE_PAGE_SIZE = 5;
  const [openDeviceId, setOpenDeviceId] = useState<string | null>(null);
  const [togglingDeviceId, setTogglingDeviceId] = useState<string | null>(null);
  const [mapSrc, setMapSrc] = useState("");
  const { showAlert } = useAlert();
  const lastBatteryAlertRef = useRef<string | null>(null);
  const [deviceSearch, setDeviceSearch] = useState("");
  // simulatedRobots는 아래 useMemo로 계산 (useEffect+setState 연쇄 리렌더 방지)

  // ── 실제 로봇 데이터 (mock 대체) ──
  const [apiRobotsFull, setApiRobotsFull] = useState<ApiRobotFull[]>([]);
  const [liveRobots, setLiveRobots] = useState<LiveRobot[]>([]);
  const livePollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleDeviceEnableToggle = useCallback((deviceId: string) => {
    if (togglingDeviceId) return;
    setTogglingDeviceId(deviceId);
    // TODO: Replace with real API call
    setTimeout(() => setTogglingDeviceId(null), 300);
  }, [togglingDeviceId]);

  const [isLoading, setIsLoading] = useState(true);
  const [robotsLoaded, setRobotsLoaded] = useState(false);
  const [liveLoaded, setLiveLoaded] = useState(false);
  const [currentDateTime, setCurrentDateTime] = useState(initialDateTime);
  const [selectedBusiness, setSelectedBusiness] = useState("");

  // API data state
  const [apiBusinesses, setApiBusinesses] = useState<BusinessItem[]>([]);
  const [areas, setAreas] = useState<AreaItem[]>([]);
  const [selectedArea, setSelectedArea] = useState("");
  const [areaMaps, setAreaMaps] = useState<MapItem[]>([]);
  const [selectedMapId, setSelectedMapId] = useState<number | null>(null);
  const [mapImageSize, setMapImageSize] = useState<{ w: number; h: number } | null>(null);
  const [rawApiElements, setRawApiElements] = useState<{ pois: any[]; lines: any[] } | null>(null);
  const [apiPois, setApiPois] = useState<PoiMarkerData[]>([]);
  const [apiWaypoints, setApiWaypoints] = useState<WaypointMarkerData[]>([]);
  const [apiRouteWaypoints, setApiRouteWaypoints] = useState<WaypointMarkerData[]>([]);
  const [apiRouteSegments, setApiRouteSegments] = useState<RouteSegment[]>([]);
  // POI 락 (poi_id → robot_name). 5초 폴링.
  const [poiLockMap, setPoiLockMap] = useState<Map<string, string>>(new Map());
  // Zone(통로) 락 (다중 로봇 통로 진입 직렬화)
  const [zoneLocks, setZoneLocks] = useState<ZoneLockEntry[]>([]);


  // 로봇 실시간 위치 (다중 로봇)
  const [mapMeta, setMapMeta] = useState<MapMeta | null>(null);
  const defaultMapRef = useRef<{ map_id: number | null; image_url: string | null; grid_origin_x: number; grid_origin_y: number; grid_resolution: number; area_id: number | null } | null>(null);
  const [apiRobots, setApiRobots] = useState<ApiRobot[]>([]);
  const [robotPoses, setRobotPoses] = useState<Map<string, { pos: [number, number]; ori: number }>>(new Map());
  const poseWsRefs = useRef<Map<string, WebSocket>>(new Map());
  const [robotTargets, setRobotTargets] = useState<Map<string, { x: number; y: number } | null>>(new Map());
  const poseBufferRef = useRef<Map<string, { pos: [number, number]; ori: number }>>(new Map());
  const poseFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 로봇 목록 + 실시간 데이터 API 응답 완료 시 로딩 종료 (데이터 없어도 종료)
  useEffect(() => {
    if (robotsLoaded && liveLoaded) setIsLoading(false);
  }, [robotsLoaded, liveLoaded]);

  // 마지막 선택 사업장·영역을 다른 페이지와 공유 (localStorage)
  useEffect(() => { setStoredBusiness(selectedBusiness); }, [selectedBusiness]);
  useEffect(() => { setStoredArea(selectedArea); }, [selectedArea]);

  // 10초 타임아웃 — API 응답 없어도 강제 로딩 종료
  useEffect(() => {
    const t = setTimeout(() => setIsLoading(false), 10000);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setCurrentDateTime(formatDateTime()), 1000);
    return () => clearInterval(timer);
  }, []);

  // ── 기본 맵 + 사업장 목록을 동시에 로드 (race condition 방지) ──
  useEffect(() => {
    Promise.all([
      apiFetch<{ map_id: number | null; image_url: string | null; grid_origin_x: number; grid_origin_y: number; grid_resolution: number; area_id: number | null }>("/api/map/default-map").catch(() => null),
      apiFetch<{ total: number; items: BusinessItem[] }>("/api/map/businesses").catch((err) => {
        console.error("[모니터링] 사업장 목록 로드 실패:", err);
        showAlert({ title: "알림", message: "사업장 목록을 불러오는 데 실패했습니다.", errorCode: "MAP-001", errorType: "map", source: "모니터링 > 초기 로드", description: "MonitoringClient — 사업장 목록 로드", silent: false });
        return { total: 0, items: [] as BusinessItem[] };
      }),
    ]).then(([defaultMap, bizData]) => {
      // 기본 맵 정보 설정
      if (defaultMap) {
        defaultMapRef.current = defaultMap;
        if (defaultMap.image_url && !mapSrc) {
          if (defaultMap.image_url.startsWith("/static/")) {
            setMapSrc(`${process.env.NEXT_PUBLIC_API_URL}${defaultMap.image_url}`);
          } else {
            setMapSrc(`${process.env.NEXT_PUBLIC_API_URL}/api/map/proxy-image?url=${encodeURIComponent(defaultMap.image_url)}`);
          }
        }
        if (!mapMeta) {
          setMapMeta({ grid_origin_x: defaultMap.grid_origin_x, grid_origin_y: defaultMap.grid_origin_y, grid_resolution: defaultMap.grid_resolution });
        }
      }
      // 사업장 목록 설정
      setApiBusinesses(bizData.items);
      if (!selectedBusiness) {
        // 1) 마지막 사용 사업장 (다른 페이지와 공유) 우선
        const stored = getStoredBusiness();
        const storedBiz = stored
          ? bizData.items.find((b) => String(b.business_id) === stored)
          : null;
        // 2) default-map 의 area_id 로 자동 매칭
        const defAreaId = defaultMap?.area_id;
        const defaultBizByArea = defAreaId
          ? bizData.items.find((b) => b.areas?.some((a) => a.area_id === defAreaId))
          : null;
        const chosen = storedBiz ?? defaultBizByArea ?? bizData.items[0] ?? null;
        if (chosen) setSelectedBusiness(String(chosen.business_id));
      }
    });
  }, []);

  // ── Business 변경 시 영역 목록 (apiBusinesses에서 직접 추출) ──
  // selectedArea는 BusinessSelectBox onChange(handleBusinessAreaChange)에서 직접 설정됨
  useEffect(() => {
    if (!selectedBusiness) {
      setAreas((p) => (p.length === 0 ? p : []));
      setAreaMaps((p) => (p.length === 0 ? p : []));
      setMapSrc((p) => (p === "" ? p : ""));
      return;
    }
    setAreaMaps((p) => (p.length === 0 ? p : []));
    setMapSrc((p) => (p === "" ? p : ""));
    apiFetch<{ total: number; items: AreaItem[] }>(
      `/api/map/businesses/${selectedBusiness}/areas`
    )
      .then((data) => {
        setAreas(data.items);
        if (data.items.length > 0) {
          // 1) 마지막 사용 영역 (다른 페이지와 공유) 우선
          const stored = getStoredArea();
          const storedArea = stored
            ? data.items.find((a) => String(a.area_id) === stored)
            : null;
          // 2) default-map 의 area_id 폴백
          const defaultAreaId = defaultMapRef.current?.area_id;
          const matchDefault = defaultAreaId
            ? data.items.find((a) => a.area_id === defaultAreaId)
            : null;
          const chosen = storedArea ?? matchDefault ?? data.items[0];
          setSelectedArea(String(chosen.area_id));
        } else {
          setSelectedArea("");
        }
      })
      .catch((err) => {
        console.error("[모니터링] 영역 목록 로드 실패:", err);
        showAlert({ title: "알림", message: "영역 목록을 불러오는 데 실패했습니다.", errorCode: "MAP-002", errorType: "map", source: "모니터링 > 영역 로드", description: "MonitoringClient — 영역 목록 로드", silent: false });
        setAreas((p) => (p.length === 0 ? p : []));
      });
  }, [selectedBusiness]);

  // ── Area 변경 시 맵 목록 로드 및 첫 번째 맵 이미지 + 요소 로드 ──
  const applyDefaultMap = () => {
    const def = defaultMapRef.current;
    if (def?.image_url) {
      const url = def.image_url.startsWith("/static/")
        ? `${process.env.NEXT_PUBLIC_API_URL}${def.image_url}`
        : `${process.env.NEXT_PUBLIC_API_URL}/api/map/proxy-image?url=${encodeURIComponent(def.image_url)}`;
      setMapSrc((p) => (p === url ? p : url));
      setMapMeta((prev) => {
        if (prev && prev.grid_origin_x === def.grid_origin_x && prev.grid_origin_y === def.grid_origin_y && prev.grid_resolution === def.grid_resolution) return prev;
        return { grid_origin_x: def.grid_origin_x, grid_origin_y: def.grid_origin_y, grid_resolution: def.grid_resolution };
      });
    } else {
      setMapSrc((p) => (p === "" ? p : ""));
      setMapMeta((p) => (p === null ? p : null));
    }
  };

  useEffect(() => {
    if (!selectedArea) {
      setAreaMaps((p) => (p.length === 0 ? p : []));
      setSelectedMapId((p) => (p === null ? p : null));
      applyDefaultMap();
      setRawApiElements((p) => (p === null ? p : null));
      setApiPois((p) => (p.length === 0 ? p : []));
      setApiWaypoints((p) => (p.length === 0 ? p : []));
      setApiRouteWaypoints((p) => (p.length === 0 ? p : []));
      setApiRouteSegments((p) => (p.length === 0 ? p : []));
      setMapImageSize((p) => (p === null ? p : null));
      return;
    }
    apiFetch<{ total: number; items: MapItem[] }>(
      `/api/map/areas/${selectedArea}/maps`
    )
      .then((data) => {
        setAreaMaps(data.items);
        if (data.items.length > 0 && data.items[0].image_url) {
          const defaultMapId = defaultMapRef.current?.map_id;
          const map = (defaultMapId ? data.items.find((m) => m.id === defaultMapId) : null) ?? data.items[0];
          setSelectedMapId(map.id);
          setMapMeta({
            grid_origin_x: map.grid_origin_x,
            grid_origin_y: map.grid_origin_y,
            grid_resolution: map.grid_resolution,
          });
          const imgUrl = map.image_url!;
          if (imgUrl.startsWith("/static/")) {
            setMapSrc(`${process.env.NEXT_PUBLIC_API_URL}${imgUrl}`);
          } else {
            setMapSrc(
              `${process.env.NEXT_PUBLIC_API_URL}/api/map/proxy-image?url=${encodeURIComponent(imgUrl)}`
            );
          }
          // 저장된 POI·라인 로드
          apiFetch<{ pois: any[]; lines: any[] }>(
            `/api/map/maps/${map.id}/elements`
          )
            .then((elems) => setRawApiElements(elems))
            .catch((err) => {
              console.error("[모니터링] 맵 요소 로드 실패:", err);
              showAlert({ title: "알림", message: "맵 요소(POI·라인)를 불러오는 데 실패했습니다.", errorCode: "MAP-004", errorType: "map", source: "모니터링 > 맵 요소 로드", description: "MonitoringClient — 맵 요소 로드", silent: false });
              setRawApiElements(null);
            });
        } else {
          setSelectedMapId((p) => (p === null ? p : null));
          applyDefaultMap();
          setRawApiElements((p) => (p === null ? p : null));
          setApiPois((p) => (p.length === 0 ? p : []));
          setApiWaypoints((p) => (p.length === 0 ? p : []));
          setApiRouteWaypoints((p) => (p.length === 0 ? p : []));
          setApiRouteSegments((p) => (p.length === 0 ? p : []));
        }
      })
      .catch((err) => {
        console.error("[모니터링] 맵 목록 로드 실패:", err);
        showAlert({ title: "알림", message: "맵 목록을 불러오는 데 실패했습니다.", errorCode: "MAP-003", errorType: "map", source: "모니터링 > 맵 목록 로드", description: "MonitoringClient — 맵 목록 로드", silent: false });
        setAreaMaps((p) => (p.length === 0 ? p : []));
        setSelectedMapId((p) => (p === null ? p : null));
        setMapSrc((p) => (p === "" ? p : ""));
        setMapMeta((p) => (p === null ? p : null));
        setRawApiElements((p) => (p === null ? p : null));
      });
  }, [selectedArea]);

  // ── 맵 이미지 크기 로드 (좌표 변환용) ──
  useEffect(() => {
    if (!mapSrc) {
      setMapImageSize((p) => (p === null ? p : null));
      return;
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      setMapImageSize((prev) => {
        if (prev && prev.w === img.naturalWidth && prev.h === img.naturalHeight) return prev;
        return { w: img.naturalWidth, h: img.naturalHeight };
      });
    };
    img.onerror = () => setMapImageSize((p) => (p === null ? p : null));
    img.src = mapSrc;
  }, [mapSrc]);

  // ── SVG 좌표 → 이미지 픽셀 좌표 변환 (rawApiElements + mapImageSize) ──
  useEffect(() => {
    if (!rawApiElements || !mapImageSize) {
      setApiPois((prev) => (prev.length === 0 ? prev : []));
      setApiWaypoints((prev) => (prev.length === 0 ? prev : []));
      setApiRouteWaypoints((prev) => (prev.length === 0 ? prev : []));
      setApiRouteSegments((prev) => (prev.length === 0 ? prev : []));
      return;
    }

    const halfW = mapImageSize.w / 2;
    const halfH = mapImageSize.h / 2;

    const convertedPois: PoiMarkerData[] = [];
    const convertedWaypoints: WaypointMarkerData[] = [];

    const hiddenPoiNames = new Set(["CHARGING1", "ENTERPOS1", "ENTERPOS2", "ENTERPOS3"]);

    for (const p of rawApiElements.pois) {
      if (hiddenPoiNames.has(p.name)) continue;
      // SAFE-* 대피 POI 및 방화벽(FW*) 노드는 모니터링 화면에 미표시
      if (p.name?.startsWith("SAFE-") || p.type === "firewall") continue;

      let px = p.x + halfW;
      let py = p.y + halfH;

      // 충전소: DB 좌표(도킹 지점)에서 로봇 기준 뒤(angle 반대) 로 이동시켜 표시.
      // 도킹 후 로봇 본체가 마커를 가리지 않게, 그리고 실제 충전기 본체 자리에 가깝게.
      if (
        p.type === "charging" &&
        p.angle != null &&
        mapMeta &&
        mapMeta.grid_resolution > 0
      ) {
        const DOCKING_OFFSET_M = 0.5;
        const offsetPx = DOCKING_OFFSET_M / mapMeta.grid_resolution;
        px -= offsetPx * Math.cos(p.angle);
        py += offsetPx * Math.sin(p.angle);
      }

      if (p.type === "waypoint") {
        convertedWaypoints.push({
          id: p.id,
          label: p.name,
          position: { x: px, y: py },
        });
      } else {
        const poiData: import("@/lib/types/map-markers").PoiMarkerData = {
          id: p.id,
          label: p.name,
          position: { x: px, y: py },
          type: mapPoiTypeToMonitorType(p.type),
          angle: p.angle ?? undefined,
          dockingRadius: p.dockingRadius ?? undefined,
          lockedByRobot: poiLockMap.get(String(p.id)),
        };
        // 랙 위치(standby) 만 보관된 랙의 사이즈로 마커 크기 분기.
        // 작업 위치(jack) 는 어떤 사이즈의 랙이든 들어올 수 있으므로 기본 크기(S600) 로 표시.
        if (p.type === "standby" && mapMeta && mapMeta.grid_resolution > 0) {
          const rackSize = (p.rackSize ?? p.rack_size) as ("S600" | "S300" | undefined);
          const isSmall = rackSize === "S300";
          const RACK_W = isSmall ? 0.73 : 0.83;
          const RACK_D = isSmall ? 0.74 : 0.87;
          poiData.rackWidthPx = RACK_W / mapMeta.grid_resolution;
          poiData.rackDepthPx = RACK_D / mapMeta.grid_resolution;
        } else if (p.type === "jack" && mapMeta && mapMeta.grid_resolution > 0) {
          // 작업 위치: 기본 마커 사이즈 (S600 기준)
          poiData.rackWidthPx = 0.83 / mapMeta.grid_resolution;
          poiData.rackDepthPx = 0.87 / mapMeta.grid_resolution;
        }
        convertedPois.push(poiData);
      }
    }

    // 라인 → 경로 웨이포인트 + RouteSegment 변환
    const posMap = new Map<string, { x: number; y: number; name: string }>();
    for (const p of rawApiElements.pois) {
      posMap.set(p.id, { x: p.x + halfW, y: p.y + halfH, name: p.name });
    }

    const routePoints: WaypointMarkerData[] = [];
    const segments: RouteSegment[] = [];

    for (const line of rawApiElements.lines) {
      const fromPos = posMap.get(line.fromId);
      const toPos = posMap.get(line.toId);
      if (fromPos && toPos) {
        // routeWaypoints (기존 호환)
        if (
          routePoints.length === 0 ||
          routePoints[routePoints.length - 1].id !== line.fromId
        ) {
          routePoints.push({
            id: line.fromId,
            label: fromPos.name,
            position: { x: fromPos.x, y: fromPos.y },
          });
        }
        routePoints.push({
          id: line.toId,
          label: toPos.name,
          position: { x: toPos.x, y: toPos.y },
        });

        // RouteSegment (direction, curve 정보 포함)
        const controlPts = line.controlPoints
          ? line.controlPoints.map((cp: { x: number; y: number }) => ({
              x: cp.x + halfW,
              y: cp.y + halfH,
            }))
          : undefined;

        segments.push({
          id: line.id,
          from: { x: fromPos.x, y: fromPos.y },
          to: { x: toPos.x, y: toPos.y },
          direction: line.direction ?? "forward",
          lineType: line.lineType ?? "straight",
          controlPoints: controlPts,
        });
      }
    }

    setApiPois(convertedPois);
    setApiWaypoints(convertedWaypoints);
    setApiRouteWaypoints(routePoints);
    setApiRouteSegments(segments);
  }, [rawApiElements, mapImageSize, mapMeta, poiLockMap]);

  // API 사업장 → BusinessSelectBox 형식 변환 (사업장×영역 쌍으로 펼침)
  const businessesForSelectBox: Business[] = useMemo(
    () => apiBusinesses.flatMap((b) =>
      (b.areas ?? []).map((a) => ({
        id: `${b.business_id}:${a.area_id}`,
        name: (b.areas ?? []).length > 1 ? `${b.name} - ${a.name}` : b.name,
        value: String(a.area_id),
      }))
    ),
    [apiBusinesses]
  );

  // 사업장×영역 셀렉트박스 onChange → business + area 동시 설정
  const handleBusinessAreaChange = useCallback((combinedId: string) => {
    const [bizId, areaId] = combinedId.split(":");
    setSelectedBusiness(bizId);
    setSelectedArea(areaId);
  }, []);

  // ── 로봇 목록 API 로드 (selectedArea 기반) ──
  useEffect(() => {
    if (!selectedArea) {
      setApiRobotsFull((p) => (p.length === 0 ? p : []));
      setApiRobots((p) => (p.length === 0 ? p : []));
      return;
    }
    // TODO: 층별 로봇 필터 — 현재는 1대 로봇이 층 전환하며 사용하므로 전체 조회
    // apiFetch<{ total: number; items: ApiRobotFull[] }>(`/api/robots?area_id=${selectedArea}`)
    apiFetch<{ total: number; items: ApiRobotFull[] }>(
      `/api/robots`
    )
      .then((data) => {
        setApiRobotsFull(data.items);
        setApiRobots(
          data.items
            .filter((r) => r.ip_address)
            .map((r) => ({
              serial_number: r.serial_number,
              name: r.name,
              ip_address: r.ip_address!,
            }))
        );
        setRobotsLoaded(true);
      })
      .catch((err) => {
        console.error("[모니터링] 로봇 목록 로드 실패:", err);
        showAlert({ title: "알림", message: "로봇 목록을 불러오는 데 실패했습니다.", errorCode: "ROBOT-010", errorType: "robot", source: "모니터링 > 로봇 목록 로드", description: "MonitoringClient — 로봇 목록 로드 실패", silent: false });
        setApiRobotsFull((p) => (p.length === 0 ? p : []));
        setApiRobots((p) => (p.length === 0 ? p : []));
        setRobotsLoaded(true);
      });
  }, [selectedArea]);

  // ── 다중 로봇 실시간 위치 WS 연결 ──
  useEffect(() => {
    // 기존 연결 정리
    poseWsRefs.current.forEach((ws) => ws.close());
    poseWsRefs.current.clear();
    setRobotPoses((prev) => (prev.size === 0 ? prev : new Map()));

    if (!apiRobots.length) return;

    const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
    const wsBase = API_URL.replace(/^http/, "ws");

    apiRobots.forEach((robot) => {
      const wsUrl = `${wsBase}/api/map/ws/${robot.ip_address}?topics=/tracked_pose`;
      const ws = new WebSocket(wsUrl);
      poseWsRefs.current.set(robot.serial_number, ws);

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.topic === "/tracked_pose" && msg.pos) {
            poseBufferRef.current.set(robot.serial_number, {
              pos: msg.pos,
              ori: msg.ori ?? 0,
            });
            if (!poseFlushTimerRef.current) {
              poseFlushTimerRef.current = setTimeout(() => {
                poseFlushTimerRef.current = null;
                const buf = poseBufferRef.current;
                if (buf.size === 0) return;
                setRobotPoses((prev) => {
                  const next = new Map(prev);
                  buf.forEach((v, sn) => next.set(sn, v));
                  buf.clear();
                  return next;
                });
              }, 100);
            }
          }
        } catch {
          // ignore
        }
      };
      ws.onerror = () => {
        console.warn(`[MonitoringPoseWS] ${robot.name} (${robot.ip_address}) error — NET-002`);
      };
      ws.onclose = () =>
        console.log(`[MonitoringPoseWS] ${robot.name} closed`);
    });

    return () => {
      poseWsRefs.current.forEach((ws) => ws.close());
      poseWsRefs.current.clear();
    };
  }, [apiRobots]);

  // ── 로봇 실시간 상태 폴링 (5초 간격) ──
  useEffect(() => {
    if (livePollingRef.current) {
      clearInterval(livePollingRef.current);
      livePollingRef.current = null;
    }

    if (apiRobotsFull.length === 0) {
      setLiveRobots((p) => (p.length === 0 ? p : []));
      return;
    }

    const fetchLive = () => {
      apiFetch<{ total: number; items: LiveRobot[] }>("/api/robots/live")
        .then((data) => setLiveRobots(data.items))
        .catch((err) => {
          console.error("[모니터링] 로봇 실시간 상태 조회 실패:", err);
        });
    };

    const fetchLiveOnce = () => {
      apiFetch<{ total: number; items: LiveRobot[] }>("/api/robots/live")
        .then((data) => { setLiveRobots(data.items); setLiveLoaded(true); })
        .catch(() => { setLiveLoaded(true); });
    };
    fetchLiveOnce();
    livePollingRef.current = setInterval(fetchLive, 5000);

    // 로봇 이동 목표 폴링
    const fetchTargets = () => {
      apiRobots.forEach((robot) => {
        fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}/api/robots/target/${robot.ip_address}`, { cache: "no-store" })
          .then((r) => r.json())
          .then((data) => {
            setRobotTargets((prev) => {
              const next = new Map(prev);
              if (data.target_x != null && data.state === "moving") {
                next.set(robot.serial_number, { x: data.target_x, y: data.target_y });
              } else {
                next.delete(robot.serial_number);
              }
              return next;
            });
          })
          .catch(() => {
            setRobotTargets((prev) => {
              const next = new Map(prev);
              next.delete(robot.serial_number);
              return next;
            });
          });
      });
    };
    const targetInterval = setInterval(fetchTargets, 2000);
    fetchTargets();

    // POI 락 폴링 (다중 로봇 조율 표시)
    const fetchPoiLocks = () => {
      getPoiLocks()
        .then(({ locks }) => {
          setPoiLockMap((prev) => {
            const next = new Map<string, string>();
            for (const lk of locks) {
              next.set(String(lk.poi_id), lk.robot_name ?? `robot#${lk.robot_id}`);
            }
            // 변화 없으면 같은 참조 유지 (불필요 리렌더 방지)
            if (prev.size === next.size) {
              let same = true;
              for (const [k, v] of next) {
                if (prev.get(k) !== v) { same = false; break; }
              }
              if (same) return prev;
            }
            return next;
          });
        })
        .catch(() => { /* 조용히 무시 */ });
    };
    const poiLocksInterval = setInterval(fetchPoiLocks, 5000);
    fetchPoiLocks();

    // Zone 락 폴링
    const fetchZoneLocks = () => {
      getZoneLocks()
        .then(({ locks }) => {
          setZoneLocks((prev) => {
            // 비교 후 동일하면 같은 참조 유지
            if (prev.length === locks.length) {
              let same = true;
              for (let i = 0; i < locks.length; i++) {
                if (prev[i]?.zone_id !== locks[i].zone_id || prev[i]?.robot_id !== locks[i].robot_id) {
                  same = false; break;
                }
              }
              if (same) return prev;
            }
            return locks;
          });
        })
        .catch(() => {});
    };
    const zoneLocksInterval = setInterval(fetchZoneLocks, 5000);
    fetchZoneLocks();

    return () => {
      if (livePollingRef.current) {
        clearInterval(livePollingRef.current);
        livePollingRef.current = null;
      }
      clearInterval(targetInterval);
      clearInterval(poiLocksInterval);
      clearInterval(zoneLocksInterval);
    };
  }, [apiRobotsFull]);

  // ── IP 기준 live 데이터 lookup ──
  const liveByIp = useMemo(() => {
    const map = new Map<string, LiveRobot>();
    for (const lr of liveRobots) {
      map.set(lr.IP, lr);
    }
    return map;
  }, [liveRobots]);

  // ── 다중 로봇 월드 좌표 → 이미지 픽셀 좌표 변환 (live 상태 반영) ──
  // useMemo: useEffect+setState 연쇄 리렌더 방지 (WS 메시지 빈도 높을 때 Maximum update depth 해결)
  const simulatedRobots = useMemo(() => {
    if (!mapMeta || !mapImageSize || mapMeta.grid_resolution <= 0) {
      return [];
    }

    const markers: RobotMarkerData[] = [];
    const posedSNs = new Set<string>();

    // 1) 실시간 WS 데이터가 있는 로봇 (pose.ori 사용)
    robotPoses.forEach((pose, sn) => {
      posedSNs.add(sn);

      const fullRobot = apiRobotsFull.find((r) => r.serial_number === sn);
      const live = fullRobot?.ip_address ? liveByIp.get(fullRobot.ip_address) : null;
      const power = live ? mapLiveOnlineToPower(live.ONLINE) : "online";

      // 오프라인 로봇은 맵에 표시하지 않음
      if (power === "offline") return;

      const ipx =
        (pose.pos[0] - mapMeta.grid_origin_x) / mapMeta.grid_resolution;
      const ipy =
        mapImageSize.h -
        (pose.pos[1] - mapMeta.grid_origin_y) / mapMeta.grid_resolution;

      const robot = apiRobots.find((r) => r.serial_number === sn);

      console.log(`[robot-marker] ${sn} WS pose=(${pose.pos[0].toFixed(3)},${pose.pos[1].toFixed(3)}) → px=(${ipx.toFixed(1)},${ipy.toFixed(1)})`);

      markers.push({
        robotId: sn,
        robotName: robot?.name ?? sn,
        position: { x: ipx, y: ipy },
        yaw: pose.ori,
        status: live ? mapLiveRunStateToDeviceStatus(live.RUNSTATE) : "running",
        power,
      });
    });

    // 2) WS 데이터가 아직 없는 로봇 → 백엔드 status 초기값 사용
    for (const fullRobot of apiRobotsFull) {
      if (posedSNs.has(fullRobot.serial_number)) continue;
      if (!fullRobot.status) continue;

      const live = fullRobot.ip_address ? liveByIp.get(fullRobot.ip_address) : null;
      const power = live ? mapLiveOnlineToPower(live.ONLINE) : "online";

      // 오프라인 로봇은 맵에 표시하지 않음
      if (power === "offline") continue;

      const { position_x, position_y, position_yaw } = fullRobot.status;
      const ipx =
        (position_x - mapMeta.grid_origin_x) / mapMeta.grid_resolution;
      const ipy =
        mapImageSize.h -
        (position_y - mapMeta.grid_origin_y) / mapMeta.grid_resolution;

      markers.push({
        robotId: fullRobot.serial_number,
        robotName: fullRobot.name,
        position: { x: ipx, y: ipy },
        yaw: position_yaw,
        status: live ? mapLiveRunStateToDeviceStatus(live.RUNSTATE) : "running",
        power,
      });
    }

    return markers;
  }, [robotPoses, mapMeta, mapImageSize, apiRobots, apiRobotsFull, liveByIp]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1200px)");
    const handleChange = () => {
      if (media.matches) {
        setNavCollapsed(true);
        setLeftCollapsed(true);
      }
    };

    handleChange();
    if (media.addEventListener) {
      media.addEventListener("change", handleChange);
      return () => media.removeEventListener("change", handleChange);
    }

    media.addListener(handleChange);
    return () => media.removeListener(handleChange);
  }, []);

  const handleNavToggle = () => {
    setNavCollapsed((v) => !v);
  };

  const handleNavItemSelect = () => {
    setNavCollapsed(true);
  };

  const handleItemToggle = (label: string, checked: boolean) => {
    setOverlayItems((prev) =>
      prev.map((item) =>
        item.label === label ? { ...item, checked } : item
      )
    );
  };

  const handleDeviceToggle = (deviceId: string) => {
    setExpandedDeviceId((prev) => (prev === deviceId ? null : deviceId));
  };


  // ── API 로봇 → DeviceRow 형식 변환 ──
  const deviceList = useMemo(() => {
    return apiRobotsFull.map((robot) => {
      const live = robot.ip_address ? liveByIp.get(robot.ip_address) : null;

      let power: "online" | "offline";
      let battery: string;
      let status: "idle" | "running" | "charging" | "error" | "warning" | "disable";

      if (live) {
        power = mapLiveOnlineToPower(live.ONLINE);
        battery = formatLiveBattery(live["POWER(%)"], live.ONLINE);
        status = mapLiveRunStateToDeviceStatus(live.RUNSTATE);
      } else if (robot.status) {
        power = mapBackendStatusToPower(robot.status.status);
        battery = formatBattery(robot.status.battery_level, power);
        status = mapBackendStatusToDeviceStatus(robot.status.status);
      } else {
        power = "offline";
        battery = "--";
        status = "disable";
      }

      return {
        id: String(robot.id),
        name: robot.name,
        power,
        battery,
        status,
        ip: robot.ip_address || "",
      };
    });
  }, [apiRobotsFull, liveByIp]);

  const filteredDevices = useMemo(() => {
    if (!deviceSearch) return deviceList;
    return deviceList.filter((d) =>
      d.name.toLowerCase().includes(deviceSearch.toLowerCase())
    );
  }, [deviceList, deviceSearch]);

  const deviceCounts = useMemo(() => ({
    all: deviceList.length,
    online: deviceList.filter((d) => d.power === "online").length,
    offline: deviceList.filter((d) => d.power === "offline").length,
    error: deviceList.filter((d) => d.status === "error").length,
  }), [deviceList]);

  // ── 로봇 정보 모달 (실제 데이터) ──
  const selectedDevice: RobotDevice | null = useMemo(() => {
    if (!openDeviceId) return null;
    const robot = apiRobotsFull.find((r) => String(r.id) === openDeviceId);
    if (!robot) return null;

    const live = robot.ip_address ? liveByIp.get(robot.ip_address) : null;

    let runState: RunState | null = null;
    if (live) {
      if (live.RUNSTATE === "EXECUTING") runState = "EXECUTING";
      else if (live.RUNSTATE === "CHARGING") runState = "CHARGING";
      else if (live.RUNSTATE === "IDLE") runState = "IDLE";
    }

    return {
      id: String(robot.id),
      sn: robot.serial_number,
      robotName: robot.name,
      model: robot.model ?? "-",
      runState,
      online: live ? live.ONLINE === "Online" : (robot.status?.status !== 4),
      signal: live && live.SIGNAL !== "N/A" ? parseInt(live.SIGNAL, 10) || null : null,
      power: live && live.ONLINE === "Online"
        ? parseInt(live["POWER(%)"], 10) || null
        : robot.status?.battery_level ?? null,
      enable: robot.is_active,
      nickname: live?.NICKNAME ?? null,
      ip: robot.ip_address ?? null,
      axbotVersion: live?.AXBOT_VERSION ?? null,
      platform: live?.PLATFORM ?? null,
      busiName: null,
      buildingName: null,
      robotType: ((robot.robot_type ?? "lifting") as "lifting" | "serving"),
      currentTask: [],
    };
  }, [openDeviceId, apiRobotsFull, liveByIp]);

  const showMapBackground =
    overlayItems.find((item) => item.label === "맵 배경")?.checked ?? true;
  const showNavigationLine =
    overlayItems.find((item) => item.label === "내비게이션 경로")?.checked ?? false;
  const showDirectionArrows =
    overlayItems.find((item) => item.label === "이동 방향")?.checked ?? false;
  const showVirtualWalls =
    overlayItems.find((item) => item.label === "가상 벽")?.checked ?? false;
  const showNavigationNodes =
    overlayItems.find((item) => item.label === "내비게이션 노드")?.checked ?? false;
  const showPoiMarkers =
    overlayItems.find((item) => item.label === "작업 지점")?.checked ?? false;

  const renderedWaypoints = showNavigationNodes ? apiWaypoints : [];
  const routeWaypoints = showNavigationLine ? apiRouteWaypoints : [];
  const routeSegments = showNavigationLine ? apiRouteSegments : [];

  return (
    <>
      <div className="app-shell">
      <TopBar
        dateTime={currentDateTime}
        onToggleNav={handleNavToggle}
        navExpanded={!navCollapsed}
      />
      <div className="shell-body">
        <SideNav
          items={defaultNavItems}
          collapsed={navCollapsed}
          onClose={() => setNavCollapsed(true)}
          onItemSelect={handleNavItemSelect}
        />
        <main className="main-content" style={{ display: "flex" }}>
          <Panel
            title="로봇 관리"
            collapsed={leftCollapsed}
            collapsedTogglePosition="end"
            onToggle={() => setLeftCollapsed((value) => !value)}
            toggleIcon="right"
            className="panel--overlay panel--overlay-left"
            headerContent={
              <>
                <div className="chip-row">
                  <button className="chip">
                    <span className="chip__label">전체</span>
                    <span className="chip__count">{deviceCounts.all}</span>
                  </button>
                  <button className="chip">
                    <span className="chip__label">온라인</span>
                    <span className="chip__count">{deviceCounts.online}</span>
                  </button>
                  <button className="chip">
                    <span className="chip__label">오프라인</span>
                    <span className="chip__count">{deviceCounts.offline}</span>
                  </button>
                  <button className="chip">
                    <span className="chip__label">오류</span>
                    <span className="chip__count">{deviceCounts.error}</span>
                  </button>
                </div>
                <SearchInput
                  placeholder="로봇 명"
                  onSearch={setDeviceSearch}
                />
              </>
            }
          >
            <div className="left-panel-split">
              <div className="left-panel-split__top">
                <div className="device-list">
                  <div className="device-row__header">
                    <span className="device-row__header-cell">로봇 명</span>
                    <span className="device-row__header-cell">전원</span>
                    <span className="device-row__header-cell">배터리</span>
                    <span className="device-row__header-cell">상태</span>
                  </div>
                  {filteredDevices.length === 0 ? (
                    <div className="device-list__empty">등록된 로봇이 없습니다.</div>
                  ) : (
                    <>
                      {filteredDevices
                        .slice((devicePage - 1) * DEVICE_PAGE_SIZE, devicePage * DEVICE_PAGE_SIZE)
                        .map((device) => (
                          <DeviceRow
                            key={device.id}
                            id={device.id}
                            name={device.name}
                            power={device.power}
                            battery={device.battery}
                            status={device.status}
                            ip={device.ip}
                            isExpanded={expandedDeviceId === device.id}
                            onToggleExpand={handleDeviceToggle}
                            onInfo={setOpenDeviceId}
                            onRemote={(id: string, ip: string) => setRemoteTarget({ id, name: device.name, ip })}
                          />
                        ))}
                      {filteredDevices.length > DEVICE_PAGE_SIZE && (
                        <div className="device-list__pagination">
                          {Array.from(
                            { length: Math.ceil(filteredDevices.length / DEVICE_PAGE_SIZE) },
                            (_, i) => i + 1
                          ).map((num) => (
                            <button
                              key={num}
                              className={`device-list__page-btn ${num === devicePage ? "device-list__page-btn--active" : ""}`}
                              onClick={() => setDevicePage(num)}
                            >
                              {num}
                            </button>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
              <div className="left-panel-split__divider" />
              <div className="left-panel-split__bottom">
                <DispatchTabs
                  liveRobots={liveRobots}
                  areaId={selectedArea ? Number(selectedArea) : undefined}
                />
              </div>
            </div>
          </Panel>

          <div
            className="monitoring-stage"
            style={{
              flex: 1,
              minWidth: 0,
            }}
          >
            <section className={mapMode === "3d" ? "monitoring-map is-3d" : "monitoring-map"}>
              <BusinessSelectBox
                businesses={businessesForSelectBox}
                selectedId={`${selectedBusiness}:${selectedArea}`}
                onChange={handleBusinessAreaChange}
                extraButton={selectedArea && apiRobotsFull.length > 0 ? (
                  <button
                    className="btn btn--ghost"
                    style={{ fontSize: 13, whiteSpace: "nowrap", padding: "4px 10px" }}
                    onClick={async () => {
                      const robotId = apiRobotsFull[0]?.id;
                      if (!robotId) return;
                      try {
                        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || ""}/api/robots/${robotId}/switch-floor`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ area_id: Number(selectedArea) }),
                        });
                        if (res.ok) {
                          const d = await res.json();
                          showAlert({ title: "층 전환", message: `층 전환 완료: ${d.map_name}` });
                          window.location.reload();
                        } else if (res.status === 409) {
                          showAlert({ title: "층 전환", message: "로봇이 작업 중입니다" });
                        } else {
                          const d = await res.json();
                          showAlert({ title: "층 전환", message: d.detail || "층 전환 실패" });
                        }
                      } catch { showAlert({ title: "층 전환", message: "연결 오류" }); }
                    }}
                  >층 전환</button>
                ) : undefined}
              />
              {isLoading ? (
                <div className="monitoring-map__loading">
                  <div className="spinner" />
                </div>
              ) : !selectedArea ? (
                <div className="monitoring-map__empty">
                  <p>현재 사업장에 등록된 영역이 없습니다.</p>
                </div>
              ) : mapMode === "3d" ? (
                <MonitoringMap3D
                  mapSrc={mapSrc}
                  pois={apiPois}
                  waypoints={renderedWaypoints}
                  routeWaypoints={routeWaypoints}
                  routeSegments={routeSegments}
                  robots={simulatedRobots}
                  virtualWalls={[]}
                  showMapBackground={showMapBackground}
                  showNavigationLine={showNavigationLine}
                  showDirectionArrows={showDirectionArrows}
                  showVirtualWalls={showVirtualWalls}
                  showNavigationNodes={showNavigationNodes}
                  showPoiMarkers={showPoiMarkers}
                />
              ) : (
                <MonitoringMapCanvas
                  mapSrc={mapSrc}
                  pois={apiPois}
                  waypoints={renderedWaypoints}
                  routeWaypoints={routeWaypoints}
                  routeSegments={routeSegments}
                  robots={simulatedRobots}
                  virtualWalls={[]}

                  showMapBackground={showMapBackground}
                  showNavigationLine={showNavigationLine}
                  showDirectionArrows={showDirectionArrows}
                  showVirtualWalls={showVirtualWalls}
                  showNavigationNodes={showNavigationNodes}
                  showPoiMarkers={showPoiMarkers}
                  robotTargets={(() => {
                    if (!mapMeta || !mapImageSize || robotTargets.size === 0) return undefined;
                    const converted = new Map<string, { x: number; y: number }>();
                    robotTargets.forEach((target, sn) => {
                      if (target) {
                        converted.set(sn, {
                          x: (target.x - mapMeta!.grid_origin_x) / mapMeta!.grid_resolution,
                          y: mapImageSize!.h - (target.y - mapMeta!.grid_origin_y) / mapMeta!.grid_resolution,
                        });
                      }
                    });
                    return converted;
                  })()}
                />
              )}
            </section>
            <div className="overlay-card-layer">
              {isLayerOpen && (
                <OverlayCard
                  items={overlayItems}
                  onItemToggle={handleItemToggle}
                />
              )}
              <div className="overlay-card__footer">
                <LayerButton
                  isActive={isLayerOpen}
                  onToggle={() => setIsLayerOpen((v) => !v)}
                />
                <MapModeButton
                  mapMode={mapMode}
                  onMapModeChange={setMapMode}
                />
              </div>
            </div>
          </div>

          {/* 오른쪽 작업 현황 패널 */}
          <Panel
            title="작업 현황"
            collapsed={rightCollapsed}
            collapsedTogglePosition="start"
            onToggle={() => setRightCollapsed((v) => !v)}
            toggleIcon="left"
            className="panel--overlay panel--overlay-right"
          >
            <JobStatusPanel />
            {zoneLocks.length > 0 && (
              <div style={{
                marginTop: 8,
                padding: "8px 10px",
                borderRadius: 6,
                background: "rgba(255, 59, 59, 0.08)",
                border: "1px solid rgba(255, 59, 59, 0.4)",
                fontSize: 12,
              }}>
                <div style={{ color: "#ff5b5b", fontWeight: 600, marginBottom: 4 }}>
                  통로 점유 중 ({zoneLocks.length})
                </div>
                {zoneLocks.map((z) => (
                  <div key={z.zone_id} style={{ color: "#eee", lineHeight: 1.4 }}>
                    {z.zone_name ?? `zone#${z.zone_id}`} ← {z.robot_name ?? `robot#${z.robot_id}`}
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {selectedDevice && (
            <RobotDeviceInfo
              device={selectedDevice}
              onClose={() => setOpenDeviceId(null)}
              onEnableToggle={handleDeviceEnableToggle}
              togglingDeviceId={togglingDeviceId}
              showChargingStation
              readOnly
            />
          )}
          {remoteTarget && (
            <RemoteControlModal
              robotName={remoteTarget.name}
              robotIp={remoteTarget.ip}
              onClose={() => setRemoteTarget(null)}
            />
          )}
        </main>
      </div>
    </div>
    </>
  );
}

