"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { TopBar } from "../components/shell/TopBar";
import { SideNav, defaultNavItems } from "../components/shell/SideNav";
import { MapTopBar } from "../components/ui/map/MapTopBar";
import { MapCanvas } from "../components/ui/map/MapCanvas";
import { MapToolbarTop } from "../components/ui/map/MapToolbarTop";
import { MapFloatingPanel } from "../components/ui/map/MapFloatingPanel";
import { RobotConnectModal } from "../components/ui/map/RobotConnectModal";
import { MappingSetupModal } from "../components/ui/map/MappingSetupModal";
import { MappingModal } from "../components/ui/map/MappingModal";
import { MapSyncModal } from "../components/ui/map/MapSyncModal";
import { MapRelocalizeModal } from "../components/ui/map/MapRelocalizeModal";
import { POIEditPopup } from "../components/ui/map/POIEditPopup";
import { LineDirectionPopup } from "../components/ui/map/LineDirectionPopup";
import { LineEditPopup } from "../components/ui/map/LineEditPopup";
import type {
  MapTool,
  POI,
  POIType,
  PathLine,
  PolygonShape,
  LineDirection,
  ConnectedRobot,
  RobotPose,
  MapMeta,
} from "@/lib/types/map";
import { apiFetch } from "@/lib/api";
import {
  getStoredBusiness, getStoredArea, setStoredBusiness, setStoredArea,
} from "@/lib/util/selectedScope";
import { ConfirmModal } from "../components/ui/robots/ConfirmModal";
import { useAlert } from "@/lib/context/AlertContext";
import "./map.css";

type BusinessItem = {
  business_id: number;
  name: string;
};

