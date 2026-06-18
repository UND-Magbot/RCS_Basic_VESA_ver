"use client";

import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

type DispatchSession = {
  id: number;
  robot_id: number;
  robot_name: string | null;
  status: string;
  with_rack: boolean;
  current_poi_id: number | null;
  current_poi_name: string | null;
  target_poi_id: number | null;
  target_poi_name: string | null;
  started_at: string | null;
};

type StatusInfo = { icon: string; text: string; color: string };

const STATUS_INFO: Record<string, StatusInfo> = {
  starting:      { icon: "🟦", text: "시작 중",         color: "#6db5ff" },
  picking_up:    { icon: "🟦", text: "렉 픽업 중",      color: "#6db5ff" },
  moving:        { icon: "🔄", text: "이동 중",         color: "#6db5ff" },
  awaiting_next: { icon: "⏸",  text: "대기 중",         color: "#6dd99b" },
  returning:     { icon: "🏠", text: "충전소 복귀 중", color: "#ffa46d" },
};

type Props = {
  liveRobots?: any[];
  areaId?: number;
};

/**
 * VESA 활성 배차 세션 카드 (읽기 전용).
 * - 데이터 소스: /api/dispatch/status (2초 폴링, 모든 세션 한번에)
 * - 표시: 로봇명 + 운영 모드(with_rack) + 현재→목표 위치 + 상태
 * - 0대일 때도 카드 영역은 유지 (placeholder) → 깜빡임 방지
 * - 제어 버튼 없음 — 모든 명령은 위치별 태블릿이 담당
 */
export function ActiveJobsPanel(_props: Props) {
  const [sessions, setSessions] = useState<DispatchSession[] | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API}/api/dispatch/status`);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (!cancelled) setSessions(Array.isArray(data?.sessions) ? data.sessions : []);
      } catch {
        if (!cancelled) setSessions((prev) => prev ?? []);
      }
    };
    poll();
    pollingRef.current = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const count = sessions?.length ?? 0;

  return (
    <div style={{ padding: 12, borderBottom: "1px solid var(--border-color)", minHeight: 80 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: "var(--text-muted)" }}>
        {count > 0 ? `작업 중인 로봇 (${count}대)` : "작업 현황"}
      </div>

      {sessions === null ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)", padding: "8px 0" }}>로딩 중...</div>
      ) : sessions.length === 0 ? (
        <div style={{
          fontSize: 12,
          color: "var(--text-muted)",
          textAlign: "center",
          padding: "16px 0",
          opacity: 0.6,
        }}>
          현재 작업 중인 로봇 없음
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {sessions.map((s) => {
            const info = STATUS_INFO[s.status] || { icon: "·", text: s.status, color: "#b9c1d0" };
            // 위치 라인 — 상태별로 다르게 표시
            let location = "";
            if (s.status === "moving" && s.target_poi_name) {
              location = `${s.current_poi_name || "출발지"} → ${s.target_poi_name}`;
            } else if (s.status === "awaiting_next" && s.current_poi_name) {
              location = `@ ${s.current_poi_name}`;
            } else if (s.status === "returning") {
              location = "→ 충전소";
            } else if (s.target_poi_name) {
              location = `→ ${s.target_poi_name}`;
            } else if (s.current_poi_name) {
              location = `@ ${s.current_poi_name}`;
            } else {
              location = "-";
            }

            return (
              <div
                key={s.robot_id}
                style={{
                  padding: 10,
                  background: "var(--bg-surface-2)",
                  border: "1px solid var(--border-color)",
                  borderRadius: 6,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>
                    {s.robot_name || `Robot #${s.robot_id}`}
                  </span>
                  <span style={{
                    fontSize: 10,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: s.with_rack ? "#1d2a4d" : "#2a3343",
                    color: s.with_rack ? "#6db5ff" : "#8a93a6",
                    fontWeight: 500,
                  }}>
                    {s.with_rack ? "렉 운반" : "렉 없이"}
                  </span>
                </div>
                <div style={{ fontSize: 13, marginBottom: 4, color: "var(--text-primary, #f0f3fa)" }}>
                  📍 {location}
                </div>
                <div style={{ fontSize: 12, color: info.color, fontWeight: 500 }}>
                  {info.icon} {info.text}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
