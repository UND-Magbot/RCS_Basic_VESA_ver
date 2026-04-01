"use client";

import { useEffect, useRef } from "react";
import type {
  PoiMarkerData,
  WaypointMarkerData,
  RobotMarkerData,
  RouteSegment,
  VirtualWallData,
} from "@/lib/types/map-markers";
import { useCanvasLoop } from "@/lib/hooks/useCanvasLoop";
import "./MonitoringMapCanvas.css";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type Props = {
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
  robotTargets?: Map<string, { x: number; y: number }>;
};

type View = { scale: number; offsetX: number; offsetY: number };

type MapBounds = {
  offsetX: number;
  offsetY: number;
  drawWidth: number;
  drawHeight: number;
  natW: number;
  natH: number;
};

type DrawData = Omit<Props, "mapSrc">;

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function clamp(v: number, min: number, max: number) {
  return Math.min(max, Math.max(min, v));
}

function w2s(
  p: { x: number; y: number },
  v: View
) {
  return { x: p.x * v.scale + v.offsetX, y: p.y * v.scale + v.offsetY };
}

function s2w(
  p: { x: number; y: number },
  v: View
) {
  return { x: (p.x - v.offsetX) / v.scale, y: (p.y - v.offsetY) / v.scale };
}

/** Map image pixel coordinate → canvas screen coordinate */
function mapToCanvas(
  mapX: number,
  mapY: number,
  bounds: MapBounds
): { cx: number; cy: number } {
  return {
    cx: bounds.offsetX + (mapX / bounds.natW) * bounds.drawWidth,
    cy: bounds.offsetY + (mapY / bounds.natH) * bounds.drawHeight,
  };
}

function isLoopRoute(points: WaypointMarkerData[]): boolean {
  if (points.length < 2) return false;
  const first = points[0].position;
  const last = points[points.length - 1].position;
  return Math.hypot(first.x - last.x, first.y - last.y) < 5;
}

function resetShadow(ctx: CanvasRenderingContext2D) {
  ctx.shadowColor = "transparent";
  ctx.shadowBlur = 0;
  ctx.shadowOffsetX = 0;
  ctx.shadowOffsetY = 0;
}

/** 이미지 가장자리에서 연결된 배경색 픽셀을 투명 처리 (edge flood-fill) */
function removeOutsideBackground(
  img: HTMLImageElement,
  tolerance = 30
): HTMLCanvasElement {
  const w = img.naturalWidth;
  const h = img.naturalHeight;

  const offscreen = document.createElement("canvas");
  offscreen.width = w;
  offscreen.height = h;
  const octx = offscreen.getContext("2d")!;
  octx.drawImage(img, 0, 0);

  const imageData = octx.getImageData(0, 0, w, h);
  const { data } = imageData;

  // 4개 꼭짓점 픽셀 평균으로 배경색 결정
  const corners = [
    0,
    (w - 1) * 4,
    (h - 1) * w * 4,
    ((h - 1) * w + (w - 1)) * 4,
  ];
  let bgR = 0;
  let bgG = 0;
  let bgB = 0;
  for (const idx of corners) {
    bgR += data[idx];
    bgG += data[idx + 1];
    bgB += data[idx + 2];
  }
  bgR = Math.round(bgR / 4);
  bgG = Math.round(bgG / 4);
  bgB = Math.round(bgB / 4);

  const tolSq = tolerance * tolerance;
  const matches = (i: number) => {
    const dr = data[i] - bgR;
    const dg = data[i + 1] - bgG;
    const db = data[i + 2] - bgB;
    return dr * dr + dg * dg + db * db < tolSq;
  };

  // BFS flood fill — 가장자리에서 시작
  const visited = new Uint8Array(w * h);
  const queue: number[] = [];

  for (let x = 0; x < w; x++) {
    if (matches(x * 4)) { queue.push(x); visited[x] = 1; }
    const bi = (h - 1) * w + x;
    if (matches(bi * 4)) { queue.push(bi); visited[bi] = 1; }
  }
  for (let y = 1; y < h - 1; y++) {
    const li = y * w;
    if (matches(li * 4)) { queue.push(li); visited[li] = 1; }
    const ri = y * w + (w - 1);
    if (matches(ri * 4)) { queue.push(ri); visited[ri] = 1; }
  }

  let head = 0;
  while (head < queue.length) {
    const idx = queue[head++];
    const px = idx % w;
    const py = (idx - px) / w;

    const neighbors = [
      py > 0 ? idx - w : -1,
      py < h - 1 ? idx + w : -1,
      px > 0 ? idx - 1 : -1,
      px < w - 1 ? idx + 1 : -1,
    ];

    for (const ni of neighbors) {
      if (ni < 0 || visited[ni]) continue;
      if (matches(ni * 4)) {
        visited[ni] = 1;
        queue.push(ni);
      }
    }
  }

  for (let i = 0; i < visited.length; i++) {
    const p = i * 4;
    if (visited[i]) {
      data[p + 3] = 0;
    } else {
      // 내부 바닥: 벽(어두운 픽셀)은 유지, 나머지는 채움 (#1e3045)
      const brightness = (data[p] + data[p + 1] + data[p + 2]) / 3;
      if (brightness > 50) {
        data[p] = 30;
        data[p + 1] = 48;
        data[p + 2] = 69;
      }
    }
  }

  // 경계선 스무딩 — 모폴로지 closing (dilate → erode) 으로 노이즈 제거
  const smoothed = new Uint8Array(visited);
  const SMOOTH_R = 3;
  for (let pass = 0; pass < SMOOTH_R; pass++) {
    const prev = new Uint8Array(smoothed);
    for (let i = 0; i < prev.length; i++) {
      if (prev[i]) continue;
      const px = i % w;
      const py = (i - px) / w;
      if (
        (py > 0 && prev[i - w]) ||
        (py < h - 1 && prev[i + w]) ||
        (px > 0 && prev[i - 1]) ||
        (px < w - 1 && prev[i + 1])
      ) {
        smoothed[i] = 1;
      }
    }
  }
  for (let pass = 0; pass < SMOOTH_R; pass++) {
    const prev = new Uint8Array(smoothed);
    for (let i = 0; i < prev.length; i++) {
      if (!prev[i]) continue;
      const px = i % w;
      const py = (i - px) / w;
      if (
        (py > 0 && !prev[i - w]) ||
        (py < h - 1 && !prev[i + w]) ||
        (px > 0 && !prev[i - 1]) ||
        (px < w - 1 && !prev[i + 1])
      ) {
        smoothed[i] = 0;
      }
    }
  }

  // 경계선 탐지 — smoothed 기준
  const border = new Uint8Array(w * h);
  for (let i = 0; i < smoothed.length; i++) {
    if (smoothed[i]) continue;
    const px = i % w;
    const py = (i - px) / w;
    if (
      (py > 0 && smoothed[i - w]) ||
      (py < h - 1 && smoothed[i + w]) ||
      (px > 0 && smoothed[i - 1]) ||
      (px < w - 1 && smoothed[i + 1])
    ) {
      border[i] = 1;
    }
  }

  // 3px 두께 — 경계 2차 확장
  const borderFinal = new Uint8Array(border);
  for (let expand = 0; expand < 2; expand++) {
    const prev = new Uint8Array(borderFinal);
    for (let i = 0; i < prev.length; i++) {
      if (!prev[i]) continue;
      const px = i % w;
      const py = (i - px) / w;
      if (py > 0 && !borderFinal[i - w] && !smoothed[i - w]) borderFinal[i - w] = 1;
      if (py < h - 1 && !borderFinal[i + w] && !smoothed[i + w]) borderFinal[i + w] = 1;
      if (px > 0 && !borderFinal[i - 1] && !smoothed[i - 1]) borderFinal[i - 1] = 1;
      if (px < w - 1 && !borderFinal[i + 1] && !smoothed[i + 1]) borderFinal[i + 1] = 1;
    }
  }

  // 경계 픽셀 색상 적용 (#3a6a9a)
  for (let i = 0; i < borderFinal.length; i++) {
    if (!borderFinal[i]) continue;
    const p = i * 4;
    data[p] = 58;
    data[p + 1] = 106;
    data[p + 2] = 154;
    data[p + 3] = 255;
  }

  octx.putImageData(imageData, 0, 0);
  return offscreen;
}

