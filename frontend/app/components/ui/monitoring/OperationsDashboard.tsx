"use client";

import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

// ── 타입 ─────────────────────────────────────────────────
type DispatchSession = {
  id: number;
  robot_id: number;
  robot_name: string | null;
  status: string;
  current_poi_id: number | null;
  current_poi_name: string | null;
  target_poi_id: number | null;
  target_poi_name: string | null;
  with_rack: boolean;
  first_poi_id?: number | null;
  started_at?: string | null;
  ended_at?: string | null;
};
type DispatchStatusResp = {
  sessions: DispatchSession[];
  occupied_poi_ids: number[];
  available_pois: { id: number; name: string; poi_type?: string }[];
};
type LiveRobot = {
  ID?: number;
  IP: string;
  ROBOTNAME?: string;
  SN?: string;
  ONLINE: string;
  RUNSTATE?: string;
  "POWER(%)"?: string;
};
type POI = { id: number; name: string; poi_type?: string };
// 오늘의 dispatch 세션 (KPI / 최근 이벤트 집계용)
type TodaySession = {
  id: number;
  robot_id: number;
  robot_name: string | null;
  status: string;
  first_poi_id: number | null;
  current_poi_id: number | null;
  current_poi_name: string | null;
  target_poi_id: number | null;
  target_poi_name: string | null;
  with_rack: boolean;
  started_at: string;
  ended_at: string | null;
};

type Props = { areaId?: number };

const REFRESH_MS = 2000;

function fmtClock(iso?: string | null) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  } catch {
    return "";
  }
}

function isToday(iso: string) {
  const d = new Date(iso);
  const n = new Date();
  return d.getFullYear() === n.getFullYear()
    && d.getMonth() === n.getMonth()
    && d.getDate() === n.getDate();
}

function durationMin(startedAt: string, finishedAt: string | null): number | null {
  if (!finishedAt) return null;
  const s = new Date(startedAt).getTime();
  const f = new Date(finishedAt).getTime();
  if (!Number.isFinite(s) || !Number.isFinite(f)) return null;
  return (f - s) / 60_000;
}

