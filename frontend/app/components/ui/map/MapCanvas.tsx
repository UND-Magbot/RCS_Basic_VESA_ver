"use client";

import {
  useRef,
  useState,
  useEffect,
  useCallback,
  type MouseEvent,
  type WheelEvent,
} from "react";
import type { MapCanvasProps } from "@/lib/types/map";

export function MapCanvas({
  pois,
  lines,
  polygons,
  activeTool,
  selectedPOI,
  lineStartPOI,
  zoom,
  offset,
  rotation,
  mapImageUrl,
  robotPose,
  mapMeta,
  onCanvasClick,
  onPOIClick,
  onLineClick,
  onPolygonClick,
  onZoomChange,
  onOffsetChange,
  onImageLoad,
  vwTempPoints = [],
}: MapCanvasProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const isPanningRef = useRef(false);
  const panStartRef = useRef<{ x: number; y: number } | null>(null);
  const offsetStartRef = useRef({ x: 0, y: 0 });
  const mousePosRef = useRef<{ x: number; y: number } | null>(null);
  const [mousePos, setMousePos] = useState<{ x: number; y: number } | null>(null);
  const [vwMousePos, setVwMousePos] = useState<{ x: number; y: number } | null>(null);

  // lineStartPOI 해제 시 mousePos 초기화
  useEffect(() => {
    if (!lineStartPOI) setMousePos(null);
  }, [lineStartPOI]);
  const [processedImg, setProcessedImg] = useState<{
    url: string; w: number; h: number;
  } | null>(null);

  // Load image, remove gray outer area, produce transparent PNG data URL
  useEffect(() => {
    if (!mapImageUrl) { setProcessedImg(null); return; }
    const img = new window.Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const ctx = c.getContext("2d")!;
      ctx.drawImage(img, 0, 0);
      const imageData = ctx.getImageData(0, 0, c.width, c.height);
      const d = imageData.data;
      for (let i = 0; i < d.length; i += 4) {
        const r = d[i], g = d[i + 1], b = d[i + 2];
        // Gray pixels: R≈G≈B and in mid-gray range (100~180)
        const avg = (r + g + b) / 3;
        const spread = Math.max(Math.abs(r - avg), Math.abs(g - avg), Math.abs(b - avg));
        if (spread < 15 && avg > 100 && avg < 180) {
          d[i + 3] = 0; // make transparent
        }
      }
      ctx.putImageData(imageData, 0, 0);
      setProcessedImg({
        url: c.toDataURL("image/png"),
        w: img.naturalWidth,
        h: img.naturalHeight,
      });
      onImageLoad?.(img.naturalWidth, img.naturalHeight);
    };
    img.onerror = () => {
      console.error("[맵 이미지 로드 실패]", mapImageUrl);
      setProcessedImg(null);
    };
    img.src = mapImageUrl;
  }, [mapImageUrl]);

  const clamp = (val: number, min: number, max: number) =>
    Math.min(max, Math.max(min, val));

  const screenToCanvas = useCallback(
    (clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return { x: 0, y: 0 };
      const rect = svg.getBoundingClientRect();
      return {
        x: (clientX - rect.left - offset.x) / zoom,
        y: (clientY - rect.top - offset.y) / zoom,
      };
    },
    [zoom, offset]
  );

  const handleWheel = (e: WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    if (e.deltaY === 0) return;

    const svg = svgRef.current;
    if (!svg) return;

    const rect = svg.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    const nextZoom = clamp(zoom * factor, 0.2, 6);
    const ratio = nextZoom / zoom;

    const nextOffsetX = mouseX - (mouseX - offset.x) * ratio;
    const nextOffsetY = mouseY - (mouseY - offset.y) * ratio;

    onZoomChange(nextZoom);
    onOffsetChange({ x: nextOffsetX, y: nextOffsetY });
  };

  const handleMouseDown = (e: MouseEvent<SVGSVGElement>) => {
    if (e.button === 1 || (e.button === 0 && activeTool === "select")) {
      isPanningRef.current = true;
      panStartRef.current = { x: e.clientX, y: e.clientY };
      offsetStartRef.current = { ...offset };
      e.preventDefault();
    }
  };

  const handleMouseMove = (e: MouseEvent<SVGSVGElement>) => {
    const pos = screenToCanvas(e.clientX, e.clientY);
    mousePosRef.current = pos;

    // 가상벽 점 찍는 중이면 마우스 위치 갱신 (프리뷰용)
    if (activeTool === "virtualwall" && vwTempPoints.length > 0) {
      setVwMousePos(pos);
    }

    // 라인 그리기 중이면 마우스 위치를 state에 갱신 (임시 라인 렌더용)
    if (lineStartPOI && (activeTool === "line" || activeTool === "curveLine")) {
      setMousePos(pos);
    }

    if (isPanningRef.current && panStartRef.current) {
      // 좌클릭(1) 또는 휠클릭(4)이 눌린 상태에서만 패닝
      if (!(e.buttons & 1) && !(e.buttons & 4)) {
        isPanningRef.current = false;
        panStartRef.current = null;
        return;
      }
      const dx = e.clientX - panStartRef.current.x;
      const dy = e.clientY - panStartRef.current.y;
      onOffsetChange({
        x: offsetStartRef.current.x + dx,
        y: offsetStartRef.current.y + dy,
      });
      e.preventDefault();
    }
  };

  const handleMouseUp = (e: MouseEvent<SVGSVGElement>) => {
    if (isPanningRef.current) {
      const wasDragging =
        panStartRef.current &&
        (Math.abs(e.clientX - panStartRef.current.x) > 3 ||
          Math.abs(e.clientY - panStartRef.current.y) > 3);

      isPanningRef.current = false;
      panStartRef.current = null;

      if (wasDragging) return;
    }

    // POI나 라인 위에서 mouseUp → onClick 핸들러가 처리하므로 캔버스 클릭 무시
    const target = e.target as SVGElement;
    if (target.closest?.(".map-poi") || target.closest?.(".map-line")) return;

    if (
      activeTool === "point" ||
      activeTool === "jackPoint" ||
      activeTool === "line" ||
      activeTool === "curveLine" ||
      activeTool === "polygon" ||
      activeTool === "firewall" ||
      activeTool === "virtualwall"
    ) {
      const pos = screenToCanvas(e.clientX, e.clientY);
      onCanvasClick(pos.x, pos.y);
    }
  };

  const cursorClass = (() => {
    if (isPanningRef.current) return "map-canvas-area__svg--panning";
    switch (activeTool) {
      case "point":
      case "jackPoint":
        return "map-canvas-area__svg--point";
      case "line":
      case "curveLine":
        return "map-canvas-area__svg--line";
      case "del":
        return "map-canvas-area__svg--del";
      case "select":
        return "map-canvas-area__svg--pan";
      default:
        return "";
    }
  })();

  const renderArrow = (
    fromX: number,
    fromY: number,
    toX: number,
    toY: number,
    lineId: string,
    suffix: string,
    isSelected: boolean
  ) => {
    const dx = toX - fromX;
    const dy = toY - fromY;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len === 0) return null;
    const nx = dx / len;
    const ny = dy / len;
    const midX = (fromX + toX) / 2;
    const midY = (fromY + toY) / 2;
    const arrowSize = 4;
    const p1x = midX + nx * arrowSize;
    const p1y = midY + ny * arrowSize;
    const p2x = midX - nx * arrowSize * 0.4 - ny * arrowSize * 0.5;
    const p2y = midY - ny * arrowSize * 0.4 + nx * arrowSize * 0.5;
    const p3x = midX - nx * arrowSize * 0.4 + ny * arrowSize * 0.5;
    const p3y = midY - ny * arrowSize * 0.4 - nx * arrowSize * 0.5;

    return (
      <polygon
        key={`arrow-${lineId}-${suffix}`}
        points={`${p1x},${p1y} ${p2x},${p2y} ${p3x},${p3y}`}
        className={isSelected ? "map-line__arrow map-line__arrow--selected" : "map-line__arrow"}
      />
    );
  };

  return (
    <div className="map-canvas-area">
      <svg
        ref={svgRef}
        className={`map-canvas-area__svg ${cursorClass}`}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onContextMenu={(e) => e.preventDefault()}
      >
        <g transform={`translate(${offset.x}, ${offset.y}) scale(${zoom}) rotate(${rotation})`}>
          {/* Background map image (gray removed) */}
          {processedImg && (
            <image
              href={processedImg.url}
              x={-processedImg.w / 2}
              y={-processedImg.h / 2}
              width={processedImg.w}
              height={processedImg.h}
              className="map-canvas-area__bg-image"
            />
          )}

          {/* Polygons (virtual walls) */}
          {polygons.map((poly) => (
            <polygon
              key={poly.id}
              points={poly.points.map((p) => `${p.x},${p.y}`).join(" ")}
              className={poly.shapeType === "firewall" ? "map-polygon__firewall" : "map-polygon__shape"}
              onClick={(e) => {
                e.stopPropagation();
                onPolygonClick(poly.id);
              }}
            />
          ))}

          {/* 가상벽 점 찍기 프리뷰 */}
          {vwTempPoints.length > 0 && (
            <g>
              {/* 찍은 점들 사이 선 */}
              <polyline
                points={[
                  ...vwTempPoints.map((p) => `${p.x},${p.y}`),
                  ...(vwMousePos ? [`${vwMousePos.x},${vwMousePos.y}`] : []),
                ].join(" ")}
                className="map-polygon__firewall--preview"
                fill="none"
              />
              {/* 4점째면 닫히는 선 프리뷰 */}
              {vwTempPoints.length === 3 && vwMousePos && (
                <line
                  x1={vwMousePos.x} y1={vwMousePos.y}
                  x2={vwTempPoints[0].x} y2={vwTempPoints[0].y}
                  className="map-polygon__firewall--preview"
                />
              )}
              {/* 찍은 점 표시 */}
              {vwTempPoints.map((p, i) => (
                <circle key={i} cx={p.x} cy={p.y} r={4 / zoom}
                  fill="#ff3c3c" stroke="#fff" strokeWidth={1 / zoom} />
              ))}
            </g>
          )}

          {/* Lines */}
          {lines.map((line) => {
            const from = pois.find((p) => p.id === line.fromId);
            const to = pois.find((p) => p.id === line.toId);
            if (!from || !to) return null;

            const isSelected = false;

            return (
              <g
                key={line.id}
                className="map-line"
                onClick={(e) => {
                  e.stopPropagation();
                  onLineClick(line.id);
                }}
              >
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  className={
                    line.lineType === "firewall"
                      ? "map-line__path--firewall"
                      : isSelected
                      ? "map-line__path map-line__path--selected"
                      : "map-line__path"
                  }
                />
                {/* Click target (wider invisible line) */}
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke="transparent"
                  strokeWidth={12}
                />
                {/* Direction arrows — 방화벽 라인은 화살표 없음 */}
                {line.lineType !== "firewall" && (line.direction === "forward" ||
                  line.direction === "bidirectional") &&
                  renderArrow(from.x, from.y, to.x, to.y, line.id, "fwd", isSelected)}
                {line.lineType !== "firewall" && (line.direction === "backward" ||
                  line.direction === "bidirectional") &&
                  renderArrow(to.x, to.y, from.x, from.y, line.id, "bwd", isSelected)}
              </g>
            );
          })}

          {/* Temporary line while drawing */}
          {lineStartPOI && (activeTool === "line" || activeTool === "curveLine" || activeTool === "firewall") && mousePos && (() => {
            const startPoi = pois.find((p) => p.id === lineStartPOI);
            if (!startPoi) return null;
            return (
              <>
                <line
                  x1={startPoi.x}
                  y1={startPoi.y}
                  x2={mousePos.x}
                  y2={mousePos.y}
                  className="map-temp-line"
                />
                {/* Snap indicator at endpoint */}
                <circle
                  cx={mousePos.x}
                  cy={mousePos.y}
                  r={3}
                  className="map-snap-indicator"
                />
              </>
            );
          })()}

          {/* POI Markers */}
          {pois.map((poi) => {
            const isSelected = selectedPOI === poi.id;
            const isLineStart = lineStartPOI === poi.id;
            const circleClass = [
              "map-poi__circle",
              `map-poi__circle--${poi.type}`,
              isSelected || isLineStart ? "map-poi__circle--selected" : "",
            ]
              .filter(Boolean)
              .join(" ");

            return (
              <g
                key={poi.id}
                className="map-poi"
                onClick={(e) => {
                  e.stopPropagation();
                  onPOIClick(poi.id);
                }}
              >
                {poi.type === "firewall" ? (
                  <rect
                    x={poi.x - 3}
                    y={poi.y - 3}
                    width={6}
                    height={6}
                    transform={`rotate(45, ${poi.x}, ${poi.y})`}
                    className={circleClass}
                    strokeWidth={1}
                  />
                ) : poi.type === "jack" ? (
                  (() => {
                    const res = mapMeta?.grid_resolution || 0.05;
                    const rackW = 0.83 / res;
                    const rackD = 0.87 / res;
                    const angle = poi.angle != null ? -poi.angle * (180 / Math.PI) + 90 : 0;
                    return (
                      <g transform={`translate(${poi.x}, ${poi.y}) rotate(${angle})`}>
                        <rect
                          x={-rackW / 2}
                          y={-rackD / 2}
                          width={rackW}
                          height={rackD}
                          fill="rgba(155, 89, 182, 0.25)"
                          stroke={isSelected || isLineStart ? "#fff" : "#9b59b6"}
                          strokeWidth={isSelected || isLineStart ? 1 : 0.6}
                          rx={0.5}
                        />
                        {/* V자 쉐브론 방향 표시 */}
                        <polyline
                          points={`${-rackW * 0.3},${-rackD * 0.05} 0,${-rackD * 0.25} ${rackW * 0.3},${-rackD * 0.05}`}
                          fill="none"
                          stroke="rgba(255,255,255,0.6)"
                          strokeWidth={0.5}
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                        <polyline
                          points={`${-rackW * 0.3},${rackD * 0.15} 0,${-rackD * 0.05} ${rackW * 0.3},${rackD * 0.15}`}
                          fill="none"
                          stroke="rgba(255,255,255,0.6)"
                          strokeWidth={0.5}
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </g>
                    );
                  })()
                ) : (
                  <circle
                    cx={poi.x}
                    cy={poi.y}
                    r={3}
                    className={circleClass}
                    strokeWidth={1}
                  />
                )}
                <text
                  x={poi.x}
                  y={poi.y - (poi.type === "jack" ? (0.87 / (mapMeta?.grid_resolution || 0.05)) / 2 + 3 : 6)}
                  className="map-poi__label"
                >
                  {poi.name}
                </text>
              </g>
            );
          })}

          {/* Robot position indicator (red arrow) */}
          {robotPose && mapMeta && processedImg && mapMeta.grid_resolution > 0 && (() => {
            const imgW = processedImg.w;
            const imgH = processedImg.h;
            const ipx = (robotPose.pos[0] - mapMeta.grid_origin_x) / mapMeta.grid_resolution;
            const ipy = imgH - (robotPose.pos[1] - mapMeta.grid_origin_y) / mapMeta.grid_resolution;
            const cx = ipx - imgW / 2;
            const cy = ipy - imgH / 2;
            const ori = -robotPose.ori; // canvas Y is flipped


            const sz = 3;

            return (
              <g
                className="robot-indicator"
                transform={`translate(${cx}, ${cy}) rotate(${(ori * 180) / Math.PI})`}
              >
                <polygon
                  points={`${sz * 1.5},0 ${-sz * 0.8},${-sz * 0.8} ${-sz * 0.8},${sz * 0.8}`}
                  className="robot-indicator__arrow"
                />
              </g>
            );
          })()}
        </g>
      </svg>
    </div>
  );
}
