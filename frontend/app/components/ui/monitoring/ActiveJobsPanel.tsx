"use client";

import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

type LiveRobot = { ID: number; IP: string; SN: string; ROBOTNAME: string; ONLINE: string; [key: string]: any };
type PoiOption = { id: number; name: string; type: string };

type RobotJob = {
  ip: string;
  name: string;
  status: string;
  message: string;
  work_mode?: string;
};

const STATUS_LABELS: Record<string, string> = {
  pending: "대기 중",
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
  failed: "실패",
  running: "진행 중",
};

type Props = {
  liveRobots: LiveRobot[];
  areaId?: number;
};

/**
 * 단일/배치 어느 모드든 공통으로 작업 진행 중인 로봇 카드 + 제어 버튼을 표시.
 * - 정지/재개 토글 (pause/resume)
 * - 강제 종료 (work_mode 별 분기: rack_pickup 은 랙 보관 후, 그 외는 곧장 충전소)
 * - 출발 / 복귀 / 다음 포인트 선택 (수동 confirm 흐름용)
 */
export function ActiveJobsPanel({ liveRobots, areaId }: Props) {
  const [activeJobs, setActiveJobs] = useState<RobotJob[]>([]);
  const [pausedIps, setPausedIps] = useState<Set<string>>(new Set());
  const [pois, setPois] = useState<PoiOption[]>([]);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const onlineRobots = liveRobots.filter((r) => r.ONLINE === "Online");
  const jackPois = pois.filter((p) => p.type === "jack");

  const getRobotName = (ip: string) => {
    const r = liveRobots.find((x) => x.IP === ip);
    return r?.ROBOTNAME || r?.SN || ip;
  };

  useEffect(() => {
    const url = areaId ? `${API}/api/map/active-pois?area_id=${areaId}` : `${API}/api/map/active-pois`;
    fetch(url)
      .then((r) => r.json())
      .then((data) =>
        setPois(
          Array.isArray(data)
            ? data.map((p: any) => ({ id: p.id, name: p.name, type: p.poi_type || p.type }))
            : []
        )
      )
      .catch(() => {});
  }, [areaId]);

  useEffect(() => {
    const poll = async () => {
      const ips = onlineRobots.map((r) => r.IP);
      const results: RobotJob[] = [];
      await Promise.all(
        ips.map(async (ip) => {
          try {
            const res = await fetch(`${API}/api/robots/job-status/${ip}`);
            if (!res.ok) return;
            const job = await res.json();
            if (job.status && job.status !== "idle" && job.status !== "done") {
              results.push({
                ip,
                name: getRobotName(ip),
                status: job.status,
                message: job.message || STATUS_LABELS[job.status] || job.status,
                work_mode: job.work_mode,
              });
            }
          } catch {}
        })
      );
      setActiveJobs(results);
    };
    poll();
    pollingRef.current = setInterval(poll, 2000);
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveRobots]);

  const handleConfirm = async (ip: string) => {
    try { await fetch(`${API}/api/robots/remote/confirm/${ip}`, { method: "POST" }); } catch {}
  };
  const handleReturn = async (ip: string) => {
    try { await fetch(`${API}/api/robots/remote/return/${ip}`, { method: "POST" }); } catch {}
  };
  const handleNextPoi = async (ip: string, poiId: number) => {
    try {
      await fetch(`${API}/api/robots/remote/next-point/${ip}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ poi_id: poiId }),
      });
    } catch {}
  };
  const handlePause = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/pause/${ip}`, { method: "POST" });
      setPausedIps((prev) => new Set(prev).add(ip));
    } catch {}
  };
  const handleResume = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/resume/${ip}`, { method: "POST" });
      setPausedIps((prev) => { const n = new Set(prev); n.delete(ip); return n; });
    } catch {}
  };
  const handleForceReturn = async (ip: string, workMode?: string) => {
    const msg = workMode === "rack_pickup"
      ? "강제 종료하시겠습니까?\n랙을 원래 위치(랙 보관 지점)에 두고 충전소로 복귀합니다."
      : "강제 종료하시겠습니까?\n현재 작업을 중단하고 곧바로 충전소로 복귀합니다.";
    if (!confirm(msg)) return;
    try { await fetch(`${API}/api/robots/remote/force-return/${ip}`, { method: "POST" }); } catch {}
  };

  if (activeJobs.length === 0) return null;

  return (
    <div style={{ padding: 12, borderBottom: "1px solid var(--border-color)" }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: "var(--text-muted)" }}>
        작업 중인 로봇 ({activeJobs.length}대)
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {activeJobs.map((job) => (
          <div
            key={job.ip}
            style={{
              padding: 10,
              background: "var(--bg-surface-2)",
              border: "1px solid var(--border-color)",
              borderRadius: 6,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{job.name}</span>
              <span style={{ fontSize: 11, color: "var(--color-warning)" }}>
                {STATUS_LABELS[job.status] || job.status}
              </span>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8 }}>{job.message}</div>

            {job.status === "waiting_confirm" && (
              <button
                className="btn btn--primary"
                style={{ width: "100%", padding: "6px 10px", fontSize: 12 }}
                onClick={() => handleConfirm(job.ip)}
              >출발</button>
            )}

            {job.status === "waiting_next_or_return" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ display: "flex", gap: 6 }}>
                  <select
                    className="jack-test-panel__select"
                    style={{ flex: 1, fontSize: 12 }}
                    id={`next-${job.ip}`}
                  >
                    {jackPois.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                  <button
                    className="btn btn--primary"
                    style={{ padding: "6px 10px", fontSize: 12 }}
                    onClick={() => {
                      const sel = document.getElementById(`next-${job.ip}`) as HTMLSelectElement;
                      if (sel?.value) handleNextPoi(job.ip, Number(sel.value));
                    }}
                  >이동</button>
                </div>
                <button
                  className="btn"
                  style={{ background: "linear-gradient(135deg, #36dfc8, #2bb5a0)", color: "white", padding: "6px 10px", fontSize: 12 }}
                  onClick={() => handleReturn(job.ip)}
                >복귀</button>
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 8 }}>
              {pausedIps.has(job.ip) ? (
                <button
                  style={{
                    padding: "7px 10px", fontSize: 12, fontWeight: 600,
                    background: "linear-gradient(135deg, #36dfc8, #2bb5a0)",
                    color: "white", border: "none", borderRadius: 4, cursor: "pointer",
                  }}
                  onClick={() => handleResume(job.ip)}
                >▶ 재개</button>
              ) : (
                <button
                  style={{
                    padding: "7px 10px", fontSize: 12, fontWeight: 600,
                    background: "#f3a83a", color: "white",
                    border: "none", borderRadius: 4, cursor: "pointer",
                  }}
                  onClick={() => handlePause(job.ip)}
                >⏸ 정지</button>
              )}
              <button
                style={{
                  padding: "7px 10px", fontSize: 12, fontWeight: 600,
                  background: "#d9534f", color: "white",
                  border: "none", borderRadius: 4, cursor: "pointer",
                }}
                onClick={() => handleForceReturn(job.ip, job.work_mode)}
              >■ 강제 종료</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
