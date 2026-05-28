"use client";

import { useState, useEffect } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002";

interface JobInfo {
  status: string;
  message: string;
  route: string;
  current_step: number;
  total_steps: number;
  started_at: number;
  robot_ip?: string;
  work_mode?: string;
}

interface ScheduleItem {
  id: number;
  name: string;
  route_name: string;
  start_time: string;
  end_time: string | null;
  repeat_type: string;
  is_active: boolean;
  last_run_at: string | null;
}

interface HistoryItem {
  id: number;
  task_name: string;
  route_name: string;
  robot_name: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  started: "작업 시작",
  aligning: "랙 정렬 중",
  jacking_up: "잭 올리는 중",
  jacking_down: "잭 내리는 중",
  moving_to_dropoff: "드롭오프 이동 중",
  moving: "이동 중",
  charging: "충전 도킹 중",
  waiting: "대기 중",
  waiting_confirm: "출발 대기",
  waiting_confirm_return: "복귀 대기",
  waiting_next_or_return: "다음 포인트 선택",
  returning: "충전소 복귀 중",
  done: "완료",
  error: "오류",
  stopping: "정지 중",
  failed: "실패",
};

const HISTORY_STATUS: Record<string, { label: string; color: string }> = {
  running: { label: "진행 중", color: "var(--color-primary)" },
  completed: { label: "완료", color: "var(--color-success)" },
  succeeded: { label: "완료", color: "var(--color-success)" },
  failed: { label: "실패", color: "var(--color-error)" },
  cancelled: { label: "취소", color: "var(--text-muted)" },
};

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function formatElapsed(startedAt: number): string {
  const sec = Math.floor(Date.now() / 1000 - startedAt);
  if (sec < 60) return `${sec}초`;
  const min = Math.floor(sec / 60);
  const s = sec % 60;
  return `${min}분 ${s}초`;
}