export function OperationsDashboard({ areaId }: Props) {
  const [dispatch, setDispatch] = useState<DispatchStatusResp | null>(null);
  const [live, setLive] = useState<LiveRobot[]>([]);
  const [pois, setPois] = useState<POI[]>([]);
  const [todaySessions, setTodaySessions] = useState<TodaySession[]>([]);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // POI 목록 — area 바뀔 때만 조회
  useEffect(() => {
    const url = areaId ? `${API}/api/map/active-pois?area_id=${areaId}` : `${API}/api/map/active-pois`;
    fetch(url)
      .then((r) => r.json())
      .then((d) => setPois(Array.isArray(d) ? d.map((p: any) => ({ id: p.id, name: p.name, poi_type: p.poi_type || p.type })) : []))
      .catch(() => {});
  }, [areaId]);

  // 활성 세션 + 라이브 로봇 + 오늘 세션 폴링
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const [dispR, liveR, todayR] = await Promise.all([
          fetch(`${API}/api/dispatch/status`),
          fetch(`${API}/api/robots/live`),
          fetch(`${API}/api/dispatch/sessions/today`),
        ]);
        if (cancelled) return;
        if (dispR.ok) setDispatch(await dispR.json());
        if (liveR.ok) {
          const j = await liveR.json();
          setLive(Array.isArray(j) ? j : Array.isArray(j?.items) ? j.items : []);
        }
        if (todayR.ok) {
          const j = await todayR.json();
          setTodaySessions(Array.isArray(j) ? j : []);
        }
      } catch {}
    };
    poll();
    pollingRef.current = setInterval(poll, REFRESH_MS);
    return () => {
      cancelled = true;
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  // ── 위치/충전소 분리 ──────────────────────────────
  const workPois = pois.filter((p) => p.poi_type === "jack");

  // 세션에서 robot_id → 점유 POI 매핑
  const robotAtPoi = new Map<number, { robotId: number; robotName: string | null; isTarget: boolean }>();
  for (const s of dispatch?.sessions ?? []) {
    if (s.current_poi_id) robotAtPoi.set(s.current_poi_id, { robotId: s.robot_id, robotName: s.robot_name, isTarget: false });
    if (s.target_poi_id && !robotAtPoi.has(s.target_poi_id))
      robotAtPoi.set(s.target_poi_id, { robotId: s.robot_id, robotName: s.robot_name, isTarget: true });
  }


  // ── 오늘 KPI 집계 (dispatch_sessions) ───────────────
  const okN = todaySessions.filter((s) => s.status === "completed").length;
  const failN = todaySessions.filter((s) => s.status === "failed").length;
  const totN = todaySessions.length;
  const successRate = totN > 0 ? Math.round((okN / totN) * 1000) / 10 : null;
  const durations = todaySessions
    .map((s) => durationMin(s.started_at, s.ended_at))
    .filter((v): v is number => v !== null && v > 0 && v < 600);
  const avgMin = durations.length > 0
    ? durations.reduce((a, b) => a + b, 0) / durations.length
    : null;

  // ── 최근 이벤트 (최근 6개) ────────────────────────
  const recent = todaySessions.slice(0, 6);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 4 }}>
      {/* 위치 현황 */}
      <Section title="작업 위치">
        {workPois.length === 0 ? (
          <Hint>맵에 작업 위치 없음</Hint>
        ) : (
          <Grid>
            {workPois.map((p) => {
              const occ = robotAtPoi.get(p.id);
              return <PoiCell key={p.id} name={p.name} robotId={occ?.robotId} robotName={occ?.robotName} isTarget={!!occ?.isTarget} />;
            })}
          </Grid>
        )}
      </Section>

      {/* 로봇 상태 — 한 줄에 한 로봇씩, 큰 카드 */}
      <Section title="로봇 상태">
        {live.length === 0 ? (
          <Hint>로봇 없음</Hint>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {live
              .slice()
              .sort((a, b) => (Number(a.ID) || 0) - (Number(b.ID) || 0))
              .map((r) => {
                const c = robotColor(r.ID);
                const online = r.ONLINE === "Online";
                const runstate = (r.RUNSTATE || "").toUpperCase();

                // dispatch 세션에서 이 로봇의 현재 작업 찾기
                const session = (dispatch?.sessions ?? []).find((s) => s.robot_id === r.ID);

                // 상태 라벨 결정 (우선순위: dispatch 세션 > 라이브 RUNSTATE)
                let stateLabel = "";
                let stateIcon = "";
                let stateColor = "#8a93a6";
                if (!online) {
                  stateLabel = "오프라인";
                  stateIcon = "⚫";
                  stateColor = "#555c6b";
                } else if (session) {
                  if (session.status === "moving") {
                    stateLabel = `${session.current_poi_name || "출발지"} → ${session.target_poi_name || "?"} 이동 중`;
                    stateIcon = "🔄";
                    stateColor = "#6db5ff";
                  } else if (session.status === "awaiting_next") {
                    stateLabel = `${session.current_poi_name || "?"} 도착 · 대기 중`;
                    stateIcon = "⏸";
                    stateColor = "#6dd99b";
                  } else if (session.status === "picking_up" || session.status === "starting") {
                    stateLabel = "렉 픽업 중";
                    stateIcon = "🟦";
                    stateColor = "#6db5ff";
                  } else if (session.status === "returning") {
                    stateLabel = "충전소 복귀 중";
                    stateIcon = "🏠";
                    stateColor = "#ffa46d";
                  } else {
                    stateLabel = session.status;
                    stateIcon = "▶";
                    stateColor = "#b9c1d0";
                  }
                } else if (runstate === "CHARGING") {
                  stateLabel = "충전 중";
                  stateIcon = "🔋";
                  stateColor = "#34c977";
                } else if (runstate === "IDLE" || runstate === "STANDBY" || runstate === "") {
                  stateLabel = "대기 중";
                  stateIcon = "⏳";
                  stateColor = "#8a93a6";
                } else {
                  stateLabel = runstate.toLowerCase();
                  stateIcon = "•";
                  stateColor = "#b9c1d0";
                }

                const battery = r["POWER(%)"] || "-";

                return (
                  <div
                    key={r.IP}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: 10,
                      borderRadius: 8,
                      background: online ? c.bg : "#0e1118",
                      border: `1px solid ${online ? c.border : "#232a39"}`,
                      opacity: online ? 1 : 0.55,
                    }}
                  >
                    {/* 좌측 색상바 */}
                    <div style={{
                      width: 4,
                      alignSelf: "stretch",
                      background: c.accent,
                      borderRadius: 2,
                      minHeight: 36,
                    }} />

                    {/* 본문 */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 13,
                        fontWeight: 700,
                        color: online ? c.text : "#555c6b",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}>
                        {r.ROBOTNAME || r.IP}
                      </div>
                      <div style={{
                        fontSize: 11,
                        color: stateColor,
                        marginTop: 4,
                        fontWeight: 500,
                      }}>
                        {stateIcon} {stateLabel}
                      </div>
                    </div>

                    {/* 우측 배터리 */}
                    <div style={{
                      textAlign: "right",
                      fontSize: 16,
                      fontWeight: 700,
                      color: online ? (
                        battery !== "-" && parseInt(battery) < 20 ? "#ff7d7d" : c.text
                      ) : "#555c6b",
                      minWidth: 48,
                    }}>
                      {battery}
                    </div>
                  </div>
                );
              })}
          </div>
        )}
      </Section>

      {/* 오늘 KPI */}
      <Section title="오늘">
        <KpiRow>
          <Kpi label="호출" value={String(totN)} />
          <Kpi label="완료" value={String(okN)} color="#6dd99b" />
          <Kpi label="실패" value={String(failN)} color={failN > 0 ? "#ff7d7d" : undefined} />
        </KpiRow>
        <KpiRow>
          <Kpi label="성공률" value={successRate != null ? `${successRate}%` : "-"} />
          <Kpi label="평균 시간" value={avgMin != null ? `${avgMin.toFixed(1)}분` : "-"} />
        </KpiRow>
      </Section>

      {/* 최근 이벤트 */}
      <Section title="최근 이벤트">
        {recent.length === 0 ? (
          <Hint>오늘 작업 없음</Hint>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
            {recent.map((s) => {
              // 세션 상태별 색/아이콘
              const stColor = s.status === "failed" ? "#ff7d7d"
                            : s.status === "completed" ? "#6dd99b"
                            : "#b9c1d0";
              const stIcon = s.status === "completed" ? "✓"
                            : s.status === "failed" ? "✕"
                            : s.status === "moving" ? "🔄"
                            : s.status === "awaiting_next" ? "⏸"
                            : s.status === "returning" ? "🏠"
                            : "▶";
              // 어느 POI로 갔는지(=호출 위치) — current → target → first 순으로 폴백
              const where = s.current_poi_name || s.target_poi_name
                          || (s.first_poi_id ? `#${s.first_poi_id}` : "-");
              return (
                <li key={s.id} style={{
                  fontSize: 11,
                  display: "flex",
                  gap: 8,
                  padding: "4px 0",
                  borderBottom: "1px solid var(--border-color, #232a39)",
                }}>
                  <span style={{ color: "var(--text-muted)", minWidth: 36 }}>{fmtClock(s.started_at)}</span>
                  <span style={{ flex: 1 }}>
                    {where}
                    {s.robot_name ? <span style={{ color: "var(--text-muted)" }}> · {s.robot_name}</span> : null}
                  </span>
                  <span style={{ color: stColor }}>
                    {stIcon}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Section>
    </div>
  );
}

// ── 하위 컴포넌트 ───────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", marginBottom: 6, letterSpacing: 0.5 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function Hint({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 11, color: "var(--text-muted)", padding: "8px 4px", opacity: 0.6 }}>{children}</div>;
}

function Grid({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(56px, 1fr))",
      gap: 6,
    }}>
      {children}
    </div>
  );
}

