"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { apiFetch } from "@/lib/api";

const API = process.env.NEXT_PUBLIC_API_URL || "";

type LiveRobot = { ID: number; IP: string; SN: string; ROBOTNAME: string; ONLINE: string; [key: string]: any };
type PoiOption = { id: number; name: string; type: string };

type JackJob = {
  job_id: string;
  status: string;
  message: string;
};

type Props = {
  liveRobots: LiveRobot[];
};

const STATUS_LABELS: Record<string, string> = {
  pending: "대기 중",
  aligning: "랙 정렬 중",
  jacking_up: "잭 올리는 중",
  moving_to_dropoff: "드롭오프 이동 중",
  jacking_down: "잭 내리는 중",
  moving: "이동 중",
  charging: "충전 도킹 중",
  waiting: "대기 중",
  returning: "복귀 중",
  done: "완료",
  error: "오류",
};

export function JackTestPanel({ liveRobots }: Props) {
  const [robotIp, setRobotIp] = useState("");
  const [robotId, setRobotId] = useState<number>(0);
  const [pois, setPois] = useState<PoiOption[]>([]);
  const [pickupId, setPickupId] = useState<number>(0);
  const [dropoffId, setDropoffId] = useState<number>(0);
  const [currentJob, setCurrentJob] = useState<JackJob | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const onlineRobots = liveRobots.filter((r) => r.ONLINE === "Online");
  const jackPois = pois.filter((p) => p.type === "jack");

  useEffect(() => {
    fetch(`${API}/api/map/active-pois`)
      .then((r) => r.json())
      .then((data) => setPois(Array.isArray(data) ? data.map((p: any) => ({ id: p.id, name: p.name, type: p.poi_type || p.type })) : []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const handleRobotChange = (ip: string) => {
    setRobotIp(ip);
    const robot = onlineRobots.find((r) => r.IP === ip);
    setRobotId(robot?.ID || 0);
  };

  const handleStart = async () => {
    if (!robotIp || !robotId || !pickupId || !dropoffId) return;
    setIsStarting(true);
    try {
      const res = await fetch(`${API}/api/tasks/manual-run-pois`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ robot_id: robotId, pickup_poi_id: pickupId, dropoff_poi_id: dropoffId }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      const histId = result.history_id;
      setCurrentJob({ job_id: String(histId), status: "pending", message: "작업이 시작되었습니다." });

      if (pollingRef.current) clearInterval(pollingRef.current);
      pollingRef.current = setInterval(async () => {
        try {
          const hist = await apiFetch<{ total: number; items: any[] }>(`/api/tasks/history/all?limit=5`);
          const h = hist.items.find((item: any) => item.id === histId);
          if (h) {
            const status = h.status === "succeeded" ? "done" : h.status;
            setCurrentJob({ job_id: String(histId), status, message: h.error_message || STATUS_LABELS[status] || status });
            if (h.status === "succeeded" || h.status === "failed") {
              if (pollingRef.current) {
                clearInterval(pollingRef.current);
                pollingRef.current = null;
              }
            }
          }
        } catch {}
      }, 2000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "시작 실패";
      setCurrentJob({ job_id: "", status: "error", message: msg });
    } finally {
      setIsStarting(false);
    }
  };

  const isRunning =
    currentJob != null &&
    currentJob.status !== "done" &&
    currentJob.status !== "error" &&
    currentJob.status !== "";

  const statusColor =
    currentJob?.status === "done"
      ? "var(--color-success)"
      : currentJob?.status === "error"
        ? "var(--color-error)"
        : "var(--color-warning)";

  const pickupName = jackPois.find((p) => p.id === pickupId)?.name || "";
  const dropoffName = jackPois.find((p) => p.id === dropoffId)?.name || "";

  return (
    <div className="jack-test-panel">
      <h3 className="jack-test-panel__title">수동 배차</h3>

      <div className="jack-test-panel__form">
        <label className="jack-test-panel__label">
          로봇
          <select
            className="jack-test-panel__select"
            value={robotIp}
            onChange={(e) => handleRobotChange(e.target.value)}
            disabled={isRunning}
          >
            <option value="">선택</option>
            {onlineRobots.map((r) => (
              <option key={r.IP} value={r.IP}>
                {r.ROBOTNAME || r.SN} ({r.IP})
              </option>
            ))}
          </select>
        </label>

        <label className="jack-test-panel__label">
          픽업 위치
          <select
            className="jack-test-panel__select"
            value={pickupId}
            onChange={(e) => setPickupId(Number(e.target.value))}
            disabled={isRunning}
          >
            <option value={0}>선택</option>
            {jackPois.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>

        <label className="jack-test-panel__label">
          드롭오프 위치
          <select
            className="jack-test-panel__select"
            value={dropoffId}
            onChange={(e) => setDropoffId(Number(e.target.value))}
            disabled={isRunning}
          >
            <option value={0}>선택</option>
            {jackPois.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>

        {pickupId > 0 && dropoffId > 0 && (
          <div className="jack-test-panel__route-info">
            {pickupName} <small>(픽업)</small> → {dropoffName} <small>(드롭오프)</small>
          </div>
        )}

        <div className="jack-test-panel__buttons">
          {!isRunning ? (
            <button
              className="btn btn--primary jack-test-panel__btn"
              onClick={handleStart}
              disabled={!robotIp || !pickupId || !dropoffId || isStarting}
            >
              {isStarting ? "시작 중..." : "실행"}
            </button>
          ) : (
            <button
              className="btn btn--danger jack-test-panel__btn"
              onClick={async () => {
                try {
                  if (robotIp) {
                    await fetch(`${API}/api/robots/remote/stop-all/${robotIp}`, { method: "POST" });
                  }
                } catch {}
                if (pollingRef.current) {
                  clearInterval(pollingRef.current);
                  pollingRef.current = null;
                }
                setCurrentJob({ job_id: "", status: "done", message: "중지됨" });
              }}
            >
              중지
            </button>
          )}
        </div>
      </div>

      {currentJob && (
        <div className="jack-test-panel__status" style={{ borderColor: statusColor }}>
          <div className="jack-test-panel__status-label" style={{ color: statusColor }}>
            {STATUS_LABELS[currentJob.status] || currentJob.status}
          </div>
          <div className="jack-test-panel__status-msg">{currentJob.message}</div>
        </div>
      )}
    </div>
  );
}