/* ------------------------------------------------------------------ */
/*  Draw: Map Background                                               */
/* ------------------------------------------------------------------ */

function computeMapBounds(
  img: HTMLImageElement,
  logicalW: number,
  logicalH: number
): MapBounds {
  const natW = img.naturalWidth;
  const natH = img.naturalHeight;
  const imgAr = natW / natH;
  const canvasAr = logicalW / logicalH;

  let drawWidth: number;
  let drawHeight: number;
  if (imgAr > canvasAr) {
    drawWidth = logicalW;
    drawHeight = logicalW / imgAr;
  } else {
    drawHeight = logicalH;
    drawWidth = logicalH * imgAr;
  }

  return {
    offsetX: (logicalW - drawWidth) / 2,
    offsetY: (logicalH - drawHeight) / 2,
    drawWidth,
    drawHeight,
    natW,
    natH,
  };
}

function drawMapImage(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  bounds: MapBounds
) {
  ctx.drawImage(
    img,
    bounds.offsetX,
    bounds.offsetY,
    bounds.drawWidth,
    bounds.drawHeight
  );
}

/* ------------------------------------------------------------------ */
/*  Draw: Routes                                                       */
/* ------------------------------------------------------------------ */

function drawPolylineRoute(
  ctx: CanvasRenderingContext2D,
  points: WaypointMarkerData[],
  bounds: MapBounds,
  strokeStyle: string,
  lineWidth: number,
  shadowColor?: string,
  shadowBlur?: number
) {
  if (points.length < 2) return;

  const scale = bounds.drawWidth / bounds.natW;

  ctx.beginPath();
  const first = mapToCanvas(points[0].position.x, points[0].position.y, bounds);
  ctx.moveTo(first.cx, first.cy);
  for (let i = 1; i < points.length; i++) {
    const p = mapToCanvas(points[i].position.x, points[i].position.y, bounds);
    ctx.lineTo(p.cx, p.cy);
  }

  ctx.strokeStyle = strokeStyle;
  ctx.lineWidth = lineWidth * scale;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (shadowColor && shadowBlur) {
    ctx.shadowColor = shadowColor;
    ctx.shadowBlur = shadowBlur;
  }
  ctx.stroke();
  resetShadow(ctx);
}