function shortRobotName(name?: string | null) {
  if (!name) return "";
  if (name.length <= 7) return name;
  return `…${name.slice(-4)}`;
}

// 로봇 ID → 고유 색상 (팔레트 8개를 순환)
const ROBOT_COLORS = [
  { bg: "#7c2d2d", border: "#f87171", text: "#fecaca", accent: "#ef4444" }, // 빨강
  { bg: "#1e553a", border: "#34d399", text: "#bbf7d0", accent: "#22c55e" }, // 초록
  { bg: "#3a2d10", border: "#fbbf24", text: "#fde68a", accent: "#f59e0b" }, // 노랑
  { bg: "#1e3a5a", border: "#60a5fa", text: "#bfdbfe", accent: "#3b82f6" }, // 파랑
  { bg: "#3a285a", border: "#a78bfa", text: "#ddd6fe", accent: "#8b5cf6" }, // 보라
  { bg: "#5a2d4d", border: "#f472b6", text: "#fbcfe8", accent: "#ec4899" }, // 분홍
  { bg: "#1e4d4d", border: "#22d3ee", text: "#a5f3fc", accent: "#06b6d4" }, // 청록
  { bg: "#4d2a1e", border: "#fb923c", text: "#fed7aa", accent: "#f97316" }, // 주황
];
function robotColor(robotId?: number | null) {
  if (!robotId) return ROBOT_COLORS[0];
  return ROBOT_COLORS[(robotId - 1) % ROBOT_COLORS.length];
}