type AreaItem = {
  area_id: number;
  name: string;
  is_main_floor?: boolean;
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

function formatDateTime() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  const hh = String(now.getHours()).padStart(2, "0");
  const min = String(now.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
}

let nextId = 1;
function generateId(prefix: string) {
  return `${prefix}-${nextId++}`;
}

/** DB에서 불러온 ID들("poi-123" 등)과 충돌하지 않도록 nextId를 갱신 */
function syncNextId(ids: string[]) {
  for (const id of ids) {
    const num = parseInt(id.split("-")[1] ?? "0", 10);
    if (num >= nextId) nextId = num + 1;
  }
}

// POI 타입별 자동 이름 prefix
const POI_NAME_PREFIX: Record<string, string> = {
  charging: "C",
  jack: "J",
  standby: "R",   // Rack 위치
  waypoint: "W",  // Waypoint
};

/** 같은 타입의 기존 POI 이름에서 'PrefixN' 패턴을 찾아 max+1 번호의 이름을 생성. */
function nextPoiName(pois: POI[], type: POIType): string {
  const prefix = POI_NAME_PREFIX[type] || "P";
  const re = new RegExp(`^${prefix}(\\d+)$`);
  let maxN = 0;
  for (const p of pois) {
    if (p.type !== type) continue;
    const m = p.name?.match(re);
    if (m) {
      const n = parseInt(m[1], 10);
      if (n > maxN) maxN = n;
    }
  }
  return `${prefix}${maxN + 1}`;
}

export default function MapPage() {
  const [navCollapsed, setNavCollapsed] = useState(true);
  const [currentDateTime, setCurrentDateTime] = useState(formatDateTime);
  // Map state
  const [pois, setPois] = useState<POI[]>([]);
  const [lines, setLines] = useState<PathLine[]>([]);
  const [polygons, setPolygons] = useState<PolygonShape[]>([]);
  const [activeTool, setActiveTool] = useState<MapTool>("select");
  const [selectedPOI, setSelectedPOI] = useState<string | null>(null);
  const [lineStartPOI, setLineStartPOI] = useState<string | null>(null);
  const [polygonPoints, setPolygonPoints] = useState<{ x: number; y: number }[]>([]);

  // Zoom/Pan/Rotation
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [rotation, setRotation] = useState(0);
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const offsetInitialized = useRef(false);
  const initialLoadRef = useRef(true);

  // Center the map on first render
  useEffect(() => {
    if (offsetInitialized.current) return;
    const el = canvasWrapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      setOffset({ x: rect.width / 2, y: rect.height / 2 });
      offsetInitialized.current = true;
    }
  });

  // UI state
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [floatingPanelOpen, setFloatingPanelOpen] = useState(true);
  const [connectModalOpen, setConnectModalOpen] = useState(false);
  const [syncModalOpen, setSyncModalOpen] = useState(false);
  const [relocalizeModalOpen, setRelocalizeModalOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [connectedRobot, setConnectedRobot] = useState<ConnectedRobot>(null);

  // Mapping flow
  const [mappingSetupOpen, setMappingSetupOpen] = useState(false);
  const [mappingModalOpen, setMappingModalOpen] = useState(false);
  const [mappingBusinessId, setMappingBusinessId] = useState<number | null>(null);
  const [mappingAreaId, setMappingAreaId] = useState("");
  const [mappingAreaName, setMappingAreaName] = useState("");

  // Popups
  const [editingPOI, setEditingPOI] = useState<POI | null>(null);
  const [pendingPOIId, setPendingPOIId] = useState<string | null>(null);
  const [deletePOITarget, setDeletePOITarget] = useState<POI | null>(null);
  const [editingLine, setEditingLine] = useState<PathLine | null>(null);
  const [lineDirectionPopup, setLineDirectionPopup] = useState<{
    fromId: string;
    toId: string;
    position: { x: number; y: number };
  } | null>(null);

  // Selectors
  const [businesses, setBusinesses] = useState<BusinessItem[]>([]);
  const [selectedBusiness, setSelectedBusiness] = useState("");
  const [areas, setAreas] = useState<AreaItem[]>([]);
  const [selectedArea, setSelectedArea] = useState("");
  const [areaMaps, setAreaMaps] = useState<MapItem[]>([]);
  const [selectedMapId, setSelectedMapId] = useState<number | null>(null);
  const [selectedMappingId, setSelectedMappingId] = useState<number | null>(null);
  const [mapImageUrl, setMapImageUrl] = useState<string | null>(null);
  const [mapMeta, setMapMeta] = useState<MapMeta>(null);

  // Robot pose (real-time)
  const [robotPose, setRobotPose] = useState<RobotPose>(null);
  const poseWsRef = useRef<WebSocket | null>(null);
  const vwPointsRef = useRef<{ x: number; y: number }[]>([]);
  const [vwTempPoints, setVwTempPoints] = useState<{ x: number; y: number }[]>([]);
  const [mapImageSize, setMapImageSize] = useState<{ w: number; h: number } | null>(null);

  const { showAlert, showInfo } = useAlert();

  // Undo history
  const [history, setHistory] = useState<{
    pois: POI[];
    lines: PathLine[];
    polygons: PolygonShape[];
  }[]>([]);

  useEffect(() => {
    const timer = setInterval(() => setCurrentDateTime(formatDateTime()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Esc 키 → select 모드로 복귀
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setActiveTool("select");
        setSelectedPOI(null);
        setEditingPOI(null);
        setEditingLine(null);
        setLineStartPOI(null);
        setLineDirectionPopup(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // 마지막 선택 사업장·영역을 다른 페이지와 공유
  useEffect(() => { setStoredBusiness(selectedBusiness); }, [selectedBusiness]);
  useEffect(() => { setStoredArea(selectedArea); }, [selectedArea]);

  // 사업장 목록 로드 — 백엔드 적용 default 영역 우선, 없으면 stored, 없으면 첫 번째
  useEffect(() => {
    Promise.all([
      apiFetch<{ total: number; items: BusinessItem[] }>("/api/map/businesses"),
      fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/map/default-area`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null) as Promise<{ area_id: number | null; business_id: number | null } | null>,
    ])
      .then(([data, defaultArea]) => {
        setBusinesses(data.items);
        if (!selectedBusiness && data.items.length > 0) {
          // 1) 백엔드의 default 영역에 매칭되는 사업장 우선
          let target: BusinessItem | undefined;
          if (defaultArea?.business_id != null) {
            target = data.items.find((b) => b.business_id === defaultArea.business_id);
          }
          // 2) localStorage 사업장
          if (!target) {
            const stored = getStoredBusiness();
            if (stored) target = data.items.find((b) => String(b.business_id) === stored);
          }
          // 3) 첫 번째
          if (!target) target = data.items[0];
          setSelectedBusiness(String(target.business_id));
          // default 영역도 stored 에 미리 반영 → 영역 useEffect 가 그 값을 우선 선택
          if (defaultArea?.area_id != null) {
            setStoredArea(String(defaultArea.area_id));
          }
        }
      })
      .catch((err) => {
        console.error("[사업장 목록 로드 실패]", err);
        showAlert({ title: "알림", message: "사업장 목록을 불러오는 데 실패했습니다.", errorCode: "MAP-001", errorType: "map", source: "맵 관리 > 초기 로드", description: "MapPage — 사업장 목록 로드" });
      });
  }, []);

  // Business 변경 시 영역 목록 로드
  useEffect(() => {
    if (!selectedBusiness) {
      setAreas([]);
      setSelectedArea("");
      setAreaMaps([]);
      setMapImageUrl(null);
      return;
    }
    setAreaMaps([]);
    setMapImageUrl(null);
    apiFetch<{ total: number; items: AreaItem[] }>(
      `/api/map/businesses/${selectedBusiness}/areas`
    )
      .then((data) => {
        setAreas(data.items);
        if (data.items.length > 0) {
          // 마지막 사용 영역 우선, 없으면 가장 최근 추가된 영역(맨 마지막 row)
          const stored = getStoredArea();
          const found = stored
            ? data.items.find((a) => String(a.area_id) === stored)
            : null;
          setSelectedArea(String((found ?? data.items[data.items.length - 1]).area_id));
        } else {
          setSelectedArea("");
        }
      })
      .catch((err) => {
        console.error("[영역 목록 로드 실패]", err);
        showAlert({ title: "알림", message: "영역 목록을 불러오는 데 실패했습니다.", errorCode: "MAP-002", errorType: "map", source: "맵 관리 > 초기 로드", description: "MapPage — 영역 목록 로드" });
        setAreas([]);
      });
  }, [selectedBusiness]);

  // Area 변경 시 맵 목록 로드 및 첫 번째 맵 이미지 표시
  useEffect(() => {
    if (!selectedArea) {
      setAreaMaps([]);
      setSelectedMapId(null);
      setSelectedMappingId(null);
      setMapImageUrl(null);
      setMapMeta(null);
      setPois([]);
      setLines([]);
      return;
    }
    apiFetch<{ total: number; items: MapItem[] }>(
      `/api/map/areas/${selectedArea}/maps`
    )
      .then((data) => {
        setAreaMaps(data.items);
        // 백엔드가 id.desc() 정렬 — 첫 번째가 최신 맵
        if (data.items.length > 0 && data.items[0].image_url) {
          const map = data.items[0];
          setSelectedMapId(map.id);
          setSelectedMappingId(map.mapping_id);
          const imgUrl = map.image_url!;
          if (imgUrl.startsWith("/static/")) {
            setMapImageUrl(`${process.env.NEXT_PUBLIC_API_URL}${imgUrl}`);
          } else {
            setMapImageUrl(`${process.env.NEXT_PUBLIC_API_URL}/api/map/proxy-image?url=${encodeURIComponent(imgUrl)}`);
          }
          setMapMeta({
            grid_origin_x: map.grid_origin_x,
            grid_origin_y: map.grid_origin_y,
            grid_resolution: map.grid_resolution,
          });
          // 저장된 POI·라인 로드
          apiFetch<{ pois: any[]; lines: any[]; polygons?: any[] }>(
            `/api/map/maps/${map.id}/elements`
          )
            .then((elems) => {
              const loadedPois = elems.pois.map((p: any) => ({
                id: p.id,
                x: p.x,
                y: p.y,
                name: p.name,
                type: p.type,
                phoneNumber: p.phoneNumber ?? undefined,
                angle: p.angle ?? undefined,
                loadType: p.loadType ?? undefined,
                robotSns: p.robotSns ?? undefined,
                address: p.address ?? undefined,
                dockingRadius: p.dockingRadius ?? undefined,
                rackSize: p.rackSize ?? undefined,
              }));
              const loadedLines = elems.lines.map((l: any) => ({
                id: l.id,
                fromId: l.fromId,
                toId: l.toId,
                direction: l.direction,
                lineType: l.lineType,
                controlPoints: l.controlPoints ?? undefined,
              }));
              const loadedPolygons = (elems.polygons || []).map((pg: any) => ({
                id: pg.id,
                name: pg.name,
                shapeType: pg.shapeType ?? "polygon",
                points: pg.points ?? [],
              }));
              setPois(loadedPois);
              setLines(loadedLines);
              setPolygons(loadedPolygons);
              syncNextId([
                ...loadedPois.map((p: any) => p.id),
                ...loadedLines.map((l: any) => l.id),
                ...loadedPolygons.map((pg: any) => pg.id),
              ]);
            })
            .catch((err) => {
              console.error("[맵 요소 로드 실패]", err);
              showAlert({ title: "알림", message: "맵 요소(POI·라인)를 불러오는 데 실패했습니다.", errorCode: "MAP-004", errorType: "map", source: "맵 관리 > 초기 로드", description: "MapPage — 맵 요소 로드" });
              setPois([]);
              setLines([]);
            });
        } else {
          setSelectedMapId(null);
          setSelectedMappingId(null);
          setMapImageUrl(null);
          setMapMeta(null);
          setPois([]);
          setLines([]);
        }
      })
      .catch((err) => {
        console.error("[맵 목록 로드 실패]", err);
        showAlert({ title: "알림", message: "맵 목록을 불러오는 데 실패했습니다.", errorCode: "MAP-003", errorType: "map", source: "맵 관리 > 초기 로드", description: "MapPage — 맵 목록 로드" });
        setAreaMaps([]);
        setSelectedMapId(null);
        setSelectedMappingId(null);
        setMapImageUrl(null);
        setMapMeta(null);
        setPois([]);
        setLines([]);
      });
  }, [selectedArea]);

  // ── 로봇 실시간 위치 WS 연결 ──
  useEffect(() => {
    // 기존 WS 정리
    if (poseWsRef.current) {
      poseWsRef.current.close();
      poseWsRef.current = null;
    }
    setRobotPose(null);

    if (!connectedRobot) return;

    const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
    const wsUrl = `${API_URL.replace(/^http/, "ws")}/api/map/ws/${connectedRobot.ip}?topics=/tracked_pose`;
    const ws = new WebSocket(wsUrl);
    poseWsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.topic === "/tracked_pose" && msg.pos) {
          setRobotPose({ pos: msg.pos, ori: msg.ori ?? 0 });
        }
      } catch {
        // ignore
      }
    };
    ws.onerror = () => {
      console.error("[PoseWS] 로봇 위치 WebSocket 연결 오류");
    };
    ws.onclose = (ev) => {
      console.log("[PoseWS] 연결 종료", ev.code, ev.reason);
      if (ev.code !== 1000 && ev.code !== 1005) {
        console.warn("[PoseWS] 비정상 종료 — 로봇 위치 수신이 중단되었습니다.");
      }
    };

    return () => {
      ws.close();
      poseWsRef.current = null;
    };
  }, [connectedRobot]);

  const pushHistory = useCallback(() => {
    setHistory((prev) => [
      ...prev.slice(-19),
      {
        pois: pois.map((p) => ({ ...p })),
        lines: lines.map((l) => ({ ...l })),
        polygons: polygons.map((p) => ({ ...p, points: [...p.points] })),
      },
    ]);
  }, [pois, lines, polygons]);

  const handleUndo = useCallback(() => {
    setHistory((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      setPois(last.pois);
      setLines(last.lines);
      setPolygons(last.polygons);
      return prev.slice(0, -1);
    });
  }, []);

  const handleFullscreen = useCallback(() => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
      setIsFullscreen(true);
    } else {
      document.exitFullscreen().catch(() => {});
      setIsFullscreen(false);
    }
  }, []);

  // ── Canvas Click Handler ──
  const handleCanvasClick = useCallback(
    (x: number, y: number) => {
      if (activeTool === "point") {
        // 기존 POI 위 클릭 시 새 POI 생성 방지
        const hitPOI = pois.find(
          (p) => Math.abs(p.x - x) < 15 / zoom && Math.abs(p.y - y) < 15 / zoom
        );
        if (hitPOI) {
          setSelectedPOI(hitPOI.id);
          setEditingPOI(hitPOI);
          return;
        }
        pushHistory();
        const newPOI: POI = {
          id: generateId("poi"),
          x,
          y,
          name: nextPoiName(pois, "waypoint"),
          type: "waypoint",
        };
        setPois((prev) => [...prev, newPOI]);
        setEditingPOI(newPOI);
        setSelectedPOI(newPOI.id);
        setPendingPOIId(newPOI.id);
      } else if (activeTool === "jackPoint") {
        const hitPOI = pois.find(
          (p) => Math.abs(p.x - x) < 15 / zoom && Math.abs(p.y - y) < 15 / zoom
        );
        if (hitPOI) {
          setSelectedPOI(hitPOI.id);
          setEditingPOI(hitPOI);
          return;
        }
        pushHistory();
        const newPOI: POI = {
          id: generateId("poi"),
          x,
          y,
          name: nextPoiName(pois, "jack"),
          type: "jack",
          rackSize: "S600",
        };
        setPois((prev) => [...prev, newPOI]);
        setEditingPOI(newPOI);
        setSelectedPOI(newPOI.id);
        setPendingPOIId(newPOI.id);
      } else if ((activeTool === "line" || activeTool === "curveLine") && lineStartPOI) {
        // 기존 POI 클릭 시 그대로 연결, 빈 캔버스 클릭 시 새 POI 생성 후 연결
        const hitPOI = pois.find(
          (p) => Math.abs(p.x - x) < 15 / zoom && Math.abs(p.y - y) < 15 / zoom
        );

        let toId: string;
        let toX: number;
        let toY: number;
        if (hitPOI) {
          toId = hitPOI.id;
          toX = hitPOI.x;
          toY = hitPOI.y;
        } else {
          pushHistory();
          const newPOI: POI = {
            id: generateId("poi"),
            x,
            y,
            name: nextPoiName(pois, "waypoint"),
            type: "waypoint",
          };
          setPois((prev) => [...prev, newPOI]);
          toId = newPOI.id;
          toX = x;
          toY = y;
        }

        const from = pois.find((p) => p.id === lineStartPOI);
        if (from) {
          setLineDirectionPopup({
            fromId: lineStartPOI,
            toId,
            position: {
              x: ((from.x + toX) / 2) * zoom + offset.x,
              y: ((from.y + toY) / 2) * zoom + offset.y,
            },
          });
        }
      } else if (activeTool === "firewall") {
        // 방화벽: 기존 두 점 클릭 → 라인 방식
        if (!lineStartPOI) {
          pushHistory();
          const fwCount = pois.filter((p) => p.type === "firewall").length;
          const newPOI: POI = {
            id: generateId("poi"),
            x, y,
            name: `FW${fwCount + 1}`,
            type: "firewall",
          };
          setPois((prev) => [...prev, newPOI]);
          setLineStartPOI(newPOI.id);
        } else {
          pushHistory();
          const fwCount = pois.filter((p) => p.type === "firewall").length;
          const newPOI: POI = {
            id: generateId("poi"),
            x, y,
            name: `FW${fwCount + 1}`,
            type: "firewall",
          };
          const newLine: PathLine = {
            id: generateId("line"),
            fromId: lineStartPOI,
            toId: newPOI.id,
            direction: "bidirectional",
            lineType: "firewall",
          };
          setPois((prev) => [...prev, newPOI]);
          setLines((prev) => [...prev, newLine]);
          setLineStartPOI(null);
        }
      } else if (activeTool === "virtualwall") {
        // 가상벽: 4점 클릭으로 사각형 생성
        vwPointsRef.current.push({ x, y });
        if (vwPointsRef.current.length >= 4) {
          pushHistory();
          const vwCount = polygons.filter((p) => p.shapeType === "firewall").length;
          const newPolygon: PolygonShape = {
            id: generateId("polygon"),
            points: [...vwPointsRef.current],
            name: `VW${vwCount + 1}`,
            shapeType: "firewall",
          };
          setPolygons((prev) => [...prev, newPolygon]);
          vwPointsRef.current = [];
          setVwTempPoints([]);
        } else {
          setVwTempPoints([...vwPointsRef.current]);
        }
      } else if (activeTool === "polygon") {
        setPolygonPoints((prev) => [...prev, { x, y }]);
      }
    },
    [activeTool, pois.length, pushHistory, lineStartPOI, pois, zoom, offset]
  );

  // ── POI Click Handler ──
  const handlePOIClick = useCallback(
    (id: string) => {
      if (activeTool === "del") {
        const target = pois.find((p) => p.id === id);
        if (!target) return;
        setDeletePOITarget(target);
        return;
      }

      if (activeTool === "firewall") {
        if (!lineStartPOI) {
          setLineStartPOI(id);
        } else if (lineStartPOI !== id) {
          pushHistory();
          const newLine: PathLine = {
            id: generateId("line"),
            fromId: lineStartPOI,
            toId: id,
            direction: "bidirectional",
            lineType: "firewall",
          };
          setLines((prev) => [...prev, newLine]);
          setLineStartPOI(null);
        } else {
          setLineStartPOI(null);
        }
        return;
      }

      if (activeTool === "line" || activeTool === "curveLine") {
        if (!lineStartPOI) {
          setLineStartPOI(id);
        } else if (lineStartPOI !== id) {
          // Show direction popup
          const from = pois.find((p) => p.id === lineStartPOI);
          const to = pois.find((p) => p.id === id);
          if (from && to) {
            setLineDirectionPopup({
              fromId: lineStartPOI,
              toId: id,
              position: {
                x: ((from.x + to.x) / 2) * zoom + offset.x,
                y: ((from.y + to.y) / 2) * zoom + offset.y,
              },
            });
          }
        } else {
          setLineStartPOI(null);
        }
        return;
      }

      // Select mode — open edit popup
      if (activeTool === "select") {
        const poi = pois.find((p) => p.id === id);
        if (poi) {
          setSelectedPOI(id);
          setEditingPOI(poi);
        }
      }
    },
    [activeTool, lineStartPOI, pois, zoom, offset, pushHistory]
  );

  // ── Line Click Handler ──
  const handleLineClick = useCallback(
    (id: string) => {
      if (activeTool === "del") {
        pushHistory();
        setLines((prev) => prev.filter((l) => l.id !== id));
        return;
      }
      if (activeTool === "select") {
        const line = lines.find((l) => l.id === id);
        if (line) setEditingLine(line);
      }
    },
    [activeTool, lines, pushHistory]
  );

  // ── Line Edit Handlers ──
  const handleLineUpdate = useCallback(
    (id: string, data: Partial<PathLine>) => {
      pushHistory();
      setLines((prev) =>
        prev.map((l) => (l.id === id ? { ...l, ...data } : l))
      );
    },
    [pushHistory]
  );

  const handleLineDelete = useCallback(
    (id: string) => {
      pushHistory();
      setLines((prev) => prev.filter((l) => l.id !== id));
      setEditingLine(null);
    },
    [pushHistory]
  );

  // ── Polygon Click Handler ──
  const handlePolygonClick = useCallback(
    (id: string) => {
      if (activeTool === "del") {
        pushHistory();
        setPolygons((prev) => prev.filter((p) => p.id !== id));
      }
    },
    [activeTool, pushHistory]
  );

  // ── Line Direction Selection ──
  const handleLineDirectionSelect = useCallback(
    (direction: LineDirection) => {
      if (!lineDirectionPopup) return;
      pushHistory();
      const newLine: PathLine = {
        id: generateId("line"),
        fromId: lineDirectionPopup.fromId,
        toId: lineDirectionPopup.toId,
        direction,
        lineType: activeTool === "curveLine" ? "curve" : "straight",
      };
      setLines((prev) => [...prev, newLine]);
      setLineStartPOI(null);
      setLineDirectionPopup(null);
    },
    [lineDirectionPopup, activeTool, pushHistory]
  );

  // ── POI Edit Handlers ──
  const handlePOIUpdate = useCallback(
    (id: string, data: Partial<POI>) => {
      pushHistory();
      setPois((prev) =>
        prev.map((p) => (p.id === id ? { ...p, ...data } : p))
      );
      setPendingPOIId(null);
      setEditingPOI(null);
      setSelectedPOI(null);
    },
    [pushHistory]
  );

  const handlePOIDelete = useCallback(
    (id: string) => {
      pushHistory();
      setPois((prev) => prev.filter((p) => p.id !== id));
      setLines((prev) =>
        prev.filter((l) => l.fromId !== id && l.toId !== id)
      );
      setEditingPOI(null);
      setSelectedPOI(null);
    },
    [pushHistory]
  );

  // ── Tool Change ──
  const handleToolChange = useCallback((tool: MapTool) => {
    setActiveTool(tool);
    setSelectedPOI(null);
    setEditingPOI(null);
    setLineStartPOI(null);
    setLineDirectionPopup(null);

    // 가상벽 도구에서 벗어나면 임시 점 초기화
    if (tool !== "virtualwall") {
      vwPointsRef.current = [];
      setVwTempPoints([]);
    }

    // If switching away from polygon, finalize current polygon
    if (tool !== "polygon") {
      setPolygonPoints((prev) => {
        if (prev.length >= 3) {
          const newPolygon: PolygonShape = {
            id: generateId("poly"),
            points: [...prev],
            name: `Wall${polygons.length + 1}`,
          };
          setPolygons((prevPolygons) => [...prevPolygons, newPolygon]);
        }
        return [];
      });
    }

    // currentPos / currentPosJack / chargingPile: immediately create POI at robot position
    if ((tool === "currentPos" || tool === "currentPosJack" || tool === "chargingPile") && robotPose && mapMeta && mapMeta.grid_resolution > 0 && mapImageSize) {
      const isCharging = tool === "chargingPile";
      const isJack = tool === "currentPosJack";

      // 충전소: 로봇 도킹 위치에서 yaw 방향(앞쪽=충전기 반대) 0.2m 앞에 POI 생성
      //         → 도킹 시 로봇이 충전기에 너무 가까이 붙어서 긁는 것 방지
      // 일반/잭킹 POI: 로봇 현재 위치 그대로 사용
      const DOCKING_OFFSET = -0.1;
      const angle = robotPose.ori;
      const worldX = isCharging
        ? robotPose.pos[0] - DOCKING_OFFSET * Math.cos(angle)
        : robotPose.pos[0];
      const worldY = isCharging
        ? robotPose.pos[1] - DOCKING_OFFSET * Math.sin(angle)
        : robotPose.pos[1];

      // 월드 좌표 → SVG 좌표 변환
      const ipx = (worldX - mapMeta.grid_origin_x) / mapMeta.grid_resolution;
      const ipy = mapImageSize.h - (worldY - mapMeta.grid_origin_y) / mapMeta.grid_resolution;
      const svgX = ipx - mapImageSize.w / 2;
      const svgY = ipy - mapImageSize.h / 2;

      let poiType: POI["type"];
      if (isCharging) poiType = "charging";
      else if (isJack) poiType = "jack";
      else poiType = "waypoint";
      const poiName: string = nextPoiName(pois, poiType);

      const newPOI: POI = {
        id: generateId("poi"),
        x: svgX,
        y: svgY,
        name: poiName,
        type: poiType,
        angle,
        ...(poiType === "jack" ? { rackSize: "S600" as const } : {}),
      };
      setPois((prev) => [...prev, newPOI]);
      setEditingPOI(newPOI);
      setSelectedPOI(newPOI.id);
      setActiveTool("select");
    }
  }, [polygons.length, robotPose, mapMeta, mapImageSize, pois.length]);

  // ── Robot Connection ──
  const handleRobotConnect = useCallback((sn: string, name: string, ip: string) => {
    setConnectedRobot({ sn, name, ip });
    setConnectModalOpen(false);
  }, []);

  // ── Zoom Controls ──
  const handleZoomIn = useCallback(() => {
    setZoom((prev) => Math.min(prev * 1.2, 6));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoom((prev) => Math.max(prev / 1.2, 0.2));
  }, []);

  const handleResetBearing = useCallback(() => {
    setZoom(1);
    setRotation(0);
    const el = canvasWrapRef.current;
    if (el) {
      const rect = el.getBoundingClientRect();
      setOffset({ x: rect.width / 2, y: rect.height / 2 });
    }
  }, []);

  const handleRotateLeft = useCallback(() => {
    setRotation((prev) => prev - 15);
  }, []);

  const handleRotateRight = useCallback(() => {
    setRotation((prev) => prev + 15);
  }, []);

  // ── Clear Map ──
  const [clearMapConfirmOpen, setClearMapConfirmOpen] = useState(false);
  const handleClearMap = useCallback(() => {
    setClearMapConfirmOpen(true);
  }, []);
  const handleClearMapConfirm = useCallback(() => {
    setClearMapConfirmOpen(false);
    pushHistory();
    setPois([]);
    setLines([]);
    setPolygons([]);
    setSelectedPOI(null);
    setEditingPOI(null);
    setLineStartPOI(null);
    setPolygonPoints([]);
  }, [pushHistory]);

  // ── SVG 좌표 → 로봇 물리계(월드) 좌표 변환 ──
  const svgToWorld = useCallback(
    (svgX: number, svgY: number): { worldX: number; worldY: number } | null => {
      if (!mapMeta || !mapImageSize || mapMeta.grid_resolution <= 0) return null;
      const ipx = svgX + mapImageSize.w / 2;
      const ipy = svgY + mapImageSize.h / 2;
      return {
        worldX: ipx * mapMeta.grid_resolution + mapMeta.grid_origin_x,
        worldY: (mapImageSize.h - ipy) * mapMeta.grid_resolution + mapMeta.grid_origin_y,
      };
    },
    [mapMeta, mapImageSize]
  );

  // ── Action buttons (placeholder handlers) ──
  const handleSave = useCallback(() => {
    if (!selectedMapId) {
      showInfo("안내", "저장할 맵을 먼저 선택해 주세요.");
      return;
    }

    // POI에 월드 좌표 추가
    const poisWithWorld = pois.map((p) => {
      const w = svgToWorld(p.x, p.y);
      return { ...p, worldX: w?.worldX ?? null, worldY: w?.worldY ?? null };
    });

    // 라인에 양 끝 월드 좌표 + ori 추가
    const linesWithWorld = lines.map((l) => {
      const fromPoi = pois.find((p) => p.id === l.fromId);
      const toPoi = pois.find((p) => p.id === l.toId);
      const fw = fromPoi ? svgToWorld(fromPoi.x, fromPoi.y) : null;
      const tw = toPoi ? svgToWorld(toPoi.x, toPoi.y) : null;
      return {
        ...l,
        fromWorldX: fw?.worldX ?? null,
        fromWorldY: fw?.worldY ?? null,
        toWorldX: tw?.worldX ?? null,
        toWorldY: tw?.worldY ?? null,
        fromOri: fromPoi?.angle ?? null,
        toOri: toPoi?.angle ?? null,
      };
    });

    apiFetch(`/api/map/maps/${selectedMapId}/elements`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pois: poisWithWorld,
        lines: linesWithWorld,
        polygons: polygons.map((pg) => {
          const pointsWithWorld = pg.points.map((pt) => {
            const w = svgToWorld(pt.x, pt.y);
            return { x: pt.x, y: pt.y, worldX: w?.worldX ?? null, worldY: w?.worldY ?? null };
          });
          return { ...pg, points: pointsWithWorld };
        }),
      }),
    })
      .then(() => {
        showAlert({ title: "저장 완료", message: "저장되었습니다." });
        // 저장 후 맵 목록 갱신하여 최신 이미지 반영
        if (selectedArea) {
          apiFetch<{ total: number; items: MapItem[] }>(`/api/map/areas/${selectedArea}/maps`)
            .then((data) => {
              setAreaMaps(data.items);
              if (data.items.length > 0) {
                const latest = data.items[data.items.length - 1];
                setSelectedMapId(latest.id);
                setSelectedMappingId(latest.mapping_id);
                if (latest.image_url) {
                  setMapImageUrl(
                    latest.image_url.startsWith("/static/")
                      ? `${process.env.NEXT_PUBLIC_API_URL}${latest.image_url}`
                      : `${process.env.NEXT_PUBLIC_API_URL}/api/map/proxy-image?url=${encodeURIComponent(latest.image_url)}`
                  );
                }
              }
            })
            .catch(() => {});
        }
      })
      .catch(() => showAlert({ title: "알림", message: "맵 데이터 저장에 실패했습니다.", errorCode: "MAP-006", errorType: "map", source: "맵 관리 > 맵 저장", description: "MapPage — 맵 저장 실패" }));
  }, [selectedMapId, pois, lines, polygons, svgToWorld]);
  const handleSync = () => {
    if (!selectedMappingId) {
      showInfo("안내", "동기화할 맵을 먼저 선택해 주세요.");
      return;
    }
    setSyncModalOpen(true);
  };
  const handleRelocalize = () => setRelocalizeModalOpen(true);

  // 현재 선택된 영역을 모니터링 메인의 default 로 적용
  const handleApply = async () => {
    if (!selectedArea) {
      showInfo("안내", "적용할 영역을 먼저 선택해 주세요.");
      return;
    }
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/map/default-area/${selectedArea}`,
        { method: "POST" }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({} as any));
        showAlert({
          title: "적용 실패",
          message: data?.detail || `HTTP ${res.status}`,
        });
        return;
      }
      const data = await res.json();
      // 다른 페이지(모니터링)와도 동일하게 동기화되도록 localStorage 도 갱신
      setStoredBusiness(selectedBusiness);
      setStoredArea(selectedArea);
      const areaName = areas.find((a) => String(a.area_id) === selectedArea)?.name ?? "";
      showAlert({
        title: "적용 완료",
        message: `'${areaName}' 영역의 맵('${data.map_name ?? ""}')이 모니터링 메인 기본 맵으로 적용되었습니다.`,
      });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "적용 실패";
      showAlert({ title: "오류", message: msg });
    }
  };

  const handleCreate = () => console.log("Create");
  const handleDelete = () => {
    if (!selectedArea) {
      showInfo("안내", "삭제할 영역을 선택해주세요.");
      return;
    }
    setDeleteConfirmOpen(true);
  };
  const handleDeleteConfirm = async () => {
    setDeleteConfirmOpen(false);
    try {
      const deletedIdx = areas.findIndex((a) => String(a.area_id) === selectedArea);
      await apiFetch(`/api/map/areas/${selectedArea}`, { method: "DELETE" });
      if (selectedBusiness) {
        const res = await apiFetch<{ items: AreaItem[] }>(`/api/map/businesses/${selectedBusiness}/areas`);
        setAreas(res.items);
        const nextArea = res.items[deletedIdx] ?? res.items[deletedIdx - 1];
        setSelectedArea(nextArea ? String(nextArea.area_id) : "");
      } else {
        setAreas([]);
        setSelectedArea("");
      }
      setAreaMaps([]);
      setSelectedMapId(null);
    } catch {
      showAlert({ title: "알림", message: "영역 삭제에 실패했습니다.", errorCode: "MAP-015", errorType: "map", source: "맵 관리 > 영역 삭제" });
    }
  };

  const handleStartMapping = () => {
    if (!connectedRobot) {
      showInfo("안내", "먼저 로봇을 연결해 주세요.");
      return;
    }
    setMappingSetupOpen(true);
  };
  const handleMappingSetupConfirm = (businessId: number, areaId: string, areaName: string) => {
    setMappingBusinessId(businessId);
    setMappingAreaId(areaId);
    setMappingAreaName(areaName);
    setMappingSetupOpen(false);
    setMappingModalOpen(true);
  };
  const handleMappingComplete = useCallback(async () => {
    try {
      // 사업장 목록 갱신
      const bizData = await apiFetch<{ total: number; items: BusinessItem[] }>("/api/map/businesses");
      setBusinesses(bizData.items);

      // 영역 목록 갱신
      if (selectedBusiness) {
        const areaData = await apiFetch<{ total: number; items: AreaItem[] }>(
          `/api/map/businesses/${selectedBusiness}/areas`
        );
        setAreas(areaData.items);

        // 방금 매핑한 영역이 있으면 우선 선택, 없으면 첫 번째 영역
        const targetAreaId = mappingAreaId && areaData.items.some((a) => String(a.area_id) === mappingAreaId)
          ? mappingAreaId
          : (!selectedArea && areaData.items.length > 0 ? String(areaData.items[0].area_id) : "");
        if (targetAreaId && targetAreaId !== selectedArea) {
          setSelectedArea(targetAreaId);
          return; // selectedArea 변경 시 useEffect가 맵 로드 처리
        }
      }

      // 현재 영역의 최신 맵 로드
      if (selectedArea) {
        const mapData = await apiFetch<{ total: number; items: MapItem[] }>(
          `/api/map/areas/${selectedArea}/maps`
        );
        setAreaMaps(mapData.items);

        if (mapData.items.length > 0) {
          const map = mapData.items[0];
          setSelectedMapId(map.id);
          setSelectedMappingId(map.mapping_id);
          const imgUrl = map.image_url;
          if (imgUrl) {
            setMapImageUrl(
              imgUrl.startsWith("/static/")
                ? `${process.env.NEXT_PUBLIC_API_URL}${imgUrl}`
                : `${process.env.NEXT_PUBLIC_API_URL}/api/map/proxy-image?url=${encodeURIComponent(imgUrl)}`
            );
          }
          setMapMeta({
            grid_origin_x: map.grid_origin_x,
            grid_origin_y: map.grid_origin_y,
            grid_resolution: map.grid_resolution,
          });

          // POI·라인 로드
          const elems = await apiFetch<{ pois: any[]; lines: any[]; polygons?: any[] }>(
            `/api/map/maps/${map.id}/elements`
          );
          setPois(elems.pois.map((p: any) => ({
            id: p.id, name: p.name, x: p.x, y: p.y, type: p.poi_type || p.type || "waypoint",
            phoneNumber: p.phone_number || "", angle: p.angle ?? null,
            loadType: p.load_type || "normal", robotSns: p.robot_sns ? JSON.parse(p.robot_sns) : [],
            dockingRadius: p.docking_radius ?? null,
            rackSize: p.rackSize ?? p.rack_size ?? undefined,
          })));
          setLines(elems.lines?.map((l: any) => ({
            id: l.id, fromId: l.from_poi_id, toId: l.to_poi_id,
            direction: l.direction || "forward", lineType: l.line_type || "straight",
            controlPoints: l.control_points ? JSON.parse(l.control_points) : [],
            areaName: l.area_name || "",
          })) || []);
          setPolygons(elems.polygons?.map((pg: any) => ({
            id: pg.id, name: pg.name || "", shapeType: pg.shape_type || "polygon",
            points: pg.points_json ? JSON.parse(pg.points_json) : [],
          })) || []);
        }
      }
    } catch (err) {
      console.error("[맵핑 완료 후 갱신 실패]", err);
    }
  }, [selectedBusiness, selectedArea]);
  const handleRemoteImage = () => console.log("Remote Image");
  const handleRemoteControl = () => console.log("Remote Control");

  return (
    <>
      <div className="app-shell">
      <TopBar
        dateTime={currentDateTime}
        onToggleNav={() => setNavCollapsed((v) => !v)}
        navExpanded={!navCollapsed}
      />
      <div className="shell-body">
        <SideNav
          items={defaultNavItems}
          collapsed={navCollapsed}
          onClose={() => setNavCollapsed(true)}
          onItemSelect={() => setNavCollapsed(true)}
        />
        <main className="main-content">
          <div className="map-workspace">
            {/* Map-specific top bar */}
            <MapTopBar
              connectedRobot={connectedRobot}
              onConnectClick={() => setConnectModalOpen(true)}
              businesses={businesses}
              selectedBusiness={selectedBusiness}
              onBusinessChange={setSelectedBusiness}
              areas={areas}
              selectedArea={selectedArea}
              onAreaChange={setSelectedArea}
              onSave={handleSave}
              onSync={handleSync}
              onApply={handleApply}
              onRelocalize={handleRelocalize}
              onDelete={handleDelete}
              syncDisabled={!selectedMapId || !selectedMappingId}
              applyDisabled={!selectedArea || !selectedMapId}
              onBusinessCreated={(id, name) => {
                setBusinesses((prev) => [...prev, { business_id: id, name }]);
                setSelectedBusiness(String(id));
              }}
              onAreaUpdated={() => {
                if (selectedBusiness) {
                  apiFetch<{ total: number; items: AreaItem[] }>(
                    `/api/map/businesses/${selectedBusiness}/areas`
                  ).then((data) => setAreas(data.items)).catch(() => {});
                }
              }}
            />

            {/* Map Canvas Area */}
            <div ref={canvasWrapRef} style={{ position: "relative", flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <MapCanvas
                pois={pois}
                lines={lines}
                polygons={polygons}
                vwTempPoints={vwTempPoints}
                activeTool={activeTool}
                selectedPOI={selectedPOI}
                lineStartPOI={lineStartPOI}
                zoom={zoom}
                offset={offset}
                rotation={rotation}
                mapImageUrl={mapImageUrl}
                robotPose={robotPose}
                mapMeta={mapMeta}
                onCanvasClick={handleCanvasClick}
                onPOIClick={handlePOIClick}
                onLineClick={handleLineClick}
                onPolygonClick={handlePolygonClick}
                onZoomChange={setZoom}
                onOffsetChange={setOffset}
                onImageLoad={(w, h) => setMapImageSize({ w, h })}
              />

              {/* Toolbar: 단일 가로 줄 — 되돌리기 + 모드 도구 + 충전소 + 현위치 */}
              <MapToolbarTop
                onUndo={handleUndo}
                onFullscreen={handleFullscreen}
                isFullscreen={isFullscreen}
                activeTool={activeTool}
                onToolChange={handleToolChange}
                onChargingPile={() => handleToolChange("chargingPile")}
                onCurrentPos={() => handleToolChange("currentPos")}
              />

              {/* Floating Panel: Right */}
              <MapFloatingPanel
                open={floatingPanelOpen}
                onToggle={() => setFloatingPanelOpen((v) => !v)}
                onStartMapping={handleStartMapping}
                onClearMap={handleClearMap}
              />

              {/* Bottom Left: Zoom, Rotate, Reset */}
              <div className="map-bottom-left">
                <button className="map-bottom-left__btn" onClick={handleZoomIn} title="확대">
                  <span className="map-bottom-left__icon">+</span>
                  <span className="map-bottom-left__label">확대</span>
                </button>
                <button className="map-bottom-left__btn" onClick={handleZoomOut} title="축소">
                  <span className="map-bottom-left__icon">−</span>
                  <span className="map-bottom-left__label">축소</span>
                </button>
                <div className="map-bottom-left__divider" />
                <button className="map-bottom-left__btn" onClick={handleRotateLeft} title="좌회전">
                  <span className="map-bottom-left__icon">↺</span>
                  <span className="map-bottom-left__label">좌회전</span>
                </button>
                <button className="map-bottom-left__btn" onClick={handleRotateRight} title="우회전">
                  <span className="map-bottom-left__icon">↻</span>
                  <span className="map-bottom-left__label">우회전</span>
                </button>
                <div className="map-bottom-left__divider" />
                <button className="map-bottom-left__btn" onClick={handleResetBearing} title="초기화">
                  <span className="map-bottom-left__icon">⊙</span>
                  <span className="map-bottom-left__label">초기화</span>
                </button>
              </div>

              {/* POI Edit Popup */}
              {editingPOI && (
                <POIEditPopup
                  poi={editingPOI}
                  onUpdate={handlePOIUpdate}
                  onDelete={handlePOIDelete}
                  getNextNameForType={(t) => nextPoiName(pois, t)}
                  onClose={() => {
                    if (pendingPOIId) {
                      setPois((prev) => prev.filter((p) => p.id !== pendingPOIId));
                      setLines((prev) => prev.filter((l) => l.fromId !== pendingPOIId && l.toId !== pendingPOIId));
                      setPendingPOIId(null);
                    }
                    setEditingPOI(null);
                    setSelectedPOI(null);
                  }}
                />
              )}

              {/* Line Edit Popup */}
              {editingLine && (
                <LineEditPopup
                  line={editingLine}
                  fromPoiName={pois.find((p) => p.id === editingLine.fromId)?.name ?? "알 수 없음"}
                  toPoiName={pois.find((p) => p.id === editingLine.toId)?.name ?? "알 수 없음"}
                  onUpdate={handleLineUpdate}
                  onDelete={handleLineDelete}
                  onClose={() => setEditingLine(null)}
                />
              )}

              {/* Line Direction Popup */}
              {lineDirectionPopup && (
                <LineDirectionPopup
                  position={lineDirectionPopup.position}
                  onSelect={handleLineDirectionSelect}
                  onCancel={() => {
                    setLineDirectionPopup(null);
                    setLineStartPOI(null);
                  }}
                />
              )}
            </div>
          </div>

          {/* Robot Connect Modal */}
          <RobotConnectModal
            open={connectModalOpen}
            onClose={() => setConnectModalOpen(false)}
            onConnect={handleRobotConnect}
          />

          {/* Mapping Setup Modal (Business/Area selection) */}
          <MappingSetupModal
            open={mappingSetupOpen}
            businesses={businesses}
            onClose={() => setMappingSetupOpen(false)}
            onConfirm={handleMappingSetupConfirm}
          />

          {/* Mapping Modal (real-time mapping UI) */}
          <MappingModal
            open={mappingModalOpen}
            businessId={mappingBusinessId}
            areaId={mappingAreaId}
            areaName={mappingAreaName}
            connectedRobot={connectedRobot}
            onClose={() => setMappingModalOpen(false)}
            onMappingComplete={handleMappingComplete}
          />

          {/* Map Sync Modal (맵을 복수 로봇에 로드) */}
          {selectedMappingId && selectedMapId && (
            <MapSyncModal
              open={syncModalOpen}
              onClose={() => setSyncModalOpen(false)}
              mappingId={selectedMappingId}
              mapId={selectedMapId}
              areaName={areas.find((a) => String(a.area_id) === selectedArea)?.name ?? ""}
            />
          )}

          {/* 위치 재조정 Modal */}
          <MapRelocalizeModal
            open={relocalizeModalOpen}
            onClose={() => setRelocalizeModalOpen(false)}
          />

          {/* 영역 삭제 확인 Modal */}
          <ConfirmModal
            open={deleteConfirmOpen}
            title="영역 삭제"
            message={`영역 "${areas.find((a) => String(a.area_id) === selectedArea)?.name ?? ""}"을(를) 삭제하시겠습니까?`}
            onConfirm={handleDeleteConfirm}
            onCancel={() => setDeleteConfirmOpen(false)}
          />

          {/* POI 삭제 확인 Modal */}
          <ConfirmModal
            open={!!deletePOITarget}
            title="POI 삭제"
            message={`POI "${deletePOITarget?.name ?? ""}"을(를) 삭제하시겠습니까?\n연결된 라인이 있으면 함께 삭제될 수 있습니다.`}
            onConfirm={() => {
              if (deletePOITarget) {
                pushHistory();
                setPois((prev) => prev.filter((p) => p.id !== deletePOITarget.id));
                setLines((prev) => prev.filter((l) => l.fromId !== deletePOITarget.id && l.toId !== deletePOITarget.id));
                setSelectedPOI(null);
                setEditingPOI(null);
              }
              setDeletePOITarget(null);
            }}
            onCancel={() => setDeletePOITarget(null)}
          />

          {/* 맵 초기화 확인 Modal */}
          <ConfirmModal
            open={clearMapConfirmOpen}
            title="맵 초기화"
            message="화면에 표시된 모든 POI와 라인을 초기화하시겠습니까?"
            onConfirm={handleClearMapConfirm}
            onCancel={() => setClearMapConfirmOpen(false)}
          />
        </main>
      </div>
    </div>
    </>
  );
}