function drawRouteSegment(
  ctx: CanvasRenderingContext2D,
  seg: RouteSegment,
  bounds: MapBounds,
  strokeStyle: string,
  lineWidth: number,
  shadowColor?: string,
  shadowBlur?: number
) {
  const scale = bounds.drawWidth / bounds.natW;
  const from = mapToCanvas(seg.from.x, seg.from.y, bounds);
  const to = mapToCanvas(seg.to.x, seg.to.y, bounds);

  ctx.beginPath();
  ctx.moveTo(from.cx, from.cy);

  if (seg.lineType === "curve" && seg.controlPoints && seg.controlPoints.length > 0) {
    if (seg.controlPoints.length === 1) {
      const cp = mapToCanvas(seg.controlPoints[0].x, seg.controlPoints[0].y, bounds);
      ctx.quadraticCurveTo(cp.cx, cp.cy, to.cx, to.cy);
    } else {
      const cp1 = mapToCanvas(seg.controlPoints[0].x, seg.controlPoints[0].y, bounds);
      const cp2 = mapToCanvas(seg.controlPoints[1].x, seg.controlPoints[1].y, bounds);
      ctx.bezierCurveTo(cp1.cx, cp1.cy, cp2.cx, cp2.cy, to.cx, to.cy);
    }
  } else {
    ctx.lineTo(to.cx, to.cy);
  }

  ctx.strokeStyle = strokeStyle;
  ctx.lineWidth = lineWidth * scale;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (shadowColor && shadowBlur) {
    ctx.shadowColor = shadowColor;
    ctx.shadowBlur = shadowBlur;
  }
  ctx.stroke();
  resetShadow(ctx);
}

function drawFirewallSegment(
  ctx: CanvasRenderingContext2D,
  seg: RouteSegment,
  bounds: MapBounds
) {
  const scale = bounds.drawWidth / bounds.natW;
  const from = mapToCanvas(seg.from.x, seg.from.y, bounds);
  const to = mapToCanvas(seg.to.x, seg.to.y, bounds);
  ctx.beginPath();
  ctx.moveTo(from.cx, from.cy);
  ctx.lineTo(to.cx, to.cy);
  ctx.strokeStyle = "rgb(255, 140, 0)";
  ctx.lineWidth = 2.5 * scale;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.stroke();
}

function drawRoutes(
  ctx: CanvasRenderingContext2D,
  data: DrawData,
  bounds: MapBounds
) {
  const segments = data.routeSegments ?? [];
  const ROUTE_WIDTH = 8;
  const ROUTE_ALPHA = 0.35;

  if (segments.length > 0) {
    const normalSegs = segments.filter((s) => s.lineType !== "firewall");
    const firewallSegs = segments.filter((s) => s.lineType === "firewall");

    // 일반 경로: 오프스크린 캔버스에 불투명으로 그린 뒤 한 번에 합성
    if (normalSegs.length > 0) {
      const offCanvas = document.createElement("canvas");
      offCanvas.width = ctx.canvas.width;
      offCanvas.height = ctx.canvas.height;
      const offCtx = offCanvas.getContext("2d");
      if (offCtx) {
        for (const seg of normalSegs) {
          drawRouteSegment(offCtx, seg, bounds, "rgb(25, 188, 126)", ROUTE_WIDTH);
        }
        ctx.save();
        ctx.globalAlpha = ROUTE_ALPHA;
        ctx.drawImage(offCanvas, 0, 0);
        ctx.restore();
      }
    }

    // 방화벽: 틱마크 배리어로 표시
    for (const seg of firewallSegs) {
      drawFirewallSegment(ctx, seg, bounds);
    }
  } else {
    // 기존 fallback: routeWaypoints 기반
    const routeSource = data.routeWaypoints.length > 0 ? data.routeWaypoints : data.waypoints;
    const staticWps = routeSource.filter((wp) => !wp.id.startsWith("alloc-"));
    drawPolylineRoute(ctx, staticWps, bounds, "rgba(25, 188, 126, 0.35)", ROUTE_WIDTH, "rgba(23, 160, 112, 0.2)", 2);
  }
}

/* ------------------------------------------------------------------ */
/*  Draw: Direction Arrows                                             */
/* ------------------------------------------------------------------ */

function drawArrow(
  ctx: CanvasRenderingContext2D,
  mx: number,
  my: number,
  angleRad: number,
  color: string,
  scale: number
) {
  ctx.save();
  ctx.translate(mx, my);
  ctx.rotate(angleRad);

  const s = scale;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5 * s;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  // Stem: -3 to 2
  ctx.beginPath();
  ctx.moveTo(-3 * s, 0);
  ctx.lineTo(2 * s, 0);
  ctx.stroke();

  // Arrowhead: 0,-0.7 → 2.5,0 → 0,0.7
  ctx.beginPath();
  ctx.moveTo(0, -0.7 * s);
  ctx.lineTo(2.5 * s, 0);
  ctx.lineTo(0, 0.7 * s);
  ctx.stroke();

  ctx.restore();
}

function drawSegmentArrows(
  ctx: CanvasRenderingContext2D,
  points: WaypointMarkerData[],
  color: string,
  bounds: MapBounds
) {
  if (points.length < 2) return;

  const scale = bounds.drawWidth / bounds.natW;
  const bidirectional = isLoopRoute(points);

  for (let i = 0; i < points.length - 1; i++) {
    const a = mapToCanvas(points[i].position.x, points[i].position.y, bounds);
    const b = mapToCanvas(points[i + 1].position.x, points[i + 1].position.y, bounds);

    const mx = (a.cx + b.cx) / 2;
    const my = (a.cy + b.cy) / 2;
    const angleRad = Math.atan2(b.cy - a.cy, b.cx - a.cx);

    if (bidirectional) {
      const offsetPx = 8 * scale;
      const paraX = Math.cos(angleRad) * offsetPx;
      const paraY = Math.sin(angleRad) * offsetPx;

      drawArrow(ctx, mx + paraX, my + paraY, angleRad, color, scale);
      drawArrow(ctx, mx - paraX, my - paraY, angleRad + Math.PI, color, scale);
    } else {
      drawArrow(ctx, mx, my, angleRad, color, scale);
    }
  }
}

