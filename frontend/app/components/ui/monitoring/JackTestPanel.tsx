"use client";

import { useState, useEffect, useRef } from "react";
import { ROBOT_TYPE_WORK_MODES, WORK_MODE_LABELS, type WorkMode, type RobotType } from "@/lib/constants/robotTypes";

const API = process.env.NEXT_PUBLIC_API_URL || "";

type LiveRobot = { ID: number; IP: string; SN: string; ROBOTNAME: string; ONLINE: string; ROBOT_TYPE?: string; [key: string]: any };
type PoiOption = { id: number; name: string; type: string };

type RobotJob = {
  ip: string;
  name: string;
  status: string;
  message: string;
};

type Props = {
  liveRobots: LiveRobot[];
  areaId?: number;
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

export function JackTestPanel({ liveRobots, areaId }: Props) {
  const [robotIp, setRobotIp] = useState("");
  const [robotId, setRobotId] = useState<number>(0);
  const [pois, setPois] = useState<PoiOption[]>([]);
  const [pickupId, setPickupId] = useState<number>(0);
  const [dropoffId, setDropoffId] = useState<number>(0);
  const [workMode, setWorkMode] = useState<"rack_pickup" | "delivery_no_rack" | "simple_move">("rack_pickup");
  const [isStarting, setIsStarting] = useState(false);
  const [activeJobs, setActiveJobs] = useState<RobotJob[]>([]);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const onlineRobots = liveRobots.filter((r) => r.ONLINE === "Online");
  const jackPois = pois.filter((p) => p.type === "jack");

  // 로봇 이름 찾기
  const getRobotName = (ip: string) => {
    const r = liveRobots.find((x) => x.IP === ip);
    return r?.ROBOTNAME || r?.SN || ip;
  };

  // POI 로드
  useEffect(() => {
    const url = areaId ? `${API}/api/map/active-pois?area_id=${areaId}` : `${API}/api/map/active-pois`;
    fetch(url)
      .then((r) => r.json())
      .then((data) => setPois(Array.isArray(data) ? data.map((p: any) => ({ id: p.id, name: p.name, type: p.poi_type || p.type })) : []))
      .catch(() => {});
  }, [areaId]);

  // 모든 로봇의 작업 상태 폴링
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

  // 선택한 로봇의 타입에 따른 허용 work_mode
  const selectedRobotType = (onlineRobots.find((r) => r.IP === robotIp)?.ROBOT_TYPE || "lifting") as RobotType;
  const allowedWorkModes: WorkMode[] = ROBOT_TYPE_WORK_MODES[selectedRobotType] || ["simple_move"];

  const handleRobotChange = (ip: string) => {
    setRobotIp(ip);
    const robot = onlineRobots.find((r) => r.IP === ip);
    setRobotId(robot?.ID || 0);
    // 로봇 타입이 현재 work_mode를 허용 안 하면 첫 번째 허용 모드로 변경
    const newType = (robot?.ROBOT_TYPE || "lifting") as RobotType;
    const allowed = ROBOT_TYPE_WORK_MODES[newType] || ["simple_move"];
    if (!allowed.includes(workMode)) {
      setWorkMode(allowed[0]);
    }
  };

  const handleStart = async () => {
    if (!robotIp || !robotId || !pickupId || !dropoffId) return;
    setIsStarting(true);
    try {
      const res = await fetch(`${API}/api/tasks/manual-run-pois`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ robot_id: robotId, pickup_poi_id: pickupId, dropoff_poi_id: dropoffId, manual_confirm: true, work_mode: workMode }),
      });
      if (res.status === 409) {
        alert("로봇이 이미 작업 중입니다");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      // 작업 시작 후 폼 초기화 (다른 로봇에 또 넣을 수 있게)
      setRobotIp("");
      setRobotId(0);
      setPickupId(0);
      setDropoffId(0);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "시작 실패";
      alert(`시작 실패: ${msg}`);
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/stop-all/${ip}`, { method: "POST" });
    } catch {}
  };

  const [pausedIps, setPausedIps] = useState<Set<string>>(new Set());

  const handlePause = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/pause/${ip}`, { method: "POST" });
      setPausedIps((prev) => new Set(prev).add(ip));
    } catch {}
  };

  const handleResume = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/resume/${ip}`, { method: "POST" });
      setPausedIps((prev) => {
        const n = new Set(prev);
        n.delete(ip);
        return n;
      });
    } catch {}
  };

  const handleForceReturn = async (ip: string) => {
    if (!confirm("강제 종료하시겠습니까?\n현재 위치에서 랙을 들고 원래 위치에 두고 충전소로 복귀합니다.")) return;
    try {
      await fetch(`${API}/api/robots/remote/force-return/${ip}`, { method: "POST" });
    } catch {}
  };

  const handleConfirm = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/confirm/${ip}`, { method: "POST" });
    } catch {}
  };

  const handleReturn = async (ip: string) => {
    try {
      await fetch(`${API}/api/robots/remote/return/${ip}`, { method: "POST" });
    } catch {}
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

  // 작업 중인 로봇 IP들
  const busyIps = new Set(activeJobs.map((j) => j.ip));
  // 작업 가능한 로봇 (현재 작업 중이 아닌)
  const availableRobots = onlineRobots.filter((r) => !busyIps.has(r.IP));

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
          >
            <option value="">선택</option>
            {availableRobots.map((r) => (
              <option key={r.IP} value={r.IP}>
                {r.ROBOTNAME || r.SN} ({r.IP})
              </option>
            ))}
          </select>
        </label>

        <label className="jack-test-panel__label">
          작업 종류
          <select
            className="jack-test-panel__select"
            value={workMode}
            onChange={(e) => setWorkMode(e.target.value as typeof workMode)}
            disabled={!robotIp}
          >
            {allowedWorkModes.map((m) => (
              <option key={m} value={m}>{WORK_MODE_LABELS[m]}</option>
            ))}
          </select>
        </label>

        <label className="jack-test-panel__label">
          {workMode === "simple_move" ? "시작 위치" : "픽업 위치"}
          <select
            className="jack-test-panel__select"
            value={pickupId}
            onChange={(e) => setPickupId(Number(e.target.value))}
          >
            <option value={0}>선택</option>
            {jackPois.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>

        <label className="jack-test-panel__label">
          {workMode === "simple_move" ? "도착 위치" : "드롭오프 위치"}
          <select
            className="jack-test-panel__select"
            value={dropoffId}
            onChange={(e) => setDropoffId(Number(e.target.value))}
          >
            <option value={0}>선택</option>
            {jackPois.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>

        <div className="jack-test-panel__buttons">
          <button
            className="btn btn--primary jack-test-panel__btn"
            onClick={handleStart}
            disabled={!robotIp || !pickupId || !dropoffId || isStarting}
          >
            {isStarting ? "시작 중..." : "실행"}
          </button>
        </div>
      </div>
      {/* 작업 중인 로봇 카드는 ActiveJobsPanel(공통 상단) 으로 분리됨 */}
    </div>
  );
}
