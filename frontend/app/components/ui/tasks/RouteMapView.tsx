"use client";

import { useState, useRef, useEffect, useCallback } from "react";

type PoiItem = {
  id: number;
  name: string;
  type: string;
  world_x: number;
  world_y: number;
};

type SelectedWp = {
  poi_id: number;
  waypoint_type: string;
};

type Props = {
  pois: PoiItem[];
  selectedWaypoints: SelectedWp[];
  onPoiClick: (poi: PoiItem) => void;
};

export function RouteMapView({ pois, selectedWaypoints, onPoiClick }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [mapImg, setMapImg] = useState<HTMLImageElement | null>(null);
  const [mapMeta, setMapMeta] = useState<{ ox: number; oy: number; res: number } | null>(null);
  const [zoom, setZoom] = useState(0.5);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const initialFitDone = useRef(false);
  const isPanning = useRef(false);
  const lastMouse = useRef({ x: 0, y: 0 });

  // 맵 이미지 + 메타 로드
  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_URL || "";
    fetch(`${API}/api/map/default-map`, { cache: "no-store" })
      .then((r) => r.json())
      .then((data) => {
        if (data?.image_url) {
          const img = new Image();
          img.crossOrigin = "anonymous";
          img.onload = () => setMapImg(img);
          img.src = `${API}${data.image_url}`;
          setMapMeta({
            ox: data.grid_origin_x || 0,
            oy: data.grid_origin_y || 0,
            res: data.grid_resolution || 0.05,
          });
        }
      })
      .catch(() => {});
  }, []);

  const worldToPixel = useCallback(
    (wx: number, wy: number) => {
      if (!mapMeta || !mapImg) return { x: 0, y: 0 };
      const px = (wx - mapMeta.ox) / mapMeta.res;
      const py = mapImg.height - (wy - mapMeta.oy) / mapMeta.res;
      return { x: px - mapImg.width / 2, y: py - mapImg.height / 2 };
    },
    [mapMeta, mapImg]
  );

  // 캔버스 그리기
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !mapImg) return;

    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    ctx.save();
    ctx.translate(w / 2 + offset.x, h / 2 + offset.y);
    ctx.scale(zoom, zoom);

    // 맵 이미지
    ctx.drawImage(mapImg, -mapImg.width / 2, -mapImg.height / 2);

    // POI 마커 (줌에 반비례하여 크기 유지)
    const s = 1 / zoom;
    const res = mapMeta?.res || 0.05;
    const rackW = 0.83 / res;
    const rackD = 0.87 / res;

    for (const poi of pois) {
      const pos = worldToPixel(poi.world_x, poi.world_y);
      const isSelected = selectedWaypoints.some((w) => w.poi_id === poi.id);
      const wpIdx = selectedWaypoints.findIndex((w) => w.poi_id === poi.id);

      if (poi.type === "jack") {
        // 작업 포인트: 박스 + 쉐브론
        ctx.save();
        ctx.translate(pos.x, pos.y);

        // 박스
        ctx.fillStyle = isSelected ? "rgba(90, 143, 245, 0.3)" : "rgba(155, 89, 182, 0.25)";
        ctx.strokeStyle = isSelected ? "#fff" : "#9b59b6";
        ctx.lineWidth = isSelected ? 1.5 : 0.8;
        ctx.fillRect(-rackW / 2, -rackD / 2, rackW, rackD);
        ctx.strokeRect(-rackW / 2, -rackD / 2, rackW, rackD);

        // 쉐브론
        ctx.strokeStyle = "rgba(255,255,255,0.6)";
        ctx.lineWidth = 0.8;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        const cw = rackW * 0.3;
        const ch = rackD * 0.1;
        for (const off of [-ch, ch * 2]) {
          ctx.beginPath();
          ctx.moveTo(-cw, off + ch);
          ctx.lineTo(0, off);
          ctx.lineTo(cw, off + ch);
          ctx.stroke();
        }

        // 선택 시 순서 번호
        if (isSelected && wpIdx >= 0) {
          ctx.fillStyle = "#fff";
          ctx.font = `bold ${Math.round(12 * s)}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(String(wpIdx + 1), 0, 0);
        }

        ctx.restore();
      } else {
        // 일반/대기 POI: 원 마커
        const r = 8 * s;
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, r, 0, Math.PI * 2);
        ctx.fillStyle = isSelected
          ? "#5a8ff5"
          : poi.type === "standby"
            ? "#3de0a4"
            : "#36dfc8";
        ctx.fill();
        ctx.strokeStyle = isSelected ? "#fff" : "rgba(255,255,255,0.5)";
        ctx.lineWidth = (isSelected ? 3 : 1.5) * s;
        ctx.stroke();

        // 선택 시 순서 번호
        if (isSelected && wpIdx >= 0) {
          ctx.fillStyle = "#fff";
          ctx.font = `bold ${Math.round(12 * s)}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(String(wpIdx + 1), pos.x, pos.y);
        }
      }

      // 이름 라벨 (배경 + 텍스트)
      const fontSize = Math.max(11, Math.round(14 * s));
      ctx.font = `bold ${fontSize}px sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      const labelOffset = poi.type === "jack" ? rackD / 2 + 6 * s : 8 * s + 6 * s;
      const labelY = pos.y - labelOffset;
      const textWidth = ctx.measureText(poi.name).width;
      const pad = 3 * s;
      ctx.fillStyle = "rgba(0,0,0,0.7)";
      ctx.fillRect(pos.x - textWidth / 2 - pad, labelY - fontSize - pad, textWidth + pad * 2, fontSize + pad * 2);
      ctx.fillStyle = "#fff";
      ctx.fillText(poi.name, pos.x, labelY);
    }

    // 경로 연결선
    if (selectedWaypoints.length > 1) {
      ctx.beginPath();
      ctx.strokeStyle = "rgba(90, 143, 245, 0.6)";
      ctx.lineWidth = 3 * s;
      ctx.setLineDash([4, 4]);
      for (let i = 0; i < selectedWaypoints.length; i++) {
        const poi = pois.find((p) => p.id === selectedWaypoints[i].poi_id);
        if (!poi) continue;
        const pos = worldToPixel(poi.world_x, poi.world_y);
        if (i === 0) ctx.moveTo(pos.x, pos.y);
        else ctx.lineTo(pos.x, pos.y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.restore();
  }, [mapImg, pois, selectedWaypoints, zoom, offset, worldToPixel]);

  useEffect(() => {
    draw();
  }, [draw]);

  // 맵 이미지 로드 후 캔버스에 맞추기
  useEffect(() => {
    if (!mapImg || !containerRef.current || initialFitDone.current) return;
    const cw = containerRef.current.clientWidth;
    const ch = containerRef.current.clientHeight;
    if (cw === 0 || ch === 0) return;
    const fitZoom = Math.min(cw / mapImg.width, ch / mapImg.height);
    setZoom(fitZoom);
    setOffset({ x: 0, y: 0 });
    initialFitDone.current = true;
  }, [mapImg]);

  // 리사이즈
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const ro = new ResizeObserver(() => {
      canvas.width = container.clientWidth;
      canvas.height = container.clientHeight;
      draw();
    });
    ro.observe(container);
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;
    return () => ro.disconnect();
  }, [draw]);

  // 마우스 이벤트
  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.max(0.3, Math.min(5, z * (e.deltaY < 0 ? 1.1 : 0.9))));
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    isPanning.current = true;
    lastMouse.current = { x: e.clientX, y: e.clientY };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isPanning.current) return;
    setOffset((o) => ({
      x: o.x + e.clientX - lastMouse.current.x,
      y: o.y + e.clientY - lastMouse.current.y,
    }));
    lastMouse.current = { x: e.clientX, y: e.clientY };
  };

  const handleMouseUp = () => {
    isPanning.current = false;
  };

  const handleClick = (e: React.MouseEvent) => {
    if (!canvasRef.current || !mapImg) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;

    // canvas 좌표 → world 좌표 역변환으로 가장 가까운 POI 찾기
    const w = canvasRef.current.width;
    const h = canvasRef.current.height;

    for (const poi of pois) {
      const pos = worldToPixel(poi.world_x, poi.world_y);
      const screenX = (pos.x * zoom) + w / 2 + offset.x;
      const screenY = (pos.y * zoom) + h / 2 + offset.y;
      const dist = Math.sqrt((cx - screenX) ** 2 + (cy - screenY) ** 2);
      if (dist < 15) {
        onPoiClick(poi);
        return;
      }
    }
  };

  return (
    <div className="route-map-view" ref={containerRef}>
      <canvas
        ref={canvasRef}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onClick={handleClick}
        style={{ cursor: isPanning.current ? "grabbing" : "grab" }}
      />
      {(!mapImg || pois.length === 0) && (
        <div className="route-map-view__loading">
          <div className="spinner" />
        </div>
      )}
    </div>
  );
}
