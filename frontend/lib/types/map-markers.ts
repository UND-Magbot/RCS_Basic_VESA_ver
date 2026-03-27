export type MapPixelCoord = {
  x: number;
  y: number;
};

export type PoiMarkerData = {
  id: string;
  label: string;
  position: MapPixelCoord;
  type: "workstation" | "charging" | "pickup" | "dropoff" | "jack";
  renderKind?: "circle" | "triangle";
  angle?: number;
  dockingRadius?: number;
  /** 잭킹 POI: 랙 크기 (픽셀 단위, grid_resolution 적용 후) */
  rackWidthPx?: number;
  rackDepthPx?: number;
};

export type WaypointMarkerData = {
  id: string;
  label: string;
  position: MapPixelCoord;
};

export type RobotMarkerData = {
  robotId: string;
  robotName: string;
  position: MapPixelCoord;
  yaw: number;
  status: "idle" | "running" | "charging" | "error" | "warning" | "disable";
  power: "online" | "offline";
  collisionState?: "none" | "near_miss" | "collision";
};

export type RouteSegment = {
  id: string;
  from: MapPixelCoord;
  to: MapPixelCoord;
  direction: "forward" | "backward" | "bidirectional";
  lineType: "straight" | "curve" | "firewall";
  controlPoints?: MapPixelCoord[];
};

export type VirtualWallData = {
  id: string;
  start: MapPixelCoord;
  end: MapPixelCoord;
};

export type MonitoringMapProps = {
  mapSrc: string;
  pois: PoiMarkerData[];
  waypoints: WaypointMarkerData[];
  routeWaypoints: WaypointMarkerData[];
  routeSegments?: RouteSegment[];
  robots: RobotMarkerData[];
  virtualWalls: VirtualWallData[];
  showMapBackground: boolean;
  showNavigationLine: boolean;
  showDirectionArrows: boolean;
  showVirtualWalls: boolean;
  showNavigationNodes: boolean;
  showPoiMarkers: boolean;
};