function drawSegmentDirectionArrow(
  ctx: CanvasRenderingContext2D,
  seg: RouteSegment,
  color: string,
  bounds: MapBounds
) {
  const scale = bounds.drawWidth / bounds.natW;
  const from = mapToCanvas(seg.from.x, seg.from.y, bounds);
  const to = mapToCanvas(seg.to.x, seg.to.y, bounds);

  const mx = (from.cx + to.cx) / 2;
  const my = (from.cy + to.cy) / 2;
  const angleRad = Math.atan2(to.cy - from.cy, to.cx - from.cx);

  if (seg.direction === "bidirectional") {
    const offsetPx = 8 * scale;
    const paraX = Math.cos(angleRad) * offsetPx;
    const paraY = Math.sin(angleRad) * offsetPx;
    drawArrow(ctx, mx + paraX, my + paraY, angleRad, color, scale);
    drawArrow(ctx, mx - paraX, my - paraY, angleRad + Math.PI, color, scale);
  } else if (seg.direction === "backward") {
    drawArrow(ctx, mx, my, angleRad + Math.PI, color, scale);
  } else {
    drawArrow(ctx, mx, my, angleRad, color, scale);
  }
}

function drawDirectionArrows(
  ctx: CanvasRenderingContext2D,
  data: DrawData,
  bounds: MapBounds
) {
  const segments = data.routeSegments ?? [];

  if (segments.length > 0) {
    // RouteSegment 기반: per-segment direction 지원 (방화벽 제외)
    for (const seg of segments) {
      if (seg.lineType === "firewall") continue;
      drawSegmentDirectionArrow(ctx, seg, "rgba(25, 188, 126, 0.9)", bounds);
    }
  } else {
    // 기존 fallback
    const routeSource = data.routeWaypoints.length > 0 ? data.routeWaypoints : data.waypoints;
    const staticWps = routeSource.filter((wp) => !wp.id.startsWith("alloc-"));
    drawSegmentArrows(ctx, staticWps, "rgba(25, 188, 126, 0.9)", bounds);
  }
}

/* ------------------------------------------------------------------ */
/*  Draw: Virtual Walls                                                */
/* ------------------------------------------------------------------ */

function drawVirtualWalls(
  ctx: CanvasRenderingContext2D,
  walls: VirtualWallData[],
  bounds: MapBounds
) {
  const scale = bounds.drawWidth / bounds.natW;

  for (const wall of walls) {
    const start = mapToCanvas(wall.start.x, wall.start.y, bounds);
    const end = mapToCanvas(wall.end.x, wall.end.y, bounds);

    ctx.beginPath();
    ctx.moveTo(start.cx, start.cy);
    ctx.lineTo(end.cx, end.cy);
    ctx.strokeStyle = "rgba(255, 80, 80, 0.85)";
    ctx.lineWidth = 8 * scale;
    ctx.setLineDash([16 * scale, 8 * scale]);
    ctx.lineCap = "round";
    ctx.shadowColor = "rgba(255, 50, 50, 0.5)";
    ctx.shadowBlur = 4;
    ctx.stroke();
    ctx.setLineDash([]);
    resetShadow(ctx);
  }
}

/* ------------------------------------------------------------------ */
/*  Draw: Waypoint Markers                                             */
/* ------------------------------------------------------------------ */

function drawWaypoints(
  ctx: CanvasRenderingContext2D,
  waypoints: WaypointMarkerData[],
  bounds: MapBounds,
  viewScale: number
) {
  const counterScale = 1 / viewScale;

  for (const wp of waypoints) {
    const p = mapToCanvas(wp.position.x, wp.position.y, bounds);
    const isAllocated = wp.id.startsWith("alloc-");

    const radius = isAllocated ? 4.5 * counterScale : 6 * counterScale;

    ctx.beginPath();
    ctx.arc(p.cx, p.cy, radius, 0, Math.PI * 2);

    if (isAllocated) {
      ctx.fillStyle = "rgba(170, 237, 255, 0.88)";
      ctx.shadowColor = "rgba(170, 237, 255, 0.65)";
      ctx.shadowBlur = 5 * counterScale;
    } else {
      ctx.fillStyle = "rgba(142, 221, 255, 0.9)";
      ctx.shadowColor = "rgba(138, 216, 255, 0.8)";
      ctx.shadowBlur = 8 * counterScale;
    }

    ctx.fill();
    resetShadow(ctx);
  }
}

/* ------------------------------------------------------------------ */
/*  Draw: Label Helper                                                 */
/* ------------------------------------------------------------------ */

