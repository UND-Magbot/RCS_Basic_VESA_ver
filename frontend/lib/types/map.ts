export type MapTool = "select" | "point" | "jackPoint" | "line" | "curveLine" | "polygon" | "del" | "chargingPile" | "currentPos" | "currentPosJack" | "firewall" | "virtualwall";

export type POIType = "waypoint" | "standby" | "charging" | "firewall" | "jack";

export type RackSize = "S600" | "S300";

export type LoadType = "normal" | "heavy";

export type LineDirection = "forward" | "backward" | "bidirectional";

export type POI = {
  id: string;
  x: number;
  y: number;
  name: string;
  type: POIType;
  phoneNumber?: string;
  angle?: number;
  loadType?: LoadType;
  robotSns?: string[];
  address?: string;
  dockingRadius?: number;
  rackSize?: RackSize;
};

export type PathLine = {
  id: string;
  fromId: string;
  toId: string;
  direction: LineDirection;
  lineType: "straight" | "curve" | "firewall";
  controlPoints?: { x: number; y: number }[];
};

export type PolygonShape = {
  id: string;
  points: { x: number; y: number }[];
  name: string;
  shapeType?: "polygon" | "firewall";
};

export type ConnectedRobot = {
  sn: string;
  name: string;
  ip: string;
} | null;

export type RobotPose = {
  pos: [number, number];
  ori: number;
} | null;

export type MapMeta = {
  grid_origin_x: number;
  grid_origin_y: number;
  grid_resolution: number;
} | null;

export type MapCanvasProps = {
  pois: POI[];
  lines: PathLine[];
  polygons: PolygonShape[];
  activeTool: MapTool;
  selectedPOI: string | null;
  lineStartPOI: string | null;
  zoom: number;
  offset: { x: number; y: number };
  rotation: number;
  mapImageUrl: string | null;
  robotPose?: RobotPose;
  mapMeta?: MapMeta;
  onCanvasClick: (x: number, y: number) => void;
  onPOIClick: (id: string) => void;
  onLineClick: (id: string) => void;
  onPolygonClick: (id: string) => void;
  onZoomChange: (zoom: number) => void;
  onOffsetChange: (offset: { x: number; y: number }) => void;
  onImageLoad?: (w: number, h: number) => void;
  vwTempPoints?: { x: number; y: number }[];
};

export type MapToolbarTopProps = {
  onUndo: () => void;
  onFullscreen: () => void;
  isFullscreen: boolean;
  /** 활성 도구 모드 (포인트/작업 포인트/가상벽/삭제 등) */
  activeTool: MapTool;
  /** 모드 전환 핸들러 */
  onToolChange: (tool: MapTool) => void;
  /** 즉시 실행 — 로봇 현재 위치에 충전소 POI 자동 생성 */
  onChargingPile: () => void;
  /** 즉시 실행 — 로봇 현재 위치에 일반 POI 자동 생성 */
  onCurrentPos: () => void;
};

export type MapToolbarLeftProps = {
  activeTool: MapTool;
  onToolChange: (tool: MapTool) => void;
};

export type MapFloatingPanelProps = {
  open: boolean;
  onToggle: () => void;
  onStartMapping: () => void;
  onClearMap: () => void;
};

export type RobotConnectModalProps = {
  open: boolean;
  onClose: () => void;
  onConnect: (sn: string, name: string, ip: string) => void;
};

export type POIEditPopupProps = {
  poi: POI;
  onUpdate: (id: string, data: Partial<POI>) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
  /** 유형이 바뀌면 자동으로 그 유형의 다음 이름(C1/J2/R3/W4) 으로 변경. */
  getNextNameForType?: (type: POIType) => string;
};

export type LineDirectionPopupProps = {
  position: { x: number; y: number };
  onSelect: (direction: LineDirection) => void;
  onCancel: () => void;
};

export type LineEditPopupProps = {
  line: PathLine;
  fromPoiName: string;
  toPoiName: string;
  onUpdate: (id: string, data: Partial<PathLine>) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
};

export type MappingSetupModalProps = {
  open: boolean;
  businesses: { business_id: number; name: string }[];
  onClose: () => void;
  onConfirm: (businessId: number, areaId: string, areaName: string) => void;
};

export type MappingStatus = "idle" | "mapping" | "finished" | "cancelled";

export type MappingModalProps = {
  open: boolean;
  businessId: number | null;
  areaId: string;
  areaName: string;
  connectedRobot: ConnectedRobot;
  onClose: () => void;
  onMappingComplete?: () => void;
};

export type MapSyncModalProps = {
  open: boolean;
  onClose: () => void;
  mappingId: number;
  mapId: number;
  areaName: string;
  onSyncComplete?: () => void;
};