export function JobStatusPanel() {
  const [activeJobs, setActiveJobs] = useState<Record<string, JobInfo>>({});
  const [todaySchedules, setTodaySchedules] = useState<ScheduleItem[]>([]);
  const [recentHistory, setRecentHistory] = useState<HistoryItem[]>([]);
  const [, setTick] = useState(0);
  const [jackPois, setJackPois] = useState<{id: number; name: string}[]>([]);

  useEffect(() => {
    fetch(`${API}/api/map/active-pois`)
      .then(r => r.json())
      .then(data => setJackPois((data || []).filter((p: any) => p.poi_type === "jack").map((p: any) => ({id: p.id, name: p.name}))))
      .catch(() => {});
  }, []);

  // 진행 중 작업 폴링
  useEffect(() => {
    const fetchJobs = async () => {
      try {
        const res = await fetch(`${API}/api/robots/job-status`);
        if (res.ok) setActiveJobs(await res.json());
      } catch {}
    };
    fetchJobs();
    const interval = setInterval(fetchJobs, 2000);
    return () => clearInterval(interval);
  }, []);

  // 경과 시간 업데이트
  useEffect(() => {
    const interval = setInterval(() => setTick((v) => v + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  // 스케줄 + 이력 폴링
  useEffect(() => {
    const fetchData = async () => {
      try {
        const [schedRes, histRes] = await Promise.all([
          fetch(`${API}/api/tasks?limit=10`),
          fetch(`${API}/api/tasks/history/all?limit=5`),
        ]);
        if (schedRes.ok) {
          const data = await schedRes.json();
          setTodaySchedules(data.items || []);
        }
        if (histRes.ok) {
          const data = await histRes.json();
          setRecentHistory(data.items || []);
        }
      } catch {}
    };
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, []);

  const [notification, setNotification] = useState<string | null>(null);

  const handleForceReturn = async (ip: string, workMode?: string) => {
    const msg = workMode === "rack_pickup"
      ? "강제 종료하시겠습니까?\n랙을 원래 위치(랙 보관 지점)에 두고 충전소로 복귀합니다."
      : "강제 종료하시겠습니까?\n현재 작업을 중단하고 곧바로 충전소로 복귀합니다.";
    if (!confirm(msg)) return;
    try {
      await fetch(`${API}/api/robots/remote/force-return/${ip}`, { method: "POST" });
      setNotification("강제 종료 — 충전소로 복귀합니다");
      setTimeout(() => setNotification(null), 3000);
    } catch {
      setNotification("강제 종료 명령 실패");
      setTimeout(() => setNotification(null), 3000);
    }
  };

  const handleDock = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/dock/${ip}`, { method: "POST" });
      setNotification("충전소로 이동 중입니다");
      setTimeout(() => setNotification(null), 5000);
    } catch {
      setNotification("충전소 복귀 실패");
      setTimeout(() => setNotification(null), 3000);
    }
  };

  const jobEntries = Object.entries(activeJobs);

  // 작업 완료 후 충전소 복귀 감지
  const returningJob = jobEntries.find(([, j]) => j.status === "returning");
  const showReturning = !!returningJob && !notification;

  return (
    <div className="job-status-panel">
      {/* 상단 알림바 */}
      {(notification || showReturning) && (
        <div className="job-notification">
          <div className="job-notification__bar">
            <span className="job-notification__spinner" />
            <span>{notification || "작업 완료 후 충전소로 복귀 중입니다"}</span>
          </div>
        </div>
      )}
      <h3 className="job-status-panel__title">작업 현황</h3>

      {/* 진행 중인 작업 */}
      {jobEntries.length > 0 ? (
        <div className="job-status-panel__section">
          <h4 className="job-status-panel__subtitle">진행 중</h4>
          {jobEntries.map(([ip, job]) => {
            const progress = job.total_steps > 0
              ? Math.round((job.current_step / job.total_steps) * 100)
              : 0;
            return (
              <div key={ip} className="job-card job-card--active">
                <div className="job-card__header">
                  <span className="job-card__status-badge">
                    {STATUS_LABEL[job.status] || job.status}
                  </span>
                  <span className="job-card__elapsed">
                    {job.started_at ? formatElapsed(job.started_at) : ""}
                  </span>
                </div>
                <div className="job-card__route">{job.route}</div>
                <div className="job-card__progress-bar">
                  <div className="job-card__progress-fill" style={{ width: `${progress}%` }} />
                </div>
                <div className="job-card__progress-text">
                  {job.current_step}/{job.total_steps} 단계 ({progress}%)
                </div>
                <div className="job-card__message">{job.message}</div>
                {job.status === "waiting_confirm" && (
                  <div style={{ marginTop: 8 }}>
                    <button
                      style={{ width: "100%", background: "linear-gradient(135deg, #5a8ff5, #3b6fd4)", color: "white", border: "none", padding: "10px", borderRadius: 8, cursor: "pointer", fontWeight: 600, fontSize: "var(--font-size-sm)" }}
                      onClick={async () => {
                        await fetch(`${API}/api/robots/remote/confirm/${ip}`, { method: "POST" });
                      }}
                    >출발</button>
                  </div>
                )}
                {job.status === "waiting_next_or_return" && (
                  <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                    <div style={{ display: "flex", gap: 6 }}>
                      <select
                        id={`next-poi-${ip}`}
                        style={{ flex: 1, padding: "8px 10px", borderRadius: 6, background: "var(--bg-surface-2)", color: "var(--text-primary)", border: "1px solid var(--border-color)", fontSize: "var(--font-size-xs)" }}
                      >
                        {jackPois.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                      </select>
                      <button
                        style={{ background: "linear-gradient(135deg, #5a8ff5, #3b6fd4)", color: "white", border: "none", padding: "8px 16px", borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: "var(--font-size-xs)", whiteSpace: "nowrap" }}
                        onClick={async () => {
                          const sel = document.getElementById(`next-poi-${ip}`) as HTMLSelectElement;
                          if (sel?.value) {
                            await fetch(`${API}/api/robots/remote/next-point/${ip}`, {
                              method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({ poi_id: Number(sel.value) }),
                            });
                          }
                        }}
                      >다음 이동</button>
                    </div>
                    <button
                      style={{ width: "100%", background: "linear-gradient(135deg, #36dfc8, #2bb5a0)", color: "white", border: "none", padding: "8px", borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: "var(--font-size-xs)" }}
                      onClick={async () => {
                        await fetch(`${API}/api/robots/remote/return/${ip}`, { method: "POST" });
                      }}
                    >복귀</button>
                  </div>
                )}
                <div className="job-card__footer" style={{ marginTop: 6 }}>
                  {ip && <span className="job-card__robot-ip">로봇: {ip}</span>}
                  <button
                    className="job-card__stop-btn"
                    onClick={() => handleForceReturn(ip, job.work_mode)}
                  >강제 종료</button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="job-card job-card--idle" style={{ textAlign: "center" }}>
          <span style={{ color: "var(--text-muted)", fontSize: "var(--font-size-sm)" }}>
            진행 중인 작업 없음
          </span>
        </div>
      )}

      {/* 오늘 스케줄 */}
      {todaySchedules.length > 0 && (
        <div className="job-status-panel__section">
          <h4 className="job-status-panel__subtitle">등록된 스케줄</h4>
          {todaySchedules.map((s) => {
            const now = new Date();
            const nowHHMM = `${String(now.getHours()).padStart(2,"0")}:${String(now.getMinutes()).padStart(2,"0")}`;
            const isBefore = s.start_time && nowHHMM < s.start_time.slice(0, 5);
            const isExpired = s.end_time && nowHHMM > s.end_time;
            const isBusy = jobEntries.length > 0;
            const canRun = !isBusy && !isExpired && !isBefore && s.is_active;
            const btnLabel = isBusy ? "작업 진행 중" : isBefore ? "시간 전" : isExpired ? "시간 종료" : !s.is_active ? "비활성" : "즉시 실행";
            return (
              <div key={s.id} className="job-card">
                <div className="job-card__header">
                  <span className="job-card__robot-name">{s.name}</span>
                  <span style={{
                    fontSize: "var(--font-size-2xs)",
                    color: isExpired ? "var(--text-muted)" : s.is_active ? "var(--color-success)" : "var(--text-muted)"
                  }}>
                    {isExpired ? "시간 종료" : s.is_active ? "활성" : "비활성"}
                  </span>
                </div>
                <div className="job-card__route">{s.route_name}</div>
                <div className="job-card__meta">
                  <span>{s.start_time}{s.end_time ? ` ~ ${s.end_time}` : ""}</span>
                  <span>{s.repeat_type === "once" ? "1회" : s.repeat_type === "daily" ? "매일" : "매주"}</span>
                </div>
                <div className="job-card__footer">
                  <span />
                  <button
                    className={`job-card__run-btn${!canRun ? " job-card__run-btn--disabled" : ""}`}
                    disabled={!canRun}
                    onClick={async () => {
                      if (!canRun) return;
                      try {
                        const res = await fetch(`${API}/api/tasks/schedule/${s.id}/run`, { method: "POST" });
                        if (res.ok) {
                          setNotification(`'${s.name}' 작업 실행 시작`);
                          setTimeout(() => setNotification(null), 3000);
                        }
                      } catch {}
                    }}
                  >{btnLabel}</button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 최근 실행 이력 */}
      {recentHistory.length > 0 && (
        <div className="job-status-panel__section">
          <h4 className="job-status-panel__subtitle">최근 실행 이력</h4>
          {recentHistory.map((h) => {
            const st = HISTORY_STATUS[h.status] || { label: h.status, color: "var(--text-muted)" };
            return (
              <div key={h.id} className="job-card">
                <div className="job-card__header">
                  <span className="job-card__robot-name">{h.route_name || h.task_name}</span>
                  <span style={{ fontSize: "var(--font-size-2xs)", color: st.color }}>{st.label}</span>
                </div>
                <div className="job-card__meta">
                  <span>{h.robot_name}</span>
                  <span>{formatTime(h.started_at)}{h.finished_at ? ` ~ ${formatTime(h.finished_at)}` : ""}</span>
                </div>
                {h.error_message && (
                  <div style={{ fontSize: "var(--font-size-2xs)", color: "var(--color-error)", marginTop: "4px" }}>
                    {h.error_message}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