function PoiCell({ name, robotId, robotName, isTarget }: {
  name: string; robotId?: number | null; robotName?: string | null; isTarget: boolean;
}) {
  const occupied = !!robotName;

  if (!occupied) {
    return (
      <div
        title={`${name} (비어있음)`}
        style={{
          padding: "10px 4px",
          borderRadius: 8,
          background: "#1a1f2a",
          border: "1px dashed #2a3343",
          textAlign: "center",
          fontSize: 12,
          fontWeight: 500,
          color: "#555c6b",
          minHeight: 56,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          gap: 2,
        }}
      >
        <div>{name}</div>
        <div style={{ fontSize: 9, opacity: 0.6 }}>비어있음</div>
      </div>
    );
  }

  // 점유 — 로봇별 고유 색상. 도착/진입은 채도(투명도)로 구분
  const c = robotColor(robotId);
  const label = isTarget ? "▶ 진입 중" : "● 점유";

  return (
    <div
      title={`${name} — ${robotName}${isTarget ? " (이동 중)" : " (도착)"}`}
      style={{
        padding: "10px 4px",
        borderRadius: 8,
        background: c.bg,
        border: `2px ${isTarget ? "dashed" : "solid"} ${c.border}`,
        textAlign: "center",
        fontSize: 13,
        fontWeight: 700,
        color: "#fff",
        minHeight: 56,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 2,
        boxShadow: isTarget ? "none" : `0 0 0 1px rgba(0,0,0,0.3), 0 4px 12px ${c.accent}40`,
        opacity: isTarget ? 0.85 : 1,
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 800 }}>{name}</div>
      <div style={{
        fontSize: 9,
        color: c.text,
        fontWeight: 700,
        letterSpacing: 0.3,
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 9,
        color: c.text,
        fontWeight: 500,
        marginTop: 1,
        opacity: 0.9,
      }}>
        {shortRobotName(robotName)}
      </div>
    </div>
  );
}

function KpiRow({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 4 }}>
      {children}
    </div>
  );
}

function Kpi({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      padding: "8px 4px",
      borderRadius: 6,
      background: "#181d28",
      border: "1px solid #232a39",
      textAlign: "center",
    }}>
      <div style={{ fontSize: 16, fontWeight: 700, color: color || "#f0f3fa" }}>{value}</div>
      <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>{label}</div>
    </div>
  );
}
