"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { Modal } from "../Modal";
import { apiFetch, apiPatch } from "@/lib/api";
import type { MappingModalProps, MappingStatus } from "@/lib/types/map";
import { useAlert } from "@/lib/context/AlertContext";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

// 맵 데이터가 이 시간 동안 한 번도 안 오면 "뭔가 잘못됐다"고 보고 안내 + 재시작 버튼을 띄운다.
// (충전 도킹 중 맵핑처럼 로봇이 에러도 안 주고 조용히 실패하는 경우가 있음 — 미해결이슈 7번)
const MAP_STALL_SEC = 15;

function getWsUrl(httpUrl: string): string {
  return httpUrl.replace(/^http/, "ws");
}

export function MappingModal({
  open,
  businessId,
  areaId,
  areaName,
  connectedRobot,
  onClose,
  onMappingComplete,
}: MappingModalProps) {
  const { showAlert } = useAlert();
  const [status, setStatus] = useState<MappingStatus>("idle");
  const [mappingId, setMappingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [hasMapData, setHasMapData] = useState(false);
  const [mapDataStalled, setMapDataStalled] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Map data refs
  const baseImageRef = useRef<HTMLImageElement | null>(null);
  const mapMetaRef = useRef<{
    origin: [number, number];
    resolution: number;
    size: [number, number];
  } | null>(null);
  const poseRef = useRef<{ pos: [number, number]; ori: number } | null>(null);
  const trajectoryRef = useRef<[number, number][]>([]);
  const scanPointsRef = useRef<[number, number, number][]>([]);
  const rafRef = useRef<number>(0);
  const needsRenderRef = useRef(false);
  const has5cmTopicRef = useRef(false);

  const closeWs = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!open) {
      closeWs();
      setHasMapData(false);
      setMapDataStalled(false);
      mapMetaRef.current = null;
      baseImageRef.current = null;
      poseRef.current = null;
      trajectoryRef.current = [];
      scanPointsRef.current = [];
      has5cmTopicRef.current = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    }
  }, [open, closeWs]);

  // ── Convert world coordinates → canvas pixel ──
  const worldToPixel = useCallback(
    (
      wx: number,
      wy: number,
      origin: [number, number],
      resolution: number,
      imgH: number,
      scale: number,
      offsetX: number,
      offsetY: number
    ): [number, number] => {
      // image pixel
      const ipx = (wx - origin[0]) / resolution;
      const ipy = imgH - (wy - origin[1]) / resolution; // Y flipped
      // canvas pixel (scaled + offset for centering)
      return [offsetX + ipx * scale, offsetY + ipy * scale];
    },
    []
  );

  // ── Composite render ──
  const compositeRender = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    const img = baseImageRef.current;
    const meta = mapMetaRef.current;
    if (!canvas || !container || !img || !meta) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Fit canvas to container (retina-safe)
    const cw = container.clientWidth;
    const ch = container.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = cw * dpr;
    canvas.height = ch * dpr;
    canvas.style.width = `${cw}px`;
    canvas.style.height = `${ch}px`;
    ctx.scale(dpr, dpr);

    // 맵 이미지의 모서리 픽셀 색상을 샘플링하여 배경색으로 사용
    const tmpCanvas = document.createElement("canvas");
    tmpCanvas.width = img.width;
    tmpCanvas.height = img.height;
    const tmpCtx = tmpCanvas.getContext("2d")!;
    tmpCtx.drawImage(img, 0, 0);
    const pixel = tmpCtx.getImageData(0, 0, 1, 1).data;
    const bgColor = `rgb(${pixel[0]}, ${pixel[1]}, ${pixel[2]})`;

    // Clear
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, cw, ch);

    // Calculate scale to fit image in container (object-fit: contain)
    const imgW = img.width;
    const imgH = img.height;
    const scale = Math.min(cw / imgW, ch / imgH);
    const ox = (cw - imgW * scale) / 2;
    const oy = (ch - imgH * scale) / 2;

    // Draw base map image
    ctx.drawImage(img, ox, oy, imgW * scale, imgH * scale);

    const { origin, resolution } = meta;

    // Draw scan matched points (magenta/pink)
    if (scanPointsRef.current.length > 0) {
      ctx.fillStyle = "rgba(255, 105, 180, 0.7)";
      for (const pt of scanPointsRef.current) {
        const [px, py] = worldToPixel(
          pt[0], pt[1], origin, resolution, imgH, scale, ox, oy
        );
        ctx.fillRect(px - 1, py - 1, 3, 3);
      }
    }

    // Draw trajectory (cyan)
    if (trajectoryRef.current.length > 1) {
      ctx.beginPath();
      ctx.strokeStyle = "#00e5ff";
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      const [sx, sy] = worldToPixel(
        trajectoryRef.current[0][0],
        trajectoryRef.current[0][1],
        origin, resolution, imgH, scale, ox, oy
      );
      ctx.moveTo(sx, sy);
      for (let i = 1; i < trajectoryRef.current.length; i++) {
        const [tx, ty] = worldToPixel(
          trajectoryRef.current[i][0],
          trajectoryRef.current[i][1],
          origin, resolution, imgH, scale, ox, oy
        );
        ctx.lineTo(tx, ty);
      }
      ctx.stroke();
    }

    // Draw robot pose (red triangle with direction)
    if (poseRef.current) {
      const [rx, ry] = worldToPixel(
        poseRef.current.pos[0],
        poseRef.current.pos[1],
        origin, resolution, imgH, scale, ox, oy
      );
      const ori = poseRef.current.ori;
      const sz = 10;

      ctx.save();
      ctx.translate(rx, ry);
      // Rotate: ori is math angle (CCW from +X), canvas Y is flipped
      ctx.rotate(-ori);

      // Triangle pointing right (in robot's forward direction)
      ctx.beginPath();
      ctx.moveTo(sz * 1.5, 0);           // tip
      ctx.lineTo(-sz * 0.8, -sz * 0.8);  // top-left
      ctx.lineTo(-sz * 0.8, sz * 0.8);   // bottom-left
      ctx.closePath();
      ctx.fillStyle = "#ff3355";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }
  }, [worldToPixel]);

  // ── requestAnimationFrame-based render loop ──
  const scheduleRender = useCallback(() => {
    needsRenderRef.current = true;
  }, []);

  useEffect(() => {
    if (status !== "mapping" && status !== "finished") return;

    const tick = () => {
      if (needsRenderRef.current) {
        needsRenderRef.current = false;
        compositeRender();
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [status, compositeRender]);

  // 맵 데이터 무수신 감지 — MAP_STALL_SEC 동안 한 장도 못 받으면 안내 + 재시작 버튼
  useEffect(() => {
    if (status !== "mapping" || hasMapData) {
      setMapDataStalled(false);
      return;
    }
    const timer = setTimeout(() => setMapDataStalled(true), MAP_STALL_SEC * 1000);
    return () => clearTimeout(timer);
  }, [status, hasMapData]);

  // Also re-render on container resize
  useEffect(() => {
    if (status !== "mapping" && status !== "finished") return;
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver(() => scheduleRender());
    observer.observe(container);
    return () => observer.disconnect();
  }, [status, scheduleRender]);

  // ── WS message handlers ──
  const handleMapTopic = useCallback(
    (msg: Record<string, unknown>) => {
      const data = msg.data as string | undefined;
      if (typeof data !== "string" || data.length < 10) return;

      const size = msg.size as [number, number] | undefined;
      const origin = msg.origin as [number, number] | undefined;
      const resolution = msg.resolution as number | undefined;

      if (size && origin && resolution) {
        mapMetaRef.current = { origin, resolution, size };
      }

      const img = new Image();
      img.onload = () => {
        baseImageRef.current = img;
        setHasMapData(true);
        scheduleRender();
      };
      img.src = `data:image/png;base64,${data}`;
    },
    [scheduleRender]
  );

  const handlePoseTopic = useCallback(
    (msg: Record<string, unknown>) => {
      const pos = msg.pos as [number, number] | undefined;
      const ori = msg.ori as number | undefined;
      if (pos && pos.length >= 2) {
        poseRef.current = { pos, ori: ori ?? 0 };
        scheduleRender();
      }
    },
    [scheduleRender]
  );

  const handleTrajectoryTopic = useCallback(
    (msg: Record<string, unknown>) => {
      const points = msg.points as [number, number][] | undefined;
      if (Array.isArray(points)) {
        trajectoryRef.current = points;
        scheduleRender();
      }
    },
    [scheduleRender]
  );

  const handleScanPointsTopic = useCallback(
    (msg: Record<string, unknown>) => {
      const points = msg.points as [number, number, number][] | undefined;
      if (Array.isArray(points)) {
        scanPointsRef.current = points;
        scheduleRender();
      }
    },
    [scheduleRender]
  );

  const handleWsMessage = useCallback(
    (raw: string) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }

      if (msg.error) {
        // 이전엔 console.error 만 찍어서 화면엔 아무 표시가 없었다 → 사용자에게도 보이게
        console.error("[WS] error:", msg.error);
        setError(`맵핑 데이터 오류: ${String(msg.error)}`);
        return;
      }

      const topic = msg.topic as string | undefined;

      if (topic === "/map") {
        // ⚠️ 이 펌웨어는 맵핑 중(맵 미생성)에도 /map 을 빈 값(size [0,0], data 0바이트)으로
        // 계속 발행한다. 예전처럼 "도착만 하면" 플래그를 세우면 /maps/5cm/1hz 폴백이
        // 영구히 막혀 화면이 영원히 안 그려진다(미해결이슈 9번).
        // → 실제 데이터가 들어있을 때만 잠근다.
        const mapData = msg.data as string | undefined;
        if (typeof mapData === "string" && mapData.length >= 10) {
          has5cmTopicRef.current = true;
        }
        handleMapTopic(msg);
      } else if (topic === "/maps/5cm/1hz" || topic === "/maps/1cm/1hz") {
        // /map이 아직 안 온 경우에만 /maps/5cm/1hz를 fallback으로 사용
        if (!has5cmTopicRef.current) {
          handleMapTopic(msg);
        }
      } else if (topic === "/tracked_pose") {
        handlePoseTopic(msg);
      } else if (topic === "/trajectory") {
        handleTrajectoryTopic(msg);
      } else if (topic === "/scan_matched_points2") {
        handleScanPointsTopic(msg);
      }
    },
    [handleMapTopic, handlePoseTopic, handleTrajectoryTopic, handleScanPointsTopic]
  );

  const connectWs = useCallback(
    (robotIp: string) => {
      closeWs();
      const wsUrl = `${getWsUrl(API_URL)}/api/map/ws/${robotIp}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => console.log("[WS] 매핑 WebSocket 연결됨");
      ws.onmessage = (e) => handleWsMessage(e.data);
      ws.onerror = () => {
        console.error("[WS] 매핑 WebSocket 연결 오류");
        setError("맵핑 데이터 수신 연결에 실패했습니다.");
        showAlert({ title: "맵 오류", message: "맵핑 데이터 수신 연결에 실패했습니다.", errorCode: "MAP-009", errorType: "map", silent: true });
      };
      ws.onclose = (e) => {
        console.log("[WS] 매핑 WebSocket 종료:", e.code, e.reason);
        if (e.code !== 1000 && e.code !== 1005 && status === "mapping") {
          setError("맵핑 데이터 연결이 끊어졌습니다.");
          showAlert({ title: "맵 오류", message: "맵핑 데이터 연결이 끊어졌습니다.", errorCode: "MAP-010", errorType: "map", silent: true });
        }
      };
    },
    [closeWs, handleWsMessage]
  );

  // ── START ──
  const handleStart = async () => {
    if (!connectedRobot) {
      setError("맵핑할 로봇이 연결되지 않았습니다.");
      showAlert({ title: "맵 오류", message: "맵핑할 로봇이 연결되지 않았습니다.", errorCode: "MAP-008", errorType: "map", silent: true });
      return;
    }

    setError(null);
    setHasMapData(false);
    setMapDataStalled(false);
    poseRef.current = null;
    trajectoryRef.current = [];
    scanPointsRef.current = [];
    has5cmTopicRef.current = false;

    try {
      // 기존 매핑 작업이 있으면 자동으로 취소
      try {
        await apiPatch(`/api/map/${connectedRobot.ip}/mappings/current`, {
          state: "cancelled",
        });
        // 취소 후 로봇이 정리할 시간을 줌
        await new Promise((r) => setTimeout(r, 1000));
      } catch {
        // 현재 매핑이 없으면 404 — 무시
      }

      const result = await apiFetch<{ id: number }>(
        `/api/map/${connectedRobot.ip}/mappings`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            continue_mapping: false,
            start_pose_type: "current_pose",
          }),
        }
      );

      setMappingId(result.id);
      setStatus("mapping");
      connectWs(connectedRobot.ip);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "매핑 시작에 실패했습니다.";
      setError(message);
    }
  };

  // 맵 데이터 무수신 상태에서 사용자가 직접 다시 시도 —
  // handleStart 가 앞에서 진행 중인 맵핑을 cancelled 로 정리하므로 그대로 재호출하면 된다.
  const handleRestart = () => {
    setMapDataStalled(false);
    void handleStart();
  };

  // ── STOP ──
  const handleStop = async () => {
    if (!connectedRobot || mappingId === null) return;

    setError(null);
    setSaving(true);
    try {
      await apiPatch(`/api/map/${connectedRobot.ip}/mappings/current`, {
        state: "finished",
      });

      closeWs();
      setStatus("finished");

      const mappingData = await apiFetch<Record<string, unknown>>(
        `/api/map/${connectedRobot.ip}/mappings/${mappingId}`
      );

      const areaResult = await apiFetch<{ area_id: number }>(
        "/api/map/areas",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            business_id: businessId,
            name: areaName,
          }),
        }
      );

      await apiFetch("/api/map/maps/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          business_id: businessId,
          area_id: areaResult.area_id,
          robot_sn: connectedRobot.sn,
          mapping_id: mappingId,
          name: mappingData.name ?? null,
          thumbnail_url: mappingData.thumbnail_url ?? null,
          image_url: mappingData.image_url ?? null,
          grid_origin_x: mappingData.grid_origin_x ?? 0,
          grid_origin_y: mappingData.grid_origin_y ?? 0,
          grid_resolution: mappingData.grid_resolution ?? 0,
          url: mappingData.url ?? null,
          start_time: mappingData.start_time ?? null,
          end_time: mappingData.end_time ?? null,
          state: mappingData.state ?? "finished",
          bag_id: mappingData.bag_id ?? null,
          bag_url: mappingData.bag_url ?? null,
          download_url: mappingData.download_url ?? null,
          pbstream_url: mappingData.pbstream_url ?? null,
          trajectories_url: mappingData.trajectories_url ?? null,
          initial_x: mappingData.initial_x ?? 0,
          initial_y: mappingData.initial_y ?? 0,
          initial_ori: mappingData.initial_ori ?? 0,
        }),
      });

      onMappingComplete?.();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "매핑 완료 처리에 실패했습니다.";
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  // ── CANCEL ──
  const handleCancel = async () => {
    if (!connectedRobot) {
      setStatus("idle");
      setMappingId(null);
      setHasMapData(false);
      closeWs();
      onClose();
      return;
    }

    setError(null);
    try {
      if (status === "mapping") {
        await apiPatch(`/api/map/${connectedRobot.ip}/mappings/current`, {
          state: "cancelled",
        });
      }
    } catch {
      // ignore
    } finally {
      closeWs();
      setMappingId(null);
      setHasMapData(false);
      setStatus("idle");
      onClose();
    }
  };

  const handleClose = () => {
    if (status === "mapping") return;
    closeWs();
    setMappingId(null);
    setHasMapData(false);
    setStatus("idle");
    setError(null);
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="맵핑"
      width="90vw"
      height="85vh"
    >
      <div className="mapping-modal">
        {/* Info bar */}
        <div className="mapping-modal__info">
          <span className="mapping-modal__info-item">
            <strong>영역:</strong> {areaName}
          </span>
          {connectedRobot && (
            <span className="mapping-modal__info-item">
              <strong>로봇:</strong> {connectedRobot.name} ({connectedRobot.ip})
            </span>
          )}
          {status === "mapping" && (
            <span className="mapping-modal__status mapping-modal__status--active">
              <span className="mapping-modal__status-dot" />
              맵핑 진행 중...
            </span>
          )}
          {status === "finished" && (
            <span className="mapping-modal__status mapping-modal__status--done">
              맵핑 완료
            </span>
          )}
        </div>

        {error && <div className="mapping-modal__error">{error}</div>}

        {/* Map visualization area */}
        <div ref={containerRef} className="mapping-modal__map-area">
          {status === "idle" && (
            <div className="mapping-modal__placeholder">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <path d="M3 9l6 6 4-4 8 8" />
                <circle cx="15" cy="7" r="2" />
              </svg>
              <span>시작 버튼을 눌러 맵핑을 시작하세요</span>
            </div>
          )}

          {(status === "mapping" || status === "finished") && (
            <canvas ref={canvasRef} className="mapping-modal__canvas" />
          )}

          {status === "mapping" && !hasMapData && (
            <div className="mapping-modal__overlay-loading">
              <div className="mapping-modal__scan-animation">
                <div className="mapping-modal__scan-ring" />
                <div className="mapping-modal__scan-ring mapping-modal__scan-ring--delay" />
              </div>
              <span>맵 데이터 수신 대기 중...</span>
              {mapDataStalled && (
                <>
                  <span className="mapping-modal__stall-hint">
                    {MAP_STALL_SEC}초 동안 맵 데이터가 오지 않았습니다.
                    <br />
                    로봇이 충전독에 도킹돼 있으면 맵이 생성되지 않습니다 — 충전기에서 분리한 뒤 다시 시작하세요.
                    <br />
                    잭이 내려가 있는지(progress 0.0), 로봇 경보가 없는지도 확인하세요.
                  </span>
                  <button
                    className="mapping-modal__btn mapping-modal__btn--start"
                    onClick={handleRestart}
                    disabled={saving}
                  >
                    다시 시작
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="mapping-modal__actions">
          <button
            className="mapping-modal__btn mapping-modal__btn--cancel"
            onClick={handleCancel}
            disabled={status === "finished" || saving}
          >
            취소
          </button>
          <div className="mapping-modal__actions-right">
            {status === "idle" && (
              <button
                className="mapping-modal__btn mapping-modal__btn--start"
                onClick={handleStart}
                disabled={!connectedRobot}
                title={!connectedRobot ? "로봇을 먼저 연결하세요" : ""}
              >
                시작
              </button>
            )}
            {status === "mapping" && (
              <button
                className="mapping-modal__btn mapping-modal__btn--stop"
                onClick={handleStop}
                disabled={saving}
              >
                {saving ? "저장 중..." : "중지"}
              </button>
            )}
            {status === "finished" && (
              <button
                className="mapping-modal__btn mapping-modal__btn--confirm"
                onClick={handleClose}
              >
                완료
              </button>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