function drawMarkerLabel(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  text: string,
  color: string,
  counterScale: number,
  fontWeight: number = 600
) {
  const fontSize = Math.max(9, 11 * counterScale);
  ctx.font = `${fontWeight} ${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = color;
  ctx.shadowColor = "rgba(0, 0, 0, 0.85)";
  ctx.shadowBlur = 5;
  ctx.shadowOffsetY = 1;
  ctx.fillText(text, x, y);
  resetShadow(ctx);
}

/* ------------------------------------------------------------------ */
/*  Draw: POI Markers                                                  */
/* ------------------------------------------------------------------ */

function drawPoiCircle(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  label: string,
  counterScale: number
) {
  const outerR = 9 * counterScale;

  // Outer circle with white border
  ctx.beginPath();
  ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(35, 45, 55, 0.86)";
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.92)";
  ctx.lineWidth = 2 * counterScale;
  ctx.stroke();

  // Inner red dot
  ctx.beginPath();
  ctx.arc(cx, cy, 3 * counterScale, 0, Math.PI * 2);
  ctx.fillStyle = "#ff2b2b";
  ctx.shadowColor = "rgba(255, 74, 74, 0.65)";
  ctx.shadowBlur = 5 * counterScale;
  ctx.fill();
  resetShadow(ctx);

  // Label
  drawMarkerLabel(ctx, cx, cy + outerR + 4 * counterScale, label, "#ffffff", counterScale, 700);
}

function drawPoiBox(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  label: string,
  counterScale: number,
  angle?: number,
  rackWidthPx?: number,
  rackDepthPx?: number,
  color?: "purple" | "blue"
) {
  const w = rackWidthPx ?? 16 * counterScale;
  const d = rackDepthPx ?? 16 * counterScale;
  const isBlue = color === "blue";

  ctx.save();
  ctx.translate(cx, cy);
  if (angle != null) ctx.rotate(-angle + Math.PI / 2);

  // 박스 외곽
  ctx.fillStyle = isBlue ? "rgba(59, 130, 246, 0.25)" : "rgba(168, 85, 247, 0.25)";
  ctx.strokeStyle = isBlue ? "#3b82f6" : "#a855f7";
  ctx.lineWidth = 1.5 * counterScale;
  ctx.beginPath();
  ctx.rect(-w / 2, -d / 2, w, d);
  ctx.fill();
  ctx.stroke();

  // 4개 다리 (모서리)
  const legSize = 2 * counterScale;
  ctx.fillStyle = isBlue ? "#2563eb" : "#7c3aed";
  for (const [lx, ly] of [[-w/2, -d/2], [w/2 - legSize, -d/2], [w/2 - legSize, d/2 - legSize], [-w/2, d/2 - legSize]]) {
    ctx.fillRect(lx, ly, legSize, legSize);
  }

  // V자 쉐브론 방향 표시
  ctx.strokeStyle = "rgba(255, 255, 255, 0.7)";
  ctx.lineWidth = 1.5 * counterScale;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const cw = w * 0.35;
  const ch = d * 0.2;
  for (const offset of [-ch, ch * 0.4]) {
    ctx.beginPath();
    ctx.moveTo(-cw, offset + ch);
    ctx.lineTo(0, offset);
    ctx.lineTo(cw, offset + ch);
    ctx.stroke();
  }

  ctx.restore();
  const labelColor = isBlue ? "#93c5fd" : "#d8b4fe";
  drawMarkerLabel(ctx, cx, cy + d / 2 + 5 * counterScale, label, labelColor, counterScale, 700);
}

function drawPoiTriangle(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  label: string,
  counterScale: number
) {
  const halfW = 10 * counterScale;
  const h = 16 * counterScale;

  ctx.beginPath();
  ctx.moveTo(cx, cy - h * 0.5);
  ctx.lineTo(cx - halfW, cy + h * 0.5);
  ctx.lineTo(cx + halfW, cy + h * 0.5);
  ctx.closePath();
  ctx.fillStyle = "#1f6fff";
  ctx.shadowColor = "rgba(8, 35, 108, 0.45)";
  ctx.shadowOffsetY = 1;
  ctx.shadowBlur = 3;
  ctx.fill();
  resetShadow(ctx);

  // White inner dot
  ctx.beginPath();
  ctx.arc(cx, cy + 1 * counterScale, 3 * counterScale, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255, 255, 255, 0.8)";
  ctx.fill();

  // Label
  drawMarkerLabel(ctx, cx, cy + h * 0.5 + 4 * counterScale, label, "#ffffff", counterScale, 700);
}

function drawPoiAngleIndicator(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  angleDeg: number,
  counterScale: number
) {
  const angleRad = (angleDeg * Math.PI) / 180;
  const len = 18 * counterScale;
  const endX = cx + Math.cos(angleRad) * len;
  const endY = cy - Math.sin(angleRad) * len;

  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(endX, endY);
  ctx.strokeStyle = "rgba(255, 200, 60, 0.9)";
  ctx.lineWidth = 2 * counterScale;
  ctx.lineCap = "round";
  ctx.stroke();

  // arrowhead
  const headLen = 6 * counterScale;
  const a1 = angleRad + Math.PI + Math.PI / 6;
  const a2 = angleRad + Math.PI - Math.PI / 6;
  ctx.beginPath();
  ctx.moveTo(endX + Math.cos(a1) * headLen, endY - Math.sin(a1) * headLen);
  ctx.lineTo(endX, endY);
  ctx.lineTo(endX + Math.cos(a2) * headLen, endY - Math.sin(a2) * headLen);
  ctx.strokeStyle = "rgba(255, 200, 60, 0.9)";
  ctx.lineWidth = 2 * counterScale;
  ctx.stroke();
}

function drawDockingRadius(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  radiusMeters: number,
  bounds: MapBounds,
  counterScale: number
) {
  // dockingRadius는 미터 단위 → 픽셀 스케일 적용
  const pixelRadius = (radiusMeters / bounds.natW) * bounds.drawWidth;

  ctx.beginPath();
  ctx.arc(cx, cy, pixelRadius, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(100, 200, 255, 0.5)";
  ctx.lineWidth = 1.5 * counterScale;
  ctx.setLineDash([6 * counterScale, 4 * counterScale]);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.beginPath();
  ctx.arc(cx, cy, pixelRadius, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(100, 200, 255, 0.08)";
  ctx.fill();
}

function drawPois(
  ctx: CanvasRenderingContext2D,
  pois: PoiMarkerData[],
  bounds: MapBounds,
  viewScale: number
) {
  const counterScale = 1 / viewScale;

  for (const poi of pois) {
    const p = mapToCanvas(poi.position.x, poi.position.y, bounds);
    const isCharging = poi.type === "charging";
    const renderKind = poi.renderKind ?? (isCharging ? "circle" : "triangle");

    // dockingRadius 원 (POI 뒤에 먼저 그리기)
    if (poi.dockingRadius != null && poi.dockingRadius > 0) {
      drawDockingRadius(ctx, p.cx, p.cy, poi.dockingRadius, bounds, counterScale);
    }

    if (poi.type === "jack") {
      drawPoiBox(ctx, p.cx, p.cy, poi.label, counterScale, poi.angle, poi.rackWidthPx, poi.rackDepthPx, "purple");
    } else if (poi.type === "standby") {
      drawPoiBox(ctx, p.cx, p.cy, poi.label, counterScale, poi.angle, poi.rackWidthPx, poi.rackDepthPx, "blue");
    } else if (renderKind === "circle") {
      drawPoiCircle(ctx, p.cx, p.cy, poi.label, counterScale);
    } else {
      drawPoiTriangle(ctx, p.cx, p.cy, poi.label, counterScale);
    }

    // angle 방향 표시 (충전/대기/잭킹 지점 제외)
    if (poi.angle != null && poi.type !== "charging" && poi.type !== "workstation" && poi.type !== "jack" && poi.type !== "standby") {
      drawPoiAngleIndicator(ctx, p.cx, p.cy, poi.angle, counterScale);
    }
  }
}

/* ------------------------------------------------------------------ */
/*  Draw: Robot Markers                                                */
/* ------------------------------------------------------------------ */

/** Rounded rectangle path helper */
function roundedRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number
) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

const TRAJECTORY_COLORS = [
  "#5a8ff5", // 파랑
  "#f5b731", // 노랑
  "#3de0a4", // 초록
  "#f56565", // 빨강
  "#a855f7", // 보라
  "#36dfc8", // 민트
  "#ff8c42", // 주황
  "#e879f9", // 핑크
];

function drawRobotTargetLines(
  ctx: CanvasRenderingContext2D,
  targets: Map<string, { x: number; y: number }>,
  robots: RobotMarkerData[],
  bounds: MapBounds,
  viewScale: number,
) {
  const counterScale = 1 / viewScale;
  let colorIdx = 0;

  targets.forEach((target, sn) => {
    const robot = robots.find((r) => r.robotId === sn);
    if (!robot) return;

    const color = TRAJECTORY_COLORS[colorIdx % TRAJECTORY_COLORS.length];
    colorIdx++;

    const to = mapToCanvas(target.x, target.y, bounds);

    // 펄스 링 1 (1.5초 주기)
    const t = (Date.now() % 1500) / 1500;
    const pulseR = (6 + t * 25) * counterScale;
    ctx.beginPath();
    ctx.arc(to.cx, to.cy, pulseR, 0, Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3 * counterScale;
    ctx.globalAlpha = 0.9 * (1 - t);
    ctx.stroke();

    // 펄스 링 2 (위상 차이)
    const t2 = ((Date.now() + 750) % 1500) / 1500;
    const pulseR2 = (6 + t2 * 25) * counterScale;
    ctx.beginPath();
    ctx.arc(to.cx, to.cy, pulseR2, 0, Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = 3 * counterScale;
    ctx.globalAlpha = 0.9 * (1 - t2);
    ctx.stroke();

    // 글로우 배경
    ctx.globalAlpha = 0.4;
    ctx.beginPath();
    ctx.arc(to.cx, to.cy, 12 * counterScale, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    // 중심 점
    ctx.globalAlpha = 1.0;
    ctx.beginPath();
    ctx.arc(to.cx, to.cy, 5 * counterScale, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2 * counterScale;
    ctx.stroke();

    ctx.globalAlpha = 1.0;
  });
}

function drawRobot(
  ctx: CanvasRenderingContext2D,
  robot: RobotMarkerData,
  canvasX: number,
  canvasY: number,
  counterScale: number
) {
  const { yaw, robotName, status, collisionState } = robot;

  const rotationDeg = -(yaw * 180) / Math.PI + 90;
  const rotationRad = (rotationDeg * Math.PI) / 180;

  ctx.save();
  ctx.translate(canvasX, canvasY);

  // --- Rotated body ---
  ctx.save();
  ctx.rotate(rotationRad);

  const s = counterScale;

  // Accent color
  let accentColor = "#42d8ff";
  let glowColor = "rgba(66, 216, 255, 0.7)";
  if (collisionState === "collision") {
    accentColor = "#ff4343";
    glowColor = "rgba(255, 67, 67, 0.9)";
  } else if (collisionState === "near_miss") {
    accentColor = "#ffbf2d";
    glowColor = "rgba(255, 191, 45, 0.8)";
  } else if (status === "warning") {
    accentColor = "#ffba24";
    glowColor = "rgba(255, 186, 36, 0.7)";
  } else if (status === "error") {
    accentColor = "#ff5757";
    glowColor = "rgba(255, 87, 87, 0.7)";
  }

  // ── 1. Direction Arrow (cursor / pointer shape) ──
  ctx.beginPath();
  ctx.moveTo(0, -40 * s);          // tip
  ctx.lineTo(9 * s, -18 * s);     // bottom-right
  ctx.lineTo(0, -25 * s);         // notch center
  ctx.lineTo(-9 * s, -18 * s);    // bottom-left
  ctx.closePath();
  ctx.lineJoin = "round";
  ctx.fillStyle = accentColor;
  ctx.shadowColor = glowColor;
  ctx.shadowBlur = 8 * s;
  ctx.fill();
  resetShadow(ctx);
  ctx.strokeStyle = accentColor;
  ctx.lineWidth = 2 * s;
  ctx.lineJoin = "round";
  ctx.stroke();

  // ── 2. Wheels (4 corners) ──
  const wheelW = 4 * s;
  const wheelH = 7 * s;
  const wheelR = 1.5 * s;
  const wheels: [number, number][] = [
    [-14 * s, -10 * s], [10 * s, -10 * s],
    [-14 * s,   9 * s], [10 * s,   9 * s],
  ];
  for (const [wx, wy] of wheels) {
    roundedRect(ctx, wx, wy, wheelW, wheelH, wheelR);
    ctx.fillStyle = "#2a2a2a";
    ctx.fill();
    // Hub line
    ctx.beginPath();
    ctx.moveTo(wx + wheelW / 2, wy + 1.2 * s);
    ctx.lineTo(wx + wheelW / 2, wy + wheelH - 1.2 * s);
    ctx.strokeStyle = "#999";
    ctx.lineWidth = 1 * s;
    ctx.lineCap = "round";
    ctx.stroke();
  }

  // ── 3. Body (AGV platform) ──
  const bw = 24 * s;
  const bh = 28 * s;
  const bx = -12 * s;
  const by = -12 * s;
  const br = 4 * s;

  roundedRect(ctx, bx, by, bw, bh, br);
  ctx.fillStyle = "#f0f2f5";
  ctx.shadowColor = "rgba(0, 0, 0, 0.3)";
  ctx.shadowOffsetY = 1 * s;
  ctx.shadowBlur = 4 * s;
  ctx.fill();
  resetShadow(ctx);
  ctx.strokeStyle = "#a0aab4";
  ctx.lineWidth = 1.2 * s;
  ctx.stroke();

  // ── 4–6. Body details (clipped to body shape) ──
  ctx.save();
  roundedRect(ctx, bx, by, bw, bh, br);
  ctx.clip();

  // Dark bands (front/rear edge)
  ctx.fillStyle = "#8a9bb0";
  ctx.fillRect(bx, by, bw, 2.5 * s);
  ctx.fillRect(bx, by + bh - 2.5 * s, bw, 2.5 * s);

  // Front panel
  ctx.fillStyle = "#e0e4e8";
  ctx.fillRect(-7 * s, -11 * s, 14 * s, 3 * s);

  // Pillars (horizontal bars, top-down)
  ctx.fillStyle = "#dce0e5";
  ctx.fillRect(-4 * s, -6 * s, 8 * s, 2 * s);
  ctx.fillRect(-4 * s,  8 * s, 8 * s, 2 * s);

  // Sensors (front)
  ctx.fillStyle = "#777";
  ctx.beginPath();
  ctx.arc(-4 * s, -10 * s, 1.5 * s, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(4 * s, -10 * s, 1.5 * s, 0, Math.PI * 2);
  ctx.fill();

  ctx.restore(); // undo clip

  // ── 7. LED Strips (both sides, rear half) ──
  const ledW = 2.5 * s;
  const ledH = 12 * s;
  const ledR = 1 * s;
  const ledY = 2 * s;

  roundedRect(ctx, bx + 0.8 * s, ledY, ledW, ledH, ledR);
  ctx.fillStyle = accentColor;
  ctx.shadowColor = glowColor;
  ctx.shadowBlur = 4 * s;
  ctx.fill();
  resetShadow(ctx);

  roundedRect(ctx, bx + bw - ledW - 0.8 * s, ledY, ledW, ledH, ledR);
  ctx.fillStyle = accentColor;
  ctx.shadowColor = glowColor;
  ctx.shadowBlur = 4 * s;
  ctx.fill();
  resetShadow(ctx);

  ctx.restore(); // undo rotation

  // --- Label (not rotated) ---
  drawMarkerLabel(ctx, 0, 22 * counterScale, robotName, accentColor, counterScale, 700);

  ctx.restore(); // undo translation
}

function drawRobots(
  ctx: CanvasRenderingContext2D,
  robots: RobotMarkerData[],
  bounds: MapBounds,
  viewScale: number
) {
  const counterScale = 1 / viewScale;

  for (const robot of robots) {
    const p = mapToCanvas(robot.position.x, robot.position.y, bounds);
    drawRobot(ctx, robot, p.cx, p.cy, counterScale);
  }
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function MonitoringMapCanvas({
  mapSrc,
  pois,
  waypoints,
  routeWaypoints,
  routeSegments = [],
  robots,
  virtualWalls,
  showMapBackground,
  showNavigationLine,
  showDirectionArrows,
  showVirtualWalls: showVirtualWallsFlag,
  showNavigationNodes,
  showPoiMarkers,
  robotTargets,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const viewRef = useRef<View>({ scale: 1, offsetX: 0, offsetY: 0 });
  const processedRef = useRef<HTMLCanvasElement | null>(null);
  const imgReadyRef = useRef(false);
  const mapSizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });
  const dragRef = useRef({ dragging: false, lastX: 0, lastY: 0 });

  // Store latest props in a ref for the rAF draw callback
  const dataRef = useRef<DrawData>({
    pois,
    waypoints,
    routeWaypoints,
    routeSegments,
    robots,
    virtualWalls,
    showMapBackground,
    showNavigationLine,
    showDirectionArrows,
    showVirtualWalls: showVirtualWallsFlag,
    showNavigationNodes,
    showPoiMarkers,
    robotTargets,
  });
  dataRef.current = {
    pois,
    waypoints,
    routeWaypoints,
    routeSegments,
    robots,
    virtualWalls,
    showMapBackground,
    showNavigationLine,
    showDirectionArrows,
    showVirtualWalls: showVirtualWallsFlag,
    showNavigationNodes,
    showPoiMarkers,
    robotTargets,
  };

  // --- Image loading ---
  useEffect(() => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.decoding = "async";
    img.src = mapSrc;

    img.onload = () => {
      processedRef.current = removeOutsideBackground(img);
      imgReadyRef.current = true;
      mapSizeRef.current = { w: img.naturalWidth, h: img.naturalHeight };
      fitToView();
    };

    img.onerror = () => {
      processedRef.current = null;
      imgReadyRef.current = false;
    };

    return () => {
      processedRef.current = null;
      imgReadyRef.current = false;
    };
  }, [mapSrc]);

  // --- Resize observer ---
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;

    const ro = new ResizeObserver(() => {
      resizeCanvas();
      fitToView();
    });
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  function resizeCanvas() {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = wrap.getBoundingClientRect();

    canvas.width = Math.max(1, Math.floor(rect.width * dpr));
    canvas.height = Math.max(1, Math.floor(rect.height * dpr));
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
  }

  function fitToView(padding = 24) {
    const wrap = wrapRef.current;
    if (!wrap) return;

    const rect = wrap.getBoundingClientRect();
    const cw = rect.width;
    const ch = rect.height;

    const { w, h } = mapSizeRef.current;
    if (w <= 0 || h <= 0 || cw <= 0 || ch <= 0) return;

    const scale = Math.min((cw - padding * 2) / w, (ch - padding * 2) / h);
    const offsetX = (cw - w * scale) / 2;
    const offsetY = (ch - h * scale) / 2;

    viewRef.current = { scale: clamp(scale, 0.1, 10), offsetX, offsetY };
  }

  // --- Pointer events ---
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;

    const getOffset = (e: MouseEvent | WheelEvent) => {
      const r = wrap.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const p = getOffset(e);
      dragRef.current = { dragging: true, lastX: p.x, lastY: p.y };
      wrap.classList.add("is-panning");
    };

    const onMouseUp = () => {
      dragRef.current.dragging = false;
      wrap.classList.remove("is-panning");
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!dragRef.current.dragging) return;
      const p = getOffset(e);
      const dx = p.x - dragRef.current.lastX;
      const dy = p.y - dragRef.current.lastY;

      const v = viewRef.current;
      viewRef.current = { ...v, offsetX: v.offsetX + dx, offsetY: v.offsetY + dy };

      dragRef.current.lastX = p.x;
      dragRef.current.lastY = p.y;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();

      const mouse = getOffset(e);
      const v0 = viewRef.current;
      const before = s2w(mouse, v0);

      const zoomIntensity = 0.0015;
      const nextScale = v0.scale * Math.exp(-e.deltaY * zoomIntensity);
      const scale = clamp(nextScale, 0.2, 8);

      const v1: View = { ...v0, scale };
      const after = w2s(before, v1);

      v1.offsetX += mouse.x - after.x;
      v1.offsetY += mouse.y - after.y;

      viewRef.current = v1;

      if (scale > 1) {
        wrap.classList.add("is-pannable");
      } else {
        wrap.classList.remove("is-pannable");
      }
    };

    canvas.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("mousemove", onMouseMove);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      canvas.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mouseup", onMouseUp);
      window.removeEventListener("mousemove", onMouseMove);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, []);

  // --- Draw loop ---
  useCanvasLoop(canvasRef, (ctx, canvas) => {
    const wrap = wrapRef.current;
    if (!wrap) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = wrap.getBoundingClientRect();
    const logicalW = rect.width;
    const logicalH = rect.height;

    // Resize if needed
    const expectedW = Math.max(1, Math.floor(logicalW * dpr));
    const expectedH = Math.max(1, Math.floor(logicalH * dpr));
    if (canvas.width !== expectedW || canvas.height !== expectedH) {
      canvas.width = expectedW;
      canvas.height = expectedH;
      canvas.style.width = `${logicalW}px`;
      canvas.style.height = `${logicalH}px`;
    }

    // Reset transform & clear
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, logicalW, logicalH);

    // Background fill
    ctx.fillStyle = "#141e2e";
    ctx.fillRect(0, 0, logicalW, logicalH);

    const v = viewRef.current;
    const data = dataRef.current;

    // Apply view transform
    ctx.save();
    ctx.translate(v.offsetX, v.offsetY);
    ctx.scale(v.scale, v.scale);

    // Draw map image
    if (imgReadyRef.current && processedRef.current) {
      const img = processedRef.current;

      if (data.showMapBackground) {
        ctx.drawImage(img, 0, 0);
      }

      // Compute bounds in world coordinates (image pixel space)
      const bounds: MapBounds = {
        offsetX: 0,
        offsetY: 0,
        drawWidth: img.width,
        drawHeight: img.height,
        natW: img.width,
        natH: img.height,
      };

      // Routes
      if (data.showNavigationLine) {
        drawRoutes(ctx, data, bounds);
      }

      // Direction arrows
      if (data.showDirectionArrows && data.showNavigationLine) {
        drawDirectionArrows(ctx, data, bounds);
      }


      // Waypoint markers
      if (data.showNavigationNodes) {
        drawWaypoints(ctx, data.waypoints, bounds, v.scale);
      }

      // POI markers
      if (data.showPoiMarkers) {
        drawPois(ctx, data.pois, bounds, v.scale);
      }

      // Robot target lines (현재 위치 → 목적지)
      if (data.robotTargets && data.robotTargets.size > 0) {
        drawRobotTargetLines(ctx, data.robotTargets, data.robots, bounds, v.scale);
      }

      // Robot markers (always shown)
      drawRobots(ctx, data.robots, bounds, v.scale);
    }

    ctx.restore();
  });

  return (
    <div ref={wrapRef} className="monitoring-canvas-wrap">
      <canvas ref={canvasRef} />
    </div>
  );
}
